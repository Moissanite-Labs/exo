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


# --- send routing through an injected GroupHost (wakes a running conversation) ---

import json  # noqa: E402

from exo.core.group_host import ConversationOptions, GroupHost, HostError  # noqa: E402

PROFILES = ["lead", "one", "two"]


class SpyEngine:
    def __init__(self, profile, store):
        self.agent, self.store, self.turns, self._done = profile, store, [], {}

    def turn_done(self, index) -> asyncio.Event:
        return self._done.setdefault(index, asyncio.Event())

    async def run_turn(self, command):
        self.turns.append(command)
        self.turn_done(len(self.turns)).set()
        await asyncio.sleep(0)
        return SimpleNamespace(stop_reason="end", error=None,
                               message=SimpleNamespace(text=lambda: "ack"))


def make_host(store, owner="one", engines=None):
    engines = {} if engines is None else engines

    def factory(group):
        return {m.profile: engines.setdefault(m.profile, SpyEngine(m.profile, store))
                for m in group.members}
    return GroupHost(GroupStore(store), owner=owner, known_profiles=PROFILES, engine_factory=factory)


async def arun(engine, args, who="principal"):
    token = principal.CALLER.set(principal.Caller(who))
    try:
        return await group_commands.command(engine, SimpleNamespace(args=args))
    finally:
        principal.CALLER.reset(token)


def rows(store):
    return store._q("SELECT id, sender_kind FROM swarm_messages")


def test_send_via_host_wakes_running_conversation(eng):
    engine, gid, _ = eng
    engines = {}
    host = make_host(engine.store, engines=engines)
    engine._group_host = host

    async def scenario():
        host.start(gid, conversation=ConversationOptions(max_turns=3, idle_timeout=60))
        wake = host._wakes[gid]
        event = wake._events["two"]
        for _ in range(1000):  # park: member consumed its initial wake (scheduling yields only)
            if not event.is_set():
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("member never idled")
        assert all(not e.turns for e in engines.values())

        sent = await arun(engine, f'send {{"id": "{gid}", "text": "hello", "audience": ["two"]}}')
        assert event.is_set()  # the command itself signalled the wake; no idle poll (timeout 60s)
        await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
        prompt = engines["two"].turns[0].text
        assert json.loads(prompt.split("\n", 1)[1]) == [
            {"sender": "one", "sender_kind": "human", "text": "hello"}]
        assert sent["sender"] == "one" and sent["audience"] == ["two"]
        assert [r["id"] for r in rows(engine.store) if r["sender_kind"] == "human"] == [sent["id"]]
        host.stop(gid)
        await asyncio.wait_for(host.wait(gid), 30)

    asyncio.run(scenario())


def test_send_via_host_not_started_persists_once_without_wake(eng):
    engine, gid, _ = eng
    engine._group_host = host = make_host(engine.store)
    sent = run(engine, f'send {{"id": "{gid}", "text": "early"}}')
    assert sent["audience"] == ["lead", "one", "two"]
    assert [r["id"] for r in rows(engine.store)] == [sent["id"]]
    assert gid not in host._wakes


def test_send_without_host_unchanged(eng):
    engine, gid, _ = eng
    assert getattr(engine, "_group_host", None) is None
    sent = run(engine, f'send {{"id": "{gid}", "text": "hi", "audience": ["lead"]}}')
    assert [r["id"] for r in rows(engine.store)] == [sent["id"]]
    assert sent["audience"] == ["lead"]


def _forbid_send(host, calls):
    def send(*a, **k):
        calls.append((a, k))
        raise AssertionError("incompatible host must not be called")
    host.send = send


def test_foreign_owner_host_not_called_direct_path_used(eng):
    engine, gid, _ = eng
    host, calls = make_host(engine.store, owner="other"), []
    _forbid_send(host, calls)
    engine._group_host = host
    sent = run(engine, f'send {{"id": "{gid}", "text": "hi"}}')
    assert calls == [] and [r["id"] for r in rows(engine.store)] == [sent["id"]]
    # and no new authority: the other owner's group is still invisible
    with pytest.raises(CommandFailed, match="no (such )?group"):
        run(engine, f'send {{"id": "{eng[2]}", "text": "hi"}}')
    assert calls == [] and len(rows(engine.store)) == 1


def test_foreign_store_host_not_called_direct_path_used(eng):
    engine, gid, _ = eng
    other_store = SessionStore()
    try:
        host, calls = make_host(other_store, owner="one"), []
        _forbid_send(host, calls)
        engine._group_host = host
        sent = run(engine, f'send {{"id": "{gid}", "text": "hi"}}')
        assert calls == [] and [r["id"] for r in rows(engine.store)] == [sent["id"]]
        assert other_store._q("SELECT * FROM swarm_messages") == []  # host's store untouched
    finally:
        other_store.close()


def test_non_host_object_not_called(eng):
    engine, gid, _ = eng
    calls = []
    engine._group_host = SimpleNamespace(owner="one", send=lambda *a, **k: calls.append(1))
    sent = run(engine, f'send {{"id": "{gid}", "text": "hi"}}')
    assert calls == [] and [r["id"] for r in rows(engine.store)] == [sent["id"]]


