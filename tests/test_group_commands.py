"""The command.run name "group": principal-only human commands over this seat's groups.

The engine is a SimpleNamespace: the handlers touch only ``eng.agent`` (the seat
name, always the group owner) and ``eng.store`` (a SessionStore), nothing else.
"""
import asyncio
from types import SimpleNamespace

import pytest

from exo import principal
from exo.core import group_commands
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
