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
process that is not a descendant of the core; ``local`` for everything else, including stdio and TCP.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from exo.core.types import to_dict
from exo.protocol import schema as s
from exo import principal as pr
from exo.server.engine import Closed, CommandFailed, Engine, Hub

SERVER_CAPABILITIES = ["sse", "replay", "stdio"]
HEARTBEAT = 15.0
MAX_BODY = 8 * 1024 * 1024


def _fail(cid: str, code: str, message: str, retryable: bool = False) -> s.Response:
    return s.Response(id=cid, ok=False, error=s.ErrorInfo(code=code, message=message, retryable=retryable))


def _raw_id(raw: str | bytes) -> str:
    try:
        data = json.loads(raw)
        return data["id"] if isinstance(data, dict) and isinstance(data.get("id"), str) else ""
    except ValueError:
        return ""


class Dispatcher:
    """Decodes commands, answers ``hello`` and ``events.subscribe`` itself, and
    sends everything else to the engine. Never raises on bad input."""

    def __init__(self, engine: Engine, hub: Hub | None = None, caller: str = pr.LOCAL):
        self.engine = engine
        # An engine with its own durable hub (the real core) keeps it; tests get a memory hub.
        self.hub = hub or getattr(engine, "hub", None) or Hub()
        self.caller = caller  # D-039: who an in-process user of this dispatcher is; transports pass their own

    async def dispatch(self, raw: str | bytes, caller: str | None = None) -> tuple[s.Command | None, s.Response]:
        try:
            cmd = s.Command.from_json(raw)
        except s.ProtocolError as exc:
            return None, _fail(_raw_id(raw), exc.code, exc.message)
        return cmd, await self.run(cmd, caller)

    async def run(self, cmd: s.Command, caller: str | None = None) -> s.Response:
        who = pr.Caller(caller or self.caller)
        tok = pr.CALLER.set(who)
        try:
            return await self._run(cmd)
        finally:
            who.live = False  # a task the command started does not keep the caller's standing
            pr.CALLER.reset(tok)

    async def _run(self, cmd: s.Command) -> s.Response:
        body = cmd.body
        if isinstance(body, s.Hello):
            if body.protocol != s.PROTOCOL_VERSION:
                return _fail(cmd.id, "protocol_mismatch", f"server speaks {s.PROTOCOL_VERSION}, client {body.protocol}")
            result = {"protocol": s.PROTOCOL_VERSION, "server": "exod",
                      "capabilities": SERVER_CAPABILITIES, "seq": self.hub.seq}
            return s.Response(id=cmd.id, result=result)
        if isinstance(body, s.EventsSubscribe):
            # Without a stream, subscribe is a one-shot replay; HTTP clients stream from GET /events.
            events = self.hub.since(body.since, body.session)
            return s.Response(id=cmd.id, result={"events": [e.to_dict() for e in events], "seq": self.hub.seq})
        try:
            result = await self.engine.handle(cmd, self.hub)
        except CommandFailed as exc:
            return _fail(cmd.id, exc.code, exc.message, exc.retryable)
        except Exception as exc:  # an engine bug must not kill the connection
            return _fail(cmd.id, "internal", f"{type(exc).__name__}: {exc}")
        return s.Response(id=cmd.id, result=to_dict(result or {}))


