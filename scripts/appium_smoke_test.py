"""
Standalone smoke test for the ONE genuinely new/unverified mechanism
scripts/run_appium_suite.py depends on: BrowserStack's `midSessionInstallApps`
capability + `mobile: installApp` actually upgrading a running session's app
in place. Run this ONCE, in isolation, before trusting the full
scenario_1/2/5 implementations built on top of it - the exact capability
placement (top-level vs under `bstack:options`) wasn't independently
confirmed from BrowserStack's docs alone (their AI-summarized doc pages
disagreed slightly on this), so this is the cheapest way to nail it down
against a real session rather than debug it inside a 10-minute scenario run.

Usage:
    python scripts/appium_smoke_test.py
"""
import os
import pathlib
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
from appium_browserstack_client import AppiumBrowserStackClient

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OLD_APK_PATH = REPO_ROOT / "resources" / "commcare_2.45_release.apk"
NEW_APK_PATH = REPO_ROOT / "apks" / "app-commcare-release.apk"


def main():
    load_dotenv(REPO_ROOT / ".env")
    bs = AppiumBrowserStackClient()

    if not NEW_APK_PATH.exists():
        raise SystemExit(f"{NEW_APK_PATH} not found - run scripts/run_suite.py once first "
                          f"(or scripts/download_apk.py directly) to populate it.")

    print(f"Uploading old APK ({OLD_APK_PATH.name}) ...")
    old_app_url = bs.upload_app(str(OLD_APK_PATH))["app_url"]
    print(f"  -> {old_app_url}")

    print(f"Uploading new APK ({NEW_APK_PATH.name}) ...")
    new_app_url = bs.upload_app(str(NEW_APK_PATH))["app_url"]
    print(f"  -> {new_app_url}")

    print("Starting session on OLD apk with midSessionInstallApps=[new_app_url] ...")
    driver = bs.start_session(
        old_app_url, "Samsung Galaxy S26", "16.0",
        build_name="appium-smoke-test", session_name="mid_session_install_check",
        mid_session_apps=[new_app_url],
    )
    try:
        # UPDATE (2026-08-19): "mobile: appInfo" isn't a real command on this
        # Appium server (1.22.0) either - confirmed live via this exact
        # script's first run (UnknownMethodException, same class of
        # doc-vs-actual-server mismatch as install_app below). is_app_installed
        # is a stable, long-standing Appium-Python-Client method (not a raw
        # execute_script call), so it isn't exposed to that same risk.
        before_installed = driver.is_app_installed("org.commcare.dalvik")
        print(f"org.commcare.dalvik installed BEFORE mid-session install: {before_installed}")

        print("Calling driver.install_app() with the new apk ...")
        bs.install_mid_session(driver, new_app_url)

        after_installed = driver.is_app_installed("org.commcare.dalvik")
        print(f"org.commcare.dalvik installed AFTER mid-session install: {after_installed}")
        print("install_app() returned without raising - mid-session install call succeeded "
              "(this confirms the mechanism works; it does not by itself prove the APK VERSION "
              "changed - the next real dispatch's app-version check inside a scenario is what "
              "confirms that).")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
