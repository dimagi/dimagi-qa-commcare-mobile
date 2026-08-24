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
    # UPDATE (2026-08-20), per direct user-supplied link, confirmed live via
    # HQClient.list_releases (name == "Case Managements!"): the
    # prompted_updates varying_prompt_01/03/04/05 flows were installing
    # APP_CODE_BASIC_TESTS - the wrong app - while
    # hq_setup/prompted_updates/varying_prompt_setup.json's custom
    # properties (num-views-before-reducing-frequency etc.) were always
    # set on THIS app instead. Every one of those flows' own recordings
    # showed the update prompt firing on "Case Managements!", not "Basic
    # Tests" - confirming the mismatch, not just the missing id.
    "CASE_MANAGEMENTS": ("qateam", "ca82bb0c5cc043a781d96437ee83944b"),
    # UPDATE (2026-08-20), per direct user instruction: the varying_prompt_*
    # flows must install an EXISTING, already-cut build (identified by its
    # release note, matching the test plan's "Install the application with
    # release notes '3.4,1'") rather than a build hq_setup mints fresh each
    # run - the earlier varying_prompt_setup.json's create_new_build action
    # was creating a needless new "3.4,1"-commented build on every single
    # dispatch (confirmed live via HQ - v233/v234 today alone), and those
    # Maestro-authored builds must never be selected/relied on going forward
    # (the user can't delete them). Pinned like RU_TEST_ONE/TWO/THREE below -
    # v230, build_comment "3.4.1  - 2.64" (by Sameena Shaik, CommCare 2.64.0,
    # matching the test APK), confirmed live via HQClient.list_releases. A
    # SEPARATE, unpinned CASE_MANAGEMENTS entry above stays as-is for
    # flows/form_submissions/save_to_case_01_03... which wants the current
    # top build, not this specific historical one.
    "CASE_MGMT_VP": (
        "qateam", "ca82bb0c5cc043a781d96437ee83944b", "2216dbc11efb4b3dabd095e1727dd53e",
    ),
    "MULTIMEDIA": ("qateam", "4df9b7f7e66740a2bd9e02371af832b1"),
    "RIGHT_TO_LEFT": ("qateam", "1abba0dead4daede49abc56c04e56ae0"),
    "DATE_WIDGETS": ("qateam", "02a769c498fa428f89978b61e2846317"),
    "SESSION_EXPIRATION": ("qateam", "825e17ec246c487ab2d51c6696463898"),
    "OTHER": ("qateam", "0d7b77064440473a859fd19174806992"),
    # UPDATE (2026-08-21), confirmed live via HQClient.list_releases +
    # scripts/run_appium_suite.py's own fix (see its citation for the full
    # story): the plain unpinned form here resolves (via max_commcare_
    # version's "newest build under the ceiling" rule) to v79, the current
    # top build - which is ALREADY "Mobile Updates - Test 1_2!", not
    # "Version 1" as the sheet's own Setup step requires ("Install Version
    # 1... Set Version 2 as released"), and attempting to release v79 (or
    # the similarly-old v26) can fail live with a real HQ platform error
    # ("mobile UCR restore version... needs to be updated to V2.0" - an
    # app-level migration blocker, not something this repo can push
    # through). Pinned to v66 ("2.55 - Version 1", already released,
    # confirmed live this one does NOT hit that blocker) - both of this
    # key's real callers (scenario_1/scenario_2's Maestro flows) want
    # "Version 1" as the starting install per their own Setup steps, and
    # v71 ("2.55 (Heavier) Version 2", already released=True) is already
    # the next-highest-numbered released build, so the "update to Version
    # 2" step in both flows has something correct to land on without
    # needing to touch it separately.
    "MOBILE_UPDATES_1_2": ("qateam", "424db1b7c64a94e3e4cdc03c6cc61038", "969f2df0118b4619ac386f123c58edd3"),
    # UPDATE (2026-08-21), per direct user-supplied recording: scenario_3's
    # sheet references "Mobile Updates - Test 3", which does not exist under
    # that name anywhere in the qateam domain's app list (confirmed live via
    # /apps/api/list_apps/, all 123 apps checked) - the real app, per the
    # recording's own on-screen title bar, is "Mobile2.47". Its release
    # history (HQClient.list_releases) has a genuine Version 1-4 progression
    # matching the sheet's own notes ("Version 1 - Forms A and B... Version
    # 4 - Made a change in Form A"): v19 ("Version V1 - Form A & B", CC
    # 2.45.2, already released) is the only build low enough for the old
    # 2.45 APK under test to install via app-code at all, and v68 ("Version
    # 4 A(updated)&B", CC 2.57.0) is ALREADY the current top-released build
    # - satisfying Update 10's "mark Version V4 as Released" step for free.
    # Pinned with release_first=False (the 4th tuple element) since v19 is
    # already released and marking it again hits the SAME real HQ UCR
    # migration error as MOBILE_UPDATES_1_2's old v26/v79 candidates above -
    # confirmed live, not assumed.
    "MOBILE2_47": ("qateam", "4fc92eeed6032a7c650a10f6627f6cea", "3d85d0fd6a7a48c5abbd0c0c0f013ad2", False),
    # UPDATE (2026-08-21), per direct user correction: scenario_4/5's linked
    # app ("Mobile Updates Test 4/5 Master"'s linked app) lives under the
    # "let-sdoit" domain, NOT qateam - confirmed live via HQClient.
    # list_releases against the real app link the user supplied
    # (https://www.commcarehq.org/a/let-sdoit/apps/view/07f49277d22d6c77ecc2df0489a80aae/).
    # An earlier lookup under qateam found a DIFFERENT, wrong app that
    # happened to share a similar name - explicitly superseded by this one.
    # Both scenario_4's Installation 2 and scenario_5's Installation 1 want
    # "Version V2" installed first, then updated to "Version V6" - pinned
    # to v19 ("Version V2 - Form A & B", CC 2.45.2, already released=True)
    # with release_first=False since it's already released. v23 ("Version
    # V6 - Age question added in Form A", CC 2.45.2) is ALREADY the
    # current top-released build for this app (nothing above it is
    # released, unlike the wrong qateam app's v93 which is a real,
    # actively-maintained build) - so no further HQ action is needed for
    # the "Update App" step in either scenario; it naturally finds v23.
    # Switches both scenarios away from the "See Apps for My User" mobile-
    # worker install (which was fetching whatever's CURRENTLY the top
    # release directly, skipping "Version V2" entirely, and - for
    # scenario_5 specifically - was also logging into the WRONG domain via
    # the shared HQ_MOBILE_WORKER_USERNAME/HQ_DOMAIN qateam vars) to a
    # pinned app-code install instead, matching every other fix today.
    "LINKED_APP_TEST45": ("let-sdoit", "07f49277d22d6c77ecc2df0489a80aae", "e3ce9341eb014e878fae1977c9903818", False),
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
    # UPDATE (2026-08-24), per direct user-supplied app link: "Recovery:
    # Point Release" is NOT single-build after all - confirmed live via
    # list_releases it has 4 real builds (v9 unreleased, v11 released, v12
    # unreleased, v14 released), and per the user's own direction this is
    # the REAL app for update_app_02_forced_two_to_three.yaml's Test Two ->
    # Test Three transition (moved off the "Reinstall and Update App" app
    # this flow used before, which triggers the wrong recovery-measure
    # type for this row's own auto-update-no-chooser narrative - see that
    # flow's own header and the Recovery Measures tab's "Update 5/6"
    # measure-type-mismatch note this resolves). Pinned to the two already-
    # released builds (release_first=False - no HQ release action needed,
    # both already are): v11 as the older "Test Two" state, v14 (the
    # current top) as "Test Three". Named "PT_REL_*" rather than the more
    # obvious "POINT_RELEASE_TEST_*" - the latter's "APP_CODE_" + key form
    # is 31-33 chars, over the confirmed 30-char setEnvVariables key limit
    # (same class of bug already hit twice today).
    "PT_REL_TWO": ("qateam", "df1c05ddfb640f32c9d453f780442ce4", "8ab3f997208c4c6899e48817037d27b1", False),
    "PT_REL_THREE": ("qateam", "df1c05ddfb640f32c9d453f780442ce4", "b0833a601b404dda969e47e4a4b0589b", False),
    "HEARTBEAT": ("qateam", "35b9a60884c8f8e69e507c56cbe8f370"),
    # A genuinely different, unregistered app from "Basic Tests" - see
    # capture_01_gather_signature.yaml's own header for the full root-cause
    # writeup (the recording's real signature-canvas widget only renders on
    # THIS app, not on APP_CODE_BASIC_TESTS). User-confirmed app_id
    # (2026-08-13) via its HQ Releases page URL.
    #
    # UPDATE (2026-08-24), per direct user-supplied app link + screen
    # recording: this SAME app ("[Master] Basic Tests NS Copy" - its own
    # real app description literally reads "Basic test app - Please make
    # no changes to this!!!") is also where updates_2_49's 3 tests
    # (prompted_update_scenario_01/02, auto_cc_update_03) now live, off the
    # shared BASIC_TESTS/BASIC_TESTS_LATEST entries above. Two real
    # reasons, not just a rename: (1) a dedicated copy means these tests'
    # own apk_prompt/app_prompt/apk_version mutations (via
    # set_prompt_update_settings) don't collide with every OTHER flow in
    # this repo that also installs the shared BASIC_TESTS app expecting
    # its normal, unmutated state - the exact same class of shared-HQ-
    # state collision already root-caused and fixed today for
    # prompted_updates' own scenario_01/02 (see appium_scenarios.py's
    # 2026-08-22 UPDATE on run_prompted_update_scenario_01/02_*).
    # (2) confirmed live via a real superuser session cookie that this
    # app's Manage Update Settings dropdown genuinely has 131 apk_version
    # choices for a superuser (including "2.63.2/latest", "2.64.0/latest"
    # ["alpha"], "2.65.0/latest" ["dev"]) vs only 128 for a regular web
    # user (tops out at "2.63.1/latest") - alpha/dev CommCare versions are
    # real, HQ-known choices, just hidden from a regular account's own
    # rendered <option> list, not missing from the underlying data. Also
    # confirmed live that the regular (non-superuser) web-user account CAN
    # WRITE "2.65.0/latest" via set_prompt_update_settings once its CSRF
    # cookie is primed (see login()'s own 2026-08-24 UPDATE in
    # hq_client.py for a related real bug this surfaced) - the visibility
    # restriction is template/rendering-only, not a write-permission gate,
    # so no superuser credentials are needed for actual test dispatches.
    # (An earlier same-day attempt used "2.65.0/dev" - accepted by the
    # form but disproven live via 5 real login attempts over 12 minutes
    # that never triggered the prompt; superseded by the superuser-
    # dropdown discovery and the real "2.65.0/latest" value above.)
    "BASIC_TESTS_NS_COPY": ("qateam", "8a04cdf1b9474ee2b090b1d7896b1bd7"),
    # Same split as BASIC_TESTS/BASIC_TESTS_LATEST above, for the same
    # reason - prompted_update_scenario_01/02 need the unfiltered variant
    # (see NO_VERSION_FILTER_KEYS below); auto_cc_update_03 uses the plain
    # filtered entry above. UPDATE (2026-08-24), confirmed live (a real 422
    # from BrowserStack's own /android/build endpoint): named
    # "BASIC_TESTS_NS_COPY_LATEST" at first, but "APP_CODE_" + that key is
    # 35 chars - over the confirmed 30-char setEnvVariables key limit
    # already root-caused once this session for
    # CASE_MANAGEMENTS_VARYING_PROMPT -> CASE_MGMT_VP. Shortened to fit
    # (26 chars with the APP_CODE_ prefix) rather than repeat that bug.
    "BT_NS_COPY_LATEST": ("qateam", "8a04cdf1b9474ee2b090b1d7896b1bd7"),
    # A separate app from "Multimedia" - see lazy_video_05's own header for
    # the root-cause writeup (video-question forms, reached via a "Video
    # Questions" menu item that doesn't exist on APP_CODE_MULTIMEDIA).
    # User-confirmed app_id (2026-08-13) via its HQ Releases page URL.
    "LAZY_VIDEOS": ("qateam", "9348a6c0baa5b98d1082a8e94048697a"),
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
NO_VERSION_FILTER_KEYS = {"BASIC_TESTS_LATEST", "BT_NS_COPY_LATEST"}
