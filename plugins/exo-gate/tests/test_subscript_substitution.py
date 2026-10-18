"""#179: bash evaluates an array subscript in a variable name as arithmetic, which runs a $( ) or ` ` inside it.

The name arguments of printf -v, read, declare/typeset/local, unset, test -v / [ -v and wait -p are read for
that subscript whatever their quoting. A command in it is classified like an unquoted $( ). Any expansion in it
also asks, since its value (a variable's, or the command's output) such as `b[$(cmd)]` is evaluated too. Every
line is classification data only; nothing here runs it.
"""
import pytest

from exo import gate
from exo.gate import classify, decide

GH = "gh pr comment 3 --body hi"
DENY_CATEGORY = {"gates": {"categories": {"github_post": "deny"}}}
DENY_LIST = {"gates": {"deny": ["github_post"]}}


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


# The issue's rows, the typeset and in-function local forms, and option, prefix and backtick variants. On main
# 35b7de8 each was `read` (allow under all three policies), except the function and `bash -c` forms, which were
# `opaque_exec` (ask, never deny).
POSTS = [
    f"printf -v 'a[$({GH})]' %s x",
    f"read 'a[$({GH})]' <<< x",
    f"declare 'a[$({GH})]=1'",
    f"typeset 'a[$({GH})]=1'",
    f"unset 'a[$({GH})]'",
    f"test -v 'a[$({GH})]'",
    f"[ -v 'a[$({GH})]' ]",
    f"wait -p 'a[$({GH})]'",
    f"f() {{ local 'a[$({GH})]=1'; }}; f",
    f"local 'a[$({GH})]=1'",
    f"read -a 'a[$({GH})]' <<< x",
    f"read -r x 'a[$({GH})]' <<< x",
    f"read -rp 'go: ' 'a[$({GH})]' <<< x",
    f"declare -a 'a[$({GH})]=1'",
    f"wait -np 'a[$({GH})]'",
    f"printf -v'a[$({GH})]' %s x",
    f"unset 'a[b[$({GH})]]'",
    "unset 'a[$(gh pr comment 3 --body \"[x]\")]'",
    f"printf -v 'a[$((1+$({GH})))]' %s x",
    f'printf -v "a[\\$({GH})]" %s x',
    f"builtin printf -v 'a[$({GH})]' %s x",
    f"command unset 'a[$({GH})]'",
    f"if [ -v 'a[$({GH})]' ]; then echo y; fi",
    f"bash -c \"unset 'a[\\$({GH})]'\"",
    f"printf -v 'a[`{GH}`]' %s x",
    f"unset 'a[`{GH}`]'",
    f"declare 'a[`{GH}`]=1'",
    f"test -v 'a[`{GH}`]'",
    f"unset 'a[\"]\"$({GH})]'",
    f"unset 'a[\\]$({GH})]'",
    f"unset \"a[']'\\$({GH})]\"",
    f"unset 'a[\"$({GH})\"]'",
    # A [ in the subscript nests: the ] after b[0] does not end it, and bash runs the command after it.
    f"unset 'a[b[0]+$({GH})]'",
    f"printf -v 'a[b[1]$({GH})]' %s x",
    # At the subscript's top level a \ hides the $ or ` after it, and bash runs the backticks that follow. On
    # 6e44914 the scan read `\${` as the start of a ${ } and asked, or read `\`` as the start of backticks.
    f"unset 'a[\\${{`{GH}`]'",
    f"printf -v 'a[\\${{`{GH}`}}]' %s x",
    f"unset 'a[\\``{GH}`]'",
    # Bash before 5.2 expands a nested subscript again, so a \$ or \` there runs the command (bash 3.2 also for a
    # [ ] in double quotes).
    f"unset 'a[b[\\$({GH})]]'",
    f"printf -v 'a[b[\\`{GH}\\`]]' %s x",
    f"read 'a[1+b[\\$({GH})]]' <<< x",
    f"unset 'a[\"b[\\$({GH})]\"]'",
]


@pytest.mark.parametrize("line", POSTS)
def test_command_in_subscript_is_classified(line):
    c = sh(line)
    assert c.category == "github_post", c
    assert decide(c).outcome == "ask", c
    assert decide(c, DENY_CATEGORY).outcome == "deny", c
    assert decide(c, DENY_LIST).outcome == "deny", c


# A variable, ${ }, $(( )) or quoting in the subscript cannot be read, and neither can a command's output: bash
# evaluates either value as arithmetic, so a value such as `b[$(cmd)]` runs cmd. On main 35b7de8 each was `read`
# (allow under all three policies).
UNREADABLE = [
    "printf -v 'a[$i]' %s x",
    "printf -v 'a[${x}]' %s x",
    "printf -v 'a[$((1+2))]' %s x",
    "unset 'a[$i]'",
    "read 'a[$i]' <<< x",
    "declare 'a[${x}]=1'",
    "test -v 'a[$i]'",
    "wait -p 'a[$i]'",
    'unset "a[$i]"',
    "unset 'a[\"k\"]'",
    "unset 'a[\\i]'",
    "printf -v 'a[$(echo b[\\$\\(gh pr comment 3 --body hi\\)])]' %s x",
    "printf -v 'a[$(printf %s \"b[\\$(gh pr comment 3 --body hi)]\")]' %s x",
    "printf -v 'a[$(cat f.txt)]' %s x",
    'printf -v "a[$(cat f.txt)]" %s x',
    "unset 'a[`cat f.txt`]'",
]


