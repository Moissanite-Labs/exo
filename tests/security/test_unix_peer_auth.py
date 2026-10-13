"""D-060: a Unix connection is checked with the kernel, both ways, before the principal credential is sent or
accepted. Also the snapshot cursor, stream liveness and no-resend rules that ride on it."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from exo import principal as pr
from exo.protocol import schema as s
from exo.protocol.client import Client
from exo.server.app import Dispatcher, HttpServer
from exo.server.engine import Hub

REPO = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO / "src"), str(REPO / "clients" / "tui")])}


class Probe:
    """An engine that answers every command with the caller the transport stamped."""

    hub = None

    async def handle(self, command, hub):
        return {"caller": pr.current_caller()}


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


async def _serve(sock, engine=None):
    srv = HttpServer(Dispatcher(engine or Probe()), principal=True)
    await srv.start_unix(sock)
    return srv, asyncio.create_task(srv.serve_forever())


async def _stop(srv, task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await srv.close()


def _token(sock) -> str:
    raw = pr.token_path(sock).read_text().strip()
    return json.loads(raw)["token"] if raw.startswith("{") else raw  # the pre-D-060 file held the bare token


# ---------------------------------------------------------------- the client checks the daemon first

FAKE = r'''
import socket, sys, time
path, out = sys.argv[1:]
srv = socket.socket(socket.AF_UNIX)
srv.bind(path)
srv.listen()
open(out + ".ready", "w").close()
srv.settimeout(10)
got = b""
try:
    conn, _ = srv.accept()
    conn.settimeout(2)
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        got += chunk
except OSError:
    pass
open(out, "wb").write(got)
'''


def _fake_daemon(tmp_path, sock):
    script, out = tmp_path / "fake.py", tmp_path / "fake.bytes"
    script.write_text(FAKE)
    proc = subprocess.Popen([sys.executable, str(script), str(sock), str(out)])
    end = time.monotonic() + 10
    while not Path(str(out) + ".ready").exists():
        assert time.monotonic() < end and proc.poll() is None
        time.sleep(0.02)
    return proc, out


def test_replaced_socket_gets_no_credential_byte(tmp_path, sock_dir):
    """Another process of this user binds the socket path while a real core's record is in place. The principal
    client must refuse before writing anything: the fake daemon reads zero bytes."""
    sock = sock_dir / "exod.sock"
    token = pr.issue(sock)  # a record naming this process, as a real core would
    proc, out = _fake_daemon(tmp_path, sock)

    async def main():
        async with Client(socket=str(sock), principal="tui", timeout=3) as c:
            with pytest.raises(httpx.ConnectError, match="not the core in the launch record"):
                await c.send(s.SessionList())
    try:
        asyncio.run(main())
    finally:
        proc.wait(15)
    got = out.read_bytes()
    assert token.encode() not in got and got == b""


@pytest.mark.parametrize("damage", ["stale", "symlink", "shared", "old_format", "missing"])
def test_bad_launch_record_refuses_before_any_byte(sock_dir, damage):
    """The socket is served by this process, but the record is stale (pid reused: start identity differs),
    a symlink, readable by others, the pre-D-060 plain token, or absent. Nothing may be written."""
    sock = sock_dir / "exod.sock"
    path = pr.token_path(sock)

    async def main():
        got = bytearray()

        async def take(reader, writer):
            got.extend(await reader.read(65536))
            writer.close()
        srv = await asyncio.start_unix_server(take, path=str(sock))
        token = pr.issue(sock)
        raw = path.read_text().strip()
        rec = json.loads(raw) if raw.startswith("{") else {"token": raw}
        if damage == "stale":
            path.write_text(json.dumps({**rec, "start": "1.000000"}))
        elif damage == "symlink":
            real = sock_dir / "elsewhere.json"
            real.write_text(path.read_text())
            real.chmod(0o600)
            path.unlink()
            path.symlink_to(real)
        elif damage == "shared":
            path.chmod(0o644)
        elif damage == "old_format":
            path.write_text(token + "\n")
        elif damage == "missing":
            path.unlink()
        async with Client(socket=str(sock), principal="tui", timeout=3) as c:
            with pytest.raises(httpx.ConnectError, match="principal connection refused"):
                await c.send(s.SessionList())
        await asyncio.sleep(0.05)
        srv.close()
        return bytes(got)
    assert asyncio.run(main()) == b""


def test_platform_without_peer_credentials_fails_closed(sock_dir, monkeypatch):
    sock = sock_dir / "exod.sock"

    async def main():
        srv, task = await _serve(sock)
        try:
            token = _token(sock)
            monkeypatch.setattr(sys, "platform", "sunos5")
            async with Client(socket=str(sock), principal="tui", timeout=3) as c:
                with pytest.raises(httpx.ConnectError, match="will not name the peer"):
                    await c.send(s.SessionList())
            async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(sock)), base_url="http://exod") as h:
                r = await h.post("/command", content=s.Command.of(s.SessionList()).to_json(),
                                 headers={pr.HEADER: token, "X-Exo-Client": "tui"})
            return r.json()["result"]["caller"]
        finally:
            monkeypatch.undo()
            await _stop(srv, task)
    assert asyncio.run(main()) == "local"


# ---------------------------------------------------------------- the core checks the peer


def test_core_refuses_a_peer_whose_kernel_credentials_do_not_match(sock_dir, monkeypatch):
    """The right token from a connection the kernel says is another uid is local, not the principal."""
    sock = sock_dir / "exod.sock"

    async def main():
        srv, task = await _serve(sock)
        try:
            headers = {pr.HEADER: _token(sock), "X-Exo-Client": "tui"}
            async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(sock)), base_url="http://exod") as h:
                body = s.Command.of(s.SessionList()).to_json()
                ok = (await h.post("/command", content=body, headers=headers)).json()["result"]["caller"]
                real = getattr(pr, "peer_cred", None)
                monkeypatch.setattr(pr, "peer_cred", lambda sk: (lambda c: c and (c[0], c[1] + 1))(real(sk)),
                                    raising=False)
                bad = (await h.post("/command", content=body, headers=headers)).json()["result"]["caller"]
            return ok, bad
        finally:
            await _stop(srv, task)
    assert asyncio.run(main()) == ("principal:tui", "local")
    monkeypatch.undo()
    now, tok = time.time(), "t" * 43
    assert pr.stamp(tok, tok, pr.Peer(os.getpid(), os.geteuid(), now), "tui") == "principal:tui"
    assert pr.stamp(tok, tok, pr.Peer(os.getpid(), os.geteuid() + 1, now), "tui") == "local"
    assert pr.stamp(tok, tok, pr.Peer(None, os.geteuid(), now), "tui") == "local"
    assert pr.stamp(tok, tok, pr.Peer(os.getpid(), os.geteuid(), now - 3600), "tui") == "local"  # pid reused


DETACH = r'''
import json, os, socket, sys, time
sock, out = sys.argv[1:]
raw = open(sock[:-len(".sock")] + ".principal.key").read().strip()
try:
    token = json.loads(raw)["token"]
except ValueError:
    token = raw
conn = socket.socket(socket.AF_UNIX)
conn.connect(sock)  # connected from inside the core's tree ...
if os.fork() == 0:
    time.sleep(0.8)  # ... then the connecting process exits and is reaped; this orphan uses its connection
    body = '{"id":"c1","type":"session.list","body":{}}'
    conn.sendall((f"POST /command HTTP/1.1\r\nHost: exod\r\nX-Exo-Principal: {token}\r\nX-Exo-Client: tui\r\n"
                  f"Content-Length: {len(body)}\r\n\r\n{body}").encode())
    got = b""
    while (chunk := conn.recv(65536)):
        got += chunk
    open(out, "wb").write(got.split(b"\r\n\r\n", 1)[1])
    os._exit(0)
os._exit(0)
'''

CHILD_CLIENT = r'''
import asyncio, json, sys
from exo.protocol import schema as s
from exo.protocol.client import Client

async def main():
    async with Client(socket=sys.argv[1], principal="tui") as c:
        print(json.dumps((await c.call(s.SessionList()))["caller"]))
asyncio.run(main())
'''


def test_tool_tree_descendant_is_refused_even_after_detaching(tmp_path, sock_dir):
    """A child of the core holding the credential is local through the real client, and stays local when the
    process that connected exits so the kernel's peer pid no longer has a readable parent chain."""
    sock = sock_dir / "exod.sock"
    script, out = tmp_path / "detach.py", tmp_path / "detach.json"
    script.write_text(DETACH)

    async def main():
        srv, task = await _serve(sock)
        try:
            child = await asyncio.create_subprocess_exec(sys.executable, "-c", CHILD_CLIENT, str(sock), env=ENV,
                                                         stdout=asyncio.subprocess.PIPE)
            direct = json.loads((await asyncio.wait_for(child.communicate(), 30))[0])
            proc = await asyncio.create_subprocess_exec(sys.executable, str(script), str(sock), str(out), env=ENV)
            await proc.wait()  # the connecting process is gone and reaped before its orphan writes
            end = time.monotonic() + 15
            while not out.exists():
                assert time.monotonic() < end
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.05)
            return direct, json.loads(out.read_bytes())["result"]["caller"]
        finally:
            await _stop(srv, task)
    assert asyncio.run(main()) == ("local", "local")


