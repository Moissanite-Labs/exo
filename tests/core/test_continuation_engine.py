"""Offline CoreRuntime proof of continuation wiring and takeover races."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from exo import provenance
from exo.core.continuation import ORIGIN
from exo.core.engine import CoreRuntime
from exo.core.tools.registry import ToolContext
from exo.core.types import ToolCall, ToolSpec
from exo.protocol import schema as s
from exo.server.app import Dispatcher


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0)


async def drain(engine):
    # Timer spawning and task done callbacks each need their own event-loop tick.
    for _ in range(4):
        await asyncio.sleep(0)
        if engine._tasks:
            await asyncio.gather(*tuple(engine._tasks))


async def setup(tmp_path, *, args=None, steps=None):
    engine = CoreRuntime(tmp_path, {
        "fake": {"reply": "finished", "rules": [{"match": "begin", "steps": steps or [
            {"tool": "exo_end_turn", "args": args or {"continue_now": True}}, "requested"]}]},
        "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, env={}, cwd=str(tmp_path))
    engine.plugins.hooks.register("pre_tool_call", lambda **kw: None)
    session = await engine.loop.create_session(agent=engine.agent)
    return engine, session.id


def users(engine, sid):
    return [m for m in engine.store.messages(sid) if m.role == "user"]


async def begin(engine, sid):
    return await engine.run_turn(s.TurnSend(session=sid, text="begin"), {"author": provenance.PRINCIPAL})


def test_continue_now_runs_exactly_one_system_wake(tmp_path):
    async def run():
        engine, sid = await setup(tmp_path)
        try:
            result = await begin(engine, sid)
            await drain(engine)
            messages = users(engine, sid)
            assert len(messages) == 2
            assert messages[1].meta["origin"] == ORIGIN
            assert messages[1].meta["author"] == provenance.SYSTEM
            assert messages[1].meta["continuation_of"] == result.turn_id
            assert engine.store.messages(sid)[-1].text() == "finished"
            assert not engine.continuations.pending(sid)
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "human", "close"])
def test_pending_timer_cancelled_by_takeover_or_close(tmp_path, action):
    async def run():
        engine, sid = await setup(tmp_path, args={"wake_after": 30})
        entered, release = asyncio.Event(), asyncio.Event()
        async def sleep(delay):
            assert delay == 30
            entered.set()
            await release.wait()
        engine.continuations.sleep = sleep
        closed = False
        try:
            await begin(engine, sid)
            await entered.wait()
            assert engine.continuations.pending(sid)
            if action == "stop":
                await engine._turn_interrupt(s.TurnInterrupt(session=sid))
            elif action == "human":
                await Dispatcher(engine).run(s.Command.of(s.TurnSend(session=sid, text="human")), "principal:tui")
                await drain(engine)
            else:
                await engine.close()
                closed = True
            release.set()
            await drain(engine)
            assert not engine.continuations.pending(sid)
            if not closed:
                assert len(users(engine, sid)) == (2 if action == "human" else 1)
                assert not any(m.meta.get("origin") == ORIGIN for m in users(engine, sid))
            assert not engine._tasks
        finally:
            if not closed:
                await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "human"])
def test_fired_wake_waiting_for_session_lock_is_voided(tmp_path, action):
    async def run():
        engine, sid = await setup(tmp_path)
        release_timer, timer_entered = asyncio.Event(), asyncio.Event()
        reached_send = asyncio.Event()
        async def sleep(delay):
            timer_entered.set()
            await release_timer.wait()
        engine.continuations.sleep = sleep
        original_send = engine.loop.send
        async def observed_send(*args, **kwargs):
            if (kwargs.get("meta") or {}).get("origin") == ORIGIN:
                reached_send.set()
            return await original_send(*args, **kwargs)
        engine.loop.send = observed_send
        try:
            await begin(engine, sid)
            await timer_entered.wait()
            lock = engine.loop.state(sid).lock
            await lock.acquire()
            try:
                release_timer.set()
                await reached_send.wait()
                assert not engine.continuations.pending(sid)
                if action == "stop":
                    await engine._turn_interrupt(s.TurnInterrupt(session=sid))
                else:
                    await Dispatcher(engine).run(s.Command.of(s.TurnSend(session=sid, text="human")), "principal:tui")
                    await until(lambda: engine.continuations._s[sid].gen > 1)
            finally:
                lock.release()
            await drain(engine)
            assert len(users(engine, sid)) == (2 if action == "human" else 1)
            assert not any(m.meta.get("origin") == ORIGIN for m in users(engine, sid))
        finally:
            await engine.close()
    asyncio.run(run())


def test_stop_during_turn_clears_requested_wake(tmp_path):
    async def run():
        engine, sid = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause(tool_name, **kw):
            if tool_name == "exo_end_turn":
                entered.set()
                await release.wait()
        engine.plugins.hooks.register("post_tool_call", pause)
        task = asyncio.create_task(begin(engine, sid))
        try:
            await entered.wait()
            assert engine.continuations._s[sid].request.wakes
            assert (await engine._turn_interrupt(s.TurnInterrupt(session=sid)))["interrupted"] == 1
            release.set()
            await task
            await drain(engine)
            assert len(users(engine, sid)) == 1
            assert not engine.continuations.pending(sid)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await engine.close()
    asyncio.run(run())


def test_user_denied_tool_prevents_wake(tmp_path):
    async def run():
        engine, sid = await setup(tmp_path, steps=[
            {"tool": "exo_end_turn", "args": {"continue_now": True}},
            {"tool": "needs_permission", "args": {}}, "finished"])
        engine.registry.register(ToolSpec(name="needs_permission", description="test", parameters={}),
                                 lambda args, ctx: pytest.fail("denied tool executed"))
        engine.plugins.hooks.register("pre_tool_call", lambda tool_name, **kw:
            {"action": "approve", "message": "permission?"} if tool_name == "needs_permission" else None)
        task = asyncio.create_task(begin(engine, sid))
        try:
            await until(lambda: bool(engine.approvals.open))
            request_id = next(iter(engine.approvals.open))
            response = await Dispatcher(engine).run(s.Command.of(s.ApprovalRespond(
                request_id=request_id, decision="deny")), "principal:tui")
            assert response.ok
            await task
            await drain(engine)
            assert len(users(engine, sid)) == 1
            assert not engine.continuations.pending(sid)
            results = [e.body.result for e in engine.hub.since(0) if isinstance(e.body, s.ToolCompleted)]
            assert any(r.is_error and "user denied" in r.text() for r in results)
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("owner", ["scheduler", "subagent"])
def test_engine_registered_tool_rejects_non_main_sessions(tmp_path, owner):
    async def run():
        engine, sid = await setup(tmp_path)
        try:
            if owner == "scheduler":
                engine.scheduler = SimpleNamespace(loops={sid: engine.loop})
            else:
                engine.agents.loops[sid] = engine.loop
            result = await engine.registry.call(ToolCall(id="restricted", name="exo_end_turn",
                arguments={"continue_now": True}), ToolContext(session=sid, meta={"turn_id": "t"}))
            assert result.is_error and "subagent or scheduled run" in result.text()
            assert not engine.continuations._s
        finally:
            engine.scheduler = None
            engine.agents.loops.pop(sid, None)
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "human"])
def test_late_request_from_displaced_turn_cannot_rearm_wake(tmp_path, action):
    async def run():
        engine, sid = await setup(tmp_path)
        before_request, release_request = asyncio.Event(), asyncio.Event()
        human_queued, release_human = asyncio.Event(), asyncio.Event()
        async def pause_request(tool_name, **kw):
            if tool_name == "exo_end_turn":
                before_request.set()
                await release_request.wait()
        async def pause_human(message, **kw):
            if message == "human":
                human_queued.set()
                await release_human.wait()
        engine.plugins.hooks.register("pre_tool_call", pause_request)
        engine.plugins.hooks.register("on_turn_start", pause_human)
        old = asyncio.create_task(begin(engine, sid))
        human = None
        try:
            await before_request.wait()
            if action == "stop":
                await engine._turn_interrupt(s.TurnInterrupt(session=sid))
            else:
                human = asyncio.create_task(engine.run_turn(s.TurnSend(session=sid, text="human")))
                await human_queued.wait()
            release_request.set()
            await old
            await drain(engine)
            assert not engine.continuations.pending(sid)
            assert len(users(engine, sid)) == 1
            if action == "human":
                assert any(e.body.result.is_error and "stopped or took over" in e.body.result.text()
                           for e in engine.hub.since(0) if isinstance(e.body, s.ToolCompleted))
            release_human.set()
            if human:
                await human
            await drain(engine)
            assert not any(m.meta.get("origin") == ORIGIN for m in users(engine, sid))
        finally:
            release_request.set()
            release_human.set()
            await asyncio.gather(old, *([human] if human else []), return_exceptions=True)
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("owner", ["scheduler", "subagent"])
def test_non_main_models_do_not_discover_continuation_tool(tmp_path, owner):
    from exo.core.models.fake import FakeProvider
    from exo.scheduler import Scheduler

    async def run():
        engine, sid = await setup(tmp_path)
        seen = []
        class Inspect:
            name = "fake"

            async def stream(self, messages, tools, **opts):
                seen.append({tool.name for tool in tools})
                async for event in FakeProvider(["done"]).stream(messages, tools, **opts):
                    yield event
        engine.loop.provider = Inspect()
        try:
            assert "exo_end_turn" in {tool.name for tool in engine._specs()}
            if owner == "subagent":
                brief = engine.agents._brief(sid, 0, {"goal": "work"}, str(tmp_path))
                await engine.agents.run(sid, brief)
            else:
                engine.scheduler = Scheduler(engine)
                assert await engine.scheduler.run({"id": "test", "title": "test", "tier": "standard",
                    "prompt": "work", "deliver": "notice"}, "test-task") == "done", [e.body.text for e in engine.hub.since(0) if isinstance(e.body, s.Notice)]
            assert seen and all("exo_end_turn" not in names for names in seen)
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("author", ["principal:tui", None])
def test_principal_steer_drops_the_requested_wake(tmp_path, author):
    async def run():
        engine, sid = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause(tool_name, **kw):
            if tool_name == "exo_end_turn":
                entered.set()
                await release.wait()
        engine.plugins.hooks.register("post_tool_call", pause)
        task = asyncio.create_task(begin(engine, sid))
        try:
            await entered.wait()
            assert engine.continuations._s[sid].request.wakes
            steer = s.TurnSend(session=sid, text="STOP. Do not continue that work.", mode="steer")
            if author:
                reply = await Dispatcher(engine).run(s.Command.of(steer), author)
            else:  # an internal steer with no principal caller keeps the wake
                reply = await engine._turn_send(steer)
            assert "steered" in str(reply)
            release.set()
            await task
            await drain(engine)
            wakes = [m for m in users(engine, sid) if m.meta.get("origin") == ORIGIN]
            assert len(wakes) == (0 if author else 1)
            assert not engine.continuations.pending(sid)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await engine.close()
    asyncio.run(run())


def test_undo_cancels_the_pending_wake(tmp_path):
    async def run():
        engine, sid = await setup(tmp_path, args={"wake_after": 30})
        entered, release = asyncio.Event(), asyncio.Event()
        async def sleep(delay):
            entered.set()
            await release.wait()
        engine.continuations.sleep = sleep
        try:
            await begin(engine, sid)
            await entered.wait()
            assert engine.continuations.pending(sid)
            await Dispatcher(engine).run(s.Command.of(s.SessionUndo(session=sid)), "principal:tui")
            assert not engine.continuations.pending(sid)
            release.set()
            await drain(engine)
            assert users(engine, sid) == []
        finally:
            release.set()
            await engine.close()
    asyncio.run(run())


def test_non_principal_turn_does_not_renew_the_wake_budget(tmp_path):
    async def run():
        engine = CoreRuntime(tmp_path, {
            "fake": {"reply": "finished", "rules": [
                {"match": "", "steps": [{"tool": "exo_end_turn", "args": {"continue_now": True}}, "again"]}]},
            "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, env={}, cwd=str(tmp_path))
        engine.plugins.hooks.register("pre_tool_call", lambda **kw: None)
        engine.continuations.max_chain = 2
        sid = (await engine.loop.create_session(agent=engine.agent)).id
        def wakes():
            return sum(m.meta.get("origin") == ORIGIN for m in users(engine, sid))
        try:
            await begin(engine, sid)
            await until(lambda: wakes() == 2 and not engine._tasks)
            await drain(engine)
            await engine.run_turn(s.TurnSend(session=sid, text="job notice"))  # author system, like a job wake
            await drain(engine)
            assert wakes() == 2
            await begin(engine, sid)  # the principal resumes the work
            await until(lambda: wakes() == 4 and not engine._tasks)
            await drain(engine)
            assert wakes() == 4
        finally:
            await engine.close()
    asyncio.run(run())
