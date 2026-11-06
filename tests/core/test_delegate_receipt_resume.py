"""Independent offline regression checks for owned completion resumption and takeover fences."""
from __future__ import annotations

import asyncio

import pytest

from exo import provenance
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from tests.core.test_continuation_delegate_wake import setup, notice, wakes, notices
from tests.core.test_continuation_engine import begin, drain, until


@pytest.mark.parametrize("watching", [True, False])
@pytest.mark.parametrize("chain", [0, 5])
def test_owned_completion_resumes_without_timer_even_at_poll_cap(tmp_path, watching, chain):
    async def run():
        e, sid, _ = await setup(tmp_path, args={"followup_pending": True}, watching=watching)
        try:
            await begin(e, sid)
            e.continuations._st(sid).chain = chain
            e.continuations.delegate_started(sid, "t1")
            await notice(e, sid)
            await drain(e)
            assert len(wakes(e, sid)) == 1
            assert wakes(e, sid)[0].meta["author"] == provenance.SYSTEM
            assert notices(e, sid)[0].meta["author"] == "delegate"
            assert e.continuations._st(sid).chain == chain
            await notice(e, sid)
            await drain(e)
            assert len(wakes(e, sid)) == 1  # one credit per actual launched task
        finally:
            await e.close()
    asyncio.run(run())


@pytest.mark.parametrize("watching", [True, False])
@pytest.mark.parametrize("takeover", ["stop", "human"])
def test_old_owned_completion_stays_queued_after_takeover(tmp_path, watching, takeover):
    async def run():
        e, sid, _ = await setup(tmp_path, args={"followup_pending": True}, watching=watching)
        try:
            await begin(e, sid)
            e.continuations.delegate_started(sid, "t1")
            if takeover == "stop":
                await e._turn_interrupt(s.TurnInterrupt(session=sid))
            else:
                await Dispatcher(e).run(s.Command.of(s.TurnSend(session=sid, text="human")), "principal:tui")
                await drain(e)
            await notice(e, sid)
            await drain(e)
            assert not wakes(e, sid) and len(notices(e, sid)) == 1
        finally:
            await e.close()
    asyncio.run(run())


