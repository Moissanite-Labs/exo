import asyncio
from types import SimpleNamespace

import pytest

from exo.core.group_engine_adapter import AdapterError, GroupEngineAdapter
from exo.core.group_messages import GroupMessages
from exo.core.group_runtime import GroupRuntime
from exo.core.groups import GroupStore
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
                                               ("interrupted", "stopped")])
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
        note = runtime.send_human(audience=["two"], text="question")
        runtime.start()
        assert {s.state for s in await runtime.wait()} == {expected}
        pending = messages.pending(group.id, owner="host", member="two")
        assert (note in pending) == (reason != "end")
        assert len(started) == 3
        assert any("question" in c.text for c in started)
        assert runtime.receipt().members == {p: expected for p in engines}
        if reason == "end":
            worker_history = messages.history(group.id, owner="host", member="two")
            assert not any(m.sender == "one" for m in worker_history)
    asyncio.run(scenario())


def test_real_engine_turns(room, tmp_path):
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
            runtime.start()
            assert {s.state for s in await runtime.wait()} == {"completed"}
            for member in group.members:
                assert engines[member.profile].store.messages(member.session)
        finally:
            for engine in engines.values():
                await engine.close()
    asyncio.run(scenario())
