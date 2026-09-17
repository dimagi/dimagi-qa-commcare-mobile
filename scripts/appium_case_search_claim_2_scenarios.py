"""
Master Mobile Plan (2026) > Case Filters > "Case Search & Claim 2" (row 20)
- "Date Widget - Single Date". Was "Not automatable" per coverage_matrix.csv's
own E20 citation ("Step 6 explicitly requires verifying functionality 'on
webapps' - a different platform/surface than this repo's mobile-only Maestro
framework covers").

RESOLVED to PARTIAL (2026-09-16), same app as Case Search & Claim 1 (row 19,
scripts/appium_case_search_claim_scenarios.py) - domain "casesearch", app_id
414ea62a6610470eb8582a13954515ea, app_registry.py's CASE_SEARCH_AND_CLAIM.
This row's own steps 1-3 ("Open Songs (Search First) > Select a song > Open
Shows (note a show date)") are a real, live-confirmed navigation path.

MODULE-NAME COLLISION AVOIDED, confirmed live via HQClient.get_module_search_
config against the app's own /apps/source/ JSON: this app has TWO modules
both literally named "Shows" - a "basic" module (unique_id ca2cb971916b6853
80459ba183ac2ce0f8e1b76b, root_module_id = Songs (Search First), reached by
selecting a song then tapping CONTINUE) and a separate "shadow" module
(rooted under "Shadow Menu" instead). Navigating via THIS row's own literal
steps (Songs (Search First) -> select a song -> CONTINUE -> "Shows") lands
on the basic module.

BLOCKER CONFIRMED LIVE (across multiple BrowserStack sessions on 2026-09-16
and 2026-09-17): the basic Shows module's own "Show Date" property
(when_is_the_show, input_="date", a genuine SINGLE date) NEVER renders as a
query prompt on-device at all - a real, persistent UI rendering gap for the
single-date search-prompt widget on this app's currently installed
CommCare client (of the date/daterange search properties across this
app's modules, this was the only input_="date" one - every other date
field uses the working input_="daterange" widget, including Case Search &
Claim 1's own "Date Opened" field). The property is still present in the
module's own search_config (confirmed live via HQClient.get_module_search_
config: when_is_the_show, label "Show Date", default_value still the same
quoted-string literal "2022-03-03") but genuinely does not render.

RESOLVED (2026-09-17), per direct user instruction after reviewing a real
device recording: a NEW property was added to this same module - "Date
Opened" (date-opened, input_="daterange", the SAME kind of widget Case
Search & Claim 1's own field uses) - confirmed live it renders correctly
and is fully functional. Per direct user instruction, this scenario uses
THIS field (not literally named "Show Date", but the row's own practical
"search by date" intent) the same way Case Search & Claim 1 uses its own
Date Opened field: enters today's date in the calendar picker's start/end
fields, verifies the field collapses to "<date> to <date>", submits the
query, and accepts EITHER a real results screen OR a clean "Query response
had no results" toast as a pass (default filters commonly cause zero
matches, same established philosophy as Case Search & Claim 1). Step 6
("functional on webapps") remains out of scope for this mobile-only repo;
step 7 (verifying the ORIGINAL "Show Date" widget's calendar/trash/close
icon behavior) remains not automatable since that specific field still
does not render - the Date Opened field's own calendar widget mechanics
are exercised instead as the practical equivalent.

Per Case Search & Claim 1's own hard-won lesson, h.hide_keyboard() (Appium's
dedicated hideKeyboard command) is used instead of h.back() for any
"dismiss whatever's open" need in this module - h.back() performs a REAL
navigation when no keyboard is actually open, not a safe no-op.

Two real bugs were found and fixed while building this scenario against
this app's recent updates: (1) appium_helpers.all_visible_texts() only
collects NON-EMPTY text/content-desc attributes, so any list-reading code
needs to query real elements (find_elements), not a text-presence scan,
wherever blank cells are possible. (2) A relative XPath queried FROM a
found WebElement (element.find_elements(XPATH, "./...")) returned zero
matches on this driver even when the same rows were genuinely present and
visible - an absolute XPath scoped by resource-id, queried from `driver`
itself, is used instead wherever this scenario needs to read a case list's
own row structure.
"""
import datetime
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, _run_steps, _install_app_by_code, _login
from appium_case_search_claim_scenarios import (
    _open_date_range_picker,
    _enter_single_date_in_picker,
    _assert_date_field_shows_single_day_range,
    _submit_query_and_accept_either_outcome,
)
from appium.webdriver.common.appiumby import AppiumBy

# The Songs (Search First) case list's own fixed column headers (confirmed
# live) - real song rows follow immediately after these 6 header texts, so
# skipping past them (rather than hardcoding a specific song's name, which
# is real, changeable test data) picks whichever song is actually first.
_SONGS_LIST_HEADERS = ["Name", "Artist", "Mood", "Rating", "Play Count", "Date Opened"]
_SONGS_LIST_ROW_WIDTH = len(_SONGS_LIST_HEADERS)


def _navigate_to_songs_search_first(driver):
    h.tap_by_text(driver, "Start", timeout=10)
    h.tap_by_text(driver, "Songs (Search First)", timeout=10)
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_entity_select_list", timeout=15)


