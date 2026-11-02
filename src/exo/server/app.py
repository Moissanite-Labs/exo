"""exod: one dispatcher, three transports.

- HTTP + SSE on a Unix socket (default; socket file is mode 0600, token optional).
- HTTP + SSE on TCP (bearer token required; the server refuses to start without one).
- stdio JSON lines, for a client that launches the core as a child process.

HTTP routes:
  POST /command   body is a Command envelope; reply is a Response.
  GET  /events    SSE stream, ``?since=<seq>&session=<id>``; ``Last-Event-ID`` also resumes.
  GET  /health    ``{"ok": true, "protocol": 1, "seq": <last>}``.

stdio: each input line is a Command; output lines are Responses (have ``ok``)
and Events (have ``seq``). ``events.subscribe`` starts the event feed.

Every command runs with a caller the transport stamps (D-039, ``exo.principal``): ``principal`` only for an
in-process client or a Unix socket request that carries the per-launch credential in ``X-Exo-Principal`` from a
process that is not a descendant of the core; ``channel`` for the channels exod runs (``ChannelLink``, in process);
``local`` for everything else, including stdio and TCP."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import socket as _socket
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from exo.core.private import umask
from exo.core.types import to_dict
from exo.protocol import schema as s
from exo import principal as pr
from exo.server.engine import Closed, CommandFailed, Engine, Hub, Subscription

SERVER_CAPABILITIES = ["sse", "replay", "stdio"]  # hello adds "heartbeat" only on a transport that pings
HEARTBEAT = 15.0
MAX_BODY = 8 * 1024 * 1024


def _fail(cid: str, code: str, message: str, retryable: bool = False) -> s.Response:
    return s.Response(id=cid, ok=False, error=s.ErrorInfo(code=code, message=message, retryable=retryable))


def _raw_id(raw: str | bytes) -> str:
    try:
        data = json.loads(raw)
        return data["id"] if isinstance(data, dict) and isinstance(data.get("id"), str) else ""
    except (ValueError, RecursionError):
        return ""


def _count(raw: str) -> int | None:
    """A number in a request: ASCII digits up to SQLite's largest integer, the event log's bound. Else None (400)."""
    n = raw.lstrip("0") or "0"  # leading zeros are digits too: measure the rest before int() and its digit limit
    return int(n) if raw.isascii() and raw.isdigit() and len(n) < 20 and int(n) < 2**63 else None


class Dispatcher:
    """Decodes commands, answers ``hello`` and ``events.subscribe`` itself, and
    sends everything else to the engine. Never raises on bad input."""

    def __init__(self, engine: Engine, hub: Hub | None = None, caller: str = pr.LOCAL):
        self.engine = engine
        # An engine with its own durable hub (the real core) keeps it; tests get a memory hub.
        self.hub = hub or getattr(engine, "hub", None) or Hub()
        self.caller = caller  # D-039: who an in-process user of this dispatcher is; transports pass their own

    async def dispatch(self, raw: str | bytes, caller: str | None = None,
                       heartbeat: float | None = None) -> tuple[s.Command | None, s.Response]:
        cmd = self.decode(raw)
        if isinstance(cmd, s.Response):
            return None, cmd
        return cmd, await self.run(cmd, caller, heartbeat)

    def decode(self, raw: str | bytes) -> s.Command | s.Response:
        """The command in ``raw``, or the typed failure to send back for it."""
        try:
            return s.Command.from_json(raw)
        except s.ProtocolError as exc:
            return _fail(_raw_id(raw), exc.code, exc.message)
        except Exception as exc:  # a decoder bug must not kill the connection or the stdio transport
            return _fail(_raw_id(raw), "internal", f"{type(exc).__name__}: {exc}")

    async def run(self, cmd: s.Command, caller: str | None = None, heartbeat: float | None = None) -> s.Response:
        """``heartbeat``: seconds between the transport's ``: ping`` comments, for ``hello``; None where it sends
        none (stdio, in process)."""
        who = pr.Caller(caller or self.caller)
        tok = pr.CALLER.set(who)
        try:
            return await self._run(cmd, heartbeat)
        finally:
            who.live = False  # a task the command started does not keep the caller's standing
            pr.CALLER.reset(tok)

    async def _run(self, cmd: s.Command, heartbeat: float | None = None) -> s.Response:
        body = cmd.body
        try:
            if isinstance(body, s.Hello):
                if body.protocol != s.PROTOCOL_VERSION:
                    return _fail(cmd.id, "protocol_mismatch", f"server speaks {s.PROTOCOL_VERSION}, client {body.protocol}")
                # D-060: only a transport that pings names a heartbeat, with its own interval
                hello: dict[str, Any] = {"protocol": s.PROTOCOL_VERSION, "server": "exod",
                                         "capabilities": SERVER_CAPABILITIES + ["heartbeat"] * bool(heartbeat),
                                         "seq": self.hub.head(), "instance": self.hub.instance}
                if heartbeat:
                    hello["heartbeat"] = heartbeat
                return s.Response(id=cmd.id, result=hello)
            if isinstance(body, s.EventsSubscribe):
                # Without a stream, subscribe is a one-shot replay; HTTP clients stream from GET /events.
                events = self.hub.since(body.since, body.session)
                return s.Response(id=cmd.id, result={"events": [e.to_dict() for e in events], "seq": self.hub.head()})
            result = await self.engine.handle(cmd, self.hub)
        except CommandFailed as exc:
            return _fail(cmd.id, exc.code, exc.message, exc.retryable)
        except Exception as exc:  # an engine or replay bug (since past SQLite's 64 bits) must not kill the connection
            return _fail(cmd.id, "internal", f"{type(exc).__name__}: {exc}")
        return s.Response(id=cmd.id, result=to_dict(result or {}))


# ---------------------------------------------------------------- HTTP + SSE


class SocketBusy(ValueError):
    """Another core owns, or is starting on, the socket. The only start failure `exo serve` reports as a plain line."""


def _lock_path(sock: Path) -> Path:
    return sock.with_name(sock.name + ".lock")


def _take_start_lock(sock: Path) -> int:
    """Exclusive, non-blocking ``flock`` on ``<sock>.lock`` (owner-only), with this pid written in it. A dead
    holder's lock is gone with its fd, so a crash never blocks a start. The file is unlinked by its holder at
    close, so a lock taken on an unlinked file is not the lock: re-open until the file we hold is the one named."""
    import fcntl  # POSIX only: imported here so the module still loads where the lock is never taken

    lock = _lock_path(sock)
    while True:  # the folder is start_unix's; no private_dir here, which refuses a symlinked folder (macOS /tmp)
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_mode & 0o077:
                os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                who = os.pread(fd, 32, 0).decode("ascii", "replace").strip()
            finally:
                os.close(fd)
            raise SocketBusy(f"another exo core is starting or running on {sock}"
                             f"{f' (pid {who})' if who.isdigit() else ''}; stop it, or give this one its own "
                             "--socket") from None
        except BaseException:
            os.close(fd)
            raise
        try:
            if os.fstat(fd).st_ino == os.stat(lock).st_ino:
                os.ftruncate(fd, 0)
                os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
                return fd
        except FileNotFoundError:
            pass
        os.close(fd)  # the holder unlinked it between our open and our lock: take the new file

_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
            405: "Method Not Allowed", 413: "Payload Too Large"}


class HttpServer:
    def __init__(self, dispatcher: Dispatcher, token: str | None = None, heartbeat: float = HEARTBEAT,
                 principal: bool = False):
        self.dispatcher = dispatcher
        self.token = token
        self.heartbeat = heartbeat
        self.principal = principal  # D-039: issue a principal credential beside the Unix socket
        self._principal_token: str | None = None
        self._unix_path: Path | None = None
        self._lock_fd: int | None = None  # the start lock, held from start_unix until close
        self._server: asyncio.base_events.Server | None = None
        self._streams: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._closing = False

    async def start_unix(self, path: str | os.PathLike, claimed: Callable[[], None] | None = None) -> None:
        """Bind the Unix socket as its one owner: probe, unlink and bind run under an exclusive ``flock`` on
        ``<path>.lock``, which this server keeps until ``close``. ``claimed`` runs once the lock is held and the
        socket is not another server's, before the socket is replaced (the pid file is written there)."""
        if self._lock_fd is not None: raise SocketBusy(f"this server already holds {self._unix_path}")  # noqa: E701
        path = Path(path)
        if (n := len(os.fsencode(str(path)))) >= 104:  # macOS limit is 104 bytes, Linux 108; stay under both
            raise ValueError(f"socket path too long ({n} bytes, max 103): {path}. Use a shorter EXO_HOME.")
        from exo.ensure_core import probe  # never take a live server's socket; only a stale file is replaced
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        sock = _socket.socket(_socket.AF_UNIX)
        try:
            self._lock_fd, self._unix_path = _take_start_lock(path), path
            if probe(path) == "alive":
                raise SocketBusy(f"another server is listening on {path}; stop it, or give this one its own --socket")
            if claimed is not None:
                claimed()
            path.unlink(missing_ok=True)
            if self.principal:  # before the socket accepts: no client may read a missing or a crashed core's record
                self._principal_token = pr.issue(path, self.dispatcher.hub.instance)
            with umask(0o177):  # bind only: an await in here let two starts on one loop leave the mask at 0177 (#563)
                sock.bind(str(path))
            os.chmod(path, 0o600)
            self._server = await asyncio.start_unix_server(self._accept_client, sock=sock)
        except BaseException:
            asyncio.get_running_loop().remove_reader(sock)  # a cancel in the start's last pass finds it on the loop
            sock.close()
            if self._principal_token:
                pr.revoke(path, self._principal_token)
                self._principal_token = None
            self._release_start_lock(unlink_socket=False)
            raise

    def _release_start_lock(self, unlink_socket: bool) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is None or self._unix_path is None:
            return
        try:
            if unlink_socket:  # while still locked, so a core that starts next is never unlinked by this one
                self._unix_path.unlink(missing_ok=True)
            _lock_path(self._unix_path).unlink(missing_ok=True)  # a waiter re-checks the inode it locked
        finally:
            os.close(fd)  # closing the fd drops the flock, as does the process dying

    async def start_tcp(self, host: str, port: int) -> int:
        if not self.token:
            raise ValueError("TCP needs a bearer token")
        self._server = await asyncio.start_server(self._accept_client, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def serve_forever(self) -> None:
        assert self._server is not None
        await self._server.start_serving()
        try:  # not Server.serve_forever: on cancel it waits for open SSE streams to end
            await asyncio.get_running_loop().create_future()
        finally:
            await self.close()

    async def close(self) -> None:
        self._closing = True
        if self._principal_token and self._unix_path is not None:
            pr.revoke(self._unix_path, self._principal_token)
            self._principal_token = None
        if self._server is not None:
            self._server.close()
        # Python 3.13 Server.wait_closed also waits for accepted transports. A handler task may not
        # have started yet, and a graceful writer.close may wait for a peer to drain buffered output.
        # Abort owned transports and drain the synchronously registered tasks before waiting.
        writers, tasks = list(self._writers), list(self._streams)
        for writer in writers:
            writer.transport.abort()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._writers.difference_update(writers)
        if self._server is not None:
            await self._server.wait_closed()
        self._release_start_lock(unlink_socket=True)

    def _accept_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Registration must happen in the connection callback, before scheduling the async handler.
        # A callback already queued when close stops listening still needs its transport closed.
        if self._closing:
            writer.transport.abort()
            return
        self._writers.add(writer)
        task = asyncio.create_task(self._client(reader, writer, pr.accepted(writer.get_extra_info("socket"))))
        self._streams.add(task)
        task.add_done_callback(self._streams.discard)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: pr.Peer) -> None:
        try:
            await self._request(reader, writer, peer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                self._writers.discard(writer)

    async def _request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: pr.Peer) -> None:
        line = (await reader.readuntil(b"\r\n")).decode("latin-1").strip()
        method, target, _version = line.split(" ", 2)
        headers: dict[str, str] = {}
        while h := (await reader.readuntil(b"\r\n")).decode("latin-1").strip():
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        url = urlsplit(target)
        if not self._authorized(headers):
            return await self._json(writer, 401, {"error": "bearer token required"})
        if url.path == "/command":
            if method != "POST":
                return await self._json(writer, 405, {"error": "POST only"})
            if (n := _count(headers.get("content-length", "0"))) is None:  # before the size check, which -1 passes
                return await self._json(writer, 400, {"error": "bad Content-Length"})
            if n > MAX_BODY:
                return await self._json(writer, 413, {"error": "body too large"})
            raw = await reader.readexactly(n)
            _cmd, resp = await self.dispatcher.dispatch(raw, self._caller(headers, peer), self.heartbeat)
            return await self._send(writer, 200, resp.to_json().encode())
        if url.path == "/events" and method == "GET":
            q = parse_qs(url.query)
            since, last = _count(q.get("since", ["0"])[0]), _count(headers.get("last-event-id") or "0")
            if since is None or last is None:
                return await self._json(writer, 400, {"error": "bad since or Last-Event-ID"})
            return await self._sse(writer, max(since, last), q.get("session", [None])[0])
        if url.path == "/health":
            return await self._json(writer, 200, {"ok": True, "protocol": s.PROTOCOL_VERSION,
                                                  "seq": self.dispatcher.hub.head()})
        return await self._json(writer, 404, {"error": "not found"})

    def _caller(self, headers: dict[str, str], peer: pr.Peer) -> str:
        """D-039, D-060: principal only on the Unix socket, with the credential, from this user's process outside
        the core's tree, as the kernel named it when the connection was accepted (one request per connection)."""
        if self._unix_path is None or not self._principal_token:
            return pr.LOCAL
        return pr.stamp(headers.get(pr.HEADER.lower()), self._principal_token, peer, headers.get("x-exo-client", ""))

    def _authorized(self, headers: dict[str, str]) -> bool:
        if not self.token:
            return True
        got = headers.get("authorization", "")
        return got.startswith("Bearer ") and hmac.compare_digest(got[7:].encode(), self.token.encode())

    async def _send(self, writer: asyncio.StreamWriter, status: int, body: bytes,
                    ctype: str = "application/json") -> None:
        head = (f"HTTP/1.1 {status} {_REASONS.get(status, '')}\r\nContent-Type: {ctype}\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
        writer.write(head.encode() + body)
        await writer.drain()

    async def _json(self, writer: asyncio.StreamWriter, status: int, data: dict[str, Any]) -> None:
        await self._send(writer, status, json.dumps(data).encode())

    async def _sse(self, writer: asyncio.StreamWriter, since: int, session: str | None) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Connection: close\r\n\r\n" + f": exod instance={self.dispatcher.hub.instance} "
                     f"heartbeat={self.heartbeat:g}\n\n".encode())
        await writer.drain()
        sub = self.dispatcher.hub.subscribe(since, session)
        try:
            while True:
                try:
                    ev = await sub.next(timeout=self.heartbeat)
                except Closed:
                    return
                if ev is None:
                    writer.write(b": ping\n\n")
                else:  # raw UTF-8 keeps a frame near the body that made it (D-060); escapes stay valid JSON
                    data = json.dumps(ev.to_dict(), separators=(",", ":"), ensure_ascii=False)
                    for ch in "\x85\u2028\u2029":  # line breaks to str.splitlines, so to httpx's line reader
                        data = data.replace(ch, f"\\u{ord(ch):04x}")
                    writer.write(f"id: {ev.seq}\nevent: {ev.type}\ndata: {data}\n\n".encode("utf-8", "backslashreplace"))
                await writer.drain()
        finally:
            sub.close()


