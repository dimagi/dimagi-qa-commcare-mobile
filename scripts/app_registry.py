"""
Registry of apps that flows/common/install_app_by_code.yaml needs a fresh
install code for. Maps an UPPER_SNAKE key to (domain, app_id) - NEVER a code
value. A code is tied to one specific build and goes stale the instant
someone cuts + publishes a new version (HQClient.get_app_install_code's
docstring), so run_suite.py resolves it fresh at the start of every run and
injects it as env var APP_CODE_<KEY> - no flow file or script in this repo
should ever hardcode a literal code.

app_id/domain come from each app's HQ Releases page URL, .../a/<domain>/
apps/view/<APP_ID>/releases/, cross-referenced against the Master Mobile
Plan (2026) sheet's own per-tab Domain links (2026-08-08).

Every entry gets the media-inclusive code (get_app_install_code's own
include_media=True default) rather than tracking per-app whether it "has"
multimedia - confirmed live on Performance Testing: the no-media code
installs the app fine but then gets permanently stuck on a "multimedia has
not been installed" screen, reproducing identically across multiple
different builds (not a timing/version issue). Requesting media for an app
that has none is a safe no-op, so there's no reason to special-case this
per app and risk missing the next one.
"""

APP_REGISTRY = {
    "BASIC_TESTS": ("qateam", "cdfa6c85eb594b23b0c08729cd2beff1"),
    "MULTIMEDIA": ("qateam", "4df9b7f7e66740a2bd9e02371af832b1"),
    "RIGHT_TO_LEFT": ("qateam", "1abba0dead4daede49abc56c04e56ae0"),
    "DATE_WIDGETS": ("qateam", "02a769c498fa428f89978b61e2846317"),
    "SESSION_EXPIRATION": ("qateam", "825e17ec246c487ab2d51c6696463898"),
    "OTHER": ("qateam", "0d7b77064440473a859fd19174806992"),
    "MOBILE_UPDATES_1_2": ("qateam", "424db1b7c64a94e3e4cdc03c6cc61038"),
    "CASE_MANAGEMENTS": ("qateam", "ca82bb0c5cc043a781d96437ee83944b"),
    "UPDATE_TEST_ALTERNATE": ("qateam", "7e8e7e8857f7466495888a37952e7ad0"),
    "RATE_LIMITED": ("rate-limited", "35589b21ffde2cd1e7be968088acd620"),
    "PERFORMANCE_TESTING": ("qateam", "4a1189cb56c44277906e9fc058838ebc"),
    "CONDITIONAL_ENUM": ("qateam", "2b3f11fa7e4f4d1a9d94aa9a93272e80"),
    "EXTERNAL_APP_FIXTURE": ("sansar", "e45245362793f25c9692791c58d10b15"),
    # Same app/domain as BASIC_TESTS, but deliberately NOT run through
    # max_commcare_version filtering (see NO_VERSION_FILTER_KEYS below) -
    # this is Basic Tests' single most recent build, whatever it requires.
    "BASIC_TESTS_LATEST": ("qateam", "cdfa6c85eb594b23b0c08729cd2beff1"),
}

# Keys that must always resolve to the app's single most recent build,
# bypassing get_app_install_code's max_commcare_version safety filter -
# confirmed live (2026-08-08, per docs/[Master] Mobile Plan (2026).xlsx's
# "Updates" tab, the "2.49 Tests" section): flows/updates_2_49/
# prompted_update_scenario_01_ill_update_later.yaml and
# _02_forced_commcare_update.yaml deliberately need the CommCare-APK-
# version-mismatch dialog ("New version of CommCare is Available"/
# "...is Required") to actually appear - that dialog is the feature under
# test, not an installation obstacle. Their own HQ pre-step
# (setup_02_commcare_version_plus_one.json) sets the Basic Tests app's top
# build to require one CommCare version ahead of what's installed
# specifically to trigger it; picking an older, version-filtered build
# instead would make the dialog never appear and both tests meaningless.
NO_VERSION_FILTER_KEYS = {"BASIC_TESTS_LATEST"}
