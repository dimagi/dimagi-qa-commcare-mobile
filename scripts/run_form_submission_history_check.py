"""
Generic HQ-only post-check for Master Mobile Plan (2026) test cases whose
final steps are "go to Submit History, locate the form you just submitted,
verify its metadata (timeStart/timeEnd etc.) is accurate" - the exact
pattern behind Form Submissions > "Markdown" (row 21, steps 4-7) and
"Incomplete Forms 4" (row 30). Both previously automated only their
on-device half (paging through/submitting the form); this closes the
remaining HQ-side verification using HQClient.find_recent_submission() +
get_form_metadata(), the same Submit-History-report mechanism already
proven live elsewhere in this repo (e.g. verify_submission.py's
--require-location flag).

Confirmed live 2026-09-01 against a real "Markdown" submission
(qateam/test1): find_recent_submission(form_path_contains="Markdown")
correctly located it via its module>form breadcrumb, and get_form_metadata()
returned real, parseable timeStart/timeEnd ISO datetimes (timeEnd after
timeStart, both close to the actual dispatch time) - not merely present-but-
empty fields.

Deliberately does NOT filter by an `after` timestamp: coordinating a shared
"when did the Maestro flow actually run" timestamp across two separate CI
steps/processes is more machinery than this check's real intent needs - the
row's own ask is "does Submit History show a real, correctly-timed
submission for this form", not strictly "the one from this exact run". If a
domain ever accumulates enough unrelated same-named-form submissions for
this to matter, `find_recent_submission` already supports `after=` and this
script's `--after-minutes-ago` flag opts into it.

Usage:
    python scripts/run_form_submission_history_check.py \
        --test-name markdown --username test1 --form-path-contains Markdown
    python scripts/run_form_submission_history_check.py \
        --test-name incomplete_forms_4 --username test1 \
        --form-path-contains "Incomplete Form"
"""
import argparse
import datetime
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import hq_client as hq_client_module
import report_generator

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(hq, username, form_path_contains, after_minutes_ago=None):
    after = None
    if after_minutes_ago is not None:
        after = datetime.datetime.utcnow() - datetime.timedelta(minutes=after_minutes_ago)

    # limit=500, not 50: confirmed live (CI run 34448685828) a real Markdown
    # submission fell off a 50-row Submit History page by the time this
    # check ran - see find_recent_submission()'s own docstring for the full
    # citation.
    submission = hq.find_recent_submission(username, form_path_contains=form_path_contains, after=after, limit=500)
    if submission is None:
        raise AssertionError(
            f"No Submit History entry found for username={username!r} with "
            f"form_path_contains={form_path_contains!r} - either the on-device "
            f"submit step didn't actually reach the server, or the path/username "
            f"filter no longer matches this form's real breadcrumb/submitter."
        )

    metadata = hq.get_form_metadata(submission["form_id"])
    missing = [k for k in ("timeStart", "timeEnd") if not metadata.get(k)]
    if missing:
        raise AssertionError(
            f"Submission {submission['form_id']} ({submission['path']}) is missing "
            f"{missing} in its Form Metadata tab - {metadata}"
        )

    try:
        start = datetime.datetime.fromisoformat(metadata["timeStart"])
        end = datetime.datetime.fromisoformat(metadata["timeEnd"])
    except ValueError as exc:
        raise AssertionError(f"timeStart/timeEnd aren't parseable ISO datetimes: {metadata} ({exc})")

    if end < start:
        raise AssertionError(f"timeEnd ({end}) is before timeStart ({start}) - {metadata}")

    return submission, metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-name", required=True, help="Used only for the report entry's name, e.g. 'markdown'.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--form-path-contains", required=True)
    parser.add_argument("--after-minutes-ago", type=int, default=None,
                         help="Only consider submissions after (now - N minutes). Default: no time filter.")
    parser.add_argument("--domain", default=os.environ.get("HQ_DOMAIN", "qateam"))
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    hq = hq_client_module.HQClient(domain=args.domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    start = time.monotonic()
    try:
        submission, metadata = run(hq, args.username, args.form_path_contains, args.after_minutes_ago)
        result = report_generator.TestResult(
            name=f"form_submissions/{args.test_name}_submit_history_check",
            workflow="form_submissions",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
        )
        print(f"PASS: {submission['path']} - timeStart={metadata['timeStart']}, timeEnd={metadata['timeEnd']}")
    except Exception as exc:  # noqa: BLE001 - report the real failure, don't mask it
        result = report_generator.TestResult(
            name=f"form_submissions/{args.test_name}_submit_history_check",
            workflow="form_submissions",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
            error=str(exc),
            failed_step=f"{args.test_name}_submit_history_check - {exc}",
        )
        print(f"FAIL: {exc}")

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text("N/A (HQ API only, no device)", encoding="utf-8")

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    results = [result]
    if existing_results_path.exists():
        import json
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"form-submission-history-check-{args.test_name}-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if result.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
