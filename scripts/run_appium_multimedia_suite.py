"""
Orchestrator for Multimedia Appium scenarios blocked by the same real infra
gap already solved for Recovery Measures' offline-CCZ scenarios
(scripts/run_appium_offline_ccz_suite.py): Maestro's `addMedia` can only
place a file into the shared MediaStore/gallery, never at an arbitrary
device filesystem path. See scripts/appium_multimedia_scenarios.py's own
module docstring for the full per-scenario citations.

Usage:
    python scripts/run_appium_multimedia_suite.py --scenario external_file_in_forms
    python scripts/run_appium_multimedia_suite.py --release-tag commcare_2.64.1 --devices "Samsung Galaxy S26-16.0"
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
import appium_multimedia_scenarios as multimedia_scenarios
import appium_scenarios

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

SCENARIOS = ("external_file_in_forms",)

_SCENARIO_META = {
    # (workflow name for the report, flow stem for the report name, scenario fn)
    "external_file_in_forms": ("multimedia", "external_file_in_forms", multimedia_scenarios.run_external_file_in_forms),
}


def _split_device(devices_arg):
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


def _run_one_scenario(bs, name, workflow, stem, app_url, device, os_version, build_name, fn):
    driver = None
    result = None
    start = time.monotonic()
    try:
        driver = bs.start_session(app_url, device, os_version, build_name=build_name, session_name=stem)
        fn(driver)
        result = report_generator.TestResult(
            name=f"{workflow}/{stem}",
            workflow=workflow,
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, stem)
        result = report_generator.TestResult(
            name=f"{workflow}/{stem}",
            workflow=workflow,
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"{stem} (Appium) - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001 - session-level infra failure (upload/session-start/push_file/etc.)
        _save_failure_evidence(driver, stem)
        result = report_generator.TestResult(
            name=f"{workflow}/{stem}",
            workflow=workflow,
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"{stem} (Appium) - session/infra error: {exc}",
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
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium-multimedia")
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=SCENARIOS,
                         help="Run only this scenario (repeatable). Defaults to all.")
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

    cc_username = os.environ["CC_TEST_USERNAME"]
    cc_password = os.environ["CC_TEST_PASSWORD"]

    domain, app_id = APP_REGISTRY["MULTIMEDIA"]
    print(f"Resolving install code for MULTIMEDIA ({domain}/{app_id}) ...")
    hq_client = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    app_code = hq_client.get_app_install_code(app_id)

    local_video_path = str(REPO_ROOT / "flows" / "multimedia" / "assets" / "yourvideo.mp4")
    if not pathlib.Path(local_video_path).exists():
        raise SystemExit(f"Missing {local_video_path} - see scripts/appium_multimedia_scenarios.py's own citation.")

    bs = AppiumBrowserStackClient()
    print(f"Uploading APK ({apk_path}) to BrowserStack ...")
    app_url = bs.upload_app(apk_path)["app_url"]

    results = []
    for name in scenarios_to_run:
        workflow, stem, fn = _SCENARIO_META[name]
        print(f"Running {name} (Appium, real video push: {local_video_path}) ...")
        result = _run_one_scenario(
            bs, name, workflow, stem, app_url, device, os_version, args.build_name,
            lambda driver, fn=fn: fn(driver, bs, app_code, cc_username, cc_password, local_video_path),
        )
        print(f"  {name}: {result.status}" + (f" - {result.failed_step}" if result.status == "failed" else ""))
        results.append(result)

    # RETRY-FAILED, same mechanism/citation as
    # scripts/run_appium_offline_ccz_suite.py's own RETRY-FAILED block.
    failed_names = [name for name, r in zip(scenarios_to_run, results) if r.status == "failed"]
    if failed_names:
        print(f"Retrying {len(failed_names)} failed scenario(s): {', '.join(failed_names)} ...")
        retry_results = []
        for name in failed_names:
            workflow, stem, fn = _SCENARIO_META[name]
            print(f"Running {name} (Appium, real video push, retry) ...")
            retry_result = _run_one_scenario(
                bs, name, workflow, stem, app_url, device, os_version, args.build_name,
                lambda driver, fn=fn: fn(driver, bs, app_code, cc_username, cc_password, local_video_path),
            )
            print(f"  {name}: {retry_result.status}" +
                  (f" - {retry_result.failed_step}" if retry_result.status == "failed" else ""))
            retry_results.append(retry_result)
        results = report_generator.merge_rerun(results, retry_results)

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text(
            apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"appium-multimedia-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
