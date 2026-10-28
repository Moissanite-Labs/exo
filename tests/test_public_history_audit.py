"""History preparation audits are read-only and never print matched content (#49)."""
import importlib.util
import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_public_history.py"
spec = importlib.util.spec_from_file_location("history_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)


def test_deleted_history_and_secondary_refs_are_checked_without_content_disclosure(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "docs").mkdir()
    brief = repo / "docs/BRIEF.md"
    brief.write_text("fixture-person@example.invalid /Users/fixture-seat private-client-fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "synthetic sensitive history")
    git(repo, "tag", "old-fixture")
    brief.unlink()
    git(repo, "add", "-u")
    git(repo, "commit", "-qm", "remove fixture")
    before = git(repo, "show-ref")
    patterns = tmp_path / "private-patterns"
    patterns.write_text("private-client-fixture\n")
    assert audit.main(["--repo", str(repo), "--literal-file", str(patterns)]) == 0
    output = capsys.readouterr().out
    for private in ["fixture-person", "/Users/fixture-seat", "private-client-fixture", str(patterns), str(repo)]:
        assert private not in output
    report = json.loads(output)
    row = next(x for x in report["targets"] if x["target"] == "docs/BRIEF.md")
    assert row["historical_blobs"] == row["scanned_blobs"] == 1
    assert row["pattern_matches"] == {"email": 1, "home_path": 1}
    assert row["literal_matches"] == 1 and row["removal_candidate"]
    assert report["read_only"] and report["complete"] and report["ref_count"] == 2
    assert git(repo, "show-ref") == before
    assert git(repo, "status", "--porcelain") == b""


def test_errors_do_not_echo_repository_or_pattern_paths(tmp_path, capsys):
    assert audit.main(["--repo", str(tmp_path / "private-repository")]) == 2
    output = capsys.readouterr().out
    assert "private-repository" not in output and str(tmp_path) not in output
    assert json.loads(output)["error"]


def test_oversized_blob_marks_the_report_incomplete(tmp_path, monkeypatch):
    def fake_git(repo, *args):
        if args[0] == "for-each-ref":
            return b"a\n"
        if args[0] == "rev-list":
            return b"a docs/BRIEF.md\n"
        if args[:2] == ("cat-file", "-t"):
            return b"blob\n"
        if args[:2] == ("cat-file", "-s"):
            return str(audit.MAX_BLOB_BYTES + 1).encode()
        raise AssertionError("oversized content must never be loaded")
    monkeypatch.setattr(audit, "git", fake_git)
    report = audit.audit(tmp_path)
    assert not report["complete"]
    assert all(x["oversized_blobs"] == 1 and x["scanned_blobs"] == 0 for x in report["targets"])
