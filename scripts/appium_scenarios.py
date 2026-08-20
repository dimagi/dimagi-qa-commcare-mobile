"""
Appium implementations of Maestro-impossible scenarios, split by WHY Maestro
can't do them:

- updates_partial_failed's scenario_1, scenario_2, scenario_5: need a
  mid-session CommCare BINARY swap (see scripts/run_appium_suite.py's own
  module docstring for the full citation/rationale).
- prompted_updates' scenario_1 (optional CCZ update) and scenario_2 (forced
  CCZ update): need an HQ-side action (mark_build_status/
  set_prompt_update_settings) to fire BETWEEN an on-device logout and the
  next login, on the SAME device/app state - a Maestro build runs its whole
  "execute" list server-side with no external control point mid-build (see
  hq_setup/prompted_updates/scenario_02_forced.json's own header for where
  this was first flagged as needing "extend scripts/run_suite.py to pause
  here"). An Appium session is driven by a persistent Python process
  instead, so it can freely call HQClient methods directly between UI
  actions on that same live session - see
  scripts/run_appium_prompted_updates_suite.py.

Each function here ports that scenario's ALREADY-automated Maestro
verification steps 1:1 (same resource-ids/text, catalogued directly from
flows/common/login.yaml, flows/common/logout.yaml,
check_app_version_via_about.yaml, navigate_form_no_errors.yaml,
update_app_via_menu.yaml, verify_form_a_questions.yaml,
verify_form_b_photo_question.yaml, and each scenario_*.yaml's own inline
steps) onto scripts/appium_helpers.py's primitives, plus whichever
genuinely-new mechanism (mid-session install, or a direct HQClient call)
Maestro itself has no way to reach.

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
    # UPDATE (2026-08-19/20), confirmed live across 3 separate real
    # dispatches that CommCare shows several DIFFERENT network-progress
    # dialogs depending on context - not one dialog with one selector:
    # dialog_cancel_button (a real Cancel button, the restore/sync dialog
    # flows/common/login.yaml's own header documents), "Logging in" (no
    # button at all - a first-ever login right after a fresh
    # install_app_by_code), and "Communicating with Server"/"Requesting
    # Data..." (a "STOP" button - a later login needing a full data
    # pull). A bare wait_not_visible on any ONE of these can pass
    # vacuously if that particular dialog hasn't appeared yet while a
    # DIFFERENT one is about to, so whatever runs right after _login()
    # returns can still get blocked. See _wait_out_progress_dialogs.
    _wait_out_progress_dialogs(driver)


_PROGRESS_DIALOG_TEXTS = ("Logging in", "Communicating with Server", "Requesting Data")


def _wait_out_progress_dialogs(driver, timeout=120):
    """Waits out ANY of CommCare's several distinct network-progress
    dialogs together (see _login's own UPDATE for the full citation of
    which ones and why a single-dialog wait isn't enough) - loops
    checking every known signature on each pass rather than waiting for
    one then returning, so a dialog that changes shape mid-wait (e.g.
    "Logging in" hands off to "Communicating with Server" without the
    screen ever going fully clear in between) doesn't slip through a
    single-shot check between two sequential waits. Call this before any
    action that might run into a sync happening at an unpredictable
    moment, not just post-login - confirmed live it can also reappear
    later, e.g. blocking "More options" while opening the update menu,
    since checking for an update is itself another server round-trip."""
    h.reassert_portrait(driver)
    time.sleep(2)  # give a dialog that hasn't appeared YET a moment to do so
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        showing = h.wait_visible_id(driver, f"{APP_ID}:id/dialog_cancel_button", timeout=0.1, optional=True)
        showing = showing or any(h.is_text_visible(driver, t) for t in _PROGRESS_DIALOG_TEXTS)
        if not showing:
            return
        time.sleep(2)
    raise TimeoutError(f"A network-progress dialog never cleared within {timeout}s")


def _logout(driver):
    """Port of flows/common/logout.yaml - "Log out of CommCare" is the
    5th/last home-grid tile, with a plain visible text label.

    UPDATE (2026-08-20), confirmed live via a real portrait screenshot, per
    direct user correction: this genuinely IS a home-screen tile (not a
    drawer item, and not icon-only) - a fresh diagnostic dispatched AFTER
    the portrait-reassertion fix (h.reassert_portrait) showed all 5 tiles
    (Start, Saved, Incomplete, Sync with Server, Log out of CommCare) at
    once with real, readable labels, no scrolling even needed on this
    device. Every earlier failure (landing on the module list, "Saved
    Forms", the drawer never opening in time) traces back to the device
    still being in LANDSCAPE at that point - its cramped height was both
    hiding the tile and (per several dumps) leaving its label off the
    accessibility tree entirely, which is what led earlier debugging
    astray into drawer/index-tap workarounds that were solving the wrong
    problem. Portrait's extra height fits everything without scrolling, but
    a defensive scroll is kept first anyway (matching Maestro's own
    logout.yaml, which always swipes first) in case a narrower device ever
    needs it."""
    for _ in range(2):
        h.swipe_up_on(driver, f"{APP_ID}:id/nsv_home_screen", optional=True)
    h.tap_by_text(driver, "Log out of CommCare")


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
    """Port of flows/common/navigate_form_no_errors.yaml.

    UPDATE (2026-08-20), confirmed live via a real dispatch: that Maestro
    file's own header already flagged this as an UNVERIFIED assumption for
    exactly the apps this is used against ("Mobile Updates - Test 1_2!") -
    a real dispatch confirmed the gap: after tapping "Start", the module
    list shows an intermediate "Mobile updates" module, not the form
    directly, so tapping form_name there timed out. Taps through that
    module first (optional, so nothing changes if a flat single-level app
    never shows it).

    UPDATE (2026-08-21), per direct user correction (a real-device
    screenshot of Form 2's last question) that overturned the whole
    "page through a few, then discard" design this was ported from: the
    SAME nav_btn_next button relabels itself "FINISH" on a form's last
    question, and tapping it there actually SUBMITS the form - Form 1 is
    supposed to complete and submit (it has "a few media files" per the
    sheet's own note, so more questions than the old fixed 4-tap loop
    covered), not get discarded via "Exit Form?" partway through, which is
    what a too-short loop was doing. Form 2 only ever "worked" by
    coincidence - its 2 questions happened to fit inside 4 taps and the
    2nd tap was already FINISH. Loops tapping Next/Finish while
    nav_btn_next is still present (bounded generously rather than fixed
    small, since the real question count varies by form) instead of
    stopping early and discarding."""
    h.tap_by_text(driver, "Start")
    h.tap_by_text(driver, "Mobile updates", optional=True, timeout=3)
    # UPDATE (2026-08-21), per direct user confirmation: "Mobile2.47"
    # (flows/common/navigate_form_no_errors.yaml's other real caller) has
    # the same intermediate-module gap but names its module "Surveys" -
    # matching that Maestro fix here too, in case a future Appium scenario
    # ever targets this app.
    h.tap_by_text(driver, "Surveys", optional=True, timeout=3)
    h.tap_by_text(driver, form_name)
    h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=5)
    for _ in range(20):
        h.tap_by_id(driver, f"{APP_ID}:id/choice_dialog_panel_3", optional=True, timeout=2)
        if not h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=2, optional=True):
            break
        h.tap_by_id(driver, f"{APP_ID}:id/nav_btn_next", optional=True, timeout=2)
    h.assert_not_visible_text(driver, "(?i).*unexpected error.*", regex=True)
    h.assert_not_visible_text(driver, "(?i).*commcare will now restart.*", regex=True)
    # UPDATE (2026-08-21), confirmed live via a real dispatch (the same fix
    # ported to flows/common/navigate_form_no_errors.yaml, build
    # "scenario2-notvisible-while-verify": passed, 1/1) after two earlier
    # failures with a fixed 2-back count (one overshooting into the
    # Android launcher, one landing on the login screen) - the real
    # nesting depth genuinely varies by navigation (most likely because
    # "Mobile updates", tapped through above, doesn't always show on a
    # later navigation), so a fixed count can't be right for every case.
    # Presses back only WHILE "Start" is not yet visible (bounded)
    # instead - a form whose submission already lands on Start presses
    # zero backs, one that doesn't gets exactly as many as it needs.
    for _ in range(3):
        if h.wait_visible_text(driver, "Start", timeout=2, optional=True):
            break
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
    _wait_out_progress_dialogs(driver)
    h.tap_by_text(driver, "More options")
    h.tap_by_text(driver, "Update App")
    h.wait_visible_text(driver, "New version of the application is available", timeout=8, optional=True)
    # UPDATE (2026-08-20), confirmed live: this button's rendered `text`
    # attribute is "UPDATE TO THE LATEST APP VERSION" (all-caps, an Android
    # textAllCaps style) - a case-sensitive literal match against the
    # title-case string never matches, so this needs regex + (?i).
    h.tap_by_text(driver, r"(?i)update to the latest app version", regex=True, optional=True, timeout=3)


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


# ------------------------------------------------- prompted_updates scenarios --

def _login_and_wait_for_forced_blocker(driver, username, password, attempts=4, wait_seconds=45):
    """Port of prompted_updates scenario_02's relogin step, hardened per a
    user-supplied recording of the real manual sequence: the forced
    "New version of the application is required" blocker does NOT always
    appear on the very next login after mark_build_status - the recording
    showed it can take multiple relogin attempts or up to ~3 minutes
    (presumably server-side propagation delay on HQ's end), not a fixed
    instant. Retries login + a bounded wait rather than asserting on one
    immediate attempt; attempts=4 * wait_seconds=45 ~= 3 minutes total,
    matching that recording's own guidance."""
    for attempt in range(attempts):
        _login(driver, username, password)
        if h.wait_visible_text(driver, "New version of the application is required",
                                timeout=wait_seconds, optional=True):
            return
        if attempt < attempts - 1:
            _logout(driver)
    raise RuntimeError(
        f"Forced update blocker never appeared after {attempts} relogin attempts "
        f"({attempts * wait_seconds}s total)"
    )


def run_prompted_update_scenario_1(driver, hq_client, app_id, latest_build_id, username, password, app_code):
    """Prompted Updates Scenario 1: optional CCZ update end-to-end. Port of
    flows/prompted_updates/scenario_01_optional_ccz_update.yaml, with the
    genuinely-new part being the mark_build_status call fired directly
    against HQClient between the flow's own logout and re-login - see this
    module's own docstring for why Maestro can't do that mid-build.
    ASSUMES Setup 2/3 (mark latest build In Test + prior Released, Prompt
    Updates to CommCare/App = On) have already run - same precondition the
    Maestro flow itself documents, see hq_setup/prompted_updates/
    setup_02_mark_in_test.json and setup_03_prompts_on.json."""
    steps = [
        ("Install app by code", lambda: _install_app_by_code(driver, app_code)),
        ("Login (before release)", lambda: _login(driver, username, password)),
        ("Assert Start visible", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=15)),
        ("Logout", lambda: _logout(driver)),
        ("HQ: mark latest build Released",
         lambda: hq_client.mark_build_status(app_id, latest_build_id, is_released=True)),
        ("Login (expect optional prompt)", lambda: _login(driver, username, password)),
        ("Update via menu: confirm prompt, complete update", lambda: _complete_update_app(driver)),
        ("Relogin (confirm no more prompt)", lambda: _login(driver, username, password)),
        ("Assert no more prompt",
         lambda: h.assert_not_visible_text(driver, "New version of the application is available")),
        ("Assert Start visible", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=15)),
    ]
    return _run_steps(steps)


def run_prompted_update_scenario_2(driver, hq_client, app_id, latest_build_id, username, password, app_code):
    """Prompted Updates Scenario 2: forced CCZ update blocker screen. Port
    of flows/prompted_updates/scenario_02_forced_ccz_update.yaml. Same
    mid-session-HQ-action mechanism as scenario_1 above. ASSUMES Setup 2
    (mark latest build In Test + prior Released) AND the forced
    set_prompt_update_settings call have already run - both belong BEFORE
    this scenario's first login, i.e. before the Appium session even
    starts, so the caller (scripts/run_appium_prompted_updates_suite.py)
    runs them, not this function - see hq_setup/prompted_updates/
    setup_02_mark_in_test.json and scenario_02_forced.json."""
    steps = [
        ("Install app by code", lambda: _install_app_by_code(driver, app_code)),
        ("Login (before release, expect no blocker)", lambda: _login(driver, username, password)),
        ("Assert Start visible", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=15)),
        ("Assert no forced blocker yet",
         lambda: h.assert_not_visible_text(driver, "New version of the application is required")),
        ("Logout", lambda: _logout(driver)),
        ("HQ: mark latest build Released",
         lambda: hq_client.mark_build_status(app_id, latest_build_id, is_released=True)),
        ("Relogin until forced blocker appears",
         lambda: _login_and_wait_for_forced_blocker(driver, username, password)),
        ("Back press is a no-op in force mode", lambda: h.back(driver)),
        ("Blocker still visible after Back",
         lambda: h.assert_visible_text(driver, "New version of the application is required")),
        ("Tap Update to the Latest App Version",
         lambda: h.tap_by_text(driver, r"(?i)update to the latest app version", regex=True)),
        ("Wait for Update to version X & log out",
         lambda: h.wait_visible_text(driver, r"Update to version.*log out", timeout=30, regex=True)),
        ("Tap Update to version X & log out",
         lambda: h.tap_by_text(driver, r"Update to version.*log out", regex=True)),
        ("Relogin (confirm no more prompt)", lambda: _login(driver, username, password)),
        ("Assert no forced blocker",
         lambda: h.assert_not_visible_text(driver, "New version of the application is required")),
        ("Assert no optional prompt",
         lambda: h.assert_not_visible_text(driver, "New version of the application is available")),
        ("Assert Start visible", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=15)),
    ]
    return _run_steps(steps)
