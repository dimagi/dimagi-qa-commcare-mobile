"""
Master Mobile Plan (2026) > Advanced Settings > Custom Properties 11-14 on the
"Targeted Updates App" (scripts/app_registry.py TARGETED_UPDATES):

    cp11  cc-update-target = release   update -> newest RELEASED build
    cp12  cc-update-target = build     update -> newest build, even "In Test"
    cp13  cc-update-target = save      update -> latest saved (unbuilt) state
    cp14  pre-update-sync-needed = yes Update App offers "Sync to update"

Each row needs HQ state a plain Maestro run can't create, and the device
must install a build made WITH the new custom property (run_suite.py's
--hq-setup pins installs to the build that was on top BEFORE setup, the
opposite of what these rows need). So per scenario this script:

  1. sets the app's custom properties (HQClient.set_custom_properties),
  2. cuts + releases base build A (the one the device installs),
  3. creates the newer state the row needs (a newer released build and/or
     an "In Test" build, or just an unsaved draft change via
     edit_module_attr on the module's internal `comment` field),
  4. runs the matching flows_non_core/advanced_settings/custom_properties_1N_*.yaml
     through scripts/run_suite.py, passing the base build's install code and
     what Update App must offer via --env,

and ALWAYS (finally) restores the original custom properties and module
comment, then cuts + releases one build with those original settings, so
the app ends where it started. Builds can't be deleted on HQ, so each run
leaves its builds (commented "Maestro CP1N ...") in the release history -
same as core's update checks.

Usage:
    python scripts/run_targeted_updates_check.py --devices "Samsung Galaxy S26-16.0"
    python scripts/run_targeted_updates_check.py --scenario cp11 --release-tag commcare_2.64.0
"""
import argparse
import os
import pathlib
import subprocess
import sys
import time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import hq_client as hq_client_module
from app_registry import APP_REGISTRY

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOWS = {
    "cp11": "flows_non_core/advanced_settings/custom_properties_11_update_target_release.yaml",
    "cp12": "flows_non_core/advanced_settings/custom_properties_12_update_target_build.yaml",
    "cp13": "flows_non_core/advanced_settings/custom_properties_13_update_target_save.yaml",
    "cp14": "flows_non_core/advanced_settings/custom_properties_14_pre_update_sync_needed.yaml",
}
PROPS = {
    "cp11": {"cc-update-target": "release", "pre-update-sync-needed": "no"},
    "cp12": {"cc-update-target": "build", "pre-update-sync-needed": "no"},
    "cp13": {"cc-update-target": "save", "pre-update-sync-needed": "no"},
    "cp14": {"cc-update-target": "build", "pre-update-sync-needed": "yes"},
}


def _cut_build(client, app_id, comment, release):
    """create_new_build, then read the new build back from list_releases -
    the create response's own shape was never confirmed live (docs/
    FRAMEWORK.md known gap 2), the release list is."""
    client.create_new_build(app_id, comment=comment)
    newest = client.list_releases(app_id, only_show_released=False, limit=1)[0]
    if (newest.get("build_comment") or "") != comment:
        raise RuntimeError(f"newest build after create_new_build isn't ours: {newest.get('build_comment')!r}")
    if release:
        client.mark_build_status(app_id, newest["id"], is_released=True)
    return newest["id"], newest["version"]


def _make_change(client, app_id, module_id, label):
    """A real but harmless draft change: the module's internal `comment`
    field (never shown on the device - see edit_module_attr's docstring).
    Returns the app's new draft version."""
    resp = client.edit_module_attr(app_id, module_id, "comment", f"Maestro {label} {int(time.time())}")
    return (resp.get("update") or {}).get("app-version")


def _prepare(client, app_id, module_id, scenario):
    tag = scenario.upper()
    client.set_custom_properties(app_id, PROPS[scenario])
    base_id, base_version = _cut_build(client, app_id, f"Maestro {tag} base", release=True)
    if scenario == "cp11":
        _make_change(client, app_id, module_id, tag)
        _, target = _cut_build(client, app_id, f"Maestro {tag} newer released", release=True)
        _make_change(client, app_id, module_id, tag)
        _cut_build(client, app_id, f"Maestro {tag} newest in test", release=False)
    elif scenario == "cp12":
        _make_change(client, app_id, module_id, tag)
        _, target = _cut_build(client, app_id, f"Maestro {tag} newest in test", release=False)
    elif scenario == "cp13":
        target = _make_change(client, app_id, module_id, tag)
        if not target:
            raise RuntimeError("edit_module_attr didn't report the new draft app-version")
    else:  # cp14
        _make_change(client, app_id, module_id, tag)
        _cut_build(client, app_id, f"Maestro {tag} newer", release=False)
        target = None
    code = client.get_app_install_code(app_id, saved_app_id=base_id, release_first=False)
    expected_update = f"Update to version {target} & log out" if target else "Sync to update"
    print(f"{scenario}: base v{base_version} installed via code; expecting {expected_update!r}")
    return code, expected_update, str(target or "")


def _restore(client, app_id, module_id, original_props, original_comment):
    for attempt in range(3):
        try:
            client.set_custom_properties(app_id, original_props)
            client.edit_module_attr(app_id, module_id, "comment", original_comment)
            _cut_build(client, app_id, "Maestro restore original settings", release=True)
            return
        except Exception as exc:  # noqa: BLE001 - best-effort, never mask the real result
            if attempt == 2:
                print(f"::warning::Targeted Updates App restore failed after 3 attempts, fix by hand on HQ: {exc}")
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", dest="scenarios", choices=sorted(FLOWS))
    parser.add_argument("--devices", default="Samsung Galaxy S26-16.0")
    parser.add_argument("--release-tag", default=None)
    parser.add_argument("--apk", default=None)
    parser.add_argument("--build-name", default="QA-COMMCARE-MOBILE-NONCORE-targeted-updates")
    args = parser.parse_args()
    load_dotenv(REPO_ROOT / ".env")

    domain, app_id = APP_REGISTRY["TARGETED_UPDATES"][:2]
    client = hq_client_module.HQClient(domain=domain).login(
        username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"),
    )
    source = client.session.get(client._apps_url(f"source/{app_id}/")).json()
    module_id = source["modules"][0]["unique_id"]
    original_props = dict((source.get("profile") or {}).get("custom_properties") or {})
    original_comment = source["modules"][0].get("comment") or ""
    print(f"Original custom properties: {original_props}")

    failures = 0
    try:
        for scenario in args.scenarios or sorted(FLOWS):
            code, expected_update, expected_version = _prepare(client, app_id, module_id, scenario)
            cmd = [sys.executable, str(REPO_ROOT / "scripts" / "run_suite.py"), "--flows-root", "flows_non_core",
                   "--flow", FLOWS[scenario], "--devices", args.devices,
                   "--build-name", f"{args.build_name}-{scenario}",
                   "--env", f"APP_CODE_TARGETED_BASE={code}",
                   "--env", f"EXPECTED_UPDATE={expected_update}",
                   "--env", f"EXPECTED_VERSION={expected_version}"]
            if args.apk:
                cmd += ["--apk", args.apk]
            elif args.release_tag:
                cmd += ["--release-tag", args.release_tag]
            rc = subprocess.call(cmd, cwd=REPO_ROOT)
            failures += rc != 0
    finally:
        _restore(client, app_id, module_id, original_props, original_comment)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
