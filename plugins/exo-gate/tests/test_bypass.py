"""Permissions bypass (D-065): what it auto-approves, what it never does, who can switch it, and how long it lasts."""
import json

import pytest

from exo import crypto, principal
from exo.ledger import Ledger
from exo.server.engine import CommandFailed
from exo_gate import hook
from exo_gate import permissions as perm
from test_hook import FOOT, call, make_gate

RISKY = [
    ("terminal", {"command": f'gh pr comment 5 --repo octo/demo --body "Looks good.\n\n{FOOT}"'}),
    ("terminal", {"command": "rm -rf build"}),
    ("terminal", {"command": "curl -X POST https://api.example.com/v1/items -d '{\"a\": 1}'"}),
]
FLOOR = [
    ("write_file", {"path": "policies/gates.yaml", "content": "decision: act"}),
    ("terminal", {"command": "echo x >> $EXO_HOME/policies/gates.yaml"}),
    ("terminal", {"command": "sqlite3 ledger.sqlite 'delete from events'"}),
    ("terminal", {"command": "cat ~/.exo/keys/exo-gate.key"}),
    # #651: a read-named tool that replaces text writes the store file it names, so its floor ask holds in
    # bypass too: the seat file, a policy file and config.json, snake and camel spellings
    ("mcp__fs__search_replace", {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}),
    ("mcp__fs__searchReplace", {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}),
    ("search_replace", {"path": "~/.exo/policies/gates.yaml", "search": "ask", "replace": "bypass"}),
    ("findAndReplace", {"path": "~/.exo/policies/gates.yaml", "oldString": "ask", "newString": "bypass"}),
    ("find_and_replace", {"path": "~/.exo/config.json", "old_string": '"ask"', "new_string": '"bypass"'}),
]


@pytest.fixture
def keyed_store(example_store, tmp_path):
    key = tmp_path / "keys" / "exo-gate.key"
    crypto.save_key(crypto.keygen(), key)
    return example_store, key


@pytest.fixture
def as_caller(request):
    def stamp(who):
        token = principal.CALLER.set(principal.Caller(who))
        request.addfinalizer(lambda: principal.CALLER.reset(token))
    return stamp


def rows(store, phase="decision"):
    with Ledger(store / "ledger.sqlite") as led:
        return [e["body"] for e in led.iter(kind="action") if e["body"].get("phase") == phase]


def bypassed(keyed_store, as_caller):
    store, key = keyed_store
    g, ctx = make_gate(store, key, decision_model=False)
    as_caller("principal:tui")
    hook.permissions_command(g, "bypass")
    return g, ctx, store


@pytest.mark.parametrize("tool,args", RISKY)
def test_risky_calls_ask_without_bypass(keyed_store, tool, args):
    store, key = keyed_store
    _, ctx = make_gate(store, key, decision_model=False)
    assert call(ctx, tool, args)["action"] == "approve"


@pytest.mark.parametrize("tool,args", RISKY)
def test_bypass_runs_risky_calls_and_logs_them_as_bypass(keyed_store, as_caller, tool, args):
    g, ctx, store = bypassed(keyed_store, as_caller)
    assert call(ctx, tool, args) is None
    [row] = rows(store)
    assert row["outcome"] == "allow" and row["decided_by"] == "bypass"
    assert row["decision"]["rule"] == "bypass" and row["bypass"]["would"] == "ask"
    assert row["call"]["tool"] == tool and json.loads(row["call"]["args"]) == args  # the exact call
    assert g.permissions.auto_approved == 1


@pytest.mark.parametrize("author", ["local", "peer:mallory", "bus:scout", "job", "schedule", "delegate", "agent"])
def test_bypass_lowers_no_turn_started_by_anyone_else(keyed_store, as_caller, author):
    """#598: the principal consented to skip asking in their own turns. A turn a peer, a bus wake, a job notice,
    a schedule, a delegate or a local caller started in the same core runs its asks as if bypass were off."""
    g, ctx, store = bypassed(keyed_store, as_caller)
    r = call(ctx, *RISKY[1], author=author)
    assert r["action"] == "approve" and rows(store)[0]["outcome"] == "ask"
    assert "decided_by" not in rows(store)[0]
    assert g.permissions.auto_approved == 0


@pytest.mark.parametrize("author", ["principal", "principal:web"])
def test_bypass_lowers_every_label_of_the_principal(keyed_store, as_caller, author):
    g, ctx, store = bypassed(keyed_store, as_caller)
    assert call(ctx, *RISKY[1], author=author) is None
    [row] = rows(store)
    assert row["decided_by"] == "bypass" and row["turn_author"] == author
    assert g.permissions.auto_approved == 1


