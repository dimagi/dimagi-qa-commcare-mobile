"""
Appium implementation of Multimedia > "MM2" (Master Mobile Plan 2026) -
previously Not automatable because step 1 requires physically moving a real
file OUT of the app's already-installed external-files directory via the
device's own file-manager app before the rest of the scenario runs.

Confirmed live 2026-09-07 by reading commcare-android source directly
(app/src/org/commcare/activities/{CommCareVerificationActivity,
MultimediaInflaterActivity}.java) that the row's REAL intent - "after
installing without multimedia and repairing via a zip that's missing ONE
file, the app should report exactly that one file as still missing" -
never actually needs an already-installed app or on-device file relocation
at all:

- Installing via an app code generated with include_media=False (same
  HQClient.get_app_install_code() this repo already uses everywhere,
  just with that one flag flipped) lands directly on
  CommCareVerificationActivity showing every real media reference as
  missing - the exact "Some of your application's multimedia has not
  been installed" state step 6 describes, with NO prior install/clear-data
  cycle needed (a fresh Appium session already starts from nothing
  installed, which IS the state MM2's own steps 2-4 exist to manufacture
  on an already-installed app).
- CommCareVerificationActivity's overflow menu has a literal "Install
  Multimedia" item (CommCareVerificationActivity.java:280, a hardcoded
  string, not localized) that launches MultimediaInflaterActivity.
- MultimediaInflaterActivity.searchForDefault() (confirmed via source,
  lines 158-221) auto-scans external storage roots ONE LEVEL DEEP for any
  file named `commcare*.zip` (case-insensitive) and pre-fills the location
  field - /sdcard/Download/ (one of push_file's 3 allow-listed prefixes,
  already proven this session) is exactly one level under
  Environment.getExternalStorageDirectory(), so a pushed
  "commcare_mm2_repair.zip" is auto-discovered with NO file-browser/SAF
  interaction needed at all - just push it, then tap the "Install
  Multimedia" button (screen_multimedia_inflater_install) directly.
- After a successful unzip, MultimediaInflaterActivity finishes with
  RESULT_OK; CommCareVerificationActivity's onActivityResult sets
  newMediaToValidate=true, and onPostResume automatically re-runs
  verifyResourceInstall() - no manual "Retry" tap needed either.
- The missing-media message (handleVerificationProblems(),
  CommCareVerificationActivity.java:204-245) embeds each unresolved
  resource's real device file path (prettyString() strips everything
  before /storage/emulated/0) - so asserting the target filename appears
  in MissingMediaPrompt's text after the repair-zip install is a precise,
  real check of "only that one file is still reported missing", not a
  guess.

The "one specific missing file" itself is manufactured entirely LOCALLY
(no device/file-manager interaction): download the app's real multimedia
zip via HQClient's download-multimedia-zip endpoint (same async-job/poll
shape as download_ccz), strip exactly one real file out of it with
Python's stdlib zipfile, and push the edited zip instead of the original.

IMPORTANT, confirmed live 2026-09-07: the full "Download ZIP" multimedia
export (116 files, ~167MB) is NOT what the CURRENT top build actually
requires - a real no-media install's own missing-media prompt only ever
names a small, stable set of currently-referenced files (five.png,
four.png, three-three_one.png, five-five_one.mp3, etc.), confirmed via a
live dispatch reading that exact prompt text. `commcare/image/data/two.png`
(this file's original target) turned out to be ORPHANED - not part of the
current required set at all - so excluding it from a repair zip produces
NO missing-file report, which would have made this scenario's core
assertion vacuously true. Retargeted to `five.png`, one of the real,
currently-required files. Separately, a full 167MB push_file call was timed
(confirmed live: 515.9s, ~8.6 minutes - base64-encodes the entire file into
one HTTP POST, see AppiumBrowserStackClient.push_file's own docstring) -
impractical for routine CI use regardless of correctness. The repair zip
built here also excludes ONE of the historical zip's 2 giant, identically-
sized (64,553,407 bytes each) `.3gp` video files, KNOWN_ORPHANED_ENTRIES
below.

UPDATE (2026-09-15), confirmed/CORRECTED live: the original "working
hypothesis" that BOTH .3gp files were orphaned like two.png was wrong for
one of them - a real run's own `_verify_only_target_file_missing` self-check
(exactly the safety net this was designed for) caught
`commcare/video/data/undefined-3q6iti.3gp` still showing up in the real
missing-media prompt after the repair-zip install, meaning it IS still
required by the current top build. Only `commcare/video-inline/data/
undefined-sy7j2g.3gp` is confirmed genuinely orphaned (never appears in the
missing-media prompt either before or after the repair). The repair zip now
includes the required 3gp (pushing it to ~100MB instead of ~38MB, still
well under the already-proven-working 167MB/8.6min full push) and only
excludes the one confirmed-orphaned file.
"""
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium.webdriver.common.appiumby import AppiumBy
from appium_scenarios import APP_ID, ScenarioFailure, _run_steps, _relaunch_app

