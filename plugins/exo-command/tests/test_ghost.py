"""D-045: no approval card outlives its request, the event stream resumes where it stopped and survives an HTTP
error, and a queued steer can be pulled back."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess

import pytest

from exo.protocol import schema as s
from exo_command import api
from exo_command.config import load_settings
from exo_command.server import STATIC, WebServer
from exo_command.service import CommandCentre
from test_chat import FakeSource, ev

TOKEN = "ghost-test-token-0123456789"


def asks(seq, rid="r1", sid="s1"):
    return ev(seq, "approval.requested", {"request_id": rid, "summary": "run it", "risk": "medium"}, sid)


class Listing(FakeSource):
    """approvals.list answers what ``open`` holds; ``before`` runs inside the call (events arriving meanwhile)."""

    def __init__(self, open_ids=(), before=None):
        super().__init__()
        self.open_ids, self.before = list(open_ids), before

    async def call(self, body):
        if type(body).TYPE == "approvals.list":
            if self.before:
                self.before()
            return {"approvals": [{"request_id": r, "summary": "x"} for r in self.open_ids]}
        return await super().call(body)


def pending(c):
    return [a["request_id"] for a in c.section("approvals")["pending"]]


def test_reconcile_drops_a_card_the_core_no_longer_holds():
    async def go():
        c = CommandCentre(load_settings({}), Listing())
        c.state.apply(asks(5))
        assert pending(c) == ["r1"]
        await c.reconcile_approvals()
        assert pending(c) == []
        assert c.section("approvals")["counts"]["pending"] == 0

    asyncio.run(go())


def test_reconcile_keeps_a_card_the_core_still_holds_and_one_that_arrived_meanwhile():
    async def go():
        c = CommandCentre(load_settings({}), Listing(open_ids=["r1"], before=lambda: c.state.apply(asks(9, "r2"))))
        c.state.apply(asks(5))
        await c.reconcile_approvals()
        assert sorted(pending(c)) == ["r1", "r2"]  # r2's event landed after the list was taken: not stale

    asyncio.run(go())


def test_bootstrap_replays_an_old_request_and_then_drops_it():
    async def go():
        src = Listing()
        src.answers["events.subscribe"] = {"events": [asks(3)], "seq": 3}
        c = CommandCentre(load_settings({}), src)
        await c.bootstrap()
        assert pending(c) == []

    asyncio.run(go())


async def http(port, path, headers=None, method="GET", body=None, read=True):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    hs = "".join(f"{k}: {v}\r\n" for k, v in {"X-Exo-Token": TOKEN, **(headers or {})}.items())
    w.write(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: {len(data)}\r\n{hs}\r\n".encode() + data)
    await w.drain()
    return r, w


def test_section_route_asks_the_core_before_answering():
    async def go():
        c = CommandCentre(load_settings({}), Listing())
        c.state.apply(asks(5))
        web = WebServer(c, token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        r, w = await http(port, "/api/section/approvals")
        raw = await r.read()
        w.close()
        assert json.loads(raw.partition(b"\r\n\r\n")[2])["pending"] == []
        await web.close()

    asyncio.run(go())


def test_event_stream_resumes_from_the_later_of_since_and_last_event_id():
    """A browser that reconnects repeats its first URL (old ?since=) and adds Last-Event-ID."""
    async def go():
        c = CommandCentre(load_settings({}), FakeSource())
        for n in range(1, 10):
            c.record(ev(n, "message.delta", {"message_id": "m", "text": f"t{n} "}))
        web = WebServer(c, token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        r, w = await http(port, f"/api/events?since=2&token={TOKEN}", {"Last-Event-ID": "7"})
        got = b""
        try:
            while b"id: 9" not in got:
                got += await asyncio.wait_for(r.read(512), 2)
        finally:
            w.close()
        ids = [int(x) for x in re.findall(rb"^id: (\d+)$", got, re.M)]
        assert ids == [8, 9], ids  # not 3..9: that would type the same words twice
        await web.close()

    asyncio.run(go())


def test_unsteer_route_pulls_a_queued_message_back():
    src = FakeSource({"command.run": {"output": {"cancelled": [{"id": "m_1", "text": "hello"}]}},
                      "turn.send": {"steered": True, "message_id": "m_1"}})
    c = CommandCentre(load_settings({}), src)
    sent = asyncio.run(api.dispatch(c, "POST", "/api/chat/sessions/s1/send", {}, {"text": "hello", "mode": "steer"}))
    assert sent == (200, {"steered": True, "message_id": "m_1"})
    code, out = asyncio.run(api.dispatch(c, "POST", "/api/chat/sessions/s1/unsteer", {}, {"message_id": "m_1"}))
    assert code == 200 and out == {"cancelled": [{"id": "m_1", "text": "hello"}]}
    cmd = src.calls[-1]
    assert isinstance(cmd, s.CommandRun) and (cmd.name, cmd.args, cmd.session) == ("unsteer", "m_1", "s1")
    assert asyncio.run(api.dispatch(c, "GET", "/api/chat/sessions/s1/unsteer", {}, {}))[0] in (404, 405)


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_chat_page_has_the_steer_chip_and_the_resync():
    js = text("chat.js")
    assert "/unsteer" in js and "steer.cancelled" in js and "xc-steer-chip" in js and "ArrowUp" in js
    assert "syncApprovals" in js and "gone.add" in js          # the server's list replaces what the page holds
    assert 'e.status === 404' in js                            # answering a closed approval removes its card
    assert "xc-steer-chip" in text("style.css")


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_bus_reopens_a_closed_stream_from_the_last_seq(tmp_path):
    """EventSource gives up for good on an HTTP error (403 after a restart, 502 while the core restarts)."""
    (tmp_path / "dom.mjs").write_text(text("dom.js"))
    (tmp_path / "bus.mjs").write_text(text("bus.js").replace("./dom.js", "./dom.mjs"))
    (tmp_path / "t.mjs").write_text('''
const made = [];
class ES { addEventListener(type, fn) { this[type] = fn; } constructor(url) { this.url = url; this.readyState = 1; made.push(this); } close() { this.readyState = 2; } }
globalThis.EventSource = ES;
globalThis.sessionStorage = { getItem: () => "tok" };
globalThis.fetch = async (path) => ({ ok: true, status: 200, json: async () => (path.includes("panels") ? { panels: [], seq: 5 } : { ok: true }) });
const bus = await import("./bus.mjs");
const got = [];
bus.on((e) => got.push(e.seq));
await bus.start();
const first = made[0];
first.onopen();
first.onmessage({ data: JSON.stringify({ seq: 6, type: "x", body: {} }) });
first.readyState = 2; first.onerror();            // closed for good: the browser will not retry
await new Promise((r) => setTimeout(r, 1500));
const second = made[1];
second.onopen();
for (const n of [5, 6, 7]) second.onmessage({ data: JSON.stringify({ seq: n, type: "x", body: {} }) });
console.log(JSON.stringify({ urls: made.map((m) => m.url), got }));
process.exit(0);
''')
    out = subprocess.run([NODE, str(tmp_path / "t.mjs")], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert len(res["urls"]) == 2 and "since=6" in res["urls"][1] and "token=tok" in res["urls"][1]
    assert res["got"] == [6, 7]  # 5 and 6 are replays the page already handled


def test_rename_route_runs_the_core_rename_command():
    src = FakeSource({"command.run": {"output": {"session": {"id": "s1", "title": "New name"}}}})
    c = CommandCentre(load_settings({}), src)
    code, out = asyncio.run(api.dispatch(c, "POST", "/api/chat/sessions/s1/rename", {}, {"title": "New name"}))
    assert code == 200 and out["session"]["title"] == "New name"
    cmd = src.calls[-1]
    assert isinstance(cmd, s.CommandRun) and (cmd.name, cmd.args, cmd.session) == ("rename", "New name", "s1")
    assert asyncio.run(api.dispatch(c, "POST", "/api/chat/sessions/s1/rename", {}, {}))[0] == 400
    assert "/rename" in text("chat.js") and "dblclick" in text("chat.js")
