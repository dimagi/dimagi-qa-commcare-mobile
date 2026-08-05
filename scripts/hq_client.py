"""
CommCareHQ session client for the pre-steps that Maestro flows can't do on-device:
marking a build Released/In Test, creating a new build, setting custom properties,
and configuring the "Manage Update Settings" prompt behavior.

PROVENANCE - every endpoint below was read directly out of the dimagi/commcare-hq
source (not guessed), specifically:
    corehq/apps/app_manager/views/releases.py  -> release_build(), save_copy()
    corehq/apps/app_manager/views/settings.py  -> edit_commcare_profile(), PromptSettingsUpdateView
    corehq/apps/app_manager/urls.py            -> url names/paths for all of the above
    corehq/apps/app_manager/forms.py + const.py -> PromptUpdateSettingsForm field choices
All of these require a logged-in Django session with edit-apps permission on the
domain (there is no token-based REST API for release management) - the same kind of
account used to browse CommCareHQ in a normal desktop browser.

CAVEAT ON LOGIN - HQLoginView is a django-two-factor-auth wizard (multi-step form,
not a plain username/password POST). `login()` below handles this generically by
echoing back whatever hidden fields the login page actually renders (so it doesn't
need to hardcode the wizard's step-prefix), but this has NOT been exercised against
a live HQ session in this environment. Verify it against your actual QA domain
before relying on it, and if SSO is enforced or the wizard shape differs, use the
HQ_SESSION_COOKIE escape hatch instead (see login() docstring).
"""
import json
import os
import re
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

    parser = argparse.ArgumentParser(description="Run a JSON-declared HQ pre-step file")
    parser.add_argument("spec_file", help="Path to an hq_setup/*.json file")
    parser.add_argument("--domain", default=os.environ.get("HQ_DOMAIN", "qateam"))
    args = parser.parse_args()

    with open(args.spec_file) as f:
        spec = json.load(f)
    client = HQClient(domain=args.domain).login()
    out = run_pre_step(spec, client=client)
    print(json.dumps(out, indent=2, default=str))
