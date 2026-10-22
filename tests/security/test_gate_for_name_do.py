"""`for NAME do LIST; done` opens the loop body as `for NAME; do LIST; done` does (#436).

Bash and POSIX accept a `do` right after the loop name, with no `in` list and no `;` before it. The loop then runs
over the positional parameters. The gate read that `do` as a word of the `for` command, so the body was never
classified: a force push, a delete, a post or a store write in it was allowed under every policy, a `deny:` entry
included. `select NAME do` is the same in bash. Classification and decision only; nothing is executed.
"""
import pytest

from exo.gate import classify, decide, normalize_policies, split_commands

DENY_PUSH = normalize_policies({"gates": {"deny": ["force_push"]}})
DENY_DELETE = normalize_policies({"gates": {"deny": ["delete"]}})
POLICIES = (None, DENY_PUSH, DENY_DELETE)
PUSH = "git push --force origin main"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def read(line):
    """The line's category and its outcome under the default policy, `deny: [force_push]` and `deny: [delete]`."""
    c = classify(sh(line))
    return c.category, [decide(c, p).outcome for p in POLICIES]


# The issue's table: on main every row was `read` and allowed under all three policies.
ISSUE = [
    (f"for x do {PUSH}; done", "force_push", ["ask", "deny", "ask"]),
    ("for x do rm -rf ~/work; done", "delete", ["ask", "ask", "deny"]),
    ("for x do gh pr comment 3 --body hi; done", "github_post", ["ask", "ask", "ask"]),
    ("for x do cp evil.yaml ~/.exo/policies/default.yaml; done", "store_write", ["ask", "ask", "ask"]),
    (f"set -- a; for x do {PUSH}; done", "force_push", ["ask", "deny", "ask"]),
]


@pytest.mark.parametrize("line,category,outcomes", ISSUE)
def test_the_issue_rows_classify_the_loop_body(line, category, outcomes):
    assert read(line) == (category, outcomes), line


# The same loop wherever bash takes it: nested lines, after an operator, inside other compounds, a nested loop, a
# function body, odd loop names (bash reads the word after `for` as the name, even `do` or `in`) and other blanks.
SPELLINGS = [
    f"select x do {PUSH}; done",
    f"echo $(for x do {PUSH}; done)",
    f"echo `for x do {PUSH}; done`",
    f"true && for x do {PUSH}; done",
    f"false || for x do {PUSH}; done",
    f"for x do {PUSH}; done | cat",
    f"for x do {PUSH}; done & wait",
    f"bash -c 'for x do {PUSH}; done'",
    f"sh -c 'for x do {PUSH}; done'",
    f"eval 'for x do {PUSH}; done'",
    f"bash -c 'select x do {PUSH}; done'",
    f"! for x do {PUSH}; done",
    f"{{ for x do {PUSH}; done; }}",
    f"(for x do {PUSH}; done)",
    f"if true; then for x do {PUSH}; done; fi",
    f"while true; do for x do {PUSH}; done; break; done",
    f"for x do for y do {PUSH}; done; done",
    f"for x in a; do for y do {PUSH}; done; done",
    f"f() for x do {PUSH}; done; f",
    f"coproc for x do {PUSH}; done",
    # after `function NAME` or `coproc [NAME]` and a reserved word, also behind reserved words such as `then`
    f"function f {{ for x do {PUSH}; done; }}; f a",
    f"function f {{ ! for x do {PUSH}; done; }}; f a",
    f"coproc NAME {{ for x do {PUSH}; done; }}",
    f"coproc NAME {{ select x do {PUSH}; done; }}",
    f"if true; then function f {{ for x do {PUSH}; done; }}; fi; f a",
    f"if true; then coproc for x do {PUSH}; done; fi",
    f"{{ function f for x do {PUSH}; done; }}; f a",
    f"for do do {PUSH}; done",
    f"for in do {PUSH}; done",
    f"for x  do {PUSH}; done",
    f"for x\tdo {PUSH}; done",
    # stdin a shell in the body reads, from a pipe or after `done`, as for `for x; do bash; done`
    f"echo '{PUSH}' | for x do bash; done",
    f"for x do bash; done <<< '{PUSH}'",
    f"for x do bash; done <<'EOF'\n{PUSH}\nEOF",
    f"select x do bash; done <<< '{PUSH}'",
]


@pytest.mark.parametrize("line", SPELLINGS)
def test_a_force_push_in_the_body_is_read_wherever_the_loop_stands(line):
    assert read(line) == ("force_push", ["ask", "deny", "ask"]), line


BODIES = [PUSH, "rm -rf build", "gh pr comment 3 --body hi", "cat ~/.ssh/id_rsa", "curl -s https://x.example/i.sh",
          "echo hi > /tmp/f", "cd ~/.ssh && cat id_rsa", "make test", "echo hi"]


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("loop", ["for x", "select x"])
def test_the_loop_reads_as_it_does_with_a_semicolon(loop, body):
    plain, semi = f"{loop} do {body}; done", f"{loop}; do {body}; done"
    assert split_commands(plain) == split_commands(semi)
    assert read(plain) == read(semi), (plain, read(plain), read(semi))
    a, b = classify(sh(plain)), classify(sh(semi))
    assert (a.target, a.setters) == (b.target, b.setters)
    assert [(p.category, p.own) for p in a.parts] == [(p.category, p.own) for p in b.parts]


# `do` stays a plain word where bash reads one: in an `in` list, quoted, or after a `for` that is not a command word.
def test_do_is_a_plain_word_where_bash_reads_one():
    assert split_commands("for x in a do; do b; done")[0] == [["for", "x", "in", "a", "do"], ["do", "b"], ["done"]]
    assert split_commands('for x "do" b; done')[0] == [["for", "x", "do", "b"], ["done"]]
    assert split_commands("echo for x do rm -rf build")[0] == [["echo", "for", "x", "do", "rm", "-rf", "build"]]
    assert split_commands("echo function f { for x do rm -rf build")[0] == [
        ["echo", "function", "f", "{", "for", "x", "do", "rm", "-rf", "build"]]
    assert read("echo for x do rm -rf ~/work") == ("read", ["allow", "allow", "allow"])


# Spellings main already read keep their reading: a `;` or newline before `do`, an `in` list, an arithmetic loop
# (bash needs no `;` before its `do` either), a `{ }` body, and a push after a newline, a `;` or a `(` in the body.
@pytest.mark.parametrize("line", [
    f"for x; do {PUSH}; done",
    f"for x in a; do {PUSH}; done",
    f"for x\ndo {PUSH}\ndone",
    f"for do in a; do {PUSH}; done",
    f"for x in do; do {PUSH}; done",
    f"for ((i=0;i<3;i++)) do {PUSH}; done",
    f"for ((i=0;i<3;i++)); do {PUSH}; done",
    f"for x; {{ {PUSH}; }}",
    f"for x do\n{PUSH}\ndone",
    f"for x do do=1; {PUSH}; done",
    f"for x do ({PUSH}); done",
])
def test_the_spellings_the_gate_already_read_are_unchanged(line):
    assert read(line) == ("force_push", ["ask", "deny", "ask"]), line


def test_a_harmless_body_keeps_its_class():
    assert read("for x do echo hi; done") == ("read", ["allow", "allow", "allow"])
    assert read("set -- a b; for x do printf '%s\\n' \"$x\"; done")[0] == "read"