# ---------------------------------------------------------------- HTTP + SSE

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
        self._server: asyncio.base_events.Server | None = None
        self._streams: set[asyncio.Task] = set()

    async def start_unix(self, path: str | os.PathLike) -> None:
        path = Path(path)
        if len(os.fsencode(str(path))) >= 104:  # macOS limit is 104 bytes, Linux 108; stay under both
            raise ValueError(f"socket path too long ({len(os.fsencode(str(path)))} bytes, max 103): {path}. "
                             "Use a shorter EXO_HOME.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            path.unlink()
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._client, path=str(path))
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        self._unix_path = path
        if self.principal:
            self._principal_token = pr.issue(path)

    async def start_tcp(self, host: str, port: int) -> int:
        if not self.token:
            raise ValueError("TCP needs a bearer token")
        self._server = await asyncio.start_server(self._client, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def serve_forever(self) -> None:
        assert self._server is not None
        await self._server.start_serving()
        try:  # not Server.serve_forever: on cancel it waits for open SSE streams to end
            await asyncio.get_running_loop().create_future()
        finally:
            await self.close()

    async def close(self) -> None:
        if self._principal_token and self._unix_path is not None:
            pr.revoke(self._unix_path, self._principal_token)
            self._principal_token = None
        if self._server is not None:
            self._server.close()
        for t in list(self._streams):
            t.cancel()
        if self._server is not None:
            await self._server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._streams.add(task)
        try:
            await self._request(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        except asyncio.CancelledError:
            pass
        finally:
            if task:
                self._streams.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readuntil(b"\r\n")).decode("latin-1").strip()
        method, target, _version = line.split(" ", 2)
        headers: dict[str, str] = {}
        while True:
            h = (await reader.readuntil(b"\r\n")).decode("latin-1").strip()
            if not h:
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        url = urlsplit(target)
        if not self._authorized(headers):
            return await self._json(writer, 401, {"error": "bearer token required"})
        if url.path == "/command":
            if method != "POST":
                return await self._json(writer, 405, {"error": "POST only"})
            n = int(headers.get("content-length", "0"))
            if n > MAX_BODY:
                return await self._json(writer, 413, {"error": "body too large"})
            raw = await reader.readexactly(n)
            _cmd, resp = await self.dispatcher.dispatch(raw, self._caller(headers, writer))
            return await self._send(writer, 200, resp.to_json().encode())
        if url.path == "/events" and method == "GET":
            q = parse_qs(url.query)
            since = int(q.get("since", ["0"])[0])
            since = max(since, int(headers.get("last-event-id") or 0))
            return await self._sse(writer, since, q.get("session", [None])[0])
        if url.path == "/health":
            return await self._json(writer, 200, {"ok": True, "protocol": s.PROTOCOL_VERSION,
                                                  "seq": self.dispatcher.hub.seq})
        return await self._json(writer, 404, {"error": "not found"})

    def _caller(self, headers: dict[str, str], writer: asyncio.StreamWriter) -> str:
        """D-039: principal only on the Unix socket, with the credential, from a process outside the core's tree."""
        if self._unix_path is None or not self._principal_token:
            return pr.LOCAL
        return pr.stamp(headers.get(pr.HEADER.lower()), self._principal_token, writer.get_extra_info("socket"),
                        headers.get("x-exo-client", ""))

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
                     b"Connection: close\r\n\r\n: exod\n\n")
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
                else:
                    writer.write(f"id: {ev.seq}\nevent: {ev.type}\ndata: {ev.to_json()}\n\n".encode())
                await writer.drain()
        finally:
            sub.close()


# ---------------------------------------------------------------- stdio


async def serve_lines(dispatcher: Dispatcher, lines: Callable[[], Awaitable[str | None]],
                      write: Callable[[str], None]) -> None:
    """Run the JSON-lines transport until ``lines`` returns None (EOF)."""
    feed: asyncio.Task | None = None
    cursor: dict[str, Any] = {}  # last seq written and session filter of the active feed

    async def pump(since: int, session: str | None) -> None:
        cursor.update(seq=since, session=session)
        sub = dispatcher.hub.subscribe(since, session)
        try:
            async for ev in sub:
                write(ev.to_json())
                cursor["seq"] = ev.seq
        finally:
            sub.close()

    try:
        while (line := await lines()) is not None:
            if not line.strip():
                continue
            cmd, resp = await dispatcher.dispatch(line)
            if cmd is not None and isinstance(cmd.body, s.EventsSubscribe) and resp.ok:
                if feed:
                    feed.cancel()
                resp = s.Response(id=cmd.id, result={"subscribed": True, "seq": dispatcher.hub.seq})
                write(resp.to_json())
                feed = asyncio.create_task(pump(cmd.body.since, cmd.body.session))
                await asyncio.sleep(0)  # start the replay before the next command
                continue
            write(resp.to_json())
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


DEFAULT_ENGINE = "exo.core.engine:create"


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


async def serve(engine: Engine, socket: str | os.PathLike | None = None, tcp: tuple[str, int] | None = None,
                token: str | None = None, stdio: bool = False, hub: Hub | None = None,
                ready: Callable[[str], None] | None = None) -> None:
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
        await srv.start_unix(socket)
        servers.append(srv)
        if (start_peers := getattr(engine, "start_peers", None)) is not None:
            await start_peers(socket)  # D-026: presence file so other daemons find this one
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
    link = None
    try:
        if getattr(engine, "_channel_cfg", None):  # channels talk to the server over a real protocol Client
            from exo.protocol.client import Client
            link = Client(socket=str(socket)) if socket else Client(url=f"http://{tcp[0]}:{port}", token=token)
            for name, err in (await engine.start_channels(link)).items():
                if err:
                    print(f"exod: channel {name}: {err}", file=sys.stderr)
        await asyncio.gather(*(srv.serve_forever() for srv in servers))
    finally:
        dispatcher.hub.close()
        if (close := getattr(engine, "close", None)) is not None:
            await close()
        if link is not None:
            await link.aclose()
        for srv in servers:
            await srv.close()
        if socket:
            Path(socket).unlink(missing_ok=True)
