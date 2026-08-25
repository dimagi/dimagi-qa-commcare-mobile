# resources/

## CCZs

UPDATE (2026-08-20), per direct user decision: this directory used to keep
committed (and, for 3 large ones, fetch-on-demand) copies of the real QA-app
CCZs, used to verify Maestro flows' navigation assumptions against actual
app content instead of guessing menu/form names from the Master Mobile
Plan's prose. Every app this repo tests is installed at run time via a
dynamic HQ app-code (`install_app_by_code.yaml`/`install_app_as_web_user.yaml`/
`install_app_as_mobile_user.yaml`, resolved fresh each run - see
`scripts/app_registry.py`/`scripts/hq_client.py:resolve_app_codes`), never by
sideloading a local CCZ, so none of them are actually needed on disk for
running the suite - removed all of them (11 committed + 3 fetched-on-demand
large ones) rather than keep reference copies nothing reads at run time.

If you need to inspect a real CCZ's `suite.xml`/`en/app_strings.txt` while
writing or fixing a flow's selectors (the original purpose of this
directory - see the many "NAVIGATION CORRECTED"/"source-verified" comments
across `flows/**/*.yaml` for what that already caught), download one
on demand instead of keeping it committed:

```python
import zipfile
zipfile.ZipFile("some.ccz").extractall("some_dir")
```

`scripts/hq_client.py`'s `HQClient.download_latest_ccz(app_id, dest_path)` /
`download_ccz(build_id, dest_path)` fetch a CCZ from HQ the same way
`scripts/fetch_large_ccz.py` (still present, now a general-purpose manual
utility rather than something the suite depends on) already does - see that
script and `HQClient`'s own docstrings for the download mechanics
(`GET /a/<domain>/apps/download/<build_id>/CommCare.ccz` kicks off an async
job rather than streaming the file directly).

UPDATE (2026-08-25): `OFFLINE_TEST_ONE.ccz`/`RU_TEST_TWO.ccz` below are a
DIFFERENT case from the cleanup above - not unread reference copies. See
"Other files" for why.

## Other files

- `Mobile API Testing App-release.apk` - the ExternalApp Tests companion
  app, pre-installed alongside the main app via `--other-app` (see
  `flows/externalapp_tests/README.md`) - not a CommCare build, so it isn't
  affected by the CCZ cleanup above.
- `commcare_2.45_release.apk` - the fixed "old" CommCare binary
  `scripts/run_appium_suite.py`'s mid-session update scenarios install
  first, before swapping to the new build under test.
- `app-commcare-release-Android16.apk` (or similar) - a custom CommCare
  build to test instead of a GitHub release, e.g. one not yet published
  there. Wired in via `scripts/run_suite.py`/`run_appium_suite.py --apk`
  and the CI workflow's `apk_source` dropdown.
- `OFFLINE_TEST_ONE.ccz` / `RU_TEST_TWO.ccz` - real CCZs pushed onto the
  test device's filesystem (via `AppiumBrowserStackClient.push_file`) by
  `scripts/run_appium_offline_ccz_suite.py`, so the Recovery Measures
  "Select CCZ" system file picker (Storage Access Framework) has a real
  file to find - the app itself is still installed at run time via a
  dynamic HQ app-code, same as every other flow; only the bytes the
  picker selects come from here. Genuinely read at runtime (unlike the
  removed CCZs above, which existed only for humans to inspect
  `suite.xml`/`app_strings.txt` while writing flow selectors) - matches
  `commcare_2.45_release.apk`'s own committed-and-referenced-directly
  convention. Re-fetch with `HQClient(domain=...).download_ccz(build_id,
  "resources/<KEY>.ccz")` (see `scripts/app_registry.py`'s
  `OFFLINE_TEST_ONE`/`RU_TEST_TWO` entries for domain/build_id) if a
  build ever needs to change.
