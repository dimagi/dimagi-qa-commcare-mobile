// Non-core timing helper (paired with stop_timer.js). runScript runs on the
// Maestro host, so this is wall-clock time between two flow steps as Maestro
// sees them - includes Maestro's own polling granularity (well under a
// second), which is fine for load-time budgets measured in seconds.
output.timerStart = Date.now();
