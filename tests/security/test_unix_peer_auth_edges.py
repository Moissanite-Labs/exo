"""D-060 edges: a frame too large to read, unsigned uids, PID namespaces, pid reuse, the launch record's timing, an
unreadable parent chain, and the reasons a refused or demoted connection is given."""
from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from exo import principal as pr
from exo.peerauth import FrameTooLarge
from exo.protocol import schema as s
from exo.protocol.client import Client
from exo.server.app import MAX_BODY, Dispatcher, HttpServer

REPO = Path(__file__).resolve().parents[2]
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO / "src"), str(REPO / "clients" / "tui")])}
FRAME = 16 * 1024 * 1024  # the Python client's frame bound (exo.peerauth.MAX_FRAME)


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- a frame too large to read


def _sse(frames: list[tuple[int, str]]) -> bytes:
    out = b": exod instance=i_1 heartbeat=15\n\n"
    for seq, data in frames:
        out += f"id: {seq}\nevent: notice\ndata: {data}\n\n".encode()
    return out


def _notice(seq: int, text: str) -> str:
    return json.dumps({"id": f"e{seq}", "seq": seq, "session": None, "type": "notice", "ts": "",
                       "body": {"text": text}})


def _mock_client(asked: list[int]) -> Client:
    """A Client on a fake core whose log holds an event too large for the client's frame bound at seq 2."""
    log = [(1, _notice(1, "before")), (2, _notice(2, "x" * (FRAME + 10))), (3, _notice(3, "after"))]

    def handler(request: httpx.Request) -> httpx.Response:
        since = int(request.url.params.get("since", "0"))
        asked.append(since)
        if len(asked) > 5:  # a stalled stream keeps asking at the same seq: fail now instead of hanging
            raise RuntimeError(f"the stream did not move past the large frame: asked since={asked}")
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              content=_sse([f for f in log if f[0] > since]))
    c = Client(url="http://exod")
    c._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))
    return c


def test_reconnecting_stream_moves_past_a_frame_too_large_to_read():
    """Resuming at the same seq replays the same frame forever; the client skips it and later events arrive."""
    async def main(asked):
        c = _mock_client(asked)
        got = []
        try:
            async for ev in c.events(0, reconnect=True, backoff=0.01):
                got.append(ev.seq)
                if ev.seq == 3:
                    return got
        finally:
            await c.aclose()
    asked: list[int] = []
    assert asyncio.run(asyncio.wait_for(main(asked), 15)) == [1, 3]
    assert asked == [0, 2]


def test_a_frame_too_large_that_comes_again_ends_the_stream():
    """A core that sends the same large frame after the client moved past it does not keep the client looping."""
    async def main(asked):
        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(int(request.url.params["since"]))
            if len(asked) > 5:  # the mock never yields to the loop, so wait_for cannot end a spin: fail here
                raise RuntimeError(f"the stream kept asking for the same frame: asked since={asked}")
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  content=_sse([(2, _notice(2, "x" * (FRAME + 10)))]))
        c = Client(url="http://exod")
        c._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))
        try:
            return [ev.seq async for ev in c.events(0)]
        finally:
            await c.aclose()
    asked: list[int] = []
    assert asyncio.run(asyncio.wait_for(main(asked), 15)) == []
    assert asked == [0, 2]


def test_a_frame_too_large_with_no_id_ends_the_stream():
    """With no seq to resume past, reconnecting would replay the frame forever: the stream ends with the error."""
    async def main(asked):
        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(int(request.url.params["since"]))
            if len(asked) > 5:
                raise RuntimeError(f"the stream kept asking for the same frame: asked since={asked}")
            frame = f"event: notice\ndata: {_notice(2, 'x' * (FRAME + 10))}\n\n".encode()
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  content=b": exod instance=i_1 heartbeat=15\n\n" + frame)
        c = Client(url="http://exod")
        c._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(FrameTooLarge):
                async for _ in c.events(0, reconnect=True, backoff=0.01):
                    pass
        finally:
            await c.aclose()
    asked: list[int] = []
    asyncio.run(asyncio.wait_for(main(asked), 15))
    assert asked == [0]


