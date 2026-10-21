"""scripts/gate_diff.py: the never-looser-than-main differential for gate changes (#423)."""
import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import gate_diff

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gate_diff.py"
DELETE_LINE = '    "delete":            ("irreversible", "private", False),\n'


def _tree(tmp_path: Path, name: str, patch: tuple[str, str] | None = None) -> Path:
    tree = tmp_path / name
    shutil.copytree(ROOT / "src" / "exo", tree / "src" / "exo", ignore=shutil.ignore_patterns("__pycache__"))
    if patch:
        gate = tree / "src" / "exo" / "gate.py"
        text = gate.read_text(encoding="utf-8")
        assert text.count(patch[0]) == 1, patch[0]
        gate.write_text(text.replace(patch[0], patch[1]), encoding="utf-8")
    return tree


def _run(base: Path, head: Path, tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess, dict]:
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("ls\nrm notes.txt\ngit status\ncat ~/.ssh/id_rsa\n", encoding="utf-8")
    out = tmp_path / "out.json"
    proc = subprocess.run([sys.executable, str(SCRIPT), "--base", str(base), "--head", str(head), "--only-corpus",
                           "--corpus", str(corpus), "--pick", "default,deny_delete,matrix_all_ask", "--jobs", "1",
                           "--json", str(out), *extra], capture_output=True, text=True, timeout=120)
    return proc, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else {})


