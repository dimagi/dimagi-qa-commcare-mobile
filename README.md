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
  report_generator.py   Builds the HTML run report (KPIs, donut, trend) - see below
  slack_notify.py       Posts the run summary + chart to Slack - see below
  run_suite.py          Orchestrates all of the above
coverage/coverage_matrix.csv   Every test case's automatability classification
reports/                Generated HTML reports (gitignored) + history.json (tracked)
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

## HTML report + trend

Every `run_suite.py` invocation ends by writing `reports/<build_id>/index.html`
(and refreshing `reports/latest.html`) via `scripts/report_generator.py`.
Maestro itself has no built-in report for BrowserStack (cloud) runs - this
turns BrowserStack's build/session JSON into KPI cards (Total, Passed, Failed,
Skipped, Rerun), a pass/fail/skip/rerun donut, and a trend line across past
runs. No chart library: the donut is a stroke-dasharray trick on stacked
`<circle>`s and the trend is a hand-built `<polyline>` + `<circle>` markers,
both self-contained SVG in the page.

The test table defaults to the **All** filter with rows ordered Failed →
Rerun → Passed → Skipped, so whatever needs attention is at the top without
clicking anything. The status chips (All/Failed/Rerun/Passed/Skipped) are
exclusive - click one to isolate it, plus a free-text search box. Failed rows
show their failure inline: the specific step Maestro failed on (best-effort,
see the caveat below) and the failure screenshot, plus links to the full
video/screenshot/log artifacts.

The trend is drawn from `reports/history.json`, which every run appends one
entry to (capped at the last 30 runs). That file is the one thing under
`reports/` that's *not* gitignored, since it's what makes the trend mean
anything across runs. In CI, `reports/history.json` survives between workflow
runs via an `actions/cache` step (see `.github/workflows/maestro-browserstack.yml`)
rather than being committed back automatically.

**Retrying failed flows.** Pass `--retry-failed` to `run_suite.py` and, if
anything fails, it re-triggers a second BrowserStack build containing only
the failed flows; anything that passes on that second attempt is reported as
**Rerun** (flaky) instead of **Failed**
(`report_generator.merge_rerun`/`match_flow_files`). The failed-name-to-flow-
file mapping is a heuristic - BrowserStack has, in every response seen so
far, reported a testcase's `name` as exactly its `flows/<workflow>/<file>.yaml`
path, but that's not documented as guaranteed anywhere, so `match_flow_files`
falls back to matching by filename if that ever changes. Without
`--retry-failed`, "Rerun" stays at 0.

**Failure step detail is also best-effort.** BrowserStack doesn't publicly
document the `maestro_commands` JSON shape, so `report_generator.fetch_failed_step`
defensively looks for a list of step entries and picks out the last failed
one - if the shape doesn't match what it expects (or the request fails), the
report falls back to a plain link to the raw commands log instead of
guessing at a step description.

To preview the report format without running BrowserStack:
```bash
python scripts/report_generator.py --from-json path/to/saved_build_response.json --build-id test
```

## Slack notification

`scripts/slack_notify.py` posts a summary of the latest run to `SLACK_CHANNEL_ID`
right after `run_suite.py` finishes (wired in as its own CI step, `if: always()`,
so it fires whether the run passed or failed). It reads straight off
`reports/latest.html` / `reports/latest_results.json` / `reports/history.json`
rather than being called from inside `run_suite.py`, so it can be rerun or
skipped independently of the test run itself. One Slack message includes:

1. Workflow name + branch/ref, and what triggered it (`GITHUB_WORKFLOW`,
   `GITHUB_REF_NAME`, `GITHUB_EVENT_NAME`, `GITHUB_ACTOR` - all set
   automatically by Actions, no extra `env:` wiring needed).
2. Pass rate, Total, Passed, Failed, Skipped, Rerun, plus up to 10 failed
   test names (`_+N more_` beyond that).
3. The same donut + trend as the HTML report, as one PNG. Slack can't render
   inline SVG, so this is the one place in the repo that uses a real chart
   library (`matplotlib`, `report_generator.render_chart_png`) instead of
   hand-drawn markup - the HTML report itself stays dependency-free.
4. A **Download HTML report** link and a **View run artifact** link
   (the GitHub Actions run page, where the `maestro-report` artifact lives).

**Setup**: the bot token needs the `files:write` and `chat:write` scopes,
and the bot must already be a member of `SLACK_CHANNEL_ID` - Slack silently
won't post into a channel it hasn't been invited to, regardless of scopes.
Both `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` are read as repo secrets in CI
(see `.github/workflows/maestro-browserstack.yml`) or from `.env` locally.

**Why two messages show up per run.** The HTML report's "download" link has
to point at an actual Slack-hosted permalink to be clickable by the whole
channel, and Slack only makes an uploaded file's permalink resolve for
non-uploaders once it's shared into the channel - so `slack_notify.py`
uploads it as its own bare file-share post (no comment text) *before* the
main summary message, purely so that message's link has somewhere to point.
The chart PNG + all the text above is the single second message.

To preview without posting for real, run `scripts/report_generator.render_chart_png`
and `scripts/slack_notify.build_message` directly against a saved
`history.json`/`results.json` - `slack_notify.py`'s `main()` is the only
part that actually talks to Slack.

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
