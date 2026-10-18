"""draft_pipeline.prose_gate passes only when check-prose.py exits 0.

Every check-prose finding line quotes the draft path and the flagged words, so reading the
output for "clean" let a draft named cleanup.md, or one that says "the cleanest proof", pass.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "skills/github-draft-review-html/scripts/draft_pipeline.py"


@pytest.fixture
def pipeline(monkeypatch):
    for var in ("EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "EXO_PROSE_POLICY", "EXO_INTERNAL_NAMES"):
        monkeypatch.delenv(var, raising=False)
    spec = importlib.util.spec_from_file_location("draft_pipeline", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def prose_gate(pipeline):
    return lambda path: pipeline.prose_gate(path, sys.executable)


def _draft(tmp_path, rel, text):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("rel,text", [
    ("cleanup.md", "This PR does the fix — nothing else.\n"),          # path says clean
    ("clean-room/fix-parser.md", "This PR does the fix — nothing else.\n"),
    ("fix.md", "This PR does the cleanup — nothing else.\n"),          # flagged line says clean
    ("proof.md", "This is the cleanest proof.\n"),                           # banned phrase says clean
])
def test_findings_block_whatever_the_output_says(prose_gate, tmp_path, rel, text):
    ok, note = prose_gate(_draft(tmp_path, rel, text))
    assert ok is False
    assert "finding(s)" in note


@pytest.mark.parametrize("name", ["-h", "--help", "--hel"])
def test_a_draft_named_like_a_help_flag_is_checked_not_parsed(prose_gate, tmp_path, monkeypatch, name):
    # A relative Path('./--help') is '--help': as a bare argument check-prose printed usage, exit 0.
    _draft(tmp_path, name, "This is the cleanest proof — nothing else.\n")
    monkeypatch.chdir(tmp_path)
    ok, note = prose_gate(Path(name))
    assert ok is False
    assert "2 finding(s)" in note


def test_unreadable_draft_blocks_even_under_a_clean_path(prose_gate, tmp_path):
    ok, note = prose_gate(tmp_path / "cleanup" / "missing.md")  # exit 3, message names the path
    assert ok is False and "cannot read" in note


def test_clean_draft_passes(prose_gate, tmp_path):
    ok, note = prose_gate(_draft(tmp_path, "fix.md", "Fixes the parser.\n"))
    assert ok is True and note.startswith("check-prose: clean")


def test_a_missing_checker_blocks_instead_of_skipping(pipeline, tmp_path, monkeypatch):
    # A partial install (no clean-writing or charts-in-posts next to this skill) used to pass unchecked.
    draft = _draft(tmp_path, "fix.md", "Fixes the parser.\n\n```mermaid\ngraph TD\n  A --> B\n```\n")
    monkeypatch.setattr(pipeline, "SKILL_DIR", tmp_path / "skills" / "github-draft-review-html")
    monkeypatch.setattr(pipeline, "MERMAID_CHECK", tmp_path / "skills" / "charts-in-posts" / "check-mermaid.py")
    ok, note = pipeline.prose_gate(draft, sys.executable)
    assert ok is False and "check-prose.py not found" in note
    ok, note = pipeline.mermaid_gate(draft, sys.executable)
    assert ok is False and "check-mermaid.py not found" in note