# ---------------------------------------------------------------- stdio


async def serve_lines(dispatcher: Dispatcher, lines: Callable[[], Awaitable[str | None]],
                      write: Callable[[str], None]) -> None:
    """Run the JSON-lines transport until ``lines`` returns None (EOF)."""
    feed: asyncio.Task | None = None
    cursor: dict[str, Any] = {}  # last seq written and session filter of the active feed

    async def pump(sub: Subscription) -> None:
        cursor.update(seq=sub.last, session=sub.session)
        try:
            while not dispatcher.hub.closed:  # a stream ended for lag (D-079) resumes after the last event written
                async for ev in sub:
                    write(ev.to_json())
                    cursor["seq"] = ev.seq
                if not dispatcher.hub.closed:
                    sub = dispatcher.hub.subscribe(cursor["seq"], sub.session)
        finally:
            sub.close()

    try:
        while (line := await lines()) is not None:
            if not line.strip():
                continue
            cmd = dispatcher.decode(line)
            if isinstance(cmd, s.Command) and isinstance(cmd.body, s.EventsSubscribe):
                # The feed reads its replay a page at a time. The one-shot reply would decode it whole first (#254).
                try:
                    sub = dispatcher.hub.subscribe(cmd.body.since, cmd.body.session)
                except Exception as exc:  # a since the log cannot read (past SQLite's 64 bits) gets a typed reply
                    write(_fail(cmd.id, "internal", f"{type(exc).__name__}: {exc}").to_json())
                    continue
                if feed:
                    feed.cancel()
                write(s.Response(id=cmd.id, result={"subscribed": True, "seq": dispatcher.hub.head()}).to_json())
                feed = asyncio.create_task(pump(sub))
                await asyncio.sleep(0)  # start the replay before the next command
                continue
            write((cmd if isinstance(cmd, s.Response) else await dispatcher.run(cmd, pr.LOCAL)).to_json())
    finally:
        if feed:
            feed.cancel()
            # Flush events already published but not yet written, so a piped client sees them.
            for ev in dispatcher.hub.since(cursor.get("seq", 0), cursor.get("session")):
                write(ev.to_json())


