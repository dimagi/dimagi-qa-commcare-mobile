"""
Thin wrapper around BrowserStack's App Automate REST API for Maestro
(https://www.browserstack.com/docs/app-automate/api-reference/maestro/*).

Endpoints used here (verified via BrowserStack's published docs, 2026-08-04):
    POST https://api-cloud.browserstack.com/app-automate/maestro/v2/app          (upload apk)
    POST https://api-cloud.browserstack.com/app-automate/maestro/v2/test-suite  (upload flows.zip)
    POST https://api-cloud.browserstack.com/app-automate/maestro/v2/android/build (trigger run)
    GET  https://api-cloud.browserstack.com/app-automate/maestro/v2/builds/<build_id> (poll status)
    GET  https://api-cloud.browserstack.com/app-automate/maestro/v2/builds/<build_id>/sessions/<session_id>
         (per-test names/statuses/artifact URLs - NOT included in the builds/<id> response above,
         which only has aggregate counts per session)

Auth is HTTP Basic with your BrowserStack username + access key (the same
BROWSERSTACK_USERNAME/BROWSERSTACK_PASSWORD pair commcare-android's Espresso CI job
already uses - BrowserStack's "Access Key" is that same value).
"""
import os
import time

import requests

API_BASE = "https://api-cloud.browserstack.com/app-automate/maestro/v2"

# UPDATE (2026-08-24), confirmed live in CI (run 32702204540, group-c): a
# genuine transient "504 Server Error: GATEWAY_TIMEOUT" from BrowserStack's
# own /android/build endpoint hit trigger_build()'s bare
# resp.raise_for_status(), with no retry - the resulting unhandled
# HTTPError crashed run_suite.py's whole process mid-dispatch, losing every
# already-passing result from that run (nothing had been flushed to
# reports/latest_results.json yet). Every method below now retries through
# transient failures (5xx responses, connection resets, timeouts) a few
# times with backoff before giving up - the exact same class of "real
# server-side hiccup, worth absorbing centrally" reasoning already applied
# to flows/common/login.yaml's Bad Server Response retries, just at the
# HTTP-client layer instead of the on-device UI layer.
_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


def _request_with_retry(method, url, attempts=4, backoff_seconds=5, file_path=None, file_field="file", **kwargs):
    """`file_path`/`file_field`: for file-upload calls, opens the file FRESH
    on every attempt (rather than accepting an already-opened handle in
    kwargs) - a retried request reusing the same handle from a prior
    attempt would send an already-fully-read/empty body, a silent-corruption
    bug worse than not retrying at all."""
    last_exc = None
    for attempt in range(attempts):
        try:
            if file_path:
                with open(file_path, "rb") as f:
                    resp = requests.request(method, url, files={file_field: f}, **kwargs)
            else:
                resp = requests.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
        else:
            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                resp.raise_for_status()
                return resp
            last_exc = requests.exceptions.HTTPError(
                f"{resp.status_code} Server Error (retryable) for url: {url}", response=resp
            )
        if attempt < attempts - 1:
            time.sleep(backoff_seconds)
    raise last_exc


class BrowserStackClient:
    def __init__(self, username=None, access_key=None):
        self.username = username or os.environ["BROWSERSTACK_USERNAME"]
        self.access_key = access_key or os.environ["BROWSERSTACK_ACCESS_KEY"]
        self.auth = (self.username, self.access_key)

    def upload_app(self, apk_path, custom_id=None):
        data = {"custom_id": custom_id} if custom_id else {}
        resp = _request_with_retry("post", f"{API_BASE}/app", auth=self.auth, data=data, file_path=apk_path)
        return resp.json()  # {"app_url": "bs://...", ...}

    def upload_test_suite(self, zip_path, custom_id=None):
        data = {"custom_id": custom_id} if custom_id else {}
        resp = _request_with_retry("post", f"{API_BASE}/test-suite", auth=self.auth, data=data, file_path=zip_path)
        return resp.json()  # {"test_suite_url": "bs://...", ...}

    def trigger_build(self, app_url, test_suite_url, devices, project="QA COMMCARE MOBILE TESTS",
                       build_name=None, execute=None, env_variables=None,
                       other_apps=None, extra_params=None):
        payload = {
            "app": app_url,
            "testSuite": test_suite_url,
            "devices": devices,
            "project": project,
        }
        if build_name:
            # BrowserStack's real key is `customBuildName`, not `build` - the
            # latter is silently ignored (no error, name just never shows up
            # in the dashboard). Confirmed against BrowserStack's own Maestro
            # build API reference.
            payload["customBuildName"] = build_name
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
        resp = _request_with_retry("post", f"{API_BASE}/android/build", auth=self.auth, json=payload)
        return resp.json()  # includes build id

    def get_build(self, build_id):
        resp = _request_with_retry("get", f"{API_BASE}/builds/{build_id}", auth=self.auth)
        return resp.json()

    def get_session(self, build_id, session_id):
        """GET .../builds/<id> only carries aggregate per-session counts
        (passed/failed/skipped numbers) - the individual test names/statuses
        (report_generator needs these) live in this separate per-session
        endpoint. Confirmed live: the build-level response's session objects
        have no `testcases.data`, only `testcases.count`/`testcases.status`."""
        resp = _request_with_retry("get", f"{API_BASE}/builds/{build_id}/sessions/{session_id}", auth=self.auth)
        return resp.json()

    def wait_for_build(self, build_id, poll_seconds=30, timeout_seconds=5400):
        """Poll until the build leaves the 'running'/'queued' state or times out.

        5400s (90min) headroom: a single build can hold dozens of flows
        (BrowserStack's own test-suite chunking only splits by the execute
        array's serialized length, not by flow count/runtime) executing
        sequentially on one real device - confirmed live when a 33-flow
        mobile_pins build was still genuinely 'running' (not stuck) after the
        previous 1800s default elapsed."""
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