def test_joined_window_stream_moves_past_a_frame_too_large_to_read(monkeypatch):
    from exo_tui import conn

    asked: list[int] = []
    monkeypatch.setattr(conn, "_open", lambda home, agent: _mock_client(asked))

    async def main():
        shared = conn.SharedClient(Path("/nonexistent"), "default", _mock_client(asked))
        got = []
        try:
            async for ev in shared.events(0, reconnect=True, backoff=0.01):
                got.append(ev.seq)
                if ev.seq == 3:
                    return got
        finally:
            await shared.aclose()
    assert asyncio.run(asyncio.wait_for(main(), 15)) == [1, 3]
    assert asked == [0, 2]


@pytest.fixture
def core_home():
    d = Path(tempfile.mkdtemp(prefix="exoe", dir="/tmp" if os.path.isdir("/tmp") else None))
    (d / "config.json").write_text(json.dumps({"version": 1, "cwd": str(d), "models": {"fake": {"rules": []}}}))
    proc = None
    env = {k: v for k, v in os.environ.items() if not k.startswith(("EXO_", "ANTHROPIC_"))}
    env.update(EXO_HOME=str(d), PYTHONUNBUFFERED="1")

    def start() -> Path:
        nonlocal proc
        proc = subprocess.Popen([sys.executable, "-m", "exo.cli", "serve"], env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        sock = d / "run" / "exod.sock"
        end = time.monotonic() + 30
        while not (sock.exists() and pr.token_path(sock).exists()):
            assert proc.poll() is None and time.monotonic() < end, "exod did not start"
            time.sleep(0.05)
        return sock
    try:
        yield d, start
    finally:
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(10)
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.parametrize("char,delivered", [("\U0001F600", True), ("\x85", False)])
def test_raw_utf8_turn_near_max_body_does_not_stall_the_event_streams(core_home, char, delivered):
    """A local caller sends turn.send with raw UTF-8 near MAX_BODY. An emoji message goes out near its own size and
    is delivered; NEL text still escapes to three times its size, so that frame is skipped. Either way the events
    after it reach a reconnecting Client.events and a joined window's SharedClient.events."""
    from exo_tui import conn

    home, start = core_home
    sock = start()

    async def main():
        async with Client(socket=str(sock), timeout=60) as c:
            sid = (await c.call(s.SessionCreate()))["session"]["id"]
            text = char * ((MAX_BODY - 4096) // len(char.encode()))
            raw = json.dumps(s.Command.of(s.TurnSend(session=sid, text=text)).to_dict(), ensure_ascii=False)
            assert len(raw.encode()) <= MAX_BODY
            since, until, last = (await c.call(s.Hello()))["seq"], time.monotonic() + 30, 0
            resp = await c.send_raw(raw)
            assert resp.ok, resp.error
            while not last:  # the turn's end, read over /command (no frame bound there)
                assert time.monotonic() < until
                await asyncio.sleep(0.2)
                evs = (await c.call(s.EventsSubscribe(since=since, session=sid)))["events"]
                last = next((e["seq"] for e in evs if e["type"] == "turn.completed"), 0)

        async def follow(client) -> list[s.Event]:
            got = []
            async for ev in client.events(0, reconnect=True, backoff=0.05):
                got.append(ev)
                if ev.seq >= last:
                    return got
            return got
        plain = Client(socket=str(sock), timeout=60)
        shared = conn.SharedClient(home, "default", Client(socket=str(sock), principal="tui", timeout=60))
        try:
            return text, await asyncio.wait_for(follow(plain), 60), await asyncio.wait_for(follow(shared), 60), last
        finally:
            await plain.aclose()
            await shared.aclose()

    text, plain, shared, last = asyncio.run(main())
    for got in (plain, shared):
        assert got[-1].seq >= last
        users = [e for e in got if e.type == "message.completed" and e.body.message.role == "user"]
        assert bool(users) == delivered
        if delivered:
            assert users[0].body.message.text() == text


def test_lone_surrogate_and_line_separators_stay_one_frame(sock_dir):
    """Raw UTF-8 frames escape what httpx's line reader splits on, and a lone surrogate, so the event round-trips."""
    sock = sock_dir / "exod.sock"
    odd = "a b c\x85d\ud800e\U0001F600"

    async def main():
        srv = HttpServer(Dispatcher(_Probe()), principal=True)
        await srv.start_unix(sock)
        task = asyncio.create_task(srv.serve_forever())
        try:
            srv.dispatcher.hub.publish(s.Notice(text=odd), None)
            async with Client(socket=str(sock)) as c:
                return await asyncio.wait_for(anext(c.events(0)), 5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await srv.close()
    assert asyncio.run(main()).body.text == odd


# ---------------------------------------------------------------- the kernel's answer


class _Sock:
    def __init__(self, raw: bytes):
        self.raw = raw

    def getsockopt(self, *args):
        return self.raw


def test_so_peercred_uid_above_two_to_the_31_is_unsigned(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    if not hasattr(socket, "SO_PEERCRED"):
        monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    assert pr.peer_cred(_Sock(struct.pack("iII", 4242, 3_000_000_000, 3_000_000_000))) == (4242, 3_000_000_000)


def test_pid_zero_names_the_pid_namespace(monkeypatch, sock_dir):
    monkeypatch.setattr(sys, "platform", "linux")
    if not hasattr(socket, "SO_PEERCRED"):
        monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    zero = _Sock(struct.pack("iII", 0, os.geteuid(), os.getegid()))
    with pytest.raises(pr.PeerRefused, match="another PID namespace"):
        pr.verify_daemon(zero, sock_dir / "exod.sock")
    tok = "t" * 43
    assert pr.stamp(tok, tok, pr.Peer(0, os.geteuid(), time.time()), "tui") == "local"


@pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="kernel peer credentials on a socketpair")
def test_pid_that_names_another_process_by_stamp_time_is_local(monkeypatch):
    """The start identity read at accept must still be the pid's when the request is stamped; a process started
    within the clock slack of the accept, under a reused pid, is not the one that connected."""
    a, b = socket.socketpair(socket.AF_UNIX)
    tok, ident = "t" * 43, {"now": "boot:100"}
    start = time.time() - 0.5  # within CLOCK_SLACK of the accept
    monkeypatch.setattr(pr, "proc_info", lambda pid: pr.Proc(1, start, ident["now"]) if pid > 0 else None)
    try:
        peer = pr.accepted(a)
        assert pr.stamp(tok, tok, peer, "tui") == "principal:tui"
        ident["now"] = "boot:101"  # the connecting process exited and its pid was reused
        assert pr.stamp(tok, tok, peer, "tui") == "local"
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------- the parent walk fails closed


def _walk(monkeypatch, parents: dict[int, int | None]):
    now = time.time()
    monkeypatch.setattr(pr, "proc_info", lambda pid: pr.Proc(parents.get(pid) or 1, now - 60, f"id{pid}")
                        if pid in parents else None)
    monkeypatch.setattr(pr, "_ppid", lambda pid: parents.get(pid))
    return pr.Peer(4242, os.geteuid(), now, "id4242")


def test_clean_chain_outside_the_core_is_the_principal(monkeypatch):
    peer = _walk(monkeypatch, {4242: 4000, 4000: 1})
    assert pr.lineage(4242) == [4000, 1]
    assert pr.stamp("t" * 43, "t" * 43, peer, "tui") == "principal:tui"


def test_unreadable_ancestor_is_the_tool_tree(monkeypatch, capsys):
    """A live peer whose middle ancestor cannot be read (hidepid, ProtectProc) is never outside the tree."""
    peer = _walk(monkeypatch, {4242: 4000, 4000: None})
    assert pr.lineage(4242) is None
    assert pr.descends_from(4242, os.getpid())
    assert pr.stamp("t" * 43, "t" * 43, peer, "tui") == "local"
    assert "an ancestor of it cannot be read" in capsys.readouterr().err


def test_descendant_of_the_core_is_local(monkeypatch):
    peer = _walk(monkeypatch, {4242: os.getpid(), os.getpid(): 1})
    assert pr.stamp("t" * 43, "t" * 43, peer, "tui") == "local"


# ---------------------------------------------------------------- reasons


class _Probe:
    hub = None

    async def handle(self, command, hub):
        return {"caller": pr.current_caller()}


class _Strict:
    """An engine whose every command is principal-only."""

    hub = None

    async def handle(self, command, hub):
        return {"caller": pr.require_principal("do this")}


CHILD = r'''
import asyncio, json, sys
from exo.protocol import schema as s
from exo.protocol.client import Client, ClientError

async def main():
    body = s.CommandRun(**json.loads(sys.argv[2])) if len(sys.argv) > 2 else s.SessionList()
    async with Client(socket=sys.argv[1], principal="tui") as c:
        try:
            print(json.dumps((await c.call(body)).get("caller")))
        except ClientError as exc:
            print(json.dumps(exc.message))
asyncio.run(main())
'''


def _from_tool_child(sock: Path, dispatcher: Dispatcher, *args: str) -> object:
    """What a principal client started by this process (so in the core's tool tree, and demoted) is told."""
    async def main():
        srv = HttpServer(dispatcher, principal=True)
        await srv.start_unix(sock)
        task = asyncio.create_task(srv.serve_forever())
        try:
            child = await asyncio.create_subprocess_exec(sys.executable, "-c", CHILD, str(sock), *args, env=ENV,
                                                         stdout=asyncio.subprocess.PIPE)
            return json.loads((await asyncio.wait_for(child.communicate(), 30))[0])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await srv.close()
    return asyncio.run(main())


@pytest.mark.parametrize("hidden,why", [(False, "a tool process"), (True, "an ancestor of it cannot be read")])
def test_demoted_credential_is_logged_and_named_in_the_refusal(sock_dir, monkeypatch, capsys, hidden, why):
    """A principal client the core takes as local learns why, and the core's log says it once."""
    monkeypatch.setattr(pr, "_SAID", set())
    sock = sock_dir / "exod.sock"
    if hidden:  # the core cannot read the child's parent link, as under hidepid
        real = pr._ppid
        monkeypatch.setattr(pr, "_ppid", lambda pid: None if pid != os.getpid() else real(pid))
    msg = _from_tool_child(sock, Dispatcher(_Strict()))
    assert "only the principal can do this" in msg and why in msg
    assert why in capsys.readouterr().err


class _BrokenStream:
    def write(self, text):
        raise OSError(5, "Input/output error")

    def flush(self):
        raise OSError(5, "Input/output error")


@pytest.mark.parametrize("broken", [False, True])
def test_closed_or_broken_stderr_does_not_change_the_refusal(sock_dir, monkeypatch, broken):
    """The core's log line is a diagnostic: a stderr that cannot be written still gives the client its answer."""
    monkeypatch.setattr(pr, "_SAID", set())   # a reason already logged once would skip the print under test
    err = _BrokenStream() if broken else io.StringIO()
    if not broken:
        err.close()  # writing to it raises ValueError
    monkeypatch.setattr(sys, "stderr", err)
    msg = _from_tool_child(sock_dir / "exod.sock", Dispatcher(_Strict()))
    assert "only the principal can do this" in msg and "a tool process" in msg


@pytest.mark.parametrize("name,args,refusal", [
    ("rename", "Billing refactor", "only the principal can rename a session"),
    ("subagent", "send t1 change course", "only the principal or the parent session can steer a subagent")],
    ids=["rename", "subagent"])
def test_principal_only_commands_name_why_the_credential_was_local(sock_dir, tmp_path, monkeypatch, name, args,
                                                                   refusal):
    """Every principal-only refusal a demoted connection gets names the reason, not only ``require_principal``'s."""
    from exo.core.engine import CoreEngine

    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path),
                     env={})
    row = {"task_id": "t1", "parent": "s_parent", "child_session": "s_child", "status": "running", "title": "child"}
    monkeypatch.setattr(eng.agents, "list", lambda session=None: [row])  # a running subagent to refuse
    msg = _from_tool_child(sock_dir / "exod.sock", Dispatcher(eng),
                           json.dumps({"name": name, "args": args, "session": "s_parent"}))
    assert msg.startswith(refusal) and "carried the principal credential but is local: it runs under this core" in msg


def test_core_that_cannot_read_its_own_identity_is_named_and_revoked(sock_dir, monkeypatch, capsys):
    sock = sock_dir / "exod.sock"
    monkeypatch.setattr(pr, "proc_info", lambda pid: None)
    token = pr.issue(sock)
    assert "cannot read this process's own start identity" in capsys.readouterr().err
    with pytest.raises(pr.PeerRefused, match="could not read its own process start identity"):
        pr.read_record(sock)
    pr.revoke(sock, "not-" + token)
    assert pr.token_path(sock).exists()
    pr.revoke(sock, token)
    assert not pr.token_path(sock).exists()


def test_launch_record_is_in_place_before_the_socket_accepts(sock_dir, monkeypatch):
    """A crashed core's record is replaced before a client can connect: never 'not the core in the launch record'."""
    sock = sock_dir / "exod.sock"
    pr.token_path(sock).write_text(json.dumps({"v": 1, "token": "old", "pid": 1, "uid": os.geteuid(),
                                               "start": "x", "instance": ""}))
    pr.token_path(sock).chmod(0o600)
    seen = []
    real = asyncio.start_unix_server

    async def spy(*args, **kwargs):
        server = await real(*args, **kwargs)  # listening now: a client may connect from here on
        try:
            seen.append(pr.read_record(sock)["pid"])
        except pr.PeerRefused as exc:
            seen.append(str(exc))
        return server
    monkeypatch.setattr(asyncio, "start_unix_server", spy)

    async def main():
        srv = HttpServer(Dispatcher(_Probe()), principal=True)
        await srv.start_unix(sock)
        await srv.close()
    asyncio.run(main())
    assert seen == [os.getpid()]


def test_launch_record_is_revoked_when_the_listen_fails(sock_dir, monkeypatch):
    """A core that issued its record but could not listen leaves no record for a client to trust."""
    sock = sock_dir / "exod.sock"
    issued = []

    async def refuse(*args, **kwargs):
        issued.append(pr.token_path(sock).exists())
        raise OSError(98, "Address already in use")
    monkeypatch.setattr(asyncio, "start_unix_server", refuse)

    async def main():
        with pytest.raises(OSError, match="in use"):
            await HttpServer(Dispatcher(_Probe()), principal=True).start_unix(sock)
    asyncio.run(main())
    assert issued == [True] and not pr.token_path(sock).exists()


def test_refused_principal_is_a_connect_error_subclass(sock_dir):
    """Callers that retry on ConnectError keep doing so; callers that explain it can tell it apart."""
    from exo.peerauth import PrincipalRefused

    sock = sock_dir / "exod.sock"

    async def main():
        srv = await asyncio.start_unix_server(lambda r, w: w.close(), path=str(sock))
        try:
            async with Client(socket=str(sock), principal="tui", timeout=3) as c:
                with pytest.raises(PrincipalRefused, match="no readable launch record") as e:
                    await c.send(s.SessionList())
                assert isinstance(e.value, httpx.ConnectError)
        finally:
            srv.close()
    asyncio.run(main())


# ---------------------------------------------------------------- #226 follow-ups


def test_a_line_over_the_frame_bound_is_not_read_in_full():
    """httpx's line reader held a whole line before the frame bound saw it, so a 40 MiB line was read in full. The
    client now stops reading a line once it passes the bound, and skips that frame by its seq as before (#226)."""
    pulled = [0]

    async def big():
        yield b": exod instance=i_1 heartbeat=15\n\nid: 2\nevent: notice\ndata: "
        chunk = b"x" * 65536
        while pulled[0] < 3 * FRAME:
            pulled[0] += len(chunk)
            yield chunk
        yield b"\n\n"

    async def main():
        def handler(request: httpx.Request) -> httpx.Response:
            since = int(request.url.params["since"])
            body = big() if since == 0 else b": exod instance=i_1 heartbeat=15\n\n"
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)
        c = Client(url="http://exod")
        c._http = httpx.AsyncClient(base_url="http://exod", transport=httpx.MockTransport(handler))
        try:
            got = [ev.seq async for ev in c.events(0)]
            return got, c.last_seq
        finally:
            await c.aclose()
    assert asyncio.run(asyncio.wait_for(main(), 60)) == ([], 2)
    assert pulled[0] <= FRAME + 65536


