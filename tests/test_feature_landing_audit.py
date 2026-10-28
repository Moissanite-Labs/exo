"""A merge commit alone cannot prove that its source implementation reached main (#741)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_feature_landings.py"
spec = importlib.util.spec_from_file_location("feature_landing_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    (tmp_path / "base.txt").write_text("base")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base fixture")
    return tmp_path


def feature(repo):
    git(repo, "checkout", "-qb", "feature")
    (repo / "feature.py").write_text("feature fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feature fixture")
    return git(repo, "rev-parse", "HEAD")


def pull_request(source, merged, **kwargs):
    return {"number": 1, "state": "MERGED", "url": "https://example.invalid/pull/1", "baseRefName": "integration",
            "headRefOid": source, "mergeCommit": {"oid": merged},
            "files": {"pageInfo": {"hasNextPage": False}, "nodes": [{"path": "feature.py"}]}, **kwargs}


def test_ours_merge_is_ancestor_but_missing_source_file_is_still_reported(repo):
    source = feature(repo)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-s", "ours", "feature", "-m", "intentionally omit source fixture")
    merged = git(repo, "rev-parse", "HEAD")
    row = module.audit(repo, "main", [pull_request(source, merged)])["pull_requests"][0]
    assert row["merge_in_base"] is True
    assert row["missing_source_paths"] == ["feature.py"]
    assert row["status"] == "content_review_required"


def test_merge_into_feature_branch_is_not_main_landing(repo):
    source = feature(repo)
    row = module.audit(repo, "main", [pull_request(source, source)])["pull_requests"][0]
    assert row["merge_in_base"] is False and row["missing_source_paths"] == ["feature.py"]


def test_content_can_match_without_source_merge_ancestry(repo):
    source = feature(repo)
    git(repo, "checkout", "-q", "main")
    (repo / "main-only.txt").write_text("main fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "independent main fixture")
    git(repo, "cherry-pick", source)
    row = module.audit(repo, "main", [pull_request(source, source)])["pull_requests"][0]
    assert row["merge_in_base"] is False
    assert row["exact_source_paths"] == ["feature.py"] and row["status"] == "listed_paths_match"


def test_changed_source_is_reviewable_not_automatically_lost(repo):
    source = feature(repo)
    git(repo, "checkout", "-q", "main")
    (repo / "feature.py").write_text("reviewed replacement fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "replacement fixture")
    row = module.audit(repo, "main", [pull_request(source, source)])["pull_requests"][0]
    assert row["changed_source_paths"] == ["feature.py"] and not row["missing_source_paths"]


def test_unavailable_objects_and_paginated_files_never_claim_complete(repo):
    source = feature(repo)
    assert not module.audit(repo, "main", [pull_request("a" * 40, source)])["complete"]
    pr = pull_request(source, source)
    pr["files"]["pageInfo"]["hasNextPage"] = True
    assert not module.audit(repo, "main", [pr])["complete"]


def test_missing_original_pr_is_rejected():
    with pytest.raises((KeyError, ValueError)):
        module.source({"data": {"repository": {}}})


def test_file_mode_difference_is_not_reported_as_matching_content(repo):
    source = feature(repo)
    git(repo, "checkout", "-q", "main")
    file = repo / "feature.py"
    file.write_text("feature fixture")
    file.chmod(0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "mode differs fixture")
    row = module.audit(repo, "main", [pull_request(source, source)])["pull_requests"][0]
    assert row["changed_source_paths"] == ["feature.py"]
    assert not row["exact_source_paths"]


def test_shallow_history_is_incomplete_even_when_source_objects_exist(repo):
    source = feature(repo)
    (repo / ".git" / "shallow").write_text(source + "\n")
    report = module.audit(repo, "main", [pull_request(source, source)])
    assert not report["ancestry_complete"] and not report["complete"]
