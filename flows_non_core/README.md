# Non-core CommCare Mobile flows

Maestro flows for the *[Master] Mobile Plan (2026)* tabs listed on the
Inventory workbook's **"CommCare Mobile - Non-Core"** tab (Developer Options,
Advanced Settings, Printing, Text to Speech, Graphing, Mapbox, Performance
Tests, Case List Optimization and Cache, ...). The core suite (the
"CommCare Mobile" tab) lives in `../flows/`.

The two trees never overlap:

- `scripts/run_suite.py` only looks at one tree per run, picked with
  `--flows-root` (`flows` by default, `flows_non_core` for this one), so a
  core `--tag`/"All" run can't pick these up and vice versa.
- They run from their own CI workflow,
  `.github/workflows/maestro-browserstack-non-core.yml`, with its own Slack
  post and report artifact.

Shared subflows (`login.yaml`, `install_app_by_code.yaml`, ...) are **not**
copied here. Non-core flows reference them as `../common/<file>.yaml`, the
same as core flows, and `run_suite.py` puts `flows/common/` at the zip's
`common/` either way. A helper only non-core flows need can go in
`flows_non_core/common/`; a filename that clashes with `flows/common/` is a
hard error.

Same conventions as the core suite (see `docs/FRAMEWORK.md`): one flow per
test case, `tags: [<workflow>, automatable|partial]`, every flow runnable on a
fresh install, and every non-obvious selector cites the commcare-android
source it came from.

## Running

```bash
python scripts/run_suite.py --flows-root flows_non_core --tag developer_options
```

```bash
python scripts/run_suite.py --flows-root flows_non_core --flow flows_non_core/advanced_settings/custom_properties_06_hide_issue_report.yaml
```