def test_event_stream_lines_split_where_sse_splits():
    """CRLF, LF and CR end a line, a CRLF split across chunks is one end, and the other str.splitlines breaks (which
    httpx's reader also split on) stay in the line; a line past the bound is refused before its end (#226)."""
    from exo.peerauth import LineTooLong, bounded_lines

    async def lines(chunks, limit=64):
        async def gen():
            for c in chunks:
                yield c
        return [ln async for ln in bounded_lines(gen(), limit)]
    got = asyncio.run(lines(["a\r", "\nb\rc\n", "", "d e\x85f\x0bg\r\n", "\n", "tail"]))
    assert got == ["a", "b", "c", "d e\x85f\x0bg", "", "tail"]
    with pytest.raises(LineTooLong):
        asyncio.run(lines(["x" * 40, "y" * 40, "\n"]))
    assert asyncio.run(lines(["x" * 40 + "\n" + "y" * 40 + "\n"])) == ["x" * 40, "y" * 40]


def test_principal_keeps_no_unused_peer_helpers():
    """``peer_pid`` (#175's walker changes) and ``read`` (#248) lost their callers; the kernel is asked only through
    ``peer_cred``, ``accepted`` and ``verify_daemon`` (#226)."""
    assert not hasattr(pr, "peer_pid") and not hasattr(pr, "read")


