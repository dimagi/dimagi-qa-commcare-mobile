"""
Download a CommCare APK built by a dimagi/commcare-android GitHub Actions run
(e.g. a customised build someone made by following the team's build steps) so
the QA workflows can test it instead of a published GitHub release or a
committed resources/*.apk.

Why this exists: the commcare-android "commcare-android PR CI" and "CommCare
Android Release Build" workflows upload their APKs as run artifacts
(commcare-release-apk, ccc-staging-release-apk, and - for manually dispatched
PR CI runs only - commcare-qaAutomation-release-apk, kept just 3 days). An
artifact downloads as a ZIP wrapping the .apk, so this script unwraps it,
checks it really is a CommCare APK, and puts it at a predictable path.

Usage:
    python scripts/fetch_apk.py --run 37427195893
    python scripts/fetch_apk.py --run https://github.com/dimagi/commcare-android/actions/runs/37427195893 \
        --artifact commcare-qaAutomation-release-apk
    python scripts/fetch_apk.py --latest                         # newest dispatched dev build, any branch
    python scripts/fetch_apk.py --latest --branch commcare_2.65  # ... on one branch
    python scripts/fetch_apk.py --run 37427195893 --github-env   # CI: exports APK_OVERRIDE

Needs the GitHub CLI (`gh`) authenticated: locally via `gh auth login`, in CI via
the GH_TOKEN env var. commcare-android is a public repo, so any valid token with
read access works; if the workflow's built-in token turns out not to be allowed to
read another repository's artifacts, set a repo secret COMMCARE_ANDROID_READ_TOKEN
(the workflows prefer it when present).
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

DEFAULT_REPO = "dimagi/commcare-android"
DEFAULT_ARTIFACT = "commcare-release-apk"
EXPECTED_PACKAGE = "org.commcare.dalvik"

_RUN_URL_RE = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)(?:[/?#].*)?$")
_ARTIFACT_RE = re.compile(r"^[\w.\-]+$")
_BRANCH_RE = re.compile(r"^[\w./\-]+$")


def parse_run(value, default_repo=DEFAULT_REPO):
    """Accept a bare run id or an Actions run/job URL. Returns (repo, run_id).
    Strictly validated: the value reaches subprocess args and a file name."""
    value = (value or "").strip()
    if value.isdigit():
        return default_repo, value
    match = _RUN_URL_RE.match(value)
    if not match:
        raise SystemExit(
            f"'{value}' is not a run id or a https://github.com/<owner>/<repo>/actions/runs/<id> link."
        )
    repo, run_id = match.group(1), match.group(2)
    if repo.lower() != default_repo.lower():
        raise SystemExit(
            f"Only {default_repo} runs are supported (got {repo}) - refusing to download "
            f"build artifacts from another repository."
        )
    return default_repo, run_id


_READ_ONLY_RUN_SUBCOMMANDS = ("view", "download")
_API_WRITE_FLAGS = ("-X", "--method", "-f", "--raw-field", "-F", "--field", "--input")


def _assert_read_only(args):
    """This script only ever READS dimagi/commcare-android (list/view runs, download artifacts).
    It must never dispatch, re-run, cancel or otherwise trigger anything on the dev team's repo,
    so any other gh usage is rejected here - before it runs - rather than trusted to review."""
    if args and args[0] == "run" and len(args) > 1 and args[1] in _READ_ONLY_RUN_SUBCOMMANDS:
        return
    if args and args[0] == "api" and not any(a in _API_WRITE_FLAGS or a.startswith("--method=")
                                             for a in args):
        return  # gh api defaults to GET unless a write flag/field makes it a POST
    raise SystemExit(f"Refusing gh {' '.join(args[:3])} ...: fetch_apk.py is read-only on commcare-android.")


def _gh(*args, timeout=120, attempts=1):
    """Run gh with a hard timeout (a stalled artifact download otherwise hangs the job until
    the workflow's own timeout) and optional retries on timeout."""
    _assert_read_only(args)
    gh = shutil.which("gh")
    if not gh:
        raise SystemExit("The GitHub CLI (gh) is required but was not found on PATH.")
    for attempt in range(1, attempts + 1):
        try:
            proc = subprocess.run([gh, *args], capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            if attempt == attempts:
                raise SystemExit(f"gh {' '.join(args[:3])} ... timed out after {timeout}s "
                                 f"({attempts} attempt(s)).")
            print(f"gh {' '.join(args[:3])} ... timed out after {timeout}s; retrying "
                  f"({attempt}/{attempts})")
            continue
        if proc.returncode != 0:
            raise SystemExit(f"gh {' '.join(args[:3])} ... failed: {(proc.stderr or proc.stdout).strip()}")
        return proc.stdout


PR_CI_WORKFLOW_FILE = "commcare-android-pr-workflow.yml"
PR_CI_WORKFLOW = "commcare-android PR CI"


def _dispatched_runs(repo, scan, branch=None):
    """Successful, manually dispatched PR CI runs as {id: created_at}, newest build per id.

    Two independent REST listings are merged (with and without the server-side status filter)
    because `gh run list` / the filtered listing was seen returning an INCOMPLETE set that omitted
    the newest runs - both locally and in the Actions job - which silently selected a build from
    weeks earlier. Run ids only ever increase, so the merged set is ordered by id, not by the
    order the API happened to return."""
    base = f"repos/{repo}/actions/workflows/{PR_CI_WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page={scan}"
    if branch:
        base += f"&branch={branch}"
    jq = r'.workflow_runs[] | select(.conclusion == "success") | "\(.id) \(.head_branch) \(.created_at)"'
    found = {}
    for query in (base + "&status=success", base):
        for line in _gh("api", query, "--jq", jq).splitlines():
            run_id, branch, created = line.split(" ", 2)
            found[int(run_id)] = (branch, created)
    return found


def find_latest_run(repo, artifact, scan=50, branch=None):
    """Newest successful manually dispatched ("Run workflow") PR CI run, on ANY branch (dev build
    branch names change every time), that still has a non-expired `artifact`. These are the builds
    the team creates for testing before a release; pull_request runs are deliberately excluded."""
    if branch and not _BRANCH_RE.match(branch):
        raise SystemExit(f"Invalid branch name '{branch}'.")
    runs = _dispatched_runs(repo, scan, branch)
    if not runs:
        raise SystemExit(f"No successful dispatched '{PR_CI_WORKFLOW}' runs found in {repo}"
                         + (f" on branch '{branch}'." if branch else "."))
    newest = sorted(runs, reverse=True)
    print(f"Newest successful dispatched runs: "
          + ", ".join(f"{i} ({runs[i][0]}, {runs[i][1][:10]})" for i in newest[:3]))
    for run_id in newest:
        branch, created = runs[run_id]
        # Listed twice before a run is skipped: one transient/empty artifact listing must not
        # silently downgrade "latest" to an older build.
        for listing in (1, 2):
            names = _gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts", "--paginate",
                        "--jq", ".artifacts[] | select(.expired == false) | .name").split()
            if artifact in names:
                break
        else:
            print(f"Skipping run {run_id} on '{branch}' ({created}): "
                  f"no live '{artifact}' artifact (has: {', '.join(names) or 'none'})")
            continue
        print(f"Latest dispatched build with a live '{artifact}': run {run_id} on '{branch}' ({created})")
        return str(run_id)
    raise SystemExit(
        f"No successful dispatched '{PR_CI_WORKFLOW}' run in the last {len(runs)} still has a live "
        f"'{artifact}' artifact. Dispatch the commcare-android workflow again, or pass --run."
    )


def describe_run(repo, run_id):
    info = json.loads(_gh("run", "view", run_id, "-R", repo, "--json",
                          "status,conclusion,headBranch,headSha,displayTitle,createdAt,event"))
    print(f"commcare-android run {run_id}: '{info['displayTitle']}' on {info['headBranch']} "
          f"@ {info['headSha'][:10]} ({info['event']}, {info['status']}/{info['conclusion'] or '-'}, "
          f"started {info['createdAt']})")
    return info


def find_artifact(repo, run_id, name):
    """Return the artifact record, or exit with a message that says what IS available."""
    out = _gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts", "--paginate",
              "--jq", ".artifacts[] | {name, expired, expires_at, size_in_bytes}")
    artifacts = [json.loads(line) for line in out.splitlines() if line.strip()]
    for a in artifacts:
        if a["name"] == name:
            if a["expired"]:
                raise SystemExit(
                    f"Artifact '{name}' of run {run_id} has expired ({a['expires_at']}). "
                    f"commcare-qaAutomation builds are kept only 3 days - re-run the commcare-android "
                    f"workflow and pass the new run id."
                )
            return a
    available = ", ".join(a["name"] + (" (expired)" if a["expired"] else "") for a in artifacts) or "none"
    raise SystemExit(f"Run {run_id} has no artifact named '{name}'. Available: {available}.")


