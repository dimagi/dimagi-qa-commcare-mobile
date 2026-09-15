"""
Master Mobile Plan (2026) > Form Submissions > "Graceful Session Pause"
(row 57) - the sheet's own pre-requisite is "HQ Apps should have the custom
property 'cc-auto-form-save-on-pause' set to 'yes'". The row's own on-device
steps (kill the app mid-form via force-stop, relaunch, confirm the draft is
restored) were never automatable regardless - this repo has a confirmed dead
end on terminateApp/backgroundApp/adb_shell-style forced termination on
BrowserStack (Maestro's stop/kill primitives don't reproduce a genuine crash
the way this row's own steps need) - but the PRECONDITION itself is a plain
HQ custom-properties read, the same pattern already proven for Support
Menus' own precondition check (run_support_menus_log_property_check.py).

Property name confirmed against commcare-android source (not guessed):
MainConfigurablePreferences.java's AUTO_SAVE_FORM_ON_PAUSE constant =
"cc-auto-form-save-on-pause" (app/src/org/commcare/preferences/
MainConfigurablePreferences.java:38).

Deliberately READ-ONLY (get_custom_properties, not set_custom_properties) -
same reasoning as the Support Menus check: set_custom_properties REPLACES
the whole custom_properties dict rather than merging, so blindly "fixing"
this value would risk wiping this app's other real custom properties.

Usage: python scripts/run_graceful_session_pause_precondition_check.py
"""
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import hq_client as hq_client_module
import report_generator
from app_registry import APP_REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROPERTY_KEY = "cc-auto-form-save-on-pause"
EXPECTED_VALUE = "yes"


def main():
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    domain, app_id = APP_REGISTRY["BASIC_TESTS"]
    hq = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    start = time.monotonic()
    try:
        properties = hq.get_custom_properties(app_id)
        actual = properties.get(PROPERTY_KEY)
        if actual != EXPECTED_VALUE:
            raise AssertionError(
                f"App {app_id}'s {PROPERTY_KEY!r} custom property is {actual!r}, expected "
                f"{EXPECTED_VALUE!r} - Graceful Session Pause's own precondition ('a paused/"
                f"killed form is auto-saved as a draft') does not currently hold. Set this "
                f"on HQ's Advanced Settings > Custom Properties tab (merged with the app's "
                f"other existing custom properties, not replacing them) before this row can "
                f"be trusted even at the precondition level."
            )
        result = report_generator.TestResult(
            name="form_submissions/verify_graceful_session_pause_precondition",
            workflow="form_submissions",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
        )
        print(f"PASS: app {app_id}'s {PROPERTY_KEY}={actual!r}, matches the precondition.")
    except Exception as exc:  # noqa: BLE001 - report the real failure, don't mask it
        result = report_generator.TestResult(
            name="form_submissions/verify_graceful_session_pause_precondition",
            workflow="form_submissions",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
            error=str(exc),
            failed_step=f"verify_graceful_session_pause_precondition - {exc}",
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

    build_id = f"graceful-session-pause-precondition-check-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if result.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
