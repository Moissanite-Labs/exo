"""Issue #479: a loop body that `cd`s into the store runs its earlier commands in the store on the next pass.

bash runs a loop body again after its own `cd`, so on the second pass of `for i in 1 2; do cp /tmp/evil gates.yaml;
cd ~/.exo/policies; done` the `cp` rewrites the gate's own policy file. The gate read the body once, in order, so the
`cp` kept the folder it had before the `cd`: the line was an allowed read, also under `deny: [store_write]`. classify
now reads such a call a second time, with each loop that holds a `cd` (or `pushd`) into a store or descriptor folder
read again as if that folder were already entered, and keeps that reading beside the one main makes: decide keeps the
strictest. A `for` or `select` loop with a `{ ..; }` body counts too, and the second reading has a budget past which
it fails closed. Every command string here is data for exo.gate.classify/decide and the exo-gate hook; none is run.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins" / "exo-gate"))  # the root suite has no plugin path

from exo import crypto, gate, principal  # noqa: E402
from exo.ledger import Ledger  # noqa: E402
from exo_gate import hook  # noqa: E402


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(cmd):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


def _pol(**gates):
    return gate.normalize_policies({"gates": gates})


DENY = _pol(deny=["store_write"])
RESERVED = _pol(reserved=["store_write"])


def _store_write_part(c):
    return any(p.category == "store_write" for r in gate.readings(c) for p in gate._each_part(r))


# The issue's rows. On main cd371aed, and still on main 3570be0e, the first three are allowed reads, also under
# `deny: [store_write]`; the `select` row asks as opaque_exec and the `$( )` row as credentials, and neither denies.
ISSUE_ROWS = [
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "until false; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "select x in a b; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "while :; do echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies; done",
]
# The same order in other shapes: nested loops, a `cd -` or a `cd` out after the write (the write still lands in the
# store on pass 2), a `cd -` that goes back to the store, pushd and popd, the store named by a variable form or a
# glob, a loop on several lines, inside `if`, a group, a `case`, a subshell, a pipeline, a coproc, a closer's
# redirection, a redirection or here-doc write, a nested line, and a loop condition, which runs again too.
MORE_ROWS = [
    "for i in 1 2; do for j in 1; do cp /tmp/evil gates.yaml; done; cd ~/.exo/policies; done",
    "for i in 1 2; do while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; cd -; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; cd /tmp; done",
    "for i in 1 2; do for j in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; cd /tmp; done",
    # `cd -` goes back to the store on the third pass
    "for i in 1 2 3; do cd -; cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "for i in 1 2; do cp /tmp/evil approval-grants.json; pushd ~/.exo/state; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; popd; pushd ~/.exo/policies; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd $EXO_HOME/policies; done",
    "cat list | while read l; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    'for i in 1 2; do cp /tmp/evil gates.yaml; cd "$HOME/.exo/policies"; done',
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ${X:-~/.exo/policies}; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/pol*; done",
    "for i in 1 2\ndo\n  cp /tmp/evil gates.yaml\n  cd ~/.exo/policies\ndone",
    "if true; then for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; fi",
    "for i in 1 2; do (cp /tmp/evil gates.yaml); cd ~/.exo/policies; done",
    "while read l; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done < /tmp/list",
    "for i in 1 2; do echo hi > gates.yaml; cd ~/.exo/policies; done",
    "until false; do rm -f gates.yaml; cd ~/.exo/policies; done",
    "bash -c 'for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done'",
    "echo $(while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done)",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; echo done",
    "for i in 1 2; do echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies; done",           # #480's row
    "while [ -n \"$(cp /tmp/evil gates.yaml)\" ]; do cd ~/.exo/policies; done",              # a condition runs again
    "until cp /tmp/evil gates.yaml; do cd ~/.exo/policies; done",
    "for x do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "for ((i=0;i<2;i++)); do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "for i in 1 2; do { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }; done",
    "for i in 1 2; do case $i in 1) cp /tmp/evil gates.yaml;; 2) cd ~/.exo/policies;; esac; done",
    "for i in 1 2; do cat <<EOF > gates.yaml\nx\nEOF\ncd ~/.exo/policies; done",
    "! while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done | cat",
    "coproc while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "coproc W while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
]
# bash also takes a `{ ..; }` group as the body of a `for` or `select` loop, `for ((..))` too, and runs it once per pass
# in the same shell, so its `cd` holds on the next pass as in a do/done body (verify of #479). On main f28934a3 and
# 3570be0e, nine of these 18 rows are allowed reads, also under `deny: [store_write]` (the first, `for i;`,
# `for ((;;))`, the newline row, the three nested ones, the pushd row and the `if` row); seven ask as opaque_exec and
# the `$( )` row as credentials, and none but the `> /tmp/out` row (a store write there already) denies under
# `deny: [store_write]`.
BRACE_ROWS = [
    "for i in 1 2; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for ((i=0;i<2;i++)) { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "select x in a b; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for i; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "select x; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for ((;;)) { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for ((i=0; i<2; i++)); { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for((i=0;i<2;i++)){ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for i in 1 2\n{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for i in 1 2; { for j in 1; { cp /tmp/evil gates.yaml; }; cd ~/.exo/policies; }",
    "for i in 1 2; do for j in 1; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }; done",
    "for i in 1 2; { while :; do cp /tmp/evil gates.yaml; done; cd ~/.exo/policies; }",
    "for i in 1 2; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; } > /tmp/out",
    "coproc for i in 1 2; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for i in 1 2; { echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies; }",
    "for i in 1 2; { cp /tmp/evil approval-grants.json; pushd ~/.exo/state; }",
    "bash -c 'for i in 1 2; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }'",
    "if true; then for i in 1 2; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }; fi",
]
# Second verify: in `for ((..))` a reserved word is a plain variable name, and each `|` or `;` in the arithmetic is a
# command of its own to _parse, so the header is found from the `((` itself, not from the commands before the group.
# bash runs each of these bodies twice (printf-only probes). On main f28934a3 and 3570be0e each asks as opaque_exec
# and is not denied under `deny: [store_write]`; permissions bypass auto-allows it.
_PAD = "|0" * 20
ARITH_ROWS = [
    f"for (({n}=0; {n}<2; {n}++)) {{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }}"
    for n in ("do", "while", "done", "if", "until", "esac")
] + [
    "for ((i=0; ! (i>1); i++)) { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    f"for ((i=0; i<2{_PAD}; i++)) {{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }}",
    f"for ((i=0; i<2; i=i+(1{_PAD}))) {{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }}",
    "for ((do=0; do<2; do++))\n{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for ((do=0; do<2; do++)); { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for x in 1; do for ((done=0; done<2; done++)) { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }; done",
    "for ((i=0;i<2;i++)) { for ((do=0; do<1; do++)) { cp /tmp/evil gates.yaml; }; cd ~/.exo/policies; }",
]
# Review 5689293944 on #838: what the header's `(` and `)` count may meet. bash reads a quoted paren (in "..", '..' or a
# $( )), a base `2#10`, nested parens, a $(( )), a `<(` or `<<` that is arithmetic there, and a header over lines as
# part of the header, and a paren in a comment after the `))` as a comment; it runs each of these bodies twice
# (printf-only probes). The gate's lexer keeps a quoted paren inside its word, so it is no `(` or `)` token to count.
# On main 3570be0e each asks as opaque_exec or credentials and is not denied under `deny: [store_write]`.
_B = "{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }"
HEADER_ROWS = [
    'for ((i=0; i<${x#")"}; i++)) ' + _B,
    'for ((i=0; i<${x%"("}; i++)) ' + _B,
    "for ((i=0; i<${x%'('}; i++)) " + _B,
    "for ((i=0; i<2; i=i+$(printf ')' >/dev/null; printf 1))) " + _B,
    "for ((i=0; i<2#10; i++)) " + _B,
    "for ((i=0; i<((1+1)); i++)) " + _B,
    "for ((i=0; i<$((1+1)); i++)) " + _B,
    "for ((i=0; i<(2); i++)) " + _B,
    "for ((i=1; i<4; i=i<<1)) " + _B,
    "for ((i=0;\ni<2;\ni++))\n" + _B,
    "for ((i=0; i<2; i++)) # ) c\n" + _B,
    "for ((i=0; i<2; i++)) # ( c\n" + _B,
]
ARITH_ROWS += HEADER_ROWS
BRACE_ROWS += ARITH_ROWS
CONTROL = "cd ~/.exo/policies; cp /tmp/evil gates.yaml"
# #857: a `while` or `until` condition runs again on every pass, so when its first command is the `cd` into the
# store, a substitution in that command runs there from the second pass. bash 5.2 confirms the write lands on pass 2
# for each row. On main 1401ff81 all six are allowed reads, also under `deny: [store_write]`.
COND_ROWS = [
    "while cd ~/.exo/policies$(cp /tmp/evil gates.yaml); do sleep 1; done",
    "until ! cd ~/.exo/policies$(cp /tmp/evil gates.yaml); do sleep 1; done",
    "while cd ~/.exo/policies`cp /tmp/evil gates.yaml`; do sleep 1; done",
    "while pushd ~/.exo/policies$(cp /tmp/evil gates.yaml); do sleep 1; done",
    "while cd ~/.exo/policies < <(cp /tmp/evil gates.yaml); do sleep 1; done",
    "while if cd ~/.exo/policies$(cp /tmp/evil gates.yaml); then :; fi; do sleep 1; done",
]


@pytest.mark.parametrize("line", ISSUE_ROWS + MORE_ROWS + BRACE_ROWS + COND_ROWS + [CONTROL])
def test_a_write_before_the_loops_cd_into_the_store_is_a_store_write(line):
    c = _bash(line)
    assert _store_write_part(c), (line, [r.category for r in gate.readings(c)])
    d = gate.decide(c)
    assert d.outcome == "ask", (line, d)
    assert gate.decide(c, DENY).outcome == "deny", line
    r = gate.decide(c, RESERVED)
    assert r.outcome == "ask" and ("store_write", r.reserved[0][1]) in r.reserved, (line, r)
    lifted = _pol(categories={"store_write": "allow", "read": "allow", "local_write": "allow", "shell": "allow"})
    assert gate.decide(c, lifted).outcome == "ask", line              # no policy lowers the store floor
    assert gate.no_grant(c) and gate.binds_exact(c), line
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:lead"]}})
    assert gate.call_approvers(c, approvals) == ["principal"], line


@pytest.mark.parametrize("line", ISSUE_ROWS[:3])
def test_the_issue_rows_lead_with_the_store_write_and_keep_mains_reading(line):
    c = _bash(line)
    assert c.category == "store_write", (line, c.category)
    # main's reading of the line is still one of the readings, as main made it
    assert "read" in [r.category for r in gate.readings(c)], line
    plain = gate.classify_shell(line)                 # classify_shell alone reads the line once, as on main
    assert plain.category == "read" and not any(p.category == "store_write" for p in gate._each_part(plain))


@pytest.mark.parametrize("line", [
    "while :; do cp /tmp/evil fd/3; cd /dev; done",
    "for i in 1 2; do cp /tmp/evil 3; cd /dev/fd; done",
    "for i in 1 2; do cp /tmp/evil 3; cd /dev; cd fd; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; cp /tmp/evil 3; cd /dev/fd; done",
    "for i in 1 2; { cp /tmp/evil 3; cd /dev/fd; }",
    "while cd /dev/fd < <(cp /tmp/evil 3); do :; done",            # #857: a condition's own `cd` names it too
])
def test_a_loop_into_a_descriptor_folder_names_that_folder(line):
    c = _bash(line)
    assert any(p.category == "store_write" and p.target.startswith("/dev")
               for r in gate.readings(c) for p in gate._each_part(r)), line
    assert gate.decide(c, _pol(deny=["target:/dev*"])).outcome == "deny", line
    assert gate.decide(c).outcome == "ask", line


# #858 review: when the body holds a `cd` of its own, the body's folder keeps naming the second pass, as on main, and
# the condition's descriptor `cd` adds nothing over it. Each row denies under its one target rule on main.
@pytest.mark.parametrize("line,target", [
    ("while cd /dev/fd; do cp /tmp/evil 3; cd /proc/self/fd; done", "target:/proc*"),
    ("while cd /proc/self/fd; do cp /tmp/evil 3; cd /dev/fd; done", "target:/dev*"),
    ("while cd /dev/fd < <(cp /tmp/evil 3); do cd /proc/self/fd; done", "target:/proc*"),
    ("while cd /dev/fd < <(cp /tmp/evil 3); do cd ~/.exo/policies; done", "target:*store*"),
])
def test_a_body_cd_keeps_naming_the_second_pass_beside_a_condition_cd(line, target):
    c = _bash(line)
    assert gate.decide(c, _pol(deny=[target])).outcome == "deny", (line, [
        (p.category, p.target) for r in gate.readings(c) for p in gate._each_part(r)])


# Guards: lines whose reading this change must leave as it was on main (cd371aed, f28934a3 and 3570be0e).
@pytest.mark.parametrize("line,category,outcome", [
    ("for i in 1 2; do cp /tmp/evil gates.yaml; done", "read", "allow"),               # no cd in the loop
    ("for i in 1 2; do cp /tmp/evil gates.yaml; cd /tmp/proj; done", "read", "allow"),  # a cd into a plain folder
    ("while :; do cp a b; cd ../src; done", "read", "allow"),
    # #857: a condition's `cd` into the store with nothing written, a closer's redirection (run once, before the
    # first pass) and a condition's `cd` into a plain folder keep their reading
    ("while cd ~/.exo/policies; do sleep 1; done", "read", "allow"),
    ("while cd ~/.exo/policies; do :; done < <(cp /tmp/evil gates.yaml)", "read", "allow"),
    ("while cd /tmp/proj$(cp /tmp/evil gates.yaml); do sleep 1; done", "read", "allow"),
    # only the loop runs again: a write before the loop runs once, where the line started
    ("cp /tmp/evil gates.yaml; for i in 1 2; do cd ~/.exo/policies; done", "shell", "allow"),
    # bash expands a `for` word list once, before the first pass, so its $( ) runs where the line started
    ("for f in $(cp /tmp/evil gates.yaml); do cd ~/.exo/policies; done", "credentials", "ask"),
    ("cd ~/.exo/policies; for f in *; do cat $f; done", "credentials", "ask"),
    # brace bodies: a plain folder, a group that is no loop's body (it runs once), a group after a loop's `done`,
    # a word list's $( ) and a write before the loop keep their reading too
    ("for i in 1 2; { cp /tmp/evil gates.yaml; cd /tmp/proj; }", "read", "allow"),
    ("{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "shell", "allow"),
    ("for i in 1 2; do :; done; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "read", "allow"),
    ("for x in a; do :; done\n{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "read", "allow"),
    ("for f in $(cp /tmp/evil gates.yaml); { cd ~/.exo/policies; }", "credentials", "ask"),
    ("cp /tmp/evil gates.yaml; for i in 1 2; { cd ~/.exo/policies; }", "shell", "allow"),
    # an arithmetic command is no loop, and only the group right after a `for ((..))` header is its body
    ("(( i++ )); { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "opaque_exec", "ask"),
    ("for ((;;)) { :; }; { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "read", "allow"),
    ("for ((i=0;i<2;i++)) { :; }\n{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "opaque_exec", "ask"),
])
def test_guard_rows_keep_their_reading(line, category, outcome):
    c = _bash(line)
    assert (c.category, gate.decide(c).outcome) == (category, outcome), (line, c.category)
    assert c.also is None and not _store_write_part(c), line


# A known limit (docs/GATE.md, D-038 amendment): the `cd` check reads no variable's value where it is set, so a `cd`
# through a loop variable or a variable set earlier is not followed, as on a line with no loop. It decides as on main.
@pytest.mark.parametrize("line", [
    "for d in /tmp ~/.exo/policies; do cp /tmp/evil gates.yaml; cd $d; done",
    "d=~/.exo/policies; for i in 1 2; do cp /tmp/evil gates.yaml; cd $d; done",
    "d=~/.exo/policies; cd $d; cp /tmp/evil gates.yaml",
])
def test_a_cd_through_a_variable_is_a_known_limit(line):
    c = _bash(line)
    assert (c.category, gate.decide(c, DENY).outcome) == ("read", "allow"), (line, c.category)


# Issue #853 (a known limit here before): `_parse` reads `time` as a program, so the compound behind it was its argv
# and the loop had no second reading. On main a1dd4fec these ask as `opaque_exec`, `deny: [store_write]` does not deny
# them and the hook's bypass auto-allows them. classify now also reads the line with the compound behind a pipeline
# prefix opened, beside main's reading (opaque_exec, which still leads on the tie): the second pass's write is a store
# write in that reading, so the line denies under `deny: [store_write]` and no grant covers it.
# tests/security/test_gate_pipeline_prefix_853.py has the other prefixes, bodies and the hook.
@pytest.mark.parametrize("line", [
    "time for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "time while :; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
])
def test_a_loop_behind_time_is_read_as_without_it(line):
    c = _bash(line)
    assert (c.category, gate.decide(c).outcome, gate.decide(c, DENY).outcome) == ("opaque_exec", "ask", "deny"), line
    assert c.also is not None and _store_write_part(c) and gate.no_grant(c), line


# Review 5689293944 on #838: headers the gate's lexer reads apart from bash, which decide as on main 3570be0e. In
# bash a `#` inside `((..))` is no comment: a word that starts with it fails the update expression after the first
# pass, so the body runs once (printf-only probes), while the lexer takes the rest of the line as a comment. A known
# limit: the lexer reads a `<<` in the header as a here-doc, whose body takes the next line, so a `{ ..; }` on that
# line is not marked as the loop's body and its second pass is not read, on main as here, though bash runs it twice
# (`<<` is a shift there). Its commands are still read once, as on main: an `rm` or a store path in it asks or
# denies as on its own. On one line the second pass is read (HEADER_ROWS).
@pytest.mark.parametrize("line", [
    "for ((i=0; i<2; i++ #x)) { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for ((i=0; i<2; i++ #))\n{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
    "for ((i=1; i<4; i=i<<1))\n{ cp /tmp/evil gates.yaml; cd ~/.exo/policies; }",
])
def test_a_header_the_lexer_reads_apart_from_bash_decides_as_on_main(line):
    c = _bash(line)
    assert (c.category, gate.decide(c).outcome, gate.decide(c, DENY).outcome) == ("opaque_exec", "ask", "ask"), line
    assert c.also is None and not _store_write_part(c), line


def _nest(depth, body="while :; do echo {x}; cp /tmp/evil gates.yaml; cd ~/.exo/policies; done"):
    line = "true"
    for _ in range(depth):
        line = body.replace("{x}", "$(" + line + ")")
    return line


def _unread(c):
    return [p for r in gate.readings(c) for p in gate._each_part(r) if "unread loop pass" in p.signals]


@pytest.mark.parametrize("line", ISSUE_ROWS + BRACE_ROWS + [_nest(2)])
def test_an_ordinary_line_stays_within_the_second_readings_budget(line):
    assert not _unread(_bash(line)), line


def test_a_nest_past_the_budget_fails_closed_and_keeps_mains_reading():
    # Each level reads its body in two folders, so the second reading of this nest reads afresh more lines than the
    # first reading did plus _LOOP_BUDGET: it stops there, and one store write stands for the passes it left unread.
    c = _bash(_nest(8))
    [part] = _unread(c)
    assert part.category == "store_write" and part.own is None
    assert "credentials" in [r.category for r in gate.readings(c)]          # main's reading, as main made it
    assert gate.decide(c).outcome == "ask" and gate.decide(c, DENY).outcome == "deny"
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:lead"]}})
    assert gate.call_approvers(c, approvals) == ["principal"] and gate.no_grant(c)


@pytest.mark.parametrize("body", [
    "while :; do echo {x}; cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "while cd ~/.exo/policies; do echo {x}; cp /tmp/evil gates.yaml; done",          # #857: a condition's `cd`
    "while cd ~/.exo/policies{x}; do cp /tmp/evil gates.yaml; done",
])
def test_the_second_reading_of_a_loop_nest_costs_about_what_the_first_does(monkeypatch, body):
    # The verifier measured the second reading of such nests at about 6 times main's time, at every depth from 5 to
    # 13. With the budget a call reads afresh at most _LOOP_BUDGET more lines than twice main's reading does. CPU time
    # of this process (time.process_time), the better of two runs, against the call read without its second reading.
    # It guards the new code: on main it fails only because main has no _loop_reading to patch.
    action = {"tool": "bash", "args": {"command": _nest(8, body)}}

    def cpu():
        best = None
        for _ in range(2):
            t = time.process_time()
            gate.classify(action)
            took = time.process_time() - t
            best = took if best is None else min(best, took)
        return best
    whole = cpu()
    monkeypatch.setattr(gate, "_loop_reading", lambda c, *a: c)
    first = cpu()
    assert whole <= 3 * first + 0.3, (whole, first)


def test_a_classify_inside_a_classify_keeps_the_outer_calls_loop_list(monkeypatch):
    # Review 5689293944 on #838. A line read inside the call (`bash -c`) counts on the call's own _loop_pass list, on
    # each reading. A classify called while one reads (D-074's `ordinary` reading does so) sets a list of its own and
    # resets it, so the outer list is the same object with the same items after it. None is left set after classify,
    # also when the reading raises.
    outer = "for i in 1 2; do bash -c 'echo hi'; cp /tmp/evil gates.yaml; cd ~/.exo/policies; done"
    real, lists, kept = gate._classify_shell, {}, []

    def shell(cmd, depth, *a):
        loops = gate._loop_pass.get()
        lists.setdefault((cmd, loops[0]), []).append(id(loops))
        if cmd == outer:
            before = list(loops)
            inner = gate.classify({"tool": "bash", "args": {"command": ISSUE_ROWS[1]}})
            assert inner.category == "store_write"
            kept.append((gate._loop_pass.get() is loops, before == list(loops)))
        return real(cmd, depth, *a)

    monkeypatch.setattr(gate, "_classify_shell", shell)
    c = _bash(outer)
    assert _store_write_part(c) and gate._loop_pass.get() is None
    assert kept == [(True, True), (True, True)]                      # the first reading and the second
    for again in (False, True):
        assert lists[(outer, again)] == lists[("echo hi", again)], again
    assert lists[(outer, False)] != lists[(outer, True)]

    def boom(cmd, depth, *a):
        raise RuntimeError("probe")
    monkeypatch.setattr(gate, "_classify_shell", boom)
    with pytest.raises(RuntimeError):
        _bash(outer)
    assert gate._loop_pass.get() is None


# The hook: the line asks the principal, and permissions bypass keeps the store floor.
class MockCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


def _gate(tmp_path, bypass, gates=None):
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "approvals.yaml").write_text('approvers:\n  default: [principal, "seat:builder"]\n')
    if gates is not None:
        (root / "policies" / "gates.yaml").write_text(gates)
    key = tmp_path / "gate.key"
    crypto.save_key(crypto.keygen(), key)
    ctx = MockCtx({"principal_store": str(root), "key_path": str(key), "decision_model": False, "mode": "enforce"})
    g = hook.register(ctx)
    g.policies()
    if bypass:
        token = principal.CALLER.set(principal.Caller("principal:tui"))
        try:
            hook.permissions_command(g, "bypass")
        finally:
            principal.CALLER.reset(token)
        assert g.bypass_on
    return g, ctx, root


def _run(ctx, line):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name="terminal", args={"command": line}, session_id="s1", tool_call_id="c1",
                          turn_id="t1", task_id="", author="principal:tui"))


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize("line", ISSUE_ROWS[:3] + BRACE_ROWS[:1] + ARITH_ROWS[:3] + ARITH_ROWS[6:8] + COND_ROWS[:2])
def test_the_hook_asks_the_principal_and_bypass_keeps_the_floor(tmp_path, bypass, line):
    g, ctx, root = _gate(tmp_path, bypass)
    r = _run(ctx, line)
    assert r is not None and r["action"] == "approve" and r["approvers"] == ["principal"], (line, r)
    assert r.get("once") is True and r.get("binding"), r
    if bypass:
        with Ledger(root / "ledger.sqlite") as led:
            [row] = [e["body"] for e in led.iter(kind="action") if e["body"].get("phase") == "decision"]
        assert row["outcome"] == "ask" and row["bypass"] == {"floor": "store_write"}, row
        assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1


@pytest.mark.parametrize("bypass", [False, True])
def test_the_hook_asks_the_principal_for_a_nest_past_the_budget(tmp_path, bypass):
    # Review 5689293944 on #838: the smallest nest whose second reading runs past _LOOP_BUDGET (5 levels; 4 stays
    # within it). The unread passes are one store write, so the hook asks the principal once, in bypass too (there the
    # floor it names is main's reading, credentials, which leads).
    line = _nest(5)
    assert _unread(_bash(line)) and not _unread(_bash(_nest(4)))
    g, ctx, root = _gate(tmp_path, bypass)
    r = _run(ctx, line)
    assert r is not None and r["action"] == "approve" and r["approvers"] == ["principal"], r
    assert r.get("once") is True and r.get("binding"), r
    if bypass:
        with Ledger(root / "ledger.sqlite") as led:
            [row] = [e["body"] for e in led.iter(kind="action") if e["body"].get("phase") == "decision"]
        assert row["outcome"] == "ask" and row["bypass"] == {"floor": "credentials"}, row
        assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1


@pytest.mark.parametrize("bypass", [False, True])
def test_the_hook_denies_a_nest_past_the_budget_under_deny_store_write(tmp_path, bypass):
    # The unread passes reach the hook: under `deny: [store_write]` it blocks the nest, where main 3570be0e asks.
    _, ctx, _ = _gate(tmp_path, bypass, gates="deny: [store_write]\n")
    r = _run(ctx, _nest(5))
    assert r is not None and r["action"] == "block", r


def test_the_hook_still_allows_a_loop_with_no_cd_into_the_store(tmp_path):
    _, ctx, _ = _gate(tmp_path, False)
    assert _run(ctx, "for i in 1 2; do cp /tmp/evil gates.yaml; cd /tmp/proj; done") is None
