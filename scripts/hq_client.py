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
import html
import json
import os
import re
import time
import requests

DEFAULT_BASE_URL = os.environ.get("HQ_BASE_URL", "https://www.commcarehq.org")
LATEST_APK_VALUE = "latest"  # corehq/apps/app_manager/const.py
LATEST_APP_VALUE = 0  # corehq/apps/app_manager/const.py
# Sentinel for set_prompt_update_settings()'s apk_version param - resolved via
# find_dev_apk_version() at call time instead of a literal version string, so
# callers never hardcode a specific pre-release build (e.g. "2.65.0/latest")
# that goes stale the moment a newer dev/alpha build ships. See
# find_dev_apk_version()'s own docstring for the full citation.
DEV_APK_VALUE = "AUTO_DEV_BUILD"

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
            # UPDATE (2026-08-24), confirmed live: the username/password path
            # below primes a real csrftoken cookie as a side effect of its
            # own GET to /accounts/login/ before ever POSTing anything, but
            # this cookie escape hatch skipped straight to returning with
            # only `sessionid` set - the first POST any caller made right
            # after (e.g. set_prompt_update_settings) got a spurious 403
            # (missing/stale CSRF cookie), not a real permission error. A
            # throwaway GET here closes that gap the same way the normal
            # path already does.
            self.session.get(self.base_url)
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

    def _reports_url(self, path):
        return f"{self.base_url}/a/{self.domain}/reports/{path}"

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
        `_id` (the build's own doc id, usable as saved_app_id/DownloadCCZ's
        app_id - confirmed live 2026-08-20 the real key is `_id`, not `id`),
        `version`, and `is_released`.
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

    def get_app_install_code(self, app_id, saved_app_id=None, release_first=True, max_commcare_version=None,
                              include_media=True):
        """
        Return the short alphanumeric "Enter app code on installation screen"
        code shown in HQ's Releases page "Download to Android > Online
        Install" panel (the same code flows/common/install_app_by_code.yaml's
        APP_CODE param expects) - e.g. "455h9iR" for "[Master] Basic Tests".

        If `saved_app_id` isn't given, uses the app's single most recent
        build (whether or not it's released), matching what the Releases
        page's top accordion (the "Publish" button's target) shows - UNLESS
        `max_commcare_version` is given, in which case the newest build
        whose own `build_spec.version` requirement is <= it is used instead.

        `max_commcare_version` matters because a build's minimum-CommCare-
        version requirement is enforced HARD by the online-install ("Enter
        Code") path: confirmed live (2026-08-08, date_widgets validation
        runs 31247897876/31248486050/31249076393) that dismissing the
        resulting "The application requires CommCare version X..." dialog
        ("I'LL UPDATE LATER") does NOT let the install proceed - it loops
        back to the same dialog no matter how many times Start Install is
        retapped. (install_app_as_web_user.yaml's CCZ-based install doesn't
        hit this same hard block, which is why this never surfaced before -
        confirmed on Date Widgets: top build v40 requires CommCare 2.64.0,
        which doesn't exist as a GitHub release yet, while v38 only requires
        2.62.0.) Callers should pass the actual CommCare APK version under
        test (e.g. from download_apk.resolve()'s release tag).

        `release_first=True` (default) marks that build Released before
        generating the code, matching the "click Publish where the release
        toggle is on for the existing top build" action confirmed live
        against HQ's own UI/DevTools (2026-08-08) - NOT because the endpoint
        below technically requires it (confirmed live it also works against
        an unreleased build), but because that's the deliberate action this
        was asked to automate, and a released build is what a real "Online
        Install" user is expected to land on.

        `include_media=True` (default) requests the "short_odk_media_url"
        code rather than the app-only "short_odk_url" one - confirmed live
        (2026-08-08) that the no-media code installs an app fine but then
        gets permanently stuck on CommCareVerificationActivity's "Some of
        your application's multimedia has not been installed... press
        retry" screen (id/screen_multimedia_retry) whenever the app has real
        media references (seen on "Performance Testing"/menu_badges) - NOT
        a timing issue, retrying never helps, and it reproduces identically
        across multiple different builds of the same app. Requesting media
        for an app that has none should be a safe no-op (there's simply
        nothing to fetch), so True is the default rather than something
        callers must remember to opt into per app - re-verify against a
        media-less app (e.g. Date Widgets) if this default is ever
        suspected of causing a regression.

        Source: static/webpack/app_manager/app_manager.bundle.js (minified,
        no non-minified source available locally) - the release-manager
        Knockout view model's `base_url`/`generate_short_url`/
        `parse_bitly_url`/`get_odk_url_type` functions:
            n.base_url = () => "/a/" + domain + "/apps/odk/" + id + "/"
            n.generate_short_url = (type) => ajax({url: base_url()+type+"/?profile="+build_profile})
            n.parse_bitly_url = (url) => last path segment of url (word chars only)
            n.get_odk_url_type = () => include_media() ? "short_odk_media_url" : "short_odk_url"
        where `id` is the BUILD's own doc id (saved_app_id) - confirmed
        live: GET /a/<domain>/apps/odk/<saved_app_id>/<url_type>/?profile=
        returns a bare bit.ly URL body (e.g. "https://bit.ly/3U0MUXW"); the
        code is that URL's last path segment.
        """
        if saved_app_id is None:
            if max_commcare_version is None:
                releases = self.list_releases(app_id, only_show_released=False, limit=1)
                if not releases:
                    raise RuntimeError(f"App {app_id} has no builds at all - nothing to release/code.")
                chosen = releases[0]
            else:
                releases = self.list_releases(app_id, only_show_released=False, limit=20)
                ceiling = _version_tuple(max_commcare_version)
                chosen = next(
                    (r for r in releases if _version_tuple(r["build_spec"]["version"]) <= ceiling),
                    None,
                )
                if chosen is None:
                    raise RuntimeError(
                        f"App {app_id} has no build (of its {len(releases)} most recent) requiring "
                        f"CommCare <= {max_commcare_version} - every candidate needs a newer APK "
                        f"than the one under test."
                    )
            saved_app_id = chosen["id"]
            already_released = chosen["is_released"]
        else:
            already_released = None

        if release_first and not already_released:
            self.mark_build_status(app_id, saved_app_id, is_released=True)

        url_type = "short_odk_media_url" if include_media else "short_odk_url"
        url = self._apps_url(f"odk/{saved_app_id}/{url_type}/")
        resp = self.session.get(url, params={"profile": ""})
        resp.raise_for_status()
        match = re.match(r"^https?://.*/(\w+)/?$", resp.text.strip())
        if not match:
            raise RuntimeError(f"Could not parse an app code out of {url_type} response: {resp.text!r}")
        return match.group(1)

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
        POST /a/<domain>/apps/view/<app_id>/update_prompts/
        Source: corehq/apps/app_manager/views/settings.py:PromptSettingsUpdateView
                corehq/apps/app_manager/forms.py:PromptUpdateSettingsForm

        UPDATE (2026-08-20), confirmed live (first real dispatch of this
        method - every prior hq_setup/prompted_updates/*.json call site had
        an unfilled placeholder app_id until today, so this was never
        actually exercised before): the original URL here
        ("update_prompts/<app_id>/") 404'd. Checked commcare-hq's real
        corehq/apps/app_manager/urls.py directly - `update_prompts/$` is
        registered INSIDE `app_urls`, and that whole list is included via
        `url(r'^view/(?P<app_id>[\\w-]+)/', include(app_urls))` - the exact
        same `view/<app_id>/` prefix mark_build_status's own (already
        correct, already confirmed working) URL uses for `releases/release/
        <saved_app_id>/`, which lives in that same app_urls list. Fixed to
        match. (create_new_build's `save/<app_id>/` and
        set_custom_properties's `edit_commcare_profile/<app_id>/` are
        registered separately, NOT inside app_urls - confirmed those two
        did not need this same fix.)
        app_prompt/apk_prompt must be one of "off", "on", "forced".

        UPDATE (2026-08-24): apk_version accepts the DEV_APK_VALUE sentinel
        ("AUTO_DEV_BUILD") in place of a literal version string - resolved via
        find_dev_apk_version() right before POSTing, so callers never hardcode
        a specific pre-release build number that goes stale next release.
        """
        assert app_prompt in ("off", "on", "forced")
        assert apk_prompt in ("off", "on", "forced")
        if apk_version == DEV_APK_VALUE:
            apk_version = self.find_dev_apk_version(app_id)
        url = self._apps_url(f"view/{app_id}/update_prompts/")
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

        # UPDATE (2026-08-24), per direct user instruction: verify the save
        # actually took before any on-device Maestro retry loop burns its
        # budget assuming a propagation delay - a 200 here only means the
        # form POST didn't error, not that HQ saved the values we expect
        # (e.g. PromptUpdateSettingsForm could silently reject/coerce a
        # value). Read the same page find_dev_apk_version() already reads
        # and compare its rendered `selected` option against what we just
        # asked for; raise loudly and immediately if they don't match,
        # rather than let a real misconfiguration masquerade as "device just
        # hasn't caught up yet" across 4+ minutes of on-device retries.
        actual = self.get_prompt_update_settings(app_id)
        expected = {"apk_prompt": apk_prompt, "apk_version": str(apk_version), "app_prompt": app_prompt}
        mismatches = {
            k: {"expected": v, "actual": actual.get(k)}
            for k, v in expected.items() if str(actual.get(k)) != str(v)
        }
        if mismatches:
            raise RuntimeError(
                f"set_prompt_update_settings for app {app_id} did not take effect as HQ now "
                f"reports it: {mismatches} - HQ may have rejected/coerced part of the POST "
                f"silently. Fix the HQ config before retrying the on-device flow, since no "
                f"amount of login/logout retrying will help a genuinely wrong setting."
            )
        return resp

    def create_device_log_request(self, domain, username):
        """
        Master Mobile Plan (2026) > Trigger device logs > "Device Logs 2" -
        "Device Log info": a plain Django-admin "add" form, not an app
        action, so it doesn't take an app_id.
        GET/POST /admin/ota/devicelogrequest/add/
        Standard django.contrib.admin add view - unlike the /a/<domain>/apps/
        endpoints above (which accept the session's csrftoken cookie as a
        bare X-CSRFToken header), django.contrib.admin's CsrfViewMiddleware
        needs the page's own embedded csrfmiddlewaretoken form field plus a
        same-origin Referer, confirmed live 2026-08-27 (a bare header-only
        POST was never tried against this endpoint - this is the working
        shape, found by reading the real add-form's fields first rather than
        guessing). Success is a 302 redirect to the changelist
        (/admin/ota/devicelogrequest/); the admin re-renders the same form
        (200) on validation failure. DeviceLogRequest enforces a unique-
        together(domain, username) constraint - confirmed live 2026-08-27,
        re-submitting the same pair re-renders the form with errorlist text
        "Device log request with this Domain and Username already exists."
        The test case's actual intent is "a request is pending for this
        domain/user", which already holds in that case, so it's treated as
        success (idempotent), not a failure.
        """
        url = f"{self.base_url}/admin/ota/devicelogrequest/add/"
        get_resp = self.session.get(url)
        get_resp.raise_for_status()
        token_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', get_resp.text)
        if not token_match:
            raise RuntimeError("create_device_log_request: csrfmiddlewaretoken not found on add form")
        resp = self.session.post(
            url,
            data={
                "csrfmiddlewaretoken": token_match.group(1),
                "domain": domain,
                "username": username,
                "_save": "Save",
            },
            headers={"Referer": url},
            allow_redirects=False,
        )
        if resp.status_code == 302:
            return {"domain": domain, "username": username, "redirect": resp.headers.get("Location"),
                    "already_existed": False}
        if resp.status_code == 200 and "already exists" in resp.text:
            return {"domain": domain, "username": username, "already_existed": True}
        raise RuntimeError(
            f"create_device_log_request failed: expected a 302 redirect to the changelist, "
            f"got {resp.status_code} (form likely re-rendered with validation errors)"
        )

    def get_prompt_update_settings(self, app_id):
        """
        Read back the Manage Update Settings tab's actually-SAVED values
        (not just what we last POSTed) by parsing which <option> in each
        <select> carries the `selected` attribute - confirmed live the
        releases page always pre-fills the form with the app's real saved
        values (e.g. right after a teardown POST of apk_prompt=off, an
        immediate GET showed `<option value="off" selected>` on that
        select), not a blank/default-only render. Same GET/regex approach
        as find_dev_apk_version() - see that method's own docstring for the
        full page-shape citation.
        """
        url = self._apps_url(f"view/{app_id}/releases/")
        resp = self.session.get(url)
        resp.raise_for_status()
        result = {}
        for field in ("apk_prompt", "apk_version", "app_prompt", "app_version"):
            select_match = re.search(
                rf'<select[^>]*name=["\']{field}["\'][^>]*>(.*?)</select>',
                resp.text, re.DOTALL | re.IGNORECASE,
            )
            if not select_match:
                result[field] = None
                continue
            selected_match = re.search(
                r'<option[^>]*value=["\']([^"\']*)["\'][^>]*selected[^>]*>',
                select_match.group(1), re.IGNORECASE,
            )
            result[field] = selected_match.group(1) if selected_match else None
        return result

    def find_dev_apk_version(self, app_id, label_substring="(dev)"):
        """
        Return the `value` of whichever <option> in the "Manage Update
        Settings" tab's apk_version <select> has `label_substring` in its
        visible label (e.g. a "CommCare 2.66.0 (dev)" option) - so callers
        never hardcode a specific pre-release version number that goes stale
        the moment a newer dev/alpha build ships (see DEV_APK_VALUE).

        UPDATE (2026-08-24), confirmed live (first real dispatch of this
        method 404'd... actually 405'd): GET
        /a/<domain>/apps/view/<app_id>/update_prompts/ is POST-only
        (PromptSettingsUpdateView's `dispatch` is wrapped in
        `@method_decorator(no_conflict_require_POST, name='dispatch')`,
        corehq/apps/app_manager/views/settings.py) - it's the form's SAVE
        target, not where it's rendered. Checked commcare-hq source
        directly: the "Manage Update Settings" tab is actually rendered as
        part of the Releases page - GET /a/<domain>/apps/view/<app_id>/releases/
        (view_app -> view_generic(release_manager=True) ->
        get_releases_context(), corehq/apps/app_manager/views/apps.py +
        view_generic.py + releases.py:197) - whose template
        (app_manager/partials/releases/releases.html) renders
        `prompt_settings_form` via `{% crispy prompt_settings_form %}`
        (django-crispy-forms renders real server-side <select>/<option>
        HTML, not client-side JS templating, so a plain GET+regex works).
        The apk_version <select> is present in that HTML even when
        apk_prompt is currently "off" - PromptUpdateSettingsForm only
        CSS-hides it then (`style="display: none;"`,
        corehq/apps/app_manager/forms.py), never omits it from the DOM.
        Source: corehq/apps/app_manager/views/apps.py:view_app
                corehq/apps/app_manager/views/view_generic.py
                corehq/apps/app_manager/views/releases.py:get_releases_context
                corehq/apps/app_manager/forms.py:PromptUpdateSettingsForm
        NOTE: option-label format assumption ("CommCare {label}", the label
        itself coming from live CommCareBuildConfig data, not source code -
        confirmed live only informally, see
        setup_02_commcare_version_plus_one.json's own "2.65.0/latest 'dev'"
        citation) - if HQ's labeling convention ever changes, this fails
        loudly (see the "no option ... found" error below) rather than
        silently resolving the wrong build.

        UPDATE (2026-08-24): alpha/dev/pre-release CommCare versions are only
        rendered as visible <option> choices in this page for a SUPERUSER
        session - confirmed live (see
        hq_setup/updates_2_49/setup_02_commcare_version_plus_one.json's own
        citation: a regular web-user's dropdown tops out at "2.63.1/latest",
        128 options, while a superuser session shows 131 options, including
        "2.64.0/latest" ("alpha") and "2.65.0/latest" ("dev")).

        UPDATE (2026-08-24, corrected same day per direct user instruction):
        this repo's existing HQ_SESSION_COOKIE / HQ_API_USERNAME+PASSWORD ARE
        themselves a superuser account's credentials - there is no separate,
        lower-privileged "regular automation account" for this domain. So
        this method just reuses self.session (the same already-authenticated
        session set_prompt_update_settings's POST already uses) - no second
        session or separate superuser-only env var needed. (An earlier
        version of this method introduced a distinct HQ_SUPERUSER_SESSION_COOKIE
        env var/throwaway session for this - removed, it was solving a
        privilege split that doesn't actually exist here.)
        """
        url = self._apps_url(f"view/{app_id}/releases/")
        resp = self.session.get(url)
        resp.raise_for_status()

        select_match = re.search(
            r'<select[^>]*name=["\']apk_version["\'][^>]*>(.*?)</select>',
            resp.text, re.DOTALL | re.IGNORECASE,
        )
        if not select_match:
            raise RuntimeError(
                f"Could not find an apk_version <select> on {url} - "
                f"the settings page shape may have changed."
            )
        options = re.findall(
            r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>([^<]*)</option>',
            select_match.group(1), re.IGNORECASE,
        )
        matches = [
            (value, html.unescape(label).strip())
            for value, label in options
            if label_substring.lower() in label.lower()
        ]
        if not matches:
            raise RuntimeError(
                f"No apk_version option containing {label_substring!r} found on {url} "
                f"({len(options)} option(s) total) - has the dev/alpha build shipped yet, "
                f"or did HQ's labeling convention change?"
            )
        if len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} apk_version options contain {label_substring!r} on {url}: "
                f"{matches} - narrow label_substring to disambiguate."
            )
        value, label = matches[0]
        print(f"[hq_client] resolved apk_version {label_substring!r} -> {value!r} ({label!r})")
        return value

    def find_recent_submission(self, username, form_path_contains=None, after=None, limit=20):
        """
        Search the Submit History report (what a human would use at
        /a/<domain>/reports/submit_history/) for the most recent form
        submitted by `username`, optionally filtered to one whose
        module>form breadcrumb contains `form_path_contains` (case-
        insensitive substring, e.g. "Markdown" or "Repeats") and submitted
        after the `after` datetime (tz-aware or naive-UTC) - use this to
        make sure you're looking at the submission a just-completed Maestro
        run produced, not an older one from a previous run.

        Returns a dict with keys `form_id` (parsed out of the "View Form"
        link, usable with get_form_metadata), `submitted_by`, `time`
        (raw display string), `path` (the module>form breadcrumb), or None
        if nothing matched within `limit` most-recent submissions overall.

        Source: corehq/apps/reports/standard/inspect.py:SubmitHistory - a
        GenericTabularReport subclass (ajax_pagination = True), served as
        JSON via corehq/apps/reports/dispatcher.py's AllowedRenderings.JSON
        rendering (`json/<report_slug>/` path prefix, confirmed against
        corehq/apps/reports/const.py) - same datatables-style
        iDisplayStart/iDisplayLength/aaData shape used by many other HQ
        reports, not something specific to this one.
        """
        url = self._reports_url("json/submit_history/")
        resp = self.session.get(url, params={"iDisplayStart": 0, "iDisplayLength": limit})
        resp.raise_for_status()
        rows = resp.json()["aaData"]

        for view_link, submitted_by, time_str, path in rows:
            if username not in submitted_by:
                continue
            if form_path_contains and form_path_contains.lower() not in path.lower():
                continue
            match = re.search(r"/reports/form_data/([\w-]+)/", view_link)
            if not match:
                continue
            if after is not None:
                submitted_at = _parse_hq_display_time(time_str)
                if submitted_at is not None and submitted_at < after:
                    continue
            return {
                "form_id": match.group(1),
                "submitted_by": html.unescape(submitted_by),
                "time": time_str,
                "path": html.unescape(path),
            }
        return None

    def get_form_metadata(self, form_id):
        """
        Return the Form Metadata tab's key/value pairs (timeStart, timeEnd,
        appVersion, deviceID, received_on, server_modified_on, etc.) for one
        submitted form, plus `has_multimedia` (whether the form's own
        multimedia block on this page lists any attachments).

        Source: corehq/apps/reports/views.py:_get_form_metadata_context()
        (called directly inside FormDataView's own GET, not a separate
        lazy/AJAX tab - confirmed live the whole dl/dt/dd metadata table is
        already present in FormDataView's first HTML response) - GET
        /a/<domain>/reports/form_data/<form_id>/.
        """
        url = self._reports_url(f"form_data/{form_id}/")
        resp = self.session.get(url)
        resp.raise_for_status()
        html = resp.text

        meta_section = html.split('id="form-metadata"', 1)[-1]
        meta_section = meta_section.split('id="form-xml"', 1)[0]
        pairs = re.findall(
            r'<dt title="([^"]+)">\s*[^<]*</dt>\s*<dd>\s*(.*?)\s*</dd>',
            meta_section,
            re.DOTALL,
        )
        metadata = {}
        for key, raw_value in pairs:
            time_match = re.search(r"datetime='([^']+)'", raw_value)
            metadata[key] = time_match.group(1) if time_match else re.sub(r"<[^>]+>", "", raw_value).strip()

        has_multimedia = bool(re.search(r'multimedia|\.(jpg|jpeg|png|mp3|mp4|3gp)"', html, re.IGNORECASE))
        metadata["has_multimedia"] = has_multimedia
        return metadata


def _parse_hq_display_time(time_str):
    """Parses SubmitHistory's "Aug 08, 2026 19:46:10 IST" display format.
    Returns None (rather than raising) on an unrecognized format, since
    callers treat this as a best-effort recency filter, not a hard
    requirement."""
    import datetime
    match = re.match(r"(\w+ \d{1,2}, \d{4} \d{1,2}:\d{2}:\d{2})", time_str)
    if not match:
        return None
    try:
        return datetime.datetime.strptime(match.group(1), "%b %d, %Y %H:%M:%S")
    except ValueError:
        return None


def resolve_app_codes(registry, base_url=None, username=None, password=None, max_commcare_version=None):
    """
    Resolve {"APP_CODE_<KEY>": code} for every entry in `registry` (see
    scripts/app_registry.py's APP_REGISTRY) - meant to be called once per
    run_suite.py invocation, right before uploading to BrowserStack, so every
    flow gets a code for whatever the CURRENT top build is instead of a
    literal that goes stale the next time someone cuts a version.

    Each registry value is either a 2-tuple (domain, app_id) - resolves to
    the app's current top build, same as always - or a 3-tuple (domain,
    app_id, saved_app_id) - pins resolution to that EXACT build instead,
    bypassing "current top build"/max_commcare_version selection entirely.
    Added (2026-08-10) for Recovery Measures' "Test One/Two/Three" builds,
    which are specific, already-cut versions a flow must address by name,
    not whatever happens to be newest today (see app_registry.py's own
    RU_TEST_ONE/TWO/THREE comment for the full citation).

    A 4th tuple element (release_first, default True) on a pinned 3-tuple
    entry opts OUT of the usual re-release attempt - use False when the
    pinned build is already confirmed released, to avoid ever calling
    mark_build_status on it (some older apps hit a real HQ platform
    migration error on that call - see app_registry.py's MOBILE2_47 entry
    for the full citation).

    Logs in once per distinct domain (a HQClient's session/CSRF token isn't
    reusable across domains needing separate permission checks) rather than
    once per app, since several registry entries commonly share a domain.

    Defaults to HQ_WEB_USER_EMAIL/HQ_WEB_USER_PASSWORD (not login()'s own
    HQ_API_USERNAME/PASSWORD default) since that's the account confirmed to
    work without a 2FA prompt (see login()'s own CAVEAT ON LOGIN docstring).

    `max_commcare_version` should be the actual CommCare APK version under
    test (see get_app_install_code's own docstring for why - a build newer
    than what's installed can never finish an online/Enter-Code install).
    Ignored for pinned (3-tuple) entries, since a specific build was already
    hand-picked and isn't subject to that selection logic.
    """
    username = username or os.environ.get("HQ_WEB_USER_EMAIL")
    password = password or os.environ.get("HQ_WEB_USER_PASSWORD")
    clients = {}
    codes = {}
    for key, entry in registry.items():
        domain, app_id = entry[0], entry[1]
        pinned_saved_app_id = entry[2] if len(entry) > 2 else None
        # UPDATE (2026-08-21), confirmed live via a real HQ platform error
        # ("mobile UCR restore version... needs to be updated to V2.0" - an
        # app-level migration blocker some older apps hit, not something
        # this repo can push through): a 4th tuple element lets a pinned
        # entry opt OUT of the release_first re-release attempt entirely
        # (default True, unchanged for every existing entry) - use False
        # for a build that's already confirmed released, to avoid ever
        # calling mark_build_status on it at all.
        release_first = entry[3] if len(entry) > 3 else True
        if domain not in clients:
            clients[domain] = HQClient(base_url=base_url, domain=domain).login(
                username=username, password=password,
            )
        if pinned_saved_app_id:
            codes[f"APP_CODE_{key}"] = clients[domain].get_app_install_code(
                app_id, saved_app_id=pinned_saved_app_id, release_first=release_first,
            )
        else:
            codes[f"APP_CODE_{key}"] = clients[domain].get_app_install_code(
                app_id, max_commcare_version=max_commcare_version,
            )
    return codes


def _version_tuple(v):
    return tuple(int(p) for p in v.split("."))


def _domain_of(url):
    return re.sub(r"^https?://", "", url).split("/")[0]


def _filename_from_content_disposition(header):
    if not header:
        return None
    match = re.search(r'filename="([^"]+)"', header)
    return match.group(1) if match else None


def run_pre_step(spec: dict, client: HQClient = None, current_apk_version: str = None):
    """
    Execute a declarative list of HQ actions, e.g. loaded from an hq_setup/*.json
    companion file for a Maestro flow:
        {"actions": [
            {"type": "mark_build_status", "app_id": "...", "saved_app_id": "...", "is_released": true},
            {"type": "create_new_build", "app_id": "...", "comment": "auto QA build"}
        ]}

    `current_apk_version` substitutes for the "$CURRENT_APK_VERSION" sentinel
    in any action's `apk_version` field (same convention as "$LAST_BUILD_ID"
    below) - e.g. a teardown spec that restores apk_version to whatever this
    run's actual under-test CommCare release was, instead of a value only the
    caller (scripts/run_suite.py's own resolved apk_commcare_version) knows.
    """
    client = client or HQClient().login()
    dispatch = {
        "mark_build_status": client.mark_build_status,
        "create_new_build": client.create_new_build,
        "set_custom_properties": client.set_custom_properties,
        "set_prompt_update_settings": client.set_prompt_update_settings,
        "get_prompt_update_settings": client.get_prompt_update_settings,
        "create_device_log_request": client.create_device_log_request,
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
        if action.get("apk_version") == "$CURRENT_APK_VERSION":
            if not current_apk_version:
                raise ValueError("$CURRENT_APK_VERSION used but no current_apk_version was given")
            action["apk_version"] = current_apk_version
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
