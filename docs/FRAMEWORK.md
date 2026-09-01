# Framework design

## Why this exists

The *Connect Test Case and Workflow Inventory* workbook's **CommCare Mobile**
tab lists 21 mobile workflows with an `Existing Script? (yes/no)` column.
Cross-referencing the `No` and `Partial Coverage` rows against the *[Master]
Mobile Plan (2026)* workbook's per-workflow tabs surfaced 11 workflows /
~218 individual manual test-case rows with no or partial automation:

| Status | Workflows |
|---|---|
| `No` | Mobile Pins, Right to Left Text, Update (partial and failed), Updates |
| `Partial Coverage` | Install, Update, Multimedia, Prompted Updates, ExternalApp Tests, Recovery Measures, Trigger Device Logs |

(`Browserstack/Android Virtual Devices`, priority 1, was excluded - its own
notes column already marks it "Manual - out of scope" since BrowserStack's
device catalog doesn't cover the very old Android versions that test needs.)

Reading every test case in those 11 workflows made one thing clear: **not
everything is automatable**. `coverage/coverage_matrix.csv` classifies all
218 rows into three buckets:

- **Automatable** (92 rows) - pure on-device Maestro steps, zero external dependency.
- **Partial** (75 rows) - on-device steps automatable, plus one scriptable
  external pre-step: an HQ API call (mark a build Released, toggle an update
  setting), an `adb`/Maestro `addMedia` push, a locale/network toggle, etc.
- **Not automatable** (51 rows) - genuine human judgment (visual RTL
  alignment, audio/video quality, image distortion), physical multi-device
  requirements, HQ's own browser-only features (Django Admin, Sumologic, App
  Preview/Web Apps), Play Store internal-tester infrastructure, timing-critical
  binary-update interleaving, or open-ended real-time waits (10 min-8 hrs).

The framework's job is to automate the first two buckets and leave a clear,
reasoned paper trail for the third.

## Architecture

```
GitHub release APK  ──▶  scripts/download_apk.py
                                   │
hq_setup/*.json     ──▶  scripts/hq_client.py  (CommCareHQ pre-steps)
                                   │
flows/**/*.yaml     ──▶  scripts/run_suite.py  ──▶  scripts/browserstack_client.py
                                                            │
                                                   BrowserStack App Automate
                                                        (Maestro v2 API)
```

`run_suite.py` is the only thing a human or CI job invokes directly; it wires
the other three together (see its docstring / `README.md` for CLI usage).

## Where each piece of information came from (provenance)

Nothing in this repo's HQ or BrowserStack integration was guessed from
training-data memory alone where it could instead be checked against a real
source:

- **GitHub release asset names**: checked live via `gh api
  repos/dimagi/commcare-android/releases/...` - asset naming is *not*
  consistent release to release (`app-commcare-release.apk` vs
  `commcare-2.63.1-release.apk`, and some releases ship no `.apk` at all).
  `download_apk.py` matches by pattern and falls back through recent releases
  rather than assuming a fixed filename.
- **CommCareHQ endpoints** (`hq_client.py`): read directly out of the
  `dimagi/commcare-hq` source on GitHub -
  `corehq/apps/app_manager/views/releases.py` (`release_build`, `save_copy`),
  `corehq/apps/app_manager/views/settings.py` (`edit_commcare_profile`,
  `PromptSettingsUpdateView`), and `forms.py`/`const.py` for the exact field
  choices. Each function in `hq_client.py` cites the source file/function it
  mirrors.
- **BrowserStack Maestro API** (`browserstack_client.py`): confirmed via
  BrowserStack's published docs (web search, since `browserstack.com` wasn't
  directly fetchable from this environment) - upload app, upload test-suite,
  trigger build, and poll build status endpoints under
  `api-cloud.browserstack.com/app-automate/maestro/v2/*`.
