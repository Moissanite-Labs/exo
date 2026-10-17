"""The run folder holds the principal credential (D-039): it is a secret store like ~/.ssh (#169).

Touching the folder itself, a credential file in it, or a wildcard there that could match one is credentials,
reads and writes alike. Job output below run/jobs/ and a bare relative `run` keep main's class.
Classification only; nothing here is run or read."""
from pathlib import Path

import pytest

from exo import gate

LAYOUTS = ["default", "default-principal", "custom", "custom-principal"]


@pytest.fixture(params=LAYOUTS)
def run_dir(request, tmp_path, monkeypatch):
    """A run folder as a command spells it: under the default ~/.exo or a custom EXO_HOME, the home's own
    or a principal's (principals/<name>/run)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)
    if request.param.startswith("custom"):
        exo_home = tmp_path / "elsewhere" / "exo"
        monkeypatch.setenv("EXO_HOME", str(exo_home))
    else:
        exo_home = home / ".exo"
        monkeypatch.delenv("EXO_HOME", raising=False)
    return str(exo_home / "principals" / "me" / "run" if request.param.endswith("principal") else exo_home / "run")


def sh(cmd):
    return ("bash", {"command": cmd})


def py(code):
    return sh(f'python3 -c "{code}"')


def _id(action):
    tool, args = action
    return f"{tool} {args.get('command') or args.get('code') or args.get('path')}"[:60]


SECRET = [
    lambda r: sh(f"cat {r}/*"),
    lambda r: sh(f"cat {r}/exod.principal.k*"),
    lambda r: sh(f"cat {r}/exod.tok*"),
    lambda r: sh(f"cat < {r}/exod.tok*"),
    lambda r: sh(f"head -n 1 {r}/agent-*"),
    lambda r: sh(f"cat {r}/*.key"),
    lambda r: sh(f"cat {r}/exod.principal.key"),
    lambda r: sh(f"ls {r}"),
    lambda r: sh(f"ls {r}/"),
    lambda r: sh(f"ls {r}/."),
    lambda r: sh(f"grep -r . {r}"),
    lambda r: sh(f"find {r}"),
    lambda r: sh(f"tar -cf - {r}"),
    lambda r: sh(f"rm -rf {r}"),
    lambda r: sh(f"rm {r}/*"),
    lambda r: sh(f"mv {r}/* /tmp/"),
    lambda r: sh(f"gzip {r}/exod*"),
    lambda r: sh(f"echo $(rm {r}/exod*)"),
    lambda r: sh(f"bash -c 'cat {r}/*'"),
    lambda r: sh(f"cat {r}/* > /tmp/out"),
    lambda r: py(f"import os; os.remove('{r}/exod.principal.key')"),
    lambda r: ("execute_code", {"code": f"import subprocess\nsubprocess.run(['ls', '{r}'])"}),
    lambda r: ("read_file", {"path": r}),
    lambda r: ("list_dir", {"path": r}),
    lambda r: ("grep", {"pattern": ".", "path": r}),
    lambda r: ("glob", {"pattern": "*", "path": r}),
    lambda r: ("write_file", {"path": f"{r}/exod.principal.key", "content": "x"}),
    lambda r: ("delete_file", {"path": f"{r}/exod.principal.key"}),
    lambda r: ("delete_file", {"path": r}),
    lambda r: sh(f"cat {r}//*"),                        # spellings of the same folder
    lambda r: sh(f"cat {r}/./*"),
    lambda r: sh(f"cat {r}/jobs/../*"),
    lambda r: sh(f"ls {r}/jobs/.."),
    lambda r: ("list_dir", {"path": f"{r}/jobs/.."}),
]


def _spelled(r):
    """The same folder through $EXO_HOME, ${EXO_HOME} and ~ where the layout allows it."""
    exo = Path(gate._exo_home())
    rel = Path(r).relative_to(exo)
    out = [f"$EXO_HOME/{rel}", f"${{EXO_HOME}}/{rel}"]
    if exo == Path(gate._home()) / ".exo":
        out.append(f"~/.exo/{rel}")
    return out


@pytest.mark.parametrize("make", SECRET, ids=lambda m: _id(m("RUN")))
def test_the_run_folder_and_its_secrets_are_credentials(make, run_dir):
    c = gate.classify(make(run_dir))
    assert c.category == "credentials", c
    assert gate.decide(c, {"gates": {"categories": {"credentials": "allow"}}}).outcome == "ask"


@pytest.mark.parametrize("cmd", ["cat ~/.config/gh/[h{]osts.ym[l}]", "cat ~/.config/gh/hosts.yml"])
def test_a_bracket_glob_with_a_brace_character_stays_credentials(cmd, run_dir):
    """Main's class (brace sets are #184: a fix must expand them, never rewrite a glob that already matches)."""
    assert gate.classify(sh(cmd)).category == "credentials"


@pytest.mark.parametrize("cmd,main", [("echo {a,b}", "read"), ("cp file{,.bak}", "shell"), ("cat src/{a,b}.py", "read"),
                                      ("awk '{print $1}' x.txt", "read"), ("ls {keys,state}", "read"),
                                      ("cat ~/{notes,docs}/*", "read"), ("rm -rf build/{a,b}", "delete")])
def test_an_ordinary_brace_set_keeps_mains_class(cmd, main, run_dir):
    assert gate.classify(sh(cmd)).category == main


