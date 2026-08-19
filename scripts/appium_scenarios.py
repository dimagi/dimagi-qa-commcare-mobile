"""
Appium implementations of updates_partial_failed's scenario_1, scenario_2,
and scenario_5 - the 3 flows whose Setup step needs a mid-session CommCare
binary swap, which Maestro cannot do (see scripts/run_appium_suite.py's own
module docstring for the full citation/rationale). Each function here ports
that scenario's ALREADY-automated Maestro verification steps 1:1 (same
resource-ids/text, catalogued directly from flows/common/login.yaml,
check_app_version_via_about.yaml, navigate_form_no_errors.yaml,
update_app_via_menu.yaml, verify_form_a_questions.yaml,
verify_form_b_photo_question.yaml, and each scenario_*.yaml's own inline
steps) onto scripts/appium_helpers.py's primitives, and adds the genuinely
new part: the real mid-session install via AppiumBrowserStackClient.

GENERIC/BEST-EFFORT (matching this repo's own convention for exactly this
kind of gap): "interrupt mid-stream" is tied to an OBSERVABLE UI signal -
CommCare's data-pull progress dialog (dialog_cancel_button, shared across
every phase per login.yaml's own citation) - polled for and then interrupted
via the mid-session install. This is the closest a UI-level tool can get to
"mid-download", not a guaranteed byte-offset; the exact dialog id/timing for
an "Update App"-triggered CCZ download (as opposed to login's own restore
dialog) was not independently confirmed live before this was written - the
first real dispatch of each scenario should confirm or correct it against
the actual failure/session evidence, same as every other flow in this repo.
"""
import re
import time

import appium_helpers as h

APP_ID = "org.commcare.dalvik"


class ScenarioFailure(Exception):
    def __init__(self, step_name, original):
        self.step_name = step_name
        self.original = original
        super().__init__(f"{step_name}: {original}")


def _run_steps(steps):
    """Runs a list of (name, fn) pairs in order, returning the list of step
    names completed. Raises ScenarioFailure(step_name, original_exc) on the
    first failure, same shape as a Maestro failed_step string."""
    completed = []
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:
            raise ScenarioFailure(name, exc) from exc
        completed.append(name)
    return completed


# --------------------------------------------------------------- primitives --

def _install_app_by_code(driver, app_code):
    """Port of flows/common/install_app_by_code.yaml. Skips the Maestro
    version's `clearState: true` - a fresh BrowserStack Appium session
    already starts from a pristine install (BrowserStack provisions a clean
    device per session), unlike a Maestro flow that might run in a reused
    session."""
    driver.activate_app(APP_ID)
    h.tap_by_text(driver, "OK", optional=True, timeout=3)  # Android 16 onboarding overlay
    h.tap_by_id(driver, f"{APP_ID}:id/enter_app_location")
    h.tap_by_id(driver, f"{APP_ID}:id/edit_profile_location")
    h.input_text(driver, f"{APP_ID}:id/edit_profile_location", app_code)
    h.hide_keyboard(driver)
    h.tap_by_id(driver, f"{APP_ID}:id/start_install")
    h.tap_by_id(driver, f"{APP_ID}:id/btn_start_install")
    h.tap_by_text(driver, "I.LL UPDATE LATER", optional=True, timeout=3)
    h.tap_by_id(driver, f"{APP_ID}:id/btn_start_install", optional=True, timeout=3)
    for _ in range(6):
        if not h.tap_by_id(driver, f"{APP_ID}:id/screen_multimedia_retry", optional=True, timeout=2):
            break
        h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=15, optional=True)
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=120)


def _install_as_mobile_worker(driver, username, domain, password):
    """Port of scenario_5's own inline mobile-worker install sequence."""
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "See Apps for My User")
    h.tap_by_id(driver, f"{APP_ID}:id/edit_username")
    h.input_text(driver, f"{APP_ID}:id/edit_username", username)
    h.tap_by_id(driver, f"{APP_ID}:id/edit_domain")
    h.input_text(driver, f"{APP_ID}:id/edit_domain", domain)
    h.tap_by_id(driver, f"{APP_ID}:id/edit_password")
    h.input_text(driver, f"{APP_ID}:id/edit_password", password)
    h.hide_keyboard(driver)
    h.tap_by_id(driver, f"{APP_ID}:id/get_apps_button")
    h.wait_visible_id(driver, f"{APP_ID}:id/apps_list_view", timeout=15)
    h.tap_by_id(driver, f"{APP_ID}:id/apps_list_view")
    h.tap_by_id(driver, f"{APP_ID}:id/install_app_button")
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=30)


