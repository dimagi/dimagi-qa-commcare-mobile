"""
HQ-only Submit-History post-checks for Master Mobile Plan (2026) >
Multimedia rows whose final steps read the Form Data page rather than the
device - closes the remaining gap on top of an already-automated on-device
submit flow, same pattern as run_form_submission_history_check.py, using
the newly added HQClient.get_form_answers()/get_form_attachments().

--check-type unanswered: "Photo Verification" (row 47) - verify specific
questions ("Take a photo", "Choose Image") show NO answer on the most
recent matching submission. Confirmed live 2026-09-07 against a real Photo 2
submission (form_id 529c2e38-be89-43a4-859a-3540b5b0f5d4): both questions
come back as an empty string via get_form_answers()'s table-based parsing.

--check-type attachments: "Image Resize 27/28" (rows 41-42) - verify the
most recent matching submission's attachments are present and downloadable
(a real Content-Length > 0 for each). Deliberately does NOT compare sizes
against each other to confirm "sized as expected (small/medium/large/
original)": confirmed live against a real Image Resize submission that
every question's attachment came back byte-identical (75 bytes each) since
BrowserStack's test devices all pick the same tiny placeholder gallery
image - there is no real resize-magnitude signal to assert on with this
test asset, the same class of "requires an actual varying-size source
image + human visual judgment" limit already documented on the sibling
Image Resize rows. Presence/downloadability is the honest, fully-verifiable
subset of the row's own steps ("verify you can download the attachments").

Usage:
    python scripts/run_multimedia_form_data_check.py --check-type unanswered \
        --test-name photo_verification --username test1 \
        --form-path-contains "Gif/Remove Image" \
        --expect-unanswered "Take a photo" --expect-unanswered "Choose Image"

    python scripts/run_multimedia_form_data_check.py --check-type attachments \
        --test-name image_resize_27 --username test1 \
        --form-path-contains "Image Resize" --min-attachments 3
"""
import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import hq_client as hq_client_module
import report_generator

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def run_unanswered(hq, username, form_path_contains, expect_unanswered):
    submission = hq.find_recent_submission(username, form_path_contains=form_path_contains, limit=50)
    if submission is None:
        raise AssertionError(
            f"No Submit History entry found for username={username!r} with "
            f"form_path_contains={form_path_contains!r}."
        )
    answers = hq.get_form_answers(submission["form_id"])
    still_answered = {q: answers.get(q) for q in expect_unanswered if answers.get(q)}
    missing_entirely = [q for q in expect_unanswered if q not in answers]
    if still_answered:
        raise AssertionError(
            f"Submission {submission['form_id']} ({submission['path']}) has a real "
            f"answer for question(s) expected to be blank: {still_answered}"
        )
    if missing_entirely:
        raise AssertionError(
            f"Submission {submission['form_id']} ({submission['path']}) has no "
            f"question matching {missing_entirely} at all - either the label text "
            f"changed, or this isn't the right form. Real questions found: "
            f"{list(answers.keys())}"
        )
    return submission, answers


def run_attachments(hq, username, form_path_contains, min_attachments):
    submission = hq.find_recent_submission(username, form_path_contains=form_path_contains, limit=50)
    if submission is None:
        raise AssertionError(
            f"No Submit History entry found for username={username!r} with "
            f"form_path_contains={form_path_contains!r}."
        )
    attachments = hq.get_form_attachments(submission["form_id"])
    if len(attachments) < min_attachments:
        raise AssertionError(
            f"Submission {submission['form_id']} ({submission['path']}) has only "
            f"{len(attachments)} attachment(s), expected at least {min_attachments}: "
            f"{attachments}"
        )
    empty_or_undownloadable = [a for a in attachments if not a["size_bytes"]]
    if empty_or_undownloadable:
        raise AssertionError(
            f"Submission {submission['form_id']} ({submission['path']}) has "
            f"attachment(s) that are empty or not downloadable: {empty_or_undownloadable}"
        )
    return submission, attachments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-type", required=True, choices=["unanswered", "attachments"])
    parser.add_argument("--test-name", required=True, help="Used only for the report entry's name.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--form-path-contains", required=True)
    parser.add_argument("--expect-unanswered", action="append", default=[],
                         help="(unanswered check) Question label expected to have no answer. Repeatable.")
    parser.add_argument("--min-attachments", type=int, default=1,
                         help="(attachments check) Minimum number of attachments expected.")
    parser.add_argument("--domain", default=os.environ.get("HQ_DOMAIN", "qateam"))
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    hq = hq_client_module.HQClient(domain=args.domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    start = time.monotonic()
    try:
        if args.check_type == "unanswered":
            if not args.expect_unanswered:
                raise SystemExit("--check-type unanswered requires at least one --expect-unanswered")
            submission, detail = run_unanswered(hq, args.username, args.form_path_contains, args.expect_unanswered)
            print(f"PASS: {submission['path']} - {args.expect_unanswered} confirmed unanswered.")
        else:
            submission, detail = run_attachments(hq, args.username, args.form_path_contains, args.min_attachments)
            print(f"PASS: {submission['path']} - {len(detail)} attachment(s), all present and downloadable.")
        result = report_generator.TestResult(
            name=f"multimedia/{args.test_name}_form_data_check",
            workflow="multimedia",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
        )
    except Exception as exc:  # noqa: BLE001 - report the real failure, don't mask it
        result = report_generator.TestResult(
            name=f"multimedia/{args.test_name}_form_data_check",
            workflow="multimedia",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
            error=str(exc),
            failed_step=f"{args.test_name}_form_data_check - {exc}",
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

    build_id = f"multimedia-form-data-check-{args.test_name}-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if result.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
