"""
Posts a Slack summary of the latest Maestro/BrowserStack run to
SLACK_CHANNEL_ID (auth via SLACK_BOT_TOKEN). Meant to run as its own CI step
right after run_suite.py, in the same job - it reads reports/latest.html,
reports/latest_results.json and reports/history.json straight off disk
rather than being wired into run_suite.py, so notifying can be skipped or
rerun independently of the test run itself.

The bot token needs the `files:write` and `chat:write` scopes, and the bot
must already be a member of SLACK_CHANNEL_ID (Slack won't post into a
channel it hasn't been invited to, regardless of scopes).

Uses Slack's current (v2) file upload flow - the old files.upload endpoint
is deprecated:
    1. files.getUploadURLExternal   - reserve an upload slot
    2. POST the raw bytes to that URL
    3. files.completeUploadExternal - finalize; passing channel_id (+ an
       optional initial_comment) posts it as a message in one call.

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


def build_message(counts, failed_results, html_permalink, run_url):
    workflow = os.environ.get("GITHUB_WORKFLOW", "Maestro BrowserStack QA")
    event = os.environ.get("GITHUB_EVENT_NAME", "manual")
    ref = os.environ.get("GITHUB_REF_NAME", "?")
    actor = os.environ.get("GITHUB_ACTOR", "?")
    status_icon = ":white_check_mark:" if counts["failed"] == 0 else ":x:"

    lines = [
        f":test_tube: *{workflow} @ {ref}*  {status_icon}",
        f"Triggered by {actor} · trigger: `{event}`",
        "",
        (f"*Pass rate:* {counts['pass_rate']:.1f}% ({counts['passed'] + counts['rerun']}/{counts['total']})   "
         f"*Total:* {counts['total']}   *Passed:* {counts['passed']}   *Failed:* {counts['failed']}   "
         f"*Skipped:* {counts['skipped']}   *Rerun:* {counts['rerun']}"),
    ]

    if failed_results:
        lines.append("")
        lines.append("*Failed tests:*")
        shown = failed_results[:MAX_FAILED_LISTED]
        for r in shown:
            lines.append(f"• {r['workflow']} — {r['name']}")
        if len(failed_results) > len(shown):
            lines.append(f"_+{len(failed_results) - len(shown)} more_")

    links = []
    if html_permalink:
        links.append(f"<{html_permalink}|:page_facing_up: Download HTML report>")
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

    html_path = REPORTS_DIR / "latest.html"
    html_permalink = ""
    if html_path.exists():
        # Shared with no initial_comment - a bare file-share post, just so
        # its permalink resolves for everyone before the summary message
        # (below) links to it.
        uploaded = upload_file(token, channel_id, html_path, title="Maestro run report")
        html_permalink = uploaded.get("permalink", "")
    else:
        print("reports/latest.html not found - report_generator.generate_report() must run first.")

    message = build_message(counts, failed_results, html_permalink, _gh_run_url())

    with tempfile.TemporaryDirectory() as tmp:
        chart_path = pathlib.Path(tmp) / "slack-chart.png"
        report_generator.render_chart_png(counts, history, chart_path)
        upload_file(token, channel_id, chart_path, title="slack-chart.png", initial_comment=message)

    print("Posted Slack notification.")


if __name__ == "__main__":
    main()