def _fake_linux_proc(monkeypatch, parents: dict[int, int]) -> dict[str, int]:
    """A Linux /proc in memory: one stat line per pid (starttime 1000 + pid), the boot id and btime. Returns the
    number of reads per path."""
    files = {f"/proc/{pid}/stat": f"{pid} (a b) S {ppid} " + " ".join(["0"] * 17) + f" {1000 + pid} 0 0\n"
             for pid, ppid in parents.items()}
    files["/proc/sys/kernel/random/boot_id"] = "b00t\n"
    files["/proc/stat"] = "cpu 1 2 3\nbtime 1700000000\nprocesses 9\n"
    reads: dict[str, int] = {}
    real = Path.read_text

    def read_text(self, *args, **kwargs):
        if (key := str(self)) in files:
            reads[key] = reads.get(key, 0) + 1
            return files[key]
        if key.startswith("/proc/"):
            raise FileNotFoundError(key)
        return real(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pr, "_BOOT_ID", "", raising=False)
    return reads


def test_parent_walk_reads_one_stat_line_per_ancestor(monkeypatch):
    """The walk read the whole start identity per ancestor (the boot id and all of /proc/stat) on the event loop,
    per connection. Now it reads one stat line per ancestor; the boot id is read once per process, and btime only
    where a start is wanted (#226)."""
    reads = _fake_linux_proc(monkeypatch, {4242: 4000, 4000: 3000, 3000: 1})
    assert pr.lineage(4242) == [4000, 3000, 1]
    assert reads == {"/proc/4242/stat": 1, "/proc/4000/stat": 1, "/proc/3000/stat": 1}
    first, again = pr.proc_info(4242), pr.proc_info(4242)
    assert first == again and first.ppid == 4000 and first.ident == "b00t:5242"
    assert first.start == 1_700_000_000 + 5242 / os.sysconf("SC_CLK_TCK")
    assert reads["/proc/sys/kernel/random/boot_id"] == 1 and reads["/proc/stat"] == 2