TARGET_ENTRY = "commcare/image/data/five.png"
TARGET_FILENAME = "five.png"
REPAIR_ZIP_DEVICE_PATH = "/sdcard/Download/commcare_mm2_repair.zip"

# Only ONE of the original 2 giant .3gp video files is actually orphaned -
# see module docstring's 2026-09-15 UPDATE for the live confirmation that
# the other one (undefined-3q6iti.3gp) is still required and must stay IN
# the repair zip.
KNOWN_ORPHANED_ENTRIES = [
    "commcare/video-inline/data/undefined-sy7j2g.3gp",
]


def build_repair_zip(full_zip_path, out_path, missing_entry=TARGET_ENTRY, also_exclude=()):
    """Copies every entry from the app's real multimedia zip into a new
    zip EXCEPT `missing_entry` (and anything in `also_exclude`) - the local
    equivalent of "move one file out before repairing". `missing_entry`
    must actually exist in the source zip (a real, present-then-removed
    file, not a placeholder); entries in `also_exclude` are excluded
    best-effort (no error if already absent), since that's used for the 2
    KNOWN_ORPHANED_ENTRIES this app's current build may or may not still
    reference from one HQ deploy to the next."""
    with zipfile.ZipFile(full_zip_path) as src, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as dst:
        names = src.namelist()
        if missing_entry not in names:
            raise ValueError(f"{missing_entry!r} not found in {full_zip_path} (has {len(names)} entries)")
        exclude = {missing_entry, *also_exclude}
        copied = 0
        for item in src.infolist():
            if item.filename in exclude:
                continue
            dst.writestr(item, src.read(item.filename))
            copied += 1
        expected = len(names) - len(exclude & set(names))
        if copied != expected:
            raise AssertionError(f"Expected to copy {expected} entries, copied {copied}")


def _install_no_media_once(driver, app_code):
    """Same shape as appium_scenarios._install_app_by_code_once() through
    the "I'LL UPDATE LATER" dismissal, but does NOT tap screen_multimedia_retry
    the way that function's own bypass loop does - MM2 needs to STOP on
    CommCareVerificationActivity, not skip past it."""
    _relaunch_app(driver)
    h.tap_by_text(driver, "OK", optional=True, timeout=3)
    h.tap_by_id(driver, f"{APP_ID}:id/enter_app_location")
    for attempt in range(3):
        h.clear_by_id(driver, f"{APP_ID}:id/edit_profile_location", timeout=1)
        h.type_into_focused(driver, app_code)
        if h.is_text_visible(driver, app_code):
            break
        h.tap_by_id(driver, f"{APP_ID}:id/enter_app_location", optional=True, timeout=3)
    else:
        raise RuntimeError(f"App code {app_code!r} never appeared on screen after typing")
    h.hide_keyboard(driver)
    h.tap_by_id(driver, f"{APP_ID}:id/start_install")
    h.tap_by_id(driver, f"{APP_ID}:id/btn_start_install")
    h.tap_by_text(driver, "I.LL UPDATE LATER", optional=True, timeout=3)
    h.tap_by_id(driver, f"{APP_ID}:id/btn_start_install", optional=True, timeout=3)
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_multimedia_retry", timeout=60)


