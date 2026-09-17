"""
Master Mobile Plan (2026) > Form Submissions > "Geoservice 1" (row 46,
"Default Map Tileset") - see coverage/coverage_matrix.csv's own Geoservice 1
row and docs/[Master] Mobile Plan (2026).xlsx's Form Submissions E46 cell for
the full prior investigation this builds on. That investigation established
(NOT re-derived here):

    (1) cc-maps-default-layer is a real, named CommCare Profile Setting
        under app.profile.properties (NOT profile.custom_properties) -
        written via hq_client.py's new set_app_properties() (a per-key
        MERGE into profile.properties, confirmed live 2026-09-17 - see that
        method's own docstring).
    (2) On-device, NOTHING observable (no accessibility-tree text/
        resource-id/content-desc, no readable log, no accessible
        SharedPreferences on this BrowserStack tier since adb_shell is
        disabled) exposes which tileset is currently active -
        GoogleMap.setMapType() is a pure pixel-rendering call with no
        exposed signal (commcare-android: GeoPointMapActivity.java/
        EntityMapActivity.java). This module does NOT attempt to assert
        which tileset rendered - only that the map-bearing form loads
        without crashing under a given tileset config, a real, checkable
        regression signal distinct from the (unautomatable) visual claim.

APP + FORM, confirmed live 2026-09-17 (NOT assumed identical to the main
Basic Tests app just because BASIC_TESTS_NS_COPY is a copy of it - the app
source was actually fetched and diffed): BASIC_TESTS_NS_COPY has NOT
drifted on this specific form. Its module 0 ("Basic_Form_Tests") form 12 is
"[Mobile] Appearance Attributes" (module_unique_id
6b5c806b9bde7eba811fea0e442c21b8bf300322, form_unique_id
28a1564a3b1f4d8bbd6deff6e76f5f46), and a fresh CCZ download + extraction
confirmed modules-0/forms-12.xml has a real
`<input appearance="maps">` geopoint question, label text "This question
will allow you to record your location on Google Maps. To do so, press
'Record Location.' Then, verify you're able to see your recorded location
in Google Maps by pressing 'Show Location.'" - the SAME modules-0/forms-12
location cited for the main Basic Tests app in this row's own prior
citation, exactly matching the CCZ layout (module 0 = first module, form 12
= 13th form in that module's list). The app's own "Geoservices " module
(case_type=geo_case) has "Register Case"/"Rate Case" forms instead - no
maps appearance question there (confirmed via CCZ text search across every
modules-*/forms-*.xml) - so "[Mobile] Appearance Attributes" is the one
real map-bearing form on this app, not a second option.

The form's OWN group-header intro screen ("OK. Please continue.") and its
per-question navigation (nav_btn_next, per this repo's own established
citation in appium_scenarios.py/appium_rtl_scenarios.py/several flows/
common/*.yaml headers - FormNavigationUI.java's real resource id) are
reused unchanged - no new navigation primitive was needed here.
"""
import time

import appium_helpers as h
from appium_scenarios import APP_ID, _run_steps, _install_app_by_code, _login
from appium.webdriver.common.appiumby import AppiumBy

MAPS_QUESTION_TEXT_RE = r"record your location on Google Maps"
MAX_FORM_PAGES = 30
_CRASH_TEXT_RE = r"(has stopped|isn't responding|keeps stopping)"


def _scroll_to_text(driver, text, max_swipes=15):
    for _ in range(max_swipes):
        if h.is_text_visible(driver, text):
            return True
        size = driver.get_window_size()
        driver.swipe(size["width"] // 2, int(size["height"] * 0.8), size["width"] // 2, int(size["height"] * 0.2), 400)
    return False


def _navigate_to_appearance_attributes_form(driver):
    h.tap_by_text(driver, "Start", timeout=15)
    h.tap_by_text(driver, "Basic_Form_Tests", timeout=15)
    if not _scroll_to_text(driver, "[Mobile] Appearance Attributes"):
        raise AssertionError(
            "Never scrolled to '[Mobile] Appearance Attributes' under Basic_Form_Tests - "
            "this app's module/form layout may have drifted, see this module's own docstring."
        )
    h.tap_by_text(driver, "Appearance Attributes", timeout=15, regex=True)
    # The form opens on a group-header intro screen ("English - The following
    # questions will go over basic question types..." / "OK. Please continue.")
    # - dismiss it if present, optional since a re-entered/resumed form could
    # land past it.
    h.tap_by_text(driver, "OK. Please continue.", timeout=5, optional=True)


def _page_to_maps_question(driver):
    for _ in range(MAX_FORM_PAGES):
        if h.is_text_visible(driver, MAPS_QUESTION_TEXT_RE, regex=True):
            return
        if not h.tap_by_id(driver, f"{APP_ID}:id/nav_btn_next", timeout=3, optional=True):
            break
    raise AssertionError(
        f"Never reached the maps geopoint question (pattern {MAPS_QUESTION_TEXT_RE!r}) within "
        f"{MAX_FORM_PAGES} nav_btn_next taps - either the form's question order changed, or "
        f"nav_btn_next stopped being present before reaching it."
    )


def _open_map_and_verify_no_crash(driver):
    # UPDATE (2026-09-17), confirmed live: the on-screen button renders as
    # "RECORD LOCATION" (all-caps, standard Android Button text-transform),
    # not "Record Location" - tap_by_text is an exact (non-regex) match by
    # default, so an earlier attempt using the mixed-case label silently
    # tapped nothing (returned False) rather than raising, since it was
    # called with optional=True during exploration. Uses the real on-screen
    # text here, non-optional, so a genuine navigation miss now raises
    # instead of silently no-opping.
    h.tap_by_text(driver, "RECORD LOCATION", timeout=10)
    # Real map rendering (a GoogleMap SurfaceView) takes a moment to inflate -
    # give it real time rather than asserting instantly.
    time.sleep(5)
    texts = h.all_visible_texts(driver)
    if any(__import__("re").search(_CRASH_TEXT_RE, t, __import__("re").IGNORECASE) for t in texts):
        raise AssertionError(f"App crash dialog detected after opening the map screen: {texts}")
    # No assertion is made about WHICH tileset rendered (Satellite/Terrain/
    # Hybrid) - confirmed unobservable on this harness, see module docstring
    # point (2). This only confirms the map screen itself loaded without the
    # app crashing/ANRing under the currently-configured tileset.


def verify_map_form_loads(driver, app_code, username, password):
    """
    Installs BASIC_TESTS_NS_COPY (whatever tileset the caller already
    configured via HQClient.set_app_properties() + create_new_build()
    BEFORE calling this), logs in, navigates to the one real map-bearing
    form on this app, opens the map, and confirms no crash - see this
    module's own docstring for what is and isn't asserted.
    """
    steps = [
        ("Install BASIC_TESTS_NS_COPY by code", lambda: _install_app_by_code(driver, app_code)),
        ("Log in", lambda: _login(driver, username, password)),
        ("Navigate to Basic_Form_Tests > [Mobile] Appearance Attributes",
         lambda: _navigate_to_appearance_attributes_form(driver)),
        ("Page through the form to the maps geopoint question",
         lambda: _page_to_maps_question(driver)),
        ("Open the map (Record Location) and confirm no crash",
         lambda: _open_map_and_verify_no_crash(driver)),
    ]
    return _run_steps(steps)
