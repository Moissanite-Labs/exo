"""Issue #867 (a #479 follow-up): each loop's second pass is named by its own descriptor folder.

#479 reads a loop that `cd`s into the store or a descriptor folder a second time, with that folder entered where the
loop starts, and names it by the loop's first descriptor `cd` (fd_cwd, #217). _loop_again set that name with
`setdefault`, once for the line, so when a line holds two such loops the second kept the first one's folder: in
`for i in 1; do cd /dev/fd; done; while cd /proc/self/fd < <(cp /tmp/evil 3); do break; done` only `/dev/fd` was
read, and under `deny: [target:/proc*]` the line asked instead of denying. classify now adds one more reading beside
main's two, in which each loop's second pass starts in its own folder (_loop_folder), so decide keeps the stricter
and the `/proc/self/fd` write is named. Main's readings stay as they were. Every command string here is data for
exo.gate.classify/decide and the exo-gate hook; none is run.
"""
import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "exo-gate"))  # the root suite has no plugin path

from exo import crypto, gate  # noqa: E402
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


DEV, PROC, STORE = "target:/dev*", "target:/proc*", "target:*store*"
IN_STORE = "a path inside the store (after cd)"


def _targets(c):
    return {p.target for r in gate.readings(c) for p in gate._each_part(r) if p.category == "store_write"}


# The issue's row and its mirror. On main d742d1bb (and b3efb5e7, 75d72a7f) each names only the first loop's folder:
# the first denies under `deny: [target:/dev*]` and asks under `deny: [target:/proc*]`, the mirror the other way.
ISSUE_ROWS = [
    "for i in 1; do cd /dev/fd; done; while cd /proc/self/fd < <(cp /tmp/evil 3); do break; done",
    "for i in 1; do cd /proc/self/fd; done; while cd /dev/fd < <(cp /tmp/evil 3); do break; done",
]
_A = "for i in 1 2; do cp /tmp/evil 3; cd /dev/fd; done"
_B = "for i in 1 2; do cp /tmp/evil 3; cd /proc/self/fd; done"
# Two loops into /dev/fd and /proc/self/fd in other shapes, in both orders where the order matters: `;`, `&&`, `||`
# and a newline between them; for, while, until, select and a `{ ..; }` body; cd, pushd, builtin cd and /dev/fd/3; a
# condition cd (#857) with `< <(..)`; nested loops; the pair inside `$( )`, `bash -c` and a function body; rm -rf
# and git push bodies and a here-string; a `cd` before the loop; and a line read in /dev/fd already (a `$( )` after
# the `cd`). On main d742d1bb each names the first folder only, so one of the two target rules asks.
TWO_ROWS = [
    f"{_A}; {_B}",
    f"{_B}; {_A}",
    f"{_A} && while :; do cp /tmp/evil 3; cd /proc/self/fd; done",
    f"{_A} || until false; do cp /tmp/evil 3; cd /proc/self/fd; done",
    f"{_A}\nselect x in a; do cp /tmp/evil 3; cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; {{ cp /tmp/evil 3; cd /proc/self/fd; }}",
    f"{_A}; for i in 1 2; do cp /tmp/evil 3; pushd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do cp /tmp/evil 3; builtin cd /proc/self/fd; done",
    "for i in 1 2; do cp /tmp/evil 3; cd /dev/fd/3; done; " + _B,
    _B + "; for i in 1 2; do cp /tmp/evil 3; cd /dev/fd/3; done",
    "while cd /dev/fd < <(cp /tmp/evil 3); do :; done; while cd /proc/self/fd < <(cp /tmp/evil 3); do :; done",
    "while cd /proc/self/fd < <(cp /tmp/evil 3); do :; done; while cd /dev/fd < <(cp /tmp/evil 3); do :; done",
    f"for j in 1; do {_A}; done; {_B}",
    f"echo $({_A}; {_B})",
    f"bash -c '{_A}; {_B}'",
    f"f() {{ {_A}; {_B}; }}",
    f"{_A}; for i in 1 2; do rm -rf 3; cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do git push origin main; cd /proc/self/fd; done",
    f"{_A}; while cat <<< x > 3; do cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do cp /tmp/evil 3; cd /proc/self/fd; cd /dev/fd; done",
    "cd /dev/fd; " + _B,
    f"cd /dev/fd; echo $({_B})",
    # each loop behind its own `time`, `time -p` or `!` (#853): only the opened reading sees the loops, so the second
    # folder is named only if the reading this change adds is made inside it (_opened_reading) too
    f"time {_A}; time {_B}",
    f"! {_B}; time -p {_A}",
    # each loop in its own `coproc` (#864): only #864's late reading opens it, so the second folder is named only if
    # the reading this change adds is made inside that reading too (_late_reading)
    f"{{ coproc {_A}; }}; {{ coproc {_B}; }}",
    f"if true; then coproc {_A}; fi; if true; then coproc {_B}; fi",
    f"coproc W {{ {_A}; }}; coproc V {{ {_B}; }}",
]
_AC = "while cd /dev/fd < <(cp /tmp/evil 3); do :; done"
_BC = "while cd /proc/self/fd < <(cp /tmp/evil 3); do :; done"
# The pair inside one outer loop (for, while, until, select, a `{ ..; }` body, in `$( )`, two loops deep), in both
# orders, with body and condition (#857) cds: the outer loop is one span, named by its first descriptor `cd`, so on
# main d742d1bb the inner /proc/self/fd loop's own second pass was never named. Also a `cd /dev/fd` in the outer body
# before the loop, an inner loop whose write is in a `$( )` (alone, and after an inner loop into the same folder
# that writes nothing), and the outer loop in a `$( )` read in /dev/fd already.
_WRAPS = ["for j in 1 2; do {}; done", "while :; do {}; done", "until false; do {}; done", "for j in 1 2; {{ {}; }}",
          "select z in a; do {}; done", "echo $(for j in 1 2; do {}; done)",
          "for j in 1 2; do for k in 1; do {}; done; done"]
