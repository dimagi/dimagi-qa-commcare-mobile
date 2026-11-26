"""Test the latest dev build of commcare-android's master branch (QA Plan task B1).

Agreed with the dev team (Shubham, Mobile Automation QA Plan): dev APKs are built on
demand by commcare-android's "PR CI" workflow (commcare-android-pr-workflow.yml), which
QA dispatches on master.

This is the only script here that writes to commcare-android, and the only thing it
does there is dispatch that workflow on master. fetch_apk.py stays read-only and does
the download, checks and version sidecar, exactly as for "Internal latest APK".

In order:

1. Reads master's head commit.
2. If a dispatched master build of that commit already has the artifact, uses it.
3. If one is still running, waits for it rather than dispatching again: the workflow's
   concurrency group would cancel the running build in favour of ours.
4. Otherwise dispatches a master build (with commcare-core's master commit - a dispatched
   run only checks out commcare-core when given commcare_core_sha) and waits until the
   artifact is uploaded, which happens part-way through the run.
5. If the head build finished without the artifact (or dispatching is off), falls back to
   the newest older master build that still has it, and says so in the log and the job
   summary - a run on yesterday's code beats no run, as long as nobody mistakes it for today's.

Builds dispatched by bots (commcare-core's cross-repo CI builds master against a
commcare-core PR) are ignored: they aren't master as it stands.

Needs COMMCARE_ANDROID_TOKEN: a token that can read commcare-android's Actions artifacts
and run its workflows (the A1 interrupt ticket asks the dev team for one). The download
step uses gh with the same token.

Usage:
    python scripts/dev_build.py                                  # commcare-release-apk
    python scripts/dev_build.py --artifact commcare-qaAutomation-release-apk --github-env
    python scripts/dev_build.py --no-dispatch                    # only builds that exist
"""
import argparse
import os
import sys
import time

import requests

REPO = "dimagi/commcare-android"
REPO_API = f"https://api.github.com/repos/{REPO}"
CORE_API = "https://api.github.com/repos/dimagi/commcare-core"
WORKFLOW = "commcare-android-pr-workflow.yml"
BRANCH = "master"
DEFAULT_ARTIFACT = "commcare-release-apk"
RUNS_TO_SCAN = 50
HTTP_TIMEOUT = 60
POLL_SECONDS = 60
# A full PR CI build takes about 30 minutes; the job itself times out at 90.
DEFAULT_WAIT_MINUTES = 80


class DevBuildError(Exception):
    """No usable dev build could be found or made."""


class GitHub:
    def __init__(self, token, sleep=time.sleep):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.sleep = sleep

    def get(self, path, **params):
        return self.get_url(f"{REPO_API}/{path}", **params)

    def get_url(self, url, **params):
        response = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def post(self, path, payload):
        response = self.session.post(f"{REPO_API}/{path}", json=payload, timeout=HTTP_TIMEOUT)
        if response.status_code in (403, 404):
            raise DevBuildError(
                f"Not allowed to dispatch {WORKFLOW} (HTTP {response.status_code}). The token needs "
                "permission to run commcare-android's workflows (A1 interrupt ticket)."
            )
        response.raise_for_status()


def master_head(gh):
    return gh.get(f"commits/{BRANCH}")["sha"]


def master_builds(gh):
    """Dispatched master runs of the PR CI workflow by people, newest first.

    Listed with the branch filter and the event checked here: the event-filtered listing
    has been seen returning a stale set (see fetch_apk._dispatched_runs)."""
    runs = gh.get(f"actions/workflows/{WORKFLOW}/runs", branch=BRANCH, per_page=RUNS_TO_SCAN)["workflow_runs"]
    return sorted(
        (run for run in runs
         if run["event"] == "workflow_dispatch" and not run["actor"]["login"].endswith("[bot]")),
        key=lambda run: run["id"],
        reverse=True,
    )


def live_artifact(gh, run_id, name):
    artifacts = gh.get(f"actions/runs/{run_id}/artifacts", per_page=100)["artifacts"]
    return next((a for a in artifacts if a["name"] == name and not a["expired"]), None)


def wait_for_artifact(gh, run_id, name, minutes):
    """Wait until the run has uploaded the artifact or finished. Returns the artifact or None."""
    deadline = time.monotonic() + minutes * 60
    while True:
        artifact = live_artifact(gh, run_id, name)
        if artifact:
            return artifact
        run = gh.get(f"actions/runs/{run_id}")
        if run["status"] == "completed":
            return live_artifact(gh, run_id, name)
        if time.monotonic() > deadline:
            raise DevBuildError(f"Gave up after {minutes} minutes waiting for run {run_id} to upload {name}")
        print(f"  run {run_id} is {run['status']}; waiting for {name}...")
        gh.sleep(POLL_SECONDS)


def dispatch(gh):
    """Dispatch a master build against commcare-core's master and return its run id."""
    newest_before = max((run["id"] for run in master_builds(gh)), default=0)
    core_sha = gh.get_url(f"{CORE_API}/commits/{BRANCH}")["sha"]
    gh.post(f"actions/workflows/{WORKFLOW}/dispatches", {"ref": BRANCH, "inputs": {"commcare_core_sha": core_sha}})
    for _ in range(10):
        gh.sleep(10)
        new = [run for run in master_builds(gh) if run["id"] > newest_before]
        if new:
            return new[-1]["id"]
    raise DevBuildError("Dispatched a master build but it never appeared in the run list")


