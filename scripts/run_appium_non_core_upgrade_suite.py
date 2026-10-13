"""
Non-core rows that need a CommCare APK UPGRADE in the middle of the test:
install an OLD CommCare (resources/commcare_2.45_release.apk), create state
on it, upgrade to the release under test, then check that state survived.
Maestro can't install an APK mid-flow; BrowserStack Appium sessions can
(midSessionInstallApps + driver.install_app - same certificate on both
APKs, verified for this exact pair in scripts/run_appium_suite.py). Steps
reuse scripts/appium_scenarios.py's proven ports of the core subflows.

Scenarios (Master Mobile Plan (2026)):
  database_change_upgrade - Database Change > Setup 1 (direct APK install,
      not the Play Store), Database 2 (incomplete form survives), Final 1
      (update CommCare), Final 2 (verify outstanding data), Database 7/9
      (jump from an old CommCare: 2.45 -> release) and Database Change 10
      ("CommCare updated" in Data Change Logs). App: "Database Change!"
      (its builds target CommCare 2.21, so CommCare 2.45 can install them).
  performance_upgrade - Performance Tests > Perf 1 (old CommCare + Large
      App) and Perf 2 (update CommCare), then times the cold start and the
      login on the upgraded client (the sheet's two timing rows, after an
      upgrade rather than on a fresh install).

A dev/PR APK (versionCode 1) is repackaged with the old one so the upgrade can
install, exactly as scripts/run_appium_suite.py does (apk_info.repackage_for_upgrade).

Results merge into reports/latest_results.json the same way
scripts/run_appium_suite.py does.

Usage:
    python scripts/run_appium_non_core_upgrade_suite.py --devices "Samsung Galaxy S26-16.0"
    python scripts/run_appium_non_core_upgrade_suite.py --scenario database_change_upgrade
"""
import argparse
import json
import os
import pathlib
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import apk_info
import appium_helpers as h
import appium_scenarios as s
import download_apk
import hq_client as hq_client_module
import report_generator
from app_registry import APP_REGISTRY
from appium_browserstack_client import AppiumBrowserStackClient
from run_appium_suite import (_save_failure_evidence, _set_browserstack_session_status, _split_device,
                              swap_not_possible_reason)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OLD_APK_PATH = REPO_ROOT / "resources" / "commcare_2.45_release.apk"
APP_ID = s.APP_ID
FLOW_STEM = {
    "database_change_upgrade": ("database_change", "database_change_upgrade_from_2_45_appium"),
    "performance_upgrade": ("performance_tests", "perf_01_02_upgrade_from_2_45_appium"),
}


def _save_incomplete_form(driver, module, form):
    """Database 2: open a form and leave it via SAVE INCOMPLETE
    (keep_changes, app/res/values/strings.xml) - an incomplete form needs no
    offline step, unlike the sheet's saved/case forms."""
    s._wait_out_progress_dialogs(driver)
    h.tap_by_text(driver, "Start")
    h.tap_by_text(driver, module)
    h.tap_by_text(driver, form)
    h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=20, optional=True)
    h.back(driver)
    h.tap_by_text(driver, "SAVE INCOMPLETE", timeout=10)
    h.wait_visible_text(driver, "Start", timeout=20)


def _incomplete_form_listed(driver, form):
    """Final 2: the incomplete form from before the upgrade is still there."""
    s._wait_out_progress_dialogs(driver)
    h.tap_by_text(driver, "Incomplete")
    h.wait_visible_text(driver, form, timeout=20)
    h.back(driver)
    h.wait_visible_text(driver, "Start", timeout=10)


def _data_change_log_shows(driver, *messages):
    """Database Change 10: Login screen > Go To App Manager > Advanced > Data
    Change Logs (login.menu.app.manager / menu.data.change.logs); messages
    from app/src/org/commcare/logging/DataChangeLog.kt."""
    s._logout(driver)
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=30)
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "Go To App Manager")
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "Advanced")
    h.tap_by_text(driver, "Data Change Logs", timeout=15)
    for message in messages:
        h.wait_visible_text(driver, f"(?s).*{message}.*", regex=True, timeout=20)


def run_database_change_upgrade(driver, bs, new_app_url, username, password, app_code):
    steps = [
        ("Setup 1-3: install Database Change app on CommCare 2.45, log in",
         lambda: (s._install_app_by_code(driver, app_code), s._login(driver, username, password))),
        ("Database 2: save an incomplete form on the old client",
         lambda: _save_incomplete_form(driver, "Basic Test", "First Form")),
        ("Final 1 / Database 7, 9: upgrade CommCare 2.45 -> release mid-session",
         lambda: bs.install_mid_session(driver, new_app_url)),
        ("Relaunch on the upgraded client", lambda: s._relaunch_app(driver)),
        ("Final 2: log in on the upgraded client", lambda: s._login(driver, username, password)),
        ("Final 2: incomplete form survived the upgrade", lambda: _incomplete_form_listed(driver, "First Form")),
        ("Database Change 10: Data Change Logs show the CommCare upgrade",
         lambda: _data_change_log_shows(driver, "CommCare updated")),
    ]
    return s._run_steps(steps)


