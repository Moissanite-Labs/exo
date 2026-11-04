"""Merge-aware gate preflight (scripts/gate_diff_pr.py)."""
import json
import os
import sys
from pathlib import Path

from tests.test_gate_diff import DELETE_LINE, ROOT, _tree

PR_SCRIPT = ROOT / "scripts" / "gate_diff_pr.py"
FULL_TOOL_OUTPUT_BLOCK = (
    '    elif (tool == "tool_output" and set(args) <= {"reference", "offset", "limit"}\n'
    '          and isinstance(args.get("reference"), str)\n'
    '          and type(args.get("offset", 0)) is int and args.get("offset", 0) >= 0\n'
    '          and type(args.get("limit", 1)) is int and 1 <= args.get("limit", 1) <= 24000):\n'
    '        c = _make("read", tool=tool, summary="expand retained tool output")\n'
)
SYNTHETIC_BRIDGE_BLOCK = (
    '    elif tool == "exo_mb_test_tool" and not args:\n'
    '        c = _make("store_write", tool=tool, summary="bridge-only synthetic")\n'
)
CLASSIFY_ELSE = "    else:\n        old_tokens = [p for p in re.split(r\"[_\\-.:/]+\", tool.lower()) if p]"


def _gate_diff_pr(
    pr_base: Path,
    main: Path,
    head: Path,
    tmp_path: Path,
    merge_base: Path | None = None,
    *args: str,
    timeout: int = 120,
) -> tuple[object, dict]:
    out = tmp_path / "pr.json"
    out.unlink(missing_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    mb = merge_base or pr_base
    base_args = [
        "--git-dir", str(ROOT),
        "--pr-base-ref", "a" * 40,
        "--head-ref", "b" * 40,
        "--merge-base-ref", "c" * 40,
        "--main-ref", "d" * 40,
        "--merge-base", str(mb),
    ]
    proc = __import__("subprocess").run(
        [sys.executable, str(PR_SCRIPT), "--pr-base", str(pr_base), "--main", str(main), "--head", str(head),
         *base_args, "--json", str(out), *args],
        capture_output=True, text=True, timeout=timeout, cwd=str(ROOT), env=env,
    )
    return proc, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else {})


def test_a_lowered_pair_main_changed_since_mb_untouched_by_bridge_is_allowed(tmp_path):
    patch = (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible"))
    pr_base = _tree(tmp_path, "pr_base")
    mb = _tree(tmp_path, "mb")
    main = _tree(tmp_path, "main", patch)
    head = _tree(tmp_path, "head", patch)
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, mb, "--only-corpus", "--no-variants",
                              "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ma = rep["merge_aware"]["counts"]
    assert ma["lowered_vs_main"] == 0
    assert ma["lowered_vs_base"] == 1
    assert ma["allowed_because_main"] == 1
    assert ma["forbidden_vs_base"] == 0
    assert ma["conflict_bridge_stricter"] == 0


def test_a_lowered_pair_not_matching_main_fails(tmp_path):
    pr_base = _tree(tmp_path, "pr_base")
    mb = _tree(tmp_path, "mb")
    main = _tree(tmp_path, "main")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    corpus = tmp_path / "c2.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, mb, "--only-corpus", "--no-variants",
                              "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["merge_aware"]["counts"]["forbidden_vs_base"] >= 1
    assert rep["merge_aware"]["counts"]["allowed_because_main"] == 0


def test_dropping_a_non_tool_output_bridge_rule_is_forbidden(tmp_path):
    pr_base = _tree(tmp_path, "pr_base", (CLASSIFY_ELSE, SYNTHETIC_BRIDGE_BLOCK + CLASSIFY_ELSE))
    mb = _tree(tmp_path, "mb")
    main = _tree(tmp_path, "main")
    head = _tree(tmp_path, "head")
    action = json.dumps({"tool": "exo_mb_test_tool", "args": {}})
    corpus = tmp_path / "tool.json"
    corpus.write_text("[" + action + "]\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, mb, "--only-corpus", "--no-variants",
                              "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    ma = rep["merge_aware"]["counts"]
    assert ma["forbidden_vs_base"] >= 1
    assert ma["allowed_because_main"] == 0


def test_dropping_the_tool_output_bridge_rule_is_forbidden_without_a_special_detector(tmp_path):
    read_line = '_make("read", tool=tool, summary="expand retained tool output")'
    write_line = '_make("store_write", tool=tool, summary="expand retained tool output")'
    pr_base = _tree(tmp_path, "pr_base", (read_line, write_line))
    drop = (FULL_TOOL_OUTPUT_BLOCK, "")
    mb = _tree(tmp_path, "mb", drop)
    main = _tree(tmp_path, "main", drop)
    head = _tree(tmp_path, "head", drop)
    action = json.dumps({"tool": "tool_output", "args": {"reference": "call:1", "offset": 0, "limit": 1}})
    corpus = tmp_path / "tool.json"
    corpus.write_text("[" + action + "]\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, mb, "--only-corpus", "--no-variants",
                              "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["merge_aware"]["counts"]["forbidden_vs_base"] >= 1
    assert "bridge_tool_output" not in json.dumps(rep)


def test_a_conflict_bridge_stricter_pair_is_forbidden(tmp_path):
    patch = (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible"))
    mb = _tree(tmp_path, "mb", patch)
    pr_base = _tree(tmp_path, "pr_base")
    main = _tree(tmp_path, "main", patch)
    head = _tree(tmp_path, "head", patch)
    corpus = tmp_path / "c4.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, mb, "--only-corpus", "--no-variants",
                              "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    ma = rep["merge_aware"]["counts"]
    assert ma["conflict_bridge_stricter"] >= 1
    assert ma["forbidden_vs_base"] >= 1


def test_a_pair_lowered_vs_main_fails(tmp_path):
    mb = _tree(tmp_path, "mb")
    pr_base = _tree(tmp_path, "pr_base")
    main = _tree(tmp_path, "main2")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    corpus = tmp_path / "c3.txt"
    corpus.write_text("rm notes.txt\n", encoding="utf-8")
    proc, rep = _gate_diff_pr(pr_base, main, head, tmp_path, mb, "--only-corpus", "--no-variants",
                              "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["merge_aware"]["counts"]["lowered_vs_main"] >= 1
