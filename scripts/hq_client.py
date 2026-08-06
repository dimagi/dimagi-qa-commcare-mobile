"""
CommCareHQ session client for the pre-steps that Maestro flows can't do on-device:
marking a build Released/In Test, creating a new build, setting custom properties,
and configuring the "Manage Update Settings" prompt behavior.

PROVENANCE - every endpoint below was read directly out of the dimagi/commcare-hq
source (not guessed), specifically:
    corehq/apps/app_manager/views/releases.py  -> release_build(), save_copy(), paginate_releases()
    corehq/apps/app_manager/views/settings.py  -> edit_commcare_profile(), PromptSettingsUpdateView
    corehq/apps/app_manager/views/download.py  -> DownloadCCZ (extends DownloadMultimediaZip)
    corehq/apps/app_manager/urls.py            -> url names/paths for all of the above
    corehq/apps/app_manager/forms.py + const.py -> PromptUpdateSettingsForm field choices
    corehq/apps/app_manager/models/applications.py -> SavedAppBuild.releases_list_json()
        (confirms each release's own build id comes back as plain `id`, the same value
        used as `saved_app_id` elsewhere in this file)
The local commcare-mobile/commcare-hq checkout had no working tree (git status showed
everything staged as deleted) when this was written, so these were fetched directly
from https://github.com/dimagi/commcare-hq/blob/master/... instead - re-verify if HQ's
release-listing/download views ever change shape.
All of these require a logged-in Django session with edit-apps permission on the
domain (there is no token-based REST API for release management) - the same kind of
account used to browse CommCareHQ in a normal desktop browser.

CAVEAT ON LOGIN - HQLoginView is a django-two-factor-auth wizard (multi-step form,
not a plain username/password POST). `login()` below handles this generically by
echoing back whatever hidden fields the login page actually renders (so it doesn't
need to hardcode the wizard's step-prefix). Confirmed working live against the
qateam domain with HQ_WEB_USER_EMAIL/HQ_WEB_USER_PASSWORD (2026-08-06, no 2FA
prompt encountered for that account) - if SSO is enforced or the wizard shape
differs for a different account, use the HQ_SESSION_COOKIE escape hatch instead
(see login() docstring).
"""
import json
import os
import re
import time
import requests

DEFAULT_BASE_URL = os.environ.get("HQ_BASE_URL", "https://www.commcarehq.org")
LATEST_APK_VALUE = "latest"  # corehq/apps/app_manager/const.py
LATEST_APP_VALUE = 0  # corehq/apps/app_manager/const.py