@pytest.mark.parametrize("line", UNREADABLE)
def test_unreadable_subscript_asks(line):
    c = sh(line)
    assert c.category == "read", c
    assert "indirection" in c.signals, c
    for policies in (None, DENY_CATEGORY, DENY_LIST):
        assert decide(c, policies).outcome == "ask", (policies, c)


# The ask is a signal, not a part that outranks the rest of the line: a deny on the line's own category (on main
# 35b7de8 each was denied under all three forms) still wins.
DENY_KEPT = [
    ("local_write", "unset 'a[$i]'; echo x > /tmp/f"),
    ("local_write", 'printf -v "arr[$i]" %s x > /tmp/f'),
    ("local_write", "printf -v 'a[$(cat f.txt)]' %s x > /tmp/f"),
    ("web_read", "unset 'a[$i]'; curl https://example.com"),
]


@pytest.mark.parametrize("category,line", DENY_KEPT)
def test_unreadable_subscript_keeps_a_deny(category, line):
    c = sh(line)
    assert c.category == category, c
    assert "indirection" in c.signals, c
    assert decide(c).outcome == "ask", c
    for policies in denies(category):
        assert decide(c, policies).outcome == "deny", (policies, c)


# Plain names, literal subscripts, and the arguments that are not names keep main's class: each was `read` and
# allow on main 35b7de8. A bare name such as `i` in a subscript is evaluated by bash too; the gate does not model
# variable values (docs/GATE.md, known limits), so these stay as they were.
UNCHANGED = [
    "printf -v 'a[0]' %s x",
    "printf -v 'a[i+1]' %s x",
    "printf -v 'a[key]' %s x",
    "declare 'a[0]=1'",
    "unset 'a[0]'",
    "unset 'a[b[0]]'",
    "[ -v 'a[0]' ]",
    "wait -p 'a[0]'",
    "read line",
    "read -r a b",
    "read -a parts <<< 'a b c'",
    "declare -a arr",
    "declare -A map",
    "unset x",
    "unset -v x y",
    "test -v HOME",
    "[ -v HOME ]",
    "wait -n",
    "wait -p pid",
    "printf -v out %s x",
    "printf '[%s]\\n' x",
    f"printf -v out '%s[$({GH})]' y",
    f"read -p 'a[$({GH})]' v",
    "read -rp 'x[$i]' -a 'b[1]'",
    "test -n 'a[$i]'",
    f"echo 'a[$({GH})]'",
    'declare x="${a[$i]}"',
    'unset "${arr[@]}"',
]


@pytest.mark.parametrize("line", UNCHANGED)
def test_ordinary_names_keep_their_class(line):
    c = sh(line)
    assert c.category == "read", c
    assert decide(c).outcome == "allow", c


# A $( ) in a double-quoted name is read once, by the line's own parse; the subscript scan does not read it again
# (each nested level would double the count otherwise). Compared with the same line read without the scan.
@pytest.mark.parametrize("n", [1, 2, 4, 6])
def test_double_quoted_name_is_read_once(monkeypatch, n):
    seen = []
    real = gate.classify_shell

    def counted(cmd, depth=0):
        seen.append(cmd)
        return real(cmd, depth)

    monkeypatch.setattr(gate, "classify_shell", counted)
    line = 'unset "a[$(' * n + "true" + ')]"' * n
    sh(line)
    with_scan = len(seen)
    seen.clear()
    monkeypatch.setattr(gate, "_NAME_BUILTINS", {})
    sh(line)
    assert with_scan == len(seen)


# Bash reads `\$` at a subscript's top level as a literal $, so `a[\$(cmd)]` runs no command (bash 3.2 to 5.2):
# the scan reads none either, and the \ still asks. On 6e44914 the scan read `$(date)` as a command.
@pytest.mark.parametrize("line", ["unset 'a[\\$(date)]'", "printf -v 'a[\\$(date)]' %s x",
                                  "unset 'a[\"\\$(date)\"]'"])
def test_escaped_dollar_runs_no_command(monkeypatch, line):
    seen = []
    real = gate.classify_shell

    def counted(cmd, depth=0):
        seen.append(cmd)
        return real(cmd, depth)

    monkeypatch.setattr(gate, "classify_shell", counted)
    c = sh(line)
    assert "date" not in seen, seen
    assert c.category == "read" and "indirection" in c.signals, c
    assert decide(c).outcome == "ask", c


# At the depth cap a command in a subscript is not read, and is opaque, as an unquoted $( ) there is.
@pytest.mark.parametrize("line", [f"unset 'a[$({GH})]'", "printf -v 'a[`true`]' %s x"])
def test_subscript_at_depth_cap_is_opaque(line):
    c = gate.classify_shell(line, gate._DEPTH)
    assert c.category == "opaque_exec", c
    assert decide(c, {"gates": {"categories": {"opaque_exec": "deny"}}}).outcome == "deny", c
