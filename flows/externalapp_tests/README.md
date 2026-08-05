# ExternalApp Tests

Master Mobile Plan (2026) > **ExternalApp Tests** tab. Exercises CommCare's
external-app integration surface (a companion Android app launching CommCare
to start a form, query case/fixture data, and grant a "CommCare Key") against
a fixture app called **External App Fixture Tests**, deployed on the
`sansar` domain
(https://www.commcarehq.org/a/sansar/apps/view/e45245362793f25c9692791c58d10b15/
- see the tab's own `Domain:`/`Application:` header rows).

## The companion app

The "buttons" side of every test case here is driven by a second, separate
APK - Dimagi's internal **"Mobile API Testing App"**, built by an internal
Jenkins job:

    https://jenkins.dimagi.com/job/Mobile%20API%20Testing%20App/

That Jenkins instance is not reachable from this environment, so this repo
has **never seen the real APK**. Concretely, that means:

- **appId is an UNVERIFIED PLACEHOLDER.** Every flow below declares it as an
  `EXTAPP_APP_ID` env var defaulting to `com.dimagi.mobileapitestingapp`
  (a guess at Dimagi's usual reverse-DNS convention, not read out of any
  manifest). Whoever next has the real APK should grab its real package name
  (`aapt dump badging <apk>.apk | grep package:\ name`, or
  `adb shell pm list packages | grep -i mobileapi` after installing it) and
  either override `EXTAPP_APP_ID` at run time or edit the default in each
  flow's `env:` block - no other change should be needed.
- **On-screen button text is copied verbatim from the sheet's own wording**
  ("Start CommCare", "Acquire CommCare Key", "View Case Data", "View Fixture
  Data"), also exposed as env vars (`EXTAPP_BTN_*`) with the sheet's wording
  as the default, in case the live app's copy differs slightly from the
  sheet's shorthand.
- **No resource ids from the companion app are used anywhere** (unlike the
  `org.commcare.dalvik:id/...` ids used for the CommCare side of these
  flows, which are verified against the read-only `commcare-android`
  reference repo) - every companion-app assertion below matches by visible
  text only, since there's no manifest/layout XML to read ids out of.

None of this blocks writing the flows - Maestro's `assertVisible`/`tapOn` by
text and `launchApp: appId: ...` work the same whether the appId/text are
real or placeholders - it just means this suite needs a real-APK smoke pass
before it can be trusted, which someone with access to the Jenkins job needs
to do.

## Setup 1 ("Download ExternalApp") - no flow file, by design

The sheet's Setup 1 row is:

> 1. Download the Mobile API Testing App:
>    https://jenkins.dimagi.com/job/Mobile%20API%20Testing%20App/
> 2. Transfer the app to your testing device
> 3. Install the ExternalApp

This is **out-of-band provisioning** - fetching a build artifact from an
internal Jenkins job and getting the resulting APK onto a device - not
something a Maestro flow (which only drives what's already installed) can
express. There is intentionally no `setup_01_*.yaml` in this directory.

Here's how it plugs into the rest of this repo instead: BrowserStack App
Automate's Maestro product supports an `otherApps` capability - up to 3
companion APKs, given as `bs://<app_id>` upload urls, pre-installed
alongside the main `app` at session start (same session, so a flow can
`launchApp` into any of them). This repo wires that up in
`scripts/browserstack_client.py`'s `BrowserStackClient.trigger_build(...,
other_apps=[...])` and `scripts/run_suite.py --other-app <path-to-apk>`
(repeatable, uploads each companion APK and resolves it to its `bs://` url).
So once a human has done Setup 1 manually and has the Mobile API Testing App
APK sitting locally, the rest of this suite runs as:

```bash
python scripts/run_suite.py --tag externalapp_tests \
  --other-app /path/to/mobile-api-testing-app.apk \
  --hq-setup hq_setup/externalapp_tests/setup_03_deploy_fixture_app.json
```

(`--hq-setup` only runs `setup_03_deploy_fixture_app.json`'s HQ actions -
`setup_03_login_and_background.yaml` itself is picked up by `--tag
externalapp_tests` along with the rest of the on-device flows below.)

## Flows in this directory

| File | Sheet row | Notes |
|---|---|---|
| `setup_03_login_and_background.yaml` | Setup 3 | Login qa_user/123, background CommCare without logging out, via `flows/common/login_and_background.yaml` |
| `external_app_01_launch_and_verify_buttons.yaml` | External App 1 | Launch companion app, assert the 4 button labels |
| `external_app_02_start_commcare_submit_form.yaml` | External App 2 | Tap "Start CommCare", CommCare's Basic Form launches, answer + submit |
| `external_app_03_view_case_data.yaml` | External App 3 | Tap "View Case Data", open a case, verify properties/values shown |
| `external_app_04_launch_update_form.yaml` | External App 4 | Long-press a case, CommCare's update form launches, answer + submit |
| `external_app_05_view_fixture_data.yaml` | External App 5 | Tap "View Fixture Data", drill into location data |

Every flow above re-establishes CommCare's logged-in-and-backgrounded state itself via
`flows/common/login_and_background.yaml` (login + `pressKey: home`) rather than assuming
Setup 3 already ran earlier in the same session, per this repo's "each flow runnable in
isolation" convention (`docs/FRAMEWORK.md`). That reusable subflow is also what
`setup_03_login_and_background.yaml` itself calls - Setup 3 is both its own sheet row/test
case and a precondition every External App N flow re-establishes independently.

Setup 2 (plain CommCare install) and External App 6 (Acquire CommCare Key +
OS permission dialog + sync) are intentionally **not** in this directory -
Setup 2 needs no HQ dependency and no companion app (it's just the ordinary
CommCare install every other workflow in this repo already does), and
External App 6 is `Not automatable` per `coverage/coverage_matrix.csv`
(combines the cross-app flow with an OS permission dialog that behaves
inconsistently across device-farm Android versions).
