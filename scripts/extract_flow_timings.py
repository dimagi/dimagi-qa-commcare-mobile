"""
Report the load/start-up times measured by flows_non_core/ timing flows
(Case List Optimization and Cache, Performance Tests).

Those flows bracket the step being timed with runScript start_timer.js /
stop_timer.js and follow it with an `assertTrue` labelled "TIMING <what>"
(the loud-failure budget check). The measured value itself can't be read back
from the run: BrowserStack's Maestro command log keeps neither runScript
console.log output nor evaluated ${...} labels (confirmed live 2026-10-02).
What it does keep is each TOP-LEVEL command's own `timestamp`/`duration`, so
the elapsed time is recomputed here as

    stop_timer.timestamp - (start_timer.timestamp + start_timer.duration)

and reported under the next "TIMING ..." assertTrue's label. Steps inside a
runFlow are not logged at all, which is why the timed steps sit at the top
level of each flow (see flows_non_core/common/open_case_list_menu.yaml).

Usage:
    python scripts/extract_flow_timings.py [reports/latest_results.json]

Writes reports/timings.json and prints a Markdown table (the non-core CI
workflow appends it to the job summary). Best-effort: a flow whose log can't
be fetched is listed with no value rather than failing the step.
"""
import json
import os
import pathlib
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))
import report_generator

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def timings_from_commands(commands):
    """[(label, elapsed_ms)] for every start_timer -> stop_timer pair."""
    found, start, pending = [], None, None
    for entry in commands:
        command = entry.get("command") or {}
        meta = entry.get("metadata") or {}
        script = (command.get("runScriptCommand") or {}).get("sourceDescription", "")
        if script.endswith("start_timer.js"):
            start = meta.get("timestamp", 0) + meta.get("duration", 0)
        elif script.endswith("stop_timer.js") and start is not None:
            pending = meta.get("timestamp", 0) - start
            start = None
        elif pending is not None and "assertConditionCommand" in command:
            label = (command["assertConditionCommand"].get("label") or meta.get("description") or "")
            found.append((label.removeprefix("TIMING ").strip() or "(unlabelled)", pending))
            pending = None
    if pending is not None:
        found.append(("(unlabelled)", pending))
    return found


def main():
    results_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "reports" / "latest_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    auth = report_generator._browserstack_auth()
    rows = []
    for r in results:
        url = r.get("maestro_commands_url")
        if not url or not r.get("workflow") in ("case_list_optimization", "performance_tests"):
            continue
        try:
            resp = requests.get(url, auth=auth, timeout=30)
            resp.raise_for_status()
            pairs = timings_from_commands(resp.json())
        except Exception as exc:  # noqa: BLE001 - best-effort reporting
            print(f"WARNING: couldn't read the command log for {r['name']}: {exc}", file=sys.stderr)
            pairs = []
        for label, ms in pairs:
            rows.append({"flow": r["name"], "status": r["status"], "measurement": label, "elapsed_ms": ms})
        if not pairs:
            rows.append({"flow": r["name"], "status": r["status"], "measurement": "", "elapsed_ms": None})

    out = results_path.parent / "timings.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    print("| Flow | Measurement | Time | Flow result |")
    print("|---|---|---|---|")
    for row in rows:
        t = f"{row['elapsed_ms'] / 1000:.1f} s" if row["elapsed_ms"] is not None else "-"
        print(f"| {row['flow']} | {row['measurement'] or '-'} | {t} | {row['status']} |")


if __name__ == "__main__":
    main()
