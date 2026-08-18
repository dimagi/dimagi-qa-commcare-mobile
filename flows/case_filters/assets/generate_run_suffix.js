// Generates a real, run-test-runner-side unique value (not device-derived)
// so geoservices_01_register_case.yaml/geoservices_02_rate_case.yaml can
// name their geo_case sites uniquely per run - per direct user instruction
// (2026-08-19): a shared, never-pruned domain accumulating identically
// named "Geoservices Automation Site"/"Geoservices Rate Automation Site"
// cases run after run is confusing to eyeball in the case list. Same
// mechanism already established in
// flows/conditional_enum_in_case_list/assets/generate_run_suffix.js - see
// that file's own docstring for why this lives under assets/ (only
// flows/<category>/assets/ gets copied wholesale into the uploaded
// test-suite zip by scripts/run_suite.py's build_flows_zip).
output.runSuffix = Date.now().toString();