def _login(driver, username, password):
    """Port of flows/common/login.yaml."""
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=30)
    h.tap_by_id(driver, f"{APP_ID}:id/edit_username")
    h.tap_by_text(driver, "Skip", optional=True, timeout=2)
    h.input_text(driver, f"{APP_ID}:id/edit_username", username)
    h.tap_by_id(driver, f"{APP_ID}:id/edit_password")
    h.tap_by_text(driver, "Skip", optional=True, timeout=2)
    h.input_text(driver, f"{APP_ID}:id/edit_password", password)
    h.hide_keyboard(driver)
    h.tap_by_id(driver, f"{APP_ID}:id/login_button")
    for error_text in ("Bad Server Response", "Server Error"):
        for _ in range(5):
            if not h.is_text_visible(driver, error_text):
                break
            time.sleep(3)
            h.tap_by_id(driver, f"{APP_ID}:id/login_button")
    h.wait_not_visible_id(driver, f"{APP_ID}:id/dialog_cancel_button", timeout=120)


def _check_app_version(driver, expected_version):
    """Port of flows/common/check_app_version_via_about.yaml."""
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "About CommCare")
    h.wait_visible_text(driver, rf"(?s).*App v{re.escape(expected_version)}\..*", timeout=5, regex=True)
    h.tap_by_text(driver, "OK")


def _navigate_form_no_errors(driver, form_name):
    """Port of flows/common/navigate_form_no_errors.yaml."""
    h.tap_by_text(driver, "Start")
    h.tap_by_text(driver, form_name)
    h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=5)
    for _ in range(4):
        h.tap_by_id(driver, f"{APP_ID}:id/choice_dialog_panel_3", optional=True, timeout=2)
        h.tap_by_id(driver, f"{APP_ID}:id/nav_btn_next", optional=True, timeout=2)
    h.assert_not_visible_text(driver, "(?i).*unexpected error.*", regex=True)
    h.assert_not_visible_text(driver, "(?i).*commcare will now restart.*", regex=True)
    h.back(driver)
    h.tap_by_text(driver, "(?i).*exit without saving.*", regex=True, optional=True, timeout=2)
    h.back(driver)
    h.wait_visible_text(driver, "Start", timeout=5)


def _verify_form_a_questions(driver, form_name):
    """Port of flows/common/verify_form_a_questions.yaml."""
    h.tap_by_text(driver, "Start")
    h.tap_by_text(driver, form_name)
    h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=5)
    h.assert_visible_text(driver, "(?i).*registration date.*", regex=True)
    h.tap_by_id(driver, f"{APP_ID}:id/nav_btn_next")
    h.assert_visible_text(driver, r"(?i).*\bage\b.*", regex=True)
    h.assert_not_visible_text(driver, "(?i).*unexpected error.*", regex=True)
    h.assert_not_visible_text(driver, "(?i).*commcare will now restart.*", regex=True)
    h.back(driver)
    h.tap_by_text(driver, "(?i).*exit without saving.*", regex=True, optional=True, timeout=2)
    h.back(driver)
    h.wait_visible_text(driver, "Start", timeout=5)


def _verify_form_b_photo_question(driver, form_name):
    """Port of flows/common/verify_form_b_photo_question.yaml."""
    h.tap_by_text(driver, "Start")
    h.tap_by_text(driver, form_name)
    h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=5)
    h.assert_visible_text(driver, "(?i).*(take picture|choose image).*", regex=True)
    h.assert_not_visible_text(driver, "(?i).*unexpected error.*", regex=True)
    h.assert_not_visible_text(driver, "(?i).*commcare will now restart.*", regex=True)
    h.back(driver)
    h.tap_by_text(driver, "(?i).*exit without saving.*", regex=True, optional=True, timeout=2)
    h.back(driver)
    h.wait_visible_text(driver, "Start", timeout=5)


def _open_update_app_menu(driver):
    """First half of flows/common/update_app_via_menu.yaml - opens the
    update dialog and taps through to the point where a CCZ download/stage
    would begin, WITHOUT waiting for or tapping the final "Update to version
    X & log out" completion button - the interruption point for
    scenario_1/scenario_2."""
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "Update App")
    h.wait_visible_text(driver, "New version of the application is available", timeout=8, optional=True)
    h.tap_by_text(driver, "Update to the Latest App Version", optional=True, timeout=3)


def _complete_update_app(driver):
    """Full flows/common/update_app_via_menu.yaml, run to completion (used
    when a scenario needs a genuinely-finished, uninterrupted update - e.g.
    scenario_2's Update 5)."""
    _open_update_app_menu(driver)
    h.wait_visible_text(driver, r"Update to version.*log out", timeout=30, regex=True)
    h.tap_by_text(driver, r"Update to version.*log out", regex=True)


