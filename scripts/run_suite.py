"""
Orchestrator: ensure an APK is available, optionally run an HQ pre-step, pick
which Maestro flows to run (by tag), zip them, upload app + flows to
BrowserStack App Automate, trigger a Maestro build, and wait for the result.

Usage:
    python scripts/run_suite.py --tag mobile_pins --devices "Samsung Galaxy S26-16.0"
    python scripts/run_suite.py --tag prompted_updates --hq-setup hq_setup/prompted_updates/varying_prompt_setup.json
    python scripts/run_suite.py --flow flows/install/install_04_see_apps_menu_item_visible.yaml
    python scripts/run_suite.py --tag updates_2_49 \
        --hq-setup hq_setup/updates_2_49/setup_02_commcare_version_plus_one.json \
        --hq-teardown hq_setup/updates_2_49/prompted_update_scenario_teardown.json
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import download_apk
import hq_client as hq_client_module
import report_generator
import app_registry
from app_registry import APP_REGISTRY
from browserstack_client import BrowserStackClient

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOWS_DIR = REPO_ROOT / "flows"

# Whitelist of vars flows may reference via ${VAR} - passed to BrowserStack's
# setEnvVariables so it can do the substitution server-side (this isn't a
# local `maestro test` run, so plain os.environ isn't visible to it).
FLOW_ENV_VARS = [
    "CC_TEST_USERNAME", "CC_TEST_PASSWORD",
    "CC_TEST2_USERNAME", "CC_TEST2_PASSWORD",
    "HQ_DOMAIN",
    "HQ_MOBILE_WORKER_USERNAME", "HQ_MOBILE_WORKER_PASSWORD",
    "HQ_WEB_USER_EMAIL", "HQ_WEB_USER_PASSWORD",
]


def select_flow_files(tags=None, explicit_flows=None):
    """Return the set of flow .yaml files to run, always including flows/common/
    (needed by runFlow references) regardless of tag filtering.

    Flows tagged `blocked_missing_asset` (an addMedia reference to a local
    file that doesn't exist yet - filesize_01/02, filesize_warning_02 as of
    this writing) are excluded regardless of tag filtering: BrowserStack
    rejects the WHOLE build's parse if even one addMedia target is missing
    from the zip (confirmed live), so leaving one of these in a chunk
    silently zeroes out every other flow sharing that chunk. Pass
    --tag blocked_missing_asset or --flow explicitly to run them anyway once
    their assets exist.

    UPDATE, confirmed live (2026-08-08, real CI run 31272376559): the
    exclusion above used to only apply when NO tags were given at all (the
    bare "All" case) - once real tags were passed (as this repo's own 3-way
    CI matrix always does, e.g. --tag multimedia), a blocked_missing_asset
    flow that ALSO carries that tag got swept in anyway via the `flow_tags &
    set(tags)` check below, reproducing the exact PARSE_ERROR this
    exclusion exists to prevent - confirmed live: filesize_01_trigger_warning,
    filesize_02_no_warning, and filesize_warning_02_select_large_video (all
    tagged both `multimedia` and `blocked_missing_asset`) each caused their
    own batch to fail to parse when group-a ran with --tag multimedia. Fixed
    so the exclusion applies whenever tags are given too, unless
    blocked_missing_asset itself is one of the requested tags (or the flow
    was named explicitly via --flow, which always bypasses tag filtering
    entirely - a direct request to run a specific file should never be
    silently dropped)."""
    if explicit_flows:
        selected = {pathlib.Path(f).resolve() for f in explicit_flows}
    else:
        selected = set()
        for path in FLOWS_DIR.rglob("*.yaml"):
            if path.parent.name == "common":
                continue
            with open(path, encoding="utf-8") as fh:
                doc = next(yaml.safe_load_all(fh))
            flow_tags = set((doc or {}).get("tags") or [])
            if "blocked_missing_asset" in flow_tags and (not tags or "blocked_missing_asset" not in tags):
                continue
            # UPDATE (2026-08-25): same exclusion pattern as
            # blocked_missing_asset above - cc_reinstall_needed_01/
            # cc_update_needed_01_trigger_on_old_client.yaml need a
            # completely different CommCare BINARY (resources/
            # commcare_2.45_release.apk, via --apk) than every other
            # recovery_measures flow, which runs against the normal
            # current-release build. Without this, --tag recovery_measures
            # would sweep them into the same build as everything else,
            # testing them against the wrong (current, not old) binary.
            if "requires_old_client_apk" in flow_tags and (not tags or "requires_old_client_apk" not in tags):
                continue
            # UPDATE (2026-08-25): same exclusion pattern again -
            # offline_06_select_ccz_via_picker/offline_08_move_ccz_to_
            # downloads/offline_reinstall_update_app_flow/reinstall_update_05_
            # 06_chooser_and_ccz are all structurally blocked (Maestro can't
            # push a .ccz file onto the device) and now have real, passing
            # coverage via scripts/appium_offline_ccz_scenarios.py instead
            # (see each file's own UPDATE header) - dispatching them
            # alongside their Appium replacements would just show 4
            # permanent, misleading "failures" in every recovery_measures
            # report. Kept on disk (not deleted) for reference; still
            # runnable directly via --flow or --tag superseded_by_appium.
            if "superseded_by_appium" in flow_tags and (not tags or "superseded_by_appium" not in tags):
                continue
            # UPDATE (2026-08-27): same exclusion pattern again -
            # retry_recovery_02_network_toggle_retry.yaml is structurally
            # blocked (its precondition needs a CommCare-client version
            # range, 2.45-2.54, that's below the current release under
            # test) and was permanently failing in every recovery_measures
            # report despite being documented Not Automatable in the
            # Master Mobile Plan - its own flow tag just never matched
            # that documentation until now. Kept on disk (not deleted) for
            # reference; still runnable directly via --flow or
            # --tag not_automatable.
            if "not_automatable" in flow_tags and (not tags or "not_automatable" not in tags):
                continue
            if not tags:
                selected.add(path)
                continue
            if flow_tags & set(tags):
                selected.add(path)

    # common/ is always needed for runFlow references
    for path in (FLOWS_DIR / "common").glob("*.yaml"):
        selected.add(path)
    return sorted(selected)


def build_flows_zip(flow_files, out_dir):
    """Zip the selected flows preserving their flows/<category>/<file>.yaml
    layout, since flow files reference siblings via relative runFlow paths.

    FIXED 2026-08-08: this only ever copied the .yaml flow files themselves -
    any binary fixture a flow pushes via `addMedia` (e.g.
    flows/multimedia/assets/sample_image.png, referenced by both
    flows/form_submissions/upload_test_01_specific_file_extensions.yaml via
    "../multimedia/assets/sample_image.png" and several flows/multimedia/
    flows via "./assets/sample_image.png") was NEVER included in the
    uploaded testSuite zip, regardless of the addMedia path's phrasing.
    Confirmed live: inspecting the actual zip built for a run of just
    upload_test_01 (+ flows/common/) showed 20 entries, all .yaml, zero
    images - and the corresponding BrowserStack build
    (baca2770ccdfa7a0a01067208180cf3e2c93db19, retried as
    636152ea40e174e2b88eb0e9a9e89ee6d8827592) failed both times with
    [BROWSERSTACK_TESTSUITE_PARSE_ERROR] "No Tests Ran" - the exact same
    failure signature this function's own module docstring already documents
    for a *genuinely missing* addMedia asset ("BrowserStack rejects the
    WHOLE build's parse if even one addMedia target is missing from the
    zip"). The difference here is the asset isn't missing from the repo at
    all (flows/multimedia/assets/sample_image.png exists on disk) - it was
    just never copied into the zip by this function, a bug in the zip
    builder itself rather than the flow file. Fixed by also mirroring every
    flows/<category>/assets/ directory into the zip (small, few-KB fixture
    files - inexpensive to always include) so any relative addMedia
    reference, same-directory or cross-directory, resolves inside the
    archive the same way it resolves on disk relative to FLOWS_DIR."""
    staging = pathlib.Path(out_dir) / "flows"
    if staging.exists():
        shutil.rmtree(staging)
    for f in flow_files:
        rel = f.relative_to(FLOWS_DIR)
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest)

    for assets_dir in FLOWS_DIR.glob("*/assets"):
        rel = assets_dir.relative_to(FLOWS_DIR)
        dest = staging / rel
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(assets_dir, dest)

    zip_path = pathlib.Path(out_dir) / "flows.zip"
    import zipfile
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in staging.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(staging.parent))
    return zip_path


def chunk_flows_by_execute_length(non_common_files, flows_dir, limit=900):
    """BrowserStack rejects a build whose `execute` array serializes past
    1000 characters - discovered via a real 422 running the full suite in
    one build: BROWSERSTACK_INVALID_SYNTAX, "'[execute]' length must be less
    than 1000 characters." Undocumented anywhere, so chunk conservatively
    (limit defaults well under the real 1000 cutoff) rather than assume the
    exact boundary never shifts."""
    chunks, current, current_len = [], [], 0
    for f in non_common_files:
        rel_len = len(f.relative_to(flows_dir).as_posix()) + 1  # +1 for the joining comma
        if current and current_len + rel_len > limit:
            chunks.append(current)
            current, current_len = [], 0
        current.append(f)
        current_len += rel_len
    if current:
        chunks.append(current)
    return chunks


# Overall wall-clock ceiling for a whole run_suite.py invocation. Discovered
# live: a run with several slow/parse-error-prone flows chained enough
# 90-minute wait_for_build retries and per-file fallback attempts (see
# run_build's own docstring) to exceed GitHub Actions' hard 6-hour job limit
# - the job was killed mid-poll with NO report, NO Slack notification, and
# no indication anything had even gone wrong beyond the job simply vanishing.
# Checked before starting each new chunk/retry/fallback attempt - comfortably
# under 6h so the report-generation and Slack-notification steps that run
# AFTER run_suite.py still have time to execute once this returns.
DEFAULT_WALL_CLOCK_BUDGET_SECONDS = 5 * 3600


def run_all_builds(bs, flow_files, app_url, args, env_variables, other_app_urls, tmp_dir, build_name=None,
                    deadline=None):
    """Runs flow_files as one or more BrowserStack builds - chunked per
    chunk_flows_by_execute_length when the execute list would be too long for
    a single build - and returns (build_ids, combined normalized test
    results). A logical "run" can be >1 real BrowserStack build; callers
    report/retry against the combined list, not per-chunk. common/ subflows
    are always pulled in fresh here (not from flow_files) since every chunk
    needs the full set for runFlow references, independent of which
    top-level flows that chunk executes.

    deadline is a time.monotonic() timestamp (see DEFAULT_WALL_CLOCK_BUDGET_
    SECONDS) - any chunk not yet started once it's passed is skipped outright
    (synthesized as "failed" with a clear reason) rather than risking the
    open-ended retry/fallback chain that caused the 6-hour hang above."""
    non_common_files = [f for f in flow_files if f.parent.name != "common"]
    common_files = list((FLOWS_DIR / "common").glob("*.yaml"))
    chunks = chunk_flows_by_execute_length(non_common_files, FLOWS_DIR)

    def synthesize_missing(files, reason):
        return [report_generator.TestResult(
            name=f"{f.parent.name}/{f.stem}",
            workflow=f.parent.name,
            status="failed",
            error=reason,
        ) for f in files]

    build_ids, test_results = [], []
    for i, chunk in enumerate(chunks):
        if deadline is not None and time.monotonic() > deadline:
            print(f"Wall-clock budget exceeded - skipping remaining {len(chunks) - i} chunk(s) "
                  f"({sum(len(c) for c in chunks[i:])} flow file(s)) outright.")
            test_results.extend(synthesize_missing(
                [f for c in chunks[i:] for f in c],
                "Skipped: this run's overall wall-clock budget was exceeded before this "
                "flow's chunk could even be attempted (see DEFAULT_WALL_CLOCK_BUDGET_SECONDS "
                "in run_suite.py).",
            ))
            break
        chunk_name = f"{build_name}-part{i + 1}" if build_name and len(chunks) > 1 else build_name
        if len(chunks) > 1:
            print(f"Build part {i + 1}/{len(chunks)}: {len(chunk)} flow file(s)")
        # run_build normally returns a single (build_id, result,
        # covered_files, upload_attempts) tuple, but falls back to one tuple
        # per flow file if the whole chunk stays a total parse error after
        # retries (see run_build's own docstring) - handle both shapes
        # uniformly.
        for build_id, result, covered_files, upload_attempts in run_build(
                bs, chunk + common_files, app_url, args, env_variables, other_app_urls, tmp_dir,
                build_name=chunk_name, deadline=deadline):
            if build_id is not None:
                build_ids.append(build_id)
            normalized = report_generator.normalize_build(result, bs_client=bs) if result else []
            if upload_attempts > 1:
                # Not the same thing as the existing "rerun" status (that's
                # for a flow that genuinely EXECUTED and failed, then passed
                # on a real retry) - this is BrowserStack's upload/parse
                # pipeline needing multiple attempts before Maestro ever ran
                # anything, so status/counts are left alone; just note it so
                # it isn't invisible (confirmed live: a flow that needed 2
                # retries reported a clean 100% pass rate with Rerun: 0).
                note = f"(passed after {upload_attempts} upload attempts due to an intermittent BrowserStack-side build failure - parse error or a mid-run session error)"
                for r in normalized:
                    r.error = f"{r.error} {note}".strip() if r.error else note
            test_results.extend(normalized)
            # A flow whose build stayed a total parse error (or ran out of
            # wall-clock budget before even being attempted) has no
            # testcases.data at all - normalize can't produce anything for
            # it, so it would otherwise vanish from the report's totals
            # instead of counting as a failure. Synthesize an explicit
            # failed result for any covered flow with no matching
            # normalized entry.
            seen_names = {r.name for r in normalized}
            missing = [f for f in covered_files if f"{f.parent.name}/{f.stem}" not in seen_names]
            test_results.extend(synthesize_missing(
                missing,
                f"BrowserStack build {build_id} stayed a total failure "
                f"({_describe_build_failure(result)}) even after retry/fallback - this "
                f"flow's real result (if any) couldn't be recovered (see the CI log for "
                f"retry/fallback details)."
                if build_id else
                "Skipped: this run's overall wall-clock budget was exceeded before this "
                "flow could even be attempted (see DEFAULT_WALL_CLOCK_BUDGET_SECONDS in "
                "run_suite.py).",
            ))
    return build_ids, test_results


def _is_testsuite_parse_error(result):
    """True if every device/session in a build result came back as a total
    BROWSERSTACK_TESTSUITE_PARSE_ERROR ("No Tests Ran", testcases.count==0) -
    confirmed live (repeatedly, deterministically for identical re-uploads of
    the exact same small flow sets, while much larger uploads parsed fine)
    that this is a real, unexplained BrowserStack-side parsing failure, not a
    zip-structure or flow-content bug - re-uploading the identical content as
    a fresh test-suite (new bs:// id) reliably clears it. --retry-failed
    can't help here since match_flow_files has no failed test NAMES to match
    against when the whole build reports zero testcases.

    UPDATE, confirmed live (CI run 31616429191, build
    c4c49e90d5da5ecfdbcb3b95a1ef8be3bb14bbdf): a SEPARATE BrowserStack
    infra-level failure mode hit a 15-flow chunk - session status "error",
    message "Could not start a session : Something went wrong during test
    execution.", with a NONZERO build-level aggregate (testcases.count=15,
    12 passed/0 failed/3 error) - i.e. this ORIGINALLY looked like a normal
    completed build, not a parse error, so this function returned False and
    run_build accepted the result as final with zero retries. The real
    problem: BrowserStack's separate per-session detail endpoint
    (browserstack_client.get_session, which normalize_build calls to get
    actual per-test names/statuses - the build-level response only ever
    carries the aggregate count) returned an EMPTY testcases.data array for
    this session despite that nonzero aggregate - confirmed live via a
    direct GET .../builds/<id>/sessions/<id> call. With no per-test data to
    read, report_generator has no way to know WHICH of the 15 passed, so
    ALL 15 got silently synthesized as "failed" by run_all_builds's own
    missing-flow fallback - even though 12 genuinely passed on
    BrowserStack's side. Any session-level status=="error" (BrowserStack's
    own signal that its infra broke before a trustworthy per-test result
    was produced, distinct from "failed" which means the test itself ran
    and failed) is now treated the same as a parse error: retry, then fall
    back to individual single-flow builds if it persists - matching the
    same "re-upload/re-run to get real per-test data" remedy that already
    works for the classic 0-count parse error."""
    if not isinstance(result, dict):
        return False
    for device in result.get("devices", []):
        for session in device.get("sessions", []):
            error = session.get("error") or {}
            count = (session.get("testcases") or {}).get("count", 0)
            status = session.get("status")
            if count == 0 and (
                "PARSE_ERROR" in (error.get("message") or "")
                or error.get("short_error_message") == "No Tests Ran"
            ):
                continue
            if status == "error":
                continue
            return False
    return bool(result.get("devices"))


def _describe_build_failure(result):
    """Human-readable reason a build tripped _is_testsuite_parse_error -
    either the classic 0-count parse error, or the session-status=="error"
    infra failure documented in that function's own UPDATE - so log/failure
    messages stop unconditionally saying "PARSE_ERROR (0 tests ran)" for a
    build that BrowserStack's own aggregate says actually ran real tests."""
    if not isinstance(result, dict):
        return "an unrecognized build response"
    reasons = []
    for device in result.get("devices", []):
        for session in device.get("sessions", []):
            error = session.get("error") or {}
            count = (session.get("testcases") or {}).get("count", 0)
            if count == 0:
                reasons.append("BROWSERSTACK_TESTSUITE_PARSE_ERROR (0 tests ran)")
            else:
                status_line = (session.get("testcases") or {}).get("status", {})
                reasons.append(
                    f"session status=\"error\" ({error.get('message') or 'no message'}) "
                    f"with a nonzero aggregate ({count} tests, {status_line}) but no "
                    f"retrievable per-test data"
                )
    return "; ".join(reasons) if reasons else "an unrecognized build response"