def test_host_refusal_is_bad_request_with_no_persist_or_wake(eng):
    engine, gid, _ = eng
    engine._group_host = host = make_host(engine.store)

    async def scenario():
        host.start(gid, conversation=ConversationOptions(max_turns=2, idle_timeout=60))
        event = host._wakes[gid]._events["two"]
        for _ in range(1000):
            if not event.is_set():
                break
            await asyncio.sleep(0)
        with pytest.raises(CommandFailed) as e:
            await arun(engine, f'send {{"id": "{gid}", "text": "x", "audience": ["nobody"]}}')
        assert e.value.code == "bad_request" and "invalid audience" in str(e.value)
        with pytest.raises(CommandFailed) as e:
            await arun(engine, f'send {{"id": "{gid}", "text": "  "}}')
        assert e.value.code == "bad_request"
        assert not event.is_set() and rows(engine.store) == []
        host.stop(gid)
        await asyncio.wait_for(host.wait(gid), 30)

    asyncio.run(scenario())


def test_host_error_text_surfaces_only_as_bad_request(eng):
    engine, gid, _ = eng
    engine._group_host = host = make_host(engine.store)

    def boom(*a, **k):
        raise HostError("refused by host")
    host.send = boom
    with pytest.raises(CommandFailed) as e:
        run(engine, f'send {{"id": "{gid}", "text": "hi"}}')
    assert e.value.code == "bad_request" and "refused by host" in str(e.value)
    assert rows(engine.store) == []


def test_host_send_still_requires_principal(eng):
    engine, gid, _ = eng
    calls = []
    engine._group_host = host = make_host(engine.store)
    host.send = lambda *a, **k: calls.append(1)
    for who in ("local", "channel"):
        with pytest.raises(CommandFailed) as e:
            run(engine, f'send {{"id": "{gid}", "text": "hi"}}', who)
        assert e.value.code == "forbidden"
    assert calls == []


# --- compose shape: engine and host open SEPARATE SessionStore objects over one db path ---


def _composed_shape(tmp_path):
    """Mirrors compose.create + group_launch: CoreEngine and GroupHost each own a SessionStore
    on home/sessions.sqlite. (A full compose.create needs plugins/config, so the engine is a
    SimpleNamespace with the engine-side store; the host is checked by compose's own validator.)"""
    path = str(tmp_path / "sessions.sqlite")
    engine_store, host_store = SessionStore(path), SessionStore(path)
    assert engine_store is not host_store and engine_store.path == host_store.path == path
    engine = SimpleNamespace(agent="one", store=engine_store)
    groups = GroupStore(host_store)
    gid = groups.create(owner="one", mode="conductor", members=["lead", "one", "two"],
                        known_profiles=PROFILES, conductor="lead").id
    engines = {}
    host = make_host(host_store, engines=engines)
    GroupMessages(host._groups)
    from exo import compose
    compose._validate_group_host(host, tmp_path, "one")  # the real compose binding check passes
    engine._group_host = host
    return engine, host, gid, engines, (engine_store, host_store)


def test_separate_stores_same_path_wake_and_persist_once(tmp_path):
    engine, host, gid, engines, stores = _composed_shape(tmp_path)
    try:
        async def scenario():
            host.start(gid, conversation=ConversationOptions(max_turns=3, idle_timeout=60))
            event = host._wakes[gid]._events["two"]
            for _ in range(1000):
                if not event.is_set():
                    break
                await asyncio.sleep(0)
            else:
                raise AssertionError("member never idled")
            sent = await arun(engine, f'send {{"id": "{gid}", "text": "hello", "audience": ["two"]}}')
            assert event.is_set()  # woken by the command itself (idle_timeout is 60s)
            await asyncio.wait_for(engines["two"].turn_done(1).wait(), 30)
            assert json.loads(engines["two"].turns[0].text.split("\n", 1)[1]) == [
                {"sender": "one", "sender_kind": "human", "text": "hello"}]
            for st in stores:  # one shared database: exactly one human row from either handle
                assert [r["id"] for r in rows(st) if r["sender_kind"] == "human"] == [sent["id"]]
            host.stop(gid)
            await asyncio.wait_for(host.wait(gid), 30)

        asyncio.run(scenario())
    finally:
        for st in stores:
            st.close()


def host_has_rows(store):
    try:
        return bool(rows(store))
    except Exception:  # no swarm_messages table at all
        return False


def test_different_path_host_still_refused(tmp_path):
    engine, host, gid, _, stores = _composed_shape(tmp_path)
    other = SessionStore(str(tmp_path / "other.sqlite"))
    try:
        foreign, calls = make_host(other), []
        _forbid_send(foreign, calls)
        engine._group_host = foreign
        assert group_commands._send_host(engine) is None
        sent = run(engine, f'send {{"id": "{gid}", "text": "hi"}}')
        assert calls == [] and [r["id"] for r in rows(engine.store)] == [sent["id"]]
        assert other._q("SELECT 1") and not host_has_rows(other)  # host store untouched
    finally:
        for st in (*stores, other):
            st.close()
