import asyncio
from types import SimpleNamespace

import pytest

from exo.core.group_engine_adapter import AdapterError, GroupEngineAdapter
from exo.core.group_messages import GroupMessages
from exo.core.group_runtime import GroupRuntime, MemberContext
from exo.core.groups import GroupMember, GroupStore
from exo.core.sessions import SessionStore


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)
    group = groups.create(owner="host", mode="conductor", members=["lead", "one", "two"],
                          known_profiles=["lead", "one", "two"], conductor="lead")
    engines = {m.profile: SimpleNamespace(agent=m.profile, store=store) for m in group.members}
    audiences = {"lead": ["one"], "one": ["lead"], "two": ["lead"]}
    yield GroupMessages(groups), group, engines, audiences
    store.close()


def test_bindings_and_routes(room):
    _, group, engines, audiences = room
    engines["one"].agent = "wrong"
    with pytest.raises(AdapterError):
        GroupEngineAdapter(group, engines, audiences=audiences)
    engines["one"].agent = "one"
    audiences["one"] = ["two"]
    with pytest.raises(AdapterError):
        GroupEngineAdapter(group, engines, audiences=audiences)


@pytest.mark.parametrize("reason,expected", [("end", "completed"), ("error", "failed"),
                                               ("interrupted", "failed")])
def test_runtime_concurrency_receipts_and_consumption(room, reason, expected):
    messages, group, engines, audiences = room
    async def scenario():
        started = []
        ready = asyncio.Event()
        async def turn(command):
            started.append(command)
            if len(started) == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), 2)
            return SimpleNamespace(stop_reason=reason, error=None,
                                   message=SimpleNamespace(text=lambda: "reply"))
        for engine in engines.values():
            engine.run_turn = turn
        adapter = GroupEngineAdapter(group, engines, audiences=audiences)
        runtime = GroupRuntime(messages, group.id, owner="host", adapters=adapter.adapters())
        note = runtime.send_human(audience=list(engines), text="question")
        runtime.start()
        assert {s.state for s in await asyncio.wait_for(runtime.wait(), 5)} == {expected}
        pending = messages.pending(group.id, owner="host", member="two")
        assert (note in pending) == (reason != "end")
        assert len(started) == 3
        assert any("question" in c.text for c in started)
        assert runtime.receipt().members == {p: expected for p in engines}
        if reason == "end":
            worker_history = messages.history(group.id, owner="host", member="two")
            assert not any(m.sender == "one" for m in worker_history)
    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("has_input", [False, True])
def test_real_engine_turns(room, tmp_path, has_input):
    from exo.core.engine import CoreEngine
    from exo.core.plugins import PluginManager
    from exo.core.seats import Seat
    messages, group, _, audiences = room
    async def scenario():
        engines = {}
        try:
            for member in group.members:
                engine = CoreEngine(tmp_path / member.profile,
                                    {"fake": {"rules": []}, "roles": {"main": ["fake:x"]}},
                                    plugins=PluginManager(), cwd=str(tmp_path), env={},
                                    seat=Seat(name=member.profile))
                engines[member.profile] = engine
                engine.store.create_session(id=member.session, agent=member.profile, kind="private")
            adapter = GroupEngineAdapter(group, engines, audiences=audiences)
            runtime = GroupRuntime(messages, group.id, owner="host", adapters=adapter.adapters())
            if has_input:
                runtime.send_human(audience=list(engines), text="question")
            runtime.start()
            assert {s.state for s in await asyncio.wait_for(runtime.wait(), 5)} == {"completed"}
            for member in group.members:
                assert bool(engines[member.profile].store.messages(member.session)) == has_input
            if not has_input:
                assert runtime.observe() == []
        finally:
            for engine in engines.values():
                await engine.close()
    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("field,value", [("kind", "thread"), ("agent", "other")])
def test_stored_session_binding_rejected(room, field, value):
    messages, group, engines, audiences = room
    member = next(m for m in group.members if m.profile == "one")
    messages.store._q(f"UPDATE sessions SET {field}=? WHERE id=?", (value, member.session))
    with pytest.raises(AdapterError, match="private member"):
        GroupEngineAdapter(group, engines, audiences=audiences)


def test_shared_engine_rejected_before_session_lookup(room):
    _, group, engines, audiences = room
    def must_not_read(session):
        raise AssertionError("an aliased engine mapping must be rejected before session access")
    engines["one"].store = SimpleNamespace(get_session=must_not_read)
    engines["two"] = engines["one"]
    with pytest.raises(AdapterError):
        GroupEngineAdapter(group, engines, audiences=audiences)


