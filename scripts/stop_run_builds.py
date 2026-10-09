"""
Safety net for a cancelled workflow job: stop every BrowserStack Maestro build this job
started that never reported finishing. Run from a step with `if: cancelled()`.

scripts/cancel_guard.py normally stops them from inside the running process on SIGINT/SIGTERM;
this covers the case where that process was SIGKILLed first (or force-cancelled), using the
registry the process appended to as it went (reports/browserstack_builds.jsonl). It only
touches builds recorded by THIS job, never other runs' builds on the shared BrowserStack account.

Usage: python scripts/stop_run_builds.py
"""
import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
import cancel_guard


def unfinished_builds(path=cancel_guard.REGISTRY_PATH):
    if not path.exists():
        return []
    state = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        state[rec["build"]] = rec["event"]
    return [b for b, event in state.items() if event != "done"]


def main():
    load_dotenv()
    builds = unfinished_builds()
    if not builds:
        print("No unfinished BrowserStack builds recorded for this job - nothing to stop.")
        return
    print(f"Stopping {len(builds)} unfinished BrowserStack build(s) recorded by this job ...")
    for build_id in builds:
        # Only record the build as done when BrowserStack confirmed the stop (or that it had already
        # finished) - a failed stop must stay visible in the registry, not be marked done.
        if cancel_guard.stop_build(build_id):
            cancel_guard.unregister_build(build_id)


if __name__ == "__main__":
    main()
