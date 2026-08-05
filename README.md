# dimagi-qa-commcare-mobile

Maestro + BrowserStack automation framework for CommCare Mobile (Android) test
cases that are currently manual (`No`) or only partially scripted (`Partial
Coverage`) per the *Connect Test Case and Workflow Inventory* > **CommCare
Mobile** tab, cross-referenced against the *[Master] Mobile Plan (2026)*
workbook's per-workflow tabs.

This repo is intentionally separate from
[`commcare-android`](https://github.com/dimagi/commcare-android) - it never
builds or modifies that app, it only consumes its public release APKs.

See [docs/FRAMEWORK.md](docs/FRAMEWORK.md) for the full design writeup and
[coverage/coverage_matrix.csv](coverage/coverage_matrix.csv) for the complete
218-row breakdown of every test case's automatability.

## Layout

```
flows/                  Maestro flow YAML, one subdirectory per workflow
  common/               Reusable subflows (login, logout, PIN helpers, ...)
hq_setup/               Declarative JSON pre-steps some flows need (see below)
scripts/
  download_apk.py       Pulls the release APK from dimagi/commcare-android's GitHub releases
  hq_client.py          CommCareHQ session client for build-release/settings actions
  browserstack_client.py  BrowserStack App Automate Maestro API wrapper
  run_suite.py          Orchestrates all of the above
coverage/coverage_matrix.csv   Every test case's automatability classification
.github/workflows/maestro-browserstack.yml   CI entry point
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials, never commit this file
```

You'll need:
- A **BrowserStack** account with App Automate access (`BROWSERSTACK_USERNAME` / `BROWSERSTACK_ACCESS_KEY`).
- **CommCareHQ** credentials with edit-apps access on the `qateam` domain, for flows whose sheet-row is tagged `Partial` because they need an HQ-side action (mark a build Released, toggle update settings). Either `HQ_API_USERNAME`/`HQ_API_PASSWORD`, or the `HQ_SESSION_COOKIE` escape hatch - see the caveat in `scripts/hq_client.py`.
- CommCare mobile-worker test credentials (`CC_TEST_USERNAME`/`CC_TEST_PASSWORD`, matching the sheet's `test1/123`).

## Running locally

```bash
# Everything tagged "mobile_pins" (the fully on-device, zero-HQ-dependency suite)
python scripts/run_suite.py --tag mobile_pins --devices "Samsung Galaxy S20-10.0"

# A flow that needs an HQ pre-step first
python scripts/run_suite.py --tag prompted_updates \
  --hq-setup hq_setup/prompted_updates/varying_prompt_setup.json

# One specific flow file
python scripts/run_suite.py --flow flows/install/install_04_see_apps_menu_item_visible.yaml
```

`run_suite.py` downloads the latest `commcare-android` release APK
automatically if `--apk` isn't given (see the naming-drift caveat in
`scripts/download_apk.py` - asset names aren't consistent release to release).

## What's actually implemented vs. documented-only

Building and verifying Maestro flows for all ~218 test-case rows in one pass
wasn't practical, so this first cut prioritizes **breadth of the framework**
(scripts, CI, HQ integration, tagging conventions) over **depth of flow
coverage**. Implemented today:

- **Mobile Pins**: the full on-device suite (Pin 1-14 + common PIN subflows) -
  this workflow was `No` coverage and is 100% automatable, so it's the
  flagship example.
- **Install**: menu/field-visibility checks (4-6) plus the two credentialed
  install flows (7-8).
- **Prompted Updates**: the four "Varying Prompt" frequency tests, plus the
  HQ pre-step JSON that seeds the custom properties they depend on.
- **Recovery Measures**: one representative flow (Reinstall and Update App)
  demonstrating the HQ-pre-step wiring pattern - flagged with an honest
  caveat that its on-screen message text couldn't be verified against app
  source (Recovery Measures message text is configured per-app on HQ, not
  bundled in the APK).

Every other automatable/partial row in the coverage matrix is documented with
enough detail (dependency, exact reason) to write its flow following the same
patterns - see [docs/FRAMEWORK.md](docs/FRAMEWORK.md) for the conventions.

## Adding a new flow

1. Find the row in `coverage/coverage_matrix.csv`.
2. If it's `Partial` because of an HQ action, add/extend a JSON file under
   `hq_setup/<workflow>/` describing the `hq_client.py` actions needed.
3. Write the flow under `flows/<workflow>/`, reusing `flows/common/` subflows
   for login/logout/etc. Tag it (`tags: [<workflow>, automatable|partial]`).
4. Update the `Flow file` column in the coverage matrix.
5. Verify any on-screen text/id selectors against `commcare-android` source
   (read-only) before trusting them - see docs/FRAMEWORK.md's selector notes.
