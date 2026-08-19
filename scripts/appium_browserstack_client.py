"""
Thin wrapper around BrowserStack's App Automate Appium support, mirroring
scripts/browserstack_client.py's shape/auth for the Maestro side. This exists
because Maestro has no way to install a second APK mid-session (confirmed
against Maestro's own command reference - only launchApp, which requires the
app already installed) - BrowserStack's Appium sessions expose a real
mid-session app-upgrade mechanism Maestro doesn't, via the
`midSessionInstallApps` capability + `mobile: installApp` execute_script
command (https://www.browserstack.com/docs/app-automate/appium/advanced-features/test-app-upgrades).
Used only by scripts/run_appium_suite.py, for the 3 updates_partial_failed
scenarios whose Setup step genuinely requires this (see that script's own
module docstring for the full citation).

Endpoints used here (verified via BrowserStack's published docs, 2026-08-19):
    POST https://api-cloud.browserstack.com/app-automate/upload
        (upload apk - the GENERIC App Automate upload endpoint shared by
        Espresso/XCUITest/Appium, NOT scripts/browserstack_client.py's
        Maestro-specific /maestro/v2/app endpoint - app_urls are not
        interchangeable between the two)
    Appium WebDriver hub: https://hub.browserstack.com/wd/hub
        (a live WebDriver session, not a "trigger a build and poll" REST
        resource the way Maestro's API works - this module hands back a
        connected appium.webdriver.Remote, not a build id)

Auth is the same HTTP Basic BROWSERSTACK_USERNAME/BROWSERSTACK_ACCESS_KEY
pair scripts/browserstack_client.py already uses (same BrowserStack account,
same credentials, different product API).
"""
import os

import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options

UPLOAD_API_BASE = "https://api-cloud.browserstack.com/app-automate"
APPIUM_HUB_URL = "https://hub.browserstack.com/wd/hub"


class AppiumBrowserStackClient:
    def __init__(self, username=None, access_key=None):
        self.username = username or os.environ["BROWSERSTACK_USERNAME"]
        self.access_key = access_key or os.environ["BROWSERSTACK_ACCESS_KEY"]
        self.auth = (self.username, self.access_key)

    def upload_app(self, apk_path, custom_id=None):
        with open(apk_path, "rb") as f:
            files = {"file": f}
            data = {"custom_id": custom_id} if custom_id else {}
            resp = requests.post(f"{UPLOAD_API_BASE}/upload", auth=self.auth, files=files, data=data)
        resp.raise_for_status()
        return resp.json()  # {"app_url": "bs://...", ...}

    def start_session(self, app_url, device, os_version, project="QA COMMCARE MOBILE TESTS",
                       build_name=None, session_name=None, mid_session_apps=None):
        """Starts a live Appium session and returns the connected driver.

        `mid_session_apps` (list of app_url strings, e.g. [new_app_url]) must
        be declared up front here - BrowserStack only allows installing an
        app mid-session if its app_url was already listed in this capability
        at session start (confirmed against BrowserStack's own "Test app
        upgrade in Appium tests" doc - it's not something you can add
        after the fact via execute_script alone)."""
        bstack_options = {
            "userName": self.username,
            "accessKey": self.access_key,
            "projectName": project,
            "deviceName": device,
            "osVersion": os_version,
        }
        if build_name:
            bstack_options["buildName"] = build_name
        if session_name:
            bstack_options["sessionName"] = session_name
        if mid_session_apps:
            bstack_options["midSessionInstallApps"] = mid_session_apps

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.app = app_url
        options.set_capability("bstack:options", bstack_options)

        return webdriver.Remote(command_executor=APPIUM_HUB_URL, options=options)

    @staticmethod
    def install_mid_session(driver, app_url):
        """Installs/upgrades to `app_url` in the currently running session -
        the one thing Maestro genuinely cannot do (no installApp command at
        all). `app_url` must have been included in start_session's
        `mid_session_apps` list.

        UPDATE (2026-08-19), confirmed live via scripts/appium_smoke_test.py:
        a hand-rolled `driver.execute_script("mobile: installApp", ...)` call
        failed with UnknownMethodException - this session's actual Appium
        server (1.22.0) doesn't expose "installApp" as a mobile: command at
        all (confirmed via the error's own listed-supported-commands, which
        only has "installMultipleApks"), even though BrowserStack's docs show
        the mobile: form. Appium-Python-Client's own `driver.install_app()`
        already handles exactly this: it tries `mobile: installApp` first and
        falls back to the older dedicated INSTALL_APP endpoint on
        UnknownMethodException/InvalidArgumentException - so call that
        instead of reimplementing (worse) the same fallback by hand."""
        driver.install_app(app_url)
