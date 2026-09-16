"""
Master Mobile Plan (2026) > Case Filters > "Case Search & Claim 1" (row 19)
- was Not automatable per coverage_matrix.csv's own citation: "Different
app ('Case Search and Claim') not present in resources/ and not
independently confirmed - not guessed per this session's own rule against
unverified app names."

RESOLVED (2026-09-16), per a direct user-supplied app link: the "Case
Search and Claim" app genuinely exists - domain "casesearch", app_id
414ea62a6610470eb8582a13954515ea, app_registry.py's CASE_SEARCH_AND_CLAIM
(the same app scripts/appium_casesearch_scenarios.py's Casesearch Checkbox
1/2 also use, for an unrelated module).

MODULE PIVOT, per direct user instruction after live investigation: the
row's own steps reference "Songs (Search First)", which DOES have
date_opened configured, but real, extensive live testing (multiple
BrowserStack sessions, clearing every possible default filter one at a
time, and finally an entirely blank/unconstrained query) confirmed this
specific module's own Case Search config genuinely returns ZERO results
for the test1 account no matter what - not a scenario-code bug. A
side-by-side comparison confirmed "Songs (See More)" (the SAME app,
already used by Casesearch Checkbox 1/2 for an unrelated row) returns
real results even on a fully blank query ("Just5000Babe552"/06/01/26,
"Just5000Babe604"/10/06/24, among others) - proving the search mechanism
itself works fine and the problem was specific to "Search First"'s own
case type/data, not the app or account. Per direct user instruction, this
scenario now targets "Songs (See More)" instead, which also already has
date_opened configured as a daterange property and (confirmed live) no
problematic pre-filled default filter VALUES the way "Search First" did
(no "Default Song"/pre-set Mood/pre-checked Rating) - so no HQ config
change was needed to make this module work, unlike Casesearch Checkbox 1's
genuinely-missing Genre-checkbox property.

REAL ON-DEVICE BEHAVIOR CONFIRMED LIVE (2026-09-16): the Date Opened field
on the search form is a read-only-looking EditText (focusable="false") -
tapping it directly does nothing, and typing into it via send_keys() just
APPENDS to the existing text. The REAL interaction is the separate
calendar icon (org.commcare.dalvik:id/assist_view) sitting in the same
row - tapping THAT opens a genuine Material date-RANGE-picker dialog with
a "Switch to text input mode" toggle exposing two independent, id-less
EditText fields (distinguished by x-position: leftmost = start, rightmost
= end). Setting BOTH fields to the SAME date and tapping SAVE collapses
the outer Date Opened field to "<date> to <date>", the same observable
outcome the row's own "end date autopopulated" language describes.

A hardcoded search date can never be relied on to match real data that
could change over time (confirmed live this was the root cause of every
early failure against "Search First") - this scenario instead reads a
REAL date_opened value directly off an actually-listed case (via regex,
DD/MM/YYYY or DD/MM/YY display format) from a first, unconstrained
"SEARCH ALL CASES" query, then re-searches using that exact date -
guaranteed to match by construction, and self-adapting if the underlying
test data ever changes.

UPDATE (2026-09-16), reversed from an earlier version of this note: using
the Android system BACK action (h.back()) as a general-purpose "dismiss
whatever keyboard might be open" tool turned out to be unsafe here, not
reliable - confirmed live via failure screenshots/XML in TWO separate
spots (once between the results screen and re-opening the search form,
once inside the date-range picker right before tapping SAVE) that when NO
keyboard was actually open at the time, back() silently performed a REAL
navigation instead (dismissing the date-picker dialog entirely, or
returning to the case list), derailing the rest of the flow with no
exception raised until a much later step failed to find an element that
was simply no longer on screen. Both spots now either check for the
actual expected screen state after back() and recover if it landed
somewhere else, or (in the date picker) use h.hide_keyboard() - Appium's
dedicated hideKeyboard command, not a navigation action - instead of
back(), so a keyboard that's already closed is simply a harmless no-op
rather than a wrong navigation.
"""
import re
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, _run_steps, _install_app_by_code, _login
from appium.webdriver.common.appiumby import AppiumBy

_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{2,4})\b")


