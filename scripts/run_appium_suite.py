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

This drives real Appium WebDriver sessions
(scripts/appium_browserstack_client.py) through scripts/appium_scenarios.py's
step ports of each scenario's Maestro counterpart, then MERGES its results
into whatever reports/latest_results.json a scripts/run_suite.py invocation
earlier in the same job already wrote (see the merge logic near the bottom
of main() - report_generator.generate_report() always overwrites that file,
so this reads it first rather than clobbering the Maestro results). Meant to
run as an EXTRA step inside an EXISTING run_suite.py matrix job whose tags
include updates_partial_failed (see .github/workflows/maestro-browserstack.yml's
own conditional step, gated the same way externalapp_tests' companion-app
upload already is) - per direct user question, a whole separate CI job/
artifact for just these 3 scenarios wasn't actually needed.

Usage (standalone, e.g. for local testing):
    python scripts/run_appium_suite.py --scenario scenario_1
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


def _save_failure_evidence(driver, name):
    """Best-effort screenshot + page_source dump on failure, same "get real
    evidence before guessing again" discipline this repo's own Maestro
    hierarchy-dump investigations already use - Appium has no BrowserStack
    dashboard command-log equivalent readily queryable here, so this is the
    fastest way to see exactly what was on screen at the failure point."""
    if driver is None:
        return None
    try:
        out_dir = REPO_ROOT / "reports" / "appium_failures"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        png_path = out_dir / f"{name}_{stamp}.png"
        xml_path = out_dir / f"{name}_{stamp}.xml"
        driver.get_screenshot_as_file(str(png_path))
        xml_path.write_text(driver.page_source, encoding="utf-8")
        print(f"  Failure evidence saved: {png_path}, {xml_path} "
              f"(BrowserStack session id: {driver.session_id})")
        return png_path
    except Exception as evidence_exc:  # noqa: BLE001 - best-effort, never mask the real failure
        print(f"  (couldn't capture failure evidence: {evidence_exc})")
        return None


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
        _save_failure_evidence(driver, name)
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
        _save_failure_evidence(driver, name)
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
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium")
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=SCENARIOS,
                         help="Run only this scenario (repeatable). Defaults to all 3 - use this to "
                              "verify one at a time, same philosophy as run_suite.py's own --flow.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    device, os_version = _split_device(args.devices)
    scenarios_to_run = args.scenarios or list(SCENARIOS)

    apk_path = args.apk
    apk_commcare_version = None
    if not apk_path:
        release, asset = download_apk.resolve(args.release_tag or None)
        apk_path = f"apks/{asset['name']}"
        print(f"Downloading {asset['name']} from {release['tag_name']} ...")
        download_apk.download(asset["browser_download_url"], apk_path)
        apk_commcare_version = release["tag_name"].removeprefix("commcare_")

    # UPDATE (2026-08-19), confirmed live (3/3 identical failures, "Setting
    # Up App / Locating application..." resetting to Welcome every time -
    # not flaky, reproducible): this app code gets typed into the OLD 2.45
    # binary FIRST (that's the whole point of this scenario), but was being
    # resolved filtered by the NEW apk's version (apk_commcare_version, e.g.
    # 2.64.0) - per hq_client.resolve_app_codes' own docstring,
    # max_commcare_version must be "the actual CommCare APK version under
    # test... a build newer than what's installed can never finish an
    # online/Enter-Code install". The old 2.45 binary is what's actually
    # performing this install, so that's the version that belongs here.
    print(f"Resolving install code for MOBILE_UPDATES_1_2 ...")
    old_apk_commcare_version = "2.45"
    app_codes = hq_client_module.resolve_app_codes(
        {"MOBILE_UPDATES_1_2": APP_REGISTRY["MOBILE_UPDATES_1_2"]},
        max_commcare_version=old_apk_commcare_version,
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
    for name in scenarios_to_run:
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

    # UPDATE (2026-08-19): this is meant to run as an EXTRA step inside an
    # existing run_suite.py matrix job (see .github/workflows/
    # maestro-browserstack.yml's own conditional, same pattern already used
    # for externalapp_tests' companion-app upload), not a separate CI job -
    # per direct user question, there was no real need for a whole new
    # "group-d". report_generator.generate_report() always OVERWRITES
    # reports/latest_results.json, though - if run_suite.py already wrote
    # one earlier in the SAME job, overwriting it here would silently drop
    # every Maestro result from that job's own artifact. Load and merge with
    # whatever's already there first, same TestResult(**item) pattern
    # scripts/merge_reports.py already uses to combine multiple artifacts.
    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    if existing_results_path.exists():
        import json
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        results = [report_generator.TestResult(**item) for item in existing] + results

    build_id = f"appium-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
