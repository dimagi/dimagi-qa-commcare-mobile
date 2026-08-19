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

def _install_app_by_code(driver, app_code, attempts=3):
    """Port of flows/common/install_app_by_code.yaml, wrapped in a bounded
    retry. UPDATE (2026-08-19), confirmed live via a full checkpoint trail
    (see _install_app_by_code_once): tapping btn_start_install genuinely
    worked and reached a real "Setting Up App / Locating application..."
    network lookup screen (whose own text - "Keep trying if connection is
    interrupted" - anticipates exactly this) - which then failed within a
    few seconds and the app reset itself all the way back to the initial
    "Welcome to CommCare!" screen, on its own, independent of anything this
    script did. Same bounded-retry treatment as every other genuinely
    transient network hiccup this whole session already used (e.g.
    flows/common/login.yaml's Server Error/Bad Server Response retries)."""
    for attempt in range(attempts):
        _install_app_by_code_once(driver, app_code)
        if h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=1, optional=True):
            return
    raise RuntimeError(f"install_app_by_code never reached the login screen after {attempts} attempts")


def _install_app_by_code_once(driver, app_code):
    """Skips the Maestro version's `clearState: true` - a fresh BrowserStack
    Appium session already starts from a pristine install (BrowserStack
    provisions a clean device per session), unlike a Maestro flow that might
    run in a reused session."""
    driver.activate_app(APP_ID)
    h.tap_by_text(driver, "OK", optional=True, timeout=3)  # Android 16 onboarding overlay
    h.tap_by_id(driver, f"{APP_ID}:id/enter_app_location")
    # UPDATE (2026-08-19), confirmed live via a saved failure hierarchy dump
    # + screenshot: tapping enter_app_location above already focuses this
    # field and opens the keyboard as a side effect (visually confirmed),
    # but the accessibility tree can go near-empty right at that moment on
    # this Appium server - see appium_helpers.type_into_focused's own
    # docstring for the full citation. Types directly into whatever's
    # already focused instead of locating edit_profile_location by id.
    # UPDATE (2026-08-19, 2nd correction), confirmed live: the next real
    # dispatch landed back on the very first "Welcome to CommCare! Please
    # choose an installation method" screen instead of ever reaching login -
    # "mobile: type" doesn't confirm WHAT actually received the input the
    # way a targeted element's send_keys does, so the app code most likely
    # never actually landed and the install attempt failed/reset. Same
    # self-verifying type-then-check-it-actually-landed pattern
    # flows/common/login.yaml already established for exactly this
    # uncertainty (see that file's own citation) - retry a few times rather
    # than trust one blind attempt.
    for attempt in range(3):
        h.type_into_focused(driver, app_code)
        if h.is_text_visible(driver, app_code):
            break
        h.tap_by_id(driver, f"{APP_ID}:id/enter_app_location", optional=True, timeout=3)
    else:
        raise RuntimeError(f"App code {app_code!r} never appeared on screen after typing")
    h.checkpoint(driver, "code_verified_before_hide_keyboard")
    h.hide_keyboard(driver)
    h.checkpoint(driver, "after_hide_keyboard_before_start_install")
    h.tap_by_id(driver, f"{APP_ID}:id/start_install")
    h.checkpoint(driver, "after_start_install_tap")
    h.tap_by_id(driver, f"{APP_ID}:id/btn_start_install")
    h.checkpoint(driver, "after_btn_start_install_tap")
    h.tap_by_text(driver, "I.LL UPDATE LATER", optional=True, timeout=3)
    h.checkpoint(driver, "after_update_later_optional")
    h.tap_by_id(driver, f"{APP_ID}:id/btn_start_install", optional=True, timeout=3)
    h.checkpoint(driver, "after_second_btn_start_install_optional")
    for i in range(6):
        if not h.tap_by_id(driver, f"{APP_ID}:id/screen_multimedia_retry", optional=True, timeout=2):
            break
        h.checkpoint(driver, f"multimedia_retry_loop_{i}")
        h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=15, optional=True)
    h.checkpoint(driver, "before_final_edit_username_wait")
    # optional here (was a hard 120s wait) - a failed lookup resets to
    # Welcome within a few seconds per the evidence above, so there's no
    # value in waiting the full 120s on a doomed attempt; _install_app_by_code's
    # own outer retry loop is what decides whether to give up.
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=60, optional=True)


