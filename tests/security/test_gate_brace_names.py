"""Issue #438: bash expands a brace set before a builtin reads a variable name in it.

`export {PATH,X}=` is `export PATH= X=`, so the line empties PATH and a later `ls` runs a `./ls`
an earlier write can plant (#329). The gate read the word as the name `{PATH,X}` and allowed it.
It now brace-expands the operands of the builtins that take a name: an exporter (`export`,
`declare`, `typeset`, `readonly`, `local`), `read`, `printf`, `test` and `[`, and `wait`. It reads
the words bash makes as well as the words as written. So a set that spells PATH asks as
`export PATH=` does, a set of other names keeps the class of the names it makes
(`export {A,B}=1; ls` reads as `export A=1 B=1; ls`), and a word that asked before still asks. An
option in a set counts too: `test {-v,'a[PATH=0]'}` sets PATH as `test -v 'a[PATH=0]'` does, and
a command in a subscript the set names is read (#179). `unset` asks on any unquoted brace already
(#329). A quoted or escaped brace is not expanded, and bash refuses the word as a name. bash fills
the last name `printf -v` is given, so the gate reads each one. Each line in the PATH list was
checked in bash 5.2.21 to set PATH and run a planted `./ls` (`+=:` and `printf -v PATH X` run one
from `.` or `X/` that PATH lacks, the `.venv/\\<newline>bin` lines one from that folder, and the
`a[PATH=0]` lines one from `0/`), but the `ssh` line and the wrapper lines bash cannot run a
builtin under (`sudo`, `env`, `timeout`), which ask as `sudo export PATH=; ls` does.

Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(cmd: str):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


@pytest.mark.parametrize("cmd", [
    # the issue's rows: read/allow on main 5a9dc394
    "export {PATH,X}=; ls",
    "declare {PATH,X}=; ls",
    "typeset {PATH,X}=; ls",
    "export {PATH,X}=.; ls",
    "local {PATH,X}=; ls",            # bash takes `local` only in a function; the body reads the same
    "readonly {PATH,X}=; ls",
    # PATH anywhere in the set, a prefix or a suffix around it, nested and sequence sets
    "export {X,PATH}=; ls",
    "export {PATH,}=; ls",
    "export {,PATH}=; ls",
    "export {PATH=,X}; ls",
    "export {P,}ATH=; ls",
    "export {PA,X}TH=; ls",
    "export {P{A,B},X}TH=; ls",
    "export {P,Q}{A,B}TH=; ls",
    "export {PATH,X}+=:; ls",
    "export {PATH,X}='$PATH:'; ls",   # single quotes keep `$PATH` as a folder name
    'export {"}",PATH}=; ls',         # a quoted } does not close the set
    'export {PATH,"X Y"}=; ls',
    "export {#,PATH}=; ls",
    "read -p {#,PATH} X; ls",         # `#` is the prompt, not a comment: read fills PATH
    "export {P..\\\nP}ATH=; ls",      # bash drops the \<newline> first: the set is {P..P}
    "declare {'a[PATH=0]=1',X}; ls",  # a subscript in a name the set makes
    # options, before and inside the set
    "declare -x {PATH,X}=; ls",
    "declare -g {PATH,X}=; ls",
    "declare -gx {PATH,X}=; ls",
    "typeset -x {PATH,X}=.; ls",
    "local -x {PATH,X}=; ls",
    "declare +x {PATH,X}=; ls",
    "export -n {PATH,X}=; ls",
    "export -- {PATH,X}=; ls",
    "declare -{x,g} {PATH,X}=; ls",
    # prefixes, wrappers, compounds and nested lines
    "command export {PATH,X}=; ls",
    "builtin export {PATH,X}=; ls",
    "builtin declare -x {PATH,X}=; ls",
    "env export {PATH,X}=; ls",
    "sudo export {PATH,X}=; ls",      # as `sudo export PATH=; ls` asks on main
    "timeout 5 export {PATH,X}=; ls",
    "export {PATH,X}= && ls",
    "cd . && export {PATH,X}= && ls",
    "(export {PATH,X}=; ls)",
    "{ export {PATH,X}=; ls; }",
    "x=$(export {PATH,X}=; ls)",
    "echo `export {PATH,X}=; ls`",
    "bash -c 'export {PATH,X}=; ls'",
    "ssh h 'export {PATH,X}=; ls'",
    "export \\\n{PATH,X}=; ls",
    # read and printf -v fill the names they are given
    "read {X,PATH} <<< 'a .'; ls",
    "read -r {X,PATH} <<< 'a .'; ls",
    "read -a{X,PATH} <<< .; ls",
    "printf -v {PATH,X} .; ls",
    "printf -v{X,PATH} .; ls",
    "printf {-v,PATH} .; ls",
    "printf -v X -v PATH .; ls",      # bash fills the last -v name, brace or not
    "printf {-v,X,-v,PATH} .; ls",    # a word the set makes twice stays in its place
    # bash keeps a \<newline> in '..' and $'..', so PATH is not .venv/bin
    "export {PATH,X}='.venv/\\\nbin'; ls",
    "export {PATH,X}=$'.venv/\\\nbin'; ls",
    # an option in a set: the name it takes sets PATH through its subscript
    "test {-v,'a[PATH=0]'}; ls",
    "[ {-v,'a[PATH=0]'} ]; ls",
    "printf {-v,'a[PATH=0]'} x; ls",
    "declare {-n,'r=a[PATH=0]'}; echo $r; ls",
])
def test_a_brace_set_that_spells_path_asks(cmd):
    c = _bash(cmd)
    assert c.category == "opaque_exec", c
    assert any(p.category == "opaque_exec" and p.summary.startswith("PATH ") for p in [c, *c.parts]), c
    assert gate.decide(c).outcome == "ask", c


def test_local_in_a_function_body_names_path():
    """The function body is read as its own line: the part for `local` names PATH."""
    for cmd in ("f() { local {PATH,X}=; ls; }; f", "function f { local -- {PATH,X}=; ls; }; f"):
        c = _bash(cmd)
        assert gate.decide(c).outcome == "ask", c
        assert any(p.category == "opaque_exec" and p.summary.startswith("PATH ") for p in c.parts), c


@pytest.mark.parametrize("cmd", ["printf -vPAT? .; ls", "printf -v X -vPAT? .; ls"])
def test_a_glob_in_a_printf_v_name_asks(cmd):
    """A glob in a name joined to `-v` is a name the gate cannot read, as it is apart (`printf -v PAT?`):
    with a file named `-vPATH` in the folder, bash runs `printf -vPATH .`."""
    c = _bash(cmd)
    assert c.category == "opaque_exec" and gate.decide(c).outcome == "ask", c


@pytest.mark.parametrize("cmd, policy, outcome", [
    ("export {LD_PRELOAD,X}=/tmp/x.so; ls", {}, "ask"),
    ("export {HOME,X}=/tmp; git status", {}, "ask"),
    ("export {GIT_SSH_COMMAND,X}='rm -rf build'; git fetch", {"gates": {"deny": ["delete"]}}, "deny"),
    ("declare {PS4,X}='$(rm -rf build)'; set -x; ls", {"gates": {"deny": ["delete"]}}, "deny"),
])
def test_other_variables_in_a_brace_set_are_read(cmd, policy, outcome):
    assert gate.decide(_bash(cmd), policy).outcome == outcome


_POLICIES = [
    {},
    {"gates": {"categories": {"opaque_exec": "allow", "local_write": "ask"}}},
    {"gates": {"deny": ["opaque_exec"]}},
    {"gates": {"deny": ["delete"]}},
]


@pytest.mark.parametrize("cmd, expanded", [
    # other names keep the class of the names they make
    ("export {A,B}=1; ls", "export A=1 B=1; ls"),
    ("export {A,B}=1", "export A=1 B=1"),
    ("declare -x {A,B}=1 && ls", "declare -x A=1 B=1 && ls"),
    ('export {PATH,X}="$PATH:/usr/bin"; ls', 'export PATH="$PATH:/usr/bin" X="$PATH:/usr/bin"; ls'),
    ("read {A,B} <<< x; ls", "read A B <<< x; ls"),
    ("printf -v {A,B} x; ls", "printf -v A B x; ls"),
    ("export -f {a,b}; ls", "export -f a b; ls"),
    ("declare -p {PATH,X}; ls", "declare -p PATH X; ls"),
    # and a set that spells PATH decides as the expanded line, under each policy
    ("export {PATH,X}=; echo hi > out.txt", "export PATH= X=; echo hi > out.txt"),
    ("export {PATH,X}=; rm -rf build", "export PATH= X=; rm -rf build"),
    ("read {X,PATH} <<< 'a .'; ls", "read X PATH <<< 'a .'; ls"),
    ("printf {-v,X,-v,PATH} .; ls", "printf -v X -v PATH .; ls"),
    ("export {PATH,X}='.venv/\\\nbin'; ls", "export PATH='.venv/\\\nbin' X='.venv/\\\nbin'; ls"),
    # bash drops a \<newline> in "..", so PATH is .venv/bin, a folder the gate trusts
    ('export {PATH,X}=".venv/\\\nbin"; ls', 'export PATH=".venv/\\\nbin" X=".venv/\\\nbin"; ls'),
    # a command in a subscript the set names runs as it does in the name bash makes (#179)
    ("printf {-v,'a[$(rm -rf build)]'} x", "printf -v 'a[$(rm -rf build)]' x"),
    ("test {-v,'a[$(rm -rf build)]'}", "test -v 'a[$(rm -rf build)]'"),
    ("[ {-v,'a[$(rm -rf build)]'} ]", "[ -v 'a[$(rm -rf build)]' ]"),
    ("declare {-i,'X=a[$(rm -rf build)]'}", "declare -i 'X=a[$(rm -rf build)]'"),
    ("declare {-n,'r=a[$(rm -rf build)]'}", "declare -n 'r=a[$(rm -rf build)]'"),
    ("wait {-p,'a[$(rm -rf build)]'}", "wait -p 'a[$(rm -rf build)]'"),
    ("test {-v,'a[PATH=0]'}; ls", "test -v 'a[PATH=0]'; ls"),
    # and a test of other words keeps its class
    ("test -f {a,b}.txt && ls", "test -f a.txt b.txt && ls"),
    ("[ -e {a,b} ] && ls", "[ -e a b ] && ls"),
])
def test_a_brace_set_decides_as_the_words_bash_makes(cmd, expanded):
    a, b = _bash(cmd), _bash(expanded)
    assert a.category == b.category, (a, b)
    for policy in _POLICIES:
        assert gate.decide(a, policy).outcome == gate.decide(b, policy).outcome, (policy, a, b)


@pytest.mark.parametrize("cmd", [
    # these asked on main as a word the gate cannot read, and still do: the words as written are read as before
    "unset {PATH,}; ls",
    "unset {PATH,X}; ls",
    "unset {A,B}; ls",
    "unset -v {PATH,X}; ls",
    "export X{A,B}=1; ls",
    "export X={a,b}; ls",
    "export PATH=/usr/bin{,}; ls",
    "declare -n r={PATH,X}; ls",
    "declare -{n,} r=PATH; ls",
    "read X{A,B} <<< x; ls",
    # a member of a set sets a git config key the line set before it: the key as written is still read. With a
    # readonly name bash keeps the first value, so git runs the hook (-r stops the later assignment); with export it
    # keeps the last
    "declare -rx GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=./h {GIT_CONFIG_KEY_0=a,b}; "
    "git commit -m x",
    "declare -rx GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=./x.sh {GIT_CONFIG_KEY_0=a,b}; "
    "git status",
    "typeset -xr GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=./h {GIT_CONFIG_KEY_0=a,b}; "
    "git commit -m x",
    "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=./h {GIT_CONFIG_KEY_0=a,b}; "
    "git commit -m x",
    "export GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=h {GIT_CONFIG_KEY_0=a,b}; git commit -m x",
])
def test_a_brace_that_asked_before_still_asks(cmd):
    c = _bash(cmd)
    assert c.category == "opaque_exec" and gate.decide(c).outcome == "ask", c


_SSH_KEY = "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.sshCommand 'GIT_CONFIG_VALUE_0=rm -rf build'"


@pytest.mark.parametrize("cmd, policy, outcome", [
    (f"typeset -xr {_SSH_KEY} {{GIT_CONFIG_KEY_0=a,b}}; git fetch", {}, "ask"),
    (f"typeset -xr {_SSH_KEY} {{GIT_CONFIG_KEY_0=a,b}}; git fetch", {"gates": {"deny": ["delete"]}}, "deny"),
    (f"declare -rx {_SSH_KEY} {{GIT_CONFIG_KEY_0=a,b}}; git fetch", {"gates": {"deny": ["delete"]}}, "deny"),
    (f"export {_SSH_KEY} {{GIT_CONFIG_KEY_0=a,b}}; git fetch", {"gates": {"deny": ["delete"]}}, "deny"),
    ("declare -rx GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=./h {GIT_CONFIG_KEY_0=a,b}; "
     "git commit -m x", {"gates": {"deny": ["opaque_exec"]}}, "deny"),
    # bash keeps a readonly name's first value without a brace too, so each value a key takes is read
    ("declare -rx GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=./h GIT_CONFIG_KEY_0=a; "
     "git commit -m x", {}, "ask"),
    ("typeset -xr GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=./x.sh GIT_CONFIG_KEY_0=a; "
     "git status", {}, "ask"),
    (f"declare -rx {_SSH_KEY} GIT_CONFIG_VALUE_0=x; git fetch", {"gates": {"deny": ["delete"]}}, "deny"),
    ("declare -rx GIT_CONFIG_COUNT=1 {GIT_CONFIG_KEY_0=core.hooksPath,X=1} GIT_CONFIG_VALUE_0=./h GIT_CONFIG_KEY_0=a; "
     "git commit -m x", {}, "ask"),
])
def test_a_git_config_key_the_line_set_is_read_with_each_value(cmd, policy, outcome):
    assert gate.decide(_bash(cmd), policy).outcome == outcome


@pytest.mark.parametrize("cmd", [
    # bash expands no quoted or escaped brace: it refuses `{PATH,X}=` as a name and sets nothing
    "export '{PATH,X}='; ls",
    'export "{PATH,X}"=; ls',
    "export \\{PATH,X}=; ls",
    'export "{"PATH,X}=; ls',
    "export {PATH\\,X}=; ls",
    "export $'{PATH,X}'=; ls",
    "read '{PATH,X}' <<< x; ls",
    # a brace that is no set, and words that only look like the builtin
    "export {}PATH=; ls",
    "echo export {PATH,X}=; ls",
    "git commit -m 'export {PATH,X}=; ls'",
])
def test_a_brace_bash_does_not_expand_stays_read(cmd):
    c = _bash(cmd)
    assert c.category in ("read", "local_write") and gate.decide(c).outcome == "allow", c


@pytest.mark.parametrize("cmd, apart", [
    ("declare 'a[$(rm -rf build)]' {X,Y}", "declare 'a[$(rm -rf build)]' X Y"),
    ('declare "a[$(rm -rf build)]" {X,Y}', 'declare "a[$(rm -rf build)]" X Y'),
    ("declare -i 'A=$(rm -rf build)' {X,Y}", "declare -i 'A=$(rm -rf build)' X Y"),
    ("export GIT_SSH_COMMAND='rm -rf build' {X,Y}=1; git fetch", "export GIT_SSH_COMMAND='rm -rf build' X=1 Y=1; git fetch"),
    ("export LD_PRELOAD=/tmp/x.so {X,Y}=1; ls", "export LD_PRELOAD=/tmp/x.so X=1 Y=1; ls"),
    ("read PATH {X,Y}; ls", "read PATH X Y; ls"),
])
def test_a_word_outside_the_set_is_read_once(cmd, apart):
    """The words bash makes add only the parts the words as written do not give, so a word outside the set does not
    give its parts twice."""
    a, b = _bash(cmd), _bash(apart)
    assert sorted(p.summary for p in gate._each_part(a)) == sorted(p.summary for p in gate._each_part(b)), (a, b)


def test_a_set_of_command_substitutions_is_read_within_the_budget(monkeypatch):
    """Each word a set makes is read again, a command in it too, so a word with a $( ), a backtick or a <( ) takes
    more of the budget. A nest of seven $( ) around a 1,000-word set is read about as often as main reads it (56
    times), not once per word (53,056 times), and asks."""
    calls = []
    real = gate.classify_shell
    monkeypatch.setattr(gate, "classify_shell", lambda cmd, depth=0: calls.append(depth) or real(cmd, depth))
    for cmd, most in [("declare -i 'A=$(" + "x=$(" * 6 + "ls '{1..1000}'" + ")" * 6 + ")'", 80),
                      ("declare -i 'A=$(ls '{1..1000}')'", 10),
                      ("declare -i 'A=`ls '{1..1000}'`'", 10),
                      ("declare -i 'A[$(ls '{1..1000}')]=1'", 10)]:
        calls.clear()
        c = _bash(cmd)
        assert len(calls) <= most, (cmd, len(calls))
        assert gate.decide(c).outcome == "ask", cmd
    calls.clear()
    assert gate.decide(_bash("export {A,B,C}=\"$(git rev-parse HEAD)\"; ls")).outcome == "allow"   # a few still fit
    assert len(calls) <= 10


def test_a_printf_v_in_a_set_changes_the_shell_for_a_rule():
    """A rule that allows a command must match each command before it that changes the shell (#394): `printf -v`
    does, also in a set."""
    policy = {"gates": {"rules": [{"match": {"command": "git push --force origin main"}, "decision": "allow"}]}}
    for cmd in ("printf -v X y; git push --force origin main", "printf {-v,X} y; git push --force origin main"):
        assert gate.decide(_bash(cmd), policy).outcome == "ask", cmd


@pytest.mark.parametrize("prog", ["export", "declare", "read", "printf", "test"])
def test_a_brace_set_past_the_limits_asks(prog):
    """A set past the brace limits is not read in part: the builtin gets an opaque_exec part."""
    for cmd in (f"{prog} " + "{a,b}" * 11 + "=; ls",                      # 2,048 words in one set
                f"{prog} " + " ".join(["{a,b}{c,d}{e,f}{g,h}{i,j}"] * 200) + "; ls"):   # 6,400 in all
        c = _bash(cmd)
        assert gate.decide(c).outcome == "ask", cmd[:60]
        assert any(p.summary == f"{prog} takes a brace set too large to read, which may name PATH"
                   for p in [c, *c.parts]), cmd[:60]
