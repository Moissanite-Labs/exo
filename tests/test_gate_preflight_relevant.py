"""scripts/gate_preflight_relevant.py: path filter for the never-looser workflow."""

from scripts.gate_preflight_relevant import (
    GATE_RELEVANT_PATTERNS,
    diff_touches_gate_paths,
    path_matches_pattern,
)


def test_patterns_cover_workflow_list():
    assert ".github/workflows/gate-preflight.yml" in GATE_RELEVANT_PATTERNS
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
