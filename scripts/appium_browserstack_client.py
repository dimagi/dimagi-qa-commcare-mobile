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
import base64
import os
import sys

from appium import webdriver
from appium.options.android import UiAutomator2Options

sys.path.insert(0, os.path.dirname(__file__))
from browserstack_client import _request_with_retry

UPLOAD_API_BASE = "https://api-cloud.browserstack.com/app-automate"
APPIUM_HUB_URL = "https://hub.browserstack.com/wd/hub"


class AppiumBrowserStackClient:
    def __init__(self, username=None, access_key=None):
        self.username = username or os.environ["BROWSERSTACK_USERNAME"]
        self.access_key = access_key or os.environ["BROWSERSTACK_ACCESS_KEY"]
        self.auth = (self.username, self.access_key)

    def upload_app(self, apk_path, custom_id=None):
        # UPDATE (2026-08-25), confirmed live (twice in one session): a
        # transient local SSLError (EOF occurred in violation of protocol)
        # on this upload has no retry at all here, unlike
        # scripts/browserstack_client.py's own upload_app - reuses that
        # exact same _request_with_retry helper (requests.exceptions.
        # SSLError is a subclass of ConnectionError, which it already
        # catches) instead of duplicating the retry/fresh-file-handle logic.
        data = {"custom_id": custom_id} if custom_id else {}
        resp = _request_with_retry("post", f"{UPLOAD_API_BASE}/upload", auth=self.auth,
                                    data=data, file_path=apk_path)
        return resp.json()  # {"app_url": "bs://...", ...}

    def start_session(self, app_url, device, os_version, project="QA COMMCARE MOBILE TESTS",
                       build_name=None, session_name=None, mid_session_apps=None, network_profile=None):
        """Starts a live Appium session and returns the connected driver.

        `mid_session_apps` (list of app_url strings, e.g. [new_app_url]) must
        be declared up front here - BrowserStack only allows installing an
        app mid-session if its app_url was already listed in this capability
        at session start (confirmed against BrowserStack's own "Test app
        upgrade in Appium tests" doc - it's not something you can add
        after the fact via execute_script alone).

        `network_profile` (e.g. "2g-gprs-good") throttles the WHOLE session
        to one of BrowserStack's named network-condition presets - this is
        an Appium-product-only capability (confirmed earlier this session:
        BrowserStack's Maestro v2 build-trigger API has no equivalent
        parameter at all). See scripts/appium_scenarios.py's run_scenario_2
        for why this is needed: real evidence showed the test app's CCZ
        download completes so fast on a normal connection that there's no
        reliable window to interrupt it mid-download, no matter how tightly
        the interrupt is timed in code - throttling the network is what
        actually creates the window the real test case's own steps
        ("while CommCare is downloading the app updates, update CommCare")
        assume exists."""
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
        if network_profile:
            bstack_options["networkProfile"] = network_profile
        # UPDATE (2026-08-19, 3rd correction), per direct user observation
        # (confirmed independently via every saved hierarchy dump's own
        # explicit width/height attributes, width > height throughout -
        # not a notation ambiguity): the device has genuinely been in
        # landscape this whole time. Neither the generic Appium
        # `orientation` capability nor the WebDriver `driver.orientation`
        # setter (both tried below/previously) actually took effect -
        # BrowserStack has its OWN platform-specific capability for this
        # (confirmed via their docs) that isn't the same generic Appium
        # mechanism, placed alongside every other bstack:options entry
        # here for consistency.
        bstack_options["deviceOrientation"] = "portrait"

        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.app = app_url
        # UPDATE (2026-08-19), confirmed live via scripts/appium_smoke_test.py's
        # real run + a user-supplied screenshot: a fresh install's first
        # launch showed Android's runtime "Allow CommCare to send you
        # notifications?" permission prompt covering the whole screen,
        # blocking enter_app_location underneath it - a real Appium-vs-Maestro
        # difference (per direct user observation): no Maestro flow in this
        # entire repo has ever needed to handle this dialog, meaning
        # BrowserStack's Maestro product apparently auto-grants runtime
        # permissions by default, while its Appium product does not unless
        # told to. auto_grant_permissions is a standard UiAutomator2Options
        # capability for exactly this - grants every permission the app
        # declares at install time, so the prompt never appears at all
        # (root-cause fix, not a defensive dismiss-tap that could race).
        options.auto_grant_permissions = True
        # UPDATE (2026-08-19), confirmed live via a saved failure hierarchy
        # dump (scripts/run_appium_suite.py's own _save_failure_evidence):
        # at the exact moment "edit_profile_location" was never found, the
        # dump showed width=2085/height=1080 with rotation="3" and the whole
        # tree otherwise empty (displayed="false"). 2085 is neither
        # portrait's 1080 nor landscape's 2340 - it's a value BETWEEN the
        # two, meaning the device was actively mid-rotation-ANIMATION at
        # that exact instant (most likely triggered by the on-screen
        # keyboard opening/closing on this device), not settled into a
        # stable landscape state. This app/test suite never accounts for
        # any orientation but portrait anywhere else in this repo.
        options.orientation = "PORTRAIT"
        options.set_capability("bstack:options", bstack_options)

        driver = webdriver.Remote(command_executor=APPIUM_HUB_URL, options=options)
        # UPDATE (2026-08-19, 2nd correction): the orientation CAPABILITY
        # above only sets the STARTING orientation at session launch - it
        # doesn't stop the device's own auto-rotate sensor from firing a
        # later in-session rotation (exactly what the mid-animation evidence
        # above suggests happened). Tried disabling auto-rotate at the OS
        # level via a real adb shell command next, but BrowserStack's
        # managed Appium server has the "adb_shell" insecure feature
        # disabled by default (confirmed live: UnknownError, "Potentially
        # insecure feature 'adb_shell' has not been enabled") - not
        # something a third-party client can enable on their managed
        # server. Falls back to the standard, always-allowed WebDriver
        # orientation SETTER (not a shell command) right after session
        # start instead - reasserts portrait in case the capability alone
        # wasn't enough.
        driver.orientation = "PORTRAIT"
        return driver

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

    @staticmethod
    def push_file(driver, device_path, local_file_path):
        """Pushes a local file onto the device's filesystem (e.g. a CCZ into
        /sdcard/Download/ or a custom folder) before driving the on-device
        UI that expects to find it there - the one thing this repo's Maestro
        flows genuinely cannot do (confirmed: no `adb`/file-push wiring
        anywhere in scripts/run_suite.py or browserstack_client.py, per
        several flows/recovery_measures/offline_*.yaml files' own
        "REQUIRED MANUAL/CI PRE-STEP (not run by Maestro)" headers).

        UPDATE (2026-08-25), confirmed live: `mobile: pushFile` (the newer
        extension-command form used by install_mid_session's own note above
        for installApp) failed outright with UnknownMethodException on this
        session's Appium server (1.22.0) - unlike installApp, there's no
        newer-form fallback needed here anyway, since Appium-Python-Client's
        classic `driver.push_file(path, base64data)` method (the older
        dedicated PUSH_FILE endpoint, same vintage as install_app's own
        fallback) worked directly on the first real attempt."""
        with open(local_file_path, "rb") as f:
            payload = base64.b64encode(f.read()).decode()
        driver.push_file(device_path, payload)
