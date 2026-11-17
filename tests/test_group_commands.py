"""The command.run name "group": principal-only human commands over this seat's groups.

The engine is a SimpleNamespace: the handlers touch only ``eng.agent`` (the seat
name, always the group owner) and ``eng.store`` (a SessionStore), nothing else.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from exo import group_commands, principal
from exo.core.group_messages import GroupMessages
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from exo.server.engine import CommandFailed


@pytest.fixture
def eng():
    store = SessionStore()
    engine = SimpleNamespace(agent="one", store=store)
    groups = GroupStore(store)
    group = groups.create(owner="one", mode="conductor", members=["lead", "one", "two"],
                          known_profiles=["lead", "one", "two"], conductor="lead")
    other = groups.create(owner="other", mode="conductor", members=["lead", "two"],
                          known_profiles=["lead", "two"], conductor="lead")
    GroupMessages(groups)  # create swarm_messages/swarm_delivery tables for the assertions below
    yield engine, group.id, other.id
    store.close()


def run(engine, args, who="principal"):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return asyncio.run(group_commands.command(engine, SimpleNamespace(args=args)))
    finally:
        principal.CALLER.reset(token)


def test_denial_local_and_channel(eng):
    engine, gid, _ = eng
    for who in ("local", "channel"):
        for args in ("list ''", f"inspect '{{\"id\": \"{gid}\"}}'",
                     f"send '{{\"id\": \"{gid}\", \"text\": \"hi\"}}'",
                     f"observe '{{\"id\": \"{gid}\"}}'"):
            with pytest.raises(CommandFailed) as e:
                run(engine, args.replace("''", "").replace("'", ""), who)
            assert e.value.code == "forbidden"
    # nothing reached the stores
    assert engine.store._q("SELECT * FROM swarm_messages") == []


def test_owner_isolation(eng):
    engine, gid, other_gid = eng
    assert [g["id"] for g in run(engine, "list")["groups"]] == [gid]
    with pytest.raises(CommandFailed, match="no (such )?group"):
        run(engine, f'inspect {{"id": "{other_gid}"}}')
    with pytest.raises(CommandFailed, match="no (such )?group"):
        run(engine, f'observe {{"id": "{other_gid}"}}')
    with pytest.raises(CommandFailed, match="no (such )?group"):
        run(engine, f'send {{"id": "{other_gid}", "text": "hi"}}')


def test_send_human_and_observe(eng):
    engine, gid, _ = eng
    sent = run(engine, f'send {{"id": "{gid}", "text": "change direction", "audience": ["two"]}}')
    assert sent["sender"] == "one" and sent["sender_kind"] == "human"
    assert sent["audience"] == ["two"]
    seen = run(engine, f'observe {{"id": "{gid}"}}')
    assert [m["id"] for m in seen["messages"]] == [sent["id"]]
    assert seen["next_after"] == sent["seq"]
    # member history
    hist = run(engine, f'observe {{"id": "{gid}", "member": "two"}}')
    assert [m["id"] for m in hist["messages"]] == [sent["id"]]
    assert run(engine, f'observe {{"id": "{gid}", "member": "lead"}}')["messages"] == []


def test_observe_is_read_only_no_ack(eng):
    engine, gid, _ = eng
    run(engine, f'send {{"id": "{gid}", "text": "hi"}}')
    run(engine, f'observe {{"id": "{gid}"}}')
    run(engine, f'observe {{"id": "{gid}", "member": "lead"}}')
    assert [(r["delivered"], r["consumed"]) for r in engine.store._q("SELECT * FROM swarm_delivery")] == [(0, 0)] * 3


def test_inspect(eng):
    engine, gid, _ = eng
    out = run(engine, f'inspect {{"id": "{gid}"}}')
    assert out["id"] == gid and out["owner"] == "one" and out["conductor"] == "lead"
    assert [m["profile"] for m in out["members"]] == ["lead", "one", "two"]


def test_broadcast_default_audience(eng):
    engine, gid, _ = eng
    sent = run(engine, f'send {{"id": "{gid}", "text": "pause"}}')
    assert sent["audience"] == ["lead", "one", "two"]


@pytest.mark.parametrize("args", [
    f'observe {{"id": "{0}"}}',                      # id not a string
    'observe {"id": "  "}',                          # id blank
    'observe {"id": 1}',                             # id not a string
    'observe {"id": "x", "after": -1}',              # after negative
    'observe {"id": "x", "after": true}',            # bool is not an int
    'observe {"id": "x", "limit": 0}',               # limit must be positive
    'observe {"id": "x", "limit": 1001}',            # limit bounded
    'observe {"id": "x", "limit": false}',           # bool is not an int
    'observe {"id": "x", "member": 3}',              # member not a string
    'observe {"id": "x", "nope": 1}',                # unknown key
    'observe {}',                                    # id required
    'inspect {"id": "x", "extra": 1}',               # unknown key
    'send {"id": "x"}',                              # text required
    'send {"id": "x", "text": "t", "wat": 1}',       # unknown key
    'list {"verbose": true}',                        # list takes no keys
    'frobnicate {}',                                 # unknown op
])
def test_malformed_args(eng, args):
    engine, gid, _ = eng
    args = args.replace('"x"', f'"{gid}"').replace('{"id": "0"}', f'{{"id": {gid!r}}}')
    with pytest.raises(CommandFailed) as e:
        run(engine, args)
    assert e.value.code == "bad_request"
    assert engine.store._q("SELECT * FROM swarm_messages") == []


def test_bad_json_and_non_object(eng):
    engine, gid, _ = eng
    for args in ("observe {nope", "observe [1, 2]", "observe \"s\""):
        with pytest.raises(CommandFailed, match="JSON|json|object"):
            run(engine, args)


@pytest.mark.parametrize("member", [None, "two", "one"])
def test_empty_observation_page_keeps_requested_cursor(eng, member):
    engine, gid, _ = eng
    sent = run(engine, f'send {{"id": "{gid}", "text": "hi", "audience": ["two"]}}')
    args = {"id": gid, "after": sent["seq"], "limit": 1}
    if member is not None:
        args["member"] = member
    for _ in range(2):
        page = run(engine, "observe " + json.dumps(args))
        assert page == {"messages": [], "next_after": sent["seq"]}
        args["after"] = page["next_after"]


@pytest.mark.parametrize("prefix", ["  list", "list\t{}", "\nlist  {}\t", "list\n{}"])
def test_command_whitespace(eng, prefix):
    engine, gid, _ = eng
    assert [g["id"] for g in run(engine, prefix)["groups"]] == [gid]


@pytest.mark.parametrize("args", ["", "inspect {bad"])
def test_usage_does_not_suggest_shell_quotes(eng, args):
    engine, _, _ = eng
    with pytest.raises(CommandFailed) as e:
        run(engine, args)
    assert e.value.code == "bad_request"
    assert "usage: /group" in e.value.message
    assert "'<json" not in e.value.message


def test_nested_json_is_bad_request_and_does_not_poison_next_call(eng):
    engine, gid, _ = eng
    with pytest.raises(CommandFailed) as e:
        run(engine, "observe " + "[" * 100000)
    assert e.value.code == "bad_request"
    assert [g["id"] for g in run(engine, "list")["groups"]] == [gid]
    assert engine.store._q("SELECT * FROM swarm_messages") == []


def test_json_integer_conversion_failure_is_bad_request(eng):
    engine, gid, _ = eng
    with pytest.raises(CommandFailed) as e:
        run(engine, 'observe {"id": ' + '9' * 5000 + '}')
    assert e.value.code == "bad_request"
    assert [g["id"] for g in run(engine, "list")["groups"]] == [gid]


@pytest.mark.parametrize("who,allowed", [
    ("principal:tui", True), ("principal", True), ("local", False),
    ("unknown", False), ("seat:helper", False), ("channel", False), ("principalx", False),
])
def test_real_dispatcher_principal_gate_and_owner_boundary(tmp_path, who, allowed):
    from exo.core.engine import CoreEngine
    from exo.core.plugins import PluginManager
    from exo.core.seats import Seat
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    async def scenario():
        engine = CoreEngine(tmp_path / "home", {"fake": {"rules": []}, "roles": {"main": ["fake:x"]}},
                            plugins=PluginManager(), cwd=str(tmp_path), env={}, seat=Seat(name="one"))
        try:
            groups = GroupStore(engine.store)
            group = groups.create(owner="one", mode="socratic", members=["one", "two"],
                                  known_profiles=["one", "two"])
            foreign = groups.create(owner="other", mode="socratic", members=["one", "two"],
                                    known_profiles=["one", "two"])
            dispatcher = Dispatcher(engine, caller=who)
            async def invoke(op, fields=None):
                args = op + (" " + json.dumps(fields) if fields is not None else "")
                return await asyncio.wait_for(dispatcher.run(s.Command.of(s.CommandRun(name="group", args=args))), 2)
            if not allowed:
                for op, fields in [("list", None), ("inspect", {"id": group.id}),
                                   ("send", {"id": group.id, "text": "hi"}),
                                   ("observe", {"id": group.id})]:
                    got = await invoke(op, fields)
                    assert not got.ok and got.error.code == "forbidden"
                nested = await asyncio.wait_for(dispatcher.run(s.Command.of(
                    s.CommandRun(name="group", args="observe " + "[" * 100000))), 2)
                assert not nested.ok and nested.error.code == "forbidden"
                huge = await asyncio.wait_for(dispatcher.run(s.Command.of(s.CommandRun(
                    name="group", args='observe {"id": ' + '9' * 5000 + '}'))), 2)
                assert not huge.ok and huge.error.code == "forbidden"
                assert engine.store._q("SELECT name FROM sqlite_master WHERE name IN "
                                       "('swarm_messages','swarm_delivery')") == []
                return
            listed = await invoke("list")
            assert listed.ok, listed.error
            assert [g["id"] for g in listed.result["output"]["groups"]] == [group.id]
            inspected = await invoke("inspect", {"id": group.id})
            assert inspected.ok and inspected.result["output"]["owner"] == "one"
            sent = await invoke("send", {"id": group.id, "text": "hi", "audience": ["two"]})
            assert sent.ok, sent.error
            message = sent.result["output"]
            assert message["sender"] == "one" and message["sender_kind"] == "human"
            seen = await invoke("observe", {"id": group.id})
            assert seen.ok and [m["id"] for m in seen.result["output"]["messages"]] == [message["id"]]
            empty = await invoke("observe", {"id": group.id, "after": message["seq"]})
            assert empty.ok and empty.result["output"] == {"messages": [], "next_after": message["seq"]}
            hidden = await invoke("observe", {"id": group.id, "member": "one"})
            assert hidden.ok and hidden.result["output"]["messages"] == []
            assert [(r["delivered"], r["consumed"]) for r in engine.store._q(
                "SELECT * FROM swarm_delivery")] == [(0, 0)]
            for op, fields in [("inspect", {"id": foreign.id}), ("observe", {"id": foreign.id}),
                               ("send", {"id": foreign.id, "text": "no"})]:
                denied = await invoke(op, fields)
                assert not denied.ok and denied.error.code == "bad_request"
            nested = await asyncio.wait_for(dispatcher.run(s.Command.of(
                s.CommandRun(name="group", args="observe " + "[" * 100000))), 2)
            assert not nested.ok and nested.error.code == "bad_request"
            huge = await asyncio.wait_for(dispatcher.run(s.Command.of(s.CommandRun(
                name="group", args='observe {"id": ' + '9' * 5000 + '}'))), 2)
            assert not huge.ok and huge.error.code == "bad_request"
            assert (await invoke("list")).ok
            assert len(engine.store._q("SELECT * FROM swarm_messages")) == 1
        finally:
            await engine.close()
    asyncio.run(asyncio.wait_for(scenario(), 10))