def replacement_build(gh, head, run_id):
    """A newer build of the same commit, if run_id was cancelled to make way for it."""
    if gh.get(f"actions/runs/{run_id}").get("conclusion") != "cancelled":
        return None
    newer = [run for run in master_builds(gh) if run["head_sha"] == head and run["id"] > run_id]
    if newer:
        print(f"Run {run_id} was cancelled for run {newer[-1]['id']}; waiting for that one")
        return newer[-1]["id"]
    return None


def rerun_failed_jobs(gh, run_id):
    """Re-run a failed build's failed jobs, and wait until GitHub shows it running again.

    Master's unit tests flake (26 Nov: three Connect view tests failed on d41d534f, which
    built cleanly that morning), and a failed Build job uploads no APK. With test builds
    kept only 3 days there is often no older build to fall back to, so one re-run is
    worth the extra half hour."""
    gh.post(f"actions/runs/{run_id}/rerun-failed-jobs", {})
    for _ in range(12):
        if gh.get(f"actions/runs/{run_id}")["status"] != "completed":
            return True
        gh.sleep(10)
    return False


def find_build(gh, artifact_name, can_dispatch=True, wait_minutes=DEFAULT_WAIT_MINUTES):
    """(run, current) for master's dev build; current is False on a fallback."""
    head = master_head(gh)
    builds = master_builds(gh)
    print(f"commcare-android {BRANCH} is at {head[:10]}")

    at_head = [run for run in builds if run["head_sha"] == head]
    for run in at_head:
        if live_artifact(gh, run["id"], artifact_name):
            print(f"Using run {run['id']}, already built from {head[:10]}")
            return run, True
    running = next((run for run in at_head if run["status"] != "completed"), None)
    if running:
        print(f"Run {running['id']} is already building {head[:10]}; waiting for it")
        run_id = running["id"]
    elif at_head and not (can_dispatch and any(run.get("conclusion") == "success" for run in at_head)):
        run_id = None
        print(f"The build of {head[:10]} (run {at_head[0]['id']}) finished without {artifact_name}; "
              "not dispatching again")
    elif can_dispatch:
        # Either nothing has built this commit yet, or a build succeeded but its artifact
        # is gone: GitHub drops expired artifacts from the list (kept 3 days), and a
        # successful build always uploads it. Re-trigger rather than rely on retention
        # (Shubham, CI-1069). A failed build is not retried here, so this cannot loop.
        run_id = dispatch(gh)
        print(f"Dispatched run {run_id} to build {head[:10]}")
    else:
        run_id = None
        print(f"No build of {head[:10]} yet, and dispatching is off")

    # Two runs can dispatch at once (the matrix legs, or the Connect suite); the
    # workflow's concurrency group then cancels the first build in favour of the
    # second. A waiter whose build was cancelled moves on to the replacement.
    rerun = set()
    for _ in range(3):
        if not run_id:
            break
        if wait_for_artifact(gh, run_id, artifact_name, wait_minutes):
            return gh.get(f"actions/runs/{run_id}"), True
        print(f"Run {run_id} finished without {artifact_name}")
        if gh.get(f"actions/runs/{run_id}").get("conclusion") == "failure" and run_id not in rerun:
            rerun.add(run_id)
            print(f"Run {run_id} failed; re-running its failed jobs once")
            if rerun_failed_jobs(gh, run_id):
                continue
        run_id = replacement_build(gh, head, run_id)

    for run in builds:
        if run["head_sha"] == head or run["status"] != "completed":
            continue
        if live_artifact(gh, run["id"], artifact_name):
            print(f"WARNING: falling back to run {run['id']}, built from {run['head_sha'][:10]} "
                  f"on {run['created_at'][:10]} - NOT master's current head {head[:10]}")
            return run, False
    raise DevBuildError(
        f"No dispatched {BRANCH} build in the last {RUNS_TO_SCAN} runs has a live {artifact_name} "
        "(qaAutomation artifacts are kept 3 days)"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT)
    parser.add_argument("--out-dir", default="apks")
    parser.add_argument("--no-dispatch", action="store_true", help="only use builds that already exist")
    parser.add_argument("--wait-minutes", type=int, default=DEFAULT_WAIT_MINUTES)
    parser.add_argument("--github-env", action="store_true",
                        help="Also append APK_OVERRIDE=<absolute path> to $GITHUB_ENV for later workflow steps.")
    args = parser.parse_args()

    token = os.getenv("COMMCARE_ANDROID_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        sys.exit("No token: set COMMCARE_ANDROID_TOKEN (needed to dispatch and to download artifacts)")
    os.environ.setdefault("GH_TOKEN", token)  # for fetch_apk's gh calls
    try:
        run, current = find_build(GitHub(token), args.artifact, not args.no_dispatch, args.wait_minutes)
    except (DevBuildError, requests.RequestException) as error:
        sys.exit(f"Dev build lookup failed: {error}")

    import fetch_apk  # imported late: it needs gh only once there is something to download

    dest = fetch_apk.fetch(str(run["id"]), args.artifact, args.out_dir)
    state = "current master" if current else "OLDER than master head"
    print(f"Dev build: {run['html_url']} ({run['head_sha'][:10]}, {state})")
    if args.github_env:
        env_file = os.environ.get("GITHUB_ENV")
        if not env_file:
            sys.exit("--github-env was given but GITHUB_ENV is not set (not running in GitHub Actions).")
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f"APK_OVERRIDE={dest.resolve().as_posix()}\n")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            note = "" if current else " **(fallback: not master's current head)**"
            f.write(f"Dev APK: [{args.artifact}]({run['html_url']}) built from "
                    f"`{run['head_sha'][:10]}` on {run['created_at'][:10]}{note}\n")


if __name__ == "__main__":
    main()