# ---------------------------------------------------------------- the real clients still work


def test_tui_and_web_clients_act_as_principal_and_stream(sock_dir, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "plugins" / "exo-command"))
    from exo_command.source import ClientSource
    from exo_tui.conn import connect

    sock = sock_dir / "exod.sock"

    async def main():
        srv, task = await _serve(sock)
        try:
            tui = connect(socket=str(sock))
            web = ClientSource(socket=str(sock))
            got = [(await tui.call(s.SessionList()))["caller"], (await web.call(s.SessionList()))["caller"]]
            hello = await tui.call(s.Hello())
            srv.dispatcher.hub.publish(s.Notice(text="hi"), None)
            ev = await asyncio.wait_for(anext(web.stream(0)), 5)
            evs = tui.events(0)
            first = await asyncio.wait_for(anext(evs), 5)
            await evs.aclose()
            await tui.aclose()
            await web.aclose()
            return got, hello, ev, first, tui.instance
        finally:
            await _stop(srv, task)
    got, hello, ev, first, instance = asyncio.run(main())
    assert got == ["principal:tui", "principal:web"]
    assert hello["instance"] and hello["heartbeat"] == 15.0 and "heartbeat" in hello["capabilities"]
    assert ev["body"]["text"] == "hi" and first.body.text == "hi"
    assert instance == hello["instance"]  # the stream names the launch it belongs to


