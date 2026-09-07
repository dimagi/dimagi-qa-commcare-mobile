"""
Master Mobile Plan (2026) > Support Menus > "Menu 2" (Force Log Submission) -
the sheet's own pre-requisite is "verify the app does NOT have logenabled=
on_demand set" (i.e. logging is force-submittable on demand, not gated
behind a schedule). flows/support_menus/menu_02_force_log_submission.yaml's
own on-device steps only ever ASSUMED this was true - there is no on-device
way to introspect an app-builder setting - so this script closes that gap by
actively reading the real HQ value first.

Confirmed live 2026-09-01 via HQClient.get_custom_properties() against the
real "[Master] Basic Tests" app (qateam/cdfa6c85eb594b23b0c08729cd2beff1):
its draft custom_properties already has logenabled="on_demand" - the
precondition already holds, this was never actually broken, just previously
unverified. Deliberately READ-ONLY (get_custom_properties, not
set_custom_properties) - set_custom_properties REPLACES the whole
custom_properties dict rather than merging, so blindly "fixing" this value
would risk wiping this app's other 4 real custom properties
(cc-enable-background-sync, num-views-before-reducing-frequency,
reduced-show-frequency, regular-show-frequency) just to check one field.

Usage: python scripts/run_support_menus_log_property_check.py
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import hq_client as hq_client_module
import report_generator
from app_registry import APP_REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPECTED_VALUE = "on_demand"


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
        actual = properties.get("logenabled")
        if actual != EXPECTED_VALUE:
            raise AssertionError(
                f"App {app_id}'s logenabled custom property is {actual!r}, expected "
                f"{EXPECTED_VALUE!r} - Menu 2's own precondition ('the app does NOT "
                f"require a schedule to submit logs') no longer holds. This must be "
                f"fixed on HQ's Advanced Settings > Custom Properties tab before "
                f"menu_02_force_log_submission.yaml's result can be trusted."
            )
        result = report_generator.TestResult(
            name="support_menus/verify_logenabled_precondition",
            workflow="support_menus",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
        )
        print(f"PASS: app {app_id}'s logenabled={actual!r}, matches Menu 2's precondition.")
    except Exception as exc:  # noqa: BLE001 - report the real failure, don't mask it
        result = report_generator.TestResult(
            name="support_menus/verify_logenabled_precondition",
            workflow="support_menus",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device="N/A (HQ API only, no device)",
            error=str(exc),
            failed_step=f"verify_logenabled_precondition - {exc}",
        )
        print(f"FAIL: {exc}")

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text("N/A (HQ API only, no device)", encoding="utf-8")

    build_id = f"support-menus-log-property-check-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, [result], enrich=False)
    print(f"Report written to {report_path}")

    if result.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
