"""
Orchestrator for the 3 updates_partial_failed scenarios (Test Case 1, 2, 5)
whose Setup step needs installing a NEWER CommCare binary mid-session, while
the app is mid-way through staging/downloading a CCZ update - genuinely
impossible with Maestro (confirmed against Maestro's own command reference:
only launchApp, which requires the app already installed - see
flows/updates_partial_failed/scenario_1_staged_update_auto_apply.yaml's own
"REMAINING GAP" note for the full citation) but supported by BrowserStack App
Automate's Appium sessions via the `midSessionInstallApps` capability +
`mobile: installApp` (https://www.browserstack.com/docs/app-automate/appium/advanced-features/test-app-upgrades),
provided both APK builds share the same signing certificate + bundle id.
Verified directly (not just from docs, 2026-08-19) that
resources/commcare_2.45_release.apk (old) and the current release APK share
an identical certificate SHA-256 fingerprint - so this genuinely applies
here.

This is intentionally a SEPARATE, additive path from scripts/run_suite.py's
Maestro pipeline - it drives real Appium WebDriver sessions
(scripts/appium_browserstack_client.py) through
scripts/appium_scenarios.py's step ports of each scenario's Maestro
counterpart, then writes a reports/latest_results.json in the exact
report_generator.TestResult shape scripts/merge_reports.py already expects
from every other matrix job's artifact - so this run's results fold into the
same merged HTML report/Slack notification with no changes needed there.

Usage:
    python scripts/run_appium_suite.py --build-name "QA-COMMCARE-MOBILE-group-d"
    python scripts/run_appium_suite.py --release-tag commcare_2.64.0 --devices "Samsung Galaxy S26-16.0"
"""
import argparse
import os
import pathlib
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import download_apk
import hq_client as hq_client_module
import report_generator
from app_registry import APP_REGISTRY
from appium_browserstack_client import AppiumBrowserStackClient
import appium_scenarios

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OLD_APK_PATH = REPO_ROOT / "resources" / "commcare_2.45_release.apk"

SCENARIOS = ("scenario_1", "scenario_2", "scenario_5")


def _split_device(devices_arg):
    """"Samsung Galaxy S26-16.0" -> ("Samsung Galaxy S26", "16.0") - same
    combined-string convention scripts/run_suite.py's own --devices already
    uses, split here since Appium's W3C capabilities want deviceName/
    osVersion as separate fields (Maestro's trigger_build wants the combined
    string as-is, so run_suite.py never needed to split it)."""
    device_name, _, os_version = devices_arg.rpartition("-")
    if not device_name:
        raise SystemExit(f"--devices {devices_arg!r} must be in 'Device Name-OSVersion' form.")
    return device_name, os_version


def _run_one_scenario(bs, name, old_app_url, new_app_url, device, os_version, build_name, env, fn):
    driver = None
    start = time.monotonic()
    try:
        driver = bs.start_session(
            old_app_url, device, os_version,
            build_name=build_name, session_name=name,
            mid_session_apps=[new_app_url],
        )
        fn(driver)
        return report_generator.TestResult(
            name=f"updates_partial_failed/{name}_appium",
            workflow="updates_partial_failed",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        return report_generator.TestResult(
            name=f"updates_partial_failed/{name}_appium",
            workflow="updates_partial_failed",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"{name}_appium.py - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001 - session-level infra failure (upload/session-start/etc.)
        return report_generator.TestResult(
            name=f"updates_partial_failed/{name}_appium",
            workflow="updates_partial_failed",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"{name}_appium.py - session/infra error: {exc}",
        )
    finally:
        if driver is not None:
            driver.quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", help="Path to an already-downloaded NEW-version APK.")
    parser.add_argument("--release-tag", default="", help="GitHub release tag to download if --apk isn't given.")
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0",
                         help="'Device Name-OSVersion', same convention as run_suite.py's own --devices.")
    parser.add_argument("--project", default="QA COMMCARE MOBILE TESTS")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-group-d")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    device, os_version = _split_device(args.devices)

    apk_path = args.apk
    apk_commcare_version = None
    if not apk_path:
        release, asset = download_apk.resolve(args.release_tag or None)
        apk_path = f"apks/{asset['name']}"
        print(f"Downloading {asset['name']} from {release['tag_name']} ...")
        download_apk.download(asset["browser_download_url"], apk_path)
        apk_commcare_version = release["tag_name"].removeprefix("commcare_")

    print(f"Resolving install code for MOBILE_UPDATES_1_2 ...")
    app_codes = hq_client_module.resolve_app_codes(
        {"MOBILE_UPDATES_1_2": APP_REGISTRY["MOBILE_UPDATES_1_2"]},
        max_commcare_version=apk_commcare_version,
    )
    app_code = app_codes["APP_CODE_MOBILE_UPDATES_1_2"]

    bs = AppiumBrowserStackClient()
    print(f"Uploading old APK ({OLD_APK_PATH.name}) to BrowserStack ...")
    old_app_url = bs.upload_app(str(OLD_APK_PATH))["app_url"]
    print(f"Uploading new APK ({apk_path}) to BrowserStack ...")
    new_app_url = bs.upload_app(apk_path)["app_url"]

    cc_username = os.environ["CC_TEST_USERNAME"]
    cc_password = os.environ["CC_TEST_PASSWORD"]

    scenario_fns = {
        "scenario_1": lambda driver: appium_scenarios.run_scenario_1(
            driver, bs, new_app_url, cc_username, cc_password, app_code),
        "scenario_2": lambda driver: appium_scenarios.run_scenario_2(
            driver, bs, new_app_url, cc_username, cc_password, app_code),
        "scenario_5": lambda driver: appium_scenarios.run_scenario_5(
            driver, bs, new_app_url, cc_username, cc_password,
            os.environ["HQ_MOBILE_WORKER_USERNAME"], os.environ["HQ_DOMAIN"],
            os.environ["HQ_MOBILE_WORKER_PASSWORD"]),
    }

    results = []
    for name in SCENARIOS:
        print(f"Running {name} (Appium, mid-session binary swap) ...")
        result = _run_one_scenario(
            bs, name, old_app_url, new_app_url, device, os_version, args.build_name,
            os.environ, scenario_fns[name],
        )
        print(f"  {name}: {result.status}" + (f" - {result.failed_step}" if result.status == "failed" else ""))
        results.append(result)

    if apk_commcare_version:
        (REPO_ROOT / "reports").mkdir(exist_ok=True)
        (REPO_ROOT / "reports" / "apk_version.txt").write_text(apk_commcare_version, encoding="utf-8")

    build_id = f"appium-group-d-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
