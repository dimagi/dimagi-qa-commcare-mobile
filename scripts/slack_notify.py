"""
Posts a Slack summary of the latest Maestro/BrowserStack run to
SLACK_CHANNEL_ID (auth via SLACK_BOT_TOKEN). Meant to run as its own CI step
right after run_suite.py, in the same job - it reads reports/latest_results.json
and reports/history.json straight off disk rather than being wired into
run_suite.py, so notifying can be skipped or rerun independently of the test
run itself.

The bot token needs the `files:write` and `chat:write` scopes, and the bot
must already be a member of SLACK_CHANNEL_ID (Slack won't post into a
channel it hasn't been invited to, regardless of scopes).

ONE message per run: the chart PNG uploaded via Slack's current (v2) file
upload flow (files.getUploadURLExternal -> PUT bytes -> files.completeUploadExternal
with channel_id + initial_comment, posting the file and the summary text as a
single message). The "Download HTML report" link points at the GitHub Actions
artifact (REPORT_ARTIFACT_URL, the `actions/upload-artifact` step's own
`artifact-url` output - see maestro-browserstack.yml), NOT a Slack-hosted
copy of the report. This matches the proven pattern in this org's other CI
repos (e2e-parity's post-slack-chart.py, dimagi-qa's hq-smoke-tests.yml) -
Slack has no API to get a permalink for an uploaded file that resolves for
the whole channel WITHOUT that upload itself posting as its own separate
message, so the earlier "upload the HTML report to Slack first" approach
here always produced two messages per run (the bare file-share, then the
chart+summary linking to it). Since a GitHub Actions artifact URL already
resolves for anyone with repo access with no separate post needed, this
sidesteps the problem entirely instead of working around it.

Usage: python scripts/slack_notify.py
"""
import json
import os
import pathlib
import sys
import tempfile

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import report_generator

SLACK_API = "https://slack.com/api"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS_DIR = report_generator.REPORTS_DIR


def _slack_post(method, token, **kwargs):
    resp = requests.post(f"{SLACK_API}/{method}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {data}")
    return data


def upload_files(token, channel_id, file_paths, initial_comment=None):
    """Reserve+upload+finalize one or more files via Slack's v2 flow as a
    SINGLE message, shared into channel_id so their permalinks are actually
    accessible to the channel (an unshared upload is private to the bot).
    Each file gets its own files.getUploadURLExternal reservation (Slack
    requires that per-file), but they're all finalized together in ONE
    files.completeUploadExternal call passing every {id, title} - that's
    what makes Slack render them as ONE message with N attachments instead
    of N separate messages. If initial_comment is given, it's posted as
    that single message's text - otherwise it's a bare file-share with no
    text. UPDATE, confirmed live (2026-08-08, real Slack failure, run #43):
    this used to be a single-file-only helper called once per notification;
    see build_message()'s own header for why the failed-tests table moved
    out of initial_comment and into its own uploaded file instead of
    growing this function into a second separate call (which would have
    reproduced the exact "two messages" bug this replaces)."""
    entries = []
    for file_path in file_paths:
        file_path = pathlib.Path(file_path)
        data = file_path.read_bytes()
        reserve = _slack_post("files.getUploadURLExternal", token,
                               data={"filename": file_path.name, "length": len(data)})
        put_resp = requests.post(reserve["upload_url"], files={"file": (file_path.name, data)})
        put_resp.raise_for_status()
        entries.append({"id": reserve["file_id"], "title": file_path.name})

    complete_kwargs = {"files": json.dumps(entries), "channel_id": channel_id}
    if initial_comment:
        complete_kwargs["initial_comment"] = initial_comment
    complete = _slack_post("files.completeUploadExternal", token, data=complete_kwargs)
    return complete["files"]


def _gh_run_url():
    """GITHUB_* vars are set automatically in every Actions step - see
    https://docs.github.com/actions/learn-github-actions/variables#default-environment-variables.
    Empty outside CI (e.g. local testing), not an error."""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


# GitHub's raw event names aren't the friendly "TRIGGER" phrasing this org's
# other Slack bots use (e2e-parity/dimagi-qa) - map the ones this workflow
# actually fires (workflow_dispatch, schedule) and fall back to a generic
# uppercased/spaced transform for anything else.
_EVENT_LABELS = {
    "schedule": "SCHEDULED TRIGGER",
    "workflow_dispatch": "MANUAL TRIGGER",
}


