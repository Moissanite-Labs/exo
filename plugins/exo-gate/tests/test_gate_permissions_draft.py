"""Session bypass needs principal consent and preserves all protected asks."""
import asyncio

import pytest

from exo import gate, principal
from exo.server.engine import CommandFailed
from exo_gate import hook, permissions as perm


class Sink:
    def __init__(self):
        self.rows = []
        self.fail = False

    def append(self, body, **kw):
        if self.fail:
            return None
        self.rows.append((body, kw))
        return "fixture-event"


@pytest.fixture
def g():
    out = hook.Gate(hook.Settings(principal_store=None, decision_model=False), sink=Sink())
    out._policies = gate.normalize_policies({"gates": {"categories": {"local_write": "ask"}}})
    out._policy_stamp = out._stamp() + out._scrub_stamp()
    return out


async def allow(session, summary, **kw):
    assert kw["options"] == ["allow", "deny"] and kw["approvers"] == ["principal"]
    assert kw["binding"]
    kw["decided_by"].append("principal:tui")
    kw["opened"].append("fixture-approval")
    return "allow"


def select(g, mode="bypass", requester=allow, sid="one"):
    token = principal.CALLER.set(principal.Caller("principal:tui"))
    try:
        return asyncio.run(hook.permissions_command(g, mode, sid, requester))
    finally:
        principal.CALLER.reset(token)


def test_session_consent_precedes_ordinary_bypass_and_new_session_stays_ask(g):
    assert g.pre_tool_call("write_file", {"path": "fixture.txt"}, session_id="one")["action"] == "approve"
    assert select(g).startswith("permissions: bypass")
    consent, meta = g.sink.rows[-1]
    assert consent["phase"] == "permissions" and consent["approved_by"] == "principal:tui"
    assert consent["approval_request"] == "fixture-approval" and meta["session"] == "one"
    assert g.pre_tool_call("write_file", {"path": "fixture.txt"}, session_id="one") is None
    row = g.sink.rows[-1][0]
    assert row["decided_by"] == "bypass" and row["args_sha256"]
    assert row["permission_mode"] == "bypass" and row["mode"] == "enforce"
    assert g.permissions_snapshot("one")["auto_approved"] == 1
    assert g.pre_tool_call("write_file", {"path": "fixture.txt"}, session_id="two")["action"] == "approve"
    assert g.permissions_snapshot()["session"] is None and "" not in g.permissions
    assert g.permissions_snapshot("two")["mode"] == "ask"
    select(g, "ask")
    assert g.pre_tool_call("write_file", {"path": "fixture.txt"}, session_id="one")["action"] == "approve"


@pytest.mark.parametrize("who", ["unknown", "local", "seat:helper", "peer:other", "bus:scout"])
def test_nonprincipal_cannot_select_mode(g, who):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        for mode in ("bypass", "ask"):
            with pytest.raises(CommandFailed, match="forbidden"):
                asyncio.run(hook.permissions_command(g, mode, "one", allow))
    finally:
        principal.CALLER.reset(token)
    assert g.permissions_snapshot("one")["mode"] == "ask" and not g.sink.rows


def test_deny_cancel_and_missing_approval_host_stay_ask(g):
    async def deny(*args, **kw):
        return "deny"
    async def cancel(*args, **kw):
        raise asyncio.CancelledError
    select(g, requester=deny)
    with pytest.raises(asyncio.CancelledError):
        select(g, requester=cancel)
    with pytest.raises(CommandFailed, match="cannot request"):
        select(g, requester=None)
    with pytest.raises(CommandFailed, match="requires a session"):
        select(g, sid="")
    assert g.permissions_snapshot("one")["mode"] == "ask" and not g.sink.rows


def test_consent_audit_failure_stays_ask_and_decision_audit_failure_blocks(g):
    g.sink.fail = True
    with pytest.raises(CommandFailed, match="consent could not be saved"):
        select(g)
    assert g.permissions_snapshot("one")["mode"] == "ask"
    g.sink.fail = False
    select(g)
    g.sink.fail = True
    assert g.pre_tool_call("write_file", {"path": "fixture.txt"}, session_id="one")["action"] == "block"
    with pytest.raises(CommandFailed):
        select(g, "ask")
    assert g.permissions_snapshot("one")["mode"] == "ask"  # tightening survives a failed audit


@pytest.mark.parametrize("change", ["required", "ask", "authority"])
def test_delayed_consent_rechecks_executor_revision_and_authority(g, change):
    async def changed(*args, **kw):
        result = await allow(*args, **kw)
        if change == "required":
            g.required_getter = lambda: True
        elif change == "ask":
            await g.set_permissions("ask", session_id="one")
        else:
            principal.CALLER.get().live = False
        return result
    with pytest.raises(CommandFailed):
        select(g, requester=changed)
    assert g.permissions_snapshot("one")["mode"] == "ask" and not g.sink.rows


@pytest.mark.parametrize("command", [
    "rm -rf fixture-output", "cat ~/.exo/keys/fixture.key", "echo fixture > $EXO_HOME/schedule.sqlite",
    "printf fixture && cat ~/.exo/keys/fixture.key", "git push origin fixture && rm -rf fixture-output",
    "curl -X POST https://example.invalid/items -d fixture",
])
def test_protected_real_classifications_still_ask(g, command):
    select(g)
    result = g.pre_tool_call("bash", {"command": command}, session_id="one")
    assert result and result["action"] in ("approve", "block")
    assert g.permissions_snapshot("one")["auto_approved"] == 0
    row = g.sink.rows[-1][0]
    assert row["permission_mode"] == "bypass" and row["outcome"] in ("ask", "deny")
    assert "decided_by" not in row