@pytest.mark.parametrize("platform, hint", [("linux", "/proc hidden by hidepid"), ("darwin", "libproc"),
                                            ("freebsd14", "only on Linux and macOS, and this is freebsd14")])
def test_core_without_its_start_identity_names_the_platform_cause(sock_dir, monkeypatch, capsys, platform, hint):
    """Off Linux, the core and the client blamed a hidden /proc; each now names its platform's cause (#226)."""
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(pr, "proc_info", lambda pid: None)
    pr.issue(sock_dir / "exod.sock")
    err = capsys.readouterr().err
    assert "cannot read this process's own start identity" in err and hint in err
    with pytest.raises(pr.PeerRefused, match="could not read its own process start identity") as e:
        pr.read_record(sock_dir / "exod.sock")
    assert hint in str(e.value)
    if platform != "linux":
        assert "/proc" not in err and "/proc" not in str(e.value)


# ---------------------------------------------------------------- the macOS branches (#226 item 7)


class _DarwinSock:
    """``getsockopt`` as macOS answers it: LOCAL_PEERCRED gives a struct xucred, LOCAL_PEERPID an int."""

    def __init__(self, version: int, uid: int, pid: int, size: int = 76):
        self.raw = struct.pack("IIh2x16I", version, uid, 1, *range(16))[:size]
        self.pid = pid

    def getsockopt(self, level, opt, size=None):
        assert level == 0 and opt in (0x001, 0x002)
        return self.raw if opt == 0x001 else self.pid


