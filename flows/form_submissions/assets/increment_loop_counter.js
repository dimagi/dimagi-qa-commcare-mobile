// Increments a per-run loop counter each call so
// multiple_submissions_10x_background_sync.yaml's submitted form text can
// include which iteration number it is, making it possible to tell
// exactly how many loop iterations actually completed from the submitted
// data itself, rather than only from the overall test pass/fail result.
// Maestro's own `repeat` has no built-in iteration index, hence this.
//
// Lives under assets/ (not directly in this directory) - see
// flows/conditional_enum_in_case_list/assets/generate_run_suffix.js's own
// docstring for why: scripts/run_suite.py's build_flows_zip only ever
// copies the SELECTED .yaml flow files plus each flows/<category>/assets/
// directory wholesale, so a sibling file placed directly next to the
// .yaml would silently never make it into the uploaded test-suite zip.
//
// Explicitly stringified - Maestro's own template interpolation
// rendered the bare number as "1.0" instead of "1" (its JS bridge
// treats all numbers as doubles), which per direct user observation was
// also just extra characters to type on every iteration.
output.loopCount = String((Number(output.loopCount) || 0) + 1);