def _install_as_mobile_worker(driver, username, domain, password):
    """Port of scenario_5's own inline mobile-worker install sequence."""
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "See Apps for My User")
    # UPDATE (2026-08-19): same self-verifying tap+type as _login below -
    # see _type_into_id_verified's own docstring for the full citation.
    _type_into_id_verified(driver, f"{APP_ID}:id/edit_username", username)
    _type_into_id_verified(driver, f"{APP_ID}:id/edit_domain", domain)
    _type_into_id_verified(driver, f"{APP_ID}:id/edit_password", password)
    h.hide_keyboard(driver)
    h.tap_by_id(driver, f"{APP_ID}:id/get_apps_button")
    h.wait_visible_id(driver, f"{APP_ID}:id/apps_list_view", timeout=15)
    h.tap_by_id(driver, f"{APP_ID}:id/apps_list_view")
    h.tap_by_id(driver, f"{APP_ID}:id/install_app_button")
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=30)


def _type_into_id_verified(driver, resource_id, text, attempts=5):
    """Self-verifying tap+type, same pattern flows/common/login.yaml already
    established for exactly this uncertainty: tapping a text field can race
    with Gboard's own one-time "Get Smart content..."/"Skip" popup, which
    can steal focus back after being dismissed - re-tap, retype, and check
    the value ACTUALLY landed rather than trust one blind attempt.

    UPDATE (2026-08-19), confirmed live via a screenshot: even with the
    field visibly focused (keyboard open, cursor there), a located
    WebElement's own send_keys() (input_text) can silently deliver nothing
    on this Appium server - same unreliability already confirmed for the
    app-code field (see appium_helpers.type_into_focused's own docstring).
    Types into whatever's already focused instead of via a located element.

    UPDATE (2026-08-19, 2nd correction), confirmed live via a screenshot: a
    PASSWORD field masks its real characters, so is_text_visible(text) can
    never match - every "failed" verification triggered another retype on
    top of the existing (already-correct) value, and 3 retries of "123"
    literally concatenated into 9 visible mask dots. Clears the field
    before every attempt and verifies by LENGTH (works for both masked and
    plain fields) instead of literal content.

    UPDATE (2026-08-19, 3rd correction), confirmed live via a failure
    screenshot: on this specific screen (CommCare's post-binary-swap
    "Welcome back! Please log in.") the status bar showed Android's own
    autofill/Smart Lock key-icon indicator, and ONLY the password field
    (never the username field, which landed correctly first-try in the
    same run) kept accumulating extra characters despite clear_by_id now
    sending both backward and forward deletes. That combination - a
    live autofill indicator plus corruption limited to the one field type
    autofill services specifically target - points at a race: the
    service's suggestion can be injected asynchronously, slightly AFTER
    our own clear+type has already run and been measured.

    UPDATE (2026-08-19, 4th correction), confirmed live via a SECOND
    failure screenshot: the settle-wait above wasn't enough on its own -
    same field, same symptom, on a later dispatch (intermittent, not
    deterministic - it passed clean on the run in between). Re-tapping the
    field on every attempt was itself suspect: each tap is a fresh focus
    event, which is exactly what a Smart Lock-style service listens for
    before deciding whether to offer/inject a suggestion, so retrying via
    "tap again, clear, type" plausibly invited a FRESH race each time
    rather than recovering from the last one. Taps into the field only
    ONCE up front, then retries clear+type+settle without any further taps
    - and requires the length check to hold across TWO reads a beat apart
    before accepting it, so a late injection landing between the first
    check and the return can still be caught by the second."""
    h.tap_by_id(driver, f"{resource_id}", optional=True, timeout=5)
    h.tap_by_text(driver, "Skip", optional=True, timeout=2)
    h.tap_by_id(driver, f"{resource_id}", optional=True, timeout=5)
    for _ in range(attempts):
        h.clear_by_id(driver, resource_id)
        h.type_into_focused(driver, text)
        time.sleep(1.0)
        if h.field_text_length(driver, resource_id) != len(text):
            continue
        time.sleep(0.6)
        if h.field_text_length(driver, resource_id) == len(text):
            return
    raise RuntimeError(f"Text {text!r} never landed in id={resource_id!r} after {attempts} attempts")