def _get_text_by_id(driver, resource_id):
    els = driver.find_elements(AppiumBy.ID, resource_id)
    if not els:
        return None
    return els[0].get_attribute("text") or ""


def _push_repair_zip(appium_client, driver, local_zip_path):
    appium_client.push_file(driver, REPAIR_ZIP_DEVICE_PATH, local_zip_path)


def _open_install_multimedia_menu(driver):
    h.tap_by_text(driver, "More options", timeout=10)
    h.tap_by_text(driver, "Install Multimedia", timeout=10)


def _complete_multimedia_install(driver):
    # UPDATE (2026-09-07), confirmed live via a real failure screenshot:
    # MultimediaInflaterActivity.searchForDefault()'s auto-discovery (see
    # module docstring's ORIGINAL plan) did NOT find the pushed zip - the
    # location field read empty ("Please select a ZIP file to begin") and
    # INSTALL MULTIMEDIA stayed disabled. Re-reading that method's own
    # source explains why it's not reliable: it scans EVERY subfolder of
    # EVERY external-storage root ONE level deep, but budgets only 400ms
    # total across all of them (MAX_TIME_TO_TRY) - on a real device with
    # more than a couple of default folders (Download, Pictures, DCIM,
    # Movies, Music, Android, ...), whether Download gets scanned before
    # that budget runs out depends on filesystem enumeration order, which
    # isn't guaranteed alphabetical. This is exactly the same class of
    # screen (org.commcare.dalvik:id/screen_multimedia_inflater_filefetch/
    # _install/_location ids all identical) already driven reliably via a
    # real SAF file-picker tap in appium_offline_ccz_scenarios.py's
    # _pick_ccz_from_downloads_picker() (confirmed passing live 2x there,
    # for a .ccz pushed to the same /sdcard/Download/) - reused here
    # instead of depending on the flaky auto-discovery shortcut.
    h.tap_by_id(driver, f"{APP_ID}:id/screen_multimedia_inflater_filefetch", timeout=15)
    h.tap_by_text(driver, "(?i).*commcare_mm2_repair.*", regex=True, timeout=15)
    # UPDATE (2026-09-15), confirmed live (real failure screenshot +
    # hierarchy dump, BrowserStack session 8b29c5b124a4a569d145b9660f06f44bfe22e23b):
    # on this device/Android version, the SAF picker resolves the tapped
    # filename to an opaque content:// URI
    # ("content://com.android.providers.downloads.documents/document/msf%3A255")
    # in the location field, NOT a path containing the literal filename -
    # asserting the location field's own text was the wrong check for this
    # platform's picker behavior, not a sign anything actually went wrong.
    # The screenshot shows the app itself already treats this URI as fully
    # valid: screen_multimedia_install_messages reads "Multimedia inflater
    # ready, press install to begin" and screen_multimedia_inflater_install
    # is enabled - that resource-id-backed status message is CommCare's own
    # real readiness signal (it only ever shows once MultimediaInflater has
    # successfully resolved a real zip via the URI), so assert on THAT
    # instead of the location field's literal text.
    ready = h.wait_visible_id(driver, f"{APP_ID}:id/screen_multimedia_install_messages", timeout=15, optional=True)
    message = _get_text_by_id(driver, f"{APP_ID}:id/screen_multimedia_install_messages")
    if not ready or not message or "ready" not in message.lower():
        location = _get_text_by_id(driver, f"{APP_ID}:id/screen_multimedia_inflater_location")
        raise AssertionError(
            f"MultimediaInflaterActivity never showed a 'ready' status message after picking "
            f"the file via the SAF picker (location field reads {location!r}, status message "
            f"reads {message!r}) - the picked file may not have resolved to a valid zip."
        )
    h.wait_visible_id(driver, f"{APP_ID}:id/screen_multimedia_inflater_install", timeout=15)
    h.tap_by_id(driver, f"{APP_ID}:id/screen_multimedia_inflater_install")
    # Real unzip of a ~38MB real media archive - generous timeout.
    h.wait_not_visible_id(driver, f"{APP_ID}:id/screen_multimedia_inflater_install", timeout=180)


