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
    python scripts/verify_submission.py --username test1 --form-path Markdown
    python scripts/verify_submission.py --username test1 --form-path "Question Types" \
        --require-multimedia --after "2026-08-08T19:00:00"

Exits non-zero (with a clear message) if no matching submission is found,
or if a requested check (multimedia present, timeEnd >= timeStart) fails -
suitable for use as a CI step's own pass/fail gate.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
from hq_client import HQClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", required=True, help="Mobile worker username that submitted the form.")
    parser.add_argument("--form-path", default=None,
                         help='Substring to match against the module>form breadcrumb, e.g. "Markdown".')
    parser.add_argument("--after", default=None,
                         help="ISO timestamp (e.g. 2026-08-08T19:00:00) - only consider submissions after this, "
                              "so you don't match a stale submission from an earlier run.")
    parser.add_argument("--require-multimedia", action="store_true",
                         help="Fail if the matched form has no multimedia attachment.")
    parser.add_argument("--require-location", action="store_true",
                         help='Fail if the matched form\'s metadata "location" field is empty '
                              '(HQ renders an unset location as the literal string "---") - use for '
                              'rows like Geoservice 2 ("Auto Capture Location") that need to confirm '
                              'a geopoint was actually captured into the submission.')
    parser.add_argument("--domain", default=os.environ.get("HQ_DOMAIN", "qateam"))
    args = parser.parse_args()

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    client = HQClient(domain=args.domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"),
        password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    after = datetime.datetime.fromisoformat(args.after) if args.after else None
    submission = client.find_recent_submission(args.username, form_path_contains=args.form_path, after=after)
    if submission is None:
        raise SystemExit(
            f"No submission found for username={args.username!r} "
            f"form_path_contains={args.form_path!r} after={args.after!r}"
        )

    print(f"Found submission: {submission['path']} at {submission['time']} (form_id={submission['form_id']})")
    metadata = client.get_form_metadata(submission["form_id"])
    for key in ("timeStart", "timeEnd", "received_on", "appVersion", "deviceID"):
        print(f"  {key}: {metadata.get(key)}")

    time_start = datetime.datetime.fromisoformat(metadata["timeStart"])
    time_end = datetime.datetime.fromisoformat(metadata["timeEnd"])
    if time_end < time_start:
        raise SystemExit(f"Form metadata is inconsistent: timeEnd ({time_end}) is before timeStart ({time_start}).")

    if args.require_multimedia and not metadata.get("has_multimedia"):
        raise SystemExit("Expected multimedia attached to this form, but none was found.")

    if args.require_location and metadata.get("location", "---") == "---":
        raise SystemExit("Expected a captured location on this form, but metadata.location is empty.")

    print("Submission verified OK.")


if __name__ == "__main__":
    main()
