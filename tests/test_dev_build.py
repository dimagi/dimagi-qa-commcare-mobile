"""Unit tests for scripts/dev_build.py: picking and dispatching commcare-android master builds.

Run: python -m pytest tests/
No network: GitHub is a fake that holds runs and artifacts in memory.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import dev_build  # noqa: E402
from dev_build import DevBuildError, find_build  # noqa: E402

ARTIFACT = "commcare-release-apk"
HEAD = "a" * 40
OLD = "b" * 40


def _run(run_id, sha=HEAD, status="completed", actor="kbo001", event="workflow_dispatch"):
    return {"id": run_id, "head_sha": sha, "status": status, "event": event, "actor": {"login": actor},
            "created_at": "2026-11-20T10:00:00Z", "html_url": f"https://github.com/run/{run_id}"}


class FakeGitHub:
    """Stands in for dev_build.GitHub: runs and artifacts by run id, dispatch appends a run."""

    def __init__(self, runs, artifacts=None, on_dispatch=None):
        self.runs = list(runs)
        self.artifacts = artifacts or {}
        self.on_dispatch = on_dispatch
        self.dispatched = 0
        self.sleeps = 0
        self.reruns = []

    def get(self, path, **_params):
        if path == "commits/master":
            return {"sha": HEAD}
        if path.endswith("/runs"):
            return {"workflow_runs": self.runs}
        if path.endswith("/artifacts"):
            run_id = int(path.split("/")[2])
            return {"artifacts": [{"name": name, "expired": False, "size_in_bytes": 1,
                                   "archive_download_url": "url"} for name in self.artifacts.get(run_id, [])]}
        run_id = int(path.split("/")[-1])
        return next(run for run in self.runs if run["id"] == run_id)

    def get_url(self, url, **_params):
        assert url.endswith("/commcare-core/commits/master")
        return {"sha": "c" * 40}

    def post(self, path, payload):
        if path.endswith("/rerun-failed-jobs"):
            self.reruns.append(int(path.split("/")[2]))
            self.on_rerun(self)
            return
        assert payload == {"ref": "master", "inputs": {"commcare_core_sha": "c" * 40}}
        self.dispatched += 1
        self.on_dispatch(self)

    def sleep(self, _seconds):
        self.sleeps += 1
        # Each wait lets an in-progress run finish uploading.
        for run in self.runs:
            if run["status"] != "completed" and run["id"] in self.pending:
                self.artifacts[run["id"]] = [ARTIFACT]
                run["status"] = "completed"

    pending = ()
    on_rerun = None


def test_uses_an_existing_build_of_master_head_without_dispatching():
    gh = FakeGitHub([_run(5), _run(4, sha=OLD)], {5: [ARTIFACT], 4: [ARTIFACT]})
    run, current = find_build(gh, ARTIFACT)
    assert (run["id"], current, gh.dispatched) == (5, True, 0)


def test_waits_for_a_running_build_instead_of_dispatching_a_second_one():
    gh = FakeGitHub([_run(7, status="in_progress")])
    gh.pending = {7}
    run, current = find_build(gh, ARTIFACT)
    assert (run["id"], current, gh.dispatched) == (7, True, 0)


def test_dispatches_when_master_head_has_no_build():
    def add_run(gh):
        gh.runs.insert(0, _run(9, status="queued"))
        gh.pending = {9}

    gh = FakeGitHub([_run(4, sha=OLD)], {4: [ARTIFACT]}, on_dispatch=add_run)
    run, current = find_build(gh, ARTIFACT)
    assert (run["id"], current, gh.dispatched) == (9, True, 1)


def test_a_failed_head_build_falls_back_to_an_older_one_without_redispatching():
    gh = FakeGitHub([_run(8), _run(4, sha=OLD)], {8: ["jacoco-report"], 4: [ARTIFACT]})
    run, current = find_build(gh, ARTIFACT)
    assert (run["id"], current, gh.dispatched) == (4, False, 0)


def test_no_dispatch_falls_back_to_an_older_build():
    gh = FakeGitHub([_run(4, sha=OLD)], {4: [ARTIFACT]})
    run, current = find_build(gh, ARTIFACT, can_dispatch=False)
    assert (run["id"], current, gh.dispatched) == (4, False, 0)


def test_bot_and_pull_request_runs_are_ignored():
    gh = FakeGitHub(
        [_run(6, actor="commcare-cross-repo-ci[bot]"), _run(5, event="pull_request")],
        {6: [ARTIFACT], 5: [ARTIFACT]},
    )
    with pytest.raises(DevBuildError, match="No dispatched master build"):
        find_build(gh, ARTIFACT, can_dispatch=False)


def test_a_build_cancelled_by_a_second_dispatch_hands_over_to_the_replacement():
    class Replaced(FakeGitHub):
        def sleep(self, _seconds):
            first = next(run for run in self.runs if run["id"] == 7)
            if first["status"] != "completed":
                first.update(status="completed", conclusion="cancelled")
                self.runs.insert(0, _run(8))
                self.artifacts[8] = [ARTIFACT]

    gh = Replaced([_run(7, status="in_progress")])
    run, current = find_build(gh, ARTIFACT)
    assert (run["id"], current, gh.dispatched) == (8, True, 0)


def test_a_failed_dispatched_build_is_rerun_once_before_falling_back():
    def add_run(gh):
        gh.runs.insert(0, _run(9, status="queued"))

    def back_to_running(gh):
        gh.runs[0].update(status="in_progress", conclusion=None)
        gh.pending = {9}

    class FailsFirst(FakeGitHub):
        def sleep(self, _seconds):
            run = next(r for r in self.runs if r["id"] == 9)
            if not self.reruns and run["status"] != "completed":
                run.update(status="completed", conclusion="failure")
                return
            super().sleep(_seconds)

    gh = FailsFirst([], on_dispatch=add_run)
    gh.on_rerun = back_to_running
    run, current = find_build(gh, ARTIFACT)
    assert (run["id"], current, gh.dispatched, gh.reruns) == (9, True, 1, [9])


def test_a_build_that_fails_again_after_its_rerun_is_not_rerun_twice():
    def add_run(gh):
        gh.runs.insert(0, _run(9, status="queued"))

    class AlwaysFails(FakeGitHub):
        def sleep(self, _seconds):
            for run in self.runs:
                if run["status"] != "completed":
                    run.update(status="completed", conclusion="failure")

    def back_to_running(gh):
        gh.runs[0].update(status="in_progress", conclusion=None)

    gh = AlwaysFails([], on_dispatch=add_run)
    gh.on_rerun = back_to_running
    with pytest.raises(DevBuildError):
        find_build(gh, ARTIFACT)
    assert gh.reruns == [9]


def test_gives_up_when_the_build_never_uploads(monkeypatch):
    clock = iter(range(0, 10**6, 3600))
    monkeypatch.setattr(dev_build.time, "monotonic", lambda: next(clock))
    gh = FakeGitHub([_run(7, status="in_progress")])
    with pytest.raises(DevBuildError, match="Gave up"):
        find_build(gh, ARTIFACT, wait_minutes=30)
