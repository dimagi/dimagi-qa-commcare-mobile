// Unique per-run suffix for case names, so repeated runs on the shared test
// user don't pile up identically named cases. Same idea as the core suite's
// flows/case_filters/assets/generate_run_suffix.js (runScript files have to
// live under <workflow>/assets/ to get into the uploaded zip).
output.runSuffix = Date.now().toString();