@pytest.mark.parametrize("change", ["engine", "owner", "kind"])
def test_binding_changed_after_construction_rejected_before_delivery(room, change):
    messages, group, engines, audiences = room
    called = []
    async def turn(command):
        called.append(command)
        return SimpleNamespace(stop_reason="end", error=None, message=None)
    for engine in engines.values():
        engine.run_turn = turn
    adapter = GroupEngineAdapter(group, engines, audiences=audiences)
    member = next(m for m in group.members if m.profile == "one")
    if change == "engine":
        engines["one"].agent = "wrong"
    else:
        field, value = ("agent", "other") if change == "owner" else ("kind", "thread")
        messages.store._q(f"UPDATE sessions SET {field}=? WHERE id=?", (value, member.session))
    async def scenario():
        runtime = GroupRuntime(messages, group.id, owner="host", adapters=adapter.adapters())
        note = runtime.send_human(audience=["one"], text="question")
        runtime.start()
        await asyncio.wait_for(runtime.wait(), 2)
        assert runtime.receipt().members["one"] == "failed"
        assert called == []
        receipt = messages.store._q("SELECT delivered,consumed FROM swarm_delivery WHERE member='one'")[0]
        assert (receipt["delivered"], receipt["consumed"]) == (0, 0)
        assert messages.pending(group.id, owner="host", member="one") == [note]
    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_foreign_member_context_rejected_even_with_valid_private_session(room):
    messages, group, engines, audiences = room
    messages.store.create_session(id="foreign-session", agent="one", kind="private")
    adapter = GroupEngineAdapter(group, engines, audiences=audiences)
    runtime = GroupRuntime(messages, group.id, owner="host", adapters=adapter.adapters())
    context = MemberContext(runtime, GroupMember("one", "foreign-session"))
    with pytest.raises(AdapterError, match="not a member"):
        asyncio.run(asyncio.wait_for(adapter.adapters()["one"](context), 2))
    assert runtime.receipt() is None
    assert runtime.observe() == []


def test_binding_changed_while_checkpoint_waits_is_revalidated(room):
    messages, group, engines, audiences = room
    called, ready = [], asyncio.Event()
    async def turn(command):
        called.append(command)
        return SimpleNamespace(stop_reason="end", error=None, message=None)
    for engine in engines.values():
        engine.run_turn = turn
    adapter = GroupEngineAdapter(group, engines, audiences=audiences)
    async def scenario():
        async def host_adapter(ctx):
            if ctx.member.profile == "one":
                runtime.pause("one")
                ready.set()
            await adapter.adapters()[ctx.member.profile](ctx)
        runtime = GroupRuntime(messages, group.id, owner="host",
                               adapters=dict.fromkeys(engines, host_adapter))
        note = runtime.send_human(audience=["one"], text="question")
        runtime.start()
        try:
            await asyncio.wait_for(ready.wait(), 2)
            assert {s.profile: s.state for s in runtime.status()}["one"] == "paused"
            engines["one"].agent = "wrong"
            runtime.resume("one")
            await asyncio.wait_for(runtime.wait(), 2)
            assert runtime.receipt().members["one"] == "failed"
            assert called == []
            assert messages.pending(group.id, owner="host", member="one") == [note]
            assert runtime.observe() == [note]
        finally:
            runtime.stop()
            await asyncio.wait_for(runtime.wait(), 2)
    asyncio.run(asyncio.wait_for(scenario(), 10))


@pytest.mark.parametrize("reply", [None, "", "  \n", "reply"])
def test_blank_success_reply_still_consumes_input_without_publishing_blank(room, reply):
    messages, group, engines, audiences = room
    called = []
    async def turn(command):
        called.append(command)
        return SimpleNamespace(stop_reason="end", error=None,
                               message=None if reply is None else SimpleNamespace(text=lambda: reply))
    for engine in engines.values():
        engine.run_turn = turn
    async def scenario():
        adapter = GroupEngineAdapter(group, engines, audiences=audiences)
        runtime = GroupRuntime(messages, group.id, owner="host", adapters=adapter.adapters())
        note = runtime.send_human(audience=["two"], text="question")
        runtime.start()
        await asyncio.wait_for(runtime.wait(), 2)
        assert {s.state for s in runtime.status()} == {"completed"}
        assert len(called) == 1
        assert messages.pending(group.id, owner="host", member="two") == []
        history = runtime.observe()
        assert history[0] == note
        assert len(history) == (2 if reply and reply.strip() else 1)
    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_requested_stop_waits_for_engine_cleanup_and_retains_input(room):
    messages, group, engines, audiences = room
    async def scenario():
        started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))
        called = []
        async def turn(command):
            called.append(command)
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                cleaned.set()
                return SimpleNamespace(stop_reason="interrupted", error=None, message=None)
        for engine in engines.values():
            engine.run_turn = turn
        adapter = GroupEngineAdapter(group, engines, audiences=audiences)
        runtime = GroupRuntime(messages, group.id, owner="host", adapters=adapter.adapters())
        note = runtime.send_human(audience=["one"], text="question")
        runtime.start()
        try:
            await asyncio.wait_for(started.wait(), 2)
            runtime.stop("one")
            await asyncio.wait_for(cleaning.wait(), 2)
            runtime.stop("one")
            for _ in range(3):
                await asyncio.sleep(0)
                assert {s.profile: s.state for s in runtime.status()}["one"] == "stopping"
                assert runtime.receipt().state == "active"
                assert not cleaned.is_set()
            release.set()
            await asyncio.wait_for(runtime.wait(), 2)
            assert cleaned.is_set() and len(called) == 1
            assert runtime.receipt().members == {"lead": "completed", "one": "stopped", "two": "completed"}
            assert messages.pending(group.id, owner="host", member="one") == [note]
            assert runtime.observe() == [note]
        finally:
            release.set()
            runtime.stop()
            await asyncio.wait_for(runtime.wait(), 2)
    asyncio.run(asyncio.wait_for(scenario(), 10))