# Real, currently-required media files (confirmed live 2026-09-07 via a
# fresh no-media install's own missing-media prompt) that WERE present in
# the repair zip and so must NOT still be reported missing after a
# successful zip install - if any of these (or the 2 excluded 3gp videos)
# show up, the repair either didn't apply or the videos turned out to still
# be required after all (see module docstring's own "working hypothesis").
OTHER_REQUIRED_FILES = ["four.png", "three-three_one.png", "five-five_one.mp3", "undefined-3q6iti.3gp", "undefined-sy7j2g.3gp"]


def _verify_only_target_file_missing(driver):
    # After MultimediaInflaterActivity finishes, CommCareVerificationActivity
    # auto re-verifies (onPostResume->newMediaToValidate) - poll the prompt
    # text until it stabilizes on a real result rather than assert instantly.
    deadline = time.monotonic() + 60
    prompt = None
    while time.monotonic() < deadline:
        if h.wait_visible_id(driver, f"{APP_ID}:id/edit_username", timeout=1, optional=True):
            raise AssertionError(
                "Reached the login screen (edit_username) - verification reported full "
                "success, meaning the target file was NOT actually reported missing "
                "(the repair zip may have included it after all, or nothing is being "
                "checked)."
            )
        prompt = _get_text_by_id(driver, f"{APP_ID}:id/MissingMediaPrompt")
        if prompt and TARGET_FILENAME in prompt:
            break
        time.sleep(2)
    else:
        raise AssertionError(
            f"MissingMediaPrompt never showed {TARGET_FILENAME!r} within 60s - last seen "
            f"text: {prompt!r}"
        )
    still_missing = [name for name in OTHER_REQUIRED_FILES if name in prompt]
    if still_missing:
        raise AssertionError(
            f"MissingMediaPrompt still lists {still_missing} (all WERE present in the "
            f"repair zip) alongside {TARGET_FILENAME!r} - either the zip install didn't "
            f"actually apply, or (for the 2 .3gp entries) they turned out to still be "
            f"required and shouldn't have been excluded from the repair zip: {prompt!r}"
        )


def run_mm2(driver, appium_client, app_code_no_media, local_repair_zip_path):
    """Deliberately does NOT log in at the end: with `two.png` still
    genuinely missing after the repair-zip install, CommCareVerificationActivity
    has no path forward to the login screen from this entry point (its
    `skip_verification_button` only renders when launched from
    AppManagerActivity, which this flow never goes through) - the row's own
    real pass criteria (step 10) is the missing-file message itself, not a
    subsequent successful login."""
    steps = [
        ("Install app with an include_media=False code", lambda: _install_no_media_once(driver, app_code_no_media)),
        ("Push edited repair zip (missing two.png) to Downloads",
         lambda: _push_repair_zip(appium_client, driver, local_repair_zip_path)),
        ("Open overflow menu > Install Multimedia", lambda: _open_install_multimedia_menu(driver)),
        ("Complete multimedia install from auto-discovered zip", lambda: _complete_multimedia_install(driver)),
        ("Verify only the target file is still reported missing", lambda: _verify_only_target_file_missing(driver)),
    ]
    return _run_steps(steps)