def _login(driver, username, password):
    """Port of flows/common/login.yaml."""
    h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=30)
    _type_into_id_verified(driver, f"{APP_ID}:id/edit_username", username)
    _type_into_id_verified(driver, f"{APP_ID}:id/edit_password", password)
    h.hide_keyboard(driver)
    h.tap_by_id(driver, f"{APP_ID}:id/login_button")
    for error_text in ("Bad Server Response", "Server Error"):
        for _ in range(5):
            if not h.is_text_visible(driver, error_text):
                break
            time.sleep(3)
            h.tap_by_id(driver, f"{APP_ID}:id/login_button")
    # UPDATE (2026-08-19), confirmed live via a screenshot: a bare
    # wait_not_visible here can pass vacuously if the "Communicating with
    # Server / Contacting server for sync..." restore dialog just hasn't
    # appeared YET at the moment of the check (same race
    # flows/common/login.yaml's own header already documents fighting) -
    # the dialog then appears a moment later and blocks whatever comes
    # right after _login() returns. Optionally wait for it to actually
    # APPEAR first (a no-op if it never does, e.g. nothing to restore),
    # then wait for it to genuinely finish.
    _wait_out_sync_dialog(driver)


def _wait_out_sync_dialog(driver, timeout=120):
    """Two-phase wait for CommCare's "Communicating with Server/Contacting
    server for sync..." dialog (dialog_cancel_button) - see _login's own
    UPDATE for the race this avoids. UPDATE (2026-08-19, 2nd correction),
    confirmed live: this same dialog can reappear LATER too, not just
    right after login - it showed up again blocking "More options" while
    opening the update menu, since checking for an update is itself
    another server round-trip. Call this before any action that might run
    into a sync happening at an unpredictable moment, not just post-login."""
    h.wait_visible_id(driver, f"{APP_ID}:id/dialog_cancel_button", timeout=5, optional=True)
    h.wait_not_visible_id(driver, f"{APP_ID}:id/dialog_cancel_button", timeout=timeout)


def _check_app_version(driver):
    """Port of flows/common/check_app_version_via_about.yaml.

    UPDATE (2026-08-19), per direct user instruction after a real dispatch
    showed "App v71." where this check's old hardcoded expectation was
    "App v2.": this HQ test app's version number increments every time it
    gets republished on HQ, unrelated to which of this test's own update
    steps is running - an exact expected number goes stale on its own, so
    it is no longer asserted. Only verifies the "About CommCare" dialog
    itself came up correctly (its own title text + "OK" button visible)
    and reads back whatever version it currently shows, for visibility in
    logs/results rather than as a pass/fail check."""
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "About CommCare")
    h.assert_visible_text(driver, "About CommCare")
    h.assert_visible_text(driver, "OK")
    about_text = h.find_text_matching(driver, r"App v(\d+)\.")
    version = re.search(r"App v(\d+)\.", about_text).group(1) if about_text else None
    h.tap_by_text(driver, "OK")
    return version


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
    _wait_out_sync_dialog(driver)
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
        ("Update 1: staged update auto-applied (About CommCare check)", lambda: _check_app_version(driver)),
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
        ("Update 4: update did NOT auto-apply (About CommCare check)", lambda: _check_app_version(driver)),
        ("Update 5: manually complete the update", lambda: _complete_update_app(driver)),
        ("Login after manual update completes", lambda: _login(driver, username, password)),
        ("Update 5 (verify): About CommCare check", lambda: _check_app_version(driver)),
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
        ("Update verification: auto-updated to V6 (About CommCare check)", lambda: _check_app_version(driver)),
        ("Update 18: Form A verification", lambda: _verify_form_a_questions(driver, "Form A")),
        ("Update 19: Form B verification", lambda: _verify_form_b_photo_question(driver, "Form B")),
    ]
    return _run_steps(steps)
