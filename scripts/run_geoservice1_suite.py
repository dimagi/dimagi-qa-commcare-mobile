"""
CLI runner for Master Mobile Plan (2026) > Form Submissions > "Geoservice 1"
(row 46, "Default Map Tileset") - see
scripts/appium_geoservice1_scenarios.py's own module docstring for the full
citation of what this is/isn't able to assert.

THIS IS A PIPELINE-VERIFICATION TOOL, NOT (yet) CI-wired automation for the
row's own full "cycle through Satellite -> Terrain -> Hybrid" narrative.
Per direct user instruction (2026-09-17): only ONE tileset value needs to be
proven end-to-end (property-write -> build -> install -> map-screen-loads)
before deciding whether the full 3x cycle is worth building out as
permanent CI automation, given it still cannot assert WHICH tileset
actually rendered (see appium_geoservice1_scenarios.py's docstring point 2)
- its main value is the config-write + regression-check portion, the same
precedent as Case Filters' Case Lists 1/2 rows (a cross-app map handoff
left Partial/manual for its own visual half).

Mutates BASIC_TESTS_NS_COPY's REAL, shared `cc-maps-default-layer` profile
property and creates a REAL new build to do so (HQ has no draft-only-can't
create build path for a property write to take effect on a fresh install -
the online-install/app-code flow always installs a BUILD, not the live
draft). By default (--no-revert not passed), reads the property's value
BEFORE mutating it and restores that exact original value via a second
create_new_build() in a `finally` block - same try/finally-with-verified-
readback convention as scripts/run_casesearch_checkbox_suite.py's own
module search_config revert, adapted for profile.properties. This does
leave two extra, permanent build-history entries on BASIC_TESTS_NS_COPY
(the temporary tileset build + the revert build) - builds aren't
deletable on HQ (same accepted tradeoff as app_registry.py's own
CASE_MANAGEMENTS_VARYING_PROMPT v233/v234 citation) - but the app's
CURRENT top build/profile.properties state ends up unchanged from before
this ran.

Pass --no-revert to instead leave the new tileset value as the app's real,
permanent current state (e.g. once the user has decided on the full 3x
cycle and wants each step to actually stick, per the row's own literal
steps 3/9/12 "Save and Publish a new version").

DANGEROUS BUG CAUGHT AND FIXED (2026-09-17) DURING THIS FILE'S OWN FIRST
REAL DISPATCH - see hq_client.py's set_app_properties() docstring for the
full citation: an early version of that method posted bare
{"properties": {...}} with no "custom_properties" key, and HQ's
edit_commcare_profile() endpoint treats an OMITTED "custom_properties" key
as "replace it with {}", not "leave it alone" - silently wiping every real
custom property BASIC_TESTS_NS_COPY had (including
cc-auto-form-save-on-pause=yes, this session's OTHER row's own
precondition) the first two times this script ran (its initial tileset
write AND its own revert, both before the fix). Caught live via
run_graceful_session_pause_precondition_check.py unexpectedly failing
right after this script ran, root-caused via a fresh get_custom_properties()
read showing `{}`, fixed by making set_app_properties() always echo back
the app's current custom_properties in the same POST, and the app's real
custom_properties + cc-maps-default-layer were both manually restored and
re-verified live afterward. set_app_properties() is now safe to call
without this side effect - the fix lives in hq_client.py itself, not here,
so every caller benefits, not just this script.

Usage:
    python scripts/run_geoservice1_suite.py --tileset terrain
    python scripts/run_geoservice1_suite.py --tileset hybrid --no-revert --devices "Samsung Galaxy S26-16.0"
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
import appium_geoservice1_scenarios as geo1

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALID_TILESETS = ("normal", "satellite", "terrain", "hybrid")


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


def _revert_tileset(hq, app_id, original_value, attempts=3):
    """Same try/verify/retry shape as run_casesearch_checkbox_suite.py's own
    revert - a failed revert leaves the SHARED app mutated for everyone
    else, which is worse than a slow retry loop."""
    last_exc = None
    for attempt in range(attempts):
        try:
            hq.set_app_properties(app_id, {"cc-maps-default-layer": original_value})
            hq.create_new_build(
                app_id,
                comment=f"QA automation: revert Default Map Tileset to {original_value!r} "
                        f"(scripts/run_geoservice1_suite.py pipeline-verification teardown)",
            )
            reverted = hq.get_app_properties(app_id).get("cc-maps-default-layer")
            if reverted == original_value:
                print(f"  Reverted cc-maps-default-layer to {original_value!r} successfully.")
                return True
            print(f"  Attempt {attempt + 1}: revert mismatch - expected {original_value!r}, got {reverted!r}")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"  Attempt {attempt + 1}: revert failed: {exc}")
        time.sleep(2)
    print(f"  *** WARNING: revert did NOT succeed after {attempts} attempts (last error: {last_exc}) - "
          f"BASIC_TESTS_NS_COPY's cc-maps-default-layer is left mutated. Fix manually on HQ.")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", help="Path to an already-downloaded APK (must be the current release build).")
    parser.add_argument("--release-tag", default="", help="GitHub release tag to download if --apk isn't given.")
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0")
    parser.add_argument("--project", default="QA COMMCARE MOBILE TESTS")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-appium-geoservice1")
    parser.add_argument("--tileset", default="terrain", choices=VALID_TILESETS,
                         help="cc-maps-default-layer value to configure before installing (default: terrain, "
                              "since Satellite is already BASIC_TESTS_NS_COPY's documented current value).")
    parser.add_argument("--no-revert", action="store_true",
                         help="Leave --tileset as the app's real, permanent current value instead of "
                              "restoring the original value afterward.")
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

    domain, app_id = APP_REGISTRY["BASIC_TESTS_NS_COPY"]
    hq = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    original_tileset = hq.get_app_properties(app_id).get("cc-maps-default-layer")
    print(f"Current (pre-test) cc-maps-default-layer: {original_tileset!r}")

    print(f"Running Geoservice 1 map-form-loads check (tileset={args.tileset}) ...")
    start = time.monotonic()
    driver = None
    result = None
    try:
        # UPDATE (2026-09-17), per code review: the mutation (set_app_properties
        # + create_new_build + the confirmation read-back) and the subsequent
        # app-code/APK-upload resolution used to run BEFORE this try block -
        # any exception there (a transient HQ read-lag on the confirmation
        # check, a network blip during create_new_build/get_app_install_code/
        # bs.upload_app) would propagate straight out of main() with the
        # revert's own finally never reached, leaving the SHARED
        # BASIC_TESTS_NS_COPY app mutated with no revert. Moved inside this
        # try so the revert in `finally` below covers the WHOLE mutation
        # window, not just the on-device portion - same shape as
        # run_casesearch_checkbox_suite.py's own mutation-inside-try/finally.
        print(f"Setting cc-maps-default-layer to {args.tileset!r} and creating a new build ...")
        hq.set_app_properties(app_id, {"cc-maps-default-layer": args.tileset})
        hq.create_new_build(
            app_id,
            comment=f"QA automation: Default Map Tileset = {args.tileset!r} "
                    f"(scripts/run_geoservice1_suite.py pipeline verification)",
        )
        confirmed = hq.get_app_properties(app_id).get("cc-maps-default-layer")
        if confirmed != args.tileset:
            raise RuntimeError(
                f"set_app_properties did not take effect as HQ now reports it: expected {args.tileset!r}, "
                f"got {confirmed!r} - not proceeding to install/spend BrowserStack time against a build that "
                f"doesn't have the tileset value this run intended to test."
            )
        app_code = hq.get_app_install_code(app_id)

        cc_username = os.environ["CC_TEST_USERNAME"]
        cc_password = os.environ["CC_TEST_PASSWORD"]

        bs = AppiumBrowserStackClient()
        print(f"Uploading APK ({apk_path}) to BrowserStack ...")
        app_url = bs.upload_app(apk_path)["app_url"]

        driver = bs.start_session(app_url, device, os_version, build_name=args.build_name,
                                   session_name=f"geoservice1_{args.tileset}")
        geo1.verify_map_form_loads(driver, app_code, cc_username, cc_password)
        result = report_generator.TestResult(
            name="form_submissions/geoservice_1_map_form_loads",
            workflow="form_submissions",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, "geoservice_1_map_form_loads")
        result = report_generator.TestResult(
            name="form_submissions/geoservice_1_map_form_loads",
            workflow="form_submissions",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"geoservice_1_map_form_loads (Appium) - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001
        _save_failure_evidence(driver, "geoservice_1_map_form_loads")
        result = report_generator.TestResult(
            name="form_submissions/geoservice_1_map_form_loads",
            workflow="form_submissions",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"geoservice_1_map_form_loads (Appium) - session/infra error: {exc}",
        )
    finally:
        if driver is not None:
            if result is not None:
                try:
                    _set_browserstack_session_status(driver, result)
                except Exception:  # noqa: BLE001
                    pass
            try:
                driver.quit()
            except Exception as quit_exc:  # noqa: BLE001
                print(f"  (driver.quit() itself failed, likely a transient network blip: {quit_exc})")

        if not args.no_revert and original_tileset is not None:
            print(f"Reverting cc-maps-default-layer back to {original_tileset!r} ...")
            _revert_tileset(hq, app_id, original_tileset)
        elif args.no_revert:
            print(f"--no-revert passed: leaving cc-maps-default-layer permanently set to {args.tileset!r}.")

    print(f"  geoservice_1_map_form_loads: {result.status}"
          + (f" - {result.failed_step}" if result.status == "failed" else ""))

    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    apk_version_path = REPO_ROOT / "reports" / "apk_version.txt"
    if not apk_version_path.exists():
        apk_version_path.write_text(
            apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    this_run_results = [result]

    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    results = list(this_run_results)
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"geoservice1-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in this_run_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