_HIDDEN_INPUT_RE = re.compile(
    r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


class HQClient:
    def __init__(self, base_url=None, domain=None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.domain = domain or os.environ.get("HQ_DOMAIN", "qateam")
        self.session = requests.Session()
        self.session.headers["Referer"] = self.base_url

    # ------------------------------------------------------------------ auth
    def login(self, username=None, password=None):
        """
        Log in with either a pre-captured session cookie (recommended, since the
        two-factor login wizard is fragile to script blind) or username/password.

        Cookie escape hatch: set HQ_SESSION_COOKIE to the value of the `sessionid`
        cookie from a real logged-in browser session (DevTools > Application >
        Cookies on commcarehq.org). This skips the form-login dance entirely.
        """
        cookie = os.environ.get("HQ_SESSION_COOKIE")
        if cookie:
            self.session.cookies.set("sessionid", cookie, domain=_domain_of(self.base_url))
            return self

        username = username or os.environ.get("HQ_API_USERNAME")
        password = password or os.environ.get("HQ_API_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "No HQ_SESSION_COOKIE and no HQ_API_USERNAME/HQ_API_PASSWORD set."
            )

        login_url = f"{self.base_url}/accounts/login/"
        page = self.session.get(login_url)
        page.raise_for_status()

        fields = dict(_HIDDEN_INPUT_RE.findall(page.text))
        fields["auth-username"] = username
        fields["auth-password"] = password

        resp = self.session.post(login_url, data=fields, headers=self._csrf_headers(), allow_redirects=True)
        resp.raise_for_status()
        if "login" in resp.url and "auth-password" in resp.text:
            raise RuntimeError(
                "HQ login did not redirect away from the login page - credentials, "
                "SSO enforcement, or the two-factor wizard's field names likely need "
                "adjustment. Consider the HQ_SESSION_COOKIE escape hatch instead."
            )
        return self

    def _csrf_headers(self):
        token = self.session.cookies.get("csrftoken")
        return {"X-CSRFToken": token} if token else {}

    # ------------------------------------------------------------- app actions
    def _apps_url(self, path):
        return f"{self.base_url}/a/{self.domain}/apps/{path}"

    def mark_build_status(self, app_id, saved_app_id, is_released):
        """
        Release or un-release ("In Test") a specific saved build.
        POST /a/<domain>/apps/view/<app_id>/releases/release/<saved_app_id>/
        Source: corehq/apps/app_manager/views/releases.py:release_build()
        """
        url = self._apps_url(f"view/{app_id}/releases/release/{saved_app_id}/")
        resp = self.session.post(
            url,
            data={"is_released": "true" if is_released else "false", "ajax": "true"},
            headers=self._csrf_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"mark_build_status failed: {data['error']}")
        return data

    def create_new_build(self, app_id, comment=""):
        """
        Create a new build ("Make New Version") of the app.
        POST /a/<domain>/apps/save/<app_id>/
        Source: corehq/apps/app_manager/views/releases.py:save_copy()
        Returns the response JSON, which includes the new saved_app under "saved_app".
        """
        url = self._apps_url(f"save/{app_id}/")
        resp = self.session.post(url, data={"comment": comment}, headers=self._csrf_headers())
        resp.raise_for_status()
        data = resp.json()
        if data.get("error_html"):
            raise RuntimeError(f"create_new_build failed: {data['error_html']}")
        return data

    def set_custom_properties(self, app_id, properties: dict):
        """
        Set the app's Advanced Settings > Custom Properties (e.g. the
        num-views-before-reducing-frequency / logenabled keys used by several
        Prompted Updates and Trigger Device Logs test cases).
        POST /a/<domain>/apps/edit_commcare_profile/<app_id>/
        Body is JSON (not form-encoded) - see edit_commcare_profile() source.
        Note: this REPLACES the full custom_properties dict, it doesn't merge.
        Source: corehq/apps/app_manager/views/settings.py:edit_commcare_profile()
        """
        url = self._apps_url(f"edit_commcare_profile/{app_id}/")
        headers = self._csrf_headers()
        headers["Content-Type"] = "application/json"
        resp = self.session.post(
            url, data=json.dumps({"custom_properties": properties}), headers=headers
        )
        resp.raise_for_status()
        return resp.json()

    def list_releases(self, app_id, only_show_released=True, limit=5):
        """
        List an app's builds, newest first, each as a dict including at least
        `id` (the build's own doc id, usable as saved_app_id/DownloadCCZ's
        app_id), `version`, and `is_released`.
        GET /a/<domain>/apps/view/<app_id>/releases/json/?limit=&only_show_released=&page=1
        Source: corehq/apps/app_manager/views/releases.py:paginate_releases()
        """
        url = self._apps_url(f"view/{app_id}/releases/json/")
        resp = self.session.get(url, params={
            "limit": limit,
            "only_show_released": "true" if only_show_released else "false",
            "page": 1,
        })
        resp.raise_for_status()
        return resp.json()["apps"]

    def download_ccz(self, build_id, dest_path, poll_seconds=3, timeout_seconds=180):
        """
        Download a specific build's CCZ (NOT the master app_id - `build_id`
        is a release's own id, e.g. from list_releases()'s `id` field or
        create_new_build()'s saved_app.id).

        GET /a/<domain>/apps/download/<build_id>/CommCare.ccz does NOT stream
        the file - confirmed live (2026-08-06), it always kicks off an async
        soil/celery job (build_application_zip) and returns
        {"download_id", "download_url", ...} even when a cached copy already
        exists. `download_url` is a status-poll endpoint (soil's
        ajax_job_poll) that renders an HTML fragment; once the job's done,
        that fragment contains a "Download File Now" link
        (/downloads/temp/<download_id>?get_file) which is the only URL that
        actually streams bytes. Source: corehq/apps/app_manager/views/
        download.py:DownloadCCZ + corehq/ex-submodules/soil/{__init__,views,util}.py.
        """
        trigger_url = self._apps_url(f"download/{build_id}/CommCare.ccz")
        resp = self.session.get(trigger_url)
        resp.raise_for_status()
        poll_url = self.base_url + resp.json()["download_url"]

        deadline = time.monotonic() + timeout_seconds
        file_url = None
        while True:
            poll_resp = self.session.get(poll_url)
            poll_resp.raise_for_status()
            match = re.search(r'href="([^"]*\?get_file[^"]*)"', poll_resp.text)
            if match:
                file_url = match.group(1)
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"CCZ for build {build_id} not ready after {timeout_seconds}s")
            time.sleep(poll_seconds)

        if not file_url.startswith("http"):
            file_url = self.base_url + file_url
        with self.session.get(file_url, stream=True) as file_resp:
            file_resp.raise_for_status()
            if os.path.isdir(dest_path):
                # DownloadCCZ's own filename ("{domain} - {app name} -
                # v{version}.ccz", from its Content-Disposition header) beats
                # any name we'd invent - matches this repo's existing
                # resources/*.ccz naming convention and stays correct as new
                # versions get released.
                filename = _filename_from_content_disposition(file_resp.headers.get("Content-Disposition"))
                dest_path = os.path.join(dest_path, filename or f"{build_id}.ccz")
            with open(dest_path, "wb") as f:
                for chunk in file_resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        return dest_path

    def download_latest_ccz(self, app_id, dest_path, released_only=True):
        """Convenience wrapper: list_releases() + download_ccz() for whatever
        is currently the newest (optionally released-only) build."""
        releases = self.list_releases(app_id, only_show_released=released_only, limit=1)
        if not releases:
            raise RuntimeError(
                f"No {'released ' if released_only else ''}builds found for app {app_id}"
            )
        return self.download_ccz(releases[0]["id"], dest_path)

    def set_prompt_update_settings(self, app_id, app_prompt="on", apk_prompt="on",
                                    apk_version=LATEST_APK_VALUE, app_version=LATEST_APP_VALUE):
        """
        Configure the "Manage Update Settings" tab (Prompt Updates to App/CommCare
        = off/on/forced, and which build version to prompt towards).
        POST /a/<domain>/apps/update_prompts/<app_id>/
        Source: corehq/apps/app_manager/views/settings.py:PromptSettingsUpdateView
                corehq/apps/app_manager/forms.py:PromptUpdateSettingsForm
        app_prompt/apk_prompt must be one of "off", "on", "forced".
        """
        assert app_prompt in ("off", "on", "forced")
        assert apk_prompt in ("off", "on", "forced")
        url = self._apps_url(f"update_prompts/{app_id}/")
        resp = self.session.post(
            url,
            data={
                "app_prompt": app_prompt,
                "apk_prompt": apk_prompt,
                "apk_version": apk_version,
                "app_version": app_version,
            },
            headers=self._csrf_headers(),
        )
        resp.raise_for_status()
        return resp


def _domain_of(url):
    return re.sub(r"^https?://", "", url).split("/")[0]


def _filename_from_content_disposition(header):
    if not header:
        return None
    match = re.search(r'filename="([^"]+)"', header)
    return match.group(1) if match else None


def run_pre_step(spec: dict, client: HQClient = None):
    """
    Execute a declarative list of HQ actions, e.g. loaded from an hq_setup/*.json
    companion file for a Maestro flow:
        {"actions": [
            {"type": "mark_build_status", "app_id": "...", "saved_app_id": "...", "is_released": true},
            {"type": "create_new_build", "app_id": "...", "comment": "auto QA build"}
        ]}
    """
    client = client or HQClient().login()
    dispatch = {
        "mark_build_status": client.mark_build_status,
        "create_new_build": client.create_new_build,
        "set_custom_properties": client.set_custom_properties,
        "set_prompt_update_settings": client.set_prompt_update_settings,
    }
    results = []
    last_build_id = None
    for action in spec.get("actions", []):
        action = dict(action)
        action_type = action.pop("type")
        if action_type not in dispatch:
            raise ValueError(f"Unknown HQ pre-step action type: {action_type}")
        if action.get("saved_app_id") == "$LAST_BUILD_ID":
            if not last_build_id:
                raise ValueError("$LAST_BUILD_ID used but no prior create_new_build result available")
            action["saved_app_id"] = last_build_id
        result = dispatch[action_type](**action)
        if action_type == "create_new_build":
            # NOTE: field name assumed from releases.py:save_copy()'s
            # `{"saved_app": copy_json}` response shape - not exercised against
            # a live response in this environment, verify the key on first use.
            last_build_id = (result.get("saved_app") or {}).get("id")
        results.append(result)
    return results


if __name__ == "__main__":
    import argparse
    import pathlib
    from dotenv import load_dotenv

    load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

    parser = argparse.ArgumentParser(description="Run a JSON-declared HQ pre-step file, or download a CCZ.")
    parser.add_argument("spec_file", nargs="?", help="Path to an hq_setup/*.json file")
    parser.add_argument("--domain", default=os.environ.get("HQ_DOMAIN", "qateam"))
    parser.add_argument("--download-ccz", metavar="APP_ID",
                         help="Download an app's newest build as a CCZ instead of running a pre-step "
                              "(mutually exclusive with spec_file). APP_ID is the master app's id, e.g. "
                              "from its HQ 'Releases' page URL - .../apps/view/<APP_ID>/releases/.")
    parser.add_argument("--out", help="Destination path for --download-ccz.")
    parser.add_argument("--include-unreleased", action="store_true",
                         help="With --download-ccz, allow the newest build even if not yet Released "
                              "(default: released builds only, matching what a device would actually "
                              "auto-update to).")
    parser.add_argument("--web-user", action="store_true",
                         help="Log in with HQ_WEB_USER_EMAIL/HQ_WEB_USER_PASSWORD instead of "
                              "HQ_API_USERNAME/HQ_API_PASSWORD.")
    args = parser.parse_args()

    if args.download_ccz and args.spec_file:
        parser.error("--download-ccz and spec_file are mutually exclusive")
    if args.download_ccz and not args.out:
        parser.error("--download-ccz requires --out")

    if args.web_user:
        username = os.environ.get("HQ_WEB_USER_EMAIL")
        password = os.environ.get("HQ_WEB_USER_PASSWORD")
        client = HQClient(domain=args.domain).login(username=username, password=password)
    else:
        client = HQClient(domain=args.domain).login()

    if args.download_ccz:
        path = client.download_latest_ccz(args.download_ccz, args.out, released_only=not args.include_unreleased)
        print(f"Downloaded to {path}")
    else:
        if not args.spec_file:
            parser.error("spec_file is required unless --download-ccz is given")
        with open(args.spec_file) as f:
            spec = json.load(f)
        out = run_pre_step(spec, client=client)
        print(json.dumps(out, indent=2, default=str))
