"""#188: declare, typeset, local, readonly and export evaluate the value of a NAME=VALUE operand again.

A compound value `(..)` is parsed again as words even when quoted (with -a or -A), so a $( ), ` ` or <( ) in it
runs; a `[key]=` in it is an arithmetic subscript. With -i the value is arithmetic, and with -n it is a name whose
subscript bash evaluates when the nameref is used. Each command there is classified like an unquoted $( ) and also
asks, and what bash evaluates again that the gate cannot read asks. Every line is classification data only; nothing
here runs it.
"""
import pytest

from exo import gate
from exo.gate import classify, decide

GH = "gh pr comment 3 --body hi"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(cmd):
    return classify(("bash", {"command": cmd}))


def denies(category):
    return [{"gates": {"categories": {category: "deny"}}}, {"gates": {"deny": [category]}},
            {"gates": {"rules": [{"match": {"category": category}, "decision": "deny", "reason": "no"}]}}]


def named_denies(category):
    """A deny on category in a policy that also names opaque_exec, which turns off the `indirection` ask (D-038)."""
    return [{"gates": {"categories": {"opaque_exec": act, category: "deny"}}} for act in ("deny", "ask", "allow")] + [
        {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "allow", "reason": "ok"}],
                   "deny": [category]}}]


# The issue's rows, then the typeset, local, readonly, -A, -ai, combined-option, `--`, `+=` and backtick variants.
# On ba4b616 (#189) each was `read` (allow under all three policies), except the function forms, which were
# `opaque_exec` (ask, never deny).
POSTS = [
    f"declare -a 'b=($({GH}))'",
    f"declare -a 'a=([$({GH})]=1)'",
    f"typeset -a 'b=($({GH}))'",
    f"declare -i x='a[$({GH})]'",
    f"declare -n r='a[$({GH})]'; echo $r",
    f"f() {{ local -a 'b=($({GH}))'; }}; f",
    f"local -a 'b=($({GH}))'",
    f"f() {{ local -n r='a[$({GH})]'; echo $r; }}; f",
    f"typeset -i 'x=a[$({GH})]'",
    f"declare -A 'm=([$({GH})]=v)'",
    f"declare -A 'm=([k]=$({GH}))'",
    f"declare -ai 'b=(a[$({GH})])'",
    f"declare -ia 'b=([$({GH})]=1)'",
    f"declare -r -a 'b=($({GH}))'",
    f"declare -a -- 'b=($({GH}))'",
    f"declare -a 'b+=($({GH}))'",
    f"declare -i 'x+=a[$({GH})]'",
    f"declare -a b='($({GH}))'",
    f'declare -a "b=(\\$({GH}))"',
    f"declare -a 'b=(x \"$({GH})\" y)'",
    f"declare 'b=($({GH}))'",
    f"declare 'a=([$({GH})]=1)'",
    f"readonly -a 'b=($({GH}))'",
    f"readonly -a 'b=([$({GH})]=1)'",
    f"builtin declare -a 'b=($({GH}))'",
    f"declare -a 'b=(`{GH}`)'",
    f"declare -a 'a=([`{GH}`]=1)'",
    f"declare -i x='a[`{GH}`]'",
    f"declare -n r='a[`{GH}`]'",
    f"local -ai 'b=(1 `{GH}`)'",
    f"export -a 'b=($({GH}))'",
    f"export -A 'm=([$({GH})]=1)'",
    f"export -a 'a=([$({GH})]=1)'",
    f'export -a "b=(\\$({GH}))"',
    f"declare -a 'b=(<({GH}))'",
    f"f() {{ local -a 'b=(>({GH}))'; }}; f",
    f'declare -a "b=(<({GH}))"',
    f"typeset -a 'b=(x <({GH}))'",
    f"declare -a 'a=([<({GH})]=1)'",
    f"declare -a 'a=([0]=<({GH}))'",
    f"declare -a b=() 'c=($({GH}))'",
    f"declare -a b=(1) x 'c=([$({GH})]=1)'",
    # Bash drops a \newline before it parses the value again; and an operand after a second unquoted compound
    # belongs to the same declare. Before the review fix these were `read` and `opaque_exec`.
    f"declare -a 'b=($\\\n({GH}))'",
    f"declare -a $'b=($\\\\\\n({GH}))'",
    f"declare -a b=(1) x=(2) 'c=($({GH}))'",
    f"declare -a b=() x=() 'c=($({GH}))'",
    # An escaped backslash before the newline stays a backslash, so `$(` after it still runs.
    f"declare -i x='a[\\\\\n$({GH})]'",
    f"declare -n r='a[\\\\\n$({GH})]'; echo $r",
    f"typeset -i 'x=a[\\\\\n$({GH})]'",
    f"declare -i x='a[\\\\\n`{GH}`]'",
    # In a compound value bash reads `\\` as a backslash and the newline as a word break, so `$(` after it runs
    # (UNREADABLE has the `[key]=` form of the same rule).
    f"declare -a 'b=(\\\\\n$({GH}))'",
    # A declare in the command an option runs (#154) is read the same way.
    f"ssh -o \"ProxyCommand=declare -a 'b=($({GH}))'\" host",
    f"ssh -o 'ProxyCommand=declare -a \"b=(\\$({GH}))\"' host",
    f"git rebase -x \"declare -i x='a[\\$({GH})]'\" main",
]


