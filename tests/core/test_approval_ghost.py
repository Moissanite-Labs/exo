"""D-045: an approval card never outlives its request, and a queued steer can be pulled back."""
from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo.server.app import Dispatcher

FAKE = {"rules": [{"match": "hello", "steps": ["hi"]},
                  {"match": "slow", "steps": [{"tool": "bash", "args": {"command": "sleep 2"}}, "done"]}]}


def engine(tmp_path) -> CoreEngine:
    eng = CoreEngine(tmp_path / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     cwd=str(tmp_path), env={})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    return eng


def events_of(hub, kind):
    return [e for e in hub.since(0) if e.type == kind]


def test_cancelled_ask_publishes_resolved(tmp_path):
    async def go():
        eng = engine(tmp_path)
        d = Dispatcher(eng, caller="principal:tui")
        sid = (await d.run(s.Command.of(s.SessionCreate(title="t")))).result["session"]["id"]
        task = asyncio.ensure_future(eng.approvals.ask(sid, "run it?", ToolCall(id="c1", name="bash", arguments={})))
        await asyncio.sleep(0.05)
        (req,) = events_of(d.hub, "approval.requested")
        task.cancel()  # a stopped turn cancels the call that waits on the card
        await asyncio.gather(task, return_exceptions=True)
        (res,) = events_of(d.hub, "approval.resolved")
        assert res.body.request_id == req.body.request_id and res.body.decision == "cancelled"
        assert (await d.run(s.Command.of(s.ApprovalsList()))).result["approvals"] == []

    asyncio.run(go())


def test_answered_ask_publishes_resolved_once(tmp_path):
    async def go():
        eng = engine(tmp_path)
        d = Dispatcher(eng, caller="principal:tui")
        sid = (await d.run(s.Command.of(s.SessionCreate(title="t")))).result["session"]["id"]
        task = asyncio.ensure_future(eng.approvals.ask(sid, "run it?", None))
        await asyncio.sleep(0.05)
        (req,) = events_of(d.hub, "approval.requested")
        r = await d.run(s.Command.of(s.ApprovalRespond(request_id=req.body.request_id, decision="allow")))
        assert r.ok, r.error
        assert await task == "allow"
        assert len(events_of(d.hub, "approval.resolved")) == 1

    asyncio.run(go())


def test_steer_can_be_cancelled_before_delivery(tmp_path):
    async def go():
        eng = engine(tmp_path)
        d = Dispatcher(eng, caller="principal:tui")
        sid = (await d.run(s.Command.of(s.SessionCreate(title="t")))).result["session"]["id"]
        await d.run(s.Command.of(s.TurnSend(session=sid, text="slow")))
        for _ in range(100):
            if eng.loop.state(sid).running:
                break
            await asyncio.sleep(0.02)
        one = (await d.run(s.Command.of(s.TurnSend(session=sid, text="first", mode="steer")))).result
        two = (await d.run(s.Command.of(s.TurnSend(session=sid, text="second", mode="steer")))).result
        assert one["steered"] and one["message_id"] and two["message_id"] != one["message_id"]
        out = (await d.run(s.Command.of(s.CommandRun(name="unsteer", args=one["message_id"], session=sid)))).result
        assert out["output"]["cancelled"] == [{"id": one["message_id"], "text": "first"}]
        (ev,) = events_of(d.hub, "steer.cancelled")
        assert ev.body.message_id == one["message_id"] and ev.body.text == "first"
        newest = (await d.run(s.Command.of(s.CommandRun(name="unsteer", session=sid)))).result["output"]
        assert [m["text"] for m in newest["cancelled"]] == ["second"]
        again = (await d.run(s.Command.of(s.CommandRun(name="unsteer", session=sid)))).result["output"]
        assert again["cancelled"] == []  # nothing left to pull back
        while eng.loop.state(sid).running:
            await asyncio.sleep(0.05)
        texts = [m.text() for m in eng.store.messages(sid) if m.role == "user"]
        assert texts == ["slow"]  # neither cancelled steer reached the conversation

    asyncio.run(go())


def test_late_steer_dispatch_preserves_principal_author_and_starts_next_turn(tmp_path):
    async def go():
        eng = engine(tmp_path)
        d = Dispatcher(eng, caller="principal:tui")
        closing, release = asyncio.Event(), asyncio.Event()
        async def observer(session, turn):
            if not closing.is_set():
                closing.set()
                await release.wait()
        eng.loop.on_turn_end = observer
        sid = (await d.run(s.Command.of(s.SessionCreate(title="t")))).result["session"]["id"]
        await d.run(s.Command.of(s.TurnSend(session=sid, text="hello")))
        await asyncio.wait_for(closing.wait(), 3)
        late = (await d.run(s.Command.of(s.TurnSend(session=sid, text="hello again", mode="steer")))).result
        assert late == {"accepted": True}
        release.set()
        for _ in range(100):
            if not eng._tasks:
                break
            await asyncio.sleep(0.02)
        users = [m for m in eng.store.messages(sid) if m.role == "user"]
        assert [m.text() for m in users] == ["hello", "hello again"]
        assert users[-1].meta["author"] == "principal"
        assert users[-1].id
        assert not eng.loop.state(sid).steer
        assert len(events_of(eng.hub, "turn.completed")) == 2
        await eng.close()
    asyncio.run(go())