def _wait_for_update_in_progress(driver, timeout=20):
    """See module docstring's GENERIC/BEST-EFFORT note - polls for the same
    dialog_cancel_button id CommCare's data-pull progress dialog uses,
    optional since a slow/fast device could plausibly miss the window."""
    return h.wait_visible_id(driver, f"{APP_ID}:id/dialog_cancel_button", timeout=timeout, optional=True)


# ----------------------------------------------------------------- scenarios --

def run_scenario_1(driver, appium_client, new_app_url, username, password, app_code):
    """Test Case 1: stage a CCZ update on the old binary, interrupt with a
    CommCare binary update mid-stream, ending logged out with the CCZ
    update staged-but-unapplied - then verify it auto-applies on next login
    (Update 1-3)."""
    steps = [
        ("Install app by code (old CommCare binary)", lambda: _install_app_by_code(driver, app_code)),
        ("Login (old binary)", lambda: _login(driver, username, password)),
        ("Trigger Update App (stage CCZ update)", lambda: _open_update_app_menu(driver)),
        ("Wait for CCZ download/staging in progress", lambda: _wait_for_update_in_progress(driver, timeout=20)),
        ("Install new CommCare binary mid-session (interrupt)",
         lambda: appium_client.install_mid_session(driver, new_app_url)),
        ("Relaunch app after binary swap", lambda: driver.activate_app(APP_ID)),
        ("Login again (new binary)", lambda: _login(driver, username, password)),
        ("Update 1: staged update auto-applied -> Version 2", lambda: _check_app_version(driver, "2")),
        ("Update 2: Form 1 navigates without error", lambda: _navigate_form_no_errors(driver, "Form 1")),
        ("Update 3: Form 2 navigates without error", lambda: _navigate_form_no_errors(driver, "Form 2")),
    ]
    return _run_steps(steps)


def run_scenario_2(driver, appium_client, new_app_url, username, password, app_code):
    """Test Case 2: update CommCare while a CCZ download is in progress,
    interrupted early enough that the CCZ update does NOT auto-apply -
    then manually complete it via the update menu (Update 4-7)."""
    steps = [
        ("Install app by code (old CommCare binary)", lambda: _install_app_by_code(driver, app_code)),
        ("Login (old binary)", lambda: _login(driver, username, password)),
        ("Trigger Update App (start CCZ download)", lambda: _open_update_app_menu(driver)),
        # No settle wait here (unlike scenario_1) - interrupt as early as
        # possible so the download genuinely doesn't finish staging.
        ("Install new CommCare binary mid-session (early interrupt)",
         lambda: appium_client.install_mid_session(driver, new_app_url)),
        ("Relaunch app after binary swap", lambda: driver.activate_app(APP_ID)),
        ("Login again (new binary)", lambda: _login(driver, username, password)),
        ("Update 4: update did NOT auto-apply -> still Version 1", lambda: _check_app_version(driver, "1")),
        ("Update 5: manually complete the update", lambda: _complete_update_app(driver)),
        ("Login after manual update completes", lambda: _login(driver, username, password)),
        ("Update 5 (verify): now on Version 2", lambda: _check_app_version(driver, "2")),
        ("Update 6: Form 1 navigates without error", lambda: _navigate_form_no_errors(driver, "Form 1")),
        ("Update 7: Form 2 navigates without error", lambda: _navigate_form_no_errors(driver, "Form 2")),
    ]
    return _run_steps(steps)


def run_scenario_5(driver, appium_client, new_app_url, cc_username, cc_password,
                    hq_mobile_worker_username, hq_domain, hq_mobile_worker_password):
    """Test Case 5: install the linked app as a mobile worker on the old
    binary, stage a V6 CCZ update, interrupt with a CommCare binary update
    mid-stream, then verify the auto-update to V6/Version 22 and Forms A/B
    (Update 17-19)."""
    steps = [
        ("Install as mobile worker (old CommCare binary)",
         lambda: _install_as_mobile_worker(driver, hq_mobile_worker_username, hq_domain, hq_mobile_worker_password)),
        ("Trigger Update App (stage V6 CCZ update)", lambda: _open_update_app_menu(driver)),
        ("Wait for CCZ download/staging in progress", lambda: _wait_for_update_in_progress(driver, timeout=20)),
        ("Install new CommCare binary mid-session (interrupt)",
         lambda: appium_client.install_mid_session(driver, new_app_url)),
        ("Relaunch app after binary swap", lambda: driver.activate_app(APP_ID)),
        ("Update 17: re-login (new binary)", lambda: _login(driver, cc_username, cc_password)),
        ("Update verification: auto-updated to V6 -> Version 22", lambda: _check_app_version(driver, "22")),
        ("Update 18: Form A verification", lambda: _verify_form_a_questions(driver, "Form A")),
        ("Update 19: Form B verification", lambda: _verify_form_b_photo_question(driver, "Form B")),
    ]
    return _run_steps(steps)
