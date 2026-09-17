"""
Master Mobile Plan (2026) > Form Submissions > "Casesearch Checkbox 1" (row
55) and "Casesearch Checkbox 2" (row 56) - adding a Case Search property in
checkbox/multi-select format against a lookup table, then confirming
multiple values can actually be selected and filtered on.

Both rows were Not automatable because configuring a Case Search property's
checkbox/multi-select format lives in HQ's App Builder Case List UI,
backed by the app's module/search_config structure - a materially more
complex write than the simple custom_properties key/value toggles
hq_client.py already wrapped (mark_build_status/set_custom_properties/
etc.).

RESOLVED (2026-09-16), per direct user instruction with exact App Builder
steps + screenshots: HQClient.set_module_search_properties() (see its own
citation for the real edit_module_detail_screens endpoint/shape, found by
reading commcare-hq source directly) adds a real "genre" Case Search
property in checkbox format against the "Genre" lookup table, on the
"[Mobile_Tests] Case Search and Claim" app's "Songs (See More)" module
(domain "casesearch", app_registry.py's CASE_SEARCH_AND_CLAIM).

Confirmed live this app is NOT exclusively edited by this program - a
colleague's own manual App Builder session left real, concurrent version-
history entries on this exact app around the same time this was built -
so every write here reads the module's CURRENT search_config fresh
(HQClient.get_module_search_config) rather than trusting a hardcoded
snapshot, both when ADDING the genre property (preserving whatever else is
currently configured) and when REVERTING (restoring exactly what was read
at the start of this same run, not a stale snapshot from whenever this
file was written).

Confirmed live end-to-end (BrowserStack session
47228751e72702f090c0f8b3775ef7bd221c2e53 navigation +
b0b51c0031a06edcb9c49c0c7a80d47d94856f5e for the multi-select query):
after adding the checkbox property and building a real app version from
it, "Songs (See More)"'s search form's Genre field renders as 4 real,
independently-clickable android.widget.CheckBox elements (Hip Hop, Latin
music, Metal, Pop music, from the real "Genre" lookup table) - selecting
TWO simultaneously showed both with checked="true" (genuine multi-select,
not mutually-exclusive radio behavior), and submitting the query returned
real matching results with no error.

The "SEARCH ALL CASES" button sits at the END of this module's case list -
confirmed live this case type has many existing cases, so reaching it
needs scrolling (a fixed tap-without-scroll attempt timed out - the button
simply wasn't rendered yet), unlike shorter case lists elsewhere in this
program that show it without scrolling.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, _run_steps, _install_app_by_code, _login
from appium.webdriver.common.appiumby import AppiumBy

GENRE_PROPERTY_NAME = "genre"
GENRE_LOOKUP_TABLE = "Genre"

GENRE_CHECKBOX_PROPERTY_INPUT = {
    "name": GENRE_PROPERTY_NAME,
    "label": "Genre",
    "hint": "",
    "appearance": "checkbox",
    "fixture": json.dumps({
        "instance_id": f"item-list:{GENRE_LOOKUP_TABLE}",
        "nodeset": f"instance('item-list:{GENRE_LOOKUP_TABLE}')/{GENRE_LOOKUP_TABLE}_list/{GENRE_LOOKUP_TABLE}",
        "label": "genre", "value": "genre", "sort": "id",
    }),
}


def _scroll_to_text(driver, text, max_swipes=15):
    for _ in range(max_swipes):
        if h.is_text_visible(driver, text):
            return True
        size = driver.get_window_size()
        driver.swipe(size["width"] // 2, int(size["height"] * 0.8), size["width"] // 2, int(size["height"] * 0.2), 400)
    return False


def _navigate_to_songs_see_more_search(driver):
    h.tap_by_text(driver, "Start", timeout=10)
    h.tap_by_text(driver, "Songs (See More)", timeout=10)
    if not _scroll_to_text(driver, "SEARCH ALL CASES"):
        raise AssertionError("Never scrolled to 'SEARCH ALL CASES' on Songs (See More)'s case list")
    h.tap_by_text(driver, "SEARCH ALL CASES", timeout=10)
    h.wait_visible_id(driver, f"{APP_ID}:id/prompt_label", timeout=15)


def _select_two_genre_checkboxes_and_query(driver):
    checkboxes = driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.CheckBox")
    genre_boxes = {cb.get_attribute("text"): cb for cb in checkboxes if cb.get_attribute("text")}
    if len(genre_boxes) < 2:
        raise AssertionError(
            f"Expected at least 2 Genre checkbox options, found {list(genre_boxes.keys())}"
        )
    names = list(genre_boxes.keys())[:2]
    for name in names:
        genre_boxes[name].click()

    still_checked = {cb.get_attribute("text"): cb.get_attribute("checked")
                     for cb in driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.CheckBox")
                     if cb.get_attribute("text") in names}
    if not all(v == "true" for v in still_checked.values()):
        raise AssertionError(
            f"Expected both {names} to be independently checked (multi-select), got {still_checked}"
        )

    h.tap_by_text(driver, "QUERY", timeout=10)
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_entity_select_list", timeout=20)


def run_casesearch_checkbox_2(driver, app_code, username, password):
    steps = [
        ("Install [Mobile_Tests] Case Search and Claim app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Navigate to Songs (See More) > Search All Cases", lambda: _navigate_to_songs_see_more_search(driver)),
        ("Select 2 Genre checkboxes and submit the query",
         lambda: _select_two_genre_checkboxes_and_query(driver)),
    ]
    return _run_steps(steps)
