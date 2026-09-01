// Generates a real, run-test-runner-side unique value (not device-derived)
// so minimize_duplicate_create_subcase_update_case.yaml can name its
// parent case uniquely per run - confirmed live (2026-08-19, build
// batch2-regressions-fix-v3): a literal, unsuffixed "MaestroBasicCase"
// accumulates across every past run of this flow in this shared domain,
// so searching/selecting by that name alone can resolve to an OLDER run's
// own case instead of this run's - exactly the same ambiguity this
// mechanism already fixes elsewhere. Same mechanism already established
// in flows/conditional_enum_in_case_list/assets/generate_run_suffix.js
// and flows/case_filters/assets/generate_run_suffix.js - see either for
// the full citation. Lives under assets/ (not directly in this directory)
// because scripts/run_suite.py's build_flows_zip only ever copies the
// SELECTED .yaml flow files plus each flows/<category>/assets/ directory
// wholesale - a sibling file placed directly next to the .yaml would
// silently never make it into the uploaded test-suite zip.
output.runSuffix = Date.now().toString();
