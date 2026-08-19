"""
Maestro-primitive equivalents built on top of a live Appium `driver`, so
scripts/appium_scenarios.py can read like the Maestro YAML flows it
replaces (tapOn/inputText/extendedWaitUntil/assertVisible/assertNotVisible)
instead of raw WebDriver calls.

Text matching walks every element's `text` attribute out of Appium's XML
`page_source` and regex/exact-matches against it in Python - the same
technique already used throughout this repo's own investigations this
session to read BrowserStack's Maestro failure hierarchy dumps (walk every
node's `text` attribute, since neither tool exposes a native regex-match
locator against Android's `text` attribute).
"""
import re
import time

from appium.webdriver.common.appiumby import AppiumBy

_TEXT_ATTR_RE = re.compile(r'text="((?:[^"\\]|\\.)*)"')
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
            els[0].click()
            return True
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Element id={resource_id!r} never became tappable within {timeout}s")


def tap_by_text(driver, pattern, timeout=10, regex=False, optional=False, poll=_DEFAULT_POLL):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for el in driver.find_elements(AppiumBy.XPATH, "//*[@text]"):
            if _matches(_unescape_xml(el.get_attribute("text") or ""), pattern, regex):
                el.click()
                return True
        time.sleep(poll)
    if optional:
        return False
    raise TimeoutError(f"Text {'matching regex ' if regex else ''}{pattern!r} never became tappable within {timeout}s")


def input_text(driver, resource_id, text, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        els = driver.find_elements(AppiumBy.ID, resource_id)
        if els:
            els[0].clear()
            els[0].send_keys(text)
            return
        time.sleep(_DEFAULT_POLL)
    raise TimeoutError(f"Element id={resource_id!r} never became available for input within {timeout}s")


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


def back(driver):
    driver.back()