def render_failed_tests_txt(failed_results, out_path):
    """Writes the Workflow|Test table for every failed result to a plain
    .txt file. UPDATE, confirmed live (2026-08-08, real Slack failure, run
    #43 with 100 failures, screenshots + a screen recording from the user):
    this table used to be embedded directly in the message's own
    initial_comment as a ``` fenced block - Slack renders that fenced block
    as a bordered/grey-background snippet-style box INSIDE the message,
    which looked right for smaller failure counts, but once this repo's own
    earlier truncation (MAX_FAILED_LISTED) was removed to stop silently
    hiding failures, a ~100-row table pushed the initial_comment past
    Slack's length threshold for a single post. Confirmed live: Slack
    responded by splitting the ONE logical post into TWO separate messages
    (each showing the same header/stats and, since the file attachment is
    tied to the post as a whole, the SAME chart image attached again) - and
    the split point landed mid-table, severing the closing ``` from its
    opening fence, so BOTH halves fell back to plain unstyled text instead
    of the intended grey box. Moving the table into its own uploaded .txt
    file fixes both symptoms at once: Slack renders a small text file as a
    genuine bordered/grey-background snippet preview (the exact look this
    replaces), and the message's own initial_comment stays short regardless
    of how many tests failed, so it can no longer trigger Slack's own
    length-based auto-split."""
    workflow_width = max(len(r["workflow"]) for r in failed_results)
    header = f"{'Workflow'.ljust(workflow_width)} | Test"
    lines = [header, "-" * len(header)]
    for r in failed_results:
        name = r["name"]
        prefix = f"{r['workflow']}/"
        if name.startswith(prefix):
            name = name[len(prefix):]
        lines.append(f"{r['workflow'].ljust(workflow_width)} | {name}")
    pathlib.Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_message(counts, failed_results, report_artifact_url, run_url):
    workflow = os.environ.get("GITHUB_WORKFLOW", "Maestro BrowserStack QA")
    event = os.environ.get("GITHUB_EVENT_NAME", "manual")
    event_label = _EVENT_LABELS.get(event, event.replace("_", " ").upper())
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "?")
    tag = (os.environ.get("RUN_TAG") or "ALL").upper()
    ref = os.environ.get("GITHUB_REF_NAME", "?")
    actor = os.environ.get("GITHUB_ACTOR", "?")
    status_icon = ":white_check_mark:" if counts["failed"] == 0 else ":x:"

    version_path = REPORTS_DIR / "apk_version.txt"
    apk_version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else None
    android_version = os.environ.get("ANDROID_VERSION")
    run_duration = os.environ.get("RUN_DURATION")
    # UPDATE, per explicit formatting request (2026-08-08): "Triggered by
    # <name> · on branch <branchname> · for apk ver <commcare tag> · with
    # total runtime <runtime>", with every <...> value in bold (Slack
    # mrkdwn *bold*, not backtick-code as this line used before).
    # UPDATE (2026-08-17), per explicit follow-up request: added "on android
    # ver <version>" right before the runtime, same bold styling, everything
    # else unchanged.
    branch_line = f"Triggered by *{actor}* · on branch *{ref}*"
    if apk_version:
        branch_line += f" · for apk ver *{apk_version}*"
    if android_version:
        branch_line += f" · on android ver *{android_version}*"
    if run_duration:
        branch_line += f" · with total runtime *{run_duration}*"

    lines = [
        f"{status_icon} :bar_chart: *[{tag}] {workflow} Run #{run_number} Test Summary Charts "
        f"triggered by {event_label} event*",
        branch_line,
        "",
        (f"*Pass rate:* {counts['pass_rate']:.1f}% ({counts['passed'] + counts['rerun']}/{counts['total']})   "
         f"*Total:* {counts['total']}   *Passed:* {counts['passed']}   *Failed:* {counts['failed']}   "
         f"*Skipped:* {counts['skipped']}   *Rerun:* {counts['rerun']}"),
        # UPDATE (2026-08-21), per direct user question on why Pass rate's
        # own numerator (104) didn't match the *Passed* count shown right
        # next to it (101): *Passed* only counts tests that passed on their
        # first attempt; *Rerun* counts tests that failed once but passed on
        # a --retry-failed retry (report_generator.py's own "rerun" status -
        # a real pass, just flagged as flaky rather than folded silently
        # into *Passed*). Pass rate's numerator is therefore Passed+Rerun,
        # not Passed alone - spelling that out here since the two numbers
        # sitting side by side without it reads as a bug/mismatch.
        "_(Pass rate counts Rerun as a pass: Total Pass = Passed + Rerun)_",
    ]

    if failed_results:
        # UPDATE, confirmed live (2026-08-08, real Slack failure, run #43,
        # 100 failures): this used to embed the whole table inline here as
        # a ``` fenced block. See render_failed_tests_txt()'s own header for
        # the full story - a long table pushed this message past Slack's
        # length threshold, causing a silent auto-split into two posts (one
        # extra chart image, one broken/unstyled table). The table itself
        # now lives in its own uploaded .txt file (a real bordered/grey
        # snippet preview, closer to what a fenced block was going for
        # anyway) - this line is just a short pointer to it.
        lines.append("")
        lines.append(f":red_circle: *Failed Tests* ({len(failed_results)}) - see attached failed-tests.txt")

    links = []
    if report_artifact_url:
        links.append(f"<{report_artifact_url}|:page_facing_up: Download HTML report>")
    if run_url:
        links.append(f"<{run_url}|:link: View run artifact>")
    if links:
        lines.append("")
        lines.append("  ·  ".join(links))

    return "\n".join(lines)