@pytest.mark.parametrize("sock, want", [
    (_DarwinSock(0, 501, 4242), (4242, 501)),
    (_DarwinSock(0, 3_000_000_000, 4242), (4242, 3_000_000_000)),  # uid_t is unsigned
    (_DarwinSock(1, 501, 4242), None),  # an xucred version this code does not know
    (_DarwinSock(0, 501, 0), None),  # no pid named
    (_DarwinSock(0, 501, 4242, size=6), None),  # a short answer
])
def test_darwin_peer_credentials_parse(monkeypatch, sock, want):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert pr.peer_cred(sock) == want


class _LibProc:
    """``proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, buf, 136)`` writing a struct proc_bsdinfo: pbi_pid and pbi_ppid at
    offset 12, pbi_start_tvsec and pbi_start_tvusec at offset 120."""

    def __init__(self, pid: int, ppid: int, sec: int, usec: int, size: int = 136):
        self.raw = bytearray(136)
        struct.pack_into("2I", self.raw, 12, pid, ppid)
        struct.pack_into("2Q", self.raw, 120, sec, usec)
        self.size = size

    def proc_pidinfo(self, pid, flavor, arg, buf, size):
        import ctypes

        assert flavor.value == 3 and size.value == 136
        ctypes.memmove(buf, bytes(self.raw), 136)
        return self.size


