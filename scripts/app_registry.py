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
    # UPDATE (2026-08-10), superseded by the six pinned TEST_ONE/TWO/THREE
    # entries below: this single unpinned entry always resolved to whatever
    # the app's CURRENT top build happened to be, which is wrong for these
    # tests - "Test One"/"Test Two"/"Test Three" are three SPECIFIC,
    # already-existing builds a flow must switch between mid-narrative (the
    # whole point of a "reinstall/update" test), not "whatever's newest
    # today". Confirmed live via a user-supplied screen recording + the
    # Recovery Measures sheet's own C2 provisioning note ("Revert to test
    # one... repeat process for version two and three") - Dimagi has
    # ALREADY cut these three builds once per CommCare-version cycle; nothing
    # here creates new ones.
    #
    # Master Mobile Plan (2026) > Recovery Measures sheet, two DISTINCT apps
    # (confirmed live via each app's own Releases page title, 2026-08-10):
    #   c49c34a1d74297ecb6ecef7a7c5d3f88 = "Recovery: Reinstall and Update
    #     App" (A6's own linked URL) - used by update_app_flow.yaml,
    #     reinstall_update_app_flow.yaml,
    #     reinstall_update_03_update_to_test_two.yaml.
    #   8009e8a2814a465b818d56284011b1e9 = "Recovery: Offline Reinstall and
    #     Update App" (a SEPARATE app, despite the similar name and the
    #     sheet's own A6 link pointing at the other one - confirmed via this
    #     app's Releases page title, and via the on-device toolbar title
    #     matching c49c34a1d74297ecb6ecef7a7c5d3f88 in the user's recording)
    #     - used by offline_reinstall_update_app_flow.yaml,
    #     offline_05/06/08/09_*.yaml.
    # A registry entry's optional 3rd tuple element pins get_app_install_code
    # to that EXACT build (bypassing "current top build" resolution) - see
    # resolve_app_codes()'s own updated docstring in hq_client.py.
    "RU_TEST_ONE": ("qateam", "c49c34a1d74297ecb6ecef7a7c5d3f88", "fc035b7fa49f49e88ab3fbfe5c5c3e4c"),
    "RU_TEST_TWO": ("qateam", "c49c34a1d74297ecb6ecef7a7c5d3f88", "e2bbb57c020f4c4b9f30d7ac6442a9b0"),
    "RU_TEST_THREE": ("qateam", "c49c34a1d74297ecb6ecef7a7c5d3f88", "c0c55463a7fa43f7a6409c54849159a6"),
    "OFFLINE_TEST_ONE": ("qateam", "8009e8a2814a465b818d56284011b1e9", "584e35da607e4df7a03733463da286bb"),
    "OFFLINE_TEST_TWO": ("qateam", "8009e8a2814a465b818d56284011b1e9", "a730fb7f1aee4e38996d01b1e16ac94c"),
    "OFFLINE_TEST_THREE": ("qateam", "8009e8a2814a465b818d56284011b1e9", "e2e7a3dcbaf94035b2ce8dbd5f1dd3b3"),
    # Master Mobile Plan (2026) > Recovery Measures sheet's own hyperlinks
    # (2026-08-10) - four more, single-build apps (the sheet's own C47/C54
    # rows both say "ensure the ONLY version is marked as released", unlike
    # the three-version Reinstall/Update apps above), so an unpinned entry
    # (always resolves to the app's current top build) is enough - no
    # specific saved_app_id to pin.
    "CC_REINSTALL_NEEDED": ("qateam", "fc3b94e8ae9a445680df496ee783439d"),
    "CC_UPDATE_NEEDED": ("qateam", "8fc02611dd694001a147749a6ea558c0"),
    "POINT_RELEASE": ("qateam", "df1c05ddfb640f32c9d453f780442ce4"),
    "HEARTBEAT": ("qateam", "35b9a60884c8f8e69e507c56cbe8f370"),
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
