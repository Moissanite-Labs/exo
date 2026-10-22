"""Issue #329: `unset PATH` makes later commands on the line run from the working folder.

With PATH unset, bash looks a command up in the working folder, so `ls` runs `./ls`, which an
earlier local write can plant. #231 already asks on the assignment forms (`PATH= ls`,
`PATH=$PATH: ls`); `unset` of PATH amounts to the same assignment and is read the same way,
`opaque_exec` named in the summary. `unset -f` names functions, not variables, and stays read.
A word the gate cannot read, a name or an option, may spell PATH or hide the options, so it
asks too. Each line in the asks lists, but `unset -n PATH` and the side effects, was checked in
bash 5.2 to run a planted `./ls`, with its variables set to fit (`V=PATH`, `x=' PATH'`).

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
    # read/allow on main 18b229b3
    "unset PATH; ls",
    "unset -v PATH; rg x",
    "declare +x PATH; unset PATH; ls",
    "unset PATH",
    "unset -- PATH; ls",
    "unset PATH HOME; ls",
    "unset -v PATH FOO; rg x",
    "unset 'PATH'; ls",
    # -n asks, to be safe. bash leaves a plain PATH set under -n, but it unsets a PATH that an
    # earlier command made a nameref (`declare -n PATH=p2; unset -n PATH; ls` runs ./ls), and the
    # gate does not track which names are namerefs.
    "unset -n PATH; ls",
    "unset PATH && ls",
    "builtin unset PATH; ls",
    "command unset PATH; ls",
    # a `+` word, `--` or a name ends unset's options, so a later -f is a name
    "unset +f -f PATH; ls",
    "unset -- -f PATH; ls",
    "unset PATH -f; ls",
    'unset "" -f PATH; ls',
])
def test_unset_path_asks_as_the_empty_assignment_does(cmd):
    c = _bash(cmd)
    assert c.category == "opaque_exec" and "PATH" in c.summary, c
    assert gate.decide(c).outcome == "ask", c


@pytest.mark.parametrize("cmd", [
    # a brace: bash expands it before unset sees the name
    "unset {PATH,}; ls",
    "unset P{A,}TH && ls",
    "unset {PATH,FOO}; ls",
    "unset {X,PATH}; ls",
    "unset PAT{H..H}; ls",
    "unset -v {PATH,X}; ls",
    'unset {"}",PATH}; ls',
    # a name from a variable, a substitution or a glob
    "V=PATH; unset $V; ls",
    "for v in PATH; do unset $v; done; ls",
    'unset "$V"; ls',
    'read -r V <<< PATH; unset "$V"; ls',
    "unset $(echo PATH); ls",
    "unset `echo PATH`; ls",
    "unset ${!X}; ls",
    "unset ${!PA*}; ls",
    'unset "${arr[@]}"; ls',  # arr=(PATH)
    "unset PAT?; ls",
    "unset [P]ATH; ls",       # a leading [ is a glob the lexer does not mark: a file named PATH matches it
    "unset \\\n[P]ATH; ls",   # bash drops the \<newline> first, so the [ still leads the word
    "unset -v \\\n[P]ATH; ls",
    "unset FOO \\\n[P]ATH; ls",
    "unset \\\n[P]ATH; git status",
    "unset FOO=$x; ls",       # unset takes no assignment: x=' PATH' splits into a second name
    'unset "a["$i]; ls',      # the $ is outside the quotes: i='1] PATH [' splits off PATH
    # an option word the gate cannot read: bash may see -v, `-`, or more words
    "unset -${f}v PATH; ls",
    "unset -$(printf v) PATH; ls",
    "unset -${foo:-v} PATH; ls",
    "unset -[fv] PATH; ls",
    "x=; unset -$x -f PATH; ls",
    "unset -{,} -f PATH; ls",
])
def test_unset_with_a_word_the_gate_cannot_read_asks(cmd):
    c = _bash(cmd)
    assert c.category == "opaque_exec" and "PATH" in c.summary, c
    assert gate.decide(c).outcome == "ask", c


@pytest.mark.parametrize("cmd", [
    # side effects the reviews named: a loop over other names asks as `export $v=` does, and an
    # unquoted subscript is a glob (quoted, below, it stays read)
    "for v in A B; do unset $v; done; ls",
    "unset arr[1]; ls",
    "unset PATH[0]; ls",
])
def test_unset_of_a_name_bash_may_expand_asks(cmd):
    c = _bash(cmd)
    assert (c.category, gate.decide(c).outcome) == ("opaque_exec", "ask"), c


@pytest.mark.parametrize("cmd", [
    # read/allow on main bd503a5d: the exporter loop took letters from words it cannot read
    # and `+x` as an option, which export and readonly read as a name
    "V=PATH; export +x -f $V=; ls",
    "V=PATH; readonly +x -f $V=; ls",
    "V=PATH; declare -${f}x $V=; ls",
    "V=PATH; local -${f}x $V=; ls",
    "declare -$x r=PATH; ls",
])
def test_an_exporter_option_the_gate_cannot_read_asks(cmd):
    c = _bash(cmd)
    assert (c.category, gate.decide(c).outcome) == ("opaque_exec", "ask"), c


@pytest.mark.parametrize("cmd", [
    "unset FOO; ls",          # an unlisted variable changes nothing the gate relies on
    "unset PATHX; ls",        # names that merely contain PATH are not PATH
    "unset XPATH; ls",
    "unset -f PATH; ls",      # -f unsets a function named PATH, not the variable
    "unset -fv PATH; ls",     # -f with -v is an error: nothing is unset
    "unset -f -v PATH; ls",
    "unset -f -- PATH; ls",
    "unset -f $fn; ls",       # after a literal -f every name is a function
    "unset -f -$x PATH; ls",
    "unset -f {,-v} PATH; ls",
    "unset -f fn; ls",
    "unset 'arr[1]'; ls",
    "unset \\\n'[P]ATH'; ls",  # a quoted [ is no glob, after a \<newline> too
    "unset PYTHONPATH; pytest",
    "echo unset PATH",        # words, not the builtin
    "git commit -m 'unset PATH; ls'",
    "cat <<'EOF'\nunset PATH; ls\nEOF",
    "export -f fn; ls",
    "declare -p PATH; ls",
    "declare +x FOO; ls",
    "local -a arr; ls",
    "export -n FOO; ls",
    "ls",
])
def test_other_unsets_stay_as_they_were(cmd):
    c = _bash(cmd)
    assert c.category in ("read", "local_write") and gate.decide(c).outcome == "allow", c


@pytest.mark.parametrize("cmd", [
    # a word wholly in double quotes is not split, and its name is read before the `[`
    'unset "a[$i]"; ls',
    'unset "arr[$key]"; ls',
    'unset -v "a[$i]" FOO; ls',
    'unset "FOO=$x"; ls',
])
def test_a_subscript_in_double_quotes_is_read_as_before(cmd):
    c = _bash(cmd)
    assert c.category == "read" and "PATH" not in c.summary, c


def test_mixed_category_policies_decide_unset_as_the_empty_assignment():
    """The line decides by its most severe part, opaque_exec, so a policy that allows opaque_exec
    but asks on local_write allows it. `PATH=; echo hi > out.txt` already did on main (D-038)."""
    pol = {"gates": {"categories": {"opaque_exec": "allow", "local_write": "ask"}}}
    for cmd in ("unset PATH; echo hi > out.txt", "PATH=; echo hi > out.txt"):
        assert gate.decide(_bash(cmd), pol).outcome == "allow", cmd
    # a deny on a part still holds
    for pol in ({"gates": {"deny": ["delete"]}},
                {"gates": {"categories": {"opaque_exec": "allow", "delete": "deny"}}}):
        assert gate.decide(_bash("unset PATH; rm -rf build"), pol).outcome == "deny", pol
    assert gate.decide(_bash("unset PATH; git push --force origin main"),
                       {"gates": {"deny": ["force_push"]}}).outcome == "deny"


def test_an_opaque_exec_allow_rule_allows_an_unquoted_subscript_as_export_does():
    """`unset a[$i]` asked on main for its subscript, as a read line. It is opaque_exec now, so an
    allow rule that names opaque_exec allows it, as it allows `export a[$i]=1` on main (D-038). So
    does a line that asked on main only for another word's indirection, once unset PATH makes it
    opaque_exec: `PATH=; unset "arr[$k]"` was allowed on main too."""
    pol = {"gates": {"rules": [{"match": {"command": ".", "category": "opaque_exec"},
                                "decision": "allow", "reason": "r"}]}}
    for cmd in ("unset a[$i]", "export a[$i]=1", "read a[$i]", 'unset "arr[$k]" PATH', 'unset $"PATH"',
                'PATH=; unset "arr[$k]"'):
        c = _bash(cmd)
        assert (c.category, gate.decide(c).outcome, gate.decide(c, pol).outcome) == ("opaque_exec", "ask", "allow"), cmd
    c = _bash('unset "a[$i]"')       # wholly in double quotes: read, and the subscript still asks
    assert (c.category, gate.decide(c, pol).outcome) == ("read", "ask"), c


def test_a_locale_word_beside_unset_path_decides_as_beside_the_empty_assignment():
    """A line with a $".." word that is not opaque_exec is also decided as opaque_exec with no own
    text (#136), so an allow rule cannot lower it, and `categories: {opaque_exec: deny}` denies it.
    A line that is opaque_exec already is decided once, and the allow rule lowers it to the opaque
    floor. unset PATH makes the line opaque_exec, so it asks there, as `export $"PATH"=; ls` and
    `PATH=; echo $"x"; ls` do on main (D-038). A deny entry for opaque_exec still denies it."""
    pol = {"gates": {"categories": {"opaque_exec": "deny"},
                     "rules": [{"match": {"command": "."}, "decision": "allow", "reason": "r"}]}}
    assert gate.decide(_bash('echo $"x"; ls'), pol).outcome == "deny"
    for cmd in ('unset $"PATH"; ls', 'unset PATH; echo $"x"; ls', 'export $"PATH"=; ls', 'PATH=; echo $"x"; ls'):
        assert gate.decide(_bash(cmd), pol).outcome == "ask", cmd
    assert gate.decide(_bash('unset $"PATH"; ls'), {"gates": {"deny": ["opaque_exec"]}}).outcome == "deny"


def test_readonly_reads_a_plus_word_and_the_words_after_it_as_names():
    """readonly takes no `+x`. bash reads it, and a `-n` after it, as names, so `r=PATH` makes a
    plain variable, not a nameref. Main took the `-n` as an option and asked. These are read now
    (D-038). declare takes `+x`, so its `-n` is still an option and makes a nameref to PATH."""
    for cmd in ("readonly +x -n r=PATH; ls", "readonly +x -n r; ls"):
        c = _bash(cmd)
        assert (c.category, gate.decide(c).outcome) == ("read", "allow"), c
    c = _bash("declare +x -n r=PATH; ls")
    assert (c.category, gate.decide(c).outcome) == ("opaque_exec", "ask"), c