def test_reconnect_after_core_restart_uses_the_new_record(sock_dir):
    sock = sock_dir / "exod.sock"

    async def main():
        async with Client(socket=str(sock), principal="tui") as c:
            srv, task = await _serve(sock)
            first = ((await c.call(s.SessionList()))["caller"], (await c.call(s.Hello()))["instance"], _token(sock))
            await _stop(srv, task)
            assert not pr.token_path(sock).exists()
            with pytest.raises(httpx.ConnectError):
                await c.call(s.SessionList())
            srv, task = await _serve(sock)
            try:
                second = ((await c.call(s.SessionList()))["caller"], (await c.call(s.Hello()))["instance"],
                          _token(sock))
            finally:
                await _stop(srv, task)
            return first, second
    (c1, i1, t1), (c2, i2, t2) = asyncio.run(main())
    assert c1 == c2 == "principal:tui" and i1 != i2 and t1 != t2


# ---------------------------------------------------------------- snapshot, liveness, no resend


def test_approvals_snapshot_carries_an_atomic_cursor(tmp_path):
    from tests.security.test_approval_identity import _engine, _pending

    eng = _engine(tmp_path, [], ["x"])

    async def main():
        d = Dispatcher(eng)
        req = await _pending(eng, d)
        snap = (await d.run(s.Command.of(s.ApprovalsList()))).result
        hello = (await d.run(s.Command.of(s.Hello()))).result
        await d.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision="deny")), "principal")
        return req, snap, hello
    req, snap, hello = asyncio.run(main())
    assert [a["request_id"] for a in snap["approvals"]] == [req.request_id]
    assert snap["cursor"]["instance"] == hello["instance"] and snap["cursor"]["seq"] >= 1