@pytest.mark.parametrize("line", POSTS)
def test_command_in_value_is_classified(line):
    c = sh(line)
    assert c.category == "github_post", c
    assert decide(c).outcome == "ask", c
    for policies in denies("github_post"):
        assert decide(c, policies).outcome == "deny", (policies, c)


# Bash evaluates these values again: an expansion or quoting in a compound key, any expansion in a -i or -n value or
# in a -i element, a compound value the line itself expanded (declare parses the expansion's text again as code), and
# a command in a quoted compound value (one the line's own parse did not read). On ba4b616 each was `read` (allow
# under all three policies).
UNREADABLE = [
    "declare -i x='$x'",
    "declare -i 'x=a[$i]'",
    "local -i n=$1",
    "declare -a 'a=([$i]=1)'",
    "declare -A 'm=([$k]=v)'",
    "declare -a 'a=([\"k\"]=1)'",
    "declare -ai 'b=($x)'",
    "declare -ai 'b=($(cat f.txt))'",
    'declare -a "b=($x)"',
    'declare -a "b=($(cat f.txt))"',
    "declare -n r='a[$i]'",
    "declare -a 'a=([`cat f.txt`]=1)'",
    "local -n ref=$1",
    'local -n out="$2"',
    "declare -a 'b=($(date))'",
    "export -a 'b=(<(cat f.txt))'",
    # bash keeps a backslash-newline in an -i or -n value (only a compound value is read again as words)
    "declare -i x='a[\\\n1]'",
    "declare -n r='a\\\nb'",
    # `\\` is a backslash and the newline a word break, so `[$i]=` starts an element and its key is arithmetic
    "declare -a 'a=(\\\\\n[$i]=1)'",
]


@pytest.mark.parametrize("line", UNREADABLE)
def test_unreadable_value_asks(line):
    c = sh(line)
    assert c.category == "read", c
    assert "indirection" in c.signals, c
    for policies in [None] + denies("github_post"):
        assert decide(c, policies).outcome == "ask", (policies, c)


# The ask is a signal, not a part that outranks the rest of the line: a deny on the line's own category (on
# ba4b616 each was denied under all three forms) still wins.
DENY_KEPT = [
    ("local_write", "declare -i x='$x'; echo x > /tmp/f"),
    ("local_write", "declare -a 'a=([$i]=1)' > /tmp/f"),
    ("web_read", "declare -n r='a[$i]'; curl https://example.com"),
]