def _select_first_song(driver, timeout=15, poll=0.5):
    """Picks whichever song is FIRST in the already-synced on-device list
    (real, changeable test data). Reads the list STRUCTURALLY (by element
    bounds/resource-id) rather than a flat text-list scan, since a blank
    cell in any OTHER column (e.g. Play Count) would otherwise silently
    misalign a naive 'skip N header texts' parse. Returns the song's Name
    for logging.

    Polls for up to `timeout` seconds rather than a single snapshot -
    confirmed live via a real failure screenshot that a one-shot scan right
    after the list widget appears can run before its row DATA has actually
    finished populating (a lazy/async-loaded ListView) even though the
    widget container itself is already present."""
    deadline = time.monotonic() + timeout
    last_header_texts = None
    while time.monotonic() < deadline:
        list_els = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/screen_entity_select_list")
        if not list_els:
            time.sleep(poll)
            continue
        header_els = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/entity_view_text")
        header_texts = [e.get_attribute("text") for e in header_els[:_SONGS_LIST_ROW_WIDTH]]
        last_header_texts = header_texts
        if header_texts != _SONGS_LIST_HEADERS:
            time.sleep(poll)
            continue
        # Absolute XPath scoped by resource-id, queried from `driver` - a
        # relative XPath queried from the found list WebElement returned
        # zero matches on this driver even with real rows present, see this
        # module's own docstring.
        row_els = driver.find_elements(
            AppiumBy.XPATH, f"//*[@resource-id='{APP_ID}:id/screen_entity_select_list']/android.widget.LinearLayout"
        )
        for row_el in row_els:
            cells = row_el.find_elements(AppiumBy.ID, f"{APP_ID}:id/entity_view_text")
            if len(cells) != _SONGS_LIST_ROW_WIDTH:
                continue
            cells = sorted(cells, key=lambda c: c.rect["x"])
            name_cell = cells[_SONGS_LIST_HEADERS.index("Name")]
            song_name = name_cell.get_attribute("text") or ""
            if song_name:
                h.tap_by_text(driver, song_name, timeout=10)
                h.wait_visible_text(driver, "CONTINUE", timeout=15)
                return song_name
        time.sleep(poll)
    if last_header_texts is not None and last_header_texts != _SONGS_LIST_HEADERS:
        raise AssertionError(
            f"Songs (Search First) list headers changed from what was confirmed live - "
            f"expected {_SONGS_LIST_HEADERS}, got {last_header_texts}"
        )
    raise AssertionError(
        f"Songs (Search First) list has no real song rows to select from after polling {timeout}s"
    )


def _continue_to_shows(driver):
    h.tap_by_text(driver, "CONTINUE", timeout=10)
    h.wait_visible_text(driver, "Shows", timeout=10)
    h.tap_by_text(driver, "Shows", timeout=10)
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_entity_select_list", timeout=15)


def _scroll_to_text(driver, text, max_swipes=15):
    for _ in range(max_swipes):
        if h.is_text_visible(driver, text):
            return True
        size = driver.get_window_size()
        driver.swipe(size["width"] // 2, int(size["height"] * 0.8), size["width"] // 2, int(size["height"] * 0.2), 400)
    return False


def _open_search_form(driver):
    """Step 4: 'Search Again' - opens the Shows module's own search form
    from its case list (the same list _continue_to_shows lands on).
    "SEARCH ALL CASES" may need scrolling into view first."""
    if h.is_text_visible(driver, "SEARCH ALL CASES"):
        h.tap_by_text(driver, "SEARCH ALL CASES", timeout=10)
    elif _scroll_to_text(driver, "SEARCH ALL CASES"):
        h.tap_by_text(driver, "SEARCH ALL CASES", timeout=10)
    else:
        raise AssertionError("Never found 'SEARCH ALL CASES' on the Shows case list")
    h.wait_visible_id(driver, f"{APP_ID}:id/prompt_label", timeout=15)


def _assert_date_opened_field_present(driver):
    labels = driver.find_elements(AppiumBy.ID, f"{APP_ID}:id/prompt_label")
    label_texts = [l.get_attribute("text") for l in labels]
    if "Date Opened" not in label_texts:
        raise AssertionError(f"'Date Opened' search field not found on the Shows module's search form - "
                              f"visible fields: {label_texts}")


def run_case_search_claim_2(driver, app_code, username, password):
    today = datetime.date.today()
    iso_date = today.isoformat()
    captured = {}

    def _select_and_open_shows():
        captured["song"] = _select_first_song(driver)
        _continue_to_shows(driver)

    steps = [
        ("Install [Mobile_Tests] Case Search and Claim app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Open Songs (Search First)", lambda: _navigate_to_songs_search_first(driver)),
        ("Select a song and open Shows", _select_and_open_shows),
        ("Search Again / open the Shows search form", lambda: _open_search_form(driver)),
        ("Verify the Date Opened search field is present", lambda: _assert_date_opened_field_present(driver)),
        ("Open the Date Opened calendar widget", lambda: _open_date_range_picker(driver)),
        ("Enter today's date manually (switch to text input mode)",
         lambda: _enter_single_date_in_picker(driver, today.month, today.day, today.year)),
        ("Verify the entered date is reflected with the end date autopopulated",
         lambda: _assert_date_field_shows_single_day_range(driver, iso_date)),
        ("Submit the query and accept either a results screen or a clean 'no results' outcome",
         lambda: captured.update(result=_submit_query_and_accept_either_outcome(driver))),
    ]
    completed = _run_steps(steps)
    return completed, captured
