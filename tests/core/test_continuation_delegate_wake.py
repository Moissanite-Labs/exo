"""A delegate-completion notice brings forward a wake the agent itself armed (``exo_end_turn`` ``wake_after``).

Offline: fake provider, real CoreRuntime, real delegate notice path and real continuation timer; the timer's sleep is
a gate, so no test waits on wall time. An unowned notice only queues; an owned task completion earns one bounded wake credit, and
``jobs.wake=false``, an open approval, Stop, a human turn and shutdown each leave the armed wake as they found it.
"""
from __future__ import annotations

import asyncio

import pytest

from exo import provenance
from exo.core.continuation import ORIGIN
from exo.core.engine import CoreRuntime
from exo.core.ingress import Ingress
from exo.core.sessions import new_id
from exo.protocol import schema as s
from exo.server.app import Dispatcher

from tests.core.test_continuation_engine import begin, drain, users  # shared offline helpers


async def setup(tmp_path, *, jobs=None, args=None, watching=True):
    engine = CoreRuntime(tmp_path, {
        "fake": {"reply": "finished", "rules": [{"match": "begin", "steps": [
            {"tool": "exo_end_turn", "args": args or {"wake_after": 300}}, "requested"]}]},
        "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, env={}, cwd=str(tmp_path), jobs=jobs)
    engine.plugins.hooks.register("pre_tool_call", lambda **kw: None)
    if watching:  # a client on the stream: the default job rule would not wake, only queue
        engine.hub.subscribe(10**9)
    session = await engine.loop.create_session(agent=engine.agent)
    gate, delays = asyncio.Event(), []

    async def sleep(delay):
        delays.append(delay)
        if delay:  # the armed wait never elapses on its own in a test
            await gate.wait()
    engine.continuations.sleep = sleep
    return engine, session.id, delays


async def notice(engine, sid, body="[delegate notice task t1: finished] result text"):
    ing = Ingress("delegate", "harness", "local", body, session=sid, id=new_id("dn"),
                  meta={"local": True, "task_id": "t1", "state": "done"})
    return await engine.agents.notices.path.submit(ing)


def wakes(engine, sid):
    return [m for m in users(engine, sid) if m.meta.get("origin") == ORIGIN]


def notices(engine, sid):
    return [m for m in users(engine, sid) if m.meta.get("author") == "delegate"]


def test_notice_brings_forward_the_armed_wake_exactly_once(tmp_path):
    async def run():
        engine, sid, delays = await setup(tmp_path)
        try:
            await begin(engine, sid)
            await asyncio.sleep(0)  # let the timer task enter its armed wait
            assert engine.continuations.pending(sid) and delays == [300.0] and not wakes(engine, sid)
            engine.continuations.delegate_started(sid, "t1")
            await notice(engine, sid)
            await drain(engine)
            assert len(notices(engine, sid)) == 1  # the receipt was kept, then the agent's own wake ran
            assert len(wakes(engine, sid)) == 1
            assert wakes(engine, sid)[0].meta["author"] == provenance.SYSTEM
            assert not engine.continuations.pending(sid)
            await notice(engine, sid, "[delegate notice task t2: finished] more")  # nothing armed any more
            await drain(engine)
            assert len(wakes(engine, sid)) == 1 and len(notices(engine, sid)) == 2
        finally:
            await engine.close()
    asyncio.run(run())


def test_a_second_notice_while_the_wake_is_starting_does_not_double_it(tmp_path):
    async def run():
        engine, sid, _ = await setup(tmp_path)
        try:
            await begin(engine, sid)
            engine.continuations.delegate_started(sid, "t1")
            await notice(engine, sid)
            await notice(engine, sid, "[delegate notice task t2: finished] more")
            await drain(engine)
            assert len(wakes(engine, sid)) == 1
        finally:
            await engine.close()
    asyncio.run(run())


def test_a_notice_without_an_armed_wake_only_queues(tmp_path):
    async def run():
        engine, sid, delays = await setup(tmp_path, args={"followup_pending": True})
        try:
            await begin(engine, sid)
            await notice(engine, sid)
            await drain(engine)
            assert len(notices(engine, sid)) == 1 and not wakes(engine, sid) and delays == []
            assert not engine.continuations.pending(sid)
        finally:
            await engine.close()
    asyncio.run(run())


def test_a_notice_does_not_create_a_wake_on_a_plain_session(tmp_path):
    async def run():
        engine, sid, _ = await setup(tmp_path)
        try:
            await notice(engine, sid)  # no turn, no exo_end_turn
            await drain(engine)
            assert len(notices(engine, sid)) == 1 and not wakes(engine, sid)
            assert not engine.continuations.pending(sid) and not users(engine, sid)[1:]
        finally:
            await engine.close()
    asyncio.run(run())


def test_jobs_wake_false_leaves_the_armed_wait_alone(tmp_path):
    async def run():
        engine, sid, _ = await setup(tmp_path, jobs={"wake": False})
        try:
            await begin(engine, sid)
            await notice(engine, sid)
            await drain(engine)
            assert engine.jobs.cfg.wake is False
            assert len(notices(engine, sid)) == 1 and not wakes(engine, sid)
            assert engine.continuations.pending(sid)  # still waiting out the agent's own delay
        finally:
            await engine.close()
    asyncio.run(run())


def test_an_open_approval_leaves_the_armed_wait_alone(tmp_path):
    async def run():
        engine, sid, _ = await setup(tmp_path)
        try:
            await begin(engine, sid)
            engine.approvals.open["ap1"] = object()
            try:
                await notice(engine, sid)
                await drain(engine)
                assert not wakes(engine, sid) and engine.continuations.pending(sid)
            finally:
                engine.approvals.open.pop("ap1", None)
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "human"])
def test_stop_or_a_human_turn_voids_the_wake_and_a_later_notice_cannot_revive_it(tmp_path, action):
    async def run():
        engine, sid, _ = await setup(tmp_path)
        try:
            await begin(engine, sid)
            assert engine.continuations.pending(sid)
            if action == "stop":
                await engine._turn_interrupt(s.TurnInterrupt(session=sid))
            else:
                await Dispatcher(engine).run(s.Command.of(s.TurnSend(session=sid, text="human")), "principal:tui")
            await drain(engine)
            assert not engine.continuations.pending(sid)
            await notice(engine, sid)
            await drain(engine)
            assert not wakes(engine, sid) and len(notices(engine, sid)) == 1
        finally:
            await engine.close()
    asyncio.run(run())


def test_a_closed_engine_does_not_wake(tmp_path):
    async def run():
        engine, sid, _ = await setup(tmp_path)
        await begin(engine, sid)
        await engine.close()
        assert not engine.continuations.pending(sid) and not wakes(engine, sid)
    asyncio.run(run())
