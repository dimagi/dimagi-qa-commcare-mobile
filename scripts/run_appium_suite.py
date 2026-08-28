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
OLD_APK_PATH = REPO_ROOT / "resources" / "commcare_2.45_release.apk"

SCENARIOS = ("scenario_1", "scenario_2", "scenario_5")

# UPDATE (2026-08-26), per direct user feedback on run_appium_offline_ccz_
# suite.py's identical naming issue: BrowserStack session names/TestResult
# names used to be the short internal key + "_appium" ("scenario_2_appium")
# - not descriptive. Unlike the offline-CCZ scenarios, these 3 don't replace
# an existing Maestro flow (flows/updates_partial_failed/scenario_1_staged_
# update_auto_apply.yaml etc. still run separately, covering the
# post-precondition steps only) - so this can't just reuse that flow's own
# name outright without colliding in the merged report. Named after that
# flow's own real stem instead of the short key, with "_appium" kept as a
# suffix to distinguish this ADDITIONAL (not superseding) coverage.
FLOW_STEM = {
    "scenario_1": "scenario_1_staged_update_auto_apply_appium",
    "scenario_2": "scenario_2_manual_update_after_interrupted_download_appium",
    "scenario_5": "scenario_5_relogin_autoupdate_verification_appium",
}


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


def _set_browserstack_session_status(driver, result):
    """UPDATE (2026-08-21), per direct user-supplied BrowserStack guidance:
    unlike a Maestro build (where BrowserStack itself tracks pass/fail from
    the flow's own assertions), a raw Appium session has no built-in signal
    of whether OUR test logic considered the run a pass or fail - without
    this, BrowserStack's own dashboard/session-status field can show
    something that doesn't match this script's own TestResult, even though
    that mismatch never affected THIS repo's own report/exit-code logic
    (that's tracked independently in Python). Best-effort: never let a
    failure here mask the real result already determined above."""
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


