"""
Orchestrator: ensure an APK is available, optionally run an HQ pre-step, pick
which Maestro flows to run (by tag), zip them, upload app + flows to
BrowserStack App Automate, trigger a Maestro build, and wait for the result.

Usage:
    python scripts/run_suite.py --tag mobile_pins --devices "Samsung Galaxy S20-10.0"
    python scripts/run_suite.py --tag prompted_updates --hq-setup hq_setup/prompted_updates/varying_prompt_setup.json
    python scripts/run_suite.py --flow flows/install/install_04_see_apps_menu_item_visible.yaml
"""
import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import download_apk
import hq_client as hq_client_module
import report_generator
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
    (needed by runFlow references) regardless of tag filtering."""
    if explicit_flows:
        selected = {pathlib.Path(f).resolve() for f in explicit_flows}
    else:
        selected = set()
        for path in FLOWS_DIR.rglob("*.yaml"):
            if path.parent.name == "common":
                continue
            if not tags:
                selected.add(path)
                continue
            with open(path, encoding="utf-8") as fh:
                doc = next(yaml.safe_load_all(fh))
            flow_tags = set((doc or {}).get("tags") or [])
            if flow_tags & set(tags):
                selected.add(path)

    # common/ is always needed for runFlow references
    for path in (FLOWS_DIR / "common").glob("*.yaml"):
        selected.add(path)
    return sorted(selected)


def build_flows_zip(flow_files, out_dir):
    """Zip the selected flows preserving their flows/<category>/<file>.yaml
    layout, since flow files reference siblings via relative runFlow paths."""
    staging = pathlib.Path(out_dir) / "flows"
    if staging.exists():
        shutil.rmtree(staging)
    for f in flow_files:
        rel = f.relative_to(FLOWS_DIR)
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, dest)

    zip_path = pathlib.Path(out_dir) / "flows.zip"
    import zipfile
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in staging.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(staging.parent))
    return zip_path


def run_build(bs, flow_files, app_url, args, env_variables, other_app_urls, tmp_dir, build_name=None):
    """Zip the given flow files, upload as a test suite, trigger a build, and
    wait for it. Shared by the main run and the --retry-failed re-run so both
    go through the exact same upload/trigger/poll path."""
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
    parser.add_argument("--devices", default="Samsung Galaxy S20-10.0",
                         help="Comma-separated BrowserStack device names.")
    parser.add_argument("--project", default="CommCare QA")
    parser.add_argument("--build-name", default=None)
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

    if args.hq_setup:
        print(f"Running HQ pre-step: {args.hq_setup}")
        with open(args.hq_setup) as f:
            spec = json.load(f)
        client = hq_client_module.HQClient().login()
        hq_client_module.run_pre_step(spec, client=client)

    apk_path = args.apk
    if not apk_path:
        release, asset = download_apk.resolve(args.release_tag)
        apk_path = f"apks/{asset['name']}"
        print(f"Downloading {asset['name']} from {release['tag_name']} ...")
        download_apk.download(asset["browser_download_url"], apk_path)

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

        build_id, result = run_build(bs, flow_files, app_resp["app_url"], args,
                                      env_variables, other_app_urls, tmp, build_name=args.build_name)
        if build_id is None or args.no_wait:
            return

        test_results = report_generator.normalize_build(result)

        failed = [r for r in test_results if r.status == "failed"]
        if args.retry_failed and failed:
            retry_files = report_generator.match_flow_files(failed, flow_files, FLOWS_DIR)
            if not retry_files:
                print("No flow files matched the failed test names - skipping retry "
                      "(see report_generator.match_flow_files' name-matching caveat).")
            else:
                print(f"Retrying {len(retry_files)} failed flow(s) ...")
                retry_build_name = f"{args.build_name}-retry" if args.build_name else None
                _, retry_result = run_build(bs, retry_files, app_resp["app_url"], args,
                                             env_variables, other_app_urls, tmp, build_name=retry_build_name)
                if retry_result is not None:
                    retry_test_results = report_generator.normalize_build(retry_result)
                    test_results = report_generator.merge_rerun(test_results, retry_test_results)

        report_path = report_generator.generate_report(build_id, test_results)
        print(f"HTML report: {report_path}")

        if any(r.status == "failed" for r in test_results):
            sys.exit(1)


if __name__ == "__main__":
    main()
