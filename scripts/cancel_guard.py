"""
Stop BrowserStack work when a CI job is cancelled (or the process is killed).

Why: cancelling a GitHub Actions job only stops the runner process. The BrowserStack
Maestro builds / Appium sessions that process had already started keep running on real
devices, holding parallel-session slots until they finish on their own (seen 2026-10-07:
five builds still running 20+ minutes after the run was cancelled).

GitHub's cancel sends SIGINT, then SIGTERM ~7.5s later, then SIGKILL ~2.5s after that.
This module:
  * keeps a registry of BrowserStack builds (and Appium drivers) this process started;
  * on SIGINT/SIGTERM stops them all straight away - via BrowserStack's documented
        POST /app-automate/maestro/builds/<id>/stop        (Maestro builds)
        driver.quit()                                       (Appium sessions)
    - then raises KeyboardInterrupt so the caller unwinds (no further chunks are triggered);
  * also appends every started build to reports/browserstack_builds.jsonl so the workflow's
    `if: cancelled()` step (scripts/stop_run_builds.py) can stop anything still running even
    if this process was SIGKILLed before it could.

Everything here is best-effort and must never raise out of a handler: a failed stop is logged,
not fatal. It only ever touches builds this run itself triggered.
"""
import atexit
import json
import os
import pathlib
import signal
import threading
import time

import requests

STOP_URL = "https://api-cloud.browserstack.com/app-automate/maestro/builds/{build_id}/stop"
REGISTRY_PATH = pathlib.Path(__file__).resolve().parent.parent / "reports" / "browserstack_builds.jsonl"

_lock = threading.Lock()
_builds = set()
_stopped = set()
_drivers = []
_installed = False
_stopping = False


def _auth():
    return (os.environ.get("BROWSERSTACK_USERNAME"), os.environ.get("BROWSERSTACK_ACCESS_KEY"))


def _append(record):
    try:
        REGISTRY_PATH.parent.mkdir(exist_ok=True)
        with REGISTRY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # the registry is only a backup for the cancelled() step


def register_build(build_id):
    with _lock:
        _builds.add(build_id)
    _append({"build": build_id, "event": "start"})


def unregister_build(build_id):
    with _lock:
        _builds.discard(build_id)
    _append({"build": build_id, "event": "done"})


def register_driver(driver):
    with _lock:
        _drivers.append(driver)
    original_quit = driver.quit

    def quit_and_forget(*args, **kwargs):
        with _lock:
            if driver in _drivers:
                _drivers.remove(driver)
        return original_quit(*args, **kwargs)

    driver.quit = quit_and_forget


def stop_build(build_id, auth=None, timeout=10, attempts=2):
    """POST the documented stop endpoint. True when the build is stopped or already finished.

    HTTP 422 ("There was an issue with stopping the build") is what BrowserStack returns for a
    build that is no longer running, so it counts as done rather than a failure to retry."""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(STOP_URL.format(build_id=build_id), auth=auth or _auth(), timeout=timeout)
        except requests.RequestException as exc:
            print(f"[cancel_guard] stop build {build_id[:12]} attempt {attempt}/{attempts}: "
                  f"{type(exc).__name__}", flush=True)
            continue
        print(f"[cancel_guard] stop build {build_id[:12]} -> HTTP {resp.status_code}", flush=True)
        return resp.status_code < 300 or resp.status_code == 422
    return False


def stop_all_active(deadline=8):
    """Stop every build this process started that is not confirmed stopped, and quit open Appium
    drivers. Builds are stopped IN PARALLEL and the call returns within `deadline` seconds even if
    BrowserStack is slow: GitHub gives a cancelled step only ~7.5s (SIGINT -> SIGTERM) + ~2.5s
    before SIGKILL, so a sequential loop could be killed half way. Anything not confirmed here is
    retried on the next signal and by the workflow's `if: cancelled()` step."""
    global _stopping
    if _stopping:
        return
    _stopping = True
    try:
        with _lock:
            builds = [b for b in _builds if b not in _stopped]
            drivers = list(_drivers)

        def stop_one(build_id):
            if stop_build(build_id):
                with _lock:
                    _stopped.add(build_id)

        threads = [threading.Thread(target=stop_one, args=(b,), daemon=True) for b in builds]
        for driver in drivers:
            threads.append(threading.Thread(target=_quit_driver, args=(driver,), daemon=True))
        for t in threads:
            t.start()
        end = time.monotonic() + deadline
        for t in threads:
            t.join(max(0.0, end - time.monotonic()))
    finally:
        _stopping = False


def _quit_driver(driver):
    try:
        driver.quit()
    except Exception as exc:  # best effort: the session may already be gone
        print(f"[cancel_guard] driver.quit() failed: {type(exc).__name__}", flush=True)


def _handle_signal(signum, _frame):
    print(f"[cancel_guard] received signal {signum} - stopping BrowserStack builds/sessions", flush=True)
    stop_all_active()
    raise KeyboardInterrupt(f"terminated by signal {signum}")


def install():
    """Idempotent. Safe to call from every client constructor."""
    global _installed
    if _installed:
        return
    _installed = True
    if threading.current_thread() is threading.main_thread():
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                signal.signal(sig, _handle_signal)
    atexit.register(stop_all_active)  # normal/unhandled exit with something still registered