_PAIRS = ((_A, _B), (_B, _A), (_A, _BC), (_AC, _B), (_AC, _BC))
NEST_ROWS = [w.format(f"{x}; {y}") for w in _WRAPS for x, y in _PAIRS] + [
    f"for j in 1 2; do cd /dev/fd; {_B}; done",
    f"while :; do cd /dev/fd/3; {_B}; done",
    f"for j in 1 2; do {_A}; for i in 1 2; do echo $(cp /tmp/evil 3); cd /proc/self/fd; done; done",
    f"for j in 1 2; do {_A}; for i in 1; do cd /proc/self/fd; done; for i in 1 2; do echo $(cp /tmp/evil 3); "
    "cd /proc/self/fd; done; done",
    f"cd /dev/fd; echo $(for j in 1 2; do {_A}; {_B}; done)",
]
_W = "echo ${v:-$(cp /tmp/evil 3)}"
# #158 (main d3cbd874) reads a `${..}` word as bash's parser does, and the call both ways where that may differ from
# the reading before (``flat``): the write in a `${v:-$( )}` word of a loop body runs on the loop's second pass in its
# own folder too. In the second loop, the first, both, a loop inside the word, the pair inside a word, after a
# `cd /dev/fd`, in a `$((`, a nested word, after a `'}'` word (the write outside it), a joined `$\<newline>` word
# (#584), a condition `<( )`, a `{ ..; }` body, behind a quoted prefix (#546; also a plain write behind one, with no
# word) and `time` (#853), inside an outer loop, `bash -c`, a sudo -i shell's stdin (#491) and an `env -S` text
# (#591). On main d3cbd874 each names /dev/fd only, so `deny: [target:/proc*]` asks.
WORD_ROWS = [
    f"{_A}; for i in 1 2; do {_W}; cd /proc/self/fd; done",
    f"for i in 1 2; do {_W}; cd /dev/fd; done; {_B}",
    f"for i in 1 2; do {_W}; cd /dev/fd; done; for i in 1 2; do {_W}; cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do echo ${{v:-$(for k in 1; do cp /tmp/evil 3; done)}}; cd /proc/self/fd; done",
    f"echo ${{v:-$({_A}; {_B})}}",
    f"{_A}; echo ${{v:-$({_B})}}",
    f"cd /dev/fd; echo ${{v:-$({_B})}}",
    f"{_A}; for i in 1 2; do echo $(( ${{v:-$(cp /tmp/evil 3)}} )); cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do echo ${{v:-${{w:-$(cp /tmp/evil 3)}}}}; cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do echo ${{v:-'}}'}}; cp /tmp/evil 3; cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do echo $\\\n{{v:-$(cp /tmp/evil 3)}}; cd /proc/self/fd; done",
    f"{_A}; while cd /proc/self/fd < <(echo ${{v:-$(cp /tmp/evil 3)}}); do :; done",
    f"{_A}; for i in 1 2; {{ {_W}; cd /proc/self/fd; }}",
    f"{_A}; for i in 1 2; do X='a b' {_W}; cd /proc/self/fd; done",
    f"{_A}; for i in 1 2; do X='a b' cp /tmp/evil 3; cd /proc/self/fd; done",
    f"time {_A}; time for i in 1 2; do {_W}; cd /proc/self/fd; done",
    f"for j in 1 2; do {_A}; for i in 1 2; do {_W}; cd /proc/self/fd; done; done",
    f"bash -c '{_A}; for i in 1 2; do {_W}; cd /proc/self/fd; done'",
    f"sudo -i <<< '{_A}; for i in 1 2; do {_W}; cd /proc/self/fd; done'",
    f"env -S 'bash -c \"{_A}; for i in 1 2; do {_W}; cd /proc/self/fd; done\"'",
]
# A store `cd` in one loop and a descriptor `cd` in the other: the store loop's second pass starts in the store, so it
# is named as one, where main named it by the first loop's descriptor folder (`target:*store*` asked on d742d1bb).
_CDW = "for i in 1 2; do cp /tmp/evil 3; cd ${v:-/proc/self/fd}; done"
STORE_ROWS = [
    f"{_A}; for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    "cd /dev/fd; echo $(for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done)",
    # each loop behind its own `time` (#853): named in the opened reading only
    f"time {_A}; time for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done",
    # the store write in a `${v:-$( )}` word (#158)
    f"{_A}; for i in 1 2; do echo ${{v:-$(cp /tmp/evil gates.yaml)}}; cd ~/.exo/policies; done",
    # a `cd` to a `${..}` word, a folder the gate does not know: named as the store, as for that loop alone (main
    # d3cbd874 names it so alone, and by the first loop's /dev/fd after it)
    f"{_A}; {_CDW}",
    f'{_A}; for i in 1 2; do cp /tmp/evil 3; cd "${{v:-/proc/self/fd}}"; done',
]