@pytest.mark.parametrize("category,line", DENY_KEPT)
def test_unreadable_value_keeps_a_deny(category, line):
    c = sh(line)
    assert c.category == category, c
    assert "indirection" in c.signals, c
    assert decide(c).outcome == "ask", c
    for policies in denies(category):
        assert decide(c, policies).outcome == "deny", (policies, c)


# A command in a quoted compound value ranks after the line's own parts, as the same value unquoted does, so it
# does not take the place of a redirection or a later command of equal severity: a deny on the line's own category
# still wins, and the line asks otherwise.
VALUE_AFTER_LINE = [
    ("local_write", "declare -a 'b=($(curl https://example.com))' > /tmp/f"),
    ("local_write", "declare -a 'b=($(curl https://example.com))'; echo x > /tmp/f"),
    ("local_write", "echo x > /tmp/f; local -a 'b=(`curl https://example.com`)'"),
    ("local_write", "declare -A 'm=([k]=\"$(curl https://example.com)\")' >> /tmp/f"),
]


@pytest.mark.parametrize("category,line", VALUE_AFTER_LINE)
def test_value_command_keeps_the_line_deny(category, line):
    c = sh(line)
    assert c.category == category, c
    assert decide(c).outcome == "ask", c
    for policies in denies(category):
        assert decide(c, policies).outcome == "deny", (policies, c)


# A command bash runs only as it reads a quoted value (or subscript) again asks through `indirection` and `reread`,
# so when it outranks the line's own category the line asks under a deny on that category (on ba4b616 each value
# form was `local_write`, denied): deny becomes ask, never allow or log, also when the policy names opaque_exec
# (which lowers only the `indirection` ask). A deny on the command's own category still denies. Since D-075 (#126)
# a `deny:` list entry matches the line's own `local_write` part and denies; since #191 a category line or a rule
# that denies that part denies the line too, so only the policy with no deny asks.
OUTRANKS = [
    ("read", "declare -a 'b=($(sudo ls))' > /tmp/f"),
    ("read", "declare -a 'b=($(ssh host ls))' > /tmp/f"),
    ("vcs_push", "declare -a 'b=($(git push origin main))' > /tmp/f"),
    ("vcs_push", "local -a 'b=(`git push origin main`)'; echo y > /tmp/f"),
    ("read", "export -a 'b=(<(sudo ls))' >> /tmp/f"),
    # #189's quoted subscripts: on ba4b616 these asked by default but were allowed under a policy naming opaque_exec
    ("read", "printf -v 'a[$(sudo ls)]' %s x > /tmp/f"),
    ("vcs_push", "printf -v 'a[$(git push origin main)]' %s x > /tmp/f"),
    ("read", "declare 'a[$(ssh host ls)]=1' > /tmp/f"),
]


@pytest.mark.parametrize("body,line", OUTRANKS)
def test_value_command_outranking_a_deny_asks(body, line):
    c = sh(line)
    assert c.category != "local_write", c
    assert "indirection" in c.signals and "reread" in c.signals, c
    assert decide(c, None).outcome == "ask", c
    for policies in denies("local_write") + named_denies("local_write"):
        assert decide(c, policies).outcome == "deny", (policies, c)
    if body != "read":
        for policies in denies(body) + named_denies(body):
            assert decide(c, policies).outcome == "deny", (policies, c)


# A command nested deeper than the gate reads is never dropped: at the depth limit it is an opaque part (ask), as a
# $( ) nested as deep on the line itself is, and it carries `reread`, so a policy that names opaque_exec still asks
# (on ba4b616 the printf form was `read` with `indirection`: deny under a deny on read, ask under one naming
# opaque_exec). On ba4b616 the value forms were `read` (allow under every policy).
def _nest(n, wrap):
    line = GH
    for _ in range(n):
        line = wrap(line.replace("'", "'\"'\"'"))
    return line