def _run_build_once(bs, flow_files, app_url, args, env_variables, other_app_urls, tmp_dir, build_name=None):
    """One upload+trigger+wait cycle, no retry/fallback logic - the primitive
    run_build composes into its retry-then-split strategy."""
    zip_path = build_flows_zip(flow_files, tmp_dir)
    print(f"Uploading test suite ({len(flow_files)} flow file(s)) to BrowserStack ...")
    suite_resp = bs.upload_test_suite(str(zip_path))

    # BrowserStack only auto-discovers flows at the zip's root by default;
    # ours live under flows/<workflow>/*.yaml, so pass explicit paths
    # (relative to the zip's root "flows/" folder) for everything that isn't
    # a flows/common/*.yaml helper (those are only reachable via runFlow,
    # never meant to be executed directly as top-level tests).
    execute = [f.relative_to(FLOWS_DIR).as_posix() for f in flow_files if f.parent.name != "common"]
    build_resp = bs.trigger_build(
        app_url=app_url,
        test_suite_url=suite_resp["test_suite_url"],
        devices=[d.strip() for d in args.devices.split(",")],
        project=args.project,
        build_name=build_name,
        execute=execute,
        env_variables=env_variables,
        other_apps=other_app_urls,
    )
    print(f"Build triggered: {json.dumps(build_resp, indent=2)}")

    build_id = build_resp.get("build_id") or build_resp.get("id")
    if not build_id:
        print("No build_id in response - can't poll for status.")
        return None, None
    if args.no_wait:
        return build_id, None
    print(f"Waiting for build {build_id} ...")
    result = bs.wait_for_build(build_id)
    print(json.dumps(result, indent=2))
    return build_id, result