def _manifest_mentions_package(apk_path, package):
    """Cheap sanity check without an AXML parser: the compiled manifest's string pool
    stores the package name as UTF-16LE (or UTF-8 in newer builds)."""
    with zipfile.ZipFile(apk_path) as z:
        if "AndroidManifest.xml" not in z.namelist():
            return False
        manifest = z.read("AndroidManifest.xml")
    return package.encode("utf-16-le") in manifest or package.encode("utf-8") in manifest


def pick_apk(directory):
    apks = sorted(p for p in pathlib.Path(directory).rglob("*.apk") if "__MACOSX" not in p.parts)
    if len(apks) != 1:
        found = ", ".join(str(p.relative_to(directory)) for p in apks) or "none"
        raise SystemExit(f"Expected exactly one .apk in the artifact, found {len(apks)}: {found}.")
    return apks[0]


def fetch(run_value, artifact, out_dir="apks", repo=DEFAULT_REPO, branch=None):
    if not _ARTIFACT_RE.match(artifact or ""):
        raise SystemExit(f"Invalid artifact name '{artifact}'.")
    if run_value is None:
        run_id = find_latest_run(repo, artifact, branch=branch)
    else:
        repo, run_id = parse_run(run_value, repo)
    describe_run(repo, run_id)
    find_artifact(repo, run_id, artifact)

    with tempfile.TemporaryDirectory() as tmp:
        _gh("run", "download", run_id, "-R", repo, "-n", artifact, "-D", tmp, timeout=900, attempts=2)
        apk = pick_apk(tmp)
        if not zipfile.is_zipfile(apk) or not _manifest_mentions_package(apk, EXPECTED_PACKAGE):
            raise SystemExit(
                f"{apk.name} does not look like a {EXPECTED_PACKAGE} APK (no AndroidManifest or "
                f"package name not found) - wrong artifact? (androidTest and AAB artifacts are not usable.)"
            )
        dest_dir = pathlib.Path(out_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"commcare-android-run-{run_id}-{artifact}.apk"
        shutil.move(str(apk), dest)
    print(f"APK ready: {dest} ({dest.stat().st_size / 1_048_576:.1f} MB)")
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", help="commcare-android Actions run id or run URL.")
    group.add_argument("--latest", action="store_true",
                       help="Use the newest successful dispatched PR CI run (any branch) that still has "
                            "the artifact.")
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT,
                        help=f"Artifact name to download (default {DEFAULT_ARTIFACT}).")
    parser.add_argument("--branch", default="",
                        help="With --latest: only consider runs on this commcare-android branch "
                             "(blank = any branch).")
    parser.add_argument("--out-dir", default="apks")
    parser.add_argument("--resolve-only", action="store_true",
                        help="Only print which run would be used; download nothing.")
    parser.add_argument("--github-env", action="store_true",
                        help="Also append APK_OVERRIDE=<absolute path> to $GITHUB_ENV for later workflow steps.")
    args = parser.parse_args()

    if args.resolve_only:
        if not _ARTIFACT_RE.match(args.artifact or ""):
            sys.exit(f"Invalid artifact name '{args.artifact}'.")
        run_id = find_latest_run(DEFAULT_REPO, args.artifact, branch=args.branch or None) if args.latest else parse_run(args.run)[1]
        describe_run(DEFAULT_REPO, run_id)
        return
    dest = fetch(None if args.latest else args.run, args.artifact, args.out_dir, branch=args.branch or None)
    if args.github_env:
        env_file = os.environ.get("GITHUB_ENV")
        if not env_file:
            sys.exit("--github-env was given but GITHUB_ENV is not set (not running in GitHub Actions).")
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f"APK_OVERRIDE={dest.resolve().as_posix()}\n")
        print("Exported APK_OVERRIDE for the following steps.")


if __name__ == "__main__":
    main()