async def serve_stdio(dispatcher: Dispatcher) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def reader() -> None:  # a thread works for pipes, files and ttys alike
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, reader)

    def write(text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    await serve_lines(dispatcher, queue.get, write)


# ---------------------------------------------------------------- entry


DEFAULT_ENGINE = "exo.compose:create"


def load_engine(spec: str = DEFAULT_ENGINE) -> Engine:
    """``echo`` or ``package.module:factory`` (a zero-argument callable returning an Engine)."""
    if spec == "echo":
        from exo.server.echo import EchoEngine

        return EchoEngine()
    import importlib

    mod, _, attr = spec.partition(":")
    if not attr:
        raise ValueError(f"engine spec {spec!r} must be echo or module:factory")
    engine = getattr(importlib.import_module(mod), attr)()
    if not isinstance(engine, Engine):
        raise TypeError(f"{spec} did not return an Engine")
    return engine


class ChannelLink:
    """The link exod gives the channels it runs: the same commands in process, as the caller ``channel``, which no
    transport stamps. Only this caller may hand the core a bus message, which pins a seat's key (D-040)."""

    def __init__(self, dispatcher: Dispatcher):
        self.d = dispatcher

    async def call(self, body: Any) -> dict[str, Any]:
        resp = await self.d.run(s.Command.of(body), pr.CHANNEL)
        if not resp.ok:
            from exo.protocol.client import ClientError
            raise ClientError(resp.error)
        return resp.result

    async def events(self, since: int = 0, session: str | None = None) -> Any:
        sub = self.d.hub.subscribe(since, session)
        try:
            async for ev in sub:
                yield ev
        finally:
            sub.close()


async def serve(engine: Engine, socket: str | os.PathLike | None = None, tcp: tuple[str, int] | None = None,
                token: str | None = None, stdio: bool = False, hub: Hub | None = None,
                ready: Callable[[str], None] | None = None, claimed: Callable[[], None] | None = None) -> None:
    """Run exod until cancelled (or until stdin closes in stdio mode)."""
    dispatcher = Dispatcher(engine, hub)
    if stdio:
        try:
            return await serve_stdio(dispatcher)
        finally:
            if (close := getattr(engine, "close", None)) is not None:
                await close()
    servers: list[HttpServer] = []
    if socket:
        # The socket file is 0600, so only this user reaches it; the token guards TCP only.
        srv = HttpServer(dispatcher, principal=True)
        await srv.start_unix(socket, claimed)
        servers.append(srv)
        if (peers := getattr(engine, "peers", None)) is not None:
            await peers.start(socket)  # D-026: presence file so other daemons find this one
        if ready:
            ready(f"unix:{socket}")
    if tcp:
        srv = HttpServer(dispatcher, token)
        port = await srv.start_tcp(*tcp)
        servers.append(srv)
        if ready:
            ready(f"http://{tcp[0]}:{port}")
    if not servers:
        raise ValueError("nothing to serve: give a socket, tcp or stdio")
    try:
        if getattr(engine, "_channel_cfg", None):
            for name, err in (await engine.start_channels(ChannelLink(dispatcher))).items():
                if err:
                    print(f"exod: channel {name}: {err}", file=sys.stderr)
        await asyncio.gather(*(srv.serve_forever() for srv in servers))
    finally:
        dispatcher.hub.close()
        if (close := getattr(engine, "close", None)) is not None:
            await close()
        for srv in servers:
            await srv.close()
