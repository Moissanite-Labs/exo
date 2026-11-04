"""scripts/gate_preflight_relevant.py: path filter for the never-looser workflow."""

import subprocess

from scripts import gate_preflight_relevant
from scripts.gate_preflight_relevant import (
    GATE_RELEVANT_PATTERNS,
    changed_paths_from_name_status,
    diff_touches_gate_paths,
    git_changed_paths,
    path_matches_pattern,
)


def test_patterns_cover_workflow_list():
    assert ".github/workflows/gate-preflight.yml" in GATE_RELEVANT_PATTERNS
    assert "scripts/gate_preflight_relevant.py" in GATE_RELEVANT_PATTERNS
    assert "tests/test_gate_preflight_relevant.py" in GATE_RELEVANT_PATTERNS
    assert "pyproject.toml" in GATE_RELEVANT_PATTERNS
    assert "uv.lock" in GATE_RELEVANT_PATTERNS


def test_gate_py_and_plugin_paths_match():
    assert path_matches_pattern("src/exo/gate.py", "src/exo/gate.py")
    assert path_matches_pattern("plugins/exo-gate/foo.py", "plugins/exo-gate/**")
    assert not path_matches_pattern("plugins/exo-other/x.py", "plugins/exo-gate/**")


def test_test_globs_match():
    assert path_matches_pattern("tests/security/test_gate_foo.py", "tests/security/test_gate*.py")
    assert path_matches_pattern("tests/test_gate_diff_extra.py", "tests/test_gate_diff*.py")
    assert not path_matches_pattern("tests/test_other.py", "tests/test_gate_diff*.py")


def test_unrelated_diff_is_not_gate_relevant():
    fixture = ["README.md", "src/exo/other.py", "plugins/exo-command/x.py"]
    assert not diff_touches_gate_paths(fixture)


def test_dependency_and_workflow_edits_are_gate_relevant():
    assert diff_touches_gate_paths(["pyproject.toml"])
    assert diff_touches_gate_paths(["uv.lock"])
    assert diff_touches_gate_paths([".github/workflows/gate-preflight.yml"])


def test_mixed_diff_detects_any_gate_path():
    fixture = ["docs/CHANGELOG.md", "scripts/gate_diff.py"]
    assert diff_touches_gate_paths(fixture)


def test_pr_weakening_detector_still_gate_relevant():
    """Workflow runs the base copy of the detector; a head edit cannot skip via this file."""
    assert diff_touches_gate_paths(["scripts/gate_preflight_relevant.py"])


def test_base_detector_run_full_when_only_detector_path_changes(tmp_path, monkeypatch):
    """Even a PR whose only change is weakening the detector still gets run_full=true from base."""
    out = tmp_path / "github_output.txt"

    def fake_git_changed_paths(base_sha: str):
        assert base_sha == "deadbeef"
        return ["scripts/gate_preflight_relevant.py"], None

    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(gate_preflight_relevant, "git_changed_paths", fake_git_changed_paths)
    assert gate_preflight_relevant.main(["--base", "deadbeef"]) == 0
    assert out.read_text(encoding="utf-8").strip() == "run_full=true"


def test_rename_away_from_watched_path_is_gate_relevant():
    status = "R100\tsrc/exo/gate.py\tsrc/exo/gate2.py\n"
    paths = changed_paths_from_name_status(status)
    assert "src/exo/gate.py" in paths
    assert diff_touches_gate_paths(paths)


def test_rename_into_watched_path_is_gate_relevant():
    status = "R100\tsrc/exo/other.py\tsrc/exo/gate.py\n"
    paths = changed_paths_from_name_status(status)
    assert diff_touches_gate_paths(paths)


def test_delete_of_watched_path_is_gate_relevant():
    status = "D\tsrc/exo/gate.py\n"
    paths = changed_paths_from_name_status(status)
    assert diff_touches_gate_paths(paths)


def test_git_diff_rename_away_lists_old_path(tmp_path, monkeypatch):
    """End-to-end: git diff --name-only hides renames; our parser must not."""
    monkeypatch.chdir(tmp_path)
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    gate = tmp_path / "src" / "exo" / "gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text("# gate\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True, capture_output=True, text=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    gate.rename(tmp_path / "src" / "exo" / "gate2.py")
    subprocess.run(["git", "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "rename"], check=True, capture_output=True, text=True)
    paths, err = git_changed_paths(base_sha)
    assert err is None
    assert "src/exo/gate.py" in paths
    assert diff_touches_gate_paths(paths)
