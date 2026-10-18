"""session.get and session.resume carry the session's status as session.list does: a client that opens a thread
knows whether a turn runs there (the terminal shows LIVE and ctrl+c stops it)."""
from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher

FAKE = {"rules": [{"match": "ask", "steps": [{"tool": "bash", "args": {"command": "echo asked"}}, "done"]}]}


def engine(tmp_path) -> CoreEngine:
    eng = CoreEngine(tmp_path / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     cwd=str(tmp_path), env={})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: {"action": "approve", "message": "run echo?",
                                                               "rule_key": "echo"}, "test-ask")
    return eng


async def until(cond, timeout=5.0):
    async def wait():
        while not cond():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(wait(), timeout)


def test_session_get_and_resume_say_live_while_a_turn_runs(tmp_path):
    async def go():
        eng = engine(tmp_path)
        d = Dispatcher(eng, caller="principal:tui")

        async def status(cmd):
            r = await d.run(s.Command.of(cmd))
            assert r.ok, r.error
            return r.result["session"]["status"]

        sid = (await d.run(s.Command.of(s.SessionCreate(title="t")))).result["session"]["id"]
        assert await status(s.SessionGet(session=sid)) == "idle"
        await d.run(s.Command.of(s.TurnSend(session=sid, text="ask")))
        await until(lambda: eng.approvals.open)  # the turn holds on the approval: it is running
        assert await status(s.SessionGet(session=sid)) == "live"
        assert await status(s.SessionResume(session=sid)) == "live"
        listed = (await d.run(s.Command.of(s.SessionList()))).result["sessions"]
        assert [r["status"] for r in listed if r["id"] == sid] == ["live"]  # the same word session.list uses
        rid = next(iter(eng.approvals.open))
        assert (await d.run(s.Command.of(s.ApprovalRespond(request_id=rid, decision="allow")))).ok
        await until(lambda: not eng.loop.state(sid).running)
        assert await status(s.SessionGet(session=sid)) == "idle"
        assert await status(s.SessionResume(session=sid)) == "idle"

    asyncio.run(go())