@pytest.mark.parametrize("shape", ["cat {}/*", "ls {}", "rm -rf {}", "grep -r . {}/", "cat {}/./exod.tok*"])
def test_every_spelling_of_the_run_folder_is_credentials(shape, run_dir):
    for r in _spelled(run_dir):
        assert gate.classify(sh(shape.format(r))).category == "credentials", shape.format(r)


# Main's class (35b7de8, measured on a scratch worktree of main) for each layout. A principal's run folder
# under a custom EXO_HOME is not in main's store paths, so a delete there is a plain delete.
STORE = {"default": "store_write", "default-principal": "store_write", "custom": "store_write",
         "custom-principal": "delete"}
READABLE = [
    (lambda r: sh(f"cat {r}/jobs/1.log"), "read"),
    (lambda r: sh(f"cat {r}/jobs/*"), "read"),
    (lambda r: sh(f"cat {r}/jobs/./*"), "read"),
    (lambda r: sh(f"cat {r}/jobs/{{1,2}}.log"), "read"),
    (lambda r: sh(f"cat {r}/*.log"), "read"),
    (lambda r: sh(f"cat {r}/exod.pid"), "read"),
    (lambda r: sh(f"ls {r}/jobs"), "read"),
    (lambda r: ("read_file", {"path": f"{r}/jobs/1.log"}), "read"),
    (lambda r: ("grep", {"pattern": "error", "path": f"{r}/jobs"}), "read"),
    (lambda r: sh(f"rm {r}/jobs/1.log"), STORE),
    (lambda r: ("delete_file", {"path": f"{r}/jobs/1.log"}), STORE),
]


@pytest.mark.parametrize("make,main", READABLE, ids=lambda m: _id(m("RUN")) if callable(m) else "")
def test_job_output_in_the_run_folder_keeps_mains_class(make, main, run_dir, request):
    layout = request.node.callspec.params["run_dir"]
    want = main[layout] if isinstance(main, dict) else main
    assert gate.classify(make(run_dir)).category == want


@pytest.mark.parametrize("cmd,main", [("uv run pytest", "shell"), ("npm run build", "opaque_exec"),
                                      ("gh run list", "read"), ("ls run", "read"), ("cat run/*", "read")])
def test_a_bare_run_word_is_not_the_run_folder(cmd, main, run_dir, monkeypatch):
    """`run` is not resolved against the gate's own working folder, even when that is EXO_HOME."""
    exo_home = Path(gate._exo_home())
    exo_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(exo_home)
    assert gate.classify(sh(cmd)).category == main


# The run folder follows the ~/.ssh rule: the same shape gives the same class on both.
SHAPES = [
    lambda d, k, p: sh(f"cat {d}/*"),
    lambda d, k, p: sh(f"cat {d}/{p}*"),
    lambda d, k, p: sh(f"cat < {d}/{p}*"),
    lambda d, k, p: sh(f"cat {d}/{k}"),
    lambda d, k, p: sh(f"cat {d}/{k} > /tmp/out"),
    lambda d, k, p: sh(f"cp {d}/{k} /tmp/x"),
    lambda d, k, p: sh(f"ls {d}"),
    lambda d, k, p: sh(f"ls {d}/"),
    lambda d, k, p: sh(f"grep -r . {d}"),
    lambda d, k, p: sh(f"find {d}"),
    lambda d, k, p: sh(f"tar -cf - {d}"),
    lambda d, k, p: sh(f"rm -rf {d}"),
    lambda d, k, p: sh(f"rm {d}/*"),
    lambda d, k, p: sh(f"rm {d}/{k}"),
    lambda d, k, p: sh(f"mv {d}/* /tmp/"),
    lambda d, k, p: sh(f"gzip {d}/{k}"),
    lambda d, k, p: sh(f"chgrp x {d}/{k}"),
    lambda d, k, p: sh(f"echo $(rm {d}/{p}*)"),
    lambda d, k, p: sh(f"bash -c 'cat {d}/*'"),
    lambda d, k, p: py(f"import os; os.remove('{d}/{k}')"),
    lambda d, k, p: ("execute_code", {"code": f"import subprocess\nsubprocess.run(['ls', '{d}'])"}),
    lambda d, k, p: ("read_file", {"path": f"{d}/{k}"}),
    lambda d, k, p: ("list_dir", {"path": d}),
    lambda d, k, p: ("grep", {"pattern": ".", "path": d}),
    lambda d, k, p: ("glob", {"pattern": "*", "path": d}),
    lambda d, k, p: ("write_file", {"path": f"{d}/{k}", "content": "x"}),
    lambda d, k, p: ("delete_file", {"path": f"{d}/{k}"}),
    lambda d, k, p: ("delete_file", {"path": d}),
]


@pytest.mark.parametrize("shape", SHAPES, ids=lambda m: _id(m("D", "K", "P")))
def test_the_run_folder_follows_the_ssh_rule(shape, run_dir):
    ssh = str(Path(gate._home()) / ".ssh")
    want = gate.classify(shape(ssh, "id_rsa", "id_rs")).category
    got = gate.classify(shape(run_dir, "exod.principal.key", "exod.principal.k")).category
    assert got == want == "credentials"


def test_only_the_folder_itself_is_the_run_folder(run_dir):
    for p in (run_dir, run_dir + "/", run_dir + "/.", run_dir + "//", run_dir + "/jobs/.."):
        assert gate.is_sensitive_path(p), p
    for p in (run_dir + "/jobs", run_dir + "/jobs/1.log", run_dir + "/exod.pid", run_dir + "/exod.sock",
              str(Path(run_dir).parent), "run", "jobs/.."):
        assert not gate.is_sensitive_path(p), p
