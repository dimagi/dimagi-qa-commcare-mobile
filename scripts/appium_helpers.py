"""
Maestro-primitive equivalents built on top of a live Appium `driver`, so
scripts/appium_scenarios.py can read like the Maestro YAML flows it
replaces (tapOn/inputText/extendedWaitUntil/assertVisible/assertNotVisible)
instead of raw WebDriver calls.

Text matching walks every element's `text` AND `content-desc` attributes out
of Appium's XML `page_source` and regex/exact-matches against it in Python -
the same technique already used throughout this repo's own investigations
this session to read BrowserStack's Maestro failure hierarchy dumps (walk
every node's own attributes, since neither tool exposes a native regex-match
locator against them). `content-desc` matters just as much as `text` here -
confirmed live (2026-08-19) that CommCare's own icon-only "More options"
overflow button exposes its label ONLY via content-desc, with no `text`
attribute at all (standard Android practice for icon buttons) - matching on
`text` alone would silently never find it.
"""
import pathlib
import re
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import StaleElementReferenceException

_TEXT_ATTR_RE = re.compile(r'(?:text|content-desc)="((?:[^"\\]|\\.)*)"')
_CHECKPOINT_DIR = pathlib.Path(__file__).resolve().parent.parent / "reports" / "appium_checkpoints"


def checkpoint(driver, label):
    """Saves a screenshot + page_source at a named point, regardless of
    pass/fail - unlike run_appium_suite.py's own _save_failure_evidence
    (only fires on a raised exception), this is for tracing an unclear
    multi-step sequence end-to-end when the failure itself gives no error
    text (e.g. a form that silently resets rather than raising). Best-effort
    - never raises, so a checkpoint call can never itself break a scenario."""
    try:
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        png_path = _CHECKPOINT_DIR / f"{stamp}_{label}.png"
        xml_path = _CHECKPOINT_DIR / f"{stamp}_{label}.xml"
        driver.get_screenshot_as_file(str(png_path))
        xml_path.write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass
_DEFAULT_POLL = 0.5


def _unescape_xml(s):
    return (s.replace("&quot;", '"').replace("&apos;", "'")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))


def _matches(text, pattern, regex):
    return re.search(pattern, text) is not None if regex else text == pattern


def all_visible_texts(driver):
    """Every non-empty `text` attribute currently in the accessibility tree."""
    return [_unescape_xml(m) for m in _TEXT_ATTR_RE.findall(driver.page_source) if m]


def is_text_visible(driver, pattern, regex=False):
    return any(_matches(t, pattern, regex) for t in all_visible_texts(driver))


def find_text_matching(driver, pattern):
    """Returns the first currently-visible text/content-desc string matching
    the regex `pattern` (DOTALL, so `.` spans newlines the same way the rest
    of this module's regex checks already do), or None if nothing matches.
    Unlike is_text_visible, hands back the actual matched string so a caller
    can pull a value out of it (e.g. a version number) instead of just
    confirming presence."""
    compiled = re.compile(pattern, re.DOTALL)
    for t in all_visible_texts(driver):
        if compiled.search(t):
            return t
    return None