def run_build(bs, flow_files, app_url, args, env_variables, other_app_urls, tmp_dir, build_name=None,
              max_parse_retries=1, deadline=None):
    """Zip the given flow files, upload as a test suite, trigger a build, and
    wait for it. Shared by the main run and the --retry-failed re-run so both
    go through the exact same upload/trigger/poll path. Returns a LIST of
    (build_id, result) pairs - normally just one, but see the fallback below.

    Retries the WHOLE upload+trigger+wait cycle (fresh zip, fresh test-suite
    upload) up to max_parse_retries times if the build comes back as a total
    testsuite parse error (see _is_testsuite_parse_error's docstring) - this
    clears genuinely transient upload/parse hiccups.

    If it's STILL a parse error after every retry, live investigation found
    this can also be a deterministic property of a specific multi-flow
    combination (confirmed: the exact same 2-3 flow set from one directory
    failed identically on 6 separate attempts, including 3 fresh-upload
    retries in a row, while single-flow uploads of the same content - and
    even 5-14 flow combinations from OTHER directories - reliably parsed
    fine; no zip-structure, line-ending, or content difference explains it).
    Retrying identical content doesn't fix a deterministic failure, so as a
    last resort this falls back to running each non-common flow file in the
    batch as its OWN single-flow build and concatenating the results -
    slower, but every single-flow upload attempted during that investigation
    parsed successfully, so this guarantees forward progress instead of
    losing the whole batch to an unexplained BrowserStack-side quirk.

    Each returned tuple's `result` may STILL be a total parse error (a
    single-flow build can rarely hit this too, confirmed live) - the caller
    is responsible for noticing when a covered flow never got a real
    testcases.data entry and surfacing that explicitly (see
    run_all_builds' own comment on this): normalize_session has nothing to
    read out of a session with no `testcases.data` array at all, so a flow
    stuck in this state otherwise vanishes from the report's totals
    silently instead of counting as a failure - confirmed live on
    session_expiration/setup_02_restore_user_session_expires.yaml, whose
    build showed a real BrowserStack "0 unique tests" parse error in the
    dashboard, but the generated report's Total counted only 1 of the 2
    flows that were actually supposed to run.

    The 4th element, upload_attempts, is how many total upload+trigger+wait
    cycles it took to get a non-parse-error result (1 if it worked first
    try). This is NOT the same thing as the existing "rerun" concept
    (--retry-failed re-running a flow that genuinely EXECUTED and failed) -
    every parse-error attempt here has testcases.count==0, meaning Maestro
    never actually ran the flow at all, so it's an upload/parse retry, not a
    test retry. Still worth surfacing (confirmed live: a flow that needed 2
    retries before passing showed a clean 100% pass rate with Rerun: 0,
    hiding that BrowserStack's upload pipeline was flaky that run) - the
    caller notes it on the TestResult without touching status or the
    Rerun count, which already means something else."""
    non_common = [f for f in flow_files if f.parent.name != "common"]
    common = [f for f in flow_files if f.parent.name == "common"]

    if deadline is not None and time.monotonic() > deadline:
        print(f"Wall-clock budget already exceeded - skipping {[str(f.name) for f in non_common]} outright.")
        return [(None, None, non_common, 0)]

    build_id, result = None, None
    for attempt in range(max_parse_retries + 1):
        build_id, result = _run_build_once(bs, flow_files, app_url, args, env_variables,
                                            other_app_urls, tmp_dir, build_name=build_name)
        if not _is_testsuite_parse_error(result):
            return [(build_id, result, non_common, attempt + 1)]
        if attempt < max_parse_retries:
            if deadline is not None and time.monotonic() > deadline:
                print(f"Wall-clock budget exceeded mid-retry for build {build_id} - "
                      f"accepting its last parse-error result rather than retrying further.")
                break
            print(f"Build {build_id} stayed a total failure ({_describe_build_failure(result)}) "
                  f"- retrying with a fresh upload ...")

    if len(non_common) <= 1:
        return [(build_id, result, non_common, max_parse_retries + 1)]
    if deadline is not None and time.monotonic() > deadline:
        print(f"Wall-clock budget exceeded - skipping the per-file fallback for "
              f"{[str(f.name) for f in non_common]} outright.")
        return [(None, None, non_common, 0)]
    print(f"Build {build_id} stayed a total failure ({_describe_build_failure(result)}) "
          f"after {max_parse_retries + 1} attempts - "
          f"falling back to {len(non_common)} individual single-flow builds ...")
    quads = []
    for i, f in enumerate(non_common):
        if deadline is not None and time.monotonic() > deadline:
            print(f"Wall-clock budget exceeded - skipping remaining fallback file(s) outright.")
            quads.append((None, None, non_common[i:], 0))
            break
        sub_name = f"{build_name}-single{i + 1}" if build_name else None
        quads.extend(run_build(bs, [f] + common, app_url, args, env_variables, other_app_urls,
                                tmp_dir, build_name=sub_name, max_parse_retries=max_parse_retries,
                                deadline=deadline))
    return quads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", action="append", dest="tags",
                         help="Only run flows carrying this tag (repeatable).")
    parser.add_argument("--flow", action="append", dest="flows",
                         help="Run this specific flow file (repeatable). Overrides --tag.")
    parser.add_argument("--apk", default=None, help="Path to an already-downloaded APK.")
    parser.add_argument("--release-tag", default=None,
                         help="GitHub release tag to download if --apk isn't given.")
    parser.add_argument("--hq-setup", default=None,
                         help="Path to an hq_setup/*.json pre-step spec to run first.")
    parser.add_argument("--hq-teardown", default=None,
                         help="Path to an hq_setup/*.json spec to run last, ALWAYS (even if the "
                              "flows themselves fail or raise) - for restoring shared HQ state a "
                              "--hq-setup mutated on an app other flows also depend on. Best-effort: "
                              "retried up to 3 times, logged rather than raised on final failure, so "
                              "a teardown hiccup never masks the real test results. Its spec's "
                              "apk_version may use the \"$CURRENT_APK_VERSION\" sentinel, substituted "
                              "with this run's own resolved APK version - see hq_client.py's "
                              "run_pre_step().")
    # UPDATE (2026-08-17), per direct user request: bumped the default
    # device+Android version from Samsung Galaxy S20 (Android 10) to
    # Samsung Galaxy S26 (Android 16) - the latest Android version
    # BrowserStack has a real Samsung flagship device for (confirmed
    # against api-cloud.browserstack.com/app-automate/devices.json), same
    # convention as .github/workflows/maestro-browserstack.yml's own
    # android_version-to-device mapping.
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0",
                         help="Comma-separated BrowserStack device names.")
    parser.add_argument("--project", default="QA COMMCARE MOBILE TESTS")
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE",
                         help="Sent as BrowserStack's customBuildName - shows up in the App "
                              "Automate dashboard in place of the generic 'Build #N' label.")
    parser.add_argument("--no-wait", action="store_true", help="Trigger the build but don't poll for results.")
    parser.add_argument("--other-app", action="append", dest="other_apps",
                         help="Path to a companion APK to pre-install alongside the main app "
                              "(repeatable, max 3 - e.g. the ExternalApp Tests companion app).")
    parser.add_argument("--retry-failed", action="store_true",
                         help="If any flow fails, re-trigger a second build containing only the failed "
                              "flows and merge the result - a test that passes on retry is reported as "
                              "'rerun' (flaky) instead of 'failed'. Relies on report_generator.match_flow_files' "
                              "name-matching heuristic - see its docstring caveat.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    prior_build_by_app_id = {}
    client = None
    if args.hq_setup:
        print(f"Running HQ pre-step: {args.hq_setup}")
        with open(args.hq_setup) as f:
            spec = json.load(f)
        # HQClient.login()'s own bare default (HQ_API_USERNAME/PASSWORD) does
        # not work against this domain (confirmed live, 2026-08-10 -
        # "HQ login did not redirect away from the login page") - use the
        # same HQ_WEB_USER_EMAIL/PASSWORD account resolve_app_codes() already
        # relies on below, which is confirmed to work without a 2FA prompt.
        client = hq_client_module.HQClient().login(
            username=os.environ.get("HQ_WEB_USER_EMAIL"),
            password=os.environ.get("HQ_WEB_USER_PASSWORD"),
        )
        # UPDATE (2026-08-20), confirmed live: a spec that creates+releases a
        # NEW build (e.g. varying_prompt_setup.json, to make a pending update
        # exist for the varying_prompt_* flows to detect) races the plain
        # resolve_app_codes() call below - both it and create_new_build's own
        # mark_build_status resolve to "current top build" via the same
        # release_first=True default (get_app_install_code), so the flow's
        # install always lands on the build hq_setup just created, never the
        # one that was current before it ran - leaving no update pending at
        # all. This is exactly what made all 4 varying_prompt_* flows fail
        # today ("New version of the application is available" never
        # appeared) - the same release_first conflict already fixed for
        # prompted_updates' Appium path (scripts/run_appium_prompted_updates_
        # suite.py's _resolve_top_two_builds), just not here yet. Captures
        # each touched app's PRE-hq_setup top build id so it can be pinned via
        # resolve_app_codes' 3-tuple form below instead of racing it.
        for action in spec.get("actions", []):
            if action.get("type") == "create_new_build":
                app_id = action["app_id"]
                releases = client.list_releases(app_id, only_show_released=False, limit=1)
                if releases:
                    prior_build_by_app_id[app_id] = releases[0]["_id"]
        hq_client_module.run_pre_step(spec, client=client)

    apk_path = args.apk
    apk_commcare_version = None
    REPORTS_DIR = REPO_ROOT / "reports"
    if not apk_path:
        release, asset = download_apk.resolve(args.release_tag)
        apk_path = f"apks/{asset['name']}"
        print(f"Downloading {asset['name']} from {release['tag_name']} ...")
        download_apk.download(asset["browser_download_url"], apk_path, expected_size=asset["size"])
        # release['tag_name'] is like "commcare_2.63.4" - strip the prefix so
        # both get_app_install_code's max_commcare_version comparison and the
        # Slack notification (reports/apk_version.txt) get a bare "2.63.4".
        apk_commcare_version = release["tag_name"].removeprefix("commcare_")
        REPORTS_DIR.mkdir(exist_ok=True)
        (REPORTS_DIR / "apk_version.txt").write_text(apk_commcare_version, encoding="utf-8")
    else:
        # UPDATE (2026-08-20), per direct user question: a custom --apk
        # (e.g. the CI workflow's apk_source dropdown picking a committed
        # resources/*.apk instead of a GitHub release) skipped this whole
        # branch entirely, so reports/apk_version.txt never got written and
        # the Slack notification silently had no APK version/source line at
        # all - not that it was wrong, just entirely absent. Records the
        # APK's own filename instead of a release tag (there isn't one) so
        # the notification still shows SOMETHING recognizable.
        REPORTS_DIR.mkdir(exist_ok=True)
        (REPORTS_DIR / "apk_version.txt").write_text(
            f"{pathlib.Path(apk_path).name} (custom)", encoding="utf-8",
        )

    try:
        _dispatch_and_report(args, apk_path, apk_commcare_version, prior_build_by_app_id)
    finally:
        # UPDATE (2026-08-24), per direct user instruction: a --hq-teardown
        # spec (e.g. hq_setup/updates_2_49/prompted_update_scenario_teardown.json)
        # must run regardless of whether _dispatch_and_report above passed,
        # failed, or raised (including its own sys.exit(1) on a failed
        # result) - same "cleanup is not part of the test result" principle
        # as scripts/run_appium_prompted_updates_suite.py's own finally-block
        # cleanup. See _run_hq_teardown()'s own docstring for the retry
        # rationale.
        if args.hq_teardown:
            _run_hq_teardown(args.hq_teardown, client, apk_commcare_version)


def _run_hq_teardown(hq_teardown_path, client, apk_commcare_version):
    """
    Best-effort HQ cleanup, run from main()'s finally block. Mirrors
    scripts/run_appium_prompted_updates_suite.py's own finally-block cleanup
    (see that file's UPDATE comments for why retries matter: a transient
    network blip on a single un-retried cleanup call can leave shared HQ
    state - e.g. Prompt Updates left on, or pointed at a dev/pre-release
    build - stuck for every other flow that touches the same app
    afterward). Logs and swallows failure after 3 attempts rather than
    raising, so a teardown hiccup never masks/replaces the real dispatch
    results _dispatch_and_report() already reported.

    `apk_commcare_version` (e.g. "2.63.4", from download_apk's release tag)
    becomes the teardown spec's "$CURRENT_APK_VERSION" sentinel value, in
    the same "<version>/latest" shape every other apk_version value in this
    repo uses - so a teardown can restore apk_version to the ACTUAL release
    this run tested against, not a hardcoded literal. None (a custom --apk
    with no release tag) means any action relying on that sentinel will
    raise inside run_pre_step - logged like any other teardown failure,
    not a special case.
    """
    print(f"Running HQ teardown: {hq_teardown_path}")
    with open(hq_teardown_path) as f:
        spec = json.load(f)
    client = client or hq_client_module.HQClient().login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"),
        password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    current_apk_version = f"{apk_commcare_version}/latest" if apk_commcare_version else None
    for attempt in range(3):
        try:
            hq_client_module.run_pre_step(spec, client=client, current_apk_version=current_apk_version)
            return
        except Exception as exc:  # noqa: BLE001 - best-effort, never mask the real dispatch results
            if attempt < 2:
                time.sleep(5)
                continue
            print(f"::warning::HQ teardown {hq_teardown_path} failed after 3 attempts, "
                  f"may need manual fixup on HQ: {exc}")


def _dispatch_and_report(args, apk_path, apk_commcare_version, prior_build_by_app_id):
    flow_files = select_flow_files(tags=args.tags, explicit_flows=args.flows)
    if not flow_files:
        raise SystemExit("No matching flow files found for the given --tag/--flow filters.")
    print(f"Selected {len(flow_files)} flow file(s):")
    for f in flow_files:
        print(f"  {f.relative_to(REPO_ROOT)}")

    with tempfile.TemporaryDirectory() as tmp:
        bs = BrowserStackClient()
        print("Uploading app to BrowserStack ...")
        app_resp = bs.upload_app(apk_path)

        other_app_urls = None
        if args.other_apps:
            other_app_urls = []
            for other_apk in args.other_apps:
                print(f"Uploading companion app {other_apk} to BrowserStack ...")
                other_app_urls.append(bs.upload_app(other_apk)["app_url"])

        env_variables = {k: v for k in FLOW_ENV_VARS if (v := os.environ.get(k))}

        # Resolve a fresh install code for whichever apps the selected flows
        # actually reference (flows/common/install_app_by_code.yaml callers
        # pass env: {APP_CODE: ${APP_CODE_<KEY>}}) - NEVER hardcoded, since a
        # code is tied to one specific build and goes stale the moment
        # someone cuts + publishes a new version. Only resolves keys actually
        # in use so a single-tag run doesn't need every domain's credentials.
        selected_text = "\n".join(f.read_text(encoding="utf-8") for f in flow_files)
        # UPDATE (2026-08-20), confirmed live: a plain substring check false-
        # matched APP_CODE_CASE_MANAGEMENTS inside the LONGER
        # APP_CODE_CASE_MGMT_VP (a real env var this repo
        # now uses), pulling in an extra unneeded key - a word-boundary-style
        # check (no further identifier char immediately after) avoids that.
        needed_keys = {key for key in APP_REGISTRY if re.search(rf"APP_CODE_{re.escape(key)}(?!\w)", selected_text)}
        # NO_VERSION_FILTER_KEYS (e.g. BASIC_TESTS_LATEST) deliberately skip
        # the max_commcare_version safety filter - see app_registry.py's own
        # comment on that set for why (some tests need the CommCare-APK-
        # version-mismatch dialog to actually appear).
        filtered_keys = needed_keys - app_registry.NO_VERSION_FILTER_KEYS
        unfiltered_keys = needed_keys & app_registry.NO_VERSION_FILTER_KEYS

        def _registry_entry(key):
            # Pins to the PRE-hq_setup build when one was captured above,
            # instead of racing hq_setup's own newly-created/released build.
            # A registry entry that's ALREADY pinned (a 3-tuple, e.g.
            # RU_TEST_ONE/TWO/THREE, CASE_MGMT_VP - or a
            # 4-tuple with a trailing release_first override, e.g.
            # MOBILE2_47) is left untouched - it's pinned to an exact build
            # on purpose. UPDATE (2026-08-21): was `== 3`, which crashed on
            # the first 4-tuple entry ("too many values to unpack") the same
            # way an earlier 3-tuple entry once crashed this same function.
            entry = APP_REGISTRY[key]
            if len(entry) >= 3:
                return entry
            domain, app_id = entry
            prior_build_id = prior_build_by_app_id.get(app_id)
            return (domain, app_id, prior_build_id) if prior_build_id else (domain, app_id)

        if filtered_keys:
            print(f"Resolving install codes for: {', '.join(sorted(filtered_keys))} ...")
            env_variables.update(hq_client_module.resolve_app_codes(
                {k: _registry_entry(k) for k in filtered_keys},
                max_commcare_version=apk_commcare_version,
            ))
        if unfiltered_keys:
            print(f"Resolving install codes (unfiltered) for: {', '.join(sorted(unfiltered_keys))} ...")
            env_variables.update(hq_client_module.resolve_app_codes(
                {k: _registry_entry(k) for k in unfiltered_keys},
            ))

        # See DEFAULT_WALL_CLOCK_BUDGET_SECONDS's own comment - computed once
        # here so it covers the WHOLE run (main pass + --retry-failed pass),
        # not reset per call.
        deadline = time.monotonic() + DEFAULT_WALL_CLOCK_BUDGET_SECONDS

        build_ids, test_results = run_all_builds(bs, flow_files, app_resp["app_url"], args,
                                                  env_variables, other_app_urls, tmp,
                                                  build_name=args.build_name, deadline=deadline)
        if not build_ids or args.no_wait:
            return

        failed = [r for r in test_results if r.status == "failed"]
        if args.retry_failed and failed:
            retry_files = report_generator.match_flow_files(failed, flow_files, FLOWS_DIR)
            if not retry_files:
                print("No flow files matched the failed test names - skipping retry "
                      "(see report_generator.match_flow_files' name-matching caveat).")
            else:
                print(f"Retrying {len(retry_files)} failed flow(s) ...")
                retry_build_name = f"{args.build_name}-retry" if args.build_name else None
                _, retry_test_results = run_all_builds(bs, retry_files, app_resp["app_url"], args,
                                                        env_variables, other_app_urls, tmp,
                                                        build_name=retry_build_name, deadline=deadline)
                if retry_test_results:
                    test_results = report_generator.merge_rerun(test_results, retry_test_results)

        # UPDATE (2026-08-25), confirmed live in CI (a real merged-report
        # run showing only 88 of the ~139 unique flows this exact dispatch
        # actually selected across all 3 matrix groups - entire categories
        # from group-a's own MAIN dispatch, e.g. multimedia/install/
        # other_error_tests/support_menus/trigger_device_logs, were
        # completely absent): generate_report() always OVERWRITES
        # reports/latest_results.json, and group-a's job now runs
        # run_suite.py 3 times in sequence (the main tag-based dispatch,
        # then the 2 dedicated updates_2_49 apk-prompt steps) - each later
        # call was silently clobbering the earlier one's results instead
        # of adding to them, so only the LAST invocation's tiny result set
        # ever made it into that job's uploaded artifact. Same real bug
        # scripts/run_appium_suite.py's own main() already found and fixed
        # (2026-08-19) for exactly this "extra step in an existing job"
        # pattern - see that file's own citation for the full reasoning,
        # including why entries are replaced-by-name rather than blindly
        # concatenated (a stale local rerun otherwise lingers forever).
        existing_results_path = REPO_ROOT / "reports" / "latest_results.json"
        if existing_results_path.exists():
            existing = json.loads(existing_results_path.read_text(encoding="utf-8"))
            new_names = {r.name for r in test_results}
            test_results = [report_generator.TestResult(**item) for item in existing
                             if item["name"] not in new_names] + test_results

        report_path = report_generator.generate_report(build_ids[0], test_results)
        print(f"HTML report: {report_path}")

        if any(r.status == "failed" for r in test_results):
            sys.exit(1)


if __name__ == "__main__":
    main()
