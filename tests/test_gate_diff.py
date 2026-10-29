"""scripts/gate_diff.py: the never-looser-than-main differential for gate changes (#423)."""
import ast
import gzip
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from scripts import gate_diff

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gate_diff.py"
DELETE_LINE = '    "delete":            ("irreversible", "private", False),\n'
CLASSIFY_DOC_END = '(_in_tool_folder sets it)."""\n'      # the first line of gate.classify's body comes after it
# _decide lists the other posts' violations after this line. The mutant before it lists each text once (#460, #462)
ROOM = "    room, seen, listed = max(_LISTED_VIOLATIONS - len(violations), 0), [0, 0], []\n"
ONCE = "    named, held = [list(t) for t in zip(*dict.fromkeys(zip(named, held)))] or [[], []]\n"


def _tree(tmp_path: Path, name: str, patch: tuple[str, str] | None = None,
          more: tuple[tuple[str, str], ...] = ()) -> Path:
    tree = tmp_path / name
    shutil.copytree(ROOT / "src" / "exo", tree / "src" / "exo", ignore=shutil.ignore_patterns("__pycache__"))
    gate = tree / "src" / "exo" / "gate.py"
    text = gate.read_text(encoding="utf-8")
    for old, new in ((patch,) if patch else ()) + more:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    gate.write_text(text, encoding="utf-8")
    return tree


def _gate_diff(base: Path, head: Path, tmp_path: Path, *args: str, timeout: int = 120
               ) -> tuple[subprocess.CompletedProcess, dict]:
    out = tmp_path / "out.json"
    out.unlink(missing_ok=True)
    proc = subprocess.run([sys.executable, str(SCRIPT), "--base", str(base), "--head", str(head), "--json", str(out),
                           *args], capture_output=True, text=True, timeout=timeout)
    return proc, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else {})


def _run(base: Path, head: Path, tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess, dict]:
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("ls\nrm notes.txt\ngit status\ncat ~/.ssh/id_rsa\n", encoding="utf-8")
    return _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                      "--pick", "default,deny_delete,matrix_all_ask", "--jobs", "1", *extra)


def test_a_loosened_category_exits_1_and_names_the_action(tmp_path):
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    proc, rep = _run(base, head, tmp_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rm notes.txt" in proc.stdout and "delete ask -> delete allow" in proc.stdout
    lowered = {(x["action"]["args"]["command"], x["policy"], tuple(x["kinds"])) for x in rep["lowered"]}
    # deny_delete still denies. matrix_all_ask still asks, but an approval of the now reversible delete no longer binds
    # to the exact call (binds_exact, #551).
    assert lowered == {("rm notes.txt", "default", ("outcome",)), ("rm notes.txt", "matrix_all_ask", ("binds_exact",))}
    assert rep["counts"]["lowered_pairs"] == 2 and rep["counts"]["actions"] == 4
    assert rep["counts"]["lowered_binds_exact"] == 1
    assert [(x["action"]["args"]["command"], x["policies"]) for x in rep["lowered_actions"]] == [
        ("rm notes.txt", ["default", "matrix_all_ask"])]
    assert rep["gate_files"]["head"].startswith(str(head))


ROWS_SUITE = """import pytest
from exo import gate


@pytest.mark.parametrize("line", [{rows}])
def test_rows(line):
    assert gate.classify_shell(line)
"""


def test_a_test_row_the_head_deletes_still_runs(tmp_path):
    # The corpus comes from the tests of both trees: deleting the row of a loosened line must not hide it.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (DELETE_LINE, DELETE_LINE.replace("irreversible", "reversible")))
    for tree, rows in ((base, '"ls", "rm notes.txt"'), (head, '"ls"')):
        (tree / "tests" / "security").mkdir(parents=True)
        (tree / "tests" / "security" / "test_gate_rows.py").write_text(ROWS_SUITE.format(rows=rows), encoding="utf-8")
    proc, rep = _gate_diff(base, head, tmp_path, "--no-variants", "--pick", "default", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["corpus_counts"] == {"harvested": 1, "harvested_base_only": 1, "budgets": len(gate_diff.BUDGET_LINES)}
    assert [x["action"]["args"]["command"] for x in rep["lowered_actions"]] == ["rm notes.txt"]


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


@pytest.mark.parametrize("case", ["home", "json", "raises", "not_json", "corpus_item"])
def test_an_error_in_the_script_exits_2_not_1(tmp_path, case):
    # Exit 1 means lowered: a fault of the tool or of its input must never read as one.
    pol = tmp_path / "pols.py"
    pol.write_text({"raises": "POLICIES = {'x': undefined_name}\n",
                    "not_json": "POLICIES = {'x': {'gates': {'deny': {'delete'}}}}\n"}.get(case, "POLICIES = {}\n"),
                   encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(["ls", {"tool": "bash"}]), encoding="utf-8")
    extra = {"home": ["--home", str(tmp_path / "missing")],
             "json": ["--json", str(tmp_path / "missing" / "out.json")],
             "corpus_item": ["--corpus", str(bad)]}.get(case, ["--policies", str(pol)])
    proc, _ = _run(ROOT, ROOT, tmp_path, *extra)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "gate_diff:" in proc.stderr and "Traceback" not in proc.stderr, proc.stderr
    assert "nothing lowered" not in proc.stdout
    assert case != "corpus_item" or 'item 2 is not a command' in proc.stderr and '{"tool": "bash"}' in proc.stderr


def test_a_worker_that_dies_fails_the_run_and_never_hangs(tmp_path):
    # A pool that replaces a dead worker but never fails its task would wait forever.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (CLASSIFY_DOC_END, CLASSIFY_DOC_END + "    if 'zz-exit' in str(action):\n"
                                                                         "        os._exit(7)\n"))
    corpus = tmp_path / "many.txt"
    corpus.write_text("".join(f"echo {i}\n" for i in range(150)) + "echo zz-exit\n", encoding="utf-8")
    proc, _ = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                         "--pick", "default", "--jobs", "2", timeout=60)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "a worker process died" in proc.stderr and "head exit 3" in proc.stderr, proc.stderr


def test_a_tree_past_the_timeout_is_stopped(tmp_path):
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", SLEEP_PATCH)
    corpus = tmp_path / "slow.txt"
    corpus.write_text("echo zz-sleep\n", encoding="utf-8")
    proc, _ = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                         "--pick", "default", "--jobs", "1", "--timeout", "2", timeout=30)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "longer than --timeout 2s" in proc.stderr, proc.stderr


SLEEP_PATCH = (CLASSIFY_DOC_END, CLASSIFY_DOC_END + "    if 'zz-sleep' in str(action):\n"
                                                    "        __import__('time').sleep(60)\n")


def _procs_naming(text: str) -> dict[int, list[str]]:
    """Live processes whose argv names ``text`` (a zombie has no argv)."""
    out = {}
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                argv = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0") if a]
            except OSError:
                continue
            if any(text in a for a in argv):
                out[int(d.name)] = argv
    return out


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="finds the tree processes in /proc")
@pytest.mark.parametrize("sig", ["SIGTERM", "SIGHUP"])
def test_a_stop_signal_stops_both_trees_and_removes_the_work_folder(tmp_path, sig):
    # A CI cancel sends SIGTERM. The trees run in sessions of their own, so only the script can stop them.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", SLEEP_PATCH)
    corpus = tmp_path / "slow.txt"
    corpus.write_text("".join(f"echo {i}\n" for i in range(150)) + "echo zz-sleep\n", encoding="utf-8")
    log = tmp_path / "stderr.txt"
    with log.open("w", encoding="utf-8") as err_file:  # a file, not a pipe: a tree left running would hold a pipe
        proc = subprocess.Popen([sys.executable, str(SCRIPT), "--base", str(base), "--head", str(head),
                                 "--only-corpus", "--no-variants", "--corpus", str(corpus), "--pick", "default",
                                 "--jobs", "2"], stdout=subprocess.DEVNULL, stderr=err_file)
    try:
        deadline, kids = time.monotonic() + 60, {}
        while time.monotonic() < deadline:   # the head tree and its pool workers, asleep on zz-sleep
            kids = {pid: argv for pid, argv in _procs_naming(str(head)).items() if pid != proc.pid}
            if len(kids) >= 2 and proc.poll() is None:
                break
            time.sleep(0.2)
        assert len(kids) >= 2, kids
        argv = next(iter(kids.values()))
        work = Path(argv[argv.index("--corpus") + 1]).parent
        assert work.is_dir() and work.name.startswith("gate-diff-")
        proc.send_signal(getattr(signal, sig))
        proc.wait(timeout=20)
    finally:
        proc.kill()
    err = log.read_text(encoding="utf-8")
    assert proc.returncode == 2, err
    assert f"stopped by {sig}" in err, err
    deadline = time.monotonic() + 5
    while _procs_naming(str(tmp_path)) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _procs_naming(str(tmp_path))
    assert not work.exists()


def test_the_harvester_finds_known_test_literals():
    h = gate_diff.harvest(ROOT)
    for cmd in ("true {PATH}>/dev/null; ls",            # a parametrize row fed through a helper
                "cd /dev && printf x > fd/3",           # a module-level list fed through a helper
                "setsid rm -rf /tmp/x",                 # a row after a list comprehension in a sum
                r"IFS=$'\n'"):                          # sorted() of a dict
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
    assert gate.classify({"tool": "exec_command", "args": {"cmd": "rm -rf dist"}})
    assert gate.classify({"tool": "read_file", "args": {"path": "notes.txt"}}, cwd="/")
''', encoding="utf-8")
    h = gate_diff.harvest(tmp_path)
    assert {"printf a", "printf b"} <= set(h.commands)
    actions = list(h.actions.values())
    for line in ("git status", "git push", "ls -la > ~/.exo/state/x", "rm -rf build > ~/.exo/state/x",
                 "echo hi > ~/.exo/state/x"):     # kept as the bash calls they are, not made terminal ones
        assert {"tool": "bash", "args": {"command": line}} in actions, line
        assert line in ("git status", "git push") or line not in h.commands, line   # those two are rows of `line`
    assert {"tool": "write_file", "args": {"path": "/tmp/y"}} in actions
    assert {"tool": "terminal", "args": {"command": "make test", "cwd": "sub"}} in actions
    assert {"tool": "exec_command", "args": {"cmd": "rm -rf dist"}} in actions
    assert {"tool": "read_file", "args": {"path": "notes.txt"}, "cwd": "/"} in actions     # the session folder
    assert {"tool": "read_file", "args": {"path": "notes.txt"}} in actions


def test_the_harvester_reads_comprehensions_dicts_and_sums(tmp_path):
    tests = tmp_path / "tests" / "security"
    tests.mkdir(parents=True)
    (tests / "test_gate_forms.py").write_text('''
import pytest
from exo import gate
LINES = [f"rm const-{x}" for x in ("a", "b")]
BASE = ["rm base-row"]
ROWS = [("rm row-1", "ask"), ("rm row-2", "ask")]
TABLE = {"rm table-1": "ask", "rm table-2": "ask"}
LABELS = {"PS4": "export PS4='{}'"}

def sh(c):
    return gate.classify({"tool": "terminal", "args": {"command": c}})

