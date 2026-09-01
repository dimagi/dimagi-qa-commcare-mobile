# Update tab > Recheck button 2 - network-toggle capability researched, gap documented

Master Mobile Plan (2026) > Update (coverage_matrix.csv XLSX Tab `Update`) >
Test Case ID `Recheck button 2` - "Reconnect, press Recheck, verify update" -
stays `Partial` in coverage_matrix.csv. Per the task, BrowserStack's Maestro
product was researched (web search + BrowserStack docs) for a scriptable
network-disable/enable capability before deciding whether to write a flow.
Findings, so this isn't left as a guess:

1. **BrowserStack's documented network-condition/airplane-mode toggle is
   Appium/Espresso/XCUITest-only, not Maestro.** The relevant docs page is
   literally titled "Simulate network conditions for **Appium** tests"
   (https://www.browserstack.com/docs/app-automate/appium/test-real-user-conditions/simulate-network-conditions)
   and its mid-session toggle is a REST call keyed off an **Appium W3C
   `sessionid`**:
   `PUT /app-automate/sessions/<sessionid>/update_network.json` with body
   `{"networkProfile": "no-network"}` (or `"airplane-mode"`, or back to a
   normal profile to reconnect). Maestro builds on BrowserStack are
   build/flow-based, not Appium-session-based, and there is no `sessionid` of
   the kind this endpoint expects to call it against. BrowserStack's own
   Maestro-specific docs (`.../app-automate/maestro/references`,
   `.../app-automate/maestro/overview`) never mention this capability, and
   the confirmed full parameter list for triggering a Maestro build
   (`POST /app-automate/maestro/v2/build`) is `app`, `testSuite`, `devices`,
   `shards`, `project`, `buildTag`, `customBuildName`, `projectNotifyURL`,
   `setEnvVariables`, `tags`, `config`, `execute` - no network-condition
   parameter anywhere in it.

2. **Maestro CLI itself does have native `toggleAirplaneMode` /
   `setAirplaneMode: enable|disable` commands** (Android-only; confirmed via
   Maestro's own docs at
   `docs.maestro.dev/reference/commands-available/toggleairplanemode`), which
   could in principle be dropped straight into a flow YAML as ordinary
   Maestro steps rather than a BrowserStack-side capability. However:
   - No BrowserStack documentation anywhere mentions this command in
     combination with their Maestro product or confirms it works against
     their hosted real-device cloud (as opposed to a locally-connected
     emulator, where Maestro's own docs examples live).
   - Toggling airplane mode via `adb`/UI automation on a real, unrooted
     Android device typically requires a system-level permission
     (`WRITE_SECURE_SETTINGS` or similar) that a shared device-farm image may
     not grant to the Maestro test runner - this is exactly the same
     "timing-critical, physical airplane-mode toggle" category that
     coverage_matrix.csv already marks `Not automatable` for the sibling rows
     `Interrupt update` and `Recheck button 1` in this same tab.
   - Without a BrowserStack-confirmed guarantee that `toggleAirplaneMode`
     actually flips connectivity on their real-device cloud (rather than
     silently no-op'ing, which Maestro's own docs explicitly say happens on
     platforms where the concept doesn't apply, e.g. iOS/web), writing a flow
     that calls it and then asserts "the update proceeded" would be
     fabricating a passing test on an unverified premise - exactly what this
     task said not to do.

**Conclusion**: no flow file was written for this row. A future spike could
resolve this cheaply - run a tiny scratch flow with `toggleAirplaneMode`
against a real BrowserStack Android device and check (via `deviceLogs`/
`networkLogs`, or a simple "no network" toast in-app) whether connectivity
actually dropped - but that spike hasn't been run in this environment, so the
row stays `Partial` with this note rather than a flow that assumes success.
If that spike succeeds, the flow would slot in as
`flows/update/recheck_button_02_reconnect_and_update.yaml`, reusing
`flows/common/login.yaml` + the "Recheck" button (`updates.check.start`
string, confirmed in `UpdateUIController.java`/`android_translatable_strings.txt`)
already surfaced by `flows/update/update_target_developer_options_flow.yaml`'s
UpdateActivity screen.
