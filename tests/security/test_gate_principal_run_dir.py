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
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "CLAUDE_CONFIG_DIR", "EXO_SEAT"):
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
    lambda r: sh(f"cat {r}/agent-default.principal.k*"),   # the default agent's key (exo_tui.conn)
    lambda r: sh(f"cat {r}/agent-default.*"),
    lambda r: sh(f"cat {r}/agent-d*"),
    lambda r: sh(f"cat {r}/{{*,}}"),                        # brace sets (#184) are expanded first
    lambda r: sh(f"cat {{{r},x}}/*"),
    lambda r: sh(f"cat {Path(r).parent}/{{run,x}}/*"),
    lambda r: sh(f"cat {Path(r).parent}/r{{u,}}n/*"),
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


@pytest.mark.parametrize("shape", ["cat {}/agent-{}.principal.k*", "cat {}/agent-{}.*", "head -n 1 {}/agent-{}*"])
def test_a_seat_or_agent_principal_key_is_credentials(shape, run_dir, monkeypatch):
    """Each agent serves at run/agent-<seat>.sock with its key beside it: $EXO_SEAT and the seats the home
    defines ($EXO_HOME/seats/<name>.toml, .json or <name>/) are sampled, not one stand-in name."""
    seats = Path(gate._exo_home()) / "seats"
    seats.mkdir(parents=True)
    (seats / "scout.toml").write_text('name = "scout"\n')
    (seats / "ops.json").write_text("{}")
    (seats / "ops-desk").mkdir()
    monkeypatch.setenv("EXO_SEAT", "lead")
    for name in ("lead", "scout", "ops", "ops-desk"):
        assert gate.classify(sh(shape.format(run_dir, name))).category == "credentials", name


# A glob with no folder is matched against the run secrets by their own names, not their paths (fnmatch's `*`
# crosses `/`), no agent name is made up, and an agent key counts only by its `agent-` or `.principal` part (a
# seat named `main` or `test` does not make `*main*` ask): these keep main's class in any folder.
BARE_KEEP = [("ls *main*", "read"), ("git branch --list '*main*'", "read"), ("find . -name '*main*'", "read"),
             ("git log --all -- '*main*'", "read"), ("find . -name 'run*'", "read"), ("ls *run*", "read"),
             ("grep 'run*' notes.txt", "read"), ("ls -d *run*/", "read"), ("ls *test*", "read"),
             ("find . -name '*test*'", "read"), ("git branch --list '*test*'", "read"), ("ls *default*", "read"),
             ("ls age*", "read"),
             # a folder part that is only wildcards, or a literal folder that is not a run folder, cannot reach a
             # run folder's agent keys in bash, though fnmatch's `*` crosses `/`
             ("ls */*default*", "read"), ("ls */*main*", "read"), ("ls ~/*test*", "read"),
             ("find . -path '*/*test*'", "read")]
# A bare name that could be a run secret in the folder it is run in, and any glob that names the run folder path.
BARE_ASK = ["cat exod*", "cat exod.tok*", "cat *.principal.key", "cat agent-*", "ls agent-d*", "rm exod*",
            "cat *-default.principal*", "cat agent-default*",
            "ls ~/.exo/run*", "ls */run*", "ls .exo/run*", "grep -r . ~/.exo/run*/",
            "cat ~/.exo/*/*default*", "cat */agent-*", "cat .exo/run/*default*"]


@pytest.fixture
def plain_home(monkeypatch):
    """A home whose path names nothing these globs could match (a CI home such as /home/runner would)."""
    monkeypatch.setenv("HOME", "/nonexistent/u")
    for k in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "CLAUDE_CONFIG_DIR", "EXO_SEAT"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("cmd,main", BARE_KEEP)
def test_a_bare_glob_elsewhere_keeps_mains_class(cmd, main, plain_home, monkeypatch):
    """Also when a sampled seat is named in it (a seat file is sampled the same way as $EXO_SEAT)."""
    for seat in (None, "main", "test"):
        if seat:
            monkeypatch.setenv("EXO_SEAT", seat)
        assert gate.classify(sh(cmd)).category == main, seat


@pytest.mark.parametrize("cmd", BARE_ASK)
def test_a_bare_secret_name_or_a_run_folder_glob_asks(cmd, plain_home):
    assert gate.classify(sh(cmd)).category == "credentials"


LINKED = [
    lambda lk: sh(f"grep -r . {lk}/.."),
    lambda lk: sh(f"cat {lk}/../*"),
    lambda lk: sh(f"ls {lk}/.."),
    lambda lk: sh(f"cat {lk}/../exod.tok*"),
    lambda lk: ("list_dir", {"path": f"{lk}/.."}),
    lambda lk: ("grep", {"pattern": ".", "path": f"{lk}/.."}),
]


@pytest.fixture
def jobs_link(run_dir, tmp_path):
    """A real symlink to run/jobs, outside the home: its `..` is the run folder, not the link's own folder."""
    (Path(run_dir) / "jobs").mkdir(parents=True)
    link = tmp_path / "links" / "jobs"
    link.parent.mkdir()
    link.symlink_to(Path(run_dir) / "jobs")
    return str(link)


@pytest.mark.parametrize("make", LINKED, ids=lambda m: _id(m("LINK")))
def test_dotdot_through_a_link_to_the_jobs_folder_is_the_run_folder(make, jobs_link):
    assert gate.classify(make(jobs_link)).category == "credentials"


@pytest.mark.parametrize("make", LINKED, ids=lambda m: _id(m("LINK")))
def test_dotdot_through_a_link_in_the_home_is_the_run_folder(make, run_dir):
    """The link right in $HOME: `~/jl/..` is $HOME once normalised, but bash goes through the link."""
    (Path(run_dir) / "jobs").mkdir(parents=True)
    home = Path(gate._home())
    (home / "jl").symlink_to(Path(run_dir) / "jobs")
    for lk in (str(home / "jl"), "~/jl", "$HOME/jl", "${HOME}/jl"):
        assert gate.classify(make(lk)).category == "credentials", lk


@pytest.mark.parametrize("make", [lambda lk: sh(f"cat {lk}/*"), lambda lk: sh(f"ls {lk}"),
                                  lambda lk: ("list_dir", {"path": lk})], ids=["cat", "ls", "list_dir"])
def test_job_output_through_a_link_stays_read(make, jobs_link):
    assert gate.classify(make(jobs_link)).category == "read"


def test_only_the_folder_itself_is_the_run_folder(run_dir):
    for p in (run_dir, run_dir + "/", run_dir + "/.", run_dir + "//", run_dir + "/jobs/.."):
        assert gate.is_sensitive_path(p), p
    for p in (run_dir + "/jobs", run_dir + "/jobs/1.log", run_dir + "/exod.pid", run_dir + "/exod.sock",
              str(Path(run_dir).parent), "run", "jobs/.."):
        assert not gate.is_sensitive_path(p), p
