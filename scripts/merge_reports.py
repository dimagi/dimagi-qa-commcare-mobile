"""
Merges multiple matrix-job artifacts' reports/latest_results.json files into
ONE combined report + history entry. Meant to run once, in a dedicated job
that `needs:` every matrix job and downloads each one's artifact first - see
maestro-browserstack.yml's `merge-reports` job.

Each matrix job runs its own slice of tags via run_suite.py, which already
produces one fully-aggregated reports/latest_results.json for that slice
(everything normalize_build/normalize_session could find, already enriched
with failed_step/screenshot_data_uri). This script just concatenates those
already-enriched lists across every slice and calls report_generator.
generate_report() ONCE on the combined set, with enrich=False since the
per-slice runs already did that network-fetching work - no need to repeat it.

Usage: python scripts/merge_reports.py <artifact_dir> [<artifact_dir> ...]
  (each dir is a downloaded matrix-job artifact root containing
  latest_results.json, e.g. artifacts/maestro-report-group-a/)
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(__file__))
import report_generator


def main():
    dirs = [pathlib.Path(d) for d in sys.argv[1:]]
    if not dirs:
        raise SystemExit("Usage: python scripts/merge_reports.py <artifact_dir> [<artifact_dir> ...]")

    all_results = []
    apk_version = None
    for d in dirs:
        results_path = d / "latest_results.json"
        if not results_path.exists():
            print(f"WARNING: {results_path} not found - skipping (that matrix job may have "
                  f"produced no results, e.g. its tag group had nothing to run).")
            continue
        data = json.loads(results_path.read_text(encoding="utf-8"))
        all_results.extend(report_generator.TestResult(**item) for item in data)
        print(f"Loaded {len(data)} result(s) from {results_path}")

        # Every matrix job runs with the same --release-tag input, so any one
        # artifact's apk_version.txt (written by run_suite.py) speaks for the
        # whole run - carried forward so slack_notify.py can read it from the
        # merged reports/ dir, same as history.json.
        if apk_version is None:
            version_path = d / "apk_version.txt"
            if version_path.exists():
                apk_version = version_path.read_text(encoding="utf-8").strip()

    if not all_results:
        raise SystemExit("No results found in any artifact directory - nothing to merge.")

    if apk_version:
        pathlib.Path("reports").mkdir(exist_ok=True)
        pathlib.Path("reports/apk_version.txt").write_text(apk_version, encoding="utf-8")
        print(f"CommCare APK version: {apk_version}")

    build_id = os.environ.get("GITHUB_RUN_ID", "merged")
    report_path = report_generator.generate_report(build_id, all_results, enrich=False)
    print(f"Merged {len(all_results)} test result(s) from {len(dirs)} artifact director"
          f"{'y' if len(dirs) == 1 else 'ies'} into {report_path}")


if __name__ == "__main__":
    main()