@pytest.mark.parametrize("line", ISSUE_ROWS + TWO_ROWS + NEST_ROWS + WORD_ROWS)
def test_each_loop_names_its_own_descriptor_folder(line):
    c = _bash(line)
    got = _targets(c)
    assert any(t.startswith("/dev/fd") for t in got) and "/proc/self/fd" in got, (line, got)
    for rule in (DEV, PROC):
        assert gate.decide(c, _pol(deny=[rule])).outcome == "deny", (line, rule, got)
    assert gate.decide(c).outcome == "ask" and gate.no_grant(c), line


@pytest.mark.parametrize("line", STORE_ROWS)
def test_a_store_loop_after_a_descriptor_folder_is_named_as_the_store(line):
    c = _bash(line)
    got = _targets(c)
    assert "/dev/fd" in got and IN_STORE in got, (line, got)
    for rule in (DEV, STORE):
        assert gate.decide(c, _pol(deny=[rule])).outcome == "deny", (line, rule, got)
    assert gate.decide(c, _pol(deny=["store_write"])).outcome == "deny", line
    assert gate.decide(c).outcome == "ask" and gate.no_grant(c), line


def _reading_targets(c):
    return [{p.target for p in gate._each_part(r) if p.category == "store_write"} for r in gate.readings(c)]


