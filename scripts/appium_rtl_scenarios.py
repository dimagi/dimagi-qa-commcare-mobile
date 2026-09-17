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

"Home Screen 1" (row 16) - same device-locale mechanism, a different
element-position claim: the toolbar's own nav-drawer icon, title, and
overflow icon. Confirmed live (2026-09-15, reports/appium_failures/
rtl_home_english.xml vs. device_locale_ar_home2.xml) these mirror exactly
like Home Screen 8's tiles do - nav-drawer icon x:[0,168] (left) under LTR
-> x:[912,1080] (right) under RTL; overflow icon x:[960,1080] (right) ->
x:[0,120] (left, confirmed again on the login screen too, reports/
appium_failures/rtl_login_screen_device_ar.xml); title x:[216,724]
(left-of-center) -> x:[356,864] (right-of-center) - and critically, the
GAP between the title's edge and the nav-drawer icon's adjacent edge is
identically ~48px in both directions (216-168=48 under LTR, 912-864=48
under RTL), confirming genuine mirrored anchoring, not just an incidental
shift. All identified by class name scoped to the toolbar's own stable
resource-id (org.commcare.dalvik:id/toolbar) - never by content-desc text,
which itself changes under a real device-locale change (confirmed live:
"More options" becomes "مزيد من الخيارات") the same way CommCare's own
strings don't.

NOT implemented here: "Login 1" (row 12) makes the same toolbar-position
claim but with the overflow icon's expected side reversed ("right corner")
- confirmed live this contradicts the real, correct RTL behavior (the
login screen's own toolbar mirrors identically to the home screen's,
overflow icon on the LEFT under RTL, not the right). This reads as an
error in the sheet's own wording rather than a genuine product
inconsistency, since both screens behave identically and only Login 1's
literal text disagrees - flagged for the user rather than encoded as a
wrong assertion.