@pytest.mark.parametrize("line", [f"rm {x}" for x in ("a", "b")])
def test_inline(line):
    sh(line)

@pytest.mark.parametrize("line", LINES)
def test_const(line):
    sh(line)

def test_loop():
    for line in [f"rm loop-{x}" for x in ("a", "b")]:
        sh(line)

@pytest.mark.parametrize("line", [*[f"rm star-{x}" for x in ("a",)], "rm star-plain"])
def test_star(line):
    sh(line)

@pytest.mark.parametrize("line", BASE + [r for r, _ in ROWS] + ["setsid rm -rf /tmp/sum"])
def test_sum(line):
    sh(line)

@pytest.mark.parametrize("line", sorted(TABLE))
def test_sorted(line):
    sh(line)

@pytest.mark.parametrize("line, want", {"rm items-1": "ask"}.items())
def test_items(line, want):
    sh(line)

def test_generator():
    assert all(sh(c) for c in {k: v for k, v in [("rm genexp-1", 1)]})

@pytest.mark.parametrize("line", [*unknown(), "rm after-unknown"] + unknown())
def test_part_not_read(line):
    sh(line)

@pytest.mark.parametrize("carrier", LABELS)
def test_label(carrier):
    sh(LABELS[carrier].format(nest()))
''', encoding="utf-8")
    got = set(gate_diff.harvest(tmp_path).commands)
    assert got == {"rm a", "rm b", "rm const-a", "rm const-b", "rm loop-a", "rm loop-b", "rm star-a", "rm star-plain",
                   "rm base-row", "rm row-1", "rm row-2", "setsid rm -rf /tmp/sum", "rm table-1", "rm table-2",
                   "rm items-1", "rm genexp-1", "rm after-unknown"}, got     # the row label PS4 is no command


def test_variants_wrap_simple_commands_only():
    out = list(gate_diff.variants(["rm x", "ls; rm x"]))
    assert len(out) == len(gate_diff.VARIANTS)
    assert "bash -c 'rm x'" in out and "cd ~/.exo/state && (cd /dev) && rm x" in out and "sudo rm x" in out
    assert "timeout 5 rm x" in out and "bash <<'EOF'\nrm x\nEOF" in out and "bash <<< 'rm x'" in out


def test_corpus_actions_are_kept_as_written():
    # The gate reads the tool name and the key: a rewrite to terminal would hide a head that drops either.
    for item in ({"tool": "bash", "args": {"command": "rm -rf build"}}, {"tool": "bash", "args": {"cmd": "ls"}},
                 {"tool": "exec_command", "args": {"command": "ls"}}, {"tool": "terminal", "args": {"cmd": "ls"}},
                 {"tool": "terminal", "args": {"command": "ls"}, "cwd": "~/.ssh"},
                 {"tool": "read_file", "args": {"path": "id_rsa"}, "cwd": "~/.ssh"}):
        assert gate_diff.normal_action(item) == item, item
    assert gate_diff.normal_action({"tool": "terminal", "args": {"command": "ls"}}) == gate_diff.normal_action("ls")
    with pytest.raises(ValueError):
        gate_diff.normal_action({"tool": "terminal", "args": {"command": "ls"}, "cwd": 3})


@pytest.mark.parametrize("item", [
    {"tool": "bash"}, {"tool": "bash", "args": "ls"}, {"tool": "bash", "arguments": ["ls"]},
    {"tool": "bash", "args": None, "arguments": {"command": "ls"}}, {"args": {"command": "ls"}},
    ["bash", {"command": "ls"}], 3, None, "", "   ",
], ids=["no args", "args a string", "arguments a list", "args null", "no tool", "a list", "a number", "null",
        "empty", "blank"])
def test_a_corpus_item_in_another_shape_stops_the_run(tmp_path, item):
    # A reviewer's item is never dropped, and a call with no arguments is not run as one with empty arguments.
    f = tmp_path / "mine.json"
    f.write_text(json.dumps(["ls", item]), encoding="utf-8")
    with pytest.raises(ValueError, match=r"mine\.json: item 2 is not a command or a \{tool, args\} action"):
        gate_diff.build_corpus((tmp_path,), [f], builtin=False, generate=False)


def test_a_corpus_file_reads_each_documented_shape():
    items = ["ls", {"tool": "bash", "args": {"cmd": "ls"}}, {"tool": "bash", "arguments": {"command": "ls"}},
             {"tool": "read_file", "args": {"path": "id_rsa"}, "cwd": "~/.ssh"}, {"tool": "list_tools", "args": {}}]
    assert [gate_diff.corpus_action(x) for x in items] == [
        {"tool": "terminal", "args": {"command": "ls"}}, {"tool": "bash", "args": {"cmd": "ls"}},
        {"tool": "bash", "args": {"command": "ls"}}, {"tool": "read_file", "args": {"path": "id_rsa"}, "cwd": "~/.ssh"},
        {"tool": "list_tools", "args": {}}]


def test_every_seed_runs_under_every_shell_form(tmp_path):
    actions, counts = gate_diff.build_corpus((tmp_path,))
    seen = {json.dumps(a, sort_keys=True) for a in actions}
    forms = gate_diff.SHELL_FORMS
    assert {t for t, _ in forms} == set(gate_diff.SHELL_NAMES) and {k for _, k in forms} == {"command", "cmd"}
    # A name that ends in terminal or shell is a shell call only with a string "command" (gate._runs_shell).
    assert all(t in gate_diff.SHELL_TOOLS for t, k in forms if k == "cmd"), forms
    assert {"powershell", "mcp__host__terminal"} <= {t for t, _ in forms}
    for seed in ("rm -rf build", "cat ~/.ssh/id_rsa", "sudo rm -rf build", "bash <<< 'cat ~/.ssh/id_rsa'"):
        for tool, key in forms:
            assert json.dumps({"tool": tool, "args": {key: seed}}, sort_keys=True) in seen, (tool, key, seed)
    assert counts["cwd_forms"] == len(gate_diff.cwd_rows()) and any(a.get("cwd") == "~/.exo/keys" for a in actions)


def test_the_shell_tool_names_are_the_gates():
    # SHELL_TOOLS and SHELL_SUFFIXES mirror gate._runs_shell, and gate_tables reads the names it lists.
    assert gate_diff.gate_tables(ROOT)["shell_tools"] == list(gate_diff.SHELL_TOOLS)
    mod = ast.parse((ROOT / "src" / "exo" / "gate.py").read_text(encoding="utf-8"))
    fn = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "_runs_shell")
    ends = [ast.literal_eval(c.args[0]) for c in ast.walk(fn) if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute) and c.func.attr == "endswith"]
    assert ends == [gate_diff.SHELL_SUFFIXES]


def test_shell_line_reads_a_call_as_the_gate_does():
    line = gate_diff.shell_line
    assert line({"tool": "bash", "args": {"cmd": "ls"}}) == "ls"
    assert line({"tool": "powershell", "args": {"command": "ls"}}) == "ls"
    assert line({"tool": "mcp__host__terminal", "args": {"command": "ls"}}) == "ls"
    assert line({"tool": "remote_terminal", "args": {"cmd": "ls"}}) is None        # no string "command"
    assert line({"tool": "read_terminal", "args": {"command": "ls"}}, known={"read_terminal"}) is None
    assert line({"tool": "read_file", "args": {"command": "ls"}}) is None


def _decisions(tmp_path: Path, actions: list, tree: Path = ROOT) -> list[tuple[str, str]]:
    """(category, outcome under the default policy) of each action, as ``tree``'s gate decides it in gate_diff's own
    child process, with its HOME and environment."""
    actions = [a if isinstance(a, dict) else {"tool": "terminal", "args": {"command": a}} for a in actions]
    work = tmp_path / "decide"
    work.mkdir()
    corpus, pols, out = work / "corpus.json", work / "policies.json", work / "out.jsonl.gz"
    corpus.write_text(json.dumps(actions), encoding="utf-8")
    pols.write_text(json.dumps({"default": gate_diff._entry({})}), encoding="utf-8")
    proc = gate_diff.start_tree("base", tree, corpus, pols, out, 1, gate_diff.make_home(work))
    assert proc.wait(timeout=120) == 0
    rows = list(gate_diff._rows(out))[1:-1]
    assert len(rows) == len(actions)
    return [(r[0], r[3][r[4][0]][0]) for r in rows]


def test_each_folder_row_decides_otherwise_without_its_folder(tmp_path):
    # A row that main decides the same without its folder checks nothing (#209): a head that ignores the folder
    # would pass. The joined row must also need the session folder, not only its own relative cwd.
    rows = gate_diff.cwd_rows()
    joined = [r for r in rows if r.get("cwd") and isinstance(r["args"].get("cwd"), str)]
    assert joined and len(rows) > 100
    twins = [gate_diff.without_cwd(r) for r in rows] + [{k: v for k, v in r.items() if k != "cwd"} for r in joined]
    got = _decisions(tmp_path, rows + joined + twins)
    with_folder, without = got[:len(rows) + len(joined)], got[len(rows) + len(joined):]
    same = [(r, a) for r, a, b in zip(rows + joined, with_folder, without) if a == b]
    assert not same, same[:3]
    assert {o for _, o in with_folder} == {"ask"}
    # The session folder makes the call credentials (#209). A shell call's own cwd adds a credentials part, and the
    # line keeps its own category (#311).
    assert {a for r, a in zip(rows + joined, with_folder) if "cwd" not in r["args"]} == {("credentials", "ask")}


# A child that loads main's gate with gate_diff's HOME and environment and decides each case's actions under every
# built-in policy, with one table entry taken out first when the case names one: (table, key) or (table, key, item).
REMOVE_CHILD = r"""
import json, os, sys
spec = json.load(open(sys.argv[1]))
sys.path.insert(0, os.path.join(spec["tree"], "src"))
from exo import gate
pols = [(gate.normalize_policies(e["policy"]), e.get("context")) for e in spec["policies"].values()]


def remove(path):
    name, *rest = path
    old = getattr(gate, name)
    key = tuple(rest[0]) if isinstance(rest[0], list) else rest[0]
    if len(rest) == 1:
        new = ({k: v for k, v in old.items() if k != key} if isinstance(old, dict)
               else type(old)(x for x in old if x != key))
    else:
        new = dict(old)
        v = old[key]
        new[key] = v.replace(rest[1], "") if isinstance(v, str) else type(v)(x for x in v if x != rest[1])
    setattr(gate, name, new)
    return name, old


def row(a):
    try:
        c = gate.classify({"tool": a["tool"], "args": a["args"]})
    except Exception as exc:
        return [type(exc).__name__]
    out = [c.category]
    for pol, ctx in pols:
        d = gate.decide(c, pol, ctx)
        out.append([d.outcome, sorted(map(str, d.reserved)), sorted(map(str, d.violations))])
    return out


res = []
for case in spec["cases"]:
    saved = remove(case["entry"]) if case["entry"] else None
    try:
        res.append([row(a) for a in case["actions"]])
    finally:
        if saved:
            setattr(gate, *saved)
json.dump(res, open(sys.argv[2], "w"))
"""


def _removed(tmp_path: Path, cases: list[tuple[tuple, list]]) -> tuple[list[str], list[list]]:
    """The built-in policy names, and for each case (entry, actions) the category and the decisions of main's gate
    under each policy for each action, with that entry taken out of its table (none for an empty entry)."""
    work = tmp_path / "removed"
    work.mkdir()
    pols = {k: gate_diff._entry(v) for k, v in gate_diff.BUILTIN_POLICIES.items()}
    spec = {"tree": str(ROOT), "policies": pols,
            "cases": [{"entry": list(e), "actions": [_as_action(a) for a in acts]} for e, acts in cases]}
    (work / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (work / "child.py").write_text(REMOVE_CHILD, encoding="utf-8")
    home = gate_diff.make_home(work)
    proc = subprocess.run([sys.executable, "-I", str(work / "child.py"), str(work / "spec.json"), str(work / "out.json")],
                          env=gate_diff.tree_env(home), cwd=str(home), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr
    return list(pols), json.loads((work / "out.json").read_text(encoding="utf-8"))


def _blind_by_design(entry: tuple, t: dict) -> str:
    """Why taking this entry out of main's gate changes no decision of any form, or ''. Each is a property of main's
    gate, not of the forms: no line or call could show the entry."""
    table, key, *item = entry
    if table == "_OPAQUE_PROGRAMS":
        return "a program the gate does not know asks as opaque_exec too: only the reason changes"
    if table == "TOOL_SUBCOMMANDS" and key in t["_INTERPRETERS"]:
        return "bun and deno are interpreters: the interpreter branch reads the line first"
    if table == "READ_PROGRAMS" and (key in t["_PREFIXES"] or key in ("cd", "pushd")):
        return "a prefix, or a folder change, which the gate reads before the allowlist"
    if table == "_INTERPRETERS" and key in ("perl", "ruby") and item[0] in ("-e", "-E"):
        return "perl and ruby read any cluster that ends in e or E (`-ne`) as inline code"
    if table == "_WRAPPERS" and len(item[0]) > 5 and any(o != item[0] and o.startswith(item[0]) for o in t[table][key]):
        return "a prefix of a longer option of the wrapper, which _wrapper_args then reads it as"
    if table == "_SENSITIVE_SAMPLES" and "/" in key and re.search(t["_SENSITIVE_PATH"], "/" + key.rpartition("/")[0] + "/"):
        return "its folder is a secret store by _SENSITIVE_PATH"
    if table == "_STORE_FOLDERS" and key == ".config/netlify":
        return "the folded Netlify name is a store by itself (#601), so the folder's pair adds no form of its own"
    if table == "TOOLS" and key in t["tool_names"]:
        return "the tool has a branch of its own in classify"
    from exo import gate
    if table == "TOOLS" and gate.classify_by_name(key) == t["TOOLS"][key]:
        return "the name hints give the same category"
    return {("PROGRAMS", "crontab"): "the crontab branch runs first",
            ("_LOCAL_HOSTS", "[::1]"): "the URL parser drops the brackets, so ::1 decides",
            ("_SENSITIVE_SAMPLES", ".config/exo/keys.env"): "the default EXO_KEYS_FILE, a sample of its own",
            }.get((table, key), "")


def test_each_table_entry_changes_its_form(tmp_path):
    # A form that main decides the same with its entry taken out of the table shows nothing: a head that drops the
    # entry, or one that adds it (its own form runs in both trees), would pass. So for each entry of table_forms,
    # and each TOOLS key, key-table entry and read verb of the calls, main's gate must decide one of its forms
    # otherwise without it, under some built-in policy (its outcome, reserved hits or posting violations).
    t = gate_diff.gate_tables(ROOT)
    forms: dict[tuple, list] = {}
    for _, item, entry in gate_diff.table_forms(t):
        if entry:
            forms.setdefault(tuple(tuple(x) if isinstance(x, list) else x for x in entry), []).append(item)
    calls = gate_diff.tool_actions([t], gate_diff.SHELL_TOOLS, gate_diff.BUILTIN_POLICIES.values())
    x, y = gate_diff.HINT_AROUND
    for name in gate_diff._names(t["TOOLS"]):
        forms[("TOOLS", name)] = [c for c in calls if c["tool"] == name][:40]
    values = {"_PATH_ARG_KEYS": (gate_diff.STORE_PATH, gate_diff.SECRET_PATH), "_TEXT_KEYS": (gate_diff.POST_TEXT,),
              "_TITLE_KEYS": (gate_diff.POST_TEXT,), "_TARGET_KEYS": (gate_diff.POST_TARGET,),
              "_AMOUNT_KEYS": gate_diff.AMOUNTS}
    for table, vals in values.items():
        for k in gate_diff._names(t[table]):
            forms[(table, k)] = [c for c in calls if c["args"].get(k) in vals][:60]
    for v in gate_diff._names(t["_READ_VERBS"]):
        forms[("_READ_VERBS", v)] = [c for c in calls if c["tool"] == f"mcp_{x}_{v}_{y}"]
    assert all(forms.values()), [e for e, f in forms.items() if not f]
    entries = list(forms)
    assert {e[0] for e in entries} >= {"PROGRAMS", "SUBCOMMANDS", "_PREFIXES", "_WRAPPERS", "_SAFE_PY_MODULES",
                                       "_EXEC_OPTS", "_GIT_EXEC_OPTS", "_EXEC_FILE_OPTS", "_WRITE_REDIRS",
                                       "_LOCAL_HOSTS", "_STORE_FOLDERS", "_SENSITIVE_SAMPLES", "_PRIVILEGED_WRAPPERS",
                                       "_INTERP_VALUE_FLAGS", "_WRAPPER_VALUE_OPTS", "_TMUX_RUNS", "TOOLS"}
    _, rows = _removed(tmp_path, [((), [f for e in entries for f in forms[e]])] + [(e, forms[e]) for e in entries])
    base, at, blind = rows[0], 0, []
    for e, without in zip(entries, rows[1:]):
        if base[at:at + len(forms[e])] == without and not _blind_by_design(e, t):
            blind.append((e, forms[e][:2]))
        at += len(forms[e])
    assert not blind, blind


def test_the_pattern_forms_get_main_s_category(tmp_path):
    # The forms written from patterns and from the HOME's links: a read of each _SENSITIVE_PATH sample asks as
    # credentials, each SQL statement kind is a delete, each patch header that names the store or a secret is a
    # write there, each post trips a rule of some built-in policy, and the budget and escalation policies decide
    # the payment and account calls as their limits say.
    t = gate_diff.gate_tables(ROOT)
    samples = gate_diff._secret_samples(t)
    assert len(samples) >= 40 and "x.pem" in samples and "proc/x/environ" in samples
    sql = gate_diff.regex_samples(t["_SQL_DESTRUCTIVE"])
    assert {"drop database", "drop schema", "drop view", "drop index", "delete from"} <= set(sql)
    patches = {path: gate_diff.regex_samples(t["_PATCH_PATHS"], path)
               for path in (gate_diff.STORE_PATH, gate_diff.SECRET_PATH)}
    assert any(p.startswith("*** Add File:") for p in patches[gate_diff.SECRET_PATH])
    texts = gate_diff.post_texts([t], gate_diff.BUILTIN_POLICIES.values())
    want: list[tuple[Any, str, str]] = []          # (action, policy, category outcome)
    want += [(f"cat ~/{s}", "default", "credentials ask") for s in samples]
    want += [({"tool": "read_file", "args": {"path": f"~/{s}"}}, "default", "credentials ask") for s in samples]
    want += [(f"{prog} {lead} {shlex.quote(s + ' x')}", "deny_delete", "delete deny") for s in sql
             for prog, lead in gate_diff.SQL_PROGRAMS]
    want += [({"tool": tool, "args": {"patch": p}}, "default", f"{cat} ask") for tool in t["patch_tools"]
             for path, cat in ((gate_diff.STORE_PATH, "store_write"), (gate_diff.SECRET_PATH, "credentials"))
             for p in patches[path]]
    want += [("cat ~/notes-link.txt", "default", "credentials ask"), ("cat notes-link.txt", "default", "credentials ask"),
             ("printf x > ~/grants-link.json", "default", "store_write ask")]
    buy = "mcp_acme_buy_item"
    for amount, daily, per_action in (("10", "allow", "allow"), ("18", "ask", "allow"), ("25", "ask", "ask"),
                                      ("500", "ask", "ask")):
        call = {"tool": buy, "args": {"amount": amount, "to": "acme"}}
        want += [(call, "budget_decides", f"payment {daily}"), (call, "budget_per_action", f"payment {per_action}"),
                 (call, "scope_private_allow_all", "payment ask")]
    want += [({"tool": buy, "args": {"to": "acme"}}, "budget_decides", "payment ask"),
             ({"tool": "mcp_create_account_item", "args": {"to": "acme"}}, "scope_private_allow_all", "account ask")]
    posts = [f"gh pr comment 3 --body {shlex.quote(s)}" for s in texts]
    names, rows = _removed(tmp_path, [((), [a for a, _, _ in want] + posts)])
    got = rows[0]
    wrong = [(a, p, w, f"{got[i][0]} {got[i][1 + names.index(p)][0]}") for i, (a, p, w) in enumerate(want)
             if f"{got[i][0]} {got[i][1 + names.index(p)][0]}" != w]
    assert not wrong, wrong
    quiet = [s for s, r in zip(texts, got[len(want):]) if not any(d[2] for d in r[1:])]
    assert not quiet, quiet                    # each post trips a posting or scrub rule under some policy
    assert len(texts) >= 15 and any("/home/" in s for s in texts) and any("password" in s for s in texts)


# One loosening each, as a reviewer found them: a table entry dropped, a key or name no longer read, a posting shape
# ignored. The built-in corpus must lower a call or line for each (the action and the policy that shows it).
LOOSENINGS = {
    "own cwd ignored": (('                    if isinstance(sub, str) and sub:\n',
                         '                    if False and isinstance(sub, str) and sub:\n'),
                        {"tool": "terminal", "args": {"command": "cat keys", "cwd": "~/.exo"}}, "default"),
    "vault tool read": (('("browser_vault_fill", "browser_vault_unlock", "browser_vault_save_login",',
                         '("browser_vault_fill", "browser_vault_save_login",'),
                        {"tool": "browser_vault_unlock", "args": {}}, "default"),
    "file_path unread": (('paths = [_first(args, ("path", "file_path"))]', 'paths = [_first(args, ("path",))]'),
                         {"tool": "edit_file", "args": {"file_path": "~/.exo/state/gate-grants.json"}}, "default"),
    "trash hint": (('"revoke", "trash"}, None, "delete")', '"revoke"}, None, "delete")'),
                   {"tool": "mcp_acme_trash_item", "args": {}}, "default"),
    "comment key": (('_TEXT_KEYS = ("body", "text", "message", "content", "comment", ',
                     '_TEXT_KEYS = ("body", "text", "message", "content", '),
                    {"tool": "mcp_gh_comment_item", "args": {"comment": "Secret plan"}}, "posting_forbid_words"),
    "shred": (('"shred": "delete", ', ''), "shred notes.md", "deny_delete"),
    "ssh-keygen": (('"ssh-keygen": "credentials", ', ''), "ssh-keygen notes.md", "categories_allow"),
    "nohup": (('"nohup": set(), ', ''), "nohup rm -rf build", "deny_delete"),
    "doas": (('"doas": {"-u", "-C"},', ''), "doas rm -rf build", "deny_delete"),
    "dash": (('_SHELLS = {"bash", "sh", "zsh", "dash", ', '_SHELLS = {"bash", "sh", "zsh", '),
             "dash -c 'rm -rf build'", "deny_delete"),
    "powershell": (('tool.endswith(("terminal", "shell")))', 'tool.endswith(("_terminal", "_shell")))'),
                   {"tool": "powershell", "args": {"command": "rm -rf build"}}, "default"),
    "forbid ignore_case": (('re.I if f.get("ignore_case") else 0', '0'),
                           {"tool": "mcp_gh_comment_item", "args": {"body": "Secret plan"}},
                           "posting_forbid_ignore_case"),
    "footer_exposures": (('footer_on = posting.get("footer_exposures") or ("internal", "public")',
                          'footer_on = ("internal", "public")'), "gh pr comment 3 --body hi",
                         "posting_footer_everywhere"),
    # The third review: an entry added to an allowlist, a value option, a pattern alternative, a scrub or forbid rule,
    # a decide step a built-in policy masked, an action value, a link in HOME and a privileged wrapper.
    "safe module": (('"pydoc", "pyright", "flake8", "pylint"}', '"pydoc", "pyright", "flake8", "pylint", "http.server"}'),
                    "python -m http.server", "default"),
    "value option": (('"ionice": {"-c", "-n", "-p"}', '"ionice": {"-c", "-n"}'), "ionice -p x rm -rf build",
                     "deny_delete"),
    "pem": ((r'id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]+\.pem|[^/]+\.key|', r'id_(rsa|dsa|ecdsa|ed25519)(_sk)?|[^/]+\.key|'),
            "cat ~/x.pem", "default"),
    "drop database": ((r'drop\s+(table|database|schema|trigger|view|index)', r'drop\s+(table|trigger)'),
                      "psql -c 'drop database x'", "deny_delete"),
    "patch add": ((r'(?:Update|Add|Delete)\s+File:', r'(?:Update)\s+File:'),
                  {"tool": "apply_patch", "args": {"patch": "*** Add File: ~/.ssh/id_rsa "}}, "default"),
    "home scrub": (('                  (r"/home/[A-Za-z0-9._-]+", "home path")]', '                  ]'),
                   "gh pr comment 3 --body 'status: /home/x'", "default"),
    "plain forbid": (('        elif str(f) and str(f) in both:\n            out.append(f"forbidden: {f}")\n', ''),
                     "gh pr comment 3 --body 'status: password'", "posting_forbid_patterns"),
    "scrub patterns": (('    for p in cfg.get("patterns") or []:\n        if isinstance(p, Mapping):',
                        '    for p in []:\n        if isinstance(p, Mapping):'),
                       "gh pr comment 3 --body 'status: bluefin'", "scrub_patterns"),
    "budget": (('    if esc.get("cost_over_budget", True):\n        per_action', '    if False:\n        per_action'),
               {"tool": "mcp_acme_buy_item", "args": {"amount": "25", "to": "acme"}}, "budget_per_action"),
    "escalate account": (('"categories": ["credentials", "account",', '"categories": ["credentials",'),
                         {"tool": "mcp_create_account_item", "args": {"to": "acme"}}, "scope_private_allow_all"),
    "cron action": (('        if action in ("list", "get", "status", ""):',
                     '        if action not in ("create", "add", "update", "edit"):'),
                    {"tool": "cronjob_manage", "args": {"action": "remove"}}, "default"),
    "send action": (('if str(args.get("action", "send")).lower() == "list":',
                     'if str(args.get("action", "send")).lower() != "send":'),
                    {"tool": "send_message", "args": {"action": "reply"}}, "default"),
    "link": (('            if real in cands and real != a and _sensitive_re().search(_fold(_below(real, base or scope.here, '
              'w, memo))):\n                return True\n', ''), "cat ~/notes-link.txt", "default"),
    "privileged": (('_PRIVILEGED_WRAPPERS = ("chroot", "nsenter", "unshare", "systemd-run")',
                    '_PRIVILEGED_WRAPPERS = ("nsenter", "unshare", "systemd-run")'), "chroot 1 touch notes.md",
                   "rule_costly_part_deny"),
}


def test_the_generated_calls_come_from_the_tables_of_both_trees(tmp_path):
    # A head that drops a table entry still runs the base's line or call, and one that adds an entry runs its own.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", more=tuple(LOOSENINGS[k][0] for k in (
        "vault tool read", "trash hint", "comment key", "shred", "ssh-keygen", "nohup", "doas", "dash")) + (
        ('READ_PROGRAMS = frozenset("""\n', 'READ_PROGRAMS = frozenset("""\nzzshow\n'),))
    actions, counts = gate_diff.build_corpus((head, base))
    seen = {gate_diff.show_action(a) for a in actions}
    for line in ("nohup rm -rf build", "doas rm -rf build", "shred notes.md", "sudo shred notes.md",
                 "dash -c 'rm -rf build'", "ssh-keygen notes.md", "docker push", "zzshow notes.md",
                 "tmux new-session 'rm -rf build'", "perl -E 'system(\"rm -rf build\")'", "browser_vault_unlock {}",
                 "mcp_acme_trash_item {}", 'mcp_gh_comment_item {"comment": "Secret plan"}',
                 'mcp_gh_comment_item {"chat_id": "pub/notes"}', 'edit_file {"file_path": "~/.exo/state/gate-grants.json"}',
                 'grep {"destination": "~/.ssh/id_rsa"}', 'mcp_acme_buy_item {"price": "10"}',
                 "doas -u x rm -rf build", "flock --wait x 1 rm -rf build", "tmux new-session -c x 'rm -rf build'",
                 "python -W x -c 'system(\"rm -rf build\")'", "uv run --with x rm -rf build", "python -m json.tool",
                 "python3 -m pytest x", "python -m pip install", "git rebase --exec 'rm -rf build'",
                 'cronjob_manage {"action": "xyz"}', 'send_message {"action": "reply"}', "cat ~/server.pem",
                 "psql -c 'drop database x'", "cat ~/notes-link.txt", "printf x &> ~/.exo/state/gate-grants.json"):
        assert line in seen, line
    assert counts["tables"] > 300 and counts["tool_forms"] > 2000


def _as_action(item: dict | str) -> dict:
    return item if isinstance(item, dict) else {"tool": "terminal", "args": {"command": item}}


def test_a_head_that_ignores_the_session_folder_is_lowered(tmp_path):
    # #209: the hook's cwd reaches classify. A head that drops it must lower a folder row of the built-in corpus.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", ('base = os.path.expanduser(cwd) if isinstance(cwd, str) and cwd else ""',
                                    'base = ""'))
    proc, rep = _gate_diff(base, head, tmp_path, "--pick", "default", "--jobs", "2", timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    lowered = [x["action"] for x in rep["lowered"]]
    assert {"tool": "terminal", "args": {"command": "cat keys"}, "cwd": "~/.exo"} in lowered
    assert all(a.get("cwd") for a in lowered), [a for a in lowered if not a.get("cwd")][:3]


def test_each_loosening_a_reviewer_found_is_lowered(tmp_path):
    # One head with every LOOSENINGS change, built-in corpus only (the copied trees hold no tests): each change must
    # lower its own call or line under its policy.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", more=tuple(p for p, _, _ in LOOSENINGS.values()))
    pick = ",".join(dict.fromkeys(pol for _, _, pol in LOOSENINGS.values()))
    proc, rep = _gate_diff(base, head, tmp_path, "--pick", pick, "--jobs", "2", "--json-limit", "1000000",
                           timeout=400)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    pairs = {(json.dumps(x["action"], sort_keys=True), x["policy"]) for x in rep["lowered"]}
    missed = [name for name, (_, item, pol) in LOOSENINGS.items()
              if (json.dumps(_as_action(item), sort_keys=True), pol) not in pairs]
    assert not missed, missed


SLEEP3_PATCH = (CLASSIFY_DOC_END, CLASSIFY_DOC_END + "    if 'zz-sleep' in str(action):\n"
                                                     "        __import__('time').sleep(3)\n")


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="needs SIGHUP")
def test_a_stop_signal_during_the_cleanup_is_ignored(tmp_path, monkeypatch):
    # A SIGTERM that comes while the trees are stopped must not leave one running or the work folder behind. After
    # main, the handlers it found are back, and a signal ignored at the start (nohup) is still ignored.
    work, seen = tmp_path / "work", []

    def work_dir() -> Path:
        work.mkdir()
        return work

    def stop_tree(_p: object) -> None:
        seen.append([signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)])
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(gate_diff, "work_dir", work_dir)
    monkeypatch.setattr(gate_diff, "start_tree", lambda *a: object())
    monkeypatch.setattr(gate_diff, "wait_trees", lambda procs, timeout: ({}, "head exit 3"))
    monkeypatch.setattr(gate_diff, "stop_tree", stop_tree)
    corpus = tmp_path / "mine.txt"
    corpus.write_text("ls\n", encoding="utf-8")
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        code = gate_diff.main(["--base", str(ROOT), "--head", str(ROOT), "--only-corpus", "--no-variants",
                               "--corpus", str(corpus), "--pick", "default", "--jobs", "1"])
        after = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    finally:
        for s, h in before.items():
            signal.signal(s, h)
    assert code == 2
    assert seen == [[signal.SIG_IGN, signal.SIG_IGN]] * 2       # both trees stopped, with both signals ignored
    assert not work.exists()
    assert after == {signal.SIGTERM: before[signal.SIGTERM], signal.SIGHUP: signal.SIG_IGN}


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="needs SIGHUP")
@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="finds the tree processes in /proc")
def test_a_run_under_nohup_ignores_sighup(tmp_path):
    # nohup starts the script with SIGHUP ignored: a closed terminal must not stop a long run.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", SLEEP3_PATCH)
    corpus = tmp_path / "slow.txt"
    corpus.write_text("echo zz-sleep\nls\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--base", str(base), "--head", str(head), "--only-corpus",
                             "--no-variants", "--corpus", str(corpus), "--pick", "default", "--jobs", "1"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    try:
        deadline = time.monotonic() + 30
        while not [p for p in _procs_naming(str(head)) if p != proc.pid] and time.monotonic() < deadline:
            time.sleep(0.1)
        proc.send_signal(signal.SIGHUP)
        out, err = proc.communicate(timeout=60)
    finally:
        proc.kill()
    assert proc.returncode == 0, out + err
    assert "nothing lowered" in out and "stopped by" not in err


@pytest.mark.parametrize("patch, example, only", [
    (('"terminal", "shell", "bash", "run_command", "exec_command")', '"terminal", "shell", "bash", "run_command")'),
     {"tool": "exec_command", "args": {"command": "rm -rf build"}}, lambda a: a["tool"] == "exec_command"),
    (('args.get("command") or args.get("cmd") or ""', 'args.get("command") or ""'),
     {"tool": "bash", "args": {"cmd": "rm -rf build"}}, lambda a: "cmd" in a["args"]),
    # #460: a name that ends in terminal is a shell tool until TOOLS lists it, and the gate reads "command" first.
    (('"read_terminal", "read_window_below"', '"read_terminal", "view_terminal", "read_window_below"'),
     {"tool": "view_terminal", "args": {"command": "rm -rf build"}}, lambda a: a["tool"] == "view_terminal"),
    (('args.get("command") or args.get("cmd") or ""', 'args.get("cmd") or args.get("command") or ""'),
     {"tool": "bash", "args": {"command": "rm -rf build", "cmd": "true"}},
     lambda a: set(a["args"]) == {"command", "cmd"}),
    # #462 review: an empty "command" falls through to "cmd" in the gate, as it does in a tool that reads either
    (('args.get("command") or args.get("cmd") or ""', 'args.get("command", args.get("cmd")) or ""'),
     {"tool": "bash", "args": {"command": "", "cmd": "rm -rf build"}}, lambda a: a["args"].get("command") == ""),
    # #462 review: a name read by its suffix gets the seed under "cmd" beside an empty "command" too
    (('tool not in TOOLS and isinstance(args.get("command"), str)',
      'tool not in TOOLS and bool(args.get("command")) and isinstance(args.get("command"), str)'),
     {"tool": "remote_terminal", "args": {"command": "", "cmd": "rm -rf build"}},
     lambda a: a["args"].get("command") == "" and a["tool"] not in gate_diff.SHELL_TOOLS),
], ids=["drop exec_command", "drop the cmd key", "a terminal name in TOOLS", "cmd read first", "empty command",
        "empty command, suffix name"])
def test_a_head_that_drops_a_shell_tool_name_or_key_is_lowered(tmp_path, patch, example, only):
    # Built-in corpus only: the copied trees hold no tests, so it is the seeds, their variants and their forms.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", patch)
    proc, rep = _gate_diff(base, head, tmp_path, "--pick", "deny_delete", "--jobs", "2", timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "LOWERED" in proc.stdout
    lowered = [x["action"] for x in rep["lowered"]]
    assert example in lowered and all(only(a) for a in lowered), lowered[:5]


SUDO_SHRED_PATCH = ('        sudo = sudo or base in ("sudo", "doas")\n',
                    '        sudo = sudo or base in ("sudo", "doas")\n'
                    '        if base == "sudo" and words[1:2] and words[1].text == "shred":\n'
                    '            return [], True, None\n')


def test_a_corpus_line_gets_the_compound_forms_too(tmp_path):
    # A line the reviewer adds is wrapped like a harvested one: here only its sudo form is lowered.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", SUDO_SHRED_PATCH)
    corpus = tmp_path / "mine.txt"
    corpus.write_text("shred -u notes.md\n", encoding="utf-8")
    proc, rep = _gate_diff(base, head, tmp_path, "--only-corpus", "--corpus", str(corpus), "--pick", "deny_delete",
                           "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "sudo shred -u notes.md" in [gate_diff.show_action(x["action"]) for x in rep["lowered"]]
    assert rep["corpus_counts"]["files"] == 1 and rep["corpus_counts"]["generated"] == len(gate_diff.VARIANTS)
    proc, rep = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                           "--pick", "deny_delete", "--jobs", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_corpus_action_runs_with_its_session_folder(tmp_path):
    # The cwd of an action reaches classify, as the hook passes it: a head that reads it otherwise shows.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (CLASSIFY_DOC_END, CLASSIFY_DOC_END + "    if cwd == '/zz-cwd':\n"
                                                                         "        return _make('read', summary='x')\n"))
    corpus = tmp_path / "mine.json"
    corpus.write_text(json.dumps([{"tool": "terminal", "args": {"command": "rm -rf build"}, "cwd": "/zz-cwd"},
                                  {"tool": "terminal", "args": {"command": "rm -rf build"}}]), encoding="utf-8")
    proc, rep = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                           "--pick", "deny_delete", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert [x["action"].get("cwd") for x in rep["lowered"]] == ["/zz-cwd"]
    assert "rm -rf build" in proc.stdout and "in /zz-cwd" in proc.stdout


def test_a_read_tool_the_gate_cannot_classify_is_an_ask(tmp_path):
    # D-084: the hook asks when classify raises, for a read tool too. So a head that raises on a read tightens it,
    # and a head that reads what the base raised on as an allowed read lowers it.
    clean = _tree(tmp_path, "clean")
    raises = _tree(tmp_path, "raises", (CLASSIFY_DOC_END, CLASSIFY_DOC_END + "    if 'zz-raise' in str(action):\n"
                                                                             "        raise RuntimeError('zz')\n"))
    corpus = tmp_path / "reads.json"
    corpus.write_text(json.dumps([{"tool": "read_file", "args": {"path": "zz-raise.md"}},
                                  {"tool": "search_files", "args": {"pattern": "zz-raise", "path": "."}}]),
                      encoding="utf-8")
    args = ("--only-corpus", "--no-variants", "--corpus", str(corpus), "--pick", "default", "--jobs", "1")
    proc, rep = _gate_diff(clean, raises, tmp_path, *args)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert rep["counts"]["tightened_actions"] == 2 and rep["counts"]["lowered_pairs"] == 0, rep["counts"]
    proc, rep = _gate_diff(raises, clean, tmp_path, *args)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert list(rep["lowered_by_transition"]) == ["error ask -> read allow"], rep["lowered_by_transition"]


# #551. The budget each BUDGET_LINES entry spends, as the constant that lifts it (None: _exo_relative's 64 store
# folders, a literal in the gate).
BUDGET_LIFTS = {"store folders": None, "join budget": "_EXO_PARTS", "join budget, levels up": "_EXO_PARTS",
                "join budget, shared by nested lines": "_EXO_PARTS", "brace budget, read": "_BRACE_BUDGET",
                "brace budget, write": "_BRACE_BUDGET"}


def test_each_budget_line_asks_on_main_only_because_its_budget_is_spent(monkeypatch, tmp_path):
    # So the seed stays meaningful if a budget moves: each line reaches its budget on main and asks, and with that
    # budget lifted main allows it. A head that fails open past the budget is then lowered on it.
    from exo import gate
    monkeypatch.setenv("HOME", str(tmp_path))
    assert set(BUDGET_LIFTS) == set(gate_diff.BUDGET_LINES)
    pol = gate.normalize_policies({})

    def run(line: str) -> tuple[str, bool]:
        c = gate.classify({"tool": "terminal", "args": {"command": line}})
        spent = any(gate._TOO_MANY in p.summary + p.target for r in gate.readings(c) for p in gate._each_part(r))
        return gate.decide(c, pol).outcome, spent

    for name, line in gate_diff.BUDGET_LINES.items():
        assert run(line) == ("ask", "brace" not in name), name
    # The folder cap: past 64 store folders the line asks even with the join budget lifted, and under it main allows.
    folders = gate_diff.BUDGET_LINES["store folders"]
    assert folders.count("cd ~/.exo/") > 64
    assert run("".join(f"cd ~/.exo/d{j}; " for j in range(60)) + "cat seat.db") == ("allow", False)
    # Each `<( )` of the shared line stays under the join budget alone: only together do they spend it.
    assert run(f"wc -l <({gate_diff._HALF})") == ("allow", False)
    for const in ("_EXO_PARTS", "_BRACE_BUDGET"):
        with monkeypatch.context() as m:
            m.setattr(gate, const, 10 ** 9)
            for name, line in gate_diff.BUDGET_LINES.items():
                if BUDGET_LIFTS[name] == const:
                    assert run(line) == ("allow", False), name
            if const == "_EXO_PARTS":
                assert run(folders) == ("ask", True)


def test_the_budget_lines_run_with_the_harvest_also_without_variants(tmp_path):
    actions, counts = gate_diff.build_corpus([tmp_path], generate=False)
    assert counts == {"budgets": len(gate_diff.BUDGET_LINES)}
    assert [a["args"]["command"] for a in actions] == list(gate_diff.BUDGET_LINES.values())
    assert gate_diff.build_corpus([tmp_path], builtin=False, generate=False) == ([], {})


# #551: changes main's gate_diff reported 0 lowered for. Each is (the patches, the corpus lines or None for the built-in
# corpus under --no-variants, the lowered (line, kinds) pairs under the default policy, which must be among them).
FAIL_OPEN = (("    if left[0] < 0:\n        return _TOO_MANY\n", '    if left[0] < 0:\n        return ""\n'),
             ("    except _Spent:\n        return _TOO_MANY\n", '    except _Spent:\n        return ""\n'))
DECIDE_DEF = ("def decide(c: Classification, policies: Mapping | None = None, context: Mapping | None = None) "
              "-> Decision:\n")


def _odd_outcome(word: str) -> tuple:
    # decide gives `word` for an asked call. A line read several ways then raises in the head (the strictest of the
    # readings cannot rank it), which the witness line is not.
    return ((DECIDE_DEF, "def decide(c, policies=None, context=None):\n    d = _decide_all(c, policies, context)\n"
                         f"    if d.outcome == 'ask':\n        d.outcome = {word!r}\n    return d\n\n\n"
                         + DECIDE_DEF.replace("def decide(", "def _decide_all(")),)


HOOK_LOOSENINGS = {
    # The lines are BUDGET_LINES entries, named here (BUDGET_LINES[name] when the test runs).
    "store budget fails open": (FAIL_OPEN, None, {(k, ("outcome",)) for k in (
        "store folders", "join budget", "join budget, levels up", "join budget, shared by nested lines")}),
    "decide says Ask": (_odd_outcome("Ask"), ["rm -rf build"], {("rm -rf build", ("outcome",))}),
    "decide says block": (_odd_outcome("block"), ["rm -rf build"], {("rm -rf build", ("outcome",))}),
    "NO_GRANT empty": ((('NO_GRANT = ("credentials", "store_write")', "NO_GRANT = ()"),), ["cat ~/.ssh/id_rsa"],
                       {("cat ~/.ssh/id_rsa", ("no_grant", "binds_exact"))}),    # binds_exact reads no_grant
    "binds_exact false": ((("    q = c.displaced\n", "    return False\n    q = c.displaced\n"),),
                          ["gh pr comment 3 --body hi"], {("gh pr comment 3 --body hi", ("binds_exact",))}),
    # The hook's check dropped, decide's own reading kept (its unseen-body violation still shows on main).
    "body_unseen dropped": ((("    unseen = body_unseen(eff)\n", "    unseen = _unseen(eff)\n"),
                             ("def body_unseen(c: Classification) -> bool:\n",
                              "def body_unseen(c: Classification) -> bool:\n    return False\n\n\n"
                              "def _unseen(c: Classification) -> bool:\n")),
                            ['gh pr comment 3 --body "$X"'], {('gh pr comment 3 --body "$X"', ("body_unseen",))}),
}


@pytest.mark.parametrize("name", list(HOOK_LOOSENINGS))
def test_a_head_that_loses_a_budget_an_outcome_or_a_hook_check_is_lowered(tmp_path, name):
    patches, lines, want = HOOK_LOOSENINGS[name]
    if lines is None:
        want = {(gate_diff.BUDGET_LINES[k], kinds) for k, kinds in want}
    base, head = _tree(tmp_path, "base"), _tree(tmp_path, "head", more=patches)
    args = ["--no-variants", "--pick", "default", "--jobs", "1"]
    if lines is not None:
        corpus = tmp_path / "lines.json"
        corpus.write_text(json.dumps(lines), encoding="utf-8")
        args += ["--only-corpus", "--corpus", str(corpus)]
    proc, rep = _gate_diff(base, head, tmp_path, *args, timeout=300)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    got = {(x["action"]["args"]["command"], tuple(x["kinds"])) for x in rep["lowered"]}
    assert want <= got, (got, rep["counts"])
    if name.startswith("decide says"):
        assert rep["counts"]["head_unknown_outcomes"] == 1 and rep["counts"]["base_unknown_outcomes"] == 0
        assert "outcomes not in allow/log/ask/deny" in proc.stdout


def test_a_hook_check_that_raises_is_the_hooks_error_card(tmp_path):
    # The hook calls binds_exact in the same try as decide: a raise sends the call to its error path, a once-only
    # ask. So it is a decide error, not a lost check, and it is not lowered against main's ask.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", ("    q = c.displaced\n", "    raise RuntimeError('zz')\n    q = c.displaced\n"))
    corpus = tmp_path / "lines.json"
    corpus.write_text(json.dumps(["gh pr comment 3 --body hi"]), encoding="utf-8")
    proc, rep = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                           "--pick", "default", "--jobs", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert rep["counts"]["head_decide_errors"] == 1 and rep["counts"]["lowered_pairs"] == 0, rep["counts"]


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
    # One violation swapped for another: the base's is gone, so the head no longer names what main asked to fix.
    (_d("ask", violations=["forbidden word: y"]), _d("ask", violations=["longer than 9 characters"]), "lowered"),
    # Review row 4: the footer violation reworded and a case-blind forbidden word dropped, at the same ask.
    (_d("ask", violations=["missing footer: -- bot", "forbidden word: secret"]),
     _d("ask", violations=["footer missing: -- bot"]), "lowered"),
    (_d("ask", violations=["forbidden word: y"]), _d("ask", violations=["forbidden word: y", "longer than 9"]),
     "same"),                                                                     # one more violation is not lower
    (_d("deny", [("delete", "x")]), _d("deny"), "same"),                          # a deny needs no reserved parts
    (_d("deny", violations=["forbidden word: y"]), _d("deny"), "same"),           # nor its violations: it blocks
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
    assert kind != "same" or c["pairs_detail_changed"] + c["pairs_same"] == 1


def _f(outcome, flags=(), reserved=(), rule="rule"):
    return [outcome, rule, "why", [list(x) for x in reserved], [], list(flags)]


@pytest.mark.parametrize("base, head, kind", [
    # #551: a hook check past decide the head no longer makes, at the same outcome below deny.
    (_f("ask", ["no_grant", "binds_exact"]), _f("ask", ["binds_exact"]), "no_grant"),
    (_f("ask", ["body_unseen"]), _f("ask"), "body_unseen"),
    (_f("log", ["body_unseen"]), _f("log"), "body_unseen"),       # a required executor refuses it below ask too
    (_f("ask", ["binds_exact"]), _f("ask"), "binds_exact"),
    # A reserved hit that moves onto the line's own (category, target): same count and categories, but a standing
    # grant for the line now covers the reserved act.
    (_f("ask", ["reserved_elsewhere"], [("delete", "y")]), _f("ask", [], [("delete", "x")]), "reserved_elsewhere"),
    (_f("deny", ["no_grant"]), _f("deny"), "same"),                      # a deny blocks: nothing to grant
    (_f("ask"), _f("ask", ["no_grant", "binds_exact"]), "same"),        # a check gained is not lower
    (_d("ask"), _f("ask", ["no_grant"]), "same"),                       # a row of a stream without flags
    # The hook's error card is once-only, so a head that errs loses none of the checks, and no reserved hit either:
    # the card refuses every grant they refuse.
    (_f("ask", ["binds_exact", "no_grant"]), _f("ask", ["no_grant", "error_card"], rule="error"), "same"),
    (_f("ask", [], [("delete", "y")]), _f("ask", ["error_card"], rule="error"), "same"),
    (_f("ask", ["no_grant"]), _f("ask", ["error_card"], rule="error"), "same"),
    # A decision whose rule is "error" but that a gate returned is not the card: what it drops is lost.
    (_f("ask", ["binds_exact"]), _f("ask", [], rule="error"), "binds_exact"),
    (_f("ask", [], [("delete", "y")]), _f("ask", [], rule="error"), "reserved"),
    # An outcome not in gate.OUTCOMES runs the call: allow in the head, ask in the base (#551).
    (_d("ask"), _d("Ask"), "outcome"), (_d("log"), _d("block"), "outcome"), (_d("allow"), _d("block"), "same"),
    (_d("deny"), _d("Deny"), "outcome"), (_d("Ask"), _d("ask"), "same"), (_d("Ask"), _d("allow"), "outcome"),
    (_f("block", ["binds_exact"]), _f("block"), "binds_exact"),         # the same odd outcome on both sides
])
def test_compare_counts_lost_hook_checks_and_odd_outcomes(tmp_path, base, head, kind):
    _stream(tmp_path / "b.gz", [["delete", "x", None, [base], [0], 0]], ["p"])
    _stream(tmp_path / "h.gz", [["delete", "x", None, [head], [0], 0]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    c = rep["counts"]
    assert c["tightened_pairs"] == 0, c
    if kind == "same":
        assert c["lowered_pairs"] == 0 and c["pairs_detail_changed"] + c["pairs_same"] == 1, c
    else:
        assert c["lowered_pairs"] == 1 and rep["lowered"][0]["kinds"] == [kind], rep["lowered"]
        assert c[f"lowered_{kind}"] == 1
    odd = [x[0] not in gate_diff.RANK for x in (base, head)]
    # Read with get, and the summary only for the rows #551 changes, so a row main already gets right (a "same" row
    # with known outcomes) is a guard that passes on main too.
    assert (c.get("base_unknown_outcomes", 0), c.get("head_unknown_outcomes", 0)) == tuple(map(int, odd)), c
    if kind == "same" and not any(odd):
        return
    rep.update(base="b", head="h", wall_secs=0, corpus_counts={})
    text = gate_diff.summary_text(rep)
    assert "no_grant lost" in text and "outcomes not in allow/log/ask/deny" in text


@pytest.mark.parametrize("base, head, lowered", [
    ((["X"], []), (["post: X"], ["post"]), 0),            # named after its post (#406): main's text is still there
    ((["X"], []), (["Y"], ["post"]), 1),
    ((["X"], []), (["other: X"], ["post"]), 1),           # a prefix that names no part of the line is a new text
    # The post's name changed with its target (#399): the same violation on the same post.
    ((["on a#9: missing footer: F"], ["on a#9"]), (["on b#5: missing footer: F"], ["on b#5"]), 0),
    ((["on a#9: missing footer: F"], ["on a#9"]), (["on b#5: forbidden word: F"], ["on b#5"]), 1),
    # One post's violation lost while another post still has it: the head lists it once where main listed it twice.
    ((["on a#9: missing footer: F", "on b#5: missing footer: F"], ["on a#9", "on b#5"]),
     (["on b#5: missing footer: F"], ["on a#9", "on b#5"]), 1),
])
def test_a_violation_named_after_its_post_is_kept(tmp_path, base, head, lowered):
    # A line of several posts names each one's violations after the post. Each tree's row lists its parts' names.
    _stream(tmp_path / "b.gz", [["github_post", "x", None, [_d("ask", violations=base[0])], [0], 0, base[1]]], ["p"])
    _stream(tmp_path / "h.gz", [["github_post", "x", None, [_d("ask", violations=head[0])], [0], 0, head[1]]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    assert rep["counts"]["lowered_pairs"] == lowered and rep["counts"]["violations_reworded"] == 1 - lowered


def test_a_text_main_lists_twice_counts_twice(tmp_path):
    # Two posts of one name each list their violation (#460): a head that lists the text once lost one.
    two, one = ["post: missing footer: F"] * 2, ["post: missing footer: F"]
    for base, head, lowered in ((two, one, 1), (two, two, 0), (one, two, 0)):
        _stream(tmp_path / "b.gz", [["delete", "x", None, [_d("ask", violations=base)], [0], 0, ["post"]]], ["p"])
        _stream(tmp_path / "h.gz", [["delete", "x", None, [_d("ask", violations=head)], [0], 0, ["post"]]], ["p"])
        rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
        assert rep["counts"]["lowered_pairs"] == lowered, (base, head, rep["counts"])


@pytest.mark.parametrize("base, head, lowered", [
    (["A", "B", "and 2 more on other posts"], ["A", "B", "C", "D"], 0),     # the head lists what main hid
    (["A", "B", "and 1 more on other posts"], ["A", "and 2 more on other posts"], 1),    # B is gone
    (["A", "and 1 more on other posts"], ["A", "and 3 more on other posts"], 0),
    (["A", "B", "and 3 more on other posts"], ["A", "B", "and 2 more on other posts"], 1),  # one fewer past the cap
    (["A", "B", "and 1 more on other posts"], ["A", "B"], 1),                               # the line gone
])
def test_the_line_that_counts_hidden_violations_counts_in_the_total(tmp_path, base, head, lowered):
    _stream(tmp_path / "b.gz", [["github_post", "x", None, [_d("ask", violations=base)], [0], 0, []]], ["p"])
    _stream(tmp_path / "h.gz", [["github_post", "x", None, [_d("ask", violations=head)], [0], 0, []]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    assert rep["counts"]["lowered_pairs"] == lowered, rep["counts"]


def test_a_reworded_pair_that_hides_fewer_is_lowered(tmp_path):
    base, head = ["p: A", "and 2 more on other posts"], ["q: A", "and 1 more on other posts"]
    _stream(tmp_path / "b.gz", [["github_post", "x", None, [_d("ask", violations=base)], [0], 0, ["p"]]], ["p"])
    _stream(tmp_path / "h.gz", [["github_post", "x", None, [_d("ask", violations=head)], [0], 0, ["q"]]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    assert rep["counts"]["lowered_violations"] == 1 and rep["counts"]["violations_reworded"] == 0, rep["counts"]


def test_reworded_examples_name_other_actions_and_a_cut_json_says_so(tmp_path):
    # #462 review nits: the examples were the first pairs in corpus order, often one line under several policies, and
    # --json cut the reworded list with no word of it.
    base, head = ["on a#9: missing footer: F"], ["on b#5: missing footer: F"]
    for path, v, n in ((tmp_path / "b.gz", base, "on a#9"), (tmp_path / "h.gz", head, "on b#5")):
        _stream(path, [["github_post", c, None, [_d("ask", violations=v)], [0, 0], 0, [n]] for c in "xy"], ["p", "q"])
    acts = [{"tool": "terminal", "args": {"command": c}} for c in "xy"]
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", acts, json_limit=3)
    assert rep["counts"]["violations_reworded"] == 4 and len(rep["reworded"]) == 3 and rep["reworded_truncated"], rep
    rep.update(base="b", head="h", wall_secs=0, corpus_counts={})
    text = gate_diff.summary_text(rep)
    shown = [line for line in text.splitlines() if line.startswith("           [")]
    assert len(shown) == 2 and shown[0].endswith(" x") and shown[1].endswith(" y"), shown
    assert "(--json keeps the first 3 reworded pairs, in corpus order)" in text, text


def test_a_reworded_pair_is_shown_and_counted_once(tmp_path):
    base, head = ["on a#9: missing footer: F"], ["on b#5: missing footer: F"]
    _stream(tmp_path / "b.gz", [["github_post", "x", None, [_d("ask", violations=base)], [0], 0, ["on a#9"]]], ["p"])
    _stream(tmp_path / "h.gz", [["github_post", "x", None, [_d("ask", violations=head)], [0], 0, ["on b#5"]]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    c = rep["counts"]
    assert c["violations_reworded"] == 1 and c["pairs_detail_changed"] == 0 and c["lowered_pairs"] == 0, c
    assert rep["reworded"] == [{"action": {"tool": "terminal", "args": {"command": "x"}}, "policy": "p", "kinds": [],
                                "base": {"category": "github_post", "violations": base},
                                "head": {"category": "github_post", "violations": head}}]
    rep.update(base="b", head="h", wall_secs=0, corpus_counts={})
    text = gate_diff.summary_text(rep)
    assert "Reworded, examples" in text and "base: on a#9: missing footer: F" in text, text
    assert "head: on b#5: missing footer: F" in text and "other detail changed: 0 pairs" in text, text


def test_each_post_also_runs_twice_to_one_pr():
    assert "rm -rf build; gh pr comment 5 --body x; gh pr comment 5 --body x" in gate_diff.post_lines(["x"])


def test_one_line_holds_more_posts_than_decide_lists():
    long = [line for line in gate_diff.post_lines(["x", "y"]) if line.count("gh pr comment") > 20]
    assert len(long) == 1 and long[0].count("gh pr comment") == 25 and long[0].startswith("rm -rf build; "), long
    assert gate_diff.post_lines([]) == []


def test_a_head_that_lists_a_text_once_where_main_lists_it_twice_is_lowered(tmp_path):
    # Through _row: the rows keep each text as often as decide lists it.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (ROOM, ONCE + ROOM))
    corpus = tmp_path / "mine.txt"
    corpus.write_text("rm -rf build; gh pr comment 5 --body 'hi there'; gh pr comment 5 --body 'hi again'\n",
                      encoding="utf-8")
    proc, rep = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                           "--pick", "posting_footer", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["counts"]["lowered_violations"] == 1, rep["counts"]


def test_a_head_that_hides_fewer_violations_past_the_cap_is_lowered(tmp_path):
    # The same head past the listing cap: both trees list the same 20 texts, and only `and N more` falls (#462 review)
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (ROOM, ONCE + ROOM))
    posts = "; ".join(f"gh pr comment {n} --body 'hi there'" for n in range(1, 25))
    corpus = tmp_path / "mine.txt"
    corpus.write_text(f"rm -rf build; {posts}; gh pr comment 1 --body 'hi there'\n", encoding="utf-8")
    proc, rep = _gate_diff(base, head, tmp_path, "--only-corpus", "--no-variants", "--corpus", str(corpus),
                           "--pick", "posting_footer", "--jobs", "1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert rep["counts"]["lowered_violations"] == 1, rep["counts"]


def test_post_names_hold_every_part_and_the_name_the_gate_hides_userinfo_in():
    from types import SimpleNamespace as NS
    user = "u:p" + "@"                 # split, so the privacy check (D-042) reads no address in this file
    gate = NS(redact_userinfo=lambda s: s.replace(user, "***@"))
    two = NS(summary=f"GitHub PR comment on https://{user}ghe.corp/pub/repo#5", category="github_post", parts=[])
    line = NS(summary="", category="github_post", parts=[NS(summary="git status", category="read", parts=[two]), two])
    assert gate_diff._post_names(gate, line) == [
        "GitHub PR comment on https://***@ghe.corp/pub/repo#5",
        f"GitHub PR comment on https://{user}ghe.corp/pub/repo#5",
        "github_post", "git status"]
    assert gate_diff._post_names(NS(), NS(summary="x", parts=None)) == ["x"]


def test_a_classify_error_is_labelled_error(tmp_path):
    _stream(tmp_path / "b.gz", [[None, None, "classify raised", [["ask", "error", "why", [], []]], [0], 0]], ["p"])
    _stream(tmp_path / "h.gz", [["read", "x", None, [_d("allow")], [0], 0]], ["p"])
    rep = gate_diff.compare(tmp_path / "b.gz", tmp_path / "h.gz", [{"tool": "terminal", "args": {"command": "x"}}])
    assert list(rep["lowered_by_transition"]) == ["error ask -> read allow"]
    assert gate_diff._decision(rep["lowered"][0]["base"]) == "error ask (error)"


def test_corpus_files_come_first_and_the_work_folder_is_out_of_tmp(tmp_path):
    # --json keeps the lowered rows in corpus order: the lines a reviewer adds must not fall past its cap.
    lines = tmp_path / "mine.txt"
    lines.write_text("echo mine\n", encoding="utf-8")
    actions, counts = gate_diff.build_corpus((tmp_path,), [lines])
    assert actions[0] == {"tool": "terminal", "args": {"command": "echo mine"}}
    # no gate, no tests here: the budget lines (#551) come with the harvest, before the generated forms
    assert list(counts)[:3] == ["files", "budgets", "generated"] and "harvested" not in counts
    assert "sudo echo mine" in [gate_diff.show_action(a) for a in actions]      # the line's own variants
    work = gate_diff.work_dir()
    try:
        tmp = os.path.realpath("/tmp")
        assert not os.path.realpath(work).startswith(tmp + os.sep) or not os.access("/var/tmp", os.W_OK)
    finally:
        work.rmdir()


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
                 "escalate_off", "category_opaque_exec_allow", "posting_forbid_ignore_case",
                 "posting_footer_everywhere", "rules_flat_named"):
        assert name in pols, name
    json.dumps(pols)
    from exo import gate
    for name, raw in pols.items():      # a policy the gate rejects would run as the defaults
        gate.normalize_policies(gate_diff._entry(raw)["policy"])


POLICY_READERS = ("check_post", "decide", "_decide", "normalize_policies", "normalize_scrub", "check_scrub")
NOT_POLICY_KEYS = {"_normalized"}       # marks a policy normalize_policies already filled in: never a written key
# The body files the exo-gate hook left unread on a line that does more than post (#343). The hook sets it, never a
# policy, and it changes only how the unseen note is worded: the post asks once either way.
NOT_POLICY_KEYS |= {"held_body_files"}


def _keys_read(fn: ast.FunctionDef) -> set[str]:
    """The string keys a function reads: x.get("k"), x["k"] and the keys of a `for k in ("a", "b")` loop."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            out.add(n.args[0].value)
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
            out.add(n.slice.value)
        elif isinstance(n, ast.For) and isinstance(n.iter, (ast.Tuple, ast.List)):
            out.update(e.value for e in n.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    return out


def _keys_of(v, out: set) -> set:
    if isinstance(v, dict):
        for k, x in v.items():
            out.add(k)
            _keys_of(x, out)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _keys_of(x, out)
    return out


def test_the_built_in_policies_use_every_key_the_gate_reads():
    # A head that stops reading a policy key (footer_exposures, a forbid entry's ignore_case) shows only when a
    # built-in policy sets that key.
    mod = ast.parse((ROOT / "src" / "exo" / "gate.py").read_text(encoding="utf-8"))
    fns = {n.name: n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name in POLICY_READERS}
    assert set(fns) == set(POLICY_READERS)
    read = set().union(*(_keys_read(f) for f in fns.values()))
    assert {"footer_exposures", "ignore_case", "spent_today", "applies_to", "patterns", "exposures", "allow"} <= read
    used: set = set()
    for raw in gate_diff.BUILTIN_POLICIES.values():
        entry = gate_diff._entry(raw)
        _keys_of(entry["policy"], used)
        _keys_of(entry["context"], used)
    assert not read - used - NOT_POLICY_KEYS, sorted(read - used - NOT_POLICY_KEYS)


def test_gate_tables_reads_every_table_of_main():
    # A table main holds in a form gate_tables does not read would lose its lines without a test failing.
    t = gate_diff.gate_tables(ROOT)
    assert set(gate_diff.GATE_TABLES) <= set(t), sorted(set(gate_diff.GATE_TABLES) - set(t))
    assert t["unread"] == {}
    assert gate_diff.unread_tables({"base": ROOT}) == {"base": {}}
    assert t["arg_values"]["cronjob_manage"]["action"][:1] == ["list"] and "patch" in t["patch_keys"]


SYNTH_GATE = """import re
def _words(s):
    return s.split()
TOOLS = dict(a_tool="read", **{"b_tool": "read"})
TOOLS |= {"zz_reader": "read"}
TOOLS["zz_lister"] = "read"
READ_PROGRAMS = frozenset(x for x in "cat ls zzshow".split() if x != "ls")
_SHELLS = set(_words("bash sh"))
PROGRAMS = {"rm": "delete"}
PROGRAMS.update({"zzrm": "delete"})
_INTERPRETERS = {"python": ("-c",)}
if True:
    _INTERPRETERS["zzpy"] = ("-c",)
_SENSITIVE_PATH = re.compile(r"(^|/)(\\.ssh|[^/]+\\.pem)(/|$)")
_SQL_DESTRUCTIVE = re.compile(r"(drop)\\1")
"""


def test_gate_tables_reads_later_writes_and_names_what_it_cannot(tmp_path):
    # `NAME |= lit`, `NAME[key] = lit`, dict() with keywords and a filtered comprehension are read. Any other write,
    # a table built by a call, a pattern the sampler cannot read and a missing table are named in "unread".
    gate = tmp_path / "src" / "exo" / "gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text(SYNTH_GATE, encoding="utf-8")
    t = gate_diff.gate_tables(tmp_path)
    assert set(t["TOOLS"]) == {"a_tool", "b_tool", "zz_reader", "zz_lister"}
    assert t["READ_PROGRAMS"] == {"cat", "zzshow"} and "x.pem" in gate_diff._secret_samples(t)
    unread = t["unread"]
    assert unread["_SHELLS"].startswith("line 8 is not a literal") and "_words" in unread["_SHELLS"]
    assert unread["PROGRAMS"] == "changed at line 10 (Call)" and unread["_INTERPRETERS"] == "changed at line 13 (Assign)"
    assert "cannot read" in unread["_SQL_DESTRUCTIVE"] and unread["OUTBOUND"] == "not in gate.py"
    assert {"TOOLS", "READ_PROGRAMS", "_SENSITIVE_PATH"}.isdisjoint(unread)
    actions, _ = gate_diff.build_corpus((tmp_path,))
    seen = {gate_diff.show_action(a) for a in actions}
    assert {"zz_reader {}", "zz_lister {}", "zzshow notes.md"} <= seen
    gate.write_text("TOOLS = {\n", encoding="utf-8")
    assert set(gate_diff.gate_tables(tmp_path)["unread"]) == set(gate_diff.GATE_TABLES)


TOOLS_ANCHOR = "# One list of secret stores, used by the path tools (read/write/patch) and by shell parsing.\n"


def test_a_read_tool_added_after_the_tools_literal_runs(tmp_path):
    # A head that adds a read tool below the TOOLS literal runs its call, which main reads as an unknown tool.
    base = _tree(tmp_path, "base")
    head = _tree(tmp_path, "head", (TOOLS_ANCHOR, 'TOOLS |= {"zz_reader": "read"}\nTOOLS["zz_lister"] = "read"\n'
                                    + TOOLS_ANCHOR))
    assert {"zz_reader", "zz_lister"} <= set(gate_diff.gate_tables(head)["TOOLS"])
    actions, _ = gate_diff.build_corpus((head, base))
    calls = [{"tool": n, "args": {}} for n in ("zz_reader", "zz_lister")]
    assert all(c in actions for c in calls)
    assert [c for c, _ in _decisions(tmp_path, calls)] == ["unknown"] * 2
    (tmp_path / "decide").rename(tmp_path / "decide-base")
    assert [c for c, _ in _decisions(tmp_path, calls, head)] == ["read"] * 2


def test_a_table_the_head_writes_otherwise_stops_the_run(tmp_path, monkeypatch):
    # A table changed in a form gate_tables does not read gets no lines: the run says so and exits 2, unless the
    # reviewer names the table in --accept-unread. A lowered pair would still exit 1.
    head = _tree(tmp_path, "head", (TOOLS_ANCHOR, 'TOOLS.update({"zz_reader": "read"})\n' + TOOLS_ANCHOR))
    monkeypatch.setattr(gate_diff, "build_corpus",
                        lambda *a, **k: ([{"tool": "terminal", "args": {"command": "ls"}}], {"files": 1}))
    argv = ["--base", str(ROOT), "--head", str(head), "--pick", "default", "--jobs", "1", "--json",
            str(tmp_path / "out.json")]
    assert gate_diff.main(argv) == 2
    rep = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert rep["incomplete"] == ["TOOLS"] and rep["unread_tables"]["head"]["TOOLS"].startswith("changed at line")
    assert gate_diff.main(argv + ["--accept-unread", "TOOLS"]) == 0
    other = _tree(tmp_path, "other", ('OUTBOUND = ("github_post", "message_send", "email_send", "social_post", "web_post", '
                                      '"publish")', 'OUTBOUND = "github_post"'))
    assert gate_diff.unread_tables({"head": other, "base": ROOT}) == {
        "head": {"OUTBOUND": "a string here, a collection in the base"}, "base": {}}
    # A mapping where main has a set is read the same (its keys), so neither tree is named.
    shells = _tree(tmp_path, "shells", ('_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "mksh", "busybox"}',
                                        '_SHELLS = {"bash": 1, "sh": 1, "zsh": 1, "dash": 1, "ksh": 1, "fish": 1, '
                                        '"mksh": 1, "busybox": 1}'))
    assert gate_diff.unread_tables({"head": shells, "base": ROOT}) == {"head": {}, "base": {}}


def test_odd_table_values_never_stop_the_forms(tmp_path):
    # A table of another shape (a TOOLS value that is a mapping or a list) gives fewer forms, never an error.
    odd = {"TOOLS": {"odd_map": {"cat": "read"}, "odd_list": ["read"], "gh_tool": "github_post"},
           "OUTBOUND": ("github_post",), "_TEXT_KEYS": ("body",), "_PREFIXES": {"sudo": None, 3: {"-u"}},
           "_WRAPPERS": ["setsid"], "_TMUX_RUNS": {"new-session": 3}, "_INTERPRETERS": {"python": None},
           "_EXEC_OPTS": {("tar",): ("-I",)}, "NAME_HINTS": [({"pay"}, None)], "DEFAULT_SCRUB": ["x"],
           "_HOME_PATTERNS": [3, ("/home/[a-z]+",)], "arg_values": {"t": {"action": "list"}}}
    calls = gate_diff.tool_actions([odd], policies=[{"posting": {"forbid": [3, None, {"pattern": 4}]}}, ["x"]])
    assert {"tool": "odd_map", "args": {}} in calls and {"tool": "gh_tool", "args": {"body": "Secret plan"}} in calls
    assert list(gate_diff.table_forms(odd))
    head = _tree(tmp_path, "head", ('    "cronjob_manage": "schedule",\n',
                                    '    "cronjob_manage": "schedule",\n    "odd_tool": {"cat": "read"},\n'))
    actions, _ = gate_diff.build_corpus((head, ROOT))
    assert {"tool": "odd_tool", "args": {}} in actions


def test_and_or_in_a_table_read_as_python_reads_them(tmp_path):
    # `A or B` is the first truthy operand and `A and B` the first falsy one, as in Python, so the table keeps its
    # entries instead of turning into a bool.
    gate = tmp_path / "src" / "exo" / "gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text('_SHELLS = {"bash", "sh"} or set()\nREAD_PROGRAMS = () or ("cat", "ls")\n'
                    'SHELL_PROGRAMS = ("make",) and ("echo", "true")\nPROGRAMS = {} and {"rm": 1}\n', encoding="utf-8")
    t = gate_diff.gate_tables(tmp_path)
    assert t["_SHELLS"] == {"bash", "sh"} and t["READ_PROGRAMS"] == ("cat", "ls")
    assert t["SHELL_PROGRAMS"] == ("echo", "true") and t["PROGRAMS"] == {}


def test_a_catalog_of_another_shape_is_read_as_the_gate_reads_it(tmp_path):
    # A roles part that is not a table stops the gate's read of the models but keeps the providers it read first.
    # Lists of models and providers are read as the gate reads them. None of these ends the run.
    catalog = tmp_path / "src" / "exo" / "core" / "models" / "catalog.toml"
    catalog.parent.mkdir(parents=True)
    for text, posts in (('roles = ["a"]\n', []),
                        ('providers = ["p"]\nmodels = ["q:m1"]\n[[roles]]\nmain = "x:y"\n', ["routed to p:x"]),
                        ('models = ["a:b"]\nproviders = ["p"]\n', ["built with b", "routed to p:x"])):
        catalog.write_text(text, encoding="utf-8")
        assert gate_diff._catalog_posts(tmp_path, ()) == posts


# A child that loads main's gate, classifies each action once and decides it under every built-in policy with each
# step of gate._decide taken out in turn: each bump() call, the part deny and the deny entries. It writes, for each
# step, its first line and how many pairs it lowered.
STEP_CHILD = r"""
import ast, inspect, json, os, sys, textwrap
spec = json.load(open(sys.argv[1]))
sys.path.insert(0, os.path.join(spec["tree"], "src"))
from exo import gate
pols = [(gate.normalize_policies(e["policy"]), e.get("context")) for e in spec["policies"].values()]
cs = [gate.classify({"tool": a["tool"], "args": a["args"]}) for a in spec["actions"]]
RANK = {"allow": 0, "log": 1, "ask": 2, "deny": 3}


def decisions():
    return [[(d.outcome, set(map(str, d.reserved)), set(map(str, d.violations)))
             for d in (gate.decide(c, pol, ctx) for pol, ctx in pols)] for c in cs]


def lower(b, h):
    return RANK[h[0]] < RANK[b[0]] or (h[0] == b[0] != "deny" and bool(b[1] - h[1] or b[2] - h[2]))


src = textwrap.dedent(inspect.getsource(gate._decide))
fn = ast.parse(src).body[0]
steps = []
for node in ast.walk(fn):
    for field in ("body", "orelse"):
        stmts = getattr(node, field, None)
        for i, st in enumerate(stmts if isinstance(stmts, list) else []):
            first = (ast.get_source_segment(src, st) or "").split("\n")[0]
            if (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and getattr(st.value.func, "id", "") == "bump"
                    or isinstance(st, ast.If) and ("_override_deny" in first or 'gates.get("deny")' in first)):
                steps.append((getattr(node, field), i, first.strip()))
base, orig, out = decisions(), gate._decide, []
for stmts, i, first in steps:
    saved, stmts[i] = stmts[i], ast.Pass()
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "<step>", "exec"), gate.__dict__)
    stmts[i] = saved
    try:
        out.append([first, sum(lower(b, h) for rb, rh in zip(base, decisions()) for b, h in zip(rb, rh))])
    finally:
        gate._decide = orig
json.dump(out, open(sys.argv[2], "w"))
"""

# Built-in actions that, under the built-in policies, show each step of gate._decide when it is taken out.
STEP_WITNESSES = ("rm -rf ~/.exo/state", "rm x", "make test", "ls", "gh pr comment 3 --body hi", "git commit -m wip",
                  "npm publish", "cat ~/.ssh/id_rsa", "tee policies/gates.yaml < /tmp/x", "echo $(git status)",
                  {"tool": "mcp_acme_buy_item", "args": {"to": "acme"}},
                  {"tool": "mcp_acme_buy_item", "args": {"amount": "25", "to": "acme"}},
                  {"tool": "mcp_acme_buy_item", "args": {"amount": "18", "to": "acme"}},
                  {"tool": "mcp_create_account_item", "args": {"to": "acme"}})


def test_each_decide_step_lowers_a_built_in_pair(tmp_path):
    # A step that every built-in policy masks with another ask (the budget, the escalation of payment and account
    # in the second review) checks nothing: a head that drops it would pass. Each step of main's _decide that raises
    # an outcome, taken out alone, must lower a pair of a built-in action and a built-in policy. A new step needs a
    # witness here, and a policy or a generated form that shows it.
    actions, _ = gate_diff.build_corpus((ROOT,))
    keys = {json.dumps(a, sort_keys=True) for a in actions}
    witnesses = [_as_action(w) for w in STEP_WITNESSES]
    assert all(json.dumps(w, sort_keys=True) in keys for w in witnesses)
    work = tmp_path / "steps"
    work.mkdir()
    spec = {"tree": str(ROOT), "policies": {k: gate_diff._entry(v) for k, v in gate_diff.BUILTIN_POLICIES.items()},
            "actions": witnesses}
    (work / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    (work / "child.py").write_text(STEP_CHILD, encoding="utf-8")
    home = gate_diff.make_home(work)
    proc = subprocess.run([sys.executable, "-I", str(work / "child.py"), str(work / "spec.json"), str(work / "out.json")],
                          env=gate_diff.tree_env(home), cwd=str(home), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    steps = json.loads((work / "out.json").read_text(encoding="utf-8"))
    assert len(steps) >= 14
    assert not [first for first, lowered in steps if not lowered], steps


def test_a_decision_shows_its_total_with_the_hidden_count():
    # #462 review nit: the summary showed how many entries a tree listed, so a fall in N alone did not show
    d = {"category": "github_post", "outcome": "ask", "rule": "posting", "reserved": [],
         "violations": ["A", "B", "and 3 more on other posts"]}
    assert gate_diff._decision(d) == "github_post ask (posting, violations 5)", gate_diff._decision(d)
