"""
CLI runner for Multimedia > "MM2" (Master Mobile Plan 2026) - see
scripts/appium_mm2_scenario.py's own module docstring for the full
citation of why this needs Appium's push_file rather than Maestro.

Usage:
    python scripts/run_appium_mm2_suite.py
    python scripts/run_appium_mm2_suite.py --release-tag commcare_2.64.1 --devices "Samsung Galaxy S26-16.0"
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
import appium_mm2_scenario as mm2_scenario
import appium_scenarios

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _split_device(devices_arg):
    device_name, _, os_version = devices_arg.rpartition("-")
    if not device_name:
        raise SystemExit(f"--devices {devices_arg!r} must be in 'Device Name-OSVersion' form.")
    return device_name, os_version


def _save_failure_evidence(driver, name):
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
    except Exception as evidence_exc:  # noqa: BLE001
        print(f"  (couldn't capture failure evidence: {evidence_exc})")
        return None


def _set_browserstack_session_status(driver, result):
    try:
        status = "passed" if result.status == "passed" else "failed"
        reason = (result.failed_step or result.error or "")[:255]
        driver.execute_script(
            "browserstack_executor: " + json.dumps({
                "action": "setSessionStatus",
                "arguments": {"status": status, "reason": reason},
            })
        )
    except Exception:  # noqa: BLE001
        pass


def _run_mm2(bs, app_url, device, os_version, build_name, app_code_no_media, local_repair_zip_path):
    driver = None
    result = None
    start = time.monotonic()
    try:
        driver = bs.start_session(app_url, device, os_version, build_name=build_name, session_name="mm2")
        mm2_scenario.run_mm2(driver, bs, app_code_no_media, local_repair_zip_path)
        result = report_generator.TestResult(
            name="multimedia/mm2",
            workflow="multimedia",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, "mm2")
        result = report_generator.TestResult(
            name="multimedia/mm2",
            workflow="multimedia",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"mm2 (Appium) - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001
        _save_failure_evidence(driver, "mm2")
        result = report_generator.TestResult(
            name="multimedia/mm2",
            workflow="multimedia",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"mm2 (Appium) - session/infra error: {exc}",
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
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0")
    parser.add_argument("--project", default="QA COMMCARE MOBILE TESTS")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium-mm2")
    parser.add_argument("--repair-zip", default=None,
                         help="Path to the pre-built repair zip (default: build one fresh from the app's "
                              "current multimedia zip via HQClient).")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    device, os_version = _split_device(args.devices)

    apk_path = args.apk
    apk_commcare_version = None
    if not apk_path:
        release, asset = download_apk.resolve(args.release_tag or None)
        apk_path = f"apks/{asset['name']}"
        print(f"Downloading {asset['name']} from {release['tag_name']} ...")
        download_apk.download(asset["browser_download_url"], apk_path, expected_size=asset["size"])
        apk_commcare_version = release["tag_name"].removeprefix("commcare_")

    domain, app_id = APP_REGISTRY["MULTIMEDIA"]
    print(f"Resolving no-media install code for MULTIMEDIA ({domain}/{app_id}) ...")
    hq = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    app_code_no_media = hq.get_app_install_code(app_id, include_media=False)

    repair_zip_path = args.repair_zip
    if not repair_zip_path:
        (REPO_ROOT / "reports").mkdir(exist_ok=True)
        raw_zip_path = str(REPO_ROOT / "reports" / "mm2_multimedia_raw.zip")
        repair_zip_path = str(REPO_ROOT / "reports" / "mm2_repair.zip")
        print(f"Downloading current multimedia zip for {app_id} ...")
        hq.download_multimedia_zip(app_id, raw_zip_path)
        print(f"Building repair zip (excluding {mm2_scenario.TARGET_ENTRY} + the 2 known-orphaned "
              f"3gp videos) ...")
        mm2_scenario.build_repair_zip(
            raw_zip_path, repair_zip_path,
            missing_entry=mm2_scenario.TARGET_ENTRY,
            also_exclude=mm2_scenario.KNOWN_ORPHANED_ENTRIES,
        )

    bs = AppiumBrowserStackClient()
    print(f"Uploading APK ({apk_path}) to BrowserStack ...")
    app_url = bs.upload_app(apk_path)["app_url"]

    print("Running mm2 (Appium) ...")
    result = _run_mm2(bs, app_url, device, os_version, args.build_name, app_code_no_media, repair_zip_path)
    print(f"  mm2: {result.status}" + (f" - {result.failed_step}" if result.status == "failed" else ""))

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text(
            apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    # Exit code reflects only THIS invocation's own result, not the
    # merged/cumulative list below - see run_suite.py's own citation
    # (CI run 34226264408) for why: merging in an earlier step's carried-
    # forward "failed" entry here would cascade a false failed exit onto
    # this step even when its own result passed.
    this_run_results = [result]

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    results = [result]
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"appium-mm2-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in this_run_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