@pytest.mark.parametrize("wrap", [lambda s: "declare -a 'b=($(" + s + "))'", lambda s: "local -i 'x=a[$(" + s + ")]'",
                                  lambda s: "printf -v 'a[$(" + s + ")]' %s x"])
def test_nested_too_deep_asks(wrap):
    c = sh(_nest(4, wrap))
    assert c.category == "github_post", c
    for policies in denies("github_post"):
        assert decide(c, policies).outcome == "deny", (policies, c)
    c = sh(_nest(5, wrap))
    assert c.category == "opaque_exec", c
    for policies in [None] + denies("github_post"):
        assert decide(c, policies).outcome == "ask", (policies, c)
    for policies in named_denies("github_post") + named_denies("read"):   # naming opaque_exec does not lower it
        assert decide(c, policies).outcome in ("ask", "deny"), (policies, c)


# Values bash does not evaluate again keep ba4b616's class (`read`, allow): plain and quoted scalar values without
# -i or -n, a plain expansion in a compound value the line did not expand, `+i` (which clears -i), a literal key, a
# literal -n name, export's and readonly's scalar values and -n (which removes the attribute), and a subscripted
# name given to readonly or export (`not a valid identifier`: bash evaluates nothing).
UNCHANGED = [
    "declare -i n=5",
    "declare -i n=0",
    "local x=1",
    "local -i count",
    "declare -r X=1",
    "readonly X=1",
    'export PATH="$HOME/bin:$PATH"',
    "export X=1",
    "export -n FOO",
    f"export -n 'x=a[$({GH})]'",
    "readonly 'a[$i]'",
    f"readonly 'a[$({GH})]=1'",
    "declare x='(not code)'",
    "declare -p x",
    "declare -a 'b=(1 2)'",
    "declare -A 'm=([k]=v)'",
    "declare -a 'b=(\"$@\")'",
    f"declare +i x='a[$({GH})]'",
    f"declare x='a[$({GH})]'",
    'local name="$1"',
    "declare -n ref=arr",
]


@pytest.mark.parametrize("line", UNCHANGED)
def test_ordinary_values_keep_their_class(line):
    c = sh(line)
    assert c.category == "read", c
    assert decide(c).outcome == "allow", c


# An unquoted compound value is split by the line's own parse, which reads its $( ) once; the value scan adds
# nothing, so each keeps ba4b616's class (`declare -a b=(1 2)` already asks there: the parse reads `1` as a program).
BASE_KEPT = [
    ("opaque_exec", "ask", "declare -a b=(1 2)"),
    ("opaque_exec", "ask", "declare -A m=([k]=v)"),
    ("opaque_exec", "ask", 'local -a args=("$@")'),
    ("opaque_exec", "ask", "declare -a files=($(ls))"),
    ("github_post", "ask", f"declare -a b=($({GH}))"),
]


@pytest.mark.parametrize("category,outcome,line", BASE_KEPT)
def test_unquoted_compound_keeps_its_class(category, outcome, line):
    c = sh(line)
    assert c.category == category, c
    assert decide(c).outcome == outcome, c


# A $( ) in a double-quoted compound value is read once, by the line's own parse; the value scan does not read it
# again. Compared with the same line read without the scan.
@pytest.mark.parametrize("line", [
    *['declare -a "b=(' + "$(" * n + "true" + ")" * n + ')"' for n in (1, 2, 4, 6)],
    *['declare -ai "b=([$(' * n + "true" + ")]=1" * n + ')"' for n in (1, 3)],
    "declare -a b=($(echo $(true)))",
    # the value scan drops the \newline the line's own parse kept in the body; it is still the same $( )
    'declare -a "b=($(ls \\\n -l))"',
    'declare -ai "b=([$(ls \\\n -l)]=1)"',
])
def test_double_quoted_compound_is_read_once(monkeypatch, line):
    assert reads(monkeypatch, line)[0] == 0