def _navigate_to_songs_see_more_search(driver):
    h.tap_by_text(driver, "Start", timeout=10)
    h.tap_by_text(driver, "Songs (See More)", timeout=10)
    if not _scroll_to_text(driver, "SEARCH ALL CASES"):
        raise AssertionError("Never scrolled to 'SEARCH ALL CASES' on Songs (See More)'s case list")
    h.tap_by_text(driver, "SEARCH ALL CASES", timeout=10)
    h.wait_visible_id(driver, f"{APP_ID}:id/prompt_label", timeout=15)


def _scroll_to_text(driver, text, max_swipes=15):
    for _ in range(max_swipes):
        if h.is_text_visible(driver, text):
            return True
        size = driver.get_window_size()
        driver.swipe(size["width"] // 2, int(size["height"] * 0.8), size["width"] // 2, int(size["height"] * 0.2), 400)
    return False


def _clear_all_filters(driver):
    for cb in driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.CheckBox"):
        if cb.get_attribute("checked") == "true":
            cb.click()
    for field in driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/prompt_et"):
        field.click()
        field.clear()
    h.back(driver)


def _submit_query(driver):
    btn = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/request_button")
    if not btn:
        raise AssertionError("No QUERY button (id=request_button) found")
    btn[0].click()


def _read_a_real_case_date_from_results(driver):
    """Reads one real date_opened value off an actually-returned result row
    (DD/MM/YYYY or DD/MM/YY display format) - see this module's own
    docstring for why a hardcoded date can't be trusted to match real,
    changeable test data."""
    if h.wait_visible_text(driver, "Query response had no results", timeout=5, optional=True):
        raise AssertionError(
            "An unconstrained ('blank') query returned no results at all on Songs (See More) - "
            "this module was previously confirmed to have real data; something has changed."
        )
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_entity_select_list", timeout=25)
    texts = h.all_visible_texts(driver)
    for text in texts:
        match = _DATE_RE.fullmatch(text.strip())
        if match:
            dd, mm, yy = match.groups()
            yyyy = yy if len(yy) == 4 else f"20{yy}"
            return {"iso": f"{yyyy}-{mm}-{dd}", "field_format": f"{int(mm)}/{int(dd)}/{yyyy}"}
    raise AssertionError(f"No real DD/MM/YYYY(YY) case date found in the query results. Visible texts: {texts}")


def _open_date_range_picker(driver):
    labels = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/prompt_label")
    date_label = next((l for l in labels if l.get_attribute("text") == "Date Opened"), None)
    if date_label is None:
        raise AssertionError("No 'Date Opened' prompt_label found on the search form")
    label_rect = date_label.rect
    icons = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/assist_view")
    target = next((i for i in icons if abs(i.rect["y"] - label_rect["y"]) < 100), None)
    if target is None:
        raise AssertionError("No calendar icon (assist_view) found next to the 'Date Opened' label")
    target.click()


def _enter_single_date_in_picker(driver, field_format_date):
    h.tap_by_text(driver, "Switch to text input mode", timeout=10)
    time.sleep(1)
    edits = sorted(driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText"), key=lambda e: e.rect["x"])
    if len(edits) < 2:
        raise AssertionError(f"Expected 2 date EditText fields in the picker, found {len(edits)}")
    start_field, end_field = edits[0], edits[1]
    for field in (start_field, end_field):
        field.click()
        field.clear()
        field.send_keys(field_format_date)
    # UPDATE (2026-09-16), confirmed live via failure screenshot/XML: the
    # unconditional back() here (originally added "to dismiss the keyboard
    # before tapping SAVE") had the exact same failure mode already fixed
    # once in _submit_query_and_verify_real_results - if the keyboard had
    # already auto-closed after send_keys filled the date field, back()
    # performed a REAL dialog-dismiss instead of a keyboard-dismiss,
    # silently dropping the whole date-picker dialog and eventually
    # derailing the flow back to the case list. A follow-up attempt to
    # gate this on driver.is_keyboard_shown() failed outright - confirmed
    # live this driver's UiAutomator2 build doesn't support the underlying
    # "mobile: isKeyboardShown" command at all (UnknownCommandError, same
    # class of driver whitelist gap as "mobile: shell"/"mobile: delete"
    # noted elsewhere in this repo). h.hide_keyboard() (Appium's dedicated
    # hideKeyboard command, NOT a back()/navigation action) can't cause the
    # same wrong-navigation side effect even as a no-op when no keyboard is
    # open - the worst case is the keyboard staying up, which the
    # subsequent tap_by_text("SAVE") would then simply fail/time out on
    # (a detectable failure) rather than silently derailing elsewhere.
    h.hide_keyboard(driver)
    h.tap_by_text(driver, "SAVE", timeout=10)


def _assert_date_field_shows_single_day_range(driver, iso_date):
    expected = f"{iso_date} to {iso_date}"
    if not h.is_text_visible(driver, expected):
        raise AssertionError(
            f"Expected the Date Opened field to read {expected!r} after entering a single date in both "
            f"start/end fields (i.e. the end date mirrors the start), but it didn't."
        )


def _submit_query_and_verify_real_results(driver):
    # UPDATE (2026-09-16), confirmed live: a back() call here "to dismiss
    # any lingering keyboard" was itself the bug once the keyboard was
    # ALREADY closed (from _enter_single_date_in_picker's own back() before
    # SAVE) - with no keyboard open, back() performs a REAL navigation
    # instead, landing on the case list and making the QUERY button
    # genuinely absent. No keyboard-dismissal needed here at all.
    _submit_query(driver)
    if h.wait_visible_text(driver, "Query response had no results", timeout=5, optional=True):
        raise AssertionError(
            "Query returned no results even for a date read directly off a real, currently-listed case - "
            "the search itself may be broken, not just a bad test date."
        )
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_entity_select_list", timeout=25)


def run_case_search_claim_1(driver, app_code, username, password):
    real_date = {}

    def _capture_date():
        _clear_all_filters(driver)
        _submit_query(driver)
        real_date.update(_read_a_real_case_date_from_results(driver))

    def _return_to_search_form():
        # UPDATE (2026-09-16), confirmed live FOUR times before landing on
        # this: (1) there's no "Search All Cases" text on the results
        # screen; (2) its toolbar magnifying-glass icon (content-desc=
        # "Search") opens an unrelated in-list text-filter box; (3) a
        # single back() from results is NOT deterministic - one real run
        # landed on the ORIGINAL case list (skipping the form entirely),
        # another real run landed directly back on the blank search form -
        # same code path, different outcome, presumably an Android
        # transition-timing race rather than a real state difference.
        # Handles BOTH: if the form's own prompt_label is already visible,
        # nothing more to do; otherwise (landed on the case list) re-scroll
        # to and tap "SEARCH ALL CASES" same as the initial entry.
        h.back(driver)
        time.sleep(1)
        if h.wait_visible_id(driver, f"{APP_ID}:id/prompt_label", timeout=3, optional=True):
            return
        if not _scroll_to_text(driver, "SEARCH ALL CASES"):
            raise AssertionError(
                "After back() from results, landed on neither the search form nor a screen with "
                "'SEARCH ALL CASES' to re-enter it"
            )
        h.tap_by_text(driver, "SEARCH ALL CASES", timeout=10)
        h.wait_visible_id(driver, f"{APP_ID}:id/prompt_label", timeout=15)

    steps = [
        ("Install [Mobile_Tests] Case Search and Claim app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Navigate to Songs (See More) > Search All Cases",
         lambda: _navigate_to_songs_see_more_search(driver)),
        ("Run a blank query and read a real case's Date Opened value", _capture_date),
        ("Return to the case list and re-open Search All Cases", _return_to_search_form),
        ("Open the Date Opened calendar widget", lambda: _open_date_range_picker(driver)),
        ("Enter that real date manually (switch to text input mode)",
         lambda: _enter_single_date_in_picker(driver, real_date["field_format"])),
        ("Verify the entered date is reflected with the end date autopopulated",
         lambda: _assert_date_field_shows_single_day_range(driver, real_date["iso"])),
        ("Submit the query and verify real matching results load",
         lambda: _submit_query_and_verify_real_results(driver)),
    ]
    return _run_steps(steps)
