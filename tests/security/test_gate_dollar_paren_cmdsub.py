"""#553: a `$((` bash reads as a command substitution is read as one.

bash reads `$((` as arithmetic only when the `$( ` closes at a `))` and the text between the parens, quoted
spans skipped, holds balanced parens (chk_arithsub); else it re-reads the span as `$( ( )`, a subshell, and
the commands in it run. The gate matched `$((` against `))` without counting quotes, so a `((` in a quoted
word made it read the whole span as arithmetic text and it never read the commands: `echo $((echo '((' ;
git push --force origin main) )` was read and allowed under `deny: [force_push]` where the same line written
`echo $( (echo '((' ; git push --force origin main) )` denied. The gate now finds the `))` bash's own scan
finds (quotes, backquotes, `$( )` bodies and comments counted) and applies bash's chk_arithsub to what is
between the parens; when either fails the span is read as a `$( ( )`, which classifies the commands in it.
A quoted paren in the body moves the span too: `$(( 1 + '(' ))` left the rest of the line unread, and what
follows an arithmetic error is discarded on bash's line but runs on the next line and in other shells, so
the span is read only as far as bash extracts it. An unclosed `$((` bash cannot parse at all and runs
nothing, and the span stays arithmetic text to the end of the line, as before. Plain arithmetic decides as
it did: `$((1+2))`, `$((PATH=5))`, a ternary, a `$( )` or a backquote inside the body.

Every line here is classification data only; nothing runs it. The bash readings were checked with a probe
whose every command word is printf or `:` (bash 5.3.20): `$((echo '((' ; printf hi) )` prints `( ( hi`,
`$((printf hi; (printf lo) ))` is an arithmetic error that runs nothing, `$(( 1 + '2' ))` and `$((printf
hi))` are arithmetic errors, `$(( 1 + "2" ))` is 3, `$(( (printf hi) ))` is an arithmetic error, and
`$(( $(printf 2) + 1 ))` is 3. zsh parse-errors the whole script where bash discards the line.
"""
import pytest

from exo import gate

PUSH = "git push --force origin main"
POLICIES = [None,
            gate.normalize_policies({"gates": {"deny": ["force_push"]}}),
            gate.normalize_policies({"gates": {"categories": {"force_push": "deny"}}}),
            gate.normalize_policies({"gates": {"deny": ["delete"]}})]
PUSH_DENIED = ["ask", "deny", "deny", "ask"]
DELETE_DENIED = ["ask", "ask", "ask", "deny"]
ASKS = ["ask"] * 4
ALLOWED = ["allow"] * 4


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
    """The line's outcome under the default policy, `deny: [force_push]`, `categories: {force_push: deny}`
    and `deny: [delete]`."""
    c = sh(line)
    return [gate.decide(c, p).outcome for p in POLICIES]


# The issue's table, and the quoted-paren rows it asks for, each under the force_push deny and the delete
# deny. Main read every one of them and allowed it.
FIXED = [
    (PUSH, "force_push", PUSH_DENIED),
    (f"echo $( (echo '(( ' ; {PUSH}) )", "force_push", PUSH_DENIED),
    (f"echo $((echo '(( ' ; {PUSH}) )", "force_push", PUSH_DENIED),
    (f'echo $((echo "(( " ; {PUSH}) )', "force_push", PUSH_DENIED),
    (f"echo $((echo $'(( ' ; {PUSH}) )", "force_push", PUSH_DENIED),
    ("echo $((echo '(( ' ; git push origin :main) )", "delete", DELETE_DENIED),
    ("echo $((echo '(( ' ; rm -rf x) )", "delete", DELETE_DENIED),
    (f"echo $(( 1 + '(' )); {PUSH}", "force_push", PUSH_DENIED),
    (f'echo $(( 1 + "(( " )); {PUSH}', "force_push", PUSH_DENIED),
    (f"echo $(( 1 + '))' )); {PUSH}", "force_push", PUSH_DENIED),
    (f"echo $((echo hi)); {PUSH}", "force_push", PUSH_DENIED),
]


@pytest.mark.parametrize("line,category,wanted", FIXED)
def test_a_command_substitution_form_denies(line, category, wanted):
    c = sh(line)
    assert c.category == category, c
    assert outcomes(line) == wanted


def test_a_joined_form_denies():
    """The #584 join: the line reads joined, where the span is a command substitution, and as written."""
    assert outcomes("echo $((echo '(( ' ; \\\n" + PUSH + ") )") == PUSH_DENIED


# Plain arithmetic keeps the reading it had, whatever bash runs or discards around it.
KEPT = [
    "echo $((1+2))",
    "echo $(( (1+2) ))",
    "echo $((x ? 1 : 2))",
    "echo $(( $(printf 2) + 1 ))",
    "echo $(( `printf 2` + 1 ))",
    'echo "$((1+2))"',
    "x=$((zz=10)); echo $x",
    "echo $((1+2)) junk",
    "echo $((echo hi; (printf lo) ))",      # arithmetic error in bash: nothing runs
    "echo $(( (echo hi) ))",                # arithmetic error in bash: nothing runs
]


@pytest.mark.parametrize("line", KEPT)
def test_plain_arithmetic_keeps_its_reading(line):
    assert outcomes(line) == ALLOWED


def test_an_unclosed_span_asks():
    """Unclosed: bash cannot parse the line and runs nothing. #158 reads a `$((` bash never closes as a part
    bash rejects, an opaque part that asks (a shell may close it elsewhere), beside this reading of it as
    arithmetic text to the end of the line, and the stricter decision holds."""
    assert outcomes("echo $((echo hi; echo HIDDEN") == ASKS


def test_an_assignment_keeps_its_reading():
    assert outcomes("echo $((PATH=5))") == ASKS
    assert outcomes("echo $[PATH=5]") == ASKS


def test_a_command_substitution_form_reads_its_commands():
    """A `$( ( )` decides as the same span written without the doubled paren does."""
    assert outcomes("echo $((echo hi) )") == ALLOWED
    c = sh("echo $((echo hi) ; " + PUSH + " )")
    assert c.category == "force_push"
    assert outcomes("echo $((echo hi) ; " + PUSH + " )") == PUSH_DENIED