@pytest.mark.parametrize("lib, want", [
    (_LibProc(4242, 4000, 1_700_000_000, 5), pr.Proc(4000, 1_700_000_000.000005, "1700000000.000005")),
    (_LibProc(4243, 4000, 1_700_000_000, 5), None),  # the answer is for another pid
    (_LibProc(4242, 4000, 1_700_000_000, 5, size=0), None),  # gone, or not ours to read
])
def test_darwin_process_info_parse(monkeypatch, lib, want):
    monkeypatch.setattr(pr, "_LIBPROC", lib)
    assert pr._proc_darwin(4242) == want


@pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="the kernel's own answers")
def test_kernel_answers_for_this_process_and_a_child():
    """No mocks: the real peer call on a socket pair, and the real process lookup and parent walk for this process
    and a child it starts. On macOS this runs ``LOCAL_PEERCRED``, ``LOCAL_PEERPID`` and libproc (#226 item 7)."""
    a, b = socket.socketpair(socket.AF_UNIX)
    try:
        assert pr.peer_cred(a) == (os.getpid(), os.geteuid())
        assert pr.accepted(a).ident == pr.proc_info(os.getpid()).ident
    finally:
        a.close()
        b.close()
    me = pr.proc_info(os.getpid())
    assert me is not None and me.ppid == os.getppid()
    before = time.time()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    after = time.time()
    try:
        info = pr.proc_info(child.pid)
        assert info is not None and info.ppid == os.getpid() and info.ident != me.ident
        assert pr.proc_info(child.pid).ident == info.ident
        assert before - 1.5 <= info.start <= after + 0.5  # Linux btime is whole seconds
        assert pr.lineage(child.pid)[:2] == [os.getpid(), os.getppid()]
        assert pr.descends_from(child.pid, os.getpid()) and not pr.descends_from(os.getpid(), child.pid)
    finally:
        child.kill()
        child.wait()
