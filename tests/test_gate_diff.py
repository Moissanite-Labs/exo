"""scripts/gate_diff.py: the never-looser-than-main differential for gate changes (#423)."""
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


def _tree(tmp_path: Path, name: str, patch: tuple[str, str] | None = None) -> Path:
    tree = tmp_path / name
    shutil.copytree(ROOT / "src" / "exo", tree / "src" / "exo", ignore=shutil.ignore_patterns("__pycache__"))
    if patch:
        gate = tree / "src" / "exo" / "gate.py"
        text = gate.read_text(encoding="utf-8")
        assert text.count(patch[0]) == 1, patch[0]
        gate.write_text(text.replace(patch[0], patch[1]), encoding="utf-8")
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


@pytest.mark.parametrize("case", ["home", "json", "raises", "not_json"])
def test_an_error_in_the_script_exits_2_not_1(tmp_path, case):
    # Exit 1 means lowered: a fault of the tool or of its input must never read as one.
    pol = tmp_path / "pols.py"
    pol.write_text({"raises": "POLICIES = {'x': undefined_name}\n",
                    "not_json": "POLICIES = {'x': {'gates': {'deny': {'delete'}}}}\n"}.get(case, "POLICIES = {}\n"),
                   encoding="utf-8")
    extra = {"home": ["--home", str(tmp_path / "missing")],
             "json": ["--json", str(tmp_path / "missing" / "out.json")]}.get(case, ["--policies", str(pol)])
    proc, _ = _run(ROOT, ROOT, tmp_path, *extra)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "gate_diff:" in proc.stderr and "Traceback" not in proc.stderr, proc.stderr
    assert "nothing lowered" not in proc.stdout


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


def test_every_seed_runs_under_every_shell_form(tmp_path):
    actions, counts = gate_diff.build_corpus((tmp_path,))
    seen = {json.dumps(a, sort_keys=True) for a in actions}
    forms = gate_diff.SHELL_FORMS
    assert {t for t, _ in forms} == set(gate_diff.SHELL_NAMES) and {k for _, k in forms} == {"command", "cmd"}
    for seed in ("rm -rf build", "cat ~/.ssh/id_rsa", "sudo rm -rf build", "bash <<< 'cat ~/.ssh/id_rsa'"):
        for tool, key in forms:
            assert json.dumps({"tool": tool, "args": {key: seed}}, sort_keys=True) in seen, (tool, key, seed)
    assert counts["cwd_forms"] > 0 and any(a.get("cwd") == "~/.ssh" for a in actions)


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
                 "escalate_off", "category_opaque_exec_allow"):
        assert name in pols, name
    json.dumps(pols)
