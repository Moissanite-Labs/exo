"""Protocol additions for the web client: session.list status, approvals.list, usage.updated model."""
import asyncio

from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher


def _engine(tmp_path):
    return CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"reply": "ok"}},
                      cwd=str(tmp_path), env={})


def test_session_list_status_approvals_list_and_usage_model(tmp_path):
    eng = _engine(tmp_path)
    disp = Dispatcher(eng, caller="principal:tui")

    async def call(body):
        r = await disp.run(s.Command.of(body))
        assert r.ok, r.error
        return r.result

    async def main():
        sid = (await call(s.SessionCreate(title="t")))["session"]["id"]
        assert [x["status"] for x in (await call(s.SessionList()))["sessions"]] == ["idle"]
        assert (await call(s.ApprovalsList()))["approvals"] == []
        ask = asyncio.create_task(eng.approvals.ask(sid, "post it", risk="high"))
        await asyncio.sleep(0.01)
        [a] = (await call(s.ApprovalsList()))["approvals"]
        assert a["summary"] == "post it" and a["risk"] == "high" and a["session"] == sid
        assert (await call(s.ApprovalsList(session="other")))["approvals"] == []
        await call(s.ApprovalRespond(request_id=a["request_id"], decision="allow"))
        assert await ask == "allow"
        assert (await call(s.ApprovalsList()))["approvals"] == []
        await call(s.TurnSend(session=sid, text="hi"))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if any(e.type == "turn.completed" for e in eng.hub.since(0)):
                break
        usage = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.UsageUpdated)]
        assert usage and usage[0].model == "fake:x"

    asyncio.run(main())