def test_failed_request_publication_leaves_no_pending_row(tmp_path):
    from tests.security.test_approval_identity import _engine

    eng = _engine(tmp_path, [], ["x"])

    def boom(*a, **k):
        raise RuntimeError("log full")

    async def main():
        eng.hub.publish = boom
        with pytest.raises(RuntimeError):
            await eng.ask("s1", "do it")
    asyncio.run(main())
    assert not eng._approvals and not eng._open_approvals and not eng._approval_rules


def test_filtered_subscription_heartbeat_is_not_starved_by_other_sessions():
    async def main():
        hub = Hub()
        sub = hub.subscribe(0, "quiet")

        async def flood():
            while True:
                hub.publish(s.Notice(text="busy"), "busy")
                await asyncio.sleep(0.01)
        f = asyncio.create_task(flood())
        try:
            t0 = time.monotonic()
            got = await asyncio.wait_for(sub.next(timeout=0.2), 3)
            return got, time.monotonic() - t0
        finally:
            f.cancel()
            sub.close()
    got, took = asyncio.run(main())
    assert got is None and took < 1.5


def test_event_stream_idle_deadline_needs_complete_frames():
    from exo.peerauth import sse_frames

    async def feed(lines, gap):
        for ln in lines:
            await asyncio.sleep(gap)
            yield ln

    async def collect(lines, gap, idle, limit=1 << 20):
        return [f async for f in sse_frames(feed(lines, gap), idle, limit)]

    beats = [": exod instance=i_1 heartbeat=15", "", ": ping", "", "data: {}", ""]
    assert len(asyncio.run(collect(beats, 0.01, 0.5))) == 3
    with pytest.raises(httpx.ReadTimeout):  # bytes keep coming but no frame ever completes
        asyncio.run(collect(["data: x"] * 40, 0.05, 0.5))
    with pytest.raises(httpx.RemoteProtocolError):
        asyncio.run(collect(["data: " + "x" * 600, "data: " + "x" * 600, ""], 0, 5, limit=1000))


class _Flaky:
    def __init__(self, exc):
        self.exc, self.sent = exc, []

    async def send(self, body):
        self.sent.append(type(body).TYPE)
        if len(self.sent) == 1:
            raise self.exc
        return s.Response(id="x", result={})

    async def aclose(self):
        pass


@pytest.mark.parametrize("body,exc,resent", [
    (s.TurnSend(session="s", text="pay the invoice"), httpx.ReadError("lost"), False),
    (s.ApprovalRespond(request_id="a", decision="allow"), httpx.RemoteProtocolError("lost"), False),
    (s.ApprovalsList(), httpx.ReadError("lost"), True),
    (s.TurnSend(session="s", text="hi"), httpx.ConnectError("refused"), True),
])
def test_shared_client_never_resends_a_change_whose_reply_was_lost(body, exc, resent, monkeypatch):
    from exo_tui import conn

    inner = _Flaky(exc)
    monkeypatch.setattr(conn, "_open", lambda home, agent: inner)
    shared = conn.SharedClient(Path("/nonexistent"), "default", inner)

    async def main():
        if resent:
            return await shared.send(body)
        with pytest.raises(type(exc)):
            await shared.send(body)
    asyncio.run(main())
    assert len(inner.sent) == (2 if resent else 1)
