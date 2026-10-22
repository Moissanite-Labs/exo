"""scripts/gate_diff.py: the never-looser-than-main differential for gate changes (#423)."""
import ast
import gzip
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import gate_diff

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gate_diff.py"
DELETE_LINE = '    "delete":            ("irreversible", "private", False),\n'
CLASSIFY_DOC_END = '(_in_tool_folder sets it)."""\n'      # the first line of gate.classify's body comes after it


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
    lowered = {(x["action"]["args"]["command"], x["policy"]) for x in rep["lowered"]}
    assert lowered == {("rm notes.txt", "default")}      # deny_delete still denies, matrix_all_ask still asks
    assert rep["counts"]["lowered_pairs"] == 1 and rep["counts"]["actions"] == 4
    assert [(x["action"]["args"]["command"], x["policies"]) for x in rep["lowered_actions"]] == [
        ("rm notes.txt", ["default"])]
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
    assert rep["corpus_counts"] == {"harvested": 1, "harvested_base_only": 1}
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
    assert {a for a in with_folder} == {("credentials", "ask")}


def test_each_table_entry_has_a_line_main_reads(tmp_path):
    # A line for a prefix, wrapper, shell or interpreter must reach the `rm -rf build` inside it in main, and a
    # PROGRAMS or SUBCOMMANDS line must get its table's category: then a head that drops the entry lowers the line.
    t = gate_diff.gate_tables(ROOT)
    lines = gate_diff.table_lines([t])
    got = dict(zip(lines, _decisions(tmp_path, lines)))
    for table in ("_PREFIXES", "_WRAPPERS", "_SHELLS", "_INTERPRETERS", "_TMUX_RUNS", "_TMUX_ALIASES"):
        for name in t[table]:
            mine = [x for x in lines if (x.startswith(f"tmux {name} ") if table.startswith("_TMUX")
                                         else x.split()[0] == name or x.endswith(f"| {name}"))]
            assert any(got[x][0] == "delete" for x in mine), (table, name, {x: got[x] for x in mine})
    for name, cat in t["PROGRAMS"].items():
        if name == "crontab":       # main's own crontab branch runs first: the entry decides no line
            continue
        assert got[f"{name} notes.md"][0] == cat, name
    for (prog, sub), cat in t["SUBCOMMANDS"].items():
        assert got[f"{prog} {sub}"][0] == cat, (prog, sub)


# One loosening each, as a reviewer found them: a table entry dropped, a key or name no longer read, a posting shape
# ignored. The built-in corpus must lower a call or line for each (the action and the policy that shows it).
LOOSENINGS = {
    "own cwd ignored": (('if isinstance(sub, str) and sub and _runs_shell(tool, args):',
                         'if False and isinstance(sub, str) and sub and _runs_shell(tool, args):'),
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
                 'grep {"destination": "~/.ssh/id_rsa"}', 'mcp_acme_buy_item {"price": "10"}'):
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
], ids=["drop exec_command", "drop the cmd key"])
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
    assert list(counts) == ["files", "generated", "shell_forms", "cwd_forms"]
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


POLICY_READERS = ("check_post", "decide", "_decide", "normalize_policies")
NOT_POLICY_KEYS = {"_normalized"}       # marks a policy normalize_policies already filled in: never a written key


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
    assert {"footer_exposures", "ignore_case", "spent_today", "applies_to"} <= read
    used: set = set()
    for raw in gate_diff.BUILTIN_POLICIES.values():
        entry = gate_diff._entry(raw)
        _keys_of(entry["policy"], used)
        _keys_of(entry["context"], used)
    assert not read - used - NOT_POLICY_KEYS, sorted(read - used - NOT_POLICY_KEYS)