@pytest.mark.parametrize("line,first", [
    (ISSUE_ROWS[0], "/dev/fd"), (ISSUE_ROWS[1], "/proc/self/fd"), (TWO_ROWS[0], "/dev/fd"),
    (TWO_ROWS[1], "/proc/self/fd")])
def test_mains_reading_is_still_one_of_the_readings(line, first):
    # Main d742d1bb reads each of these once, naming the first loop's folder only. That reading stays, and the one
    # this change adds, with both folders, is beside it.
    got = _reading_targets(_bash(line))
    assert len(got) == 2 and {first} in got and {"/dev/fd", "/proc/self/fd"} in got, (line, got)


# Guards: lines main d742d1bb reads right keep their reading, and no reading is added for them (the count of
# readings and the store targets of each are those main d742d1bb gives).
GUARD_ROWS = [
    # one loop with a descriptor cd
    ("for i in 1 2; do cp /tmp/evil 3; cd /dev/fd; done", [{"/dev/fd"}, set()]),
    ("while cd /proc/self/fd < <(cp /tmp/evil 3); do :; done", [{"/proc/self/fd"}, set()]),
    ("while cd /dev/fd; do cp /tmp/evil 3; cd /proc/self/fd; done", [{"/dev/fd"}, {"/dev/fd", "/proc/self/fd"}]),
    # two loops into the same folder
    (f"{_A}; {_A}", [{"/dev/fd"}]),
    # a store cd loop, alone and before a descriptor loop (main names both already)
    ("for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done", [{IN_STORE}, set()]),
    (f"for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done; {_B}",
     [{IN_STORE}, {IN_STORE, "/proc/self/fd"}]),
    # a `cd` after the loop, outside any loop: the `cp` before it runs once, in /dev/fd
    (f"{_A}; cp /tmp/evil 3; cd /proc/self/fd", [{"/dev/fd"}]),
    # known limit, as on main (#888): a loop whose own commands `cd` into two folders is named by its first
    # descriptor `cd`, though bash starts its second pass where the last one left
    ("while :; do cp /tmp/evil 3; cd /dev/fd; cd /proc/self/fd; done", [{"/dev/fd"}, set()]),
    (f"while :; do {_B}; cd /dev/fd; done", [{"/proc/self/fd"}, set()]),
    # #158: a `cd` in a word's `$( )` runs in a subshell and leaves the loop where it was, so the second loop has no
    # second pass of its own; two loops into one folder, one with a `${v:-$( )}` word (main d3cbd874's readings: the
    # call, its #479 second reading, and the reading as before #143, ``flat``)
    (f"{_A}; for i in 1 2; do cp /tmp/evil 3; echo ${{v:-$(cd /proc/self/fd)}}; done", [{"/dev/fd"}, {"/dev/fd"}]),
    (f"{_A}; for i in 1 2; do {_W}; cd /dev/fd; done", [{"/dev/fd"}, {"/dev/fd"}, {"/dev/fd"}, set()]),
]


@pytest.mark.parametrize("line,readings", GUARD_ROWS)
def test_guard_rows_keep_their_reading(line, readings):
    c = _bash(line)
    assert _reading_targets(c) == readings, (line, _reading_targets(c))
    assert gate.decide(c).outcome == "ask" and gate.no_grant(c), line


@pytest.mark.parametrize("line", [
    "for i in 1 2; do cp a b; cd /tmp/a; done; for i in 1 2; do cp a b; cd /tmp/b; done",
    "for i in 1 2; do cp /tmp/evil gates.yaml; done; for i in 1 2; do cp /tmp/evil 3; done",
])
def test_loops_into_plain_folders_stay_allowed_reads(line):
    c = _bash(line)
    assert (c.category, gate.decide(c).outcome, c.also) == ("read", "allow", None), line


