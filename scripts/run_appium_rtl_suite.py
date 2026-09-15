"""
CLI runner for Master Mobile Plan (2026) > Right to Left Text > "Home
Screen 8" (row 23) / "Case list 4" (row 36) - see
scripts/appium_rtl_scenarios.py's own module docstring for the full
citation on the device-level Arabic locale mechanism and why neither row
needs CommCare's own in-app language switch or any Arabic-text-matching
selector.

Usage:
    python scripts/run_appium_rtl_suite.py
    python scripts/run_appium_rtl_suite.py --release-tag commcare_2.64.1 --devices "Samsung Galaxy S26-16.0"
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
import appium_scenarios
import appium_rtl_scenarios as rtl

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


def _run_scenario(bs, app_url, device, os_version, build_name, session_name, result_name, run_fn):
    driver = None
    result = None
    start = time.monotonic()
    try:
        driver = bs.start_session(app_url, device, os_version, build_name=build_name, session_name=session_name,
                                   device_language="ar", device_locale="SA")
        run_fn(driver)
        result = report_generator.TestResult(
            name=result_name,
            workflow="right_to_left_text",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, session_name)
        result = report_generator.TestResult(
            name=result_name,
            workflow="right_to_left_text",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"{session_name} (Appium) - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001
        _save_failure_evidence(driver, session_name)
        result = report_generator.TestResult(
            name=result_name,
            workflow="right_to_left_text",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"{session_name} (Appium) - session/infra error: {exc}",
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
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium-rtl")
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

    domain, app_id = APP_REGISTRY["RIGHT_TO_LEFT"]
    print(f"Resolving install code for RIGHT_TO_LEFT ({domain}/{app_id}) ...")
    hq = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    app_code = hq.get_app_install_code(app_id)

    cc_username = os.environ["CC_TEST_USERNAME"]
    cc_password = os.environ["CC_TEST_PASSWORD"]

    bs = AppiumBrowserStackClient()
    print(f"Uploading APK ({apk_path}) to BrowserStack ...")
    app_url = bs.upload_app(apk_path)["app_url"]

    print("Running Home Screen 8 ...")
    home_screen_8_result = _run_scenario(
        bs, app_url, device, os_version, args.build_name, "home_screen_8",
        "right_to_left_text/home_screen_8",
        lambda driver: rtl.run_home_screen_8(driver, app_code, cc_username, cc_password),
    )
    print(f"  home_screen_8: {home_screen_8_result.status}"
          + (f" - {home_screen_8_result.failed_step}" if home_screen_8_result.status == "failed" else ""))

    print("Running Case list 4 ...")
    case_list_4_result = _run_scenario(
        bs, app_url, device, os_version, args.build_name, "case_list_4",
        "right_to_left_text/case_list_4",
        lambda driver: rtl.run_case_list_4(driver, app_code, cc_username, cc_password),
    )
    print(f"  case_list_4: {case_list_4_result.status}"
          + (f" - {case_list_4_result.failed_step}" if case_list_4_result.status == "failed" else ""))

    print("Running Home Screen 1 ...")
    home_screen_1_result = _run_scenario(
        bs, app_url, device, os_version, args.build_name, "home_screen_1",
        "right_to_left_text/home_screen_1",
        lambda driver: rtl.run_home_screen_1(driver, app_code, cc_username, cc_password),
    )
    print(f"  home_screen_1: {home_screen_1_result.status}"
          + (f" - {home_screen_1_result.failed_step}" if home_screen_1_result.status == "failed" else ""))

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text(
            apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    # Exit code reflects only THIS invocation's own results, not the
    # merged/cumulative list below - see run_suite.py's own citation
    # (CI run 34226264408) for why: merging in an earlier step's carried-
    # forward "failed" entry here would cascade a false failed exit onto
    # this step even when its own results passed.
    this_run_results = [home_screen_8_result, case_list_4_result, home_screen_1_result]

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    results = list(this_run_results)
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"appium-rtl-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in this_run_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
