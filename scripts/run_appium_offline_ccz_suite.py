"""
Orchestrator for the 4 Recovery Measures scenarios blocked by the same real
infra gap: none of them can get a .ccz file onto the device's filesystem
before the "Select CCZ" system file picker (Android's Storage Access
Framework) needs to find one - confirmed live via 4 independent failure
screenshots, every one showing only BrowserStack's stock seeded media
(Android_O.png, BrowserStack.jpg, etc.), never a real CCZ, because nothing in
scripts/run_suite.py/browserstack_client.py's Maestro pipeline ever pushes a
file onto the device. See each blocked flow's own "REQUIRED MANUAL/CI
PRE-STEP (not run by Maestro)" header:
    flows/recovery_measures/offline_06_select_ccz_via_picker.yaml
    flows/recovery_measures/offline_08_move_ccz_to_downloads.yaml
    flows/recovery_measures/offline_reinstall_update_app_flow.yaml
    flows/recovery_measures/reinstall_update_05_06_chooser_and_ccz.yaml

BrowserStack's Appium product CAN push a file onto the device mid-session
(scripts/appium_browserstack_client.py's push_file, confirmed live 2026-08-25
via driver.push_file - see that method's own docstring for the full
UnknownMethodException-vs-classic-endpoint citation). This script pushes a
REAL CCZ (committed under resources/ - see resources/README.md's own
2026-08-25 update) onto the device, then drives
scripts/appium_offline_ccz_scenarios.py's step ports of each flow's own
on-device sequence.

UPDATE (2026-08-25), CI wiring: runs as an EXTRA step inside the group-c
matrix job, AFTER run_suite.py's own recovery_measures (and other group-c
tags') Maestro dispatch and the requires_old_client_apk step - same
fold-into-the-matrix-job pattern as run_appium_suite.py's own
updates_partial_failed step. report_generator.generate_report() always
OVERWRITES reports/latest_results.json, so this reads whatever's already
there first and merges by name (replacing only entries THIS run's own
scenarios cover) rather than clobbering every earlier Maestro result in the
same job - identical fix already applied to run_suite.py/run_appium_suite.py
earlier this session, for the exact same reason. A completely fresh CI
checkout only ever writes each test name once, so this never picks up stale
unrelated entries the way repeated LOCAL reruns in the same working
directory can (if testing standalone locally across multiple unrelated
sessions, clear reports/latest_results.json first for a clean read).

Usage:
    python scripts/run_appium_offline_ccz_suite.py --scenario offline_08
    python scripts/run_appium_offline_ccz_suite.py --release-tag commcare_2.64.1 --devices "Samsung Galaxy S26-16.0"
"""
import argparse
import json
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
import appium_offline_ccz_scenarios as offline_scenarios
import appium_scenarios

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

SCENARIOS = ("offline_08", "offline_06", "offline_reinstall_update", "reinstall_05_06")

_SCENARIO_META = {
    # (workflow name for the report, app_registry key, scenario fn)
    "offline_08": ("recovery_measures", "OFFLINE_TEST_ONE", offline_scenarios.run_offline_08_downloads_happy_path),
    "offline_06": ("recovery_measures", "OFFLINE_TEST_ONE", offline_scenarios.run_offline_06_custom_folder_picker),
    "offline_reinstall_update": ("recovery_measures", "OFFLINE_TEST_ONE", offline_scenarios.run_offline_reinstall_update_app_flow),
    "reinstall_05_06": ("recovery_measures", "RU_TEST_TWO", offline_scenarios.run_reinstall_05_06_ccz_branch),
}


def _split_device(devices_arg):
    """"Samsung Galaxy S26-16.0" -> ("Samsung Galaxy S26", "16.0") - same
    convention scripts/run_appium_suite.py's own _split_device already uses."""
    device_name, _, os_version = devices_arg.rpartition("-")
    if not device_name:
        raise SystemExit(f"--devices {devices_arg!r} must be in 'Device Name-OSVersion' form.")
    return device_name, os_version


def _save_failure_evidence(driver, name):
    """Same best-effort screenshot + page_source dump as
    run_appium_suite.py's own helper of the same name."""
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


def _set_browserstack_session_status(driver, result):
    """Same BrowserStack session-status executor call as
    run_appium_suite.py's own helper of the same name."""
    try:
        status = "passed" if result.status == "passed" else "failed"
        reason = (result.failed_step or result.error or "")[:255]
        driver.execute_script(
            "browserstack_executor: " + json.dumps({
                "action": "setSessionStatus",
                "arguments": {"status": status, "reason": reason},
            })
        )
    except Exception:  # noqa: BLE001 - best-effort, never mask the real result
        pass


