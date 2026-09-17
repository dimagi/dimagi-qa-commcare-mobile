"""
Appium implementation of Multimedia > "External File in Forms" - the ONE
Master Mobile Plan (2026) Multimedia row whose own flow header already
documents a genuine, structural gap: the sheet needs a real video file
sitting at the EXACT device filesystem path
/storage/emulated/0/Android/data/org.commcare.dalvik/media/yourvideo.mp4
(inside the app's own external-files directory, but outside CommCare's own
MediaStore sandbox) BEFORE the flow runs - see
flows/multimedia/external_file_in_forms.yaml's own "UNMET PREREQUISITE"
header for the full citation. Maestro's `addMedia` only ever pushes into the
shared MediaStore/gallery, with no way to place a file at an arbitrary
filesystem path - confirmed by that flow's own header, and by every other
Maestro flow in this repo that hits the same class of gap
(flows/recovery_measures/offline_*.yaml).

/storage/emulated/0/ IS /sdcard/ on Android, so the real target resolves to
/sdcard/Android/data/org.commcare.dalvik/media/yourvideo.mp4 - one of the
exact 3 allow-listed prefixes BrowserStack's Appium push_file() supports
(confirmed this session: /sdcard/Download/, /sdcard/Pictures/,
/sdcard/Android/data/<package>/ - see
scripts/appium_offline_ccz_scenarios.py's own citation for the full
allow-list investigation), so this is genuinely reachable via the same
push_file mechanism already proven for the offline-CCZ scenarios, not a new
capability.

Real on-device text/path verified against the app's own real CCZ (read-only
download via HQClient.download_latest_ccz, never committed) rather than
trusting the existing Maestro flow's own "unverified against any source"
citation: modules-4/forms-0.xml's itext confirms the form's title is
"Access file" (lowercase f - the existing Maestro flow has "Access File",
which would never have matched even with a working file push), the link
text is "This link", and the target markdown link is EXACTLY
file:///storage/emulated/0/Android/data/org.commcare.dalvik/media/yourvideo.mp4,
matching the flow header's own citation precisely.

The pushed video (flows/multimedia/assets/yourvideo.mp4) is a real,
genuinely-decodable MP4 generated via cv2.VideoWriter (confirmed live:
cv2.VideoCapture successfully opens it and reads back 15 real frames) - not
a placeholder/empty file, since the whole point of this test is confirming
Android hands the file off to a real video-capable app, which requires a
file Android's own mime-type sniffing recognizes as a genuine video.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, ScenarioFailure, _run_steps, _install_app_by_code, _login

# See module docstring - /storage/emulated/0/ == /sdcard/ on Android.
EXTERNAL_FILE_DEVICE_PATH = "/sdcard/Android/data/org.commcare.dalvik/media/yourvideo.mp4"


def _push_video(appium_client, driver, local_video_path):
    appium_client.push_file(driver, EXTERNAL_FILE_DEVICE_PATH, local_video_path)


def _open_access_file_form(driver):
    h.tap_by_text(driver, "Start")
    h.tap_by_text(driver, "External File in Forms")
    # Real form title confirmed lowercase "Access file" against the app's
    # own CCZ - see module docstring.
    h.tap_by_text(driver, "Access file")


def _tap_link_and_verify_handoff(driver):
    h.tap_by_text(driver, "This link")
    # Best-effort POSITIVE signal on top of the flow's original negative-
    # only check: if tapping the link genuinely handed off to an external
    # video-capable app via Intent.ACTION_VIEW (see module docstring's
    # MarkupUtil.java citation), the foreground package should no longer be
    # CommCare's own. current_package is a plain WebDriver property (not
    # one of the vendor "mobile:" extensions already confirmed unsupported
    # on this Appium server, e.g. terminateApp/backgroundApp), so this is
    # low-risk to attempt - if it ever raises on some driver build, the
    # negative check below still stands on its own.
    try:
        time.sleep(2)
        current = driver.current_package
        if current and current != APP_ID:
            return  # handed off to a real external app - strongest signal
    except Exception:
        pass
    # Fallback/always-checked: the app's own "no handler" error must NOT be
    # showing (same assertion the original Maestro flow already used).
    h.assert_not_visible_text(driver, "(?i)No activity found to handle", regex=True)


def run_external_file_in_forms(driver, appium_client, app_code, cc_username, cc_password, local_video_path):
    steps = [
        ("Push video to app-external-files dir", lambda: _push_video(appium_client, driver, local_video_path)),
        ("Install app by code", lambda: _install_app_by_code(driver, app_code)),
        ("Login", lambda: _login(driver, cc_username, cc_password)),
        ("Open External File in Forms > Access file", lambda: _open_access_file_form(driver)),
        ("Tap This link, verify handoff (not 'no activity found')",
         lambda: _tap_link_and_verify_handoff(driver)),
    ]
    return _run_steps(steps)
