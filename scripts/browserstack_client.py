"""
Thin wrapper around BrowserStack's App Automate REST API for Maestro
(https://www.browserstack.com/docs/app-automate/api-reference/maestro/*).

Endpoints used here (verified via BrowserStack's published docs, 2026-08-04):
    POST https://api-cloud.browserstack.com/app-automate/maestro/v2/app          (upload apk)
    POST https://api-cloud.browserstack.com/app-automate/maestro/v2/test-suite  (upload flows.zip)
    POST https://api-cloud.browserstack.com/app-automate/maestro/v2/android/build (trigger run)
    GET  https://api-cloud.browserstack.com/app-automate/maestro/v2/builds/<build_id> (poll status)

Auth is HTTP Basic with your BrowserStack username + access key (the same
BROWSERSTACK_USERNAME/BROWSERSTACK_PASSWORD pair commcare-android's Espresso CI job
already uses - BrowserStack's "Access Key" is that same value).
"""
import os
import time

import requests

API_BASE = "https://api-cloud.browserstack.com/app-automate/maestro/v2"


class BrowserStackClient:
    def __init__(self, username=None, access_key=None):
        self.username = username or os.environ["BROWSERSTACK_USERNAME"]
        self.access_key = access_key or os.environ["BROWSERSTACK_ACCESS_KEY"]
        self.auth = (self.username, self.access_key)

    def upload_app(self, apk_path, custom_id=None):
        with open(apk_path, "rb") as f:
            files = {"file": f}
            data = {"custom_id": custom_id} if custom_id else {}
            resp = requests.post(f"{API_BASE}/app", auth=self.auth, files=files, data=data)
        resp.raise_for_status()
        return resp.json()  # {"app_url": "bs://...", ...}

    def upload_test_suite(self, zip_path, custom_id=None):
        with open(zip_path, "rb") as f:
            files = {"file": f}
            data = {"custom_id": custom_id} if custom_id else {}
            resp = requests.post(f"{API_BASE}/test-suite", auth=self.auth, files=files, data=data)
        resp.raise_for_status()
        return resp.json()  # {"test_suite_url": "bs://...", ...}

    def trigger_build(self, app_url, test_suite_url, devices, project="CommCare QA",
                       build_name=None, execute=None, env_variables=None,
                       other_apps=None, extra_params=None):
        payload = {
            "app": app_url,
            "testSuite": test_suite_url,
            "devices": devices,
            "project": project,
        }
        if build_name:
            payload["build"] = build_name
        if other_apps:
            # Pre-installs companion APKs (e.g. ExternalApp Tests' Mobile API
            # Testing App) alongside `app` at session start - max 3 per
            # BrowserStack's docs. Maestro flows can then `launchApp` a
            # different appId mid-flow to switch to one of these.
            payload["otherApps"] = other_apps
        if execute:
            # Paths relative to the zip's single root folder. Needed because
            # BrowserStack only auto-discovers flows at that root by default -
            # anything in a subdirectory (which is all of ours, since flows
            # are organized flows/<workflow>/*.yaml) must be listed explicitly.
            payload["execute"] = execute
        if env_variables:
            # BrowserStack substitutes ${VAR} in flow YAML from this object -
            # a flow referencing a var not present here resolves to NULL, not
            # a local shell/​os.environ value (this isn't a local `maestro test` run).
            payload["setEnvVariables"] = env_variables
        if extra_params:
            payload.update(extra_params)
        resp = requests.post(f"{API_BASE}/android/build", auth=self.auth, json=payload)
        resp.raise_for_status()
        return resp.json()  # includes build id

    def get_build(self, build_id):
        resp = requests.get(f"{API_BASE}/builds/{build_id}", auth=self.auth)
        resp.raise_for_status()
        return resp.json()

    def wait_for_build(self, build_id, poll_seconds=30, timeout_seconds=1800):
        """Poll until the build leaves the 'running'/'queued' state or times out."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            build = self.get_build(build_id)
            status = build.get("status")
            if status not in ("running", "queued"):
                return build
            if time.monotonic() > deadline:
                raise TimeoutError(f"BrowserStack build {build_id} still '{status}' after {timeout_seconds}s")
            time.sleep(poll_seconds)


def zip_flows(flow_dir, out_path):
    """Zip a directory of Maestro .yaml flows for upload as a test-suite."""
    import zipfile
    import pathlib

    flow_dir = pathlib.Path(flow_dir)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in flow_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(flow_dir.parent))
    return out_path