"Question Types 1" (row 25) / "Question Types 3" (row 27) - same partial-
coverage precedent as Case list 4 above (2 of the row's own claims tractable,
1 an already-conclusively-established intra-widget/animation-direction gap
left as a documented note in the SAME cell, not re-investigated here):

  - Question Types 1's "row_img/row_txt order" half: on the Start screen's
    menu list (the SAME row_txt element case_list_01/question_types_02's own
    Maestro flows already tap into, confirmed live 2026-09-16 this app's
    real list under the "Survey"/"Case List" app-registry app has exactly 2
    rows - "Survey" (a form, index 0) and "Case List" (a module, index 1)),
    row_img sits at bounds x:[882,1056] and its paired row_txt at
    x:[72,834] (screen width 1080) under device_language="ar" - icon
    genuinely to the RIGHT of the text, matching this row's own claim and
    the SAME element-to-element position category (not intra-widget) as
    Case list 4's own arrow-container check. The sheet's other half ("text
    right aligned within its box") stays the already-established Not
    automatable intra-widget-gravity gap (E25's own citation, confirmed via
    menu_list_item_modern.xml/MenuAdapter.java source) - not re-derived
    here.
  - Question Types 3's "swipe direction inversion" half: a PRIOR Maestro
    attempt (see flows/right_to_left_text/question_types_02_open_first_form.yaml's
    own header) concluded swiping had "NO observable effect" on this form,
    checked via a resource id "nav_btn_back" - confirmed live here that id
    does not exist in this app at all (the real id is nav_btn_prev), so that
    "no effect" reading was a false negative from a wrong/nonexistent
    selector, not real evidence against swipe navigation. Re-tested live
    (2026-09-16) with a SAFE, non-edge-hugging swipe (30%-70% of screen
    width, well clear of Android's own ~10% system-gesture edge zones - an
    EARLIER edge-to-edge 85%-15% attempt instead triggered Android's own
    system back gesture, landing on CommCare's "Exit Form?" dialog, a false
    positive for a totally different reason) on the Survey form's first real
    question ("This question should let you enter any form of text...",
    reached via one nav_btn_next tap past the form's group-header intro
    screen): a swipe with the finger moving RIGHT-TO-LEFT genuinely
    navigates BACKWARD (the group-header screen reappears), and a swipe
    with the finger moving LEFT-TO-RIGHT genuinely navigates FORWARD (the
    question screen reappears) - a real, working, and objectively checkable
    (via the visible question text changing) swipe-navigation gesture, and
    its forward direction is exactly "left to right", matching this row's
    own literal wording ("swiping between question is invervsed (left to
    right)"). The row's OTHER half (progress-bar fill direction, an
    animation/rendering direction sampled over time) stays the already-
    established Not automatable gap (E27's own citation) - not
    re-investigated here.
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


def _toolbar_children_by_class(driver, class_name):
    toolbar = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/toolbar")
    if not toolbar:
        raise AssertionError("No toolbar (id=toolbar) found")
    return toolbar[0].find_elements(AppiumBy.CLASS_NAME, class_name)


def _assert_home_toolbar_mirrored(driver):
    drawer_icons = _toolbar_children_by_class(driver, "android.widget.ImageButton")
    if not drawer_icons:
        raise AssertionError("No navigation-drawer ImageButton found in the toolbar")
    title_views = _toolbar_children_by_class(driver, "android.widget.TextView")
    if not title_views:
        raise AssertionError("No title TextView found in the toolbar")
    overflow_containers = _toolbar_children_by_class(driver, "androidx.appcompat.widget.LinearLayoutCompat")
    if not overflow_containers:
        raise AssertionError("No overflow-menu container found in the toolbar")

    drawer_x1, drawer_x2 = _bounds_x_range(drawer_icons[0])
    title_x1, title_x2 = _bounds_x_range(title_views[0])
    overflow_x1, overflow_x2 = _bounds_x_range(overflow_containers[0])
    screen_width = driver.get_window_size()["width"]
    mid = screen_width / 2

    if not (drawer_x1 > mid):
        raise AssertionError(
            f"Expected the nav-drawer icon to be mirrored to the RIGHT half under RTL "
            f"(bounds x:[{drawer_x1},{drawer_x2}], screen width {screen_width})."
        )
    if not (overflow_x2 < mid):
        raise AssertionError(
            f"Expected the overflow-menu icon to be mirrored to the LEFT half under RTL "
            f"(bounds x:[{overflow_x1},{overflow_x2}], screen width {screen_width})."
        )
    # UPDATE (2026-09-15), confirmed live: a first attempt asserted the
    # title's own LEFT edge sat right-of-center - failed live for THIS app's
    # longer title ("Right to Left Tests!", bounds x:[356,864]) even though
    # the mirroring was genuinely correct, because a longer title naturally
    # extends further left from a fixed anchor point. Confirmed via a
    # second real screen (the login screen's shorter "CommCare" title,
    # bounds x:[563,864]) that the title's RIGHT edge - not its left, and
    # not "right of center" - is the real invariant: both screens' titles
    # share the identical right edge (864) and the identical ~48px gap to
    # the drawer icon's own left edge (912-864=48, matching the home
    # screen's un-mirrored 216-168=48 gap exactly) - the title is anchored
    # adjacent to the drawer icon, wherever that icon currently sits, not
    # pinned to an absolute "right half" threshold that only holds for
    # short strings.
    gap = drawer_x1 - title_x2
    if not (0 <= gap <= 100):
        raise AssertionError(
            f"Expected the app title's right edge to sit close to the (RTL-mirrored) nav-drawer "
            f"icon's left edge (title x2={title_x2}, drawer x1={drawer_x1}, gap={gap})."
        )


def run_home_screen_1(driver, app_code, username, password):
    steps = [
        ("Install [Right to Left Tests!] app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Verify the home screen", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=20)),
        ("Verify the toolbar is RTL-mirrored (title right, options button left)",
         lambda: _assert_home_toolbar_mirrored(driver)),
    ]
    return _run_steps(steps)


def _open_start_menu(driver):
    cards = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/card")
    if not cards:
        raise AssertionError("No home_card 'card' elements found to tap into Start")
    cards[0].click()
    h.wait_visible_id(driver, f"{APP_ID}:id/row_txt", timeout=15)


def _assert_menu_rows_icon_right_of_text(driver):
    row_txts = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/row_txt")
    row_imgs = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/row_img")
    if not row_txts or not row_imgs:
        raise AssertionError(f"Expected row_txt/row_img menu rows, found {len(row_txts)}/{len(row_imgs)}")
    if len(row_txts) != len(row_imgs):
        raise AssertionError(f"row_txt count ({len(row_txts)}) != row_img count ({len(row_imgs)})")
    for i, (txt_el, img_el) in enumerate(zip(row_txts, row_imgs)):
        txt_x1, txt_x2 = _bounds_x_range(txt_el)
        img_x1, img_x2 = _bounds_x_range(img_el)
        if not (img_x1 >= txt_x2):
            raise AssertionError(
                f"Expected row {i}'s icon (row_img, bounds x:[{img_x1},{img_x2}]) to sit to the RIGHT of "
                f"its text (row_txt, bounds x:[{txt_x1},{txt_x2}]) under RTL - i.e. the reverse of the "
                f"normal LTR icon-then-text order."
            )


