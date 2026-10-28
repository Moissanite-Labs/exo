"""Negative controls for claiming hosted recovery from run metadata alone."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("actions_health", Path(__file__).parents[1] / "scripts/actions_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
SHA = "a" * 40


def inspect(*, conclusion="startup_failure", status="completed", jobs=(), expected=None, change=None):
    run = {"id": 7, "run_attempt": 2, "head_sha": SHA, "status": status, "conclusion": conclusion}
    calls = []

    def get(path):
        calls.append(path)
        if "jobs?" in path:
            assert "/attempts/2/" in path
            return {"total_count": len(jobs), "jobs": list(jobs)}
        if change and len(calls) > 1:
            return run | change
        return run

    return health.inspect_run("aska-digital/exo", 7, expected_sha=expected, get=get)


def test_real_startup_failure_shape_is_not_recovery():
    result = inspect()
    assert result["state"] == "blocked_before_jobs"
    assert not result["hosted_execution_verified"] and not result["ci_passed"]
    assert "not established" in result["cause"]


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", None])
def test_zero_jobs_never_proves_execution(conclusion):
    assert not inspect(conclusion=conclusion)["hosted_execution_verified"]


@pytest.mark.parametrize("job_conclusion", ["failure", "cancelled", "timed_out"])
def test_hosted_execution_and_passing_ci_are_different(job_conclusion):
    result = inspect(conclusion="failure", jobs=[{"id": 1, "status": "completed", "conclusion": job_conclusion}])
    assert result["hosted_execution_verified"] and not result["ci_passed"]


def test_successful_job_and_correct_head_proves_passing_ci():
    result = inspect(conclusion="success", jobs=[{"id": 1, "status": "completed", "conclusion": "success"}], expected=SHA)
    assert result["hosted_execution_verified"] and result["ci_passed"]


def test_wrong_head_does_not_prove_requested_recovery():
    result = inspect(conclusion="success", jobs=[{"id": 1, "status": "completed", "conclusion": "success"}], expected="b" * 40)
    assert not result["hosted_execution_verified"] and not result["ci_passed"]


@pytest.mark.parametrize("job", [{"id": 1, "status": "queued", "conclusion": None}, {"id": 1, "status": "completed"}])
def test_unfinished_jobs_do_not_prove_recovery(job):
    assert not inspect(conclusion="success", jobs=[job])["hosted_execution_verified"]


@pytest.mark.parametrize("change", [{"run_attempt": 3}, {"status": "in_progress"}, {"head_sha": "b" * 40}])
def test_rerun_or_transition_during_collection_fails(change):
    with pytest.raises(ValueError, match="changed during"):
        inspect(change=change)


def test_duplicate_job_rows_fail():
    with pytest.raises(ValueError, match="duplicate"):
        inspect(jobs=[{"id": 1}, {"id": 1}])


@pytest.mark.parametrize("repo,run", [("x/y/../../z", 7), ("x/y", 0), ("-x", 2)])
def test_invalid_request_rejected_before_transport(repo, run):
    with pytest.raises(ValueError):
        health.inspect_run(repo, run, get=lambda _: pytest.fail("transport called"))


def test_incomplete_paginated_inventory_is_not_success():
    run = {"id": 7, "run_attempt": 2, "head_sha": SHA, "status": "completed", "conclusion": "success"}
    def get(path):
        if "jobs?" not in path:
            return run
        return {"total_count": 3, "jobs": [{"id": 1}] if "page=1" in path else []}
    with pytest.raises(ValueError, match="incomplete"):
        health.inspect_run("x/y", 7, get=get)


def test_all_pages_required_for_hosted_result():
    run = {"id": 7, "run_attempt": 2, "head_sha": SHA, "status": "completed", "conclusion": "success"}
    calls = []
    def get(path):
        calls.append(path)
        if "jobs?" not in path:
            return run
        ids = range(100) if path.endswith("page=1") else [100]
        return {"total_count": 101, "jobs": [{"id": i, "status": "completed", "conclusion": "success"} for i in ids]}
    report = health.inspect_run("x/y", 7, get=get)
    assert report["job_count"] == 101 and report["ci_passed"]
    assert len(calls) == 4


def test_transport_uses_only_get_and_keeps_error_detail_private(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stderr="private account detail", stdout="")
    monkeypatch.setattr(health.subprocess, "run", run)
    with pytest.raises(ValueError, match=r"^GitHub GET failed \(exit 1\)$"):
        health.gh_get("repos/x/y/actions/runs/7")
    assert calls[0][0] == ["gh", "api", "--method", "GET", "repos/x/y/actions/runs/7"]
    assert calls[0][1]["timeout"] == 30
