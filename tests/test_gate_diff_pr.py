"""Merge-aware gate preflight (scripts/gate_diff_pr.py)."""
import json
import os
import sys
from pathlib import Path

import pytest

from tests.test_gate_diff import DELETE_LINE, ROOT, SCRIPT, _gate_diff, _run, _tree

PR_SCRIPT = ROOT / "scripts" / "gate_diff_pr.py"
FULL_TOOL_OUTPUT_BLOCK = (
    '    elif (tool == "tool_output" and set(args) <= {"reference", "offset", "limit"}\n'
    '          and isinstance(args.get("reference"), str)\n'
    '          and type(args.get("offset", 0)) is int and args.get("offset", 0) >= 0\n'
    '          and type(args.get("limit", 1)) is int and 1 <= args.get("limit", 1) <= 24000):\n'
    '        c = _make("read", tool=tool, summary="expand retained tool output")\n'
)
TOOL_OUTPUT_BLOCK = FULL_TOOL_OUTPUT_BLOCK
CLASSIFY_ELSE = "    else:\n        old_tokens = [p for p in re.split(r\"[_\\-.:/]+\", tool.lower()) if p]"


def _gate_diff_pr(pr_base: Path, main: Path, head: Path, tmp_path: Path, *args: str, timeout: int = 120
                  ) -> tuple[object, dict]:
    out = tmp_path / "pr.json"
    out.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    base_args = ["--git-dir", str(ROOT), "--pr-base-ref", "a" * 40, "--head-ref", "b" * 40]
    proc = __import__("subprocess").run(
        [sys.executable, str(PR_SCRIPT), "--pr-base", str(pr_base), "--main", str(main), "--head", str(head),
         *base_args, "--json", str(out), *args],
        capture_output=True, text=True, timeout=timeout, cwd=str(ROOT), env=env,
    )
    return proc, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else {})


def test_a_lowered_pair_matching_main_is_allowed(tmp_path):
    pr_base = _tree(tmp_path, "pr_base")
    patch = (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible"))
    main = _tree(tmp_path, "main", patch)
    head = _tree(tmp_path, "head", patch)
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                              "--pick", "default", "--jobs", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ma = rep["merge_aware"]["counts"]
    assert ma["lowered_vs_main"] == 0
    assert ma["lowered_vs_base"] == 1
    assert ma["allowed_because_main"] == 1
    assert ma["forbidden_vs_base"] == 0


def test_a_lowered_pair_not_matching_main_fails(tmp_path):
    pr_base = _tree(tmp_path, "pr_base")
    main = _tree(tmp_path, "main")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    corpus = tmp_path / "c2.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                              "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["merge_aware"]["counts"]["forbidden_vs_base"] >= 1
    assert rep["merge_aware"]["counts"]["allowed_because_main"] == 0


def test_removing_the_bridge_tool_output_rule_fails(tmp_path):
    pr_base = _tree(tmp_path, "pr_base")
    drop = (FULL_TOOL_OUTPUT_BLOCK, "")
    main = _tree(tmp_path, "main", drop)
    head = _tree(tmp_path, "head", drop)
    action = json.dumps({"tool": "tool_output", "args": {"reference": "call:1", "offset": 0, "limit": 1}})
    corpus = tmp_path / "tool.json"
    corpus.write_text("[" + action + "]\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                              "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["merge_aware"]["bridge_tool_output_rule_removed"]
    assert rep["merge_aware"]["counts"]["forbidden_vs_base"] >= 1


def test_a_pair_lowered_vs_main_fails(tmp_path):
    pr_base = _tree(tmp_path, "main")
    main = _tree(tmp_path, "main2")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    corpus = tmp_path / "c3.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                              "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["merge_aware"]["counts"]["lowered_vs_main"] >= 1
