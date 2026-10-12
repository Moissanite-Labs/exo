"""The web client: a small asyncio HTTP server over a ``CommandCentre``.

Standard library only (no fastapi, no uvicorn). It is the process that ``exo web`` will start; until
then run it as a module, see ``__main__.py``.

Routes:
  GET  /                      the page (static/index.html)
  GET  /static/<file>         app.js and style.css
  GET  /api/health            connection state
  GET  /api/snapshot          ``?sections=a,b&fresh=true``
  GET  /api/section/<name>
  GET  /api/stream            Server-Sent Events: ``data: {"seq": N}`` after each change in the core
  GET  /api/events            Server-Sent Events: every protocol event as JSON (``?since=N&session=ID``,
                              or the Last-Event-ID header); the chat page streams text from here
  ...                         chat, feedback, ui.respond, tasks and schedule routes: see ``api.py``
  POST /api/approvals/<id>    body ``{"decision": "allow|deny|allow_always", "note": ""}``

The server binds to loopback by default. Writes are limited to the routes in ``api.py`` and answering an
approval, and a POST from another origin is refused, so a web page elsewhere cannot answer your approvals.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import api
from .service import SECTIONS, CommandCentre
from .source import SourceError

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 64 * 1024
HEARTBEAT = 15.0
REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           501: "Not Implemented", 502: "Bad Gateway"}
DECISIONS = ("allow", "deny", "allow_always")


class WebServer:
    def __init__(self, centre: CommandCentre):
        self.centre = centre
        self._server: Optional[asyncio.AbstractServer] = None
        self._conns: set = set()
        self._feeds: list = []

    # ------------------------------------------------------------ lifecycle

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._client, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def run(self, host: str, port: int) -> None:
        """Connect to the core, then serve until cancelled. Keeps retrying the core in the background."""
        c = self.centre
        if c.source is None:
            c.connect()
        bound = await self.start(host, port)
        print("exo-command: http://{}:{}".format(host, bound), flush=True)
        feed = asyncio.create_task(self._feed())
        try:
            await asyncio.get_running_loop().create_future()
        finally:
            feed.cancel()
            await self.close()

    async def _feed(self) -> None:
        c = self.centre
        while True:
            try:
                await c.bootstrap()
                await c.follow()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # core down or restarting: show it, retry
                c.connected, c.last_error = False, "{}: {}".format(type(exc).__name__, exc)
                c._notify()
            await asyncio.sleep(2.0)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for t in list(self._conns):
            t.cancel()
        if self._server is not None:
            await self._server.wait_closed()

    # ------------------------------------------------------------ http

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._conns.add(task)
        try:
            await self._request(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        except asyncio.CancelledError:
            pass
        finally:
            self._conns.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = (await reader.readuntil(b"\r\n")).decode("latin-1").strip()
        method, target, _ = line.split(" ", 2)
        headers: Dict[str, str] = {}
        while True:
            h = (await reader.readuntil(b"\r\n")).decode("latin-1").strip()
            if not h:
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        url = urlsplit(target)
        path, q = url.path, parse_qs(url.query)
        c = self.centre
        if path == "/" and method == "GET":
            return await self._file(writer, STATIC / "index.html")
        if path.startswith("/static/") and method == "GET":
            f = (STATIC / path[len("/static/"):]).resolve()
            if STATIC not in f.parents or not f.is_file():
                return await self._json(writer, 404, {"error": "not found"})
            return await self._file(writer, f)
        if path == "/api/health" and method == "GET":
            return await self._json(writer, 200, {"ok": True, "connected": c.connected, "error": c.last_error,
                                                  "seq": c.state.seq, "settings": c.settings.public()})
        if path == "/api/snapshot" and method == "GET":
            only = [x.strip() for x in q["sections"][0].split(",")] if q.get("sections") else None
            fresh = q.get("fresh", ["false"])[0].lower() in ("1", "true", "yes")
            return await self._json(writer, 200, c.snapshot(fresh=fresh, only=only))
        if path.startswith("/api/section/") and method == "GET":
            name = path[len("/api/section/"):]
            if name not in SECTIONS:
                return await self._json(writer, 404, {"error": "unknown section {!r}".format(name)})
            return await self._json(writer, 200, c.section(name, fresh=q.get("fresh", ["false"])[0] == "true"))
        if path == "/api/stream" and method == "GET":
            return await self._sse(writer)
        if path.startswith("/api/approvals/"):
            if method != "POST":
                return await self._json(writer, 405, {"error": "POST only"})
            return await self._approve(reader, writer, headers, path[len("/api/approvals/"):])
        if path == "/api/events" and method == "GET":
            raw = (q.get("since") or [headers.get("last-event-id") or "0"])[0]
            since = int(raw) if raw.isdigit() else 0
            return await self._events(writer, since, (q.get("session") or [None])[0])
        if path.startswith("/api/"):
            body: Dict[str, Any] = {}
            if method == "POST":
                got = await self._body(reader, writer, headers)
                if got is None:
                    return
                body = got
            reply = await api.dispatch(c, method, path, q, body)
            if reply is not None:
                return await self._json(writer, reply[0], reply[1])
        return await self._json(writer, 404, {"error": "not found"})

    async def _body(self, reader, writer, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Read a JSON object body from a same-origin POST; answer the error and return None otherwise."""
        origin, host = headers.get("origin"), headers.get("host", "")
        if origin and urlsplit(origin).netloc != host:
            await self._json(writer, 403, {"error": "cross-origin request refused"})
            return None
        try:
            n = int(headers.get("content-length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            await self._json(writer, 400, {"error": "body too large"})
            return None
        try:
            body = json.loads(await reader.readexactly(n)) if n else {}
        except ValueError:
            body = None
        if not isinstance(body, dict):
            await self._json(writer, 400, {"error": "body must be a json object"})
            return None
        return body

    async def _events(self, writer, since: int, session: Optional[str]) -> None:
        """Every protocol event after ``since`` as ``id: seq`` + ``data: <event json>``, then live ones."""
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Connection: close\r\n\r\n: exo-command events\n\n")
        q = self.centre.subscribe_events()
        try:
            sent = since
            for ev in self.centre.events_since(since, session):
                writer.write(_frame(ev))
                sent = ev["seq"]
            await writer.drain()
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), HEARTBEAT)
                except asyncio.TimeoutError:
                    writer.write(b": ping\n\n")
                    await writer.drain()
                    continue
                if ev is None:
                    return
                if ev["seq"] <= sent or (session and ev.get("session") not in (None, session)):
                    continue
                sent = ev["seq"]
                writer.write(_frame(ev))
                await writer.drain()
        finally:
            self.centre.unsubscribe_events(q)

    async def _approve(self, reader, writer, headers: Dict[str, str], rid: str) -> None:
        origin, host = headers.get("origin"), headers.get("host", "")
        if origin and urlsplit(origin).netloc != host:
            return await self._json(writer, 403, {"error": "cross-origin request refused"})
        n = int(headers.get("content-length", "0"))
        if n > MAX_BODY:
            return await self._json(writer, 400, {"error": "body too large"})
        try:
            body = json.loads(await reader.readexactly(n)) if n else {}
        except ValueError:
            return await self._json(writer, 400, {"error": "bad json"})
        decision = body.get("decision")
        if decision not in DECISIONS:
            return await self._json(writer, 400, {"error": "decision must be one of {}".format(", ".join(DECISIONS))})
        try:
            await self.centre.respond_approval(rid, decision, str(body.get("note") or ""))
        except SourceError as exc:
            return await self._json(writer, 404 if exc.code == "not_found" else 502,
                                    {"error": exc.message, "code": exc.code})
        except Exception as exc:
            return await self._json(writer, 502, {"error": "{}: {}".format(type(exc).__name__, exc)})
        return await self._json(writer, 200, {"ok": True})

    async def _send(self, writer, status: int, body: bytes, ctype: str) -> None:
        head = ("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nCache-Control: no-store\r\n"
                "Connection: close\r\n\r\n").format(status, REASONS.get(status, ""), ctype, len(body))
        writer.write(head.encode() + body)
        await writer.drain()

    async def _json(self, writer, status: int, data: Any) -> None:
        await self._send(writer, status, json.dumps(data, default=_json_default).encode(), "application/json")

    async def _file(self, writer, f: Path) -> None:
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        await self._send(writer, 200, f.read_bytes(), ctype)

    async def _sse(self, writer) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Connection: close\r\n\r\n: exo-command\n\n")
        q = self.centre.subscribe()
        try:
            writer.write("data: {}\n\n".format(json.dumps({"seq": self.centre.state.seq})).encode())
            await writer.drain()
            while True:
                try:
                    seq = await asyncio.wait_for(q.get(), HEARTBEAT)
                    writer.write("data: {}\n\n".format(json.dumps({"seq": seq})).encode())
                except asyncio.TimeoutError:
                    writer.write(b": ping\n\n")
                await writer.drain()
        finally:
            self.centre.unsubscribe(q)


def _frame(ev: Dict[str, Any]) -> bytes:
    return "id: {}\ndata: {}\n\n".format(ev["seq"], json.dumps(ev, default=_json_default)).encode()


def _json_default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "__iter__"):
        return list(o)
    return str(o)