def _run_one_scenario(bs, name, workflow, app_url, device, os_version, build_name, fn):
    driver = None
    result = None
    start = time.monotonic()
    try:
        # UPDATE (2026-08-26): session_name used to be the raw internal
        # scenario key ("offline_08" etc.) - that's exactly what BrowserStack's
        # own dashboard shows as the test name (confirmed live via a real
        # screenshot of that dashboard), so it rendered unhelpfully generic
        # there even though report_generator.DISPLAY_NAMES already has a
        # proper human-readable entry for each of these 4 scenarios' "_appium"
        # stem. Reuse that same mapping here so the BrowserStack dashboard and
        # this repo's own report.html/Slack output show identical, readable
        # names for the same test.
        display_name = report_generator.display_name(f"{workflow}/{name}_appium", workflow=workflow)
        driver = bs.start_session(app_url, device, os_version, build_name=build_name, session_name=display_name)
        fn(driver)
        result = report_generator.TestResult(
            name=f"{workflow}/{name}_appium",
            workflow=workflow,
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, name)
        result = report_generator.TestResult(
            name=f"{workflow}/{name}_appium",
            workflow=workflow,
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"{name}_appium.py - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001 - session-level infra failure (upload/session-start/push_file/etc.)
        _save_failure_evidence(driver, name)
        result = report_generator.TestResult(
            name=f"{workflow}/{name}_appium",
            workflow=workflow,
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"{name}_appium.py - session/infra error: {exc}",
        )
    finally:
        if driver is not None:
            if result is not None:
                _set_browserstack_session_status(driver, result)
            driver.quit()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", help="Path to an already-downloaded APK (must be the current release build).")
    parser.add_argument("--release-tag", default="", help="GitHub release tag to download if --apk isn't given.")
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0",
                         help="'Device Name-OSVersion', same convention as run_suite.py's own --devices.")
    parser.add_argument("--project", default="QA COMMCARE MOBILE TESTS")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium-offline-ccz")
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=SCENARIOS,
                         help="Run only this scenario (repeatable). Defaults to all 4 - use this to "
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
        download_apk.download(asset["browser_download_url"], apk_path, expected_size=asset["size"])
        apk_commcare_version = release["tag_name"].removeprefix("commcare_")

    # Resolve one app code per distinct app_registry key actually used by
    # scenarios_to_run - avoids logging into an app not needed for this
    # particular --scenario selection. The app itself is still installed at
    # run time via this dynamic HQ app-code, matching resources/README.md's
    # own stated convention for every other flow in this repo. The CCZ
    # pushed onto the device's filesystem for the SAF picker to find is a
    # SEPARATE, genuinely new runtime dependency (unlike the old committed
    # CCZs resources/README.md documents removing on 2026-08-20 - those were
    # unread reference copies for inspecting suite.xml/app_strings.txt while
    # writing flows; this one's bytes are actually read by push_file() every
    # dispatch) - read directly from resources/, matching how
    # resources/commcare_2.45_release.apk is committed and referenced
    # directly rather than downloaded fresh each run.
    needed_keys = {_SCENARIO_META[name][1] for name in scenarios_to_run}
    app_codes = {}
    ccz_paths = {}
    for key in needed_keys:
        domain, app_id, build_id = APP_REGISTRY[key]
        print(f"Resolving install code for {key} ({domain}/{app_id}, build {build_id}) ...")
        hq_client = hq_client_module.HQClient(domain=domain).login(
            username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
        )
        app_codes[key] = hq_client.get_app_install_code(app_id, saved_app_id=build_id, release_first=False)
        ccz_path = REPO_ROOT / "resources" / f"{key}.ccz"
        if not ccz_path.exists():
            raise SystemExit(
                f"Missing {ccz_path} - commit a real CCZ for {key} to resources/ first "
                f"(e.g. HQClient(domain={domain!r}).download_ccz({build_id!r}, {str(ccz_path)!r}))."
            )
        ccz_paths[key] = str(ccz_path)

    bs = AppiumBrowserStackClient()
    print(f"Uploading APK ({apk_path}) to BrowserStack ...")
    app_url = bs.upload_app(apk_path)["app_url"]

    results = []
    for name in scenarios_to_run:
        workflow, app_key, fn = _SCENARIO_META[name]
        app_code = app_codes[app_key]
        local_ccz_path = ccz_paths[app_key]
        print(f"Running {name} (Appium, real CCZ push: {local_ccz_path}) ...")
        result = _run_one_scenario(
            bs, name, workflow, app_url, device, os_version, args.build_name,
            lambda driver, fn=fn, app_code=app_code, local_ccz_path=local_ccz_path:
                fn(driver, bs, app_code, local_ccz_path),
        )
        print(f"  {name}: {result.status}" + (f" - {result.failed_step}" if result.status == "failed" else ""))
        results.append(result)

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text(
            apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    # UPDATE (2026-08-25), corrected: this now runs as an EXTRA step inside
    # group-c's own CI job, AFTER run_suite.py's Maestro dispatch and the
    # requires_old_client_apk step have already written their own results to
    # reports/latest_results.json - report_generator.generate_report()
    # always OVERWRITES that file, so writing only this run's own results
    # (an earlier version of this comment's own reasoning, before the CI
    # wiring decision) would silently drop every earlier Maestro result in
    # the same job. Same replace-by-name merge as run_suite.py/
    # run_appium_suite.py already use for this exact reason. The false-
    # negative risk that merge logic caused during LOCAL standalone testing
    # earlier today (picking up stale unrelated FAILED entries left over
    # from unrelated past local reruns) doesn't apply in real CI, where a
    # fresh checkout never has a pre-existing reports/latest_results.json -
    # if testing standalone locally, clear that file first for a clean read.
    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"appium-offline-ccz-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
