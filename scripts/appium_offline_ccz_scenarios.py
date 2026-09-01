"""
Appium implementations of the Recovery Measures "select a CCZ via the system
file picker" scenarios (offline_06_select_ccz_via_picker,
offline_08_move_ccz_to_downloads, offline_reinstall_update_app_flow, and
reinstall_update_05_06_chooser_and_ccz's own "Reinstall Using CCZ" branch) -
the ONE thing all 4 of these existing Maestro flows are structurally unable
to do: get a real .ccz file onto the device's filesystem before the
"Select CCZ" system file picker (Android's Storage Access Framework, a
separate process from org.commcare.dalvik) needs to find one. Confirmed live
this session: every one of these Maestro flows' own real failure evidence
shows the picker's default view containing only BrowserStack's stock seeded
media (Android_O.png, BrowserStack.jpg, etc.), never the CCZ the flow
expects, because nothing in scripts/run_suite.py/browserstack_client.py's
pipeline ever pushes a file onto the device - see each flow's own
"REQUIRED MANUAL/CI PRE-STEP (not run by Maestro)" header.

BrowserStack's Appium product CAN do this (confirmed live, 2026-08-25):
Appium-Python-Client's classic `driver.push_file(remote_path, base64_data)`
method works on this session's Appium server (1.22.0) - the newer
`mobile: pushFile` extension command does not (UnknownMethodException, same
class of newer-vs-classic-endpoint gap already hit and solved for
install_mid_session's own installApp call). See
appium_browserstack_client.AppiumBrowserStackClient.push_file.

This module pushes a real CCZ (committed under resources/ -
OFFLINE_TEST_ONE.ccz/RU_TEST_TWO.ccz, see resources/README.md's own
2026-08-25 update for why these are genuinely read at runtime, unlike the
CCZs removed from that directory on 2026-08-20) rather than fabricating one,
onto the device BEFORE any on-device step runs, then drives the exact same
on-device sequence (install app by code -> login -> forced "Select CCZ"
recovery screen -> SAF picker navigation -> auto-logout) the existing
Maestro flows already script, just with a real file for the picker to find.

Each scenario function returns the list of completed step names (matching
scripts/appium_scenarios.py's own _run_steps/ScenarioFailure convention -
imported directly from there rather than duplicated).
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, ScenarioFailure, _run_steps, _install_app_by_code, _login

DOWNLOADS_PATH = "/sdcard/Download/test.ccz"
# UPDATE (2026-08-25), confirmed via BrowserStack's own docs (Appium
# push_file is restricted to /sdcard/Download/, /sdcard/Pictures, and
# /sdcard/Android/data/<package> - https://www.browserstack.com/docs/
# app-automate/appium/advanced-features/upload-files) AND live testing (3
# rounds: a brand-new top-level dir, that same dir nested under Documents,
# and even plain /sdcard/Documents/ itself all failed the exact same way -
# push_file()'s own PUSH_FILE endpoint is a literal `adb push` server-side,
# confirmed from the real adb error text, and BrowserStack's platform
# simply doesn't allow it outside this fixed allow-list, regardless of
# whether the target directory already exists): the original Maestro
# flows' own "/sdcard/RecoveryMeasuresTest/" folder name (from
# offline_05_place_ccz_custom_path.yaml's documented, never-automated
# manual `adb push` pre-step - real `adb push` via USB debugging isn't
# subject to this same allow-list) can't be reproduced through this
# BrowserStack Appium mechanism.
#
# UPDATE (2nd correction, same day), confirmed live: Pictures was also a
# dead end for a DIFFERENT reason - pushing there succeeds (verified via a
# real push+pull round-trip), but browsing INTO the Pictures folder from
# the SAF picker's own "Files on <device>" view renders a MediaStore-
# filtered "Files in Pictures" listing that shows only recognized image
# types (BrowserStack's stock Android_O.png/BrowserStack.jpg/etc.) - the
# real .ccz file on disk there never appears in that view at all, so no
# tap mechanism could ever find it. Per direct user question ("if we are
# downloading then shouldn't we click on Downloads directly"): pushing to
# Download and reaching it via the roots drawer's own "Downloads" shortcut
# (a plain ListView row, not a MediaStore-filtered folder-browsing
# GridView) sidesteps both the filtering issue AND the earlier
# thumbnail-loading staleness issue, while still requiring the picker to
# actually navigate away from its own default "Recent" view - the real
# thing this flow (and offline_06_select_ccz_via_picker.yaml's own name)
# is meant to exercise, distinct from offline_08's zero-navigation,
# straight-from-Recent tap.
CUSTOM_PATH = DOWNLOADS_PATH


def _push_ccz(appium_client, driver, local_ccz_path, device_path):
    appium_client.push_file(driver, device_path, local_ccz_path)


def _assert_forced_select_ccz_screen(driver):
    """Shared precondition check for the offline family's own forced
    recovery-measure screen - NOT part of reinstall_update_05_06's
    "Reinstall Using CCZ" branch, which reaches the picker through a
    completely different on-device path (its own reinstall-chooser dialog,
    not this forced screen) - kept separate so that other path doesn't
    wait on text that will never appear for it."""
    h.wait_visible_text(driver, "(?i).*recovery (procedures|measures).*initiated.*", regex=True, timeout=15)
    h.wait_visible_text(driver, "(?i).*select ccz.*", regex=True, timeout=10)
    h.tap_by_text(driver, "(?i).*select ccz.*", regex=True)


def _pick_ccz_from_downloads_picker(driver):
    """Once the SAF picker is already open: the picker's default view is
    Downloads, so a pushed file there is directly visible with no folder
    navigation - shared by every scenario whose file lands in Downloads.
    Confirmed passing live (2x, plain tap_by_text/element.click()) - left
    as-is rather than swapped to the coordinate-based tap added for
    offline_06's own confirmed click() failure, to avoid introducing an
    unverified code path into an already-working scenario."""
    h.tap_by_text(driver, "(?i).*test\\.ccz.*", regex=True, timeout=15)


def _pick_ccz_from_custom_folder_picker(driver):
    """Once the SAF picker is already open: confirmed live (2026-08-25, real
    failure hierarchy dump) the picker's default landing view is
    "Recent"/"Recent files" (com.google.android.documentsui's own MediaStore-
    backed recents list, showing only BrowserStack's stock seeded media -
    Laptop_with_code.jpg etc.) - a freshly-pushed file doesn't reliably
    appear there, so tapping it directly (the original approach) can't be
    relied on. Opens the roots drawer first (content-desc="Show roots", the
    hamburger-style button at the top-left of the toolbar - confirmed
    present in a real dump) to reach the "Downloads" shortcut, then taps the
    file there.

    UPDATE (2026-08-25), two dead ends before this, both confirmed live:
    (1) "Galaxy S26" -> "Pictures" (a genuinely different, non-Downloads
    folder) - Pictures pushed fine (real push+pull round-trip confirmed),
    but browsing INTO it from "Files on Galaxy S26" renders a MediaStore-
    filtered "Files in Pictures" view that only shows recognized image
    types - the real .ccz file sitting right there on disk never appears
    in that view at all, so no tap mechanism could ever find it.
    (2) that same GridView also raced its own thumbnail-icon loading
    against a whole-tree element scan (160 elements, ~320 get_attribute
    round-trips) badly enough to go stale on nearly every attempt,
    independent of the filtering issue.
    Per direct user question ("if we are downloading then shouldn't we
    click on Downloads directly"): the drawer's own "Downloads" shortcut
    sidesteps both problems - same reliable ListView type as "Galaxy S26"
    (no thumbnail GridView, no image-type filtering) - while still forcing
    real picker navigation away from the default Recent view, which is
    this flow's actual point, distinct from offline_08's zero-navigation
    straight-from-Recent tap."""
    # UPDATE (2026-08-26), confirmed live in the 2.64.1 full CI run
    # (32953501586): a real failure screenshot showed "Downloads" fully
    # visible and tappable in the roots drawer at the exact moment this
    # tap's own 10s timeout expired - not a missing element, just a drawer
    # that took longer than 10s to render under that session's device
    # conditions (same class of slowness seen across many unrelated flows
    # today). Raised to a more generous timeout rather than assuming a
    # real regression.
    # UPDATE (2026-08-27), confirmed live via a real CI failure (run
    # 33078644576, session 2e95c32819bc7f1b7b14b1fee44ebebb7399352c): the
    # device log's own hierarchy dump at the moment "test.ccz" never
    # became tappable showed "Can't load content at the moment" - a real
    # Android DocumentsUI/SAF content-provider error rendering the
    # Downloads listing itself, not a missing file or wrong path (the push
    # target and this tap's target already agree, both DOWNLOADS_PATH).
    # Genuinely transient OS-level flakiness, not a logic bug - retries
    # the whole roots-drawer-to-Downloads navigation (a fresh query) a few
    # times if this specific error text appears, rather than giving up on
    # one failed content-provider query.
    for attempt in range(3):
        h.tap_by_text(driver, "Show roots", timeout=20)
        h.tap_by_text(driver, "Downloads", timeout=20)
        if not h.is_text_visible(driver, "Can.t load content at the moment", regex=True):
            break
        h.checkpoint(driver, f"downloads_content_provider_error_attempt_{attempt}")
    h.tap_by_exact_text_coords(driver, "test.ccz", timeout=20)


def run_offline_08_downloads_happy_path(driver, appium_client, app_code, local_ccz_path):
    """offline_08_move_ccz_to_downloads: CCZ pre-staged directly in
    Downloads (the picker's own default view) - "happy path" contrast to
    the offline_06 custom-folder case."""
    steps = [
        ("Push CCZ to Downloads", lambda: _push_ccz(appium_client, driver, local_ccz_path, DOWNLOADS_PATH)),
        ("Install app by code", lambda: _install_app_by_code(driver, app_code)),
        ("Login (triggers forced Select CCZ)", lambda: _login(driver, os.environ["CC_TEST_USERNAME"], os.environ["CC_TEST_PASSWORD"])),
        ("Forced Select CCZ screen visible, open picker", lambda: _assert_forced_select_ccz_screen(driver)),
        ("Select CCZ from Downloads (default picker view)", lambda: _pick_ccz_from_downloads_picker(driver)),
        ("Wait for auto-logout after install", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=60)),
    ]
    return _run_steps(steps)


def run_offline_06_custom_folder_picker(driver, appium_client, app_code, local_ccz_path):
    """offline_06_select_ccz_via_picker: CCZ pre-staged in a custom,
    non-Downloads folder - "unhappy path", needs real picker folder
    navigation, not just a same-view tap."""
    steps = [
        ("Push CCZ to custom RecoveryMeasuresTest folder", lambda: _push_ccz(appium_client, driver, local_ccz_path, CUSTOM_PATH)),
        ("Install app by code", lambda: _install_app_by_code(driver, app_code)),
        ("Login (triggers forced Select CCZ)", lambda: _login(driver, os.environ["CC_TEST_USERNAME"], os.environ["CC_TEST_PASSWORD"])),
        ("Forced Select CCZ screen visible, open picker", lambda: _assert_forced_select_ccz_screen(driver)),
        ("Select CCZ via custom folder navigation", lambda: _pick_ccz_from_custom_folder_picker(driver)),
        ("Wait for auto-logout after install", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=60)),
    ]
    return _run_steps(steps)


def run_offline_reinstall_update_app_flow(driver, appium_client, app_code, local_ccz_path):
    """offline_reinstall_update_app_flow's own Recovery (offline) 10 + 11:
    same Downloads happy-path mechanism as offline_08, plus the negative
    check (re-login, no further forced recovery screen)."""
    steps = [
        ("Push CCZ to Downloads", lambda: _push_ccz(appium_client, driver, local_ccz_path, DOWNLOADS_PATH)),
        ("Install app by code", lambda: _install_app_by_code(driver, app_code)),
        ("Login (triggers forced Select CCZ)", lambda: _login(driver, os.environ["CC_TEST_USERNAME"], os.environ["CC_TEST_PASSWORD"])),
        ("Forced Select CCZ screen visible, open picker", lambda: _assert_forced_select_ccz_screen(driver)),
        ("Select CCZ from Downloads (default picker view)", lambda: _pick_ccz_from_downloads_picker(driver)),
        ("Recovery (offline) 10: wait for auto-logout", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=60)),
        ("Recovery (offline) 11: re-login, negative check",
         lambda: _login(driver, os.environ["CC_TEST_USERNAME"], os.environ["CC_TEST_PASSWORD"])),
        ("Recovery (offline) 11: Start visible", lambda: h.assert_visible_text(driver, "Start")),
        ("Recovery (offline) 11: no further forced recovery screen",
         lambda: h.assert_not_visible_text(driver, "(?i).*recovery (procedures|measures).*initiated.*", regex=True)),
    ]
    return _run_steps(steps)


def run_reinstall_05_06_ccz_branch(driver, appium_client, app_code, local_ccz_path):
    """reinstall_update_05_06_chooser_and_ccz's "Reinstall Using CCZ" branch
    (a DIFFERENT app - RU_TEST_TWO/THREE, and a DIFFERENT on-device screen
    shape than the offline family: confirmed live this button leads to its
    own "Install your CommCare application from a .ccz file / Please
    select a CCZ file to begin / INSTALL APP" screen, not straight into the
    SAF picker - tap INSTALL APP first to actually open it)."""
    steps = [
        ("Push CCZ to Downloads", lambda: _push_ccz(appium_client, driver, local_ccz_path, DOWNLOADS_PATH)),
        ("Install app by code (RU_TEST_TWO)", lambda: _install_app_by_code(driver, app_code)),
        ("Login (Two is stale relative to released Three)",
         lambda: _login(driver, os.environ["CC_TEST_USERNAME"], os.environ["CC_TEST_PASSWORD"])),
        ("Reinstall-needed chooser visible", lambda: h.assert_visible_text(driver, "(?i).*reinstall.*", regex=True)),
        ("Tap Reinstall Using CCZ", lambda: h.tap_by_text(driver, "(?i).*Reinstall.*CCZ.*", regex=True)),
        # UPDATE (2026-08-25), confirmed live via a real failure hierarchy
        # dump: "INSTALL APP" (org.commcare.dalvik:id/screen_multimedia_
        # inflater_install) is NOT what opens the SAF picker - it's
        # enabled="false" until a file path has already been chosen. The
        # real trigger is the file-fetch/browse icon right next to the
        # (empty) location EditText - org.commcare.dalvik:id/
        # screen_multimedia_inflater_filefetch, an ImageButton with no
        # text/content-desc at all (confirmed in the same dump), which is
        # exactly why the original text-based tap on "install app" never
        # found it and silently no-opped instead. INSTALL APP itself only
        # belongs AFTER a file's been picked, moved to its correct place
        # below.
        ("Tap the file-fetch icon (opens the SAF picker)",
         lambda: h.tap_by_id(driver, f"{APP_ID}:id/screen_multimedia_inflater_filefetch", timeout=15)),
        ("Select CCZ from Downloads (default picker view)", lambda: _pick_ccz_from_downloads_picker(driver)),
        ("Tap INSTALL APP (now enabled with a file chosen)",
         lambda: h.tap_by_text(driver, "(?i).*install app.*", regex=True, timeout=15)),
        ("Wait for auto-logout after install", lambda: h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=60)),
    ]
    return _run_steps(steps)
