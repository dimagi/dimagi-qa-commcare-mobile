// Generates a real, run-test-runner-side unique value (not device-derived)
// so conditional_id_mapping_01_06_full_scenario.yaml can name its "Test
// One"/"Test Two" cases uniquely per run - see that flow's own UPDATE
// comment for why (a shared, never-pruned domain accumulated 5000+
// duplicate "Test One" cases, making a literal, non-unique name unable to
// reliably resolve to THIS run's own case).
//
// Lives under assets/ (not directly in this directory) because
// scripts/run_suite.py's build_flows_zip only ever copies the SELECTED
// .yaml flow files plus each flows/<category>/assets/ directory wholesale
// - a sibling file placed directly next to the .yaml would silently never
// make it into the uploaded test-suite zip (the exact same class of bug
// that function's own docstring already documents for addMedia assets).
output.runSuffix = Date.now().toString();
