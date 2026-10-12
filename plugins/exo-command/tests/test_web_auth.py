"""D-039: every route and both event streams of the web client need the per-launch page token; the Host header must
be this server's own loopback address (DNS rebinding fails); the Origin check stays. Raw HTTP so every header is
the test's. An approval answered from the page reaches the core as the principal, and the web re-attaches to a
restarted core."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from exo.protocol import schema as s
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre

TOKEN = "page-token-for-tests-0123456789abcdef"
REPO = Path(__file__).resolve().parents[3]


class Src:
    def __init__(self):
        self.calls = []

    async def call(self, body):
        self.calls.append(body)
        t = type(body).TYPE
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.create":
            return {"session": {"id": "s1"}}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


async def raw(port, method, path, headers=None, body=None, read_all=True):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    hs = dict({"Host": "127.0.0.1:{}".format(port)}, **(headers or {}))
    head = "".join("{}: {}\r\n".format(k, v) for k, v in hs.items() if v is not None)
    w.write("{} {} HTTP/1.1\r\n{}Content-Length: {}\r\n\r\n".format(method, path, head, len(data)).encode() + data)
    await w.drain()
    if read_all:
        got = await asyncio.wait_for(r.read(), 5)
        w.close()
    else:
        got = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
        w.close()
    status = int(got.split()[1])
    return status, got.partition(b"\r\n\r\n")[2]


ROUTES = [("GET", "/api/health", None), ("GET", "/api/snapshot", None), ("GET", "/api/section/tasks", None),
          ("GET", "/api/chat/sessions", None), ("POST", "/api/chat/sessions", {"title": "x"}),
          ("GET", "/api/chat/panels", None), ("POST", "/api/approvals/r1", {"decision": "allow_always"}),
          ("POST", "/api/schedule", {"action": "list"}), ("GET", "/api/providers", None),
          ("POST", "/api/feedback", {"session": "s1", "verdict": "accept"}),
          ("POST", "/api/chat/sessions/s1/unsteer", {"message_id": "m_1"})]
STREAMS = ["/api/events", "/api/stream"]


def _web():
    return WebServer(CommandCentre(load_settings({}), Src()), token=TOKEN)


def test_every_route_and_stream_needs_the_page_token():
    async def go():
        web = _web()
        port = await web.start("127.0.0.1", 0)
        try:
            out = {}
            for method, path, body in ROUTES:
                out[path, "none"] = (await raw(port, method, path, body=body))[0]
                out[path, "wrong"] = (await raw(port, method, path, {"X-Exo-Token": "nope"}, body))[0]
                out[path, "good"] = (await raw(port, method, path, {"X-Exo-Token": TOKEN}, body))[0]
            for path in STREAMS:
                out[path, "none"] = (await raw(port, "GET", path, read_all=False))[0]
                out[path, "wrong"] = (await raw(port, "GET", path + "?token=nope", read_all=False))[0]
                out[path, "good"] = (await raw(port, "GET", path + "?token=" + TOKEN, read_all=False))[0]
                out[path, "header"] = (await raw(port, "GET", path, {"X-Exo-Token": TOKEN}, read_all=False))[0]
            return out, web.centre.source.calls
        finally:
            await web.close()

    out, calls = asyncio.run(go())
    refused = {k: v for k, v in out.items() if k[1] in ("none", "wrong") and v != 403}
    assert refused == {}, refused
    allowed = {k: v for k, v in out.items() if k[1] in ("good", "header") and v == 403}
    assert allowed == {}, allowed
    # nothing reached the core from a refused request: only the token-carrying ones did
    assert sum(isinstance(c, s.SessionCreate) for c in calls) == 1


def test_host_must_be_this_servers_loopback_address():
    async def go():
        web = _web()
        port = await web.start("127.0.0.1", 0)
        try:
            tok = {"X-Exo-Token": TOKEN}
            bad = ["rebind.example:{}".format(port), "127.0.0.1:{}".format(port + 1), "127.0.0.1", "", "evil.example"]
            got = {h: (await raw(port, "GET", "/api/health", dict(tok, Host=h)))[0] for h in bad}
            got["page:rebind"] = (await raw(port, "GET", "/", {"Host": "rebind.example:{}".format(port)}))[0]
            good = {h: (await raw(port, "GET", "/api/health", dict(tok, Host=h)))[0]
                    for h in ("127.0.0.1:{}".format(port), "localhost:{}".format(port))}
            good["page"] = (await raw(port, "GET", "/"))[0]
            good["static"] = (await raw(port, "GET", "/static/app.js"))[0]
            return got, good
        finally:
            await web.close()

    got, good = asyncio.run(go())
    assert set(got.values()) == {403}, got
    assert set(good.values()) == {200}, good


def test_origin_and_fetch_site_checks_stay():
    async def go():
        web = _web()
        port = await web.start("127.0.0.1", 0)
        try:
            tok = {"X-Exo-Token": TOKEN}
            return [
                (await raw(port, "POST", "/api/chat/sessions", dict(tok, Origin="http://evil.example"), {}))[0],
                (await raw(port, "GET", "/api/health", dict(tok, Origin="http://rebind.example:{}".format(port))))[0],
                (await raw(port, "GET", "/api/health", dict(tok, **{"Sec-Fetch-Site": "cross-site"})))[0],
                (await raw(port, "GET", "/api/health", dict(tok, Origin="http://127.0.0.1:{}".format(port))))[0],
            ]
        finally:
            await web.close()

    assert asyncio.run(go()) == [403, 403, 403, 200]


def test_the_page_never_sends_a_request_without_the_token():
    """Every fetch and EventSource in the page goes through the token helpers in dom.js."""
    static = Path(__file__).resolve().parents[1] / "exo_command" / "static"
    for f in static.glob("*.js"):
        text = f.read_text()
        if f.name != "dom.js":
            assert "fetch(" not in text, f.name
            assert "new EventSource(" not in text, f.name
    dom = (static / "dom.js").read_text()
    assert "X-Exo-Token" in dom and "export function stream(" in dom


def test_asgi_server_is_gone():
    with pytest.raises(ImportError):
        import exo_command.asgi  # noqa: F401


# ------------------------------------------------------------------ against a real core


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _core(tmp_path):
    from exo.core.engine import CoreEngine
    from exo.core.plugins import Manifest, PluginManager

    pm = PluginManager(config={"exo-gate": {"principal_store": "", "key_path": str(tmp_path / "none.key"),
                                            "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    sent = []

    def register(ctx):
        ctx.register_tool("send_message", "demo", {"parameters": {"type": "object", "properties": {
            "target": {"type": "string"}, "message": {"type": "string"}}}},
            handler=lambda args, **kw: (sent.append(args["message"]), "sent")[1], risk="high")
    pm._run_register(Manifest(name="demo"), "dir", register)
    fake = {"rules": [{"match": "announce", "steps": [
        {"tool": "send_message", "args": {"target": "#general", "message": "we shipped"}}, "announced"]}]}
    eng = CoreEngine(tmp_path / "home", {"fake": fake, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     plugins=pm, cwd=str(tmp_path), env={})
    return eng, sent


async def _until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (got := pred()):
            return got
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def test_page_answers_as_the_principal_and_reattaches_to_a_restarted_core(tmp_path, sock_dir):
    from exo.server.app import Dispatcher, HttpServer

    sock = sock_dir / "exod.sock"
    eng, sent = _core(tmp_path)

    async def serve():
        srv = HttpServer(Dispatcher(eng), principal=True)
        await srv.start_unix(sock)
        return srv, asyncio.create_task(srv.serve_forever())

    async def ask(text):
        sid = (await eng.handle(s.Command.of(s.SessionCreate()), eng.hub))["session"]["id"]
        n = len(eng._approvals)
        await Dispatcher(eng).run(s.Command.of(s.TurnSend(session=sid, text=text)))
        await _until(lambda: len(eng._approvals) > n)
        return [rid for rid, (f, _) in eng._approvals.items() if not f.done()][-1]

    async def go():
        srv, task = await serve()
        c = CommandCentre(load_settings({"socket": str(sock)}))
        c.connect()
        web = WebServer(c, token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            rid = await ask("announce one")
            no = (await raw(port, "POST", "/api/approvals/" + rid, {}, {"decision": "allow_always"}))[0]
            yes = (await raw(port, "POST", "/api/approvals/" + rid, {"X-Exo-Token": TOKEN}, {"decision": "allow"}))
            await _until(lambda: sent)
            # the core restarts: a new principal credential; the web reads it again and still answers
            task.cancel()
            await srv.close()
            srv, task = await serve()
            rid2 = await ask("announce two")
            again = (await raw(port, "POST", "/api/approvals/" + rid2, {"X-Exo-Token": TOKEN}, {"decision": "deny"}))
            await _until(lambda: not eng._tasks)
            return no, yes, again
        finally:
            await web.close()
            await c.aclose()
            task.cancel()
            await srv.close()

    no, yes, again = asyncio.run(go())
    assert no == 403 and yes[0] == 200 and again[0] == 200, (no, yes, again)
    resolved = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]
    assert [(r.decision, r.by) for r in resolved] == [("allow", "principal:web"), ("deny", "principal:web")]
    assert sent == ["we shipped"]
