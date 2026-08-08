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
MAX_FAILED_LISTED = 10


def _slack_post(method, token, **kwargs):
    resp = requests.post(f"{SLACK_API}/{method}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {data}")
    return data


def upload_file(token, channel_id, file_path, title=None, initial_comment=None):
    """Reserve+upload+finalize one file via Slack's v2 flow, shared into
    channel_id so its permalink is actually accessible to the channel (an
    unshared upload is private to the bot). If initial_comment is given,
    this posts it as the message text alongside the file in one call -
    otherwise it's a bare file-share with no text."""
    file_path = pathlib.Path(file_path)
    data = file_path.read_bytes()
    title = title or file_path.name

    reserve = _slack_post("files.getUploadURLExternal", token,
                           data={"filename": file_path.name, "length": len(data)})
    upload_url, file_id = reserve["upload_url"], reserve["file_id"]

    put_resp = requests.post(upload_url, files={"file": (file_path.name, data)})
    put_resp.raise_for_status()

    complete_kwargs = {"files": json.dumps([{"id": file_id, "title": title}]), "channel_id": channel_id}
    if initial_comment:
        complete_kwargs["initial_comment"] = initial_comment
    complete = _slack_post("files.completeUploadExternal", token, data=complete_kwargs)
    return complete["files"][0]


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
    branch_line = f"Branch `{ref}` · triggered by {actor}"
    if apk_version:
        branch_line += f" · CommCare `{apk_version}`"

    lines = [
        f"{status_icon} :bar_chart: *[{tag}] {workflow} Run #{run_number} Test Summary Charts "
        f"triggered by {event_label} event*",
        branch_line,
        "",
        (f"*Pass rate:* {counts['pass_rate']:.1f}% ({counts['passed'] + counts['rerun']}/{counts['total']})   "
         f"*Total:* {counts['total']}   *Passed:* {counts['passed']}   *Failed:* {counts['failed']}   "
         f"*Skipped:* {counts['skipped']}   *Rerun:* {counts['rerun']}"),
    ]

    if failed_results:
        # Slack's plain mrkdwn (what files.completeUploadExternal's
        # initial_comment accepts) has no color or table primitive - a real
        # bordered table / colored header needs Block Kit attachments, which
        # this upload API doesn't take. Closest achievable equivalent: a
        # red-circle emoji on the header (":red_square:" turned out not to be
        # a real Slack shortcode - it rendered as literal text), and a
        # fenced code block (Slack
        # renders ``` as a light-bordered monospace box) for the table body.
        lines.append("")
        lines.append(":red_circle: *Failed Tests*")
        shown = failed_results[:MAX_FAILED_LISTED]
        workflow_width = max(len(r["workflow"]) for r in shown)
        header = f"{'Workflow'.ljust(workflow_width)} | Test"
        table = ["```", header, "-" * len(header)]
        for r in shown:
            name = r["name"]
            prefix = f"{r['workflow']}/"
            if name.startswith(prefix):
                name = name[len(prefix):]
            table.append(f"{r['workflow'].ljust(workflow_width)} | {name}")
        if len(failed_results) > len(shown):
            table.append(f"... +{len(failed_results) - len(shown)} more")
        table.append("```")
        lines.extend(table)

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

    history = report_generator.load_history()
    if not history:
        print("reports/history.json is empty - nothing to notify about.")
        return
    counts = history[-1]

    results_path = REPORTS_DIR / "latest_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else []
    failed_results = [r for r in results if r["status"] == "failed"]

    report_artifact_url = os.environ.get("REPORT_ARTIFACT_URL", "")
    if not report_artifact_url:
        print("REPORT_ARTIFACT_URL not set - the message will have no 'Download HTML report' "
              "link (set it from the actions/upload-artifact step's `artifact-url` output).")

    message = build_message(counts, failed_results, report_artifact_url, _gh_run_url())

    with tempfile.TemporaryDirectory() as tmp:
        chart_path = pathlib.Path(tmp) / "slack-chart.png"
        report_generator.render_chart_png(counts, history, chart_path)
        upload_file(token, channel_id, chart_path, title="slack-chart.png", initial_comment=message)

    print("Posted Slack notification.")


if __name__ == "__main__":
    main()