def _builtin_policies():
    spec = importlib.util.spec_from_file_location("_gate_diff_867", ROOT / "scripts" / "gate_diff.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.BUILTIN_POLICIES.values())


_RANK = {"allow": 0, "log": 1, "ask": 2, "deny": 3}
_SPLIT = [{"gates": {"deny": [r]}} for r in (DEV, PROC, STORE)] + [
    {"gates": {"deny": ["store_write"]}}, {"gates": {"reserved": ["store_write"]}},
    {"gates": {"deny": [DEV], "reserved": [PROC]}}, {"gates": {"deny": [PROC], "reserved": [DEV]}}]
# Each two-loop line and the loops it is made of, alone.
TWINS = [
    (f"{_A}; {_B}", [_A, _B]),
    (f"{_B}; {_A}", [_B, _A]),
    (ISSUE_ROWS[0], ["while cd /proc/self/fd < <(cp /tmp/evil 3); do break; done"]),
    (ISSUE_ROWS[1], ["while cd /dev/fd < <(cp /tmp/evil 3); do break; done"]),
    (STORE_ROWS[0], [_A, "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done"]),
    (NEST_ROWS[0], [_A, _B]),
    (NEST_ROWS[2], [_A, _BC]),
    (WORD_ROWS[0], [_A, f"for i in 1 2; do {_W}; cd /proc/self/fd; done"]),
    (STORE_ROWS[4], [_A, _CDW]),
]


@pytest.mark.parametrize("line,alone", TWINS)
def test_a_two_loop_line_decides_at_least_as_strictly_as_each_loop_alone(line, alone):
    c, singles = _bash(line), [_bash(x) for x in alone]
    for raw in _SPLIT + _builtin_policies():
        pol = gate.normalize_policies(raw)
        d = gate.decide(c, pol)
        for x, s in zip(alone, singles):
            e = gate.decide(s, pol)
            assert _RANK[d.outcome] >= _RANK[e.outcome], (line, x, raw, d.outcome, e.outcome)
            if d.outcome == e.outcome == "ask":
                assert {r for r in e.reserved} <= {r for r in d.reserved}, (line, x, raw)
    assert all(gate.no_grant(s) <= gate.no_grant(c) for s in singles), line


def test_a_long_line_of_loops_stays_within_the_budget_and_costs_about_one_more_reading(monkeypatch):
    # Twenty loops alternating /dev/fd and /proc/self/fd: the reading this change adds reads the line once more, so
    # the call costs about four first readings (main d742d1bb: about 2.6). CPU time, best of three.
    line = "; ".join([_A, _B] * 10)

    def cpu():
        best = None
        for _ in range(3):
            t = time.process_time()
            c = _bash(line)
            took = time.process_time() - t
            best = took if best is None else min(best, took)
        return best, c
    whole, c = cpu()
    assert {"/dev/fd", "/proc/self/fd"} <= _targets(c)
    assert not any("unread loop pass" in p.signals for r in gate.readings(c) for p in gate._each_part(r))
    monkeypatch.setattr(gate, "_loop_reading", lambda c, *a: c)
    first, _ = cpu()
    assert whole <= 6 * first + 0.3, (whole, first)


def _nest(d):
    if d == 0:
        return "cp /tmp/evil 3"
    return f"while :; do echo $({_nest(d - 1)}); cp /tmp/evil 3; cd {('/dev/fd', '~/.exo/policies')[d % 2]}; done"


def _phase_readings(monkeypatch):
    """Instrument _read_core: each call is recorded with the reading it belongs to, ((#158's reading: True for the
    reading as before #143, ``flat``), (#853's opened reading), (the joined form, #584)), and the call's _loop_pass
    note when it is a #479 or #867 reading (None otherwise)."""
    seen, real = [], gate._read_core

    def read(call, cwd=None):
        phase = (gate._flat_words.get(), gate._opening.get(), bool((gate._joining.get() or [False])[0]))
        try:
            return real(call, cwd)
        finally:
            note = gate._loop_pass.get()
            seen.append((phase, list(note) if note is not None else None))
    monkeypatch.setattr(gate, "_read_core", read)
    return seen


@pytest.mark.parametrize("before,phases", [
    # #158 reads the line two ways (its `${Y}` word, joined): bash's reading of the words, then `flat`
    ("", [(False, False), (True, False)]),
    # behind `time` (#853) each of the two also has its opened reading, in which the loops are read as without it
    ("time ", [(False, False), (False, True), (True, False), (True, True)]),
])
def test_the_joined_form_keeps_its_second_reading_when_only_the_867_reading_runs_past_the_budget(
        monkeypatch, before, phases):
    # A joined `$\<newline>` form (#584) beside a deep nest of loops into /dev/fd and the store, after a `cd /dev/fd`:
    # at some depth the second reading of the call stays within the budget and the #867 reading runs past it. The
    # joined call is then still read a second time, as on main d742d1bb, before its own #867 reading. Since #158 the
    # call is read twice (its words as bash reads them, and ``flat``), and behind `time` each of those has its #853
    # opened reading: each such reading makes its own readings, with budgets of its own, so the sequence is checked
    # in each, not as a count over the call.
    seen = _phase_readings(monkeypatch)
    hit = 0
    for d in range(6, 11):
        seen.clear()
        c = _bash("X=$\\\n{Y}; cd /dev/fd; " + before + _nest(d))
        order = []                       # the readings' phases (#158 reading, opened), in the order they were made
        for (flat, opened, _), _note in seen:
            if not order or order[-1] != (flat, opened):
                order.append((flat, opened))
        assert order == phases, (d, order)               # each phase is read once, in one run of readings
        past = []
        for phase in phases:
            mine = [(j, n) for (f, o, j), n in seen if (f, o) == phase]
            first = [(j, n) for j, n in mine if n is not None and not n[0]]
            # main's first reading of the call and of its joined form (#479 notes the loops), in that order
            assert [j for j, _ in first] == [False, True] and all(n[1] for _, n in first), (d, phase, mine)
            loop = [(j, n) for j, n in mine if n is not None and n[0]]
            # each second reading carries the #867 note (main d3cbd874 has none: its readings name one folder)
            assert loop and all(len(n) == 4 for _, n in loop), (d, phase, loop)
            # the #479 second reading (fourth item 0, or 1 where it met a loop named by another folder) and the
            # #867 reading (2) of the call
            assert loop[0][0] is False and loop[0][1][3] == 1 and loop[0][1][2] >= 0, (d, phase, loop)
            assert loop[1][0] is False and loop[1][1][3] == 2, (d, phase, loop)
            if loop[1][1][2] < 0:
                past.append(phase)       # only the #867 reading ran past the budget: the joined form is read still
            # the joined form's second reading, within the budget, and its own #867 reading, in each phase
            assert len(loop) == 4, (d, phase, loop)
            assert loop[2][0] is True and loop[2][1][3] in (0, 1) and loop[2][1][2] >= 0, (d, phase, loop)
            assert loop[3][0] is True and loop[3][1][3] == 2, (d, phase, loop)
        if past:
            hit += 1
            # with no `time`, both of #158's readings ran past at this depth; behind it, at least the opened one did
            assert past == phases if not before else any(o for _, o in past), (d, past)
            assert gate.decide(c).outcome == "ask" and gate.no_grant(c), d
            # the #867 reading fails closed past it as the #479 one does: one store write stands for the passes it
            # did not read (main's readings ask here without it, so the decision alone does not show it)
            assert any("unread loop pass" in p.signals for r in gate.readings(c) for p in gate._each_part(r)), d
            break
    assert hit, "no depth ran past the budget in the #867 reading only"


# The hook: under a split target rule the line is blocked, also in permissions bypass, where main d742d1bb asked.
class MockCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


def _gate(tmp_path, bypass, gates):
    from exo import principal
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "approvals.yaml").write_text('approvers:\n  default: [principal, "seat:builder"]\n')
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
    return ctx


def _run(ctx, line):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name="terminal", args={"command": line}, session_id="s1", tool_call_id="c1",
                          turn_id="t1", task_id="", author="principal:tui"))


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize("line,rule", [(ISSUE_ROWS[0], PROC), (ISSUE_ROWS[1], DEV), (WORD_ROWS[0], PROC)])
def test_the_hook_blocks_the_issue_rows_under_their_target_rule(tmp_path, bypass, line, rule):
    r = _run(_gate(tmp_path, bypass, f'deny: ["{rule}"]\n'), line)
    assert r is not None and r["action"] == "block", (line, r)
