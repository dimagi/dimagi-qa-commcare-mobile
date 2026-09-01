# Update tab > Uninstall/Reinstall - documented gap, not automated

Master Mobile Plan (2026) > Update (coverage_matrix.csv XLSX Tab `Update`) >
Test Case ID `Uninstall/Reinstall` - "Fresh Play Store install + old app
version" - stays `Partial` in coverage_matrix.csv. This file exists only to
record *why* no flow or hq_setup JSON was written for it, per this repo's own
rule of not fabricating a flow that can't really work (see
`hq_setup/prompted_updates/*` and `docs/FRAMEWORK.md`'s "Known gaps" section
for the same standard applied elsewhere).

This test case genuinely needs two things this framework cannot provide:

1. **A specific historical APK, not the latest release.** The row calls for
   installing an "old app version" (pre-dating the current CommCare release
   under test) so that the subsequent update-to-latest can be exercised from
   a real prior binary. `scripts/download_apk.py` already supports fetching
   an exact historical release by tag:
   ```
   python scripts/download_apk.py --tag commcare_2.4X.Y --out apks/old_version.apk
   ```
   but *which* tag counts as "old" for this specific test needs to be pinned
   down against the Master Mobile Plan sheet / QA team (the sheet doesn't
   name an exact version number the way, e.g., the Recovery Measures tab's
   "Install old CommCare 2.45" row does - see
   `coverage/coverage_matrix.csv` row for `Updates (partial and failed) >
   Setup > Install old CommCare 2.45`, which at least has a concrete version
   to target). Once a version is confirmed, provisioning it is a solved
   problem via `download_apk.py --tag`; that part alone would NOT block
   automation.

2. **A real Google Play Store install**, which is the actual blocker. "Fresh
   Play Store install" means literally opening the Play Store app, searching
   for CommCare, and installing it via Play's own UI/infrastructure - not a
   sideloaded APK. Maestro has no first-class command for driving the real
   Play Store app's search/install flow, and BrowserStack's device farm does
   not guarantee a signed-in, purchase-capable Play Store session on its
   shared devices. This is the same category of gap already called out for
   the `Updates` tab's `In-App Update 1-10` rows in coverage_matrix.csv
   ("Depends on real Google Play internal-tester/app-sharing infrastructure
   that cloud device farms don't reliably support") and for this same
   `Update` tab's own `Recheck button 2` gap (see
   `hq_setup/update/recheck_button_02_NOTE.md`) and the Uninstall/Reinstall
   row is a strict superset of that problem (it needs a *fresh install*
   through Play, not just an in-app update prompt).

Net: even with the historical-APK half solved, the Play Store half cannot be
scripted with this framework's current tools (Maestro + BrowserStack App
Automate). Left `Partial` in coverage_matrix.csv, no flow file, no hq_setup
JSON - a flow that "sideloads an old APK and calls it done" would silently
skip the actual Play Store requirement the test case is about, which is worse
than leaving the gap explicit.