def test_a_hook_with_no_author_keeps_the_ask(keyed_store, as_caller):
    """An older core passes no author: the gate cannot say whose turn it is, so it keeps the card."""
    g, ctx, store = bypassed(keyed_store, as_caller)
    assert call(ctx, *RISKY[1], author="")["action"] == "approve"
    assert g.permissions.auto_approved == 0 and rows(store)[0]["outcome"] == "ask"


@pytest.mark.parametrize("tool,args", FLOOR)
def test_store_writes_and_secret_reads_still_ask_in_bypass(keyed_store, as_caller, tool, args):
    g, ctx, store = bypassed(keyed_store, as_caller)
    r = call(ctx, tool, args)
    assert r["action"] == "approve" and r.get("once") is True
    [row] = rows(store)
    assert row["outcome"] == "ask" and "decided_by" not in row and row["bypass"]["floor"] in perm.FLOOR
    assert g.permissions.auto_approved == 0 and g.permissions.floor_asks == 1


def test_deny_rules_still_block_in_bypass(keyed_store, as_caller):
    _, ctx, store = bypassed(keyed_store, as_caller)
    r = call(ctx, "terminal", {"command": "git push --force origin main"})
    assert r["action"] == "block"
    assert rows(store)[0]["outcome"] == "deny"


def test_a_post_that_breaks_the_posting_rules_is_held_with_the_fix_not_asked(keyed_store, as_caller):
    g, ctx, store = bypassed(keyed_store, as_caller)
    r = call(ctx, "terminal", {"command": 'gh pr comment 5 --repo octo/demo --body "hello"'})
    assert r["action"] == "block" and "Fix: missing footer" in r["message"]
    [row] = rows(store)
    assert row["outcome"] == "deny" and row["decided_by"] == "bypass" and row["decision"]["rule"] == "bypass_hold"
    assert g.permissions.held == 1 and g.permissions.auto_approved == 0


def test_secrets_in_an_auto_approved_call_are_redacted_in_the_ledger(keyed_store, as_caller):
    _, ctx, store = bypassed(keyed_store, as_caller)
    tok = "ghp_" + "a1B2c3D4e5" * 4
    assert call(ctx, "terminal", {"command": f"curl -X POST -H 'Authorization: token {tok}' https://api.example.com/x"}) is None
    [row] = rows(store)
    assert tok not in json.dumps(row) and row["call"]["redacted"] >= 1 and row["args_sha256"]


@pytest.mark.parametrize("who", ["unknown", "local", "seat:helper", "peer:other", "bus:scout", "tool", "agent"])
def test_only_a_principal_connection_can_turn_bypass_on(keyed_store, as_caller, who):
    store, key = keyed_store
    g, ctx = make_gate(store, key, decision_model=False)
    as_caller(who)
    with pytest.raises(CommandFailed, match="forbidden"):
        hook.permissions_command(g, "bypass")
    with pytest.raises(CommandFailed, match="forbidden"):
        g.set_permissions("bypass", "launch flag")
    assert not g.bypass_on and rows(store, "permissions") == []
    assert call(ctx, *RISKY[1])["action"] == "approve"


def test_unstamped_direct_call_cannot_turn_bypass_on(keyed_store):
    store, key = keyed_store
    g, _ = make_gate(store, key, decision_model=False)
    with pytest.raises(CommandFailed, match="forbidden"):
        g.set_permissions("bypass")
    assert not g.bypass_on


def test_the_switch_is_recorded_and_anyone_may_switch_back_to_ask(keyed_store, as_caller):
    g, ctx, store = bypassed(keyed_store, as_caller)
    [on] = rows(store, "permissions")
    assert on["mode"] == "bypass" and on["by"] == "principal:tui" and on["via"] == "/permissions"
    as_caller("local")  # going back to ask only tightens
    assert hook.permissions_command(g, "ask").startswith("permissions: ask")
    assert not g.bypass_on and rows(store, "permissions")[-1]["by"] == "local"
    assert call(ctx, *RISKY[1])["action"] == "approve"


def test_status_counts_the_calls_bypass_auto_approved(keyed_store, as_caller):
    g, ctx, _ = bypassed(keyed_store, as_caller)
    for tool, args in RISKY:
        call(ctx, tool, args)
    call(ctx, *FLOOR[0])
    text = hook.permissions_command(g, "")
    assert text.startswith("permissions: bypass") and "auto-approved 3 calls this session" in text
    assert "except changes to Exo's own records and reading secrets" in text
    snap = hook.permissions_command(g, "json")
    assert snap["mode"] == "bypass" and snap["auto_approved"] == 3 and snap["floor_asks"] == 1
    assert snap["via"] == "/permissions" and snap["floor"] == ["credentials", "store_write"]