def run_question_types_1(driver, app_code, username, password):
    steps = [
        ("Install [Right to Left Tests!] app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Verify the home screen", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=20)),
        ("Tap the green Start button", lambda: _open_start_menu(driver)),
        ("Verify each form/menu row's icon sits to the right of its text (RTL-mirrored order)",
         lambda: _assert_menu_rows_icon_right_of_text(driver)),
    ]
    return _run_steps(steps)


def _open_first_form_question(driver):
    """Opens the Start screen's first row (a form, "Survey" - same row
    question_types_02_open_first_form.yaml's own Maestro flow opens), then
    taps nav_btn_next once to move past the form's group-header intro
    screen onto its first REAL (answerable) question - the group-header
    screen has no distinguishing question text of its own to swipe
    between, only the form's static instructions."""
    row_txts = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/row_txt")
    if not row_txts:
        raise AssertionError("No row_txt menu rows found to open the first form")
    row_txts[0].click()
    h.wait_visible_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=15)
    next_btns = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/nav_btn_next")
    if not next_btns:
        raise AssertionError("nav_btn_next not found on the form's group-header screen")
    next_btns[0].click()
    # UPDATE (2026-09-16), confirmed live: wait_visible_text/is_text_visible
    # default to an EXACT string match (regex=False -> text == pattern), not
    # a substring/contains check - a first attempt passing a prefix of the
    # real on-screen sentence ("This question should let you enter any form
    # of text" vs the real, longer "...text or special characters. Try
    # different values.") silently never matched even though the real text
    # WAS on screen the whole time (confirmed via the failure XML dump) -
    # regex=True (re.search, not re.fullmatch) makes a prefix substring
    # match correctly.
    h.wait_visible_text(driver, "This question should let you enter any form of text", timeout=10, regex=True)


# A swipe kept well clear of Android's own ~10% system-gesture edge zones -
# an earlier edge-to-edge (85%-15% of screen width) attempt instead
# triggered Android's OWN system back gesture (landed on CommCare's "Exit
# Form?" dialog, a false positive unrelated to CommCare's in-app swipe
# handling), confirmed live 2026-09-16. See this module's own docstring
# UPDATE for the full citation.
def _swipe_form(driver, direction):
    size = driver.get_window_size()
    w, height = size["width"], size["height"]
    mid_y = int(height * 0.45)
    x_from, x_to = (int(w * 0.70), int(w * 0.30)) if direction == "left" else (int(w * 0.30), int(w * 0.70))
    driver.swipe(x_from, mid_y, x_to, mid_y, 500)


def _assert_swipe_navigates_question(driver):
    # Starting on the first real question ("This question should let you
    # enter any form of text...", reached by _open_first_form_question).
    # A RIGHT-TO-LEFT finger swipe ("left" direction) is expected to
    # navigate BACKWARD to the form's group-header screen - confirmed live
    # this is the real behavior on this form.
    _swipe_form(driver, "left")
    if not h.wait_visible_text(
        driver, "The following questions will go over basic question types", timeout=10, regex=True, optional=True
    ):
        raise AssertionError(
            "Expected a right-to-left swipe to navigate BACKWARD to the form's group-header screen - "
            "it didn't reappear within 10s."
        )

    # A LEFT-TO-RIGHT finger swipe ("right" direction) is expected to
    # navigate FORWARD again, back onto the question screen - this is the
    # row's own literal claim: the form's forward-swipe direction is
    # "left to right" (the RTL-inverted direction vs. a normal LTR form's
    # usual right-to-left "swipe to advance" convention).
    _swipe_form(driver, "right")
    if not h.wait_visible_text(
        driver, "This question should let you enter any form of text", timeout=10, regex=True, optional=True
    ):
        raise AssertionError(
            "Expected a left-to-right swipe to navigate FORWARD back onto the question screen - "
            "it didn't reappear within 10s."
        )


def run_question_types_3(driver, app_code, username, password):
    steps = [
        ("Install [Right to Left Tests!] app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Verify the home screen", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/home_gridview_buttons", timeout=20)),
        ("Tap the green Start button", lambda: _open_start_menu(driver)),
        ("Open the first form, advance past its group-header screen",
         lambda: _open_first_form_question(driver)),
        ("Verify swipe direction is RTL-inverted (left-to-right swipe advances forward)",
         lambda: _assert_swipe_navigates_question(driver)),
    ]
    return _run_steps(steps)
