"""Durable stream recovery and typed context through an offline core and HTTP bridge."""
from __future__ import annotations

import asyncio
import json
from collections import deque

from exo.protocol import schema as s
from exo_command import api
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import DispatcherSource
from test_chat import FakeSource, centre, ev, http
from test_protocol import make


def test_expired_ring_cursor_replays_durable_core_and_refreshes_transcript(tmp_path):
    async def go():
        eng, disp = make(tmp_path, [("hello", ["durable answer"])])
        src = DispatcherSource(disp)
        sid = (await src.call(s.SessionCreate()))["session"]["id"]
        cursor = eng.hub.seq
        await eng.loop.send(sid, "hello")
        durable = (await src.call(s.EventsSubscribe(since=cursor)))["events"]
        c = CommandCentre(load_settings({}), src)
        c.ring = deque(maxlen=2)
        await c.bootstrap()
        assert c.ring[0]["seq"] > cursor + 1
        web = WebServer(c, token="t0k")
        port = await web.start("127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"GET /api/events?token=t0k HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nLast-Event-ID: {cursor}\r\n\r\n".encode())
            await writer.drain()
            await asyncio.wait_for(reader.readuntil(b"\n\n"), 2)
            assert (await reader.readuntil(b"\n\n")).startswith(b"event: gap")
            replayed = []
            while True:
                frame = await asyncio.wait_for(reader.readuntil(b"\n\n"), 2)
                if frame.startswith(b"event: synced"):
                    break
                replayed.append(json.loads(frame.split(b"data: ", 1)[1]))
            assert replayed == durable
            code, body = await http(port, "GET", f"/api/chat/sessions/{sid}")
            transcript = json.loads(body)
            assert code == 200 and [m["role"] for m in transcript["messages"]] == ["user", "assistant"]
            assert transcript["messages"][-1]["content"][0]["text"] == "durable answer"
            # Private creation follows the actual SessionCreate.kind schema, never a model message.
            code, body = await http(port, "POST", "/api/chat/sessions", {"kind": "private"})
            assert code == 200 and json.loads(body)["session"]["kind"] == "private"
            assert (await http(port, "POST", "/api/chat/sessions", {"kind": "anything"}))[0] == 400
            writer.close()
            await writer.wait_closed()
        finally:
            await web.close()
            await eng.close()
    asyncio.run(go())


def test_snapshot_cutoff_precedes_rpc_and_replay_queues_live_events():
    async def go():
        class Source(FakeSource):
            async def call(self, body):
                if isinstance(body, s.SessionGet):
                    c.state.seq = 12
                    return {"session": {"id": "s1"}, "messages": []}
                if isinstance(body, s.EventsSubscribe):
                    c.record(ev(13, "notice", {"text": "live"}))
                    return {"seq": 12, "events": [ev(11, "notice", {"text": "durable"})]}
                return await super().call(body)
        c = centre(Source())
        c.state.seq = 10
        assert (await api.dispatch(c, "GET", "/api/chat/sessions/s1", {}, {}))[1]["seq"] == 10
        c.record(ev(12, "notice", {"text": "cached"}))
        q = c.subscribe_events()
        events, gap = await c.replay_events(9)
        assert gap == {"since": 9, "reset": False} and [e["seq"] for e in events] == [11]
        assert (await q.get())["seq"] == 13
        c.unsubscribe_events(q)
    asyncio.run(go())


def test_a_cursor_from_an_older_core_epoch_resets_explicitly():
    async def go():
        class Source(FakeSource):
            async def call(self, body):
                self.calls.append(body)
                return {"seq": 3, "events": [ev(3, "notice", {"text": "new core"})] if not body.since else []}
        src = Source()
        c = centre(src)
        events, gap = await c.replay_events(100)
        assert gap == {"since": 100, "reset": True} and events[0]["seq"] == 3
        assert [b.since for b in src.calls] == [100, 0]
    asyncio.run(go())
