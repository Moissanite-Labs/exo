"""The command centre as a protocol client: in-process server, fake model, no network."""
from __future__ import annotations

import asyncio
import json

from exo.core.approvals import Pending
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import DispatcherSource
from exo_command.state import State


class Keyed:
    """Fake provider: the first rule whose key is in the latest user message gives the steps; an
    asyncio.Event step holds the call until it is set."""

    name = "fake"

    def __init__(self, rules):
        self.rules = rules

    async def stream(self, messages, tools, **opts):
        users = [i for i, m in enumerate(messages) if m.role == "user"]
        text = messages[users[-1]].text() if users else ""
        step = sum(1 for m in messages[users[-1] + 1:] if m.role == "assistant") if users else 0
        spec = "fallback"
        for key, steps in self.rules:
            if key in text:
                spec = steps[min(step, len(steps) - 1)]
                break
        if isinstance(spec, asyncio.Event):
            await spec.wait()
            spec = "child finished"
        async for ev in FakeProvider([spec]).stream(messages, tools):
            yield ev


def make(tmp_path, rules):
    (tmp_path / ".git").mkdir(exist_ok=True)  # a repo: no cwd guard notice (D-032) among the events counted
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    eng.loop.provider = Keyed(rules)
    return eng, Dispatcher(eng)


async def until(pred, what, tries=400):
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for " + what)


def test_delegated_task_appears_and_finishes_in_command_centre(tmp_path):
    gate = asyncio.Event()
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD find the loader", "role": "scout"}), "parent done"]),
             ("CHILD", [gate])]
    eng, disp = make(tmp_path, rules)

    async def main():
        centre = CommandCentre(load_settings({"principal_store": str(tmp_path / "store")}), DispatcherSource(disp))
        await centre.bootstrap()
        follow = asyncio.create_task(centre.follow())
        sid = (await centre.source.call(s.SessionCreate(title="parent", seat="lead")))["session"]["id"]
        await centre.source.call(s.TurnSend(session=sid, text="PARENT go"))

        # The task appears while the child runs (task.updated: queued then running).
        await until(lambda: any(t["status"] == "running" for t in centre.state.tasks.values()), "running task")
        snap = centre.snapshot()
        cols = {c["status"]: c for c in snap["tasks"]["columns"]}
        assert "running" in cols and snap["tasks"]["open"] == 1
        card = cols["running"]["tasks"][0]
        assert card["title"].startswith("CHILD") and card["parent"] == sid
        live = {c["session_id"]: c for c in snap["sessions"]["active"]}
        assert live[sid]["status"] == "running" and live[sid]["seat"] == "lead"
        kids = [c for c in snap["sessions"]["active"] if c["kind"] == "subagent"]
        assert kids and kids[0]["task_id"] == card["task_id"]

        gate.set()  # the child finishes; the board moves the card to done
        await until(lambda: all(t["status"] == "done" for t in centre.state.tasks.values()), "done task")
        await until(lambda: not centre.state.sessions[sid]["running"], "parent turn end")
        snap = centre.snapshot()
        assert [c["status"] for c in snap["tasks"]["columns"]] == ["done"]
        done = snap["tasks"]["columns"][0]["tasks"][0]
        assert done["detail"].startswith("Done: task") and done["updates"] == 3
        assert snap["tasks"]["open"] == 0 and snap["sessions"]["counts"]["subagents"] == 0
        assert [c["kind"] for c in snap["sessions"]["past"]] == ["subagent"]
        assert snap["sessions"]["past"][0]["status"] == "done"
        assert "Tasks: 0 open, 0 failed." in centre.summary()
        follow.cancel()

    asyncio.run(main())


def test_bootstrap_replays_history_and_sync_polls(tmp_path):
    rules = [("PARENT", [tool_turn("delegate", {"goal": "CHILD x", "role": "scout"}), "ok"]), ("CHILD", ["found it"])]
    eng, disp = make(tmp_path, rules)

    async def main():
        src = DispatcherSource(disp)
        sid = (await src.call(s.SessionCreate(title="earlier")))["session"]["id"]
        await src.call(s.TurnSend(session=sid, text="PARENT earlier"))
        await until(lambda: eng.hub.since(0) and any(e.type == "turn.completed" and e.session == sid
                                                     for e in eng.hub.since(0)), "turn")
        centre = CommandCentre(load_settings(), src)  # joins late
        await centre.bootstrap()
        t = list(centre.state.tasks.values())
        assert len(t) == 1 and t[0]["status"] == "done" and sid in centre.state.sessions
        seq = centre.state.seq
        await src.call(s.SessionCreate(title="later"))
        assert await centre.sync() == 1 and centre.state.seq == seq + 1
        assert await centre.sync() == 0

    asyncio.run(main())


def test_approvals_listed_and_answered(tmp_path):
    eng, disp = make(tmp_path, [("x", ["ok"])])

    async def main():
        centre = CommandCentre(load_settings(), DispatcherSource(disp))
        await centre.bootstrap()
        sid = (await centre.source.call(s.SessionCreate(title="gated")))["session"]["id"]
        fut = asyncio.get_running_loop().create_future()
        req = s.ApprovalRequested(request_id="r1", summary="run rm -rf build", risk="high")
        eng.approvals.open["r1"] = Pending(fut, sid, req)
        eng.hub.publish(req, sid)
        await centre.sync()
        snap = centre.snapshot()
        pend = snap["approvals"]["pending"]
        assert snap["approvals"]["counts"]["pending"] == 1 and pend[0]["risk"] == "high"
        assert [c["status"] for c in snap["sessions"]["active"] if c["session_id"] == sid] == ["waiting"]
        await centre.respond_approval("r1", "deny")
        assert fut.result() == "deny"
        await centre.sync()
        snap = centre.snapshot()
        assert snap["approvals"]["counts"]["pending"] == 0 and snap["approvals"]["recent"][0]["decision"] == "deny"

    asyncio.run(main())


