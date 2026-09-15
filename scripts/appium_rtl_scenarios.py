"""
Master Mobile Plan (2026) > Right to Left Text > "Home Screen 8" (row 23)
and "Case list 4" (row 36).

Both rows were marked Not automatable for the same stated reason: this
app's own real Arabic translations mean English text selectors stop
matching once CommCare's in-app language is switched to Arabic (setup_03's
own "Choose your Language" mechanism) - and a prior Maestro build hit a
real Unicode round-trip bug even matching the Arabic text directly.

REAL FINDING (confirmed live, 2026-09-15) that resolves this cleanly:
CommCare's in-app language switch (setup_03) does NOT change Android's own
system-level layout direction at all - it only swaps which string
dictionary CommCare's OWN Localization system reads from. Switching it
alone left the home screen's tile grid at byte-identical pixel bounds to
English (no mirroring whatsoever), contradicting Home Screen 8's own
premise. What DOES cause real RTL layout mirroring is the device's actual
system locale (View.LAYOUT_DIRECTION_LOCALE) - set here via UiAutomator2's
standard `language`/`locale` capabilities (AppiumBrowserStackClient.
start_session(device_language="ar", device_locale="SA")) - confirmed live
this genuinely mirrors CommCare's home-screen RecyclerView tiles AND the
toolbar's own hamburger/overflow icons, all while leaving CommCare's OWN
string content (both its hardcoded framework strings like "Start"/"Sync
with Server"/"Log out of CommCare", AND this test app's own case-list
action-button label, confirmed live to be Arabic - "تسجيل" - unconditionally,
regardless of CommCare's in-app language setting) completely unaffected.

This means NEITHER row needs CommCare's own in-app language switch, or any
Arabic-text-matching selector, at all:
  - Home Screen 8's claim is pure tile POSITION (Sync left / Start+Logout
    right) - checked here via English text lookups ("Start"/"Sync with
    Server"/"Log out of CommCare" stay in English) plus numeric bounds
    comparison. No Unicode risk anywhere in this path.
  - Case list 4's own 3 sub-claims split cleanly on feasibility, confirmed
    live via reports/appium_failures/rtl_case_list_english.xml (English/LTR
    baseline) vs. rtl_case_list_arabic2.xml (device_language="ar"):
      1. "there is a button under the case list with Arabic text" -
         genuinely checkable (org.commcare.dalvik:id/text reads "تسجيل"
         unconditionally - checked here via an Arabic-Unicode-range regex
         match on the STRING VALUE, never as a selector/tap target, so the
         Maestro tool-chain's own text-matching bug never enters the
         picture).
      2. "the arrow on the button points left" - the chevron icon's own
         container (org.commcare.dalvik:id/right_aligned_container,
         confirmed live to hold org.commcare.dalvik:id/launch_action) sits
         at bounds x:[838,1018] (right edge, screen width 1080) under
         English/LTR and x:[62,242] (left edge) under device_language="ar"
         - a genuine, checkable element-position mirroring claim.
      3. "ensure the text is right aligned" - intra-widget text gravity,
         confirmed NOT automatable (no Maestro/Appium primitive compares a
         single element's own internal text alignment) - deliberately not
         asserted here, same as every other intra-widget-alignment gap
         already documented elsewhere in this tab (Question Types 1/3/7,
         Case list 2/3).
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, _run_steps, _install_app_by_code, _login
from appium.webdriver.common.appiumby import AppiumBy

_ARABIC_RE = re.compile(r"[؀-ۿ]")


def _bounds_x_range(element):
    bounds = element.get_attribute("bounds")  # e.g. "[838,2065][1018,2196]"
    nums = [int(n) for n in re.findall(r"-?\d+", bounds)]
    x1, _, x2, _ = nums
    return x1, x2


def _find_home_card_by_text(driver, text):
    cards = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/home_card")
    for card in cards:
        texts = card.find_elements(AppiumBy.ID, f"{APP_ID}:id/card_text")
        if texts and texts[0].get_attribute("text") == text:
            return card
    raise AssertionError(f"No home_card found with card_text {text!r}")


def _assert_home_tiles_mirrored(driver):
    start_card = _find_home_card_by_text(driver, "Start")
    logout_card = _find_home_card_by_text(driver, "Log out of CommCare")
    sync_card = _find_home_card_by_text(driver, "Sync with Server")

    start_x1, _ = _bounds_x_range(start_card)
    logout_x1, _ = _bounds_x_range(logout_card)
    sync_x1, _ = _bounds_x_range(sync_card)

    if not (sync_x1 < start_x1 and sync_x1 < logout_x1):
        raise AssertionError(
            f"Expected the RTL-mirrored layout to place 'Sync with Server' (x1={sync_x1}) "
            f"left of both 'Start' (x1={start_x1}) and 'Log out of CommCare' (x1={logout_x1}), "
            f"i.e. Sync on the left / Start+Logout on the right."
        )


def run_home_screen_8(driver, app_code, username, password):
    steps = [
        ("Install [Right to Left Tests!] app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Verify the home screen", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=20)),
        ("Verify home tiles are RTL-mirrored (Sync left, Start+Logout right)",
         lambda: _assert_home_tiles_mirrored(driver)),
    ]
    return _run_steps(steps)


def _navigate_to_case_list(driver):
    cards = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/card")
    if not cards:
        raise AssertionError("No home_card 'card' elements found to tap into Start")
    cards[0].click()
    rows_menu = h.wait_visible_id(driver, f"{APP_ID}:id/row_txt", timeout=15, optional=True)
    if not rows_menu:
        raise AssertionError("Menu list (row_txt) never appeared after tapping the first home tile")
    menu_rows = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/row_txt")
    if len(menu_rows) < 2:
        raise AssertionError(f"Expected at least 2 menu rows, found {len(menu_rows)}")
    menu_rows[1].click()
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_entity_select_list", timeout=20)


def _assert_case_list_action_button(driver):
    label_els = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/text")
    label_els = [e for e in label_els if e.get_attribute("text")]
    if not label_els:
        raise AssertionError("No case-list action button label (id=text) found")
    label_text = label_els[0].get_attribute("text")
    if not _ARABIC_RE.search(label_text):
        raise AssertionError(
            f"Expected the case-list action button's label to contain Arabic text, got {label_text!r}"
        )

    chevron_containers = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/right_aligned_container")
    if not chevron_containers:
        raise AssertionError("No right_aligned_container (chevron/arrow container) found on the action button")
    x1, x2 = _bounds_x_range(chevron_containers[0])
    screen_width = driver.get_window_size()["width"]
    if not (x2 <= screen_width / 2):
        raise AssertionError(
            f"Expected the action button's arrow container to sit in the LEFT half of the screen "
            f"under RTL (bounds x:[{x1},{x2}], screen width {screen_width}), i.e. mirrored from its "
            f"LTR right-aligned position."
        )


def run_case_list_4(driver, app_code, username, password):
    steps = [
        ("Install [Right to Left Tests!] app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Verify the home screen", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=20)),
        ("Navigate to the case list (Start -> 2nd menu row)", lambda: _navigate_to_case_list(driver)),
        ("Verify the action button has Arabic text and an RTL-mirrored arrow",
         lambda: _assert_case_list_action_button(driver)),
    ]
    return _run_steps(steps)