def run_performance_upgrade(driver, bs, new_app_url, username, password, app_code, timings):
    def _timed(label, fn):
        start = time.monotonic()
        fn()
        timings[label] = round(time.monotonic() - start, 1)

    steps = [
        ("Perf 1: install Large App on CommCare 2.45, log in",
         lambda: (s._install_app_by_code(driver, app_code), s._login(driver, username, password),
                  s._wait_out_progress_dialogs(driver, timeout=300))),
        ("Perf 2: upgrade CommCare 2.45 -> release mid-session",
         lambda: bs.install_mid_session(driver, new_app_url)),
        ("Timing row 1: cold start to login screen (upgraded client)",
         lambda: _timed("cold start to login screen (s)", lambda: (
             s._relaunch_app(driver),
             h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=180)))),
        ("Timing row 2: login to home screen (upgraded client)",
         lambda: _timed("login to home screen (s)", lambda: (
             s._login(driver, username, password),
             s._wait_out_progress_dialogs(driver, timeout=300),
             h.wait_visible_text(driver, "Start", timeout=60)))),
    ]
    return s._run_steps(steps)


def _run(bs, name, old_app_url, new_app_url, device, os_version, build_name, fn, skip_reason=None):
    workflow, stem = FLOW_STEM[name]
    driver, result, start = None, None, time.monotonic()
    base = dict(name=f"{workflow}/{stem}", workflow=workflow, device=f"{device}-{os_version}")
    if skip_reason:
        return report_generator.TestResult(status="skipped", error=skip_reason, failed_step=skip_reason, **base)
    try:
        driver = bs.start_session(old_app_url, device, os_version, build_name=build_name,
                                  session_name=stem, mid_session_apps=[new_app_url])
        note = fn(driver)
        note = note if isinstance(note, str) else ""
        result = report_generator.TestResult(status="passed", error=note,
                                             duration_ms=int((time.monotonic() - start) * 1000), **base)
    except s.ScenarioFailure as exc:
        _save_failure_evidence(driver, stem)
        result = report_generator.TestResult(status="failed", error=str(exc.original),
                                             failed_step=f"{stem} - {exc.step_name}: {exc.original}",
                                             duration_ms=int((time.monotonic() - start) * 1000), **base)
    except Exception as exc:  # noqa: BLE001 - session/infra failure
        _save_failure_evidence(driver, stem)
        result = report_generator.TestResult(status="failed", error=str(exc),
                                             failed_step=f"{stem} - session/infra error: {exc}",
                                             duration_ms=int((time.monotonic() - start) * 1000), **base)
    finally:
        if driver is not None:
            if result is not None:
                _set_browserstack_session_status(driver, result)
            driver.quit()
    return result


def _pinned_code(key):
    domain, app_id, build_id = APP_REGISTRY[key][:3]
    client = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"))
    return client.get_app_install_code(app_id, saved_app_id=build_id, release_first=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", help="Path to an already-downloaded NEW-version APK.")
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-NONCORE-upgrade-appium")
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=sorted(FLOW_STEM))
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    device, os_version = _split_device(args.devices)

    apk_path = args.apk
    if not apk_path:
        release, asset = download_apk.resolve(args.release_tag or None)
        apk_path = f"apks/{asset['name']}"
        download_apk.download(asset["browser_download_url"], apk_path, expected_size=asset["size"])

    # Same as scripts/run_appium_suite.py: a dev/PR build (versionCode 1, e.g. the 2.65
    # qaAutomation APK) can't install over the 2.45 binary, so repackage the pair (both re-signed
    # with one throwaway key, new versionCode = old + 1 - see apk_info.repackage_for_upgrade);
    # if that isn't possible, report the scenarios as skipped with the reason.
    old_apk_to_use, new_apk_to_use = str(OLD_APK_PATH), apk_path
    skip_reason = swap_not_possible_reason(OLD_APK_PATH, apk_path)
    if skip_reason:
        try:
            old_apk_to_use, new_apk_to_use, how = apk_info.repackage_for_upgrade(
                OLD_APK_PATH, apk_path, REPO_ROOT / "reports" / "repackaged_apks_non_core")
            print(f"Repackaged the APK pair for the in-place upgrade: {how}")
            skip_reason = None
        except Exception as exc:  # noqa: BLE001 - tools missing / signing failed: skip, don't fail
            skip_reason += f" (Repackaging the pair for the test was not possible: {exc})"

    bs = AppiumBrowserStackClient()
    old_app_url = bs.upload_app(str(old_apk_to_use))["app_url"]
    new_app_url = bs.upload_app(str(new_apk_to_use))["app_url"]
    password = os.environ["CC_TEST_PASSWORD"]

    perf_timings = {}
    fns = {
        "database_change_upgrade": lambda d: run_database_change_upgrade(
            d, bs, new_app_url, os.environ["CC_TEST_USERNAME"], password, _pinned_code("DB_CHANGE_OLD")),
        "performance_upgrade": lambda d: (run_performance_upgrade(
            d, bs, new_app_url, os.environ["CC_LARGE_APP_USERNAME"], password,
            _pinned_code("LARGE_APP_OLD"), perf_timings), f"timings: {json.dumps(perf_timings)}")[1],
    }
    results = []
    for name in args.scenarios or sorted(FLOW_STEM):
        print(f"Running {name} (Appium, CommCare 2.45 -> release) ...")
        r = _run(bs, name, old_app_url, new_app_url, device, os_version, args.build_name, fns[name],
                 skip_reason=skip_reason)
        print(f"  {name}: {r.status}" + (f" - {r.failed_step}" if r.status == "failed" else f" {r.error}"))
        results.append(r)

    this_run = list(results)
    existing_path = REPO_ROOT / "reports" / "latest_results.json"
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        names = {r.name for r in results}
        results = [report_generator.TestResult(**i) for i in existing if i["name"] not in names] + results
    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    report_generator.generate_report(f"appium-upgrade-{int(time.time())}", results, enrich=False)
    sys.exit(1 if any(r.status == "failed" for r in this_run) else 0)


if __name__ == "__main__":
    main()
