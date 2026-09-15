"""
Master Mobile Plan (2026) > Form Submissions > "Active users login" (row 60),
"Inactive users login" (row 61), "Deactivated user login" (row 62).

Each row asks whether HQ's own active/inactive mobile-worker state actually
gates on-device login - not something Maestro can drive on its own (no HQ
pre-step mechanism exists for toggling a user's activation state), but a
plain Appium session plus the new HQClient.create_mobile_worker()/
find_commcare_user()/set_commcare_user_active() methods (scripts/hq_client.py)
covers it directly. Uses Appium (not Maestro) purely for consistency with
every other HQ-pre-step-plus-device-assertion scenario in this program - the
on-device steps themselves need nothing Maestro couldn't also do.

ACCOUNT: a dedicated mobile worker, `maestro_inactive` (password "123", same
convention as this repo's other maestro-provisioned accounts - see
flows/externalapp_tests/setup_03_login_and_background.yaml's own citation),
created once via create_mobile_worker() (idempotent - a second call finds
the existing account instead of erroring) rather than reusing
CC_TEST_USERNAME or HQ_MOBILE_WORKER_USERNAME, both load-bearing for many
other flows this program can't risk deactivating mid-suite. "Active users
login" reuses HQ_MOBILE_WORKER_USERNAME (mobile_maestro) instead, read-only -
its own active state is never touched here.

REAL BEHAVIOR CONFIRMED LIVE (2026-09-15, BrowserStack sessions
d9d8eaf27d0d70d04ab2d0f2dd18890ff76d03c0 and
3a10a8fb08f251e9356223abe41d7f1d18e1fcb1, both against the same
maestro_inactive/123 credentials on [Master] Basic Tests): a DEACTIVATED
user's fresh login attempt (never yet installed/synced on this device)
reaches the login screen fine but fails with a real, stable "Invalid
Username or Password" message (not a distinct "deactivated"-specific
string) - confirmed by then reactivating the SAME account via HQ and
confirming login succeeds with the SAME credentials, proving the
activation state (not a bad password) was the cause. That's the assertion
run_active_users_login/run_inactive_users_login check for a fresh-install
login attempt.

run_deactivated_user_login's own last step is different: confirmed live
(2026-09-15, session dd63026398adf059b3684b3384d731d455b93181) that
deactivating an account that's ALREADY logged in on-device doesn't block
anything until the next sync - tapping "Sync with Server" afterward surfaces
a real, distinct message: "Your account has been deactivated, please
contact your domain admin to reactivate". This matches the sheet's own
row 62 steps far more precisely than reusing the fresh-login-attempt
rejection text (its own step 4/5 are literally "perform sync" / "we are
able to see the message getting displayed as the user is deactivated"),
so that's what the final step below checks instead.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import appium_helpers as h
from appium_scenarios import APP_ID, _run_steps, _install_app_by_code, _login

MAESTRO_INACTIVE_USERNAME = "maestro_inactive"
MAESTRO_INACTIVE_PASSWORD = "123"


def run_active_users_login(driver, app_code, username, password):
    steps = [
        ("Install [Master] Basic Tests app", lambda: _install_app_by_code(driver, app_code)),
        ("Log in as the already-ACTIVE mobile worker", lambda: _login(driver, username, password)),
        ("Verify the home screen (login succeeded)",
         lambda: h.wait_visible_id(driver, f"{APP_ID}:id/nsv_home_screen", timeout=20)),
    ]
    return _run_steps(steps)


def run_inactive_users_login(driver, app_code, hq, user_id, username=MAESTRO_INACTIVE_USERNAME,
                              password=MAESTRO_INACTIVE_PASSWORD):
    steps = [
        ("Deactivate the test mobile worker on HQ", lambda: hq.set_commcare_user_active(user_id, False)),
        ("Install [Master] Basic Tests app", lambda: _install_app_by_code(driver, app_code)),
        ("Attempt login as the DEACTIVATED mobile worker", lambda: _login(driver, username, password)),
        ("Verify login was rejected (Invalid Username or Password)",
         lambda: h.assert_visible_text(driver, "Invalid Username or Password")),
    ]
    return _run_steps(steps)


def run_deactivated_user_login(driver, app_code, hq, user_id, username=MAESTRO_INACTIVE_USERNAME,
                                password=MAESTRO_INACTIVE_PASSWORD):
    """Login (rejected while deactivated) -> reactivate -> login (succeeds)
    -> deactivate again -> sync (rejected with a real "account has been
    deactivated" message), per the sheet's own row 62 steps. Ends deactivated
    so it never leaves a stray active credential behind, and so it matches
    Inactive users login's own precondition regardless of which of these two
    runs (or re-runs) last."""
    steps = [
        ("Deactivate the test mobile worker on HQ", lambda: hq.set_commcare_user_active(user_id, False)),
        ("Install [Master] Basic Tests app", lambda: _install_app_by_code(driver, app_code)),
        ("Attempt login while deactivated (expect rejection)", lambda: _login(driver, username, password)),
        ("Verify login was rejected (Invalid Username or Password)",
         lambda: h.assert_visible_text(driver, "Invalid Username or Password")),
        ("Reactivate the test mobile worker on HQ", lambda: hq.set_commcare_user_active(user_id, True)),
        ("Log in again now that it's reactivated", lambda: _login(driver, username, password)),
        ("Verify the home screen (login succeeded after reactivation)",
         lambda: h.wait_visible_id(driver, f"{APP_ID}:id/nsv_home_screen", timeout=20)),
        ("Deactivate again while still logged in on-device", lambda: hq.set_commcare_user_active(user_id, False)),
        ("Attempt to sync now that the account is deactivated again",
         lambda: h.tap_by_text(driver, "Sync with Server", timeout=10)),
        ("Verify the deactivation message appears on sync",
         lambda: h.wait_visible_text(
             driver, "Your account has been deactivated, please contact your domain admin to reactivate",
             timeout=20)),
    ]
    return _run_steps(steps)
