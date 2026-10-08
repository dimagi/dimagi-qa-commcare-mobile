"""
Run one of the self-contained Appium/BrowserStack suite scripts and rerun it ONCE if it fails,
reporting a test that fails and then passes as "rerun" (flaky) - the same status the Maestro
side's `--retry-failed` produces - instead of "failed".

Why a wrapper: run_appium_suite.py and a few others already retry their own failed scenarios,
but the Right to Left, user-activation, MM1/MM2/MM3, Case Search & Claim 1/2 and Case Search
Checkbox suites ran each scenario exactly once (a single flaky UI-tree read, e.g. case_list_4's
"start_install never became tappable within 10s" while the button was on screen, failed the
whole step). Those scripts differ a lot inside, and several change HQ config around the
scenario, so the safe, uniform retry is to re-run the whole script: each invocation does its own
setup/revert and merges its results into reports/latest_results.json by test name.

Usage: python scripts/run_with_rerun.py scripts/run_appium_rtl_suite.py [args...]
Exit code: the final attempt's exit code (0 if the rerun passed).
"""
import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "reports" / "latest_results.json"


def _load():
    try:
        return {r["name"]: r for r in json.loads(RESULTS_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {}


def failed_in_last_attempt(before, after):
    """Names that failed in the attempt that just ran: failed now AND new/changed since `before`
    (the file also holds other steps' results, including their own failures, which must not be
    touched or reported as this step's rerun)."""
    return sorted(n for n, r in after.items() if r["status"] == "failed" and before.get(n) != r)


def mark_reruns(first_failed):
    """Reclassify tests that failed on the first attempt and passed on the second as "rerun"."""
    after = _load()
    changed = []
    for name in first_failed:
        if name in after and after[name]["status"] == "passed":
            after[name]["status"] = "rerun"
            changed.append(name)
    if changed:
        RESULTS_PATH.write_text(json.dumps(list(after.values()), indent=2), encoding="utf-8")
    return changed


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/run_with_rerun.py <script.py> [args...]")
    cmd = [sys.executable, *sys.argv[1:]]
    before = _load()
    rc = subprocess.call(cmd)
    if rc == 0:
        return 0
    first_failed = failed_in_last_attempt(before, _load())
    print(f"\n[run_with_rerun] attempt 1 failed (exit {rc}); failed tests: "
          f"{', '.join(first_failed) or '(none recorded)'} - rerunning once ...\n", flush=True)
    rc = subprocess.call(cmd)
    changed = mark_reruns(first_failed)
    if changed:
        print(f"[run_with_rerun] passed on the rerun (reported as rerun): {', '.join(changed)}", flush=True)
    print(f"[run_with_rerun] final result: exit {rc}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