def wait_visible_text(driver, pattern, timeout=10, regex=False, optional=False, poll=_DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_text_visible(driver, pattern, regex=regex):
            return True
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(
        f"Text {'matching regex ' if regex else ''}{pattern!r} never became visible within {timeout}s"
    )


def wait_not_visible_text(driver, pattern, timeout=10, regex=False, optional=False, poll=_DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_text_visible(driver, pattern, regex=regex):
            return True
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Text {'matching regex ' if regex else ''}{pattern!r} still visible after {timeout}s")


def assert_visible_text(driver, pattern, regex=False):
    if not is_text_visible(driver, pattern, regex=regex):
        raise AssertionError(
            f"Expected text {'matching regex ' if regex else ''}{pattern!r} to be visible - not found. "
            f"Visible texts: {all_visible_texts(driver)}"
        )


def assert_not_visible_text(driver, pattern, regex=False):
    if is_text_visible(driver, pattern, regex=regex):
        raise AssertionError(f"Expected text {'matching regex ' if regex else ''}{pattern!r} to be NOT visible.")


def tap_by_id(driver, resource_id, timeout=10, optional=False, poll=_DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        els = driver.find_elements(AppiumBy.ID, resource_id)
        if els:
            try:
                els[0].click()
                return True
            except StaleElementReferenceException:
                pass  # screen changed between find and click - retry with fresh elements
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Element id={resource_id!r} never became tappable within {timeout}s")


def tap_by_text(driver, pattern, timeout=10, regex=False, optional=False, poll=_DEFAULT_POLL):
    """UPDATE (2026-08-19), confirmed live: a screen transition happening
    WHILE this iterates the elements found via "//*[@text or @content-desc]"
    can invalidate that cached snapshot mid-loop (StaleElementReferenceException,
    "Cached elements ... do not exist in DOM anymore") - unlike
    is_text_visible/all_visible_texts above, which parse a page_source
    STRING snapshot and are immune to this, tap_by_text needs real
    WebElement handles to click(), which can go stale between being found
    and being read/clicked. Treats staleness as "this poll attempt is
    moot, try again with fresh elements" rather than a hard failure.

    Checks BOTH `text` and `content-desc` (see this module's own docstring -
    icon-only buttons like CommCare's "More options" overflow menu expose
    their label only via content-desc, never `text`)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for el in driver.find_elements(AppiumBy.XPATH, "//*[@text or @content-desc]"):
                try:
                    labels = [_unescape_xml(el.get_attribute("text") or ""),
                              _unescape_xml(el.get_attribute("content-desc") or "")]
                except StaleElementReferenceException:
                    continue
                if any(_matches(label, pattern, regex) for label in labels if label):
                    try:
                        el.click()
                        return True
                    except StaleElementReferenceException:
                        break
        except StaleElementReferenceException:
            pass
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Text {'matching regex ' if regex else ''}{pattern!r} never became tappable within {timeout}s")


def input_text(driver, resource_id, text, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        els = driver.find_elements(AppiumBy.ID, resource_id)
        if els:
            try:
                els[0].clear()
                els[0].send_keys(text)
                return
            except StaleElementReferenceException:
                pass  # screen changed between find and input - retry with fresh elements
        time.sleep(_DEFAULT_POLL)
    raise TimeoutError(f"Element id={resource_id!r} never became available for input within {timeout}s")


def clear_by_id(driver, resource_id, timeout=5):
    """UPDATE (2026-08-19), confirmed live via a screenshot: a located
    element's own .clear() is JUST AS unreliable as its send_keys() on this
    Appium server (a password field showed ~12 mask dots after 3 retyping
    attempts of a 3-character password - .clear() silently left the
    previous value in place every time, same class of issue as
    type_into_focused's own docstring already covers for typing).

    UPDATE (2026-08-19, 2nd correction), confirmed live via TWO separate
    dispatches (one using .clear(), one using 40x KEYCODE_DEL) that both
    landed on the exact SAME "12 mask dots" symptom: 12 == 4x3, consistent
    with the field starting pre-filled with the OLD 3-char password already
    (this is CommCare's "Login again" screen after a mid-session binary
    swap - app data/prefs survive the swap, and the app appears to
    pre-fill remembered credentials) and EVERY one of our 3 retries
    APPENDING another "123" rather than replacing it - i.e. KEYCODE_DEL
    (backspace, deletes the character BEFORE the cursor) was ALSO a no-op
    here. That's exactly what happens if the initiating tap lands the
    cursor at the START of the existing text rather than the end (likely
    for a short, left-aligned field where "tap the element's center" is
    very close to position 0) - backspace has nothing before the cursor to
    delete. Sends BOTH backward (KEYCODE_DEL) and forward
    (KEYCODE_FORWARD_DEL, deletes the character AFTER the cursor) delete
    events so the field clears regardless of which end the cursor is
    sitting at.

    UPDATE (3rd correction, 2026-08-22), confirmed live in CI twice over
    (prompted_updates/scenario_1_appium + scenario_2_appium, run
    32503332653): press_keycode() sends "mobile: pressKey", unsupported on
    that session's driver build (UnknownCommandError); the follow-up
    "mobile: shell" + `input keyevent` alternative then failed differently -
    "adb_shell" is a security-gated Appium server feature, disabled here.
    Both are vendor extensions with no guarantee of being enabled on
    whatever driver build a given BrowserStack session lands on. Reverted
    to the standard element .clear() command instead - part of every
    conformant driver's core WebDriver protocol, not an extension, so it
    can't fail with an unsupported-command error the way the last two
    attempts did. Per the 1st UPDATE above, .clear() alone was ALREADY
    known to be unreliable on this app's fields (silently leaves old text
    in place) - accepted here as a real tradeoff: a fix that doesn't always
    clear beats one that reliably crashes the whole session."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        els = driver.find_elements(AppiumBy.ID, resource_id)
        if els:
            for el in els:
                try:
                    el.clear()
                except Exception:
                    pass
            return True
        time.sleep(_DEFAULT_POLL)
    return False


def field_text_length(driver, resource_id):
    """Length of a field's own `text` attribute, found fresh by id - used
    to verify typed input landed in fields where the LITERAL text can't be
    checked (e.g. a masked password field, which still exposes a
    length-matching run of mask characters in its `text` attribute even
    though the real characters aren't recoverable). Returns None if the
    element can't be found at all, distinct from 0 (found but empty)."""
    els = driver.find_elements(AppiumBy.ID, resource_id)
    if not els:
        return None
    try:
        return len(els[0].get_attribute("text") or "")
    except StaleElementReferenceException:
        return None


def type_into_focused(driver, text):
    """Types into whatever currently has focus, without locating it by id
    first - confirmed live (2026-08-19) that a saved failure hierarchy dump
    can show an almost-empty accessibility tree (a lone
    displayed="false" FrameLayout) at the exact moment a field is visibly
    focused with the keyboard open on screen (confirmed via a real
    screenshot) - a genuine accessibility-tree-vs-visual-content mismatch
    on this Appium server, not a wrong resource-id. Use this instead of
    tap_by_id+input_text right after a tap that's already known to open the
    keyboard and focus the field as a side effect. `mobile: type` is a
    supported command on this server (confirmed via a real
    UnknownMethodException's own listed-supported-commands earlier)."""
    driver.execute_script("mobile: type", {"text": text})


def wait_visible_id(driver, resource_id, timeout=10, optional=False, poll=_DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if driver.find_elements(AppiumBy.ID, resource_id):
            return True
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Element id={resource_id!r} never became visible within {timeout}s")


def wait_not_visible_id(driver, resource_id, timeout=10, optional=False, poll=_DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not driver.find_elements(AppiumBy.ID, resource_id):
            return True
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Element id={resource_id!r} still visible after {timeout}s")


def hide_keyboard(driver):
    try:
        driver.hide_keyboard()
    except Exception:
        # Same "may not always be open" tolerance every hideKeyboard call
        # needs elsewhere in this repo - a no-op if no keyboard is showing.
        pass
    reassert_portrait(driver)


def reassert_portrait(driver):
    """UPDATE (2026-08-20), per direct user report + confirmed live via a
    real screenshot: despite AppiumBrowserStackClient.start_session already
    setting bstack:options' deviceOrientation, the Appium `orientation`
    capability, AND a one-time driver.orientation setter right after
    session start, a real dispatch still landed in landscape - a single
    assertion at session start doesn't survive later in-session rotation.
    The keyboard opening/closing was already suspected (see
    _install_app_by_code_once's own UPDATE comment, from a hierarchy dump
    caught mid-rotation-animation) as the likely trigger, so this re-
    asserts portrait every time hide_keyboard runs (called after every
    text-entry step) rather than relying on one assertion to stick for the
    whole session. Best-effort - swallows errors the same way the orientation
    setter can't be guaranteed to always succeed mid-transition."""
    try:
        driver.orientation = "PORTRAIT"
    except Exception:
        pass


def back(driver):
    driver.back()


def swipe_up_on(driver, resource_id, percent=0.75, optional=False):
    """Port of Maestro's `swipe: direction: UP, id: <resource_id>` -
    scrolls a scrollable element (e.g. flows/common/logout.yaml's
    nsv_home_screen) up by `percent` of its own height. No-op (returns
    False) if the element isn't found and optional=True; raises otherwise.

    UPDATE (2026-08-20), confirmed live (a real failure landing on the
    module list instead of logging out, reproduced across multiple
    dispatches): UiAutomator2's `mobile: swipeGesture` can register as a
    plain TAP at its start point instead of a scroll on this Appium
    server/device, independent of render timing - it's meant for general
    swipe gestures, not guaranteed-scroll. `mobile: scrollGesture` is
    UiAutomator2's dedicated scroll primitive (same direction/percent
    params) and doesn't carry that tap-fallback ambiguity, so it's used
    here instead."""
    els = driver.find_elements(AppiumBy.ID, resource_id)
    if not els:
        if optional:
            return False
        raise RuntimeError(f"swipe_up_on: id={resource_id!r} not found")
    driver.execute_script("mobile: scrollGesture", {
        "elementId": els[0].id,
        "direction": "up",
        "percent": percent,
    })
    return True


