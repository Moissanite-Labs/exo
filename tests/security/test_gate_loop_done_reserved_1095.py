"""Issue #1095: a reserved word right after `done`, `fi`, `esac` or `}`, with no `;` between, is read as after `;`.

bash ends a command at a compound's closer, so it takes `done }`, `done fi`, `done done`, `done esac`, `done then`,
`done else`, `done elif`, `fi done` and `} done` as it takes them with a `;` between (`bash -n` parses every line here
both ways, and `{ for i in 1 2; do cd /; done }; pwd` prints `/`). The gate kept such a closer and the word after it
in one command, and #479's loop reading looks for a command that ends with `done`. So a loop whose second pass writes
the store was missed when another closer followed its `done`: `{ for i in 1 2; do cp /tmp/evil gates.yaml;
cd ~/.exo/policies; done }` was an allowed read, also under `deny: [store_write]`, where its `done; }` form asks as a
store write and denies there. A pipe into a loop or an `if` right before such a word did not reach the commands in it
either, so `{ cat cmds | while read l; do ssh h; done }` was an allowed `shell` line. _parse_line now ends the command
at the closer before such a word, as at a `;`. Every command string here is data for exo.gate.classify/decide and the
exo-gate hook; none is run.
"""
import asyncio
import sys
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


def _reading(line):
    c = _bash(line)
    return (c.category, gate.decide(c).outcome, gate.decide(c, DENY).outcome, gate.decide(c, RESERVED).outcome,
            gate.no_grant(c), gate.binds_exact(c), _store_write_part(c))


def _forms(row):
    """The line without the `;` and with it: `@` marks where the `;` goes."""
    return row.replace("@", ""), row.replace("@", ";")


L = "for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done@"
B = "cp /tmp/evil gates.yaml; cd ~/.exo/policies"
NEST = "for a in 1 2; do for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done@"

# The issue's lines. On main b6670ab8 the first form of each is an allowed read, also under `deny: [store_write]`.
ISSUE_ROWS = [
    "{ " + L + " }",
    "if true; then " + L + " fi",
]
# The other words bash takes there, and the issue's forms inside an `if`, a group, a subshell and a function. On main
# b6670ab8 each first form is an allowed read or asks as opaque_exec, and none denies under `deny: [store_write]`.
MORE_ROWS = [
    # done done: nested loops, the inner one closed right before the outer one's `done`
    "{ " + NEST + " done@ }",
    "if true; then " + NEST + " done@ fi",
    "( if true; then " + NEST + " done@ fi )",
    "f() { " + NEST + " done@ }",
    # done esac
    "case x in x) " + L + " esac",
    "case x in (x) " + L + " esac",
    "{ case x in x) " + L + " esac }",
    "if true; then case x in x) " + L + " esac fi",
    "( case x in x) " + L + " esac )",
    "f() { case x in x) " + L + " esac }",
    # done then, done else, done elif
    "if " + L + " then :; fi",
    "if true; then " + L + " else :; fi",
    "if true; then " + L + " elif true; then :; fi",
    "if false; then :; elif " + L + " then :; fi",
    # the issue's forms inside an `if`, a group, a subshell and a function
    "if true; then { " + L + " } fi",
    "{ if true; then " + L + " fi }",
    "{ { " + L + " } }",
    "( { " + L + " } )",
    "( if true; then " + L + " fi )",
    "f() { " + L + " }",
    "function f { " + L + " }",
    "f() { if true; then " + L + " fi }",
    # a closer that ends the line before an operator or a pipe
    "{ " + L + " } && true",
    "{ " + L + " } | cat",
    # fi done and } done: a closer right before the loop's own `done`
    "if true; then for i in 1 2; do " + B + "; if true; then :; fi@ done@ fi",
    "if true; then for i in 1 2; do " + B + "; { :; }@ done@ fi",
]


@pytest.mark.parametrize("row", ISSUE_ROWS)
def test_the_issue_rows_are_store_writes_as_their_semicolon_forms(row):
    plain, semi = _forms(row)
    for line in (plain, semi):
        c = _bash(line)
        assert c.category == "store_write" and _store_write_part(c), (line, c.category)
        assert gate.decide(c).outcome == "ask", line
        assert gate.decide(c, DENY).outcome == "deny", line
        assert gate.no_grant(c) and gate.binds_exact(c), line
    assert _reading(plain) == _reading(semi), plain


@pytest.mark.parametrize("row", MORE_ROWS)
def test_a_reserved_word_after_a_closer_is_read_as_after_a_semicolon(row):
    plain, semi = _forms(row)
    got = _reading(plain)
    assert got == _reading(semi), (plain, got, _reading(semi))
    assert got[2] == "deny" and got[4] and got[6], (plain, got)     # deny: [store_write] denies, no grant, the write


# A pipe into a loop, an `if` or a `for` right before such a word reaches the commands inside it, as with the `;`:
# ssh there runs a stdin the gate cannot read (#316). On main b6670ab8 each first form is an allowed `shell` line.
FEED_ROWS = [
    "{ cat cmds | while read l; do ssh h; done@ }",
    "if true; then cat cmds | if true; then ssh h; fi@ fi",
    "for a in 1; do cat cmds | for i in 1; do ssh h; done@ done",
]


@pytest.mark.parametrize("row", FEED_ROWS)
def test_a_pipe_into_a_compound_before_such_a_word_reaches_its_commands(row):
    plain, semi = _forms(row)
    c = _bash(plain)
    assert (c.category, gate.decide(c).outcome) == ("opaque_exec", "ask"), (plain, c.category)
    assert _reading(plain) == _reading(semi), plain


# The hook: the issue's lines ask the principal, and permissions bypass keeps the store floor.
class MockCtx:
    def __init__(self, settings):
        self.settings = settings
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


def _gate(tmp_path, bypass):
    root = tmp_path / "store"
    (root / "policies").mkdir(parents=True)
    (root / "policies" / "approvals.yaml").write_text('approvers:\n  default: [principal, "seat:builder"]\n')
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
@pytest.mark.parametrize("row", ISSUE_ROWS)
def test_the_hook_asks_the_principal_and_bypass_keeps_the_floor(tmp_path, bypass, row):
    line, _ = _forms(row)
    g, ctx, root = _gate(tmp_path, bypass)
    r = _run(ctx, line)
    assert r is not None and r["action"] == "approve" and r["approvers"] == ["principal"], (line, r)
    assert r.get("once") is True and r.get("binding"), r
    if bypass:
        with Ledger(root / "ledger.sqlite") as led:
            [entry] = [e["body"] for e in led.iter(kind="action") if e["body"].get("phase") == "decision"]
        assert entry["outcome"] == "ask" and entry["bypass"] == {"floor": "store_write"}, entry
        assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1