def reads(monkeypatch, line):
    """(bodies classified only because of the value scan, the line's category with the scan)."""
    seen = []
    real = gate.classify_shell

    def counted(cmd, depth=0):
        seen.append(cmd)
        return real(cmd, depth)

    with monkeypatch.context() as m:   # undone on return, so a second call measures with the scan again
        m.setattr(gate, "classify_shell", counted)
        category = sh(line).category
        with_scan = len(seen)
        seen.clear()
        m.setattr(gate, "_NAME_BUILTINS", {})
        sh(line)
    return with_scan - len(seen), category


# The line's own $( ) keeps a \newline in single quotes, but bash dropped it from the double-quoted value it reads
# again, so an escaped copy of that body runs another command there: it is read, not taken for the line's own.
REREAD_DIFFERS = [
    ("delete", "declare -a \"b=(\\$(find . '\\\n-delete') $(find . '\\\n-delete'))\""),
    ("github_post", f"declare -a \"b=(\\$(find . '\\\n-exec' {GH} ';') $(find . '\\\n-exec' {GH} ';'))\""),
    ("github_post", "declare -a \"b=(\\$(gh 'pr\\\n' comment 3 --body hi) $(gh 'pr\\\n' comment 3 --body hi))\""),
]


@pytest.mark.parametrize("category,line", REREAD_DIFFERS)
def test_reread_body_unlike_the_lines_own_is_read(category, line):
    c = sh(line)
    assert c.category == category and "reread" in c.signals, c
    assert decide(c, {"gates": {"categories": {"opaque_exec": "allow"}}}).outcome == "ask", c
    for policies in denies(category):
        assert decide(c, policies).outcome == "deny", (policies, c)


# A body that holds a single quote or a here-document marker (`<<`, also as `<<<`) anywhere is compared as written,
# not with its \newlines dropped: bash keeps a \newline in single quotes or a quoted here-document, and the gate
# does not track where. So with a backslash-newline such a body is read again and asks, even where bash runs the
# same command twice (only the gate is stricter); the same body without the marker is read once.
REREAD_AS_WRITTEN = [
    ('declare -a "b=($(ls \'x\' \\\n -l))"', 'declare -a "b=($(ls \\\n -l))"'),
    ('declare -a "b=($(cat <<<x \\\n -l))"', 'declare -a "b=($(cat x \\\n -l))"'),
]


@pytest.mark.parametrize("marked,plain", REREAD_AS_WRITTEN)
def test_body_with_a_quote_or_heredoc_marker_is_read_again(monkeypatch, marked, plain):
    opaque_allowed = {"gates": {"categories": {"opaque_exec": "allow"}}}
    c, p = sh(marked), sh(plain)
    assert "reread" in c.signals and decide(c, opaque_allowed).outcome == "ask", c
    assert "reread" not in p.signals and decide(p, opaque_allowed).outcome == "allow", p
    assert reads(monkeypatch, marked) == (1, "read")
    assert reads(monkeypatch, plain) == (0, "read")


def test_many_values_are_compared_in_linear_time(monkeypatch):
    """The line's bodies are made comparable once per command, not once per name operand."""
    calls, gate_re = [], gate.re

    class Counted:
        def __getattr__(self, name):
            return getattr(gate_re, name)

        def sub(self, *a, **k):
            calls.append(a[0])
            return gate_re.sub(*a, **k)

        def search(self, *a, **k):
            calls.append(a[0])
            return gate_re.search(*a, **k)

    monkeypatch.setattr(gate, "re", Counted())
    counts = []
    for n in (40, 80):
        calls.clear()
        sh("declare -a " + " ".join(f'"b{i}=($(ls{i} \\\n -l))"' for i in range(n)))
        counts.append(len(calls))
    assert counts[1] < 2.5 * counts[0], counts
