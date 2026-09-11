"""
Verifies a form submission on HQ's Submit History report - the on-device-
unreachable "steps 4-7" several Master Mobile Plan (2026) Form Submissions
rows call for (e.g. "Question Types 3", "Repeat Groups 5", "Groups" steps
5-7): proceed to Submit History, search for the form you just submitted,
verify the information is correct, verify multimedia is attached (where
applicable), verify the form metadata (timeStart/timeEnd/etc.) is accurate.

A Maestro flow can't do this itself - it only drives the on-device app, not
a web browser - so this runs as a separate step after the Maestro flow
completes, using the same HQClient session pattern as every other HQ-side
action in this repo (scripts/hq_client.py).

Usage:
    python scripts/verify_submission.py --test-name repeat_groups_5 --username test1 --form-path Repeats
    python scripts/verify_submission.py --test-name question_types_check --username test1 --form-path "Question Types" \
        --require-multimedia --after "2026-08-08T19:00:00"

UPDATE (2026-09-11), confirmed live: this script was cited as the automation
for 3 Master Mobile Plan rows (Repeat Groups 5, upload_test_2, Geoservice 2)
but was never actually wired into any CI step - a genuine orphaned check,
same class of gap already fixed for Form Submissions/Support Menus/
Multimedia's own Submit-History checks (see run_form_submission_history_
check.py's own module docstring). Root cause here was different though:
this script never wrote to reports/latest_results.json at all, unlike every
other check script in this repo - a bare SystemExit on failure/success
would have worked as a CI step's own pass/fail gate, but its result would
never have shown up in the merged HTML report or Slack notification. Now
writes a report_generator.TestResult (merged with whatever's already in
reports/latest_results.json, same read-merge-write pattern as every other
check script here) before exiting, and requires --test-name so each of the
3 call sites gets a distinct, identifiable report entry.

Exits non-zero (with a clear message) if no matching submission is found,
or if a requested check (multimedia present, timeEnd >= timeStart) fails -
suitable for use as a CI step's own pass/fail gate.
"""
import argparse
import datetime
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
from hq_client import HQClient
import report_generator

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(client, username, form_path, after, require_multimedia, require_location):
    submission = client.find_recent_submission(username, form_path_contains=form_path, after=after)
    if submission is None:
        raise AssertionError(
            f"No submission found for username={username!r} form_path_contains={form_path!r} after={after!r}"
        )

    metadata = client.get_form_metadata(submission["form_id"])

    time_start = datetime.datetime.fromisoformat(metadata["timeStart"])
    time_end = datetime.datetime.fromisoformat(metadata["timeEnd"])
    if time_end < time_start:
        raise AssertionError(f"Form metadata is inconsistent: timeEnd ({time_end}) is before timeStart ({time_start}).")

    if require_multimedia and not metadata.get("has_multimedia"):
        raise AssertionError("Expected multimedia attached to this form, but none was found.")

    if require_location and metadata.get("location", "---") == "---":
        raise AssertionError("Expected a captured location on this form, but metadata.location is empty.")

    return submission, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--test-name", required=True, help="Used only for the report entry's name, e.g. 'repeat_groups_5'.")
    parser.add_argument("--username", required=True, help="Mobile worker username that submitted the form.")
    parser.add_argument("--form-path", default=None,
                         help='Substring to match against the module>form breadcrumb, e.g. "Markdown".')
    parser.add_argument("--after", default=None,
                         help="ISO timestamp (e.g. 2026-08-08T19:00:00) - only consider submissions after this, "
                              "so you don't match a stale submission from an earlier run.")
    parser.add_argument("--after-minutes-ago", type=int, default=None,
                         help="Alternative to --after: only consider submissions after (now - N minutes) - "
                              "same convenience flag as run_form_submission_history_check.py's own, for CI "
                              "steps that don't know an absolute timestamp up front.")
    parser.add_argument("--require-multimedia", action="store_true",
                         help="Fail if the matched form has no multimedia attachment.")
    parser.add_argument("--require-location", action="store_true",
                         help='Fail if the matched form\'s metadata "location" field is empty '
                              '(HQ renders an unset location as the literal string "---") - use for '
                              'rows like Geoservice 2 ("Auto Capture Location") that need to confirm '
                              'a geopoint was actually captured into the submission.')
    parser.add_argument("--domain", default=os.environ.get("HQ_DOMAIN", "qateam"))
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    client = HQClient(domain=args.domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"),
        password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    after = None
    if args.after:
        after = datetime.datetime.fromisoformat(args.after)
    elif args.after_minutes_ago is not None:
        after = datetime.datetime.utcnow() - datetime.timedelta(minutes=args.after_minutes_ago)

    start = time.monotonic()
    try:
        submission, metadata = run(client, args.username, args.form_path, after,
                                    args.require_multimedia, args.require_location)
        result = report_generator.TestResult(
            name=f"form_submissions/{args.test_name}_submission_check",
            workflow="form_submissions",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
        )
        print(f"PASS: {submission['path']} at {submission['time']} (form_id={submission['form_id']})")
        for key in ("timeStart", "timeEnd", "received_on", "appVersion", "deviceID"):
            print(f"  {key}: {metadata.get(key)}")
    except Exception as exc:  # noqa: BLE001 - report the real failure, don't mask it
        result = report_generator.TestResult(
            name=f"form_submissions/{args.test_name}_submission_check",
            workflow="form_submissions",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
            error=str(exc),
            failed_step=f"{args.test_name}_submission_check - {exc}",
        )
        print(f"FAIL: {exc}")

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text("N/A (HQ API only, no device)", encoding="utf-8")

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    results = [result]
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"verify-submission-{args.test_name}-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if result.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
