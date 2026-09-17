"""
Master Mobile Plan (2026) > Form Submissions > "Graceful Session Pause"
(row 57) - the sheet's own pre-requisite is "HQ Apps should have the custom
property 'cc-auto-form-save-on-pause' set to 'yes'".

UPDATE (2026-09-17), per direct user-supplied screenshot + live
re-verification: this check was pointed at the WRONG app. APP_REGISTRY
["BASIC_TESTS"] (the main shared "[Master] Basic Tests" app) genuinely does
NOT have this property set (confirmed live again today via
get_custom_properties: {'cc-enable-background-sync': 'yes', 'logenabled':
'on_demand', 'num-views-before-reducing-frequency': '3',
'reduced-show-frequency': '4', 'regular-show-frequency': '2'} - no
cc-auto-form-save-on-pause key at all) - but APP_REGISTRY
["BASIC_TESTS_NS_COPY"] (the dedicated "[Master] Basic Tests NS Copy!!!"
copy already used elsewhere in this repo for prompted_update_scenario_01/02
and auto_cc_update_03, precisely so mutation-heavy tests don't collide with
the shared main app) DOES have it, confirmed live: get_custom_properties
returns {'cc-auto-form-save-on-pause': 'yes', ...}. Switched this check to
BASIC_TESTS_NS_COPY accordingly - this was never a "the property is
genuinely missing everywhere" gap, just a check pointed at the wrong one of
two near-identical apps, same class of mismatch already root-caused for
this NS Copy app's signature widget (capture_01_gather_signature.yaml) and
its prompted-update settings (see app_registry.py's own citations).

The row's own on-device steps (force-stop the app mid-form, relaunch,
confirm the draft is restored) were ALSO genuinely re-tried live against
this now-correctly-configured app (installed via app-code, logged in,
navigated Start > Basic_Form_Tests > Question_Types!, answered a question,
then called Appium's driver.terminate_app("org.commcare.dalvik") -
`mobile: terminateApp`, NOT Maestro's killApp/backgroundApp and NOT
adb_shell) - and genuinely still fail: terminate_app raised a real
UnknownMethodException, "Unknown mobile command 'terminateApp'", whose own
error message enumerates every mobile: command this BrowserStack Appium
driver build (appium-uiautomator2-driver on Appium 1.22.0) supports, and
NONE of terminateApp/activateApp/backgroundApp are in that list. "shell" is
nominally listed but this repo separately confirmed elsewhere that
BrowserStack disables the underlying adb_shell feature account-wide, so a
real `am force-stop` isn't reachable that way either. No
appium_graceful_session_pause_scenarios.py/run_graceful_session_pause_suite.py
was built, since the on-device mechanism this row needs is confirmed, with
concrete fresh evidence (not a guess or an overgeneralized carryover), to
not exist on this harness. See coverage/coverage_matrix.csv's own Graceful
Session Pause row for the full citation.

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

    domain, app_id = APP_REGISTRY["BASIC_TESTS_NS_COPY"]
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
