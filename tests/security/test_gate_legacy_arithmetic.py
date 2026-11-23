"""#588: bash's legacy arithmetic `$[expr]` is read as the `$((expr))` it stands for.

bash 5.2 still accepts `$[..]` (deprecated, not removed), and it evaluates as `$((..))` does, assignments
included: `$((PATH=5))` sets PATH, and so does `$[PATH=5]`. The lexer read `$[..]` as plain text, so the
assignment went unseen: `echo $[PATH=5]; ls` was an allowed `read` where the same line written with `$((..))`
asked as `opaque_exec` (#341/#409 area), in and out of double quotes. A `$( )` nested in it was read already
(`echo $[$(git push --force origin main)]` denies under `deny: [force_push]`). The lexer now reads `$[..]` as
the `$((..))` it stands for, so the assignment check, the nested `$( )` and the quoting of that reading all
apply; the word keeps `$[..]` as written. An element the body sets (`BASH_CMDS[ls]=1` rebinds a program name)
is read too, also through `$((..))`, and `${x:-$[PATH=5]}` reads the body through the expansion word it is in,
as its `$((..))` spelling does. The #584 join of `$\\<newline>[..]` lands on this reading.

Every line here is classification data only; nothing runs it. The bash readings were checked with a probe
whose every command word is printf or `:` (bash 5.3.20): `$[a[1]+1]` is 7, `$[(1+2)]` is 3 and
`$[BASH_CMDS[ls]=1]; ls` runs the file `1` for `ls`, while `$["a]b"+1]` is an arithmetic error, so no quote
guards the `]`, and `'$[PATH=5]'` stays text.
"""
import pytest

from exo import gate

PUSH = "git push --force origin main"
POLICIES = [None,
            gate.normalize_policies({"gates": {"deny": ["force_push"]}}),
            gate.normalize_policies({"gates": {"categories": {"force_push": "deny"}}})]
PUSH_DENIED = ["ask", "deny", "deny"]
ASKS = ["ask"] * 3
ALLOWED = ["allow"] * 3


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(line):
    return gate.classify({"tool": "bash", "args": {"command": line}})


def outcomes(line):
    """The line's outcome under the default policy, `deny: [force_push]` and `categories: {force_push: deny}`."""
    c = sh(line)
    return [gate.decide(c, p).outcome for p in POLICIES]


def asks_naming(line, name):
    c = sh(line)
    assert c.category == "opaque_exec" and "indirection" in c.signals, c
    assert c.summary.startswith(name + " "), c
    assert outcomes(line) == ASKS


# The issue's rows, and the ones beside them: an assignment through `$[..]` decides as the same assignment
# through `$((..))` does. Main read every one of the `$[..]` rows and allowed it.
FIXED = [
    ("echo $[PATH=5]; ls", "PATH"),
    ('echo "$[PATH=5]"; ls', "PATH"),
    ("echo $[BASH_ENV=5]; ls", "BASH_ENV"),
    ("echo $[a[PATH=5]]; ls", "PATH"),                  # the subscript is arithmetic, and it assigns
    ("echo $[BASH_CMDS[ls]=1]; ls", "BASH_CMDS"),       # an element the body sets rebinds a program name
    ("echo ${a:-$[PATH=5]}; ls", "PATH"),               # read through the ${..} word it is in, as $((..)) is
]


@pytest.mark.parametrize("line,name", FIXED)
def test_an_assignment_in_legacy_arithmetic_asks(line, name):
    asks_naming(line, name)


def test_a_nested_substitution_keeps_its_reading():
    c = sh(f"echo $[$({PUSH})]")
    assert c.category == "force_push"
    assert outcomes(f"echo $[$({PUSH})]") == PUSH_DENIED


# Each `$[..]` line decides as the same line written with `$((..))`, which the gate read before (#341/#409).
SAME_AS_ARITH = [
    ("echo $[PATH=5]; ls", "echo $((PATH=5)); ls"),
    ('echo "$[PATH=5]"; ls', 'echo "$((PATH=5))"; ls'),
    ("echo $[BASH_ENV=5]; ls", "echo $((BASH_ENV=5)); ls"),
    ("echo $[a[PATH=5]]; ls", "echo $((a[PATH=5])); ls"),
    ("echo $[BASH_CMDS[ls]=1]; ls", "echo $((BASH_CMDS[ls]=1)); ls"),
    ("echo ${a:-$[PATH=5]}; ls", "echo ${a:-$((PATH=5))}; ls"),
]


@pytest.mark.parametrize("line,arith", SAME_AS_ARITH)
def test_legacy_arithmetic_decides_as_the_dollar_paren_paren_line(line, arith):
    assert outcomes(line) == outcomes(arith)


# Lines that stay plain reads: bash expands `$[..]` there, but it assigns nothing the gate reads.
GUARDS = ["echo $[1+2]", 'echo "$[1+2]"', "echo '$[PATH=5]'; ls", "cat file$[i+1].txt"]


@pytest.mark.parametrize("line", GUARDS)
def test_legacy_arithmetic_that_assigns_nothing_stays_a_read(line):
    c = sh(line)
    assert c.category == "read"
    assert outcomes(line) == ALLOWED


# Main read a `$[..]` in a write's target as text and made its `local_write` of the path as written; the dynamic
# reading stands beside it, so a deny on the write still reaches the line.
def test_a_legacy_form_in_a_write_target_keeps_the_write_as_written_beside():
    line = "echo x > ~/.exo/ke$[0]ys/seat"
    c = sh(line)
    assert any(p.category == "local_write" and p.target == "~/.exo/ke$[0]ys/seat" for p in c.parts)
    assert any(p.category == "opaque_exec" for p in c.parts)
    assert gate.decide(c, gate.normalize_policies({"gates": {"deny": ["local_write"]}})).outcome == "deny"