def test_state_costs_decisions_and_projects(tmp_path):
    st = State()
    ev = lambda seq, typ, body, sid=None, ts="2026-10-11T10:00:00Z": {"seq": seq, "type": typ, "body": body,
                                                                       "session": sid, "ts": ts}
    info = {"id": "s1", "title": "wire the exporter", "seat": "builder", "model": "m-std", "cwd": str(tmp_path / "work" / "repo")}
    st.apply(ev(1, "session.created", {"session": info}, "s1"))
    st.apply(ev(2, "turn.started", {"turn_id": "t1", "model": "m-std"}, "s1"))
    st.apply(ev(3, "usage.updated", {"usage": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.5},
                                     "session_total": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.5}}, "s1"))
    st.apply(ev(4, "usage.updated", {"usage": {"input_tokens": 50, "output_tokens": 5, "cost_usd": 0.25}}, "s1"))
    st.apply(ev(5, "decision.queued", {"decision_id": "d1", "title": "Ship it?", "source": "lead"}))
    st.apply(ev(6, "notice", {"text": "heads up"}))

    centre = CommandCentre(load_settings({"principal_store": str(tmp_path)}))
    centre.state = st
    now = 1791712800.0  # 2026-10-11T10:00:00Z
    c = centre.section("costs", now=now)
    assert c["total_usd"] == 0.75 and c["by_seat"][0]["seat"] == "builder"
    assert c["top"][0]["input_tokens"] == 150 and c["by_model"][0]["model"] == "m-std"
    d = centre.section("decisions", now=now)
    assert d["counts"]["waiting"] == 1 and d["waiting"][0]["title"] == "Ship it?" and d["configured"]
    assert centre.section("projects", now=now)["ok"] is True
    assert st.notices[0]["text"] == "heads up"


def test_web_server_serves_snapshot_stream_and_answers_approvals(tmp_path):
    eng, disp = make(tmp_path, [("x", ["ok"])])

    async def http(port, method, path, body=None, headers=None):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        data = json.dumps(body).encode() if body is not None else b""
        hs = "".join("{}: {}\r\n".format(k, v) for k, v in dict({"X-Exo-Token": "t0k"}, **(headers or {})).items())
        w.write("{} {} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Length: {}\r\n{}\r\n".format(
            method, path, port, len(data), hs).encode() + data)
        await w.drain()
        raw = await r.read()
        w.close()
        head, _, payload = raw.partition(b"\r\n\r\n")
        return int(head.split()[1]), payload

    async def main():
        centre = CommandCentre(load_settings(), DispatcherSource(disp))
        await centre.bootstrap()
        web = WebServer(centre, token="t0k")
        port = await web.start("127.0.0.1", 0)
        code, page = await http(port, "GET", "/")
        assert code == 200 and b"/static/app.js" in page
        assert (await http(port, "GET", "/static/app.js"))[0] == 200
        assert (await http(port, "GET", "/static/../server.py"))[0] == 404
        code, body = await http(port, "GET", "/api/snapshot?sections=tasks,costs")
        snap = json.loads(body)
        assert code == 200 and snap["tasks"]["ok"] and "sessions" not in snap and snap["connected"]
        assert (await http(port, "GET", "/api/section/nope"))[0] == 404
        assert json.loads((await http(port, "GET", "/api/health"))[1])["connected"] is True

        sid = (await centre.source.call(s.SessionCreate(title="g")))["session"]["id"]
        fut = asyncio.get_running_loop().create_future()
        req = s.ApprovalRequested(request_id="r9", summary="write file")
        eng.approvals.open["r9"] = Pending(fut, sid, req)
        eng.hub.publish(req, sid)
        await centre.sync()
        assert (await http(port, "POST", "/api/approvals/r9", {"decision": "maybe"}))[0] == 400
        assert (await http(port, "POST", "/api/approvals/r9", {"decision": "allow"},
                           {"Origin": "http://evil.example"}))[0] == 403
        assert (await http(port, "GET", "/api/approvals/r9"))[0] == 405
        assert (await http(port, "POST", "/api/approvals/r9", {"decision": "allow"}))[0] == 200
        assert fut.result() == "allow"
        assert (await http(port, "POST", "/api/approvals/nope", {"decision": "allow"}))[0] == 404

        # SSE: a change in the core reaches the page.
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("GET /api/stream?token=t0k HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\r\n".format(port).encode())
        await w.drain()
        await asyncio.wait_for(r.readuntil(b"\n\n"), 2)   # headers + comment
        await asyncio.wait_for(r.readuntil(b"\n\n"), 2)   # initial seq
        await centre.source.call(s.SessionCreate(title="more"))
        await centre.sync()
        chunk = await asyncio.wait_for(r.readuntil(b"\n\n"), 2)
        assert chunk.startswith(b"data: ") and json.loads(chunk[6:])["seq"] == centre.state.seq
        w.close()
        await web.close()

    asyncio.run(main())
