"""
Orchestrator for prompted_updates' scenario_1 (optional CCZ update) and
scenario_2 (forced CCZ update) - the 2 flows whose HQ pre-step
(mark_build_status) genuinely must fire BETWEEN an on-device logout and the
next login, on the SAME device/app state. A Maestro build runs its whole
"execute" list server-side with no external control point mid-build (see
hq_setup/prompted_updates/scenario_02_forced.json's own header, first
flagged as needing "extend scripts/run_suite.py to pause here" - and
scripts/run_suite.py's own --hq-setup only ever runs ONCE, before any flow
starts). An Appium session is driven by a persistent Python process instead,
so this script freely calls HQClient methods directly between UI actions on
one live session - see scripts/appium_scenarios.py's
run_prompted_update_scenario_1/2 for the actual step sequences.

Unlike scripts/run_appium_suite.py, this does NOT need a mid-session CommCare
BINARY swap - both scenarios test an app-level (CCZ) update on a single,
fixed CommCare client version, so this uploads one APK and starts one plain
Appium session (no midSessionInstallApps).

HQ setup performed automatically before the Appium session starts (matching
each scenario's own hq_setup/*.json "Setup" rows - see those files'
_comment for the full citation):
    Setup 2 (both scenarios): mark the CURRENT top build In Test, and the
        next-newest Released - so the device's own fresh install lands on
        that "prior" build, matching what should already be installed.
    Setup 3 (scenario_1 only): Prompt Updates to CommCare/App = On.
    scenario_2's own forced set_prompt_update_settings: run here too (it
        must land before the scenario's first login, i.e. before the
        session even starts) rather than inside the scenario itself.
The one action that genuinely can't move here - marking the latest build
Released - stays inside each scenario function, fired between its own
logout and re-login.

Usage:
    python scripts/run_appium_prompted_updates_suite.py --scenario scenario_1
    python scripts/run_appium_prompted_updates_suite.py --scenario scenario_2 --devices "Samsung Galaxy S26-16.0"
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

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HQ_SETUP_DIR = REPO_ROOT / "hq_setup" / "prompted_updates"

SCENARIOS = ("scenario_1", "scenario_2")


def _split_device(devices_arg):
    """Same "Device Name-OSVersion" convention as run_appium_suite.py's own
    helper - see that file's docstring for why Appium needs it split while
    Maestro's trigger_build wants the combined string as-is."""
    device_name, _, os_version = devices_arg.rpartition("-")
    if not device_name:
        raise SystemExit(f"--devices {devices_arg!r} must be in 'Device Name-OSVersion' form.")
    return device_name, os_version


def _save_failure_evidence(driver, name):
    """Same best-effort screenshot+page_source dump as run_appium_suite.py's
    own helper - kept as a separate copy rather than a shared import since
    it's 15 lines and the two scripts otherwise have no shared state."""
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


def _basic_tests_app_id():
    """cdfa6c85eb594b23b0c08729cd2beff1 - see scripts/app_registry.py's own
    BASIC_TESTS entry, the single source of truth this repo already uses
    elsewhere for this app id."""
    return APP_REGISTRY["BASIC_TESTS"][1]


def _resolve_top_two_builds(hq_client, app_id):
    """Returns (latest_saved_app_id, prior_saved_app_id) - the current top
    two builds by recency, per HQClient.list_releases (already sorted
    newest-first, confirmed live 2026-08-20 against this exact app). Always
    resolved fresh rather than hardcoded, same reasoning as
    hq_client.resolve_app_codes' own docstring: build ids get recreated
    between QA cycles."""
    releases = hq_client.list_releases(app_id, only_show_released=False, limit=5)
    if len(releases) < 2:
        raise RuntimeError(f"app {app_id} has fewer than 2 releases - can't resolve latest/prior")
    return releases[0]["_id"], releases[1]["_id"]