def test_model_ask_explicit_deny_reserved_and_changed_policy_are_not_lowered(g):
    select(g)
    args = {"path": "fixture.txt"}
    assert g.pre_tool_call("write_file", args, session_id="one", _verdict="ask")["action"] == "approve"
    for policy in [{"categories": {"local_write": "deny"}},
                   {"categories": {"local_write": "ask"}, "reserved": ["local_write"]},
                   {"categories": {"local_write": "ask"}, "escalate": {"categories": ["local_write"]}}]:
        g._policies = gate.normalize_policies({"gates": policy})
        assert g.pre_tool_call("write_file", args, session_id="one")["action"] in ("approve", "block")
    g._policies = gate.normalize_policies({"gates": {"categories": {"local_write": "ask"}}})
    g.changed_policies = lambda: gate.normalize_policies({})
    assert g.pre_tool_call("write_file", args, session_id="one")["action"] == "approve"


def test_seat_file_cannot_enable_bypass(g, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "seats").mkdir(parents=True)
    (home / "seats" / "fixture.toml").write_text('[permissions]\nmode = "bypass"\n')
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_SEAT", "fixture")
    assert g.permissions_snapshot("new")["mode"] == "ask" and not hasattr(perm, "seat_default")


def ordinary():
    return gate.Classification("local_write", "reversible", "private", False)


@pytest.mark.parametrize("field,value", [
    ("category", "credentials"), ("category", "store_write"), ("category", "opaque_exec"),
    ("category", "unknown"), ("reversibility", "irreversible"), ("exposure", "public"), ("third_party", True),
])
def test_protected_parts_cannot_ride_along_with_ordinary_ask(field, value):
    root, nested, protected = ordinary(), ordinary(), ordinary()
    setattr(protected, field, value)
    nested.parts = [protected]
    root.parts = [nested]
    assert perm.protected(root, gate.Decision("ask", "fixture ask", "category"))


@pytest.mark.parametrize("kwargs,verdict", [
    ({"reserved": [("vcs_merge", "fixture")]}, None), ({"violations": ["unseen body"]}, None),
    ({"rule": "floor"}, None), ({"rule": "budget"}, None), ({"rule": "opaque"}, None),
    ({"rule": "escalate"}, None), ({}, "ask"), ({}, "deny"),
])
def test_nonordinary_and_model_asks_stay_protected(kwargs, verdict):
    fields = {"outcome": "ask", "reason": "fixture ask", "rule": "category", **kwargs}
    assert perm.protected(ordinary(), gate.Decision(**fields), verdict)


def test_ordinary_eligibility_is_only_a_pure_check():
    c, d = ordinary(), gate.Decision("ask", "fixture ask", "category")
    assert not perm.protected(c, d)
    assert d.outcome == "ask"  # no decision or gate mode is changed by this scaffold


def test_explicit_deny_is_never_eligible():
    assert perm.protected(ordinary(), gate.Decision("deny", "policy deny", "category"))


def test_restart_discards_session_status(g):
    select(g)
    g.permission_state("one").auto_approved = 3
    restarted = hook.Gate(hook.Settings(principal_store=None), sink=Sink())
    assert restarted.permissions_snapshot("one")["mode"] == "ask"
    assert restarted.permissions_snapshot("one")["auto_approved"] == 0


@pytest.mark.parametrize("command", [
    "cat ~/.exo/keys/fixture.key", "echo fixture > $EXO_HOME/schedule.sqlite",
    "printf fixture && cat ~/.exo/keys/fixture.key", "git push origin fixture && rm -rf fixture-output",
])
def test_real_classifier_protected_paths_and_compounds_remain_ineligible(command):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    d = gate.decide(c)
    assert perm.protected(c, d)


@pytest.mark.parametrize("policy_kind", ["category", "rule"])
@pytest.mark.parametrize("tool,args,category", [
    ("execute_code", {"code": "print(1)"}, "code"),
    ("bash", {"command": 'echo $"fixture"'}, "read"),
    ("bash", {"command": 'echo fixture && echo $"fixture"'}, "read"),
])
def test_real_indirection_and_locale_signals_keep_equal_rank_asks(g, policy_kind, tool, args, category):
    gates = ({"categories": {category: "ask"}} if policy_kind == "category" else
             {"rules": [{"match": {"category": category}, "decision": "ask", "reason": "fixture rule"}]})
    g._policies = gate.normalize_policies({"gates": gates})
    c = gate.classify({"tool": tool, "args": args})
    d = gate.decide(c, g._policies)
    assert c.category == category and c.signals and d.outcome == "ask"
    assert d.rule == policy_kind  # equal-rank floor must survive despite this ordinary rule label
    assert perm.protected(c, d, policies=g._policies)
    root, nested = ordinary(), ordinary()
    nested.parts, root.parts = [c], [nested]
    assert perm.protected(root, gate.Decision("ask", "ordinary winner", "category"))
    select(g)
    assert g.pre_tool_call(tool, args, session_id="one")["action"] == "approve"
    assert g.sink.rows[-1][0]["permission_mode"] == "bypass"
    assert "decided_by" not in g.sink.rows[-1][0]


def test_unknown_future_signal_is_conservatively_protected():
    c = ordinary()
    c.signals.append("fixture-future-signal")
    assert perm.protected(c, gate.Decision("ask", "ordinary rule", "category"))
