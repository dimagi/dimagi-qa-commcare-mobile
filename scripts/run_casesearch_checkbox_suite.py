"""
CLI runner for Master Mobile Plan (2026) > Form Submissions > "Casesearch
Checkbox 1" (row 55) / "Casesearch Checkbox 2" (row 56) - see
scripts/appium_casesearch_scenarios.py's own module docstring for the full
citation.

Per direct user instruction, this is a REPEATABLE CI step: every run adds
the Genre checkbox Case Search property to "Songs (See More)", creates a
real app build from it, verifies the on-device multi-select behavior, then
reverts the property - all against the module's CURRENT search_config read
fresh at the start of THIS run (never a hardcoded snapshot), so a
colleague's own concurrent manual App Builder edits to this same app are
preserved rather than clobbered. The revert always runs (try/finally),
even if the on-device step fails, so a failed run doesn't leave the
temporary property behind.

Usage:
    python scripts/run_casesearch_checkbox_suite.py
    python scripts/run_casesearch_checkbox_suite.py --release-tag commcare_2.64.1 --devices "Samsung Galaxy S26-16.0"
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
import appium_casesearch_scenarios as csc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE_ID = "fe023f64e4594285b8b8796ad6b5c58c"  # "Songs (See More)"


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", help="Path to an already-downloaded APK (must be the current release build).")
    parser.add_argument("--release-tag", default="", help="GitHub release tag to download if --apk isn't given.")
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0")
    parser.add_argument("--project", default="QA COMMCARE MOBILE TESTS")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium-casesearch-checkbox")
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

    domain, app_id = APP_REGISTRY["CASE_SEARCH_AND_CLAIM"]
    hq = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    print(f"Reading current search_config for module {MODULE_ID} ...")
    original_search_config = hq.get_module_search_config(app_id, MODULE_ID)
    original_properties_input = [
        hq_client_module.HQClient._search_property_stored_to_input(p)
        for p in original_search_config.get("properties", [])
    ]
    original_names = [p["name"] for p in original_properties_input]
    print(f"  current properties: {original_names}")

    checkbox_1_start = time.monotonic()
    checkbox_1_result = None
    driver = None
    checkbox_2_result = None
    bs = None
    try:
        print("Adding Genre checkbox property (Casesearch Checkbox 1) ...")
        new_properties = original_properties_input + [csc.GENRE_CHECKBOX_PROPERTY_INPUT]
        hq.set_module_search_properties(app_id, MODULE_ID, new_properties, current_search_config=original_search_config)
        confirmed = hq.get_module_search_config(app_id, MODULE_ID)
        confirmed_genre = next((p for p in confirmed.get("properties", []) if p.get("name") == "genre"), None)
        if not confirmed_genre or confirmed_genre.get("input_") != "checkbox":
            raise AssertionError(f"Genre property didn't land as checkbox format: {confirmed_genre}")
        checkbox_1_result = report_generator.TestResult(
            name="form_submissions/casesearch_checkbox_1",
            workflow="form_submissions",
            status="passed",
            duration_ms=int((time.monotonic() - checkbox_1_start) * 1000),
            device="N/A (HQ API only, no device)",
        )
        print("  Casesearch Checkbox 1: passed")

        print("Creating a new build from this change ...")
        hq.create_new_build(
            app_id,
            comment="Temporary build for Casesearch Checkbox 1/2 verification (scripts/run_casesearch_checkbox_suite.py) - reverted after test",
        )
        app_code = hq.get_app_install_code(app_id)

        bs = AppiumBrowserStackClient()
        print(f"Uploading APK ({apk_path}) to BrowserStack ...")
        app_url = bs.upload_app(apk_path)["app_url"]

        cc_username = os.environ["CC_TEST_USERNAME"]
        cc_password = os.environ["CC_TEST_PASSWORD"]

        print("Running Casesearch Checkbox 2 (on-device multi-select verification) ...")
        checkbox_2_start = time.monotonic()
        try:
            driver = bs.start_session(app_url, device, os_version, build_name=args.build_name,
                                       session_name="casesearch_checkbox_2")
            csc.run_casesearch_checkbox_2(driver, app_code, cc_username, cc_password)
            checkbox_2_result = report_generator.TestResult(
                name="form_submissions/casesearch_checkbox_2",
                workflow="form_submissions",
                status="passed",
                duration_ms=int((time.monotonic() - checkbox_2_start) * 1000),
                device=f"{device}-{os_version}",
            )
        except appium_scenarios.ScenarioFailure as exc:
            _save_failure_evidence(driver, "casesearch_checkbox_2")
            checkbox_2_result = report_generator.TestResult(
                name="form_submissions/casesearch_checkbox_2",
                workflow="form_submissions",
                status="failed",
                duration_ms=int((time.monotonic() - checkbox_2_start) * 1000),
                device=f"{device}-{os_version}",
                error=str(exc.original),
                failed_step=f"casesearch_checkbox_2 (Appium) - {exc.step_name}: {exc.original}",
            )
        except Exception as exc:  # noqa: BLE001
            _save_failure_evidence(driver, "casesearch_checkbox_2")
            checkbox_2_result = report_generator.TestResult(
                name="form_submissions/casesearch_checkbox_2",
                workflow="form_submissions",
                status="failed",
                duration_ms=int((time.monotonic() - checkbox_2_start) * 1000),
                device=f"{device}-{os_version}",
                error=str(exc),
                failed_step=f"casesearch_checkbox_2 (Appium) - session/infra error: {exc}",
            )
        finally:
            if driver is not None:
                if checkbox_2_result is not None:
                    _set_browserstack_session_status(driver, checkbox_2_result)
                driver.quit()

    except Exception as exc:  # noqa: BLE001 - Checkbox 1 (the HQ config step) itself failed
        if checkbox_1_result is None:
            checkbox_1_result = report_generator.TestResult(
                name="form_submissions/casesearch_checkbox_1",
                workflow="form_submissions",
                status="failed",
                duration_ms=int((time.monotonic() - checkbox_1_start) * 1000),
                device="N/A (HQ API only, no device)",
                error=str(exc),
                failed_step=f"casesearch_checkbox_1 - {exc}",
            )
        print(f"  Casesearch Checkbox 1 FAILED: {exc}")
        if checkbox_2_result is None:
            checkbox_2_result = report_generator.TestResult(
                name="form_submissions/casesearch_checkbox_2",
                workflow="form_submissions",
                status="failed",
                duration_ms=0,
                device="N/A",
                error="Skipped: Casesearch Checkbox 1's own HQ config step failed",
                failed_step="casesearch_checkbox_2 - skipped, precondition (Checkbox 1) failed",
            )
    finally:
        # UPDATE (2026-09-16), confirmed live: a real run's revert POST hit a
        # transient 'Connection aborted... RemoteDisconnected' network error
        # and left the Genre property live on this SHARED app until caught
        # and fixed manually - a single-attempt revert is not safe enough
        # for a step whose whole job is cleaning up a mutation on an app
        # other people also edit. Retries the revert (not just the read-back
        # check) up to 3 times with a short backoff before giving up.
        print("Reverting search_config to its original state ...")
        revert_failed = True
        last_revert_exc = None
        for attempt in range(3):
            try:
                hq.set_module_search_properties(app_id, MODULE_ID, original_properties_input,
                                                 current_search_config=original_search_config)
                reverted = hq.get_module_search_config(app_id, MODULE_ID)
                reverted_names = [p.get("name") for p in reverted.get("properties", [])]
                if reverted_names == original_names:
                    print(f"  Reverted successfully - properties back to {reverted_names}")
                    revert_failed = False
                    break
                print(f"  Attempt {attempt + 1}: revert mismatch - expected {original_names}, got {reverted_names}")
            except Exception as revert_exc:  # noqa: BLE001
                last_revert_exc = revert_exc
                print(f"  Attempt {attempt + 1}: revert failed: {revert_exc}")
            if attempt < 2:
                time.sleep(5)
        if revert_failed:
            print(f"  *** WARNING: revert did NOT succeed after 3 attempts (last error: {last_revert_exc}) - "
                  f"manual cleanup of module {MODULE_ID} on app {app_id} (domain {domain}) is needed to remove "
                  f"the temporary Genre property left on this SHARED app. ***")

    print(f"  casesearch_checkbox_1: {checkbox_1_result.status}"
          + (f" - {checkbox_1_result.failed_step}" if checkbox_1_result.status == "failed" else ""))
    print(f"  casesearch_checkbox_2: {checkbox_2_result.status}"
          + (f" - {checkbox_2_result.failed_step}" if checkbox_2_result.status == "failed" else ""))

    # A failed revert leaves the SHARED app mutated for everyone else, which
    # is a worse outcome than a normal test failure and must never pass
    # silently just because checkbox_1/2 themselves happened to succeed.
    if revert_failed:
        checkbox_1_result.status = "failed"
        checkbox_1_result.error = (checkbox_1_result.error or "") + f" | REVERT FAILED: {last_revert_exc}"
        checkbox_1_result.failed_step = (
            f"casesearch_checkbox_1 - revert did not succeed after 3 attempts, temporary Genre property "
            f"left on shared module {MODULE_ID}: {last_revert_exc}"
        )

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text(
            apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    this_run_results = [checkbox_1_result, checkbox_2_result]

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    results = list(this_run_results)
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"casesearch-checkbox-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in this_run_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