def _set_browserstack_session_status(driver, result):
    """Same fix as scripts/run_appium_suite.py's own copy (kept as a
    separate copy rather than a shared import, same reasoning as this
    file's own _save_failure_evidence citation) - per direct user-supplied
    BrowserStack guidance: a raw Appium session has no built-in pass/fail
    signal the way a Maestro build does, so BrowserStack's own dashboard
    can otherwise show something that doesn't match this script's own
    TestResult. Best-effort: never let a failure here mask the real result
    already determined above."""
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


def _run_one_scenario(bs, name, app_url, device, os_version, build_name, fn):
    driver = None
    result = None
    start = time.monotonic()
    try:
        driver = bs.start_session(app_url, device, os_version, build_name=build_name, session_name=name)
        fn(driver)
        result = report_generator.TestResult(
            name=f"prompted_updates/{name}_appium",
            workflow="prompted_updates",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, name)
        result = report_generator.TestResult(
            name=f"prompted_updates/{name}_appium",
            workflow="prompted_updates",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"{name}_appium.py - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001 - session-level infra failure (upload/session-start/HQ call/etc.)
        _save_failure_evidence(driver, name)
        result = report_generator.TestResult(
            name=f"prompted_updates/{name}_appium",
            workflow="prompted_updates",
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
    parser.add_argument("--apk", help="Path to an already-downloaded APK.")
    parser.add_argument("--release-tag", default="", help="GitHub release tag to download if --apk isn't given.")
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0",
                         help="'Device Name-OSVersion', same convention as run_suite.py's own --devices.")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-prompted-updates-appium")
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=SCENARIOS,
                         help="Run only this scenario (repeatable). Defaults to both.")
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

    app_id = _basic_tests_app_id()
    hq_client = hq_client_module.HQClient(domain="qateam").login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )

    print(f"Resolving top two builds for app {app_id} ...")
    latest_build_id, prior_build_id = _resolve_top_two_builds(hq_client, app_id)
    print(f"  latest={latest_build_id} prior={prior_build_id}")

    # Setup 2 (both scenarios) - see hq_setup/prompted_updates/setup_02_mark_in_test.json.
    print("HQ Setup 2: mark latest build In Test, prior build Released ...")
    hq_client.mark_build_status(app_id, latest_build_id, is_released=False)
    hq_client.mark_build_status(app_id, prior_build_id, is_released=True)

    if "scenario_1" in scenarios_to_run:
        # Setup 3 - see hq_setup/prompted_updates/setup_03_prompts_on.json.
        print("HQ Setup 3: Prompt Updates to CommCare/App = On ...")
        hq_client.set_prompt_update_settings(app_id, app_prompt="on", apk_prompt="on")
    if "scenario_2" in scenarios_to_run:
        # scenario_02_forced.json's own first action - must land before
        # scenario_2's first login, i.e. before the session even starts.
        print("HQ: set forced prompt settings (scenario_2) ...")
        hq_client.set_prompt_update_settings(app_id, app_prompt="forced", apk_prompt="on")

    # UPDATE (2026-08-20), confirmed live via a real dispatch: calling
    # resolve_app_codes with the plain (domain, app_id) 2-tuple resolves
    # to the app's TOP build regardless of Setup 2 above (per
    # HQClient.get_app_install_code's own docstring: with no saved_app_id
    # override it picks "the app's single most recent build (whether or
    # not it's released)"), and that call's own release_first=True default
    # then RE-RELEASES that top build as a side effect of generating the
    # code - silently undoing Setup 2's "mark latest In Test" a moment
    # after we set it. The device ended up installing version 1141 (the
    # latest) instead of the intended prior/released build. Pins to
    # prior_build_id explicitly instead (a 3-tuple, same mechanism
    # scripts/app_registry.py's RU_TEST_ONE/TWO/THREE entries already use
    # for "a specific, already-cut build a flow must address by name, not
    # whatever's newest today" - see resolve_app_codes' own docstring).
    # release_first=True still fires on the pinned build, but that's a
    # harmless no-op here since Setup 2 already released it.
    print("Resolving install code for BASIC_TESTS (pinned to the prior build) ...")
    domain = APP_REGISTRY["BASIC_TESTS"][0]
    app_codes = hq_client_module.resolve_app_codes(
        {"BASIC_TESTS": (domain, app_id, prior_build_id)},
    )
    app_code = app_codes["APP_CODE_BASIC_TESTS"]

    bs = AppiumBrowserStackClient()
    print(f"Uploading app ({apk_path}) to BrowserStack ...")
    app_url = bs.upload_app(apk_path)["app_url"]

    cc_username = os.environ["CC_TEST_USERNAME"]
    cc_password = os.environ["CC_TEST_PASSWORD"]

    scenario_fns = {
        "scenario_1": lambda driver: appium_scenarios.run_prompted_update_scenario_1(
            driver, hq_client, app_id, latest_build_id, cc_username, cc_password, app_code),
        "scenario_2": lambda driver: appium_scenarios.run_prompted_update_scenario_2(
            driver, hq_client, app_id, latest_build_id, cc_username, cc_password, app_code),
    }

    results = []
    try:
        for name in scenarios_to_run:
            print(f"Running {name} (Appium, mid-session HQ action) ...")
            result = _run_one_scenario(bs, name, app_url, device, os_version, args.build_name, scenario_fns[name])
            print(f"  {name}: {result.status}" + (f" - {result.failed_step}" if result.status == "failed" else ""))
            results.append(result)
    finally:
        # UPDATE (2026-08-20), per direct user instruction: Setup 2/3 above
        # change PERSISTENT, SHARED HQ state on "[Master] Basic Tests" - an
        # app used by dozens of unrelated flows across this whole repo, not
        # something scoped to just this test run. Leaving Prompt Updates
        # on (or the latest build stuck "In Test", if a scenario failed
        # before its own mid-flow release step got to run) would affect
        # every one of those other flows too. Always restore both,
        # regardless of whether the scenarios above passed, failed, or
        # raised - this is cleanup, not part of the test result itself, so
        # failures here are logged, not allowed to mask/replace whatever
        # the actual scenario results were.
        # UPDATE (2026-08-22), per direct user instruction: this cleanup
        # must actually land on HQ even during a CI run where a transient
        # network blip is far more likely than it sounds - confirmed live
        # today (RemoteDisconnected on this exact call, twice) that a single
        # attempt with no retry can leave the shared app stuck mid-toggle
        # (wrong build released, or Prompt Updates left on) for every other
        # flow that touches this app afterward. Retries each cleanup call up
        # to 3 times with a short pause before giving up and logging - still
        # best-effort (this is cleanup, not the test result itself), but no
        # longer gives up after one bad connection.
        for description, cleanup_call in [
            ("mark latest build Released",
             lambda: hq_client.mark_build_status(app_id, latest_build_id, is_released=True)),
            ("set Prompt Updates to Off",
             lambda: hq_client.set_prompt_update_settings(app_id, app_prompt="off", apk_prompt="off")),
        ]:
            print(f"Cleanup: {description} ...")
            for attempt in range(3):
                try:
                    cleanup_call()
                    break
                except Exception as cleanup_exc:  # noqa: BLE001 - best-effort, never mask the real scenario results
                    if attempt < 2:
                        time.sleep(5)
                        continue
                    print(f"  (cleanup step {description!r} failed after 3 attempts, "
                          f"may need manual fixup on HQ: {cleanup_exc})")

    # UPDATE (2026-08-20), same fix as scripts/run_suite.py's own UPDATE
    # comment: a custom --apk has no release tag, so apk_commcare_version
    # stays None and this used to skip writing reports/apk_version.txt
    # entirely - the Slack notification then had no APK version/source
    # line at all, silently. Falls back to the APK's own filename.
    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    (REPO_ROOT / "reports" / "apk_version.txt").write_text(
        apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
    )

    # Same merge-not-overwrite pattern as run_appium_suite.py's own
    # main() - see that file's own UPDATE comment for the full citation,
    # including the 2026-08-21 fix (replace-by-name instead of blind
    # concatenation) that stops a stale local rerun's FAILED entry from
    # lingering next to the current run's own PASSED result.
    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    if existing_results_path.exists():
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"appium-prompted-updates-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
