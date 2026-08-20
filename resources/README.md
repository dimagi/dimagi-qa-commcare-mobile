# resources/

Real QA-app CCZs pulled from HQ's `qateam` domain (plus one from `sansar` and
one from `rate-limited`), used to verify Maestro flows' navigation
assumptions against the actual app content instead of guessing menu/form
names from the Master Mobile Plan's prose - see the many "NAVIGATION
CORRECTED" / "source-verified" comments across `flows/multimedia/*.yaml` for
what this already caught.

**A CCZ is just a zip** - `python -c "import zipfile; zipfile.ZipFile(f).extractall(...)"`
gets you `en/app_strings.txt` (real module/form display names) and `suite.xml`
(the actual menu structure), which is normally enough to confirm or fix a
flow's `tapOn` targets without ever touching a device.

## Committed vs. fetched on demand

Most of these are small enough to commit directly. Three are not - GitHub
hard-rejects any pushed file over 100MB, and `qateam - Multimedia` alone is
~167MB - so those three are `.gitignore`d and fetched at runtime instead via
`scripts/fetch_large_ccz.py` (uses the same HQ session
`scripts/hq_client.py` already authenticates for release-management
pre-steps - no separate credentials needed, `--web-user` reuses
`HQ_WEB_USER_EMAIL`/`HQ_WEB_USER_PASSWORD`):

```bash
python scripts/fetch_large_ccz.py --web-user
```

Skips any file already present (matched by filename prefix, so a later
version doesn't collide with a stale one still on disk); `--force`
re-downloads regardless.

| App | app_id | Status |
|---|---|---|
| \[Master\] Basic Tests | `cdfa6c85eb594b23b0c08729cd2beff1` | committed (155KB) |
| Multimedia | `4df9b7f7e66740a2bd9e02371af832b1` | **fetched on demand** (~167MB) |
| Right to Left Tests! | `1abba0dead4daede49abc56c04e56ae0` | **fetched on demand** (~67MB) |
| Mobile Updates - Test 1_2! | `424db1b7c64a94e3e4cdc03c6cc61038` | **fetched on demand** (~66MB) |
| Update Test Alternate! | `7e8e7e8857f7466495888a37952e7ad0` | committed (1.8MB) |
| Case Managements! | (see Prompted Updates' `varying_prompt_setup.json`) | committed (37KB) |
| External App Fixture Test | (sansar domain - see `hq_setup/externalapp_tests/`) | committed (8KB) |
| Rate limited submission app | (see `flows/common/submit_only_form.yaml`'s citation) | committed (4KB) |
| Performance Testing | - | committed (14.5MB, under the limit) |
| Advanced Settings / Conditional Enum in Case List / Date Widgets / Other / Session Expiration Test | - | committed (all <60KB) |

## How the download actually works (for anyone extending `hq_client.py`)

`GET /a/<domain>/apps/download/<build_id>/CommCare.ccz` does **not** stream
the file - confirmed live, it always kicks off an async celery job
(`build_application_zip`) and returns `{"download_id", "download_url", ...}`
even when a cached copy already exists. `download_url` is a status-poll
endpoint whose rendered HTML fragment contains the *actual* file link
(`/downloads/temp/<download_id>?get_file`) once the job's done -
`HQClient.download_ccz()` polls that fragment for a `?get_file` link rather
than assuming the first response is ever the file itself. Source:
`corehq/apps/app_manager/views/download.py:DownloadCCZ` +
`corehq/ex-submodules/soil/{__init__,views,util}.py` (fetched from
https://github.com/dimagi/commcare-hq since the local
`commcare-mobile/commcare-hq` checkout had no working tree - `git status`
there showed every file staged as deleted - when this was written).

`build_id` above is a specific **release's own id** (`HQClient.list_releases()`'s
`id` field), not the master app's id - `HQClient.download_latest_ccz(app_id, ...)`
does the list-then-download for you.