def _run_one_scenario(bs, name, old_app_url, new_app_url, device, os_version, build_name, env, fn,
                       network_profile=None):
    driver = None
    result = None
    start = time.monotonic()
    stem = FLOW_STEM[name]
    try:
        driver = bs.start_session(
            old_app_url, device, os_version,
            build_name=build_name, session_name=stem,
            mid_session_apps=[new_app_url],
            network_profile=network_profile,
        )
        fn(driver)
        result = report_generator.TestResult(
            name=f"updates_partial_failed/{stem}",
            workflow="updates_partial_failed",
            status="passed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
        )
    except appium_scenarios.ScenarioFailure as exc:
        _save_failure_evidence(driver, stem)
        result = report_generator.TestResult(
            name=f"updates_partial_failed/{stem}",
            workflow="updates_partial_failed",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc.original),
            failed_step=f"{stem} - {exc.step_name}: {exc.original}",
        )
    except Exception as exc:  # noqa: BLE001 - session-level infra failure (upload/session-start/etc.)
        _save_failure_evidence(driver, stem)
        result = report_generator.TestResult(
            name=f"updates_partial_failed/{stem}",
            workflow="updates_partial_failed",
            status="failed",
            duration_ms=int((time.monotonic() - start) * 1000),
            device=f"{device}-{os_version}",
            error=str(exc),
            failed_step=f"{stem} - session/infra error: {exc}",
        )
    finally:
        if driver is not None:
            if result is not None:
                _set_browserstack_session_status(driver, result)
            driver.quit()
    return result


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
        download_apk.download(asset["browser_download_url"], apk_path, expected_size=asset["size"])
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
    #
    # UPDATE (2026-08-21), per direct user correction with a real-device
    # recording: max_commcare_version filtering alone picks the NEWEST
    # build under that ceiling, which (confirmed live via
    # HQClient.list_releases) is v71 - already named "Mobile Updates - Test
    # 1_2!". The real scenario installs "Mobile Updates - Test 1" FIRST
    # (a build literally named/commented "Version 1"), and only ends up on
    # "Test 1_2!" AFTER the update - so this needs to pin to that specific
    # OLDER "Version 1" build, not just any build under the version
    # ceiling.
    #
    # v26 (comment "Version 1", CC 2.45.0 - matching the old APK under test
    # here) looked like the natural pick, but marking it Released failed
    # live with a real HQ platform error: "The mobile UCR restore version
    # for v26 needs to be updated to V2.0" - an app-level HQ migration
    # blocker (see the linked migration guide in that error), not something
    # this script can push through, and not specific to this one build (its
    # own record already reports mobile_ucr_restore_version=2.0). Per
    # direct user decision, uses v66 instead ("2.55 - Version 1", CC
    # 2.41.1, confirmed via list_releases to ALREADY be is_released=True)
    # and calls get_app_install_code directly with release_first=False
    # (bypassing resolve_app_codes' pinned-entry path, which always calls
    # mark_build_status regardless of current status - see its own
    # already_released=None comment for pinned entries) so this never
    # attempts the same blocked release call at all.
    print("Resolving install code for MOBILE_UPDATES_1_2 (pinned to the already-released 'Test 1' Version 1 build) ...")
    domain = APP_REGISTRY["MOBILE_UPDATES_1_2"][0]
    mobile_updates_app_id = APP_REGISTRY["MOBILE_UPDATES_1_2"][1]
    v1_build_id = "969f2df0118b4619ac386f123c58edd3"  # v66, "2.55 - Version 1", "Mobile Updates - Test 1", CC 2.41.1
    hq_client = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    app_code = hq_client.get_app_install_code(mobile_updates_app_id, saved_app_id=v1_build_id, release_first=False)

    # UPDATE (2026-08-25), confirmed live in CI (real failure: "Incompatible
    # CommCare Version for Install" right after selecting an app from the
    # mobile-worker's app list): run_scenario_5 originally installed via
    # "See Apps for My User" against the shared HQ_MOBILE_WORKER_USERNAME/
    # HQ_DOMAIN (qateam) credentials - the EXACT SAME bug already found and
    # fixed for this same scenario's Maestro counterpart
    # (flows/updates_partial_failed/scenario_5_relogin_autoupdate_
    # verification.yaml's own 2026-08-21 UPDATE): that mobile worker's app
    # lives under domain "let-sdoit", not qateam, AND "See Apps for My
    # User" always installs the CURRENT top release ("Version V6"), not
    # "Version V2" - skipping the update-verification steps this scenario
    # needs entirely. This Appium port never inherited that fix. Same
    # remedy: install by app code, pinned to "Version V2" (CC 2.45.2,
    # compatible with the OLD 2.45 binary this scenario installs on) - see
    # app_registry.py's LINKED_APP_TEST45 entry for the full citation.
    print("Resolving install code for LINKED_APP_TEST45 (pinned to 'Version V2', let-sdoit domain) ...")
    linked_app_domain, linked_app_id, linked_app_v2_build_id, _ = APP_REGISTRY["LINKED_APP_TEST45"]
    linked_app_hq_client = hq_client_module.HQClient(domain=linked_app_domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    linked_app_code = linked_app_hq_client.get_app_install_code(
        linked_app_id, saved_app_id=linked_app_v2_build_id, release_first=False)

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
            driver, bs, new_app_url, cc_username, cc_password, linked_app_code),
    }

    # UPDATE (2026-08-25), per the Master Mobile Plan's own literal Test
    # Case 2 setup steps ("DO NOT LET THE UPDATE CHECK COMPLETE - while
    # CommCare is downloading the app updates, update CommCare"): only
    # scenario_2 needs its download genuinely still in flight when
    # interrupted - confirmed live this test app's CCZ downloads fast
    # enough on a normal connection that no code-level interrupt timing
    # can reliably catch it mid-flight. Throttled to a slow preset for
    # JUST this scenario's own dedicated session (each scenario already
    # gets its own session - see _run_one_scenario), not the other two,
    # which don't need or want the extra wall-clock cost.
    #
    # UPDATE (2026-08-25, correction), confirmed live: "2g-gprs-good"
    # left the download showing the EXACT SAME "resource 1 done of 3"
    # state after both a 30s and a 180s wait - not slow-but-progressing,
    # genuinely stalled (most likely below some minimum throughput
    # CommCare's own download client tolerates before it silently retries
    # from scratch rather than visibly advancing).
    #
    # UPDATE (2026-08-25, 2nd correction), confirmed live: "3g-average-mobile"
    # isn't a real BrowserStack preset at all - a real
    # BROWSERSTACK_INVALID_NETWORK_PROFILE error named the actual full
    # list (fetched from BrowserStack's own docs, not guessed): no-network,
    # airplane-mode, 2g-gprs-good, 2g-gprs-lossy, edge-good, edge-lossy,
    # 3g-umts-good, 3g-umts-lossy, 3.5g-hspa-good, 3.5g-hspa-lossy,
    # 3.5g-hspa-plus-good, 3.5g-hspa-plus-lossy, 4g-lte-good,
    # 4g-lte-high-latency, 4g-lte-lossy, 4g-lte-advanced-good,
    # 4g-lte-advanced-lossy, reset. "3g-umts-good" is the real preset one
    # tier up from the 2g one that stalled - meaningfully slower than an
    # unthrottled connection without (hopefully) dropping below whatever
    # throughput floor caused the stall.
    #
    # UPDATE (2026-08-27), confirmed live via a real, direct diagnostic
    # (not guessed): "3g-umts-good" (400/100 Kbps per BrowserStack's own
    # docs) genuinely never stalls - it makes real, steady, monotonic
    # progress ("resource 7 of 18" -> "8" -> "10" -> "13" -> "14" over 764s
    # of live polling) - it's just far too slow for this app's real 18
    # resources, needing an estimated 20-30+ minutes total, which is why
    # every earlier timeout escalation (30s -> 180s -> 300s -> 480s) kept
    # failing regardless of how high it went; the fix was never "wait
    # longer" for this specific number. Also confirmed live:
    # "4g-lte-good" (18000/9000 Kbps) is too fast the OTHER way - the
    # entire download completed before the very first poll (t=0.0s),
    # recreating the exact original problem this whole throttle exists to
    # solve (no window left for a code-level mid-download interrupt to
    # land in). "3.5g-hspa-plus-good" (7000/1500 Kbps) sits between the
    # two and is confirmed to work correctly for both needs: a real, live
    # diagnostic caught it genuinely mid-progress ("resource 16 of 18") on
    # the very first check, then completed fully in ~20.6s total - a real,
    # multi-resource window for the interrupt (unlike 4g-lte-good) with a
    # practical, CI-reasonable completion time (unlike 3g-umts-good).
    network_profiles = {"scenario_2": "3.5g-hspa-plus-good"}

    results = []
    for name in scenarios_to_run:
        print(f"Running {name} (Appium, mid-session binary swap) ...")
        result = _run_one_scenario(
            bs, name, old_app_url, new_app_url, device, os_version, args.build_name,
            os.environ, scenario_fns[name],
            network_profile=network_profiles.get(name),
        )
        print(f"  {name}: {result.status}" + (f" - {result.failed_step}" if result.status == "failed" else ""))
        results.append(result)

    # RETRY-FAILED (2026-08-28), per direct user instruction: the Maestro
    # track (scripts/run_suite.py) has always had a --retry-failed pass
    # that re-triggers only the flows that genuinely failed once, so a
    # transient hiccup doesn't count as a real failure - this Appium track
    # never had an equivalent, despite this session repeatedly finding the
    # exact same class of transient, real-infrastructure flakiness here
    # (HQ mark_build_status propagation delays, BrowserStack session-level
    # hiccups) that the Maestro retry exists to absorb. Retries each
    # scenario that failed once, in place, then reclassifies a
    # failed-then-passed scenario as "rerun" (flaky, not a hard failure)
    # via the same report_generator.merge_rerun() helper run_suite.py's
    # own --retry-failed path already uses - one shared, proven semantic
    # for "failed once but passed on retry" across both tracks' reports.
    failed_names = [name for name, r in zip(scenarios_to_run, results) if r.status == "failed"]
    if failed_names:
        print(f"Retrying {len(failed_names)} failed scenario(s): {', '.join(failed_names)} ...")
        retry_results = []
        for name in failed_names:
            print(f"Running {name} (Appium, mid-session binary swap, retry) ...")
            retry_result = _run_one_scenario(
                bs, name, old_app_url, new_app_url, device, os_version, args.build_name,
                os.environ, scenario_fns[name],
                network_profile=network_profiles.get(name),
            )
            print(f"  {name}: {retry_result.status}" +
                  (f" - {retry_result.failed_step}" if retry_result.status == "failed" else ""))
            retry_results.append(retry_result)
        results = report_generator.merge_rerun(results, retry_results)

    # UPDATE (2026-08-20), same fix as scripts/run_suite.py's own UPDATE
    # comment: a custom --apk has no release tag, so apk_commcare_version
    # stays None and this used to skip writing reports/apk_version.txt
    # entirely - the Slack notification then had no APK version/source
    # line at all, silently. Falls back to the (new) APK's own filename.
    (REPO_ROOT / "reports").mkdir(exist_ok=True)
    (REPO_ROOT / "reports" / "apk_version.txt").write_text(
        apk_commcare_version or f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
    )

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
    # UPDATE (2026-08-21), per direct user report: a blind append here made
    # a stale FAILED entry from an earlier LOCAL debugging attempt (of the
    # exact same scenario, re-run several times while iterating on a fix)
    # linger alongside the current run's own PASSED result, so the overall
    # exit code (and this file's own report) kept reading "failed" even
    # after the real bug was fixed - confirmed live, not just suspected,
    # by inspecting reports/latest_results.json directly mid-session. This
    # was never a problem for the REAL CI case this merge exists for (a
    # fresh job only ever writes once per test name), only for repeated
    # local reruns in the same working directory - so replaces any
    # existing entry that shares a name with one of THIS run's results
    # (keeping the newest outcome) instead of blindly concatenating, which
    # fixes the local case without needing a separate report/exit code
    # scheme for standalone runs.
    existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
    if existing_results_path.exists():
        import json
        existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
        new_names = {r.name for r in results}
        results = [report_generator.TestResult(**item) for item in existing
                   if item["name"] not in new_names] + results

    build_id = f"appium-{int(time.time())}"
    report_path = report_generator.generate_report(build_id, results, enrich=False)
    print(f"Report written to {report_path}")

    if any(r.status == "failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