def main():
    load_dotenv(REPO_ROOT / ".env")
    token = os.environ["SLACK_BOT_TOKEN"]
    channel_id = os.environ["SLACK_CHANNEL_ID"]

    results_path = REPORTS_DIR / "latest_results.json"
    # UPDATE, confirmed live (2026-08-08, run 31256655977, force-cancelled
    # mid-run): merge_reports.py raises (rather than writing this file) when
    # every matrix job's own artifact was cancelled before uploading any
    # results - if that happens, history.json is untouched THIS run, and
    # history[-1] below would silently be a STALE entry from whatever
    # earlier run last completed successfully, posted as if it were this
    # run's real result. Bail out honestly instead of doing that.
    if not results_path.exists():
        print(f"{results_path} doesn't exist - this run produced no results (likely "
              f"cancelled or crashed before merge_reports.py could run) - skipping "
              f"notification rather than posting stale history data.")
        return

    history = report_generator.load_history()
    if not history:
        print("reports/history.json is empty - nothing to notify about.")
        return
    counts = history[-1]

    results = json.loads(results_path.read_text(encoding="utf-8"))
    failed_results = [r for r in results if r["status"] == "failed"]

    report_artifact_url = os.environ.get("REPORT_ARTIFACT_URL", "")
    if not report_artifact_url:
        print("REPORT_ARTIFACT_URL not set - the message will have no 'Download HTML report' "
              "link (set it from the actions/upload-artifact step's `artifact-url` output).")

    message = build_message(counts, failed_results, report_artifact_url, _gh_run_url())

    with tempfile.TemporaryDirectory() as tmp:
        chart_path = pathlib.Path(tmp) / "slack-chart.png"
        report_generator.render_chart_png(counts, history, chart_path)
        # UPDATE, per explicit request: Slack always renders a message's
        # own text FIRST, then every attached file below it in upload
        # order - there's no way to interleave text between attachments.
        # failed-tests.txt used to be uploaded SECOND (after the chart), so
        # its card rendered at the very bottom, well past the chart image,
        # far from the "see attached failed-tests.txt" pointer right next
        # to the Failed Tests count. Uploading it FIRST puts its card
        # immediately after the message text instead - as close to that
        # pointer as Slack's fixed text-then-attachments layout allows.
        files_to_upload = []
        if failed_results:
            failed_txt_path = pathlib.Path(tmp) / "failed-tests.txt"
            render_failed_tests_txt(failed_results, failed_txt_path)
            files_to_upload.append(failed_txt_path)
        files_to_upload.append(chart_path)
        # Both files finalized in ONE files.completeUploadExternal call (see
        # upload_files()'s own header) - this is what keeps it to a single
        # Slack message instead of one per file.
        upload_files(token, channel_id, files_to_upload, initial_comment=message)

    print("Posted Slack notification.")


if __name__ == "__main__":
    main()