def test_a_loosened_category_exits_1_and_names_the_action(tmp_path):
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    proc, rep = _run(base, head, tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rm notes.txt" in proc.stdout and "delete ask -> delete allow" in proc.stdout
    lowered = {(x["action"]["args"]["command"], x["policy"]) for x in rep["lowered"]}
    assert lowered == {("rm notes.txt", "default")}      # deny_delete still denies, matrix_all_ask still asks
    assert rep["counts"]["lowered_pairs"] == 1 and rep["counts"]["actions"] == 4
    assert [(x["action"]["args"]["command"], x["policies"]) for x in rep["lowered_actions"]] == [
        ("rm notes.txt", ["default"])]
    assert rep["gate_files"]["head"].startswith(str(head))


def test_identical_trees_exit_0(tmp_path):
    tree = _tree(tmp_path, "base")
    proc, rep = _run(tree, _tree(tmp_path, "copy"), tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert rep["counts"]["lowered_pairs"] == 0 and rep["counts"]["pairs_same"] == 4 * 3
    assert "nothing lowered" in proc.stdout


def test_a_tree_that_cannot_run_exits_2(tmp_path):
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", ("\nimport bisect\n", "\nimport bisect\nraise ImportError('broken')\n"))
    proc, _ = _run(base, head, tmp_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_the_harvester_finds_known_test_literals():
    h = gate_diff.harvest(ROOT)
    for cmd in ("true {PATH}>/dev/null; ls",            # a parametrize row fed through a helper
                "cd /dev && printf x > fd/3"):          # a module-level list fed through a helper
        assert cmd in h.commands, cmd
    assert any(a["tool"] == "read_file" and a["args"] == {"path": "~/.exo/keys/seat.key"}
               for a in h.actions.values())             # an action handed to the hook


def test_the_harvester_follows_helpers_rows_and_loops(tmp_path):
    tests = tmp_path / "tests" / "security"
    tests.mkdir(parents=True)
    (tests / "test_gate_sample.py").write_text('''
import pytest
from exo import gate
HOME_FILE = "~/.exo/state/x"
LINES = ["ls -la", "rm -rf build"]

def _sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}

def outcome(command, policy=None):
    return gate.decide(gate.classify(_sh(command)), policy).outcome

@pytest.mark.parametrize("line, want", [("git status", "allow"), pytest.param("git push", "log", id="push")])
def test_rows(line, want):
    assert outcome(line) == want

@pytest.mark.parametrize("cmd", LINES + ["echo hi"])
def test_list(cmd):
    assert outcome(f"{cmd} > {HOME_FILE}")

def test_loop_and_literals():
    for word in ("a", "b"):
        assert gate.classify_shell("printf " + word)
    assert gate.classify({"tool": "write_file", "args": {"path": "/tmp/y"}})
    assert gate.classify(("terminal", {"command": "make test", "cwd": "sub"}))
''', encoding="utf-8")
    h = gate_diff.harvest(tmp_path)
    assert {"git status", "git push", "ls -la > ~/.exo/state/x", "rm -rf build > ~/.exo/state/x",
            "echo hi > ~/.exo/state/x", "printf a", "printf b"} <= set(h.commands)
    actions = list(h.actions.values())
    assert {"tool": "write_file", "args": {"path": "/tmp/y"}} in actions
    assert {"tool": "terminal", "args": {"command": "make test", "cwd": "sub"}} in actions


def test_variants_wrap_simple_commands_only():
    out = list(gate_diff.variants(["rm x", "ls; rm x"]))
    assert len(out) == len(gate_diff.VARIANTS)
    assert "bash -c 'rm x'" in out and "cd ~/.exo/state && (cd /dev) && rm x" in out and "sudo rm x" in out


def _stream(path: Path, rows: list, names: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"header": {"policies": names, "actions": len(rows), "gate": str(path),
                                       "policy_errors": {}}}) + "\n")
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.write(json.dumps({"footer": {"secs": 0}}) + "\n")


def _d(outcome, reserved=(), violations=()):
    return [outcome, "rule", "why", [list(x) for x in reserved], list(violations)]


@pytest.mark.parametrize("base, head, kind", [
    (_d("deny"), ["ask", "error", "classify raised", [], []], "lowered"),     # the hook asks when the head raises
    (_d("allow"), ["ask", "error", "classify raised", [], []], "tightened"),
    (_d("ask", [("delete", "x"), ("force_push", "o")]), _d("ask", [("delete", "x")]), "lowered"),
    (_d("ask", violations=["missing footer: x", "forbidden word: y"]), _d("ask", violations=["forbidden word: y"]),
     "lowered"),
    (_d("ask", violations=["forbidden word: y"]), _d("ask", violations=["longer than 9 characters"]), "same"),
    (_d("deny", [("delete", "x")]), _d("deny"), "same"),                          # a deny needs no reserved parts
    (_d("log"), _d("ask"), "tightened"),
])
def test_compare_ranks_pairs_as_the_hook_would(tmp_path, base, head, kind):
    names = ["p"]
    _stream(tmp_path / "b.gz", [["delete", "x", None, [base], [0], 0]], names)
    _stream(tmp_path / "h.gz", [["delete", "x", None, [head], [0], 0]], names)
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    c = rep["counts"]
    got = "lowered" if c.get("lowered_pairs") else "tightened" if c.get("tightened_pairs") else "same"
    assert got == kind, c


def test_a_classification_change_with_the_same_decisions_is_counted_apart(tmp_path):
    _stream(tmp_path / "b.gz", [["delete", "x", None, [_d("ask")], [0], 0]], ["p"])
    _stream(tmp_path / "h.gz", [["local_write", "x", None, [_d("ask")], [0], 0]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    assert rep["counts"]["classification_only_actions"] == 1 and not rep["counts"].get("lowered_pairs")


def test_the_built_in_policies_cover_the_listed_kinds():
    pols = gate_diff.BUILTIN_POLICIES
    assert len(pols) >= 40
    for name in ("default", "deny_store_write", "categories_log", "reserved_store_write", "scope_internal_all",
                 "deny_target_ssh", "allow_ls_anchored", "allow_ls_unanchored", "deny_rule_rm",
                 "order_ask_under_category_deny", "matrix_deny_cells", "posting_close_and_merge",
                 "escalate_off", "category_opaque_exec_allow"):
        assert name in pols, name
    json.dumps(pols)