- **On-device selectors** (Maestro flow YAML): read out of `commcare-android`
  source *read-only* (this repo never modifies it) - mainly
  `app/instrumentation-tests/src/org/commcare/utils/InstrumentationUtility.kt`
  (existing Espresso helper - login/logout/dev-options patterns) and the
  relevant `app/src/org/commcare/activities/*.java` + `PaneledChoiceDialog.java`
  + `app/assets/locales/android_translatable_strings.txt` for exact on-screen
  text and resource IDs (e.g. PIN dialog strings, `edit_username`/`pin_entry`
  IDs). Every flow file comments which source file backs its selectors.

## Known gaps / things to verify before relying on this in production

Being upfront about what hasn't been exercised against a live system:

1. **HQ login (`hq_client.py: HQClient.login()`)**: CommCareHQ's login view
   is a `django-two-factor-auth` multi-step wizard, not a plain
   username/password POST. The client handles this generically (echoes back
   whatever hidden fields the login page renders rather than hardcoding the
   wizard's step-prefix), but this has **not** been run against a live HQ
   session. If it fails, use the `HQ_SESSION_COOKIE` escape hatch (grab the
   `sessionid` cookie from a logged-in browser session).
2. **`create_new_build`'s response shape**: `run_pre_step`'s `$LAST_BUILD_ID`
   chaining assumes the new build's ID is at `response["saved_app"]["id"]`.
   That's a reasonable guess from the Django view's code shape, not a
   confirmed live response - check it the first time you use a pre-step JSON
   that chains `create_new_build` into `mark_build_status`.
3. **BrowserStack Maestro build API's exact optional parameters** (tag
   filtering, `maestroVersion`, etc.) weren't fully enumerated - `run_suite.py`
   sidesteps this by filtering which flow files get zipped up-front (via each
   flow's `tags:` field) rather than relying on a BrowserStack-side tag filter.
4. **Recovery Measures message text**: the "needs to be reinstalled" /
   "Reinstall Using CCZ" / "Online Install" strings are **not** in
   `android_translatable_strings.txt` - Recovery Measures pages are
   configured per-app on HQ, so this text may be admin-configurable rather
   than fixed. `flows/recovery_measures/reinstall_update_app_flow.yaml` uses
   best-effort regex matching and flags this explicitly.
5. **ExternalApp Tests cross-app flows**: marked `Partial` rather than `Not
   automatable` because Maestro's `launchApp` can target a second installed
   app mid-flow. The spike on whether BrowserStack's Maestro product supports
   having a *second* APK installed in the same App Automate session is now
   **DONE** - confirmed via BrowserStack's `otherApps` capability (up to 3
   companion `bs://` app urls, pre-installed alongside `app` at session
   start), wired into `scripts/browserstack_client.py`'s
   `trigger_build(other_apps=[...])` and `scripts/run_suite.py --other-app
   <path>`. Flows now live under `flows/externalapp_tests/` - but the
   companion app itself (Dimagi's internal "Mobile API Testing App", built by
   a Jenkins job this environment can't reach) was never available to
   inspect, so those flows' appId and companion-app selectors are
   UNVERIFIED placeholders - see `flows/externalapp_tests/README.md` for the
   full caveat and how to drop in the real values once someone has the APK.
6. **Install 5/6's exact validation-error string** ("please enter all
   required fields" per the sheet) has no matching localization key found -
   left unasserted with a `TODO(verify)` comment rather than guessed.

## Conventions for new flows

- One flow file per test case, named `<workflow>/<lowercase_id>_<short_description>.yaml`.
- Tag every flow: `tags: [<workflow_name>, automatable|partial]`.
- Put anything reused more than once in `flows/common/` and pull it in via
  `runFlow`.
- **Each flow must be runnable in isolation** on a freshly-installed app. The
  original manual test cases are often written as a sequential narrative
  (e.g. "Pin 8" assumes a PIN was already set two test cases earlier in
  "Pin 7") - since a CI/BrowserStack run gives each flow a fresh app install,
  flows re-establish any assumed state themselves via `runFlow` to a common
  subflow, rather than depending on a previous flow having run first in the
  same session.
- Cite the source file/line (or string key) backing every non-obvious
  selector or expected text, the same way the existing flows do - it's the
  difference between a flow that's trustworthy and one that's a guess.