def test_followup_only_request_does_not_hide_receipt_at_turn_end(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        try:
            cont = e.continuations
            original = cont.is_running
            cont.is_running = lambda session: True  # finishing turn no longer accepting steers
            cont.delegate_started(sid, "t1")
            cont.request(sid, "parent-turn", {"followup_pending": True})
            await notice(e, sid)
            cont.is_running = original
            assert cont.turn_ended(sid, "parent-turn", "end") == "scheduled"
            await drain(e)
            assert len(wakes(e, sid)) == 1
        finally:
            await e.close()
    asyncio.run(run())


def test_receipts_during_pre_turn_hook_share_one_wake(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async def hook(**kw):
            entered.set()
            await release.wait()
        e.plugins.hooks.register("on_turn_start", hook)
        try:
            e.continuations.delegate_started(sid, "t1")
            await notice(e, sid)
            await until(entered.is_set)
            e.continuations.delegate_started(sid, "t2")
            await notice(e, sid)
            for _ in range(5):
                await asyncio.sleep(0)
            assert len(e._tasks) == 1
            release.set()
            await drain(e)
            assert len(wakes(e, sid)) == 1 and len(notices(e, sid)) == 2
        finally:
            release.set()
            await e.close()
    asyncio.run(run())


@pytest.mark.parametrize("watching", [True, False])
@pytest.mark.parametrize("takeover", [None, "stop", "human"])
def test_real_background_tool_owns_completion_generation(tmp_path, watching, takeover):
    from tests.agents.test_delegate_background import engine, tool_turn, until as wait_until
    async def run():
        child_release = asyncio.Event()
        e = engine(tmp_path, [("PARENT", [tool_turn("delegate", {
            "goal": "CHILD verify", "role": "builder", "background": True}), "waiting"]),
            ("CHILD", [child_release, "verified"]), ("human", ["taken over"])])
        sid = (await e.loop.create_session()).id
        if watching:
            e.hub.subscribe(10**9)
        try:
            await e.run_turn(s.TurnSend(session=sid, text="PARENT"), {"author": provenance.PRINCIPAL})
            task = next(iter(e.agents.bg))
            assert e.continuations._st(sid).delegates[task] == e.continuations.generation(sid)
            if takeover == "stop":
                await e._turn_interrupt(s.TurnInterrupt(session=sid))
            elif takeover == "human":
                await e.run_turn(s.TurnSend(session=sid, text="human"), {"author": provenance.PRINCIPAL})
            child_release.set()
            await wait_until(lambda: not e.agents.bg)
            await drain(e)
            assert len(notices(e, sid)) == 1
            assert len(wakes(e, sid)) == (1 if takeover is None else 0)
        finally:
            child_release.set()
            await e.close()
    asyncio.run(run())


@pytest.mark.parametrize("block", ["jobsfalse", "approval", "denied", "error", "cancelled"])
def test_owned_receipt_never_crosses_safety_gate(tmp_path, block):
    from exo.core.ingress import Ingress
    async def run():
        e, sid, _ = await setup(tmp_path, jobs={"wake": False} if block == "jobsfalse" else None,
                                args={"followup_pending": True})
        try:
            await begin(e, sid)
            e.continuations.delegate_started(sid, "t1")
            if block == "approval":
                e.approvals.open["test-approval"] = object()
            if block in ("denied", "error"):
                if block == "denied":
                    e.continuations.note_denied(sid, "parent")
                e.continuations.turn_ended(sid, "parent", "end", "failure" if block == "error" else None)
            await e.agents.notices.path.submit(Ingress("delegate", "harness", "local", "result", session=sid,
                meta={"local": True, "task_id": "t1", "state": "cancelled" if block == "cancelled" else "done"}))
            await drain(e)
            assert not wakes(e, sid) and len(notices(e, sid)) == 1
        finally:
            e.approvals.open.clear()
            await e.close()
    asyncio.run(run())


def test_stale_receipt_cannot_expedite_new_human_era_timer(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        try:
            c = e.continuations
            c.delegate_started(sid, "t1")
            await begin(e, sid)  # new principal era; old task belongs to previous generation
            await asyncio.sleep(0)
            assert c.pending(sid)
            await notice(e, sid)
            await drain(e)
            assert not wakes(e, sid) and c.pending(sid)
        finally:
            await e.close()
    asyncio.run(run())


def test_old_task_context_cannot_claim_new_generation_after_stop(tmp_path):
    from exo.core.continuation import TURN_GENERATION
    async def run():
        e, sid, _ = await setup(tmp_path)
        try:
            c = e.continuations
            token = TURN_GENERATION.set((sid, c.generation(sid)))
            try:
                c.cancel(sid)
                c.delegate_started(sid, "t1")  # a late tool worker inherited its old turn context
            finally:
                TURN_GENERATION.reset(token)
            await notice(e, sid)
            await drain(e)
            assert not wakes(e, sid)
        finally:
            await e.close()
    asyncio.run(run())


def test_principal_steer_invalidates_background_task_ownership(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path, args={"followup_pending": True})
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause(tool_name, **kw):
            if tool_name == "exo_end_turn":
                e.continuations.delegate_started(sid, "t1")
                entered.set()
                await release.wait()
        e.plugins.hooks.register("post_tool_call", pause)
        task = asyncio.create_task(begin(e, sid))
        try:
            await until(entered.is_set)
            generation = e.continuations.generation(sid)
            result = await Dispatcher(e).run(s.Command.of(s.TurnSend(session=sid, text="human takeover", mode="steer")),
                                             "principal:tui")
            assert result.ok and e.continuations.generation(sid) > generation
            release.set()
            await task
            await notice(e, sid)
            await drain(e)
            assert not wakes(e, sid) and len(notices(e, sid)) == 1
        finally:
            release.set()
            await task
            await e.close()
    asyncio.run(run())


def test_receipts_during_session_lock_wait_share_one_wake(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        lock = e.loop.state(sid).lock
        await lock.acquire()
        try:
            e.continuations.delegate_started(sid, "t1")
            await notice(e, sid)
            await until(lambda: bool(e._tasks))
            for _ in range(4):
                await asyncio.sleep(0)
            e.continuations.delegate_started(sid, "t2")
            await notice(e, sid)
            lock.release()
            await drain(e)
            assert len(wakes(e, sid)) == 1 and len(notices(e, sid)) == 2
        finally:
            if lock.locked():
                lock.release()
            await e.close()
    asyncio.run(run())


@pytest.mark.parametrize("stop_after_steer", [False, True])
def test_consumed_principal_steer_owns_new_background_work(tmp_path, stop_after_steer):
    """The old tool worker stays fenced; a new round answering the steer owns its children."""
    from tests.agents.test_delegate_background import engine, tool_turn, until as wait_until
    from exo.core.continuation import TURN_GENERATION

    async def run():
        barrier, release, child_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        e = engine(tmp_path, [("PARENT", [tool_turn("exo_end_turn", {"followup_pending": True}), "old"]),
            ("NEW", [tool_turn("delegate", {"goal": "CHILD verify", "role": "builder", "background": True}),
                     tool_turn("exo_end_turn", {"followup_pending": True}), "waiting"]),
            ("CHILD", [child_release, "verified"])])
        sid = (await e.loop.create_session()).id
        stale = []
        async def pause(tool_name, **kw):
            if tool_name == "exo_end_turn" and not barrier.is_set():
                barrier.set()
                await release.wait()
                e.continuations.delegate_started(sid, "stale-worker")
                stale.append(TURN_GENERATION.get())
        e.plugins.hooks.register("post_tool_call", pause)
        parent = asyncio.create_task(e.run_turn(s.TurnSend(session=sid, text="PARENT"),
                                               {"author": provenance.PRINCIPAL}))
        try:
            await wait_until(barrier.is_set)
            old_gen = e.continuations.generation(sid)
            sent = await Dispatcher(e).run(s.Command.of(s.TurnSend(session=sid, text="NEW", mode="steer")),
                                           "principal:tui")
            assert sent.ok
            if stop_after_steer:
                # Fence the queued steer before it enters context, as an actual Stop does.
                e.continuations.cancel(sid)
            release.set()
            await parent
            task = next(iter(e.agents.bg))
            assert stale == [(sid, old_gen)]
            assert "stale-worker" not in e.continuations._st(sid).delegates
            assert (task in e.continuations._st(sid).delegates) is (not stop_after_steer)
            errors = [r.text() for m in e.store.messages(sid) for r in m.tool_results if r.is_error]
            assert any("continuation cancelled" in t for t in errors) is stop_after_steer
            child_release.set()
            await wait_until(lambda: not e.agents.bg)
            await drain(e)
            assert len(wakes(e, sid)) == (0 if stop_after_steer else 1)
        finally:
            release.set()
            child_release.set()
            await parent
            await e.close()
    asyncio.run(run())


@pytest.mark.parametrize("case", ["current", "stale", "delegate", "closed", "unowned", "other_session"])
def test_steer_rebind_requires_current_principal_fence(case):
    from exo.core.continuation import Continuations, TURN_GENERATION

    async def start(*args):
        pass

    c = Continuations(start, lambda sid: False)
    c.turn_started("parent", {})
    old = ("other" if case == "other_session" else "parent", 0)
    token = TURN_GENERATION.set(None if case == "unowned" else old)
    try:
        if case == "closed":
            c.close()
        c.steer_entered("parent", {"author": "delegate" if case == "delegate" else provenance.PRINCIPAL,
            "continuation_steer_gen": 0 if case == "stale" else c.generation("parent")})
        assert TURN_GENERATION.get() == (("parent", 1) if case == "current" else
                                          None if case == "unowned" else old)
    finally:
        TURN_GENERATION.reset(token)