def test_launch_flag_is_recorded_as_such(keyed_store, as_caller):
    store, key = keyed_store
    g, _ = make_gate(store, key, decision_model=False)
    as_caller("principal:tui")
    hook.permissions_command(g, "bypass launch")
    assert g.bypass_on and rows(store, "permissions")[0]["via"] == "launch flag"
    assert hook.permissions_command(g, "bypass now").startswith("usage")


def test_required_executor_refuses_bypass(keyed_store, as_caller):
    store, key = keyed_store
    g, ctx = make_gate(store, key, decision_model=False)
    g.required_getter = lambda: True
    as_caller("principal:tui")
    with pytest.raises(CommandFailed, match="required tool executor"):
        hook.permissions_command(g, "bypass")
    assert not g.bypass_on and g.permissions_snapshot()["available"] is False
    assert "not available" in hook.permissions_command(g, "")
    assert call(ctx, *RISKY[1])["action"] == "approve"


def test_bypass_switched_on_before_required_mode_is_inert(keyed_store, as_caller):
    g, ctx, _ = bypassed(keyed_store, as_caller)
    g.required_getter = lambda: True
    assert not g.bypass_on and g.permissions_snapshot()["mode"] == "ask"
    assert call(ctx, *RISKY[1])["action"] == "approve"


def test_bypass_does_not_survive_a_restart(keyed_store, as_caller):
    g, ctx, store = bypassed(keyed_store, as_caller)
    assert call(ctx, *RISKY[1]) is None
    g2, ctx2 = make_gate(*keyed_store, decision_model=False)  # the core started again, no flag
    assert not g2.bypass_on and g2.permissions.auto_approved == 0
    assert call(ctx2, *RISKY[1])["action"] == "approve"


def _seat(home, body):
    (home / "seats").mkdir(parents=True, exist_ok=True)
    (home / "seats" / "builder.toml").write_text(body)


@pytest.mark.parametrize("body,on", [('[permissions]\nmode = "bypass"\n', True), ('[permissions]\nmode = "ask"\n', False),
                                     ("name = \"builder\"\n", False), ('permissions = "bypass"\n', False)])
def test_seat_default_is_read_once_at_start(keyed_store, tmp_path, monkeypatch, body, on):
    home = tmp_path / "exo-home"
    _seat(home, body)
    monkeypatch.setenv("EXO_SEAT", "builder")
    g, ctx = make_gate(*keyed_store, decision_model=False)
    assert g.bypass_on is on
    if on:
        assert rows(keyed_store[0], "permissions")[0]["via"] == "seat default"
        assert call(ctx, *RISKY[1]) is None
    _seat(home, '[permissions]\nmode = "ask"\n' if on else '[permissions]\nmode = "bypass"\n')
    assert g.bypass_on is on  # an edit later does nothing until the next start


def test_seat_default_is_refused_under_a_required_executor(keyed_store, tmp_path, monkeypatch):
    _seat(tmp_path / "exo-home", '[permissions]\nmode = "bypass"\n')
    monkeypatch.setenv("EXO_SEAT", "builder")
    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    g, _ = make_gate(*keyed_store, decision_model=False)
    assert not g.bypass_on


def test_the_seat_file_itself_is_on_the_floor_so_bypass_cannot_make_itself_the_default(keyed_store, as_caller):
    _, ctx, _ = bypassed(keyed_store, as_caller)
    for tool, args in [("write_file", {"path": "~/.exo/seats/builder.toml", "content": "[permissions]\nmode='bypass'"}),
                       ("terminal", {"command": "echo '[permissions]' >> $EXO_HOME/seats/builder/seat.toml"})]:
        assert call(ctx, tool, args)["action"] == "approve"


def test_permissions_command_is_registered():
    class Ctx:
        def __init__(self):
            self.hooks, self.commands = {}, {}

        def get_config(self, key, default=None):
            return {"decision_model": False}.get(key, default)

        def register_hook(self, name, cb):
            self.hooks.setdefault(name, []).append(cb)

        def register_command(self, name, handler, description="", args_hint=""):
            self.commands[name] = (handler, description, args_hint)

    ctx = Ctx()
    hook.register(ctx)
    handler, desc, hint = ctx.commands["permissions"]
    assert "except changes to Exo's own records and reading secrets" in desc and hint == "[status] | full <session-id|all> | bypass | ask"
    assert handler("json")["mode"] == "ask"
