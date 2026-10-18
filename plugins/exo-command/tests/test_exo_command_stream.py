"""The web's copy of the core's event stream. A re-bootstrap after the core's stream drops folds nothing twice, a core
whose log is behind the page starts the page over, and a chat reader that falls 1000 events behind loses no event."""
from __future__ import annotations

import asyncio
import json

from exo.core.approvals import Pending
from exo.core.models.fake import tool_turn
from exo.protocol import schema as s
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import DispatcherSource
from exo_command.state import State
from test_chat import FakeSource, ev
from test_protocol import make, until


def status(c, sid):
    return next(x["status"] for x in c.section("sessions")["active"] if x["session_id"] == sid)


def test_state_folds_each_seq_once():
    events = [ev(1, "session.created", {"session": {"id": "s1", "title": "t"}}),
              ev(2, "message.completed", {"message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}),
              ev(3, "tool.started", {"call": {"id": "c1", "name": "glob", "arguments": {"pattern": "*"}}}),
              ev(4, "task.updated", {"task_id": "t1", "status": "running", "title": "scout"}),
              ev(5, "approval.requested", {"request_id": "r1", "summary": "run ls", "risk": "low"}),
              ev(6, "approval.requested", {"request_id": "r2", "summary": "run rm", "risk": "high"}),
              ev(7, "approval.resolved", {"request_id": "r2", "decision": "deny"}),
              ev(8, "notice", {"text": "heads up"}, None)]
    st = State()
    st.apply_all(events)
    st.apply_all(events)  # the replay a re-bootstrap folds after the core's stream dropped
    s1 = st.sessions["s1"]
    assert (s1["message_count"], s1["tool_call_count"], s1["pending_approvals"]) == (1, 1, 1)
    assert st.tasks["t1"]["events"] == 1 and len(st.resolved) == 1 and len(st.notices) == 1
    assert sorted(st.approvals) == ["r1"] and st.seq == 8
    st.apply({"type": "notice", "body": {"text": "no seq"}})  # an event with no seq still applies
    assert len(st.notices) == 2 and st.seq == 8


def test_rebootstrap_after_the_stream_drops_counts_nothing_twice(tmp_path):
    eng, disp = make(tmp_path, [("go", [tool_turn("glob", {"pattern": "*.none"}), "done"])])

    async def main():
        c = CommandCentre(load_settings({}), DispatcherSource(disp))
        await c.bootstrap()
        sid = (await c.source.call(s.SessionCreate(title="t")))["session"]["id"]
        await c.source.call(s.TurnSend(session=sid, text="go"))
        await until(lambda: any(e.type == "turn.completed" for e in eng.hub.since(0)), "turn")
        req = s.ApprovalRequested(request_id="r1", summary="run rm -rf build", risk="high")
        fut = asyncio.get_running_loop().create_future()
        eng.approvals.open["r1"] = Pending(fut, sid, req)
        eng.hub.publish(req, sid)
        await c.sync()
        sess = c.state.sessions[sid]
        before = (sess["message_count"], sess["tool_call_count"], sess["pending_approvals"],
                  len(c.state.resolved), len(c.state.notices), c.state.seq)
        assert before[0] > 0 and before[1] == 1 and before[2] == 1 and status(c, sid) == "waiting"
        await c.bootstrap()  # what the web's feed loop does once the core's stream has ended
        sess = c.state.sessions[sid]
        assert (sess["message_count"], sess["tool_call_count"], sess["pending_approvals"],
                len(c.state.resolved), len(c.state.notices), c.state.seq) == before
        await c.respond_approval("r1", "allow")
        await c.sync()
        assert fut.result() == "allow" and c.state.sessions[sid]["pending_approvals"] == 0
        assert status(c, sid) == "idle"

    asyncio.run(main())
    asyncio.run(eng.close())


def test_core_restart_drops_a_request_the_new_core_does_not_hold(tmp_path):
    """The new core opens the same home, so its log (and hello seq) carries on from the old one."""
    async def main():
        eng, disp = make(tmp_path, [("x", ["ok"])])
        c = CommandCentre(load_settings({}), DispatcherSource(disp))
        await c.bootstrap()
        sid = (await c.source.call(s.SessionCreate(title="gated")))["session"]["id"]
        req = s.ApprovalRequested(request_id="r1", summary="run rm -rf build", risk="high")
        eng.approvals.open["r1"] = Pending(asyncio.get_running_loop().create_future(), sid, req)
        eng.hub.publish(req, sid)
        await c.sync()
        assert status(c, sid) == "waiting"
        eng.hub.close()
        await eng.close()
        eng2, disp2 = make(tmp_path, [("x", ["ok"])])
        try:
            assert eng2.hub.seq == c.state.seq
            c.source = DispatcherSource(disp2)
            await c.bootstrap()
            assert sid in c.state.sessions and c.state.sessions[sid]["pending_approvals"] == 0
            assert status(c, sid) == "idle" and c.section("approvals")["counts"]["pending"] == 0
        finally:
            await eng2.close()

    asyncio.run(main())


class Core(FakeSource):
    """A core at ``head`` whose log holds ``log``."""

    def __init__(self, head, log):
        super().__init__()
        self.head, self.log = head, log

    async def call(self, body):
        t = type(body).TYPE
        if t == "hello":
            return {"seq": self.head}
        if t == "events.subscribe":
            return {"seq": self.head, "events": [e for e in self.log if e["seq"] > body.since]}
        return await super().call(body)


def test_a_core_whose_log_is_behind_the_page_starts_the_page_over():
    async def main():
        old = [ev(99, "session.created", {"session": {"id": "s-old"}}, "s-old"),
               ev(100, "ui.panel", {"panel_id": "p", "action": "open", "kind": "markdown", "text": "x"}, "s-old")]
        c = CommandCentre(load_settings({}), Core(100, old))
        await c.bootstrap()
        await c.bootstrap()  # the same core again: nothing changes
        assert "s-old" in c.state.sessions and c.state.seq == 100 and "p" in c.panels
        q = c.subscribe_events()
        c.source = Core(2, [ev(1, "session.created", {"session": {"id": "s-new"}}, "s-new"),
                            ev(2, "notice", {"text": "new core"}, None)])  # a new home: its log starts again at 1
        await c.bootstrap()
        assert "s-old" not in c.state.sessions and "s-new" in c.state.sessions and c.state.seq == 2
        assert [e["seq"] for e in c.ring] == [1, 2] and c.panels == {}
        assert q not in c._event_subs and q.get_nowait() is None and q.empty()  # the chat stream reconnects
        c.record(ev(3, "notice", {"text": "live"}, None))
        assert [e["seq"] for e in c.events_since(2)] == [3]

    asyncio.run(main())


def test_a_reader_cut_off_for_falling_behind_keeps_every_event_for_its_resume():
    c = CommandCentre(load_settings({}), FakeSource())
    q = c.subscribe_events()
    for n in range(1, 1002):  # one burst, no await in between: the reader's 1000 slots overflow
        c.record(ev(n, "tool.output", {"call_id": "c", "text": f"{n}\n"}))
    assert q not in c._event_subs
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items[-1] is None
    delivered = [e["seq"] for e in items[:-1]]
    resumed = [e["seq"] for e in c.events_since(max(delivered, default=0))]
    assert sorted(delivered + resumed) == list(range(1, 1002))


def test_a_reader_that_falls_behind_resumes_over_http_without_a_gap():
    async def main():
        c = CommandCentre(load_settings({}), FakeSource())
        web = WebServer(c, token="t0k")
        port = await web.start("127.0.0.1", 0)

        async def open_stream(last):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(f"GET /api/events?token=t0k HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nLast-Event-ID: {last}\r\n\r\n"
                    .encode())
            await w.drain()
            await asyncio.wait_for(r.readuntil(b"\n\n"), 2)  # status line, headers and the comment line
            return r, w

        async def frames(r, n=None):
            """Event ids until ``synced``, ``n`` frames, or the server closing the stream."""
            out = []
            while n is None or len(out) < n:
                try:
                    f = await asyncio.wait_for(r.readuntil(b"\n\n"), 2)
                except asyncio.IncompleteReadError:
                    break
                if f.startswith(b"event: synced"):
                    break
                if f.startswith(b"id: "):
                    out.append(json.loads(f.split(b"data: ", 1)[1])["seq"])
            return out

        try:
            r, w = await open_stream(0)
            assert await frames(r) == []
            for n in range(1, 4):
                c.record(ev(n, "message.delta", {"message_id": "m", "text": f"{n} "}))
            first = await frames(r, 3)
            assert first == [1, 2, 3]
            for n in range(4, 1005):  # a burst folded in one go, before the writer runs again
                c.record(ev(n, "tool.output", {"call_id": "c", "text": f"{n}\n"}))
            first += await frames(r)  # the server ends this stream
            w.close()
            r2, w2 = await open_stream(first[-1])  # what EventSource does: reconnect with Last-Event-ID
            again = await frames(r2)
            w2.close()
            got = first + again
            assert sorted(got) == list(range(1, 1005)) and len(set(got)) == len(got)
        finally:
            await web.close()

    asyncio.run(main())
