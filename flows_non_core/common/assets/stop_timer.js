// Non-core timing helper: elapsed ms since start_timer.js, into
// output.elapsedMs for the caller's assertTrue budget check. (console.log
// output isn't kept in BrowserStack's Maestro command log, so the measured
// value is reported by scripts/extract_flow_timings.py from the step
// timestamps instead.)
output.elapsedMs = Date.now() - output.timerStart;
