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
  POST /api/launch            body ``{"code": "..."}``: trade a one-time launch code for the page token (#166)
  *    /api/providers...       provider setup (D-034): see ``providers.py``; loopback peer, loopback Host,
                              same Origin and the per-launch token, or refused

Access (D-039). The server binds to loopback by default. Every request must name this server in its Host header
(``127.0.0.1:<port>``, ``localhost:<port>`` or ``[::1]:<port>``), so a DNS rebinding name gets nothing, not even the
page. Every ``/api/`` route except ``POST /api/launch`` (below) and both event streams also need the per-launch page
token: ``X-Exo-Token`` on fetches, or ``?token=`` on the two event streams (a browser EventSource cannot set headers).
An Origin, when sent, must be this same host, and Sec-Fetch-Site must be same-origin or none. Besides the launch
route, only the page shell (``/`` and ``/static/``) is served without the token: it is code, not data. `exo web`
prints the page address with the token in the URL fragment (never sent to a server); the page moves it to
sessionStorage.

Launch code (#166). The browser is opened on ``#code=<one-time code>``, never on the token: the launcher's argv is
readable by other users of the host in /proc. ``/api/launch`` is the one ``/api/`` route without the token; it
passes every other check of ``providers.guard``. A code answers the token once, for at most ``LAUNCH_TTL`` seconds.
A code that comes back after it was used means two parties held it: the token is replaced, every request in flight
ends (event streams, and requests that passed the token check but are still waiting for a body), and the terminal
gets a warning and the new link. A body is checked against the current token again once it is read. Detection needs
that second redemption: a launched browser that never runs the page's script never redeems, and then nothing warns.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from exo.core.models.keys import redact_secrets

from . import api
from . import credentials as creds
from . import providers as prov
from .service import SECTIONS, CommandCentre
from .source import SourceError, core_message

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 64 * 1024
# Only the send route takes images, and the core refuses any /command body over 8 MiB, so a bigger cap here would
# only move the refusal. Pad has its own bounded cap; other routes keep MAX_BODY.
MAX_SEND_BODY = 8 * 1024 * 1024
SEND_ROUTE = re.compile(r"/api/chat/sessions/[^/]+/send")
# A valid 32 KiB Pad can use six JSON bytes per ASCII character.
MAX_PAD_BODY = 32768 * 6 + 1024
HEARTBEAT = 15.0
# Limits on reading a request (not on a stream that was already accepted). A client that stalls, sends endless header
# lines, or holds a socket without asking for anything is dropped; the server stays answerable to the page.
READ_TIMEOUT_S = 10.0          # the whole header block, and each body read
MAX_HEADER_LINES = 100
MAX_HEADER_BYTES = 16 * 1024   # request line + header lines
MAX_REQUEST_LINE = 8 * 1024
OVERFLOW_S = 1.0               # how long an over-cap connection may take to send its head
MAX_CONNECTIONS = 64           # ordinary handlers (including streams); equally bounded rejection pool replies 503
GZIP_MIN = 256                 # smaller assets are sent as they are
GZIP_TYPES = ("text/", "application/javascript", "application/json", "image/svg+xml")
# One policy for every response (#162): script, style, images and requests from this server only. No inline script,
# style attribute or eval, no plugins, no <base>, no native form posts (every form is handled in script), no framing.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = ("Content-Security-Policy: {}\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
                    .format(CSP))
REASONS = {200: "OK", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 408: "Request Timeout", 431: "Request Header Fields Too Large",
           501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable"}
DECISIONS = ("allow", "deny", "allow_always")
STREAMS = ("/api/events", "/api/stream")
LAUNCH = "/api/launch"
LAUNCH_TTL = 120.0
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@]*@")
_ABS_PATH = re.compile(r"(?<![\w/:.\-])(?:~|[A-Za-z]:)?/(?:[^\s/'\"<>:]+/)+[^\s'\"<>:]*")
BAD_TOKEN = {"error": "missing or wrong page token: open the link `exo web` printed"}


class WebServer:
    def __init__(self, centre: CommandCentre, token: Optional[str] = None):
        self.centre = centre
        # Per launch, held in memory only. `exo web` prints it in the URL fragment (never sent to a server);
        # the page keeps it in sessionStorage and sends it as X-Exo-Token on provider routes (D-034).
        self.token = token or secrets.token_urlsafe(32)
        self.router_factory = prov.ps.build_router
        self._server: Optional[asyncio.AbstractServer] = None
        self._conns: set = set()
        self._overflow_conns: set = set()
        self._close_task: Optional[asyncio.Task] = None
        self._feeds: list = []
        self._hosts: set = set()
        self._codes: Dict[str, float] = {}   # unused launch codes -> monotonic deadline
        self._spent: set = set()             # redeemed launch codes: one coming back means it leaked
        self._shown: Optional[Tuple[str, int]] = None
        self.max_connections = MAX_CONNECTIONS
        self._static: Dict[str, Tuple[int, int, str, bytes, Optional[bytes]]] = {}   # path -> mtime, size, etag, raw, gz

    # ------------------------------------------------------------ lifecycle

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        if self._close_task is not None and (not self._close_task.done() or self._conns):
            raise RuntimeError("web server shutdown is still in progress")
        if not _is_loopback_bind(host):
            msg = ("WARNING: listening on {!r}, not a loopback address. The page token and every request "
                   "travel over plain HTTP and can be read by anyone on that network. Bind 127.0.0.1 (the default) "
                   "or put a TLS proxy in front.".format(host or "all interfaces"))
            print("exo-command: " + msg, file=sys.stderr, flush=True)
        self._server = await asyncio.start_server(self._accept_client, host, port)
        self._close_task = None
        bound = self._server.sockets[0].getsockname()[1]
        names = {"127.0.0.1", "localhost", "[::1]"}
        if host and host not in ("0.0.0.0", "::", ""):
            names.add("[{}]".format(host) if ":" in host and not host.startswith("[") else host)
        self._hosts = {"{}:{}".format(n, bound).lower() for n in names}
        self._shown = (host, bound)
        return bound

    def _refuse(self, headers: Dict[str, str], path: str, q: Dict[str, Any]) -> Optional[Tuple[int, Dict[str, Any]]]:
        """None when the request may go on, else the refusal (D-039; see the module docstring)."""
        host = headers.get("host", "").strip().lower()
        if host not in self._hosts:
            return 403, {"error": "bad Host header: open the page at the address `exo web` printed"}
        if path == "/" or path.startswith("/static/"):
            return None
        origin = headers.get("origin")
        if origin is not None and origin.strip().lower() != "http://" + host:
            return 403, {"error": "cross-origin request refused"}
        site = headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            return 403, {"error": "cross-site request refused"}
        if path == LAUNCH:   # no token yet: _launch runs providers.guard_peer instead
            return None
        sent = headers.get(prov.TOKEN_HEADER, "")
        if not sent and path in STREAMS:
            sent = (q.get("token") or [""])[0]
        if not self._current(sent):
            return 403, BAD_TOKEN
        return None

    def _current(self, sent: str) -> bool:
        return bool(sent) and hmac.compare_digest(sent.encode(), self.token.encode())

    async def run(self, host: str, port: int) -> None:
        """Connect to the core, then serve until cancelled. Keeps retrying the core in the background."""
        c = self.centre
        if c.source is None:
            c.connect()
        bound = await self.start(host, port)
        url = self.page_url(host, bound)
        print("exo-command: {}".format(url), flush=True)
        try:
            if not await asyncio.to_thread(prov.ps.any_ready):
                print("exo-command: no model provider is set up yet. Open Providers in the page, "
                      "or run: exo providers add <provider>", flush=True)
        except Exception:
            pass
        if self.open_browser and not _loopback_shown(host):
            # /api/launch answers loopback only, and the token link must not go back into the launcher's argv
            print("exo-command: not opening a browser: the one-time browser link works only when --host is "
                  "127.0.0.1, localhost, ::1 or a wildcard bind. Open the link above.", flush=True)
        elif self.open_browser:
            try:
                import webbrowser
                webbrowser.open(self.launch_url(host, bound))
            except Exception:
                pass
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
                if c.auto_core:
                    await c.ensure_core()
                await c.bootstrap()
                await c.follow()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # core down or restarting: show it, retry
                c.connected, c.last_error = False, core_message(exc)
                c._notify()
            await asyncio.sleep(2.0)

    open_browser = False

    def page_url(self, host: str, port: int) -> str:
        """The page address with the token in the fragment, so it never reaches a server or a log line."""
        return "{}/#token={}".format(_origin(host, port), self.token)

    def launch_url(self, host: str, port: int) -> str:
        """The address the browser is opened on: a fresh one-time code, not the token (#166)."""
        now = time.monotonic()
        self._codes = {c: t for c, t in self._codes.items() if t > now}
        code = secrets.token_urlsafe(32)
        self._codes[code] = now + LAUNCH_TTL
        return "{}/#code={}".format(_origin(host, port), code)

    def redeem(self, code: Any) -> Tuple[int, Dict[str, Any]]:
        """The page token for an unused, unexpired launch code, once. A used code coming back replaces the token."""
        if not isinstance(code, str) or not code:
            return 403, {"error": "no launch code"}
        sent = code.encode()
        for c in list(self._spent):
            if hmac.compare_digest(sent, c.encode()):
                self._spent.discard(c)   # one replacement per leaked code: its holder never saw the new token
                self.rotate()
                return 403, {"error": "launch code already used: the page token was replaced"}
        for c, deadline in list(self._codes.items()):
            if hmac.compare_digest(sent, c.encode()):
                del self._codes[c]
                if time.monotonic() >= deadline:
                    return 403, {"error": "launch code expired"}
                self._spent.add(c)
                return 200, {"token": self.token}
        return 403, {"error": "unknown launch code"}

    def rotate(self) -> None:
        """Replace the page token. Every open page must re-open. Every other connection ends now: streams opened with
        the old token, and requests that passed the token check and still wait for their body."""
        self.token = secrets.token_urlsafe(32)
        try:
            me = asyncio.current_task()
        except RuntimeError:   # no running loop
            me = None
        for t in list(self._conns):
            if t is not me:
                t.cancel()
        if self._shown is not None:
            # paste, not click: a click from the terminal hands the token to the URL opener's argv again (#166)
            print("exo-command: warning: a browser launch link was used twice, so someone else may have had it. "
                  "The page token was replaced; paste the new link into the browser that is already open:",
                  flush=True)
            print("exo-command: {}".format(self.page_url(*self._shown)), flush=True)

    async def close(self) -> None:
        # One drain owns cancellation: concurrent/repeated closes must not interrupt a
        # handler's asynchronous finally block with another cancellation.
        if self._close_task is not None and asyncio.current_task() in self._conns:
            # The drain may already be joining this handler's cancellation cleanup.
            # Joining it here would make each task wait for the other.
            return
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(asyncio.current_task()))
        await asyncio.shield(self._close_task)
        if asyncio.current_task() not in self._conns:
            # An external closer also joins a handler that initiated the drain.
            await asyncio.shield(asyncio.gather(*self._conns, return_exceptions=True))

    async def _close(self, caller: Optional[asyncio.Task]) -> None:
        if self._server is not None:
            self._server.close()
        # A handler may initiate shutdown. It cannot join itself, but every other
        # owned handler must finish (including stream unsubscription) before return.
        clients = [t for t in self._conns if t is not caller]
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        # Newer asyncio versions also wait for client transports here. A handler
        # initiating close still owns its transport until this method returns.
        if self._server is not None and caller not in self._conns:
            await self._server.wait_closed()

    # ------------------------------------------------------------ http

    def _accept_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._close_task is not None:
            writer.close()
            return
        over = len(self._conns) - len(self._overflow_conns) >= self.max_connections
        if over and len(self._overflow_conns) >= self.max_connections:
            # The bounded rejection pool is full too: do not create more tasks
            # for idle peers. Ordinary handlers retain their own admission budget.
            writer.close()
            return
        # Register both ordinary and rejection handlers before their first turn.
        # Rotation and shutdown own every accepted handler, including idle overflow.
        task = asyncio.create_task(self._overflow(reader, writer) if over else self._client(reader, writer))
        self._conns.add(task)
        if over:
            self._overflow_conns.add(task)

        def finished(task: asyncio.Task) -> None:
            # Cancellation before the coroutine's first turn cannot run its finally.
            self._conns.discard(task)
            self._overflow_conns.discard(task)
            try:
                writer.close()
            except Exception:
                pass

        task.add_done_callback(finished)

    async def _overflow(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        body = b'{"error": "too many connections"}'
        try:
            try:
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), OVERFLOW_S)
            except Exception:
                pass
            writer.write(("HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                          "Content-Length: {}\r\nCache-Control: no-store\r\nRetry-After: 1\r\n{}"
                          "Connection: close\r\n\r\n").format(len(body), SECURITY_HEADERS).encode() + body)
            await asyncio.wait_for(writer.drain(), OVERFLOW_S)
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._conns.add(task)
        try:
            await self._request(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError,
                asyncio.TimeoutError, TimeoutError):
            pass
        except asyncio.CancelledError:
            pass
        finally:
            self._conns.discard(task)
            try:
                writer.close()
            except Exception:
                pass

    async def _read_head(self, reader: asyncio.StreamReader) -> Optional[Tuple[str, str, Dict[str, str]]]:
        """The request line and headers, or None when they pass a cap. Raises on EOF or a bad line."""
        raw = await reader.readuntil(b"\r\n")
        used = len(raw)
        if used > MAX_REQUEST_LINE:
            return None
        method, target, _ = raw.decode("latin-1").strip().split(" ", 2)
        headers: Dict[str, str] = {}
        lines = 0
        while True:
            raw = await reader.readuntil(b"\r\n")
            used += len(raw)
            lines += 1
            h = raw.decode("latin-1").strip()
            if not h:
                break
            if lines > MAX_HEADER_LINES or used > MAX_HEADER_BYTES:
                return None
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        return method, target, headers

    async def _read_body(self, reader: asyncio.StreamReader, n: int) -> bytes:
        """``n`` body bytes within READ_TIMEOUT_S; a stalled sender raises TimeoutError and the connection closes."""
        return await asyncio.wait_for(reader.readexactly(n), READ_TIMEOUT_S)

    async def _request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:   # the whole header block shares one deadline, so a client cannot hold the socket a byte at a time
            head = await asyncio.wait_for(self._read_head(reader), READ_TIMEOUT_S)
        except asyncio.TimeoutError:
            return   # dropped: nothing was asked for yet
        if head is None:
            return await self._json(writer, 431, {"error": "request headers too large"})
        method, target, headers = head
        url = urlsplit(target)
        path, q = url.path, parse_qs(url.query)
        refused = self._refuse(headers, path, q)
        if refused is not None:
            return await self._json(writer, *refused)
        c = self.centre
        if path == "/" and method == "GET":
            return await self._file(writer, STATIC / "index.html")   # no-store: the shell is always fetched anew
        if path.startswith("/static/") and method == "GET":
            f = (STATIC / path[len("/static/"):]).resolve()
            if STATIC not in f.parents or not f.is_file():
                return await self._json(writer, 404, {"error": "not found"})
            return await self._file(writer, f, headers)
        if path == "/api/health" and method == "GET":
            return await self._json(writer, 200, {"ok": True, "connected": c.connected,
                                                  "error": _public_text(c.last_error),
                                                  "seq": c.state.seq, "settings": c.settings.public()})
        if path == "/api/snapshot" and method == "GET":
            only = [x.strip() for x in q["sections"][0].split(",")] if q.get("sections") else None
            fresh = q.get("fresh", ["false"])[0].lower() in ("1", "true", "yes")
            return await self._json(writer, 200, c.snapshot(fresh=fresh, only=only))
        if path.startswith("/api/section/") and method == "GET":
            name = path[len("/api/section/"):]
            if name not in SECTIONS:
                return await self._json(writer, 404, {"error": "unknown section {!r}".format(name)})
            if name == "approvals":   # D-045: ask the core first, so a card it no longer holds is not served
                await c.reconcile_approvals()
            return await self._json(writer, 200, c.section(name, fresh=q.get("fresh", ["false"])[0] == "true"))
        if path == "/api/stream" and method == "GET":
            return await self._sse(writer)
        if path.startswith("/api/approvals/"):
            if method != "POST":
                return await self._json(writer, 405, {"error": "POST only"})
            return await self._approve(reader, writer, headers, path[len("/api/approvals/"):])
        if path == "/api/events" and method == "GET":
            # A browser that reconnects by itself repeats the first URL (its old ?since=) and adds Last-Event-ID:
            # resume from the later of the two, or every dropped connection replays the page's whole history (D-045).
            raws = [(q.get("since") or ["0"])[0], headers.get("last-event-id") or "0"]
            since = max([int(r) for r in raws if r.isdigit()] or [0])
            return await self._events(writer, since, (q.get("session") or [None])[0])
        if path == LAUNCH:
            if method != "POST":
                return await self._json(writer, 405, {"error": "POST only"})
            return await self._launch(reader, writer, headers)
        if path == "/api/credentials" or path == "/api/providers" or path.startswith("/api/providers/"):
            return await self._providers(reader, writer, headers, method, path)
        if path == "/api/complete/files":   # file names: same guard as the key routes (loopback, Host, token)
            peer = writer.get_extra_info("peername")
            refused = prov.guard(headers, peer[0] if isinstance(peer, (tuple, list)) and peer else None, self.token)
            if refused is not None:
                return await self._json(writer, *refused)
        if path.startswith("/api/"):
            body: Dict[str, Any] = {}
            if method == "POST":
                limit = MAX_SEND_BODY if SEND_ROUTE.fullmatch(path) else MAX_PAD_BODY if path == "/api/pad" else MAX_BODY
                got = await self._body(reader, writer, headers, limit=limit)
                if got is None:
                    return
                body = got
            reply = await api.dispatch(c, method, path, q, body)
            if reply is not None:
                return await self._json(writer, reply[0], reply[1])
        return await self._json(writer, 404, {"error": "not found"})

    async def _providers(self, reader, writer, headers: Dict[str, str], method: str, path: str) -> None:
        """Provider setup (D-034). The guard runs before the body is read; the body (which may hold a key) is
        never logged, echoed or kept."""
        peer = writer.get_extra_info("peername")
        refused = prov.guard(headers, peer[0] if isinstance(peer, (tuple, list)) and peer else None, self.token)
        if refused is not None:
            return await self._json(writer, *refused)
        body: Dict[str, Any] = {}
        if method == "POST":
            got = await self._body(reader, writer, headers)
            if got is None:
                return
            body = got
        if path == "/api/credentials":
            status, data = await creds.handle(self.centre, method, body)
        else:
            status, data = await prov.handle(self.centre, method, path, body, self.router_factory)
        body.clear()
        return await self._json(writer, status, data)

    async def _launch(self, reader, writer, headers: Dict[str, str]) -> None:
        """Redeem a launch code (#166). Fails closed: any error answers 403 and no token."""
        peer = writer.get_extra_info("peername")
        refused = prov.guard_peer(headers, peer[0] if isinstance(peer, (tuple, list)) and peer else None)
        if refused is not None:
            return await self._json(writer, *refused)
        try:
            n = int(headers.get("content-length", "0"))
            body = json.loads(await self._read_body(reader, n)) if 0 < n <= 1024 else None
            status, data = self.redeem(body.get("code") if isinstance(body, dict) else None)
        except Exception:
            status, data = 403, {"error": "launch code refused"}
        return await self._json(writer, status, data)

    async def _body(self, reader, writer, headers: Dict[str, str], *, limit: int = MAX_BODY) -> Optional[Dict[str, Any]]:
        """Read a JSON object body from a same-origin POST; answer the error and return None otherwise."""
        origin, host = headers.get("origin"), headers.get("host", "")
        if origin and urlsplit(origin).netloc != host:
            await self._json(writer, 403, {"error": "cross-origin request refused"})
            return None
        try:
            n = int(headers.get("content-length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            await self._json(writer, 400, {"error": "body too large"})
            return None
        try:
            body = json.loads(await self._read_body(reader, n)) if n else {}
        except (ValueError, RecursionError):
            body = None
        if not self._current(headers.get(prov.TOKEN_HEADER, "")):   # replaced while the body was on its way
            await self._json(writer, 403, BAD_TOKEN)
            return None
        if not isinstance(body, dict):
            await self._json(writer, 400, {"error": "body must be a json object"})
            return None
        return body

    async def _events(self, writer, since: int, session: Optional[str]) -> None:
        """Every protocol event after ``since`` as ``id: seq`` + ``data: <event json>``, then live ones."""
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     + SECURITY_HEADERS.encode() + b"Connection: close\r\n\r\n: exo-command events\n\n")
        q = self.centre.subscribe_events()
        try:
            events, gap = await self.centre.replay_events(since, session)
            sent = 0 if gap and gap["reset"] else since
            if gap:
                writer.write(b"event: gap\ndata: " + json.dumps(gap).encode() + b"\n\n")
            for ev in events:
                writer.write(_frame(ev))
                # Replay obeys the same backpressure as live events. A slow
                # browser must not accumulate the whole log in the transport.
                await asyncio.wait_for(writer.drain(), 10)
                await asyncio.sleep(0)
                sent = ev["seq"]
            writer.write(b'event: synced\ndata: {}\n\n')
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
        try:
            n = int(headers.get("content-length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return await self._json(writer, 400, {"error": "body too large"})
        try:
            body = json.loads(await self._read_body(reader, n)) if n else {}
        except (ValueError, RecursionError):
            return await self._json(writer, 400, {"error": "bad json"})
        if not self._current(headers.get(prov.TOKEN_HEADER, "")):   # replaced while the body was on its way
            return await self._json(writer, 403, BAD_TOKEN)
        if not isinstance(body, dict):
            return await self._json(writer, 400, {"error": "body must be a json object"})
        decision = body.get("decision")
        if decision not in DECISIONS:
            return await self._json(writer, 400, {"error": "decision must be one of {}".format(", ".join(DECISIONS))})
        try:
            await self.centre.respond_approval(rid, decision, str(body.get("note") or ""))
        except SourceError as exc:
            return await self._json(writer, 404 if exc.code == "not_found" else 502,
                                    {"error": api._safe_message(exc.message), "code": exc.code})
        except Exception as exc:
            return await self._json(writer, 502, {"error": core_message(exc)})
        return await self._json(writer, 200, {"ok": True})

    async def _send(self, writer, status: int, body: bytes, ctype: str, extra: str = "",
                    cache: str = "no-store") -> None:
        head = ("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nCache-Control: {}\r\n"
                "{}{}Connection: close\r\n\r\n").format(status, REASONS.get(status, ""), ctype, len(body), cache,
                                                          extra, SECURITY_HEADERS)
        writer.write(head.encode() + body)
        await writer.drain()

    async def _json(self, writer, status: int, data: Any) -> None:
        await self._send(writer, status, json.dumps(data, default=_json_default).encode(), "application/json")

    async def _file(self, writer, f: Path, headers: Optional[Dict[str, str]] = None) -> None:
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        textual = ctype.startswith(GZIP_TYPES)
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        if headers is None:   # the page shell: always fetched anew
            return await self._send(writer, 200, f.read_bytes(), ctype)
        etag, raw, gz = self._cached(f, textual)
        use_gz = gz is not None and "gzip" in _accepts(headers.get("accept-encoding", ""))
        tag = '"{}{}"'.format(etag, "-gzip" if use_gz else "")
        extra = "ETag: {}\r\nVary: Accept-Encoding\r\n".format(tag)
        if _etag_matches(headers.get("if-none-match", ""), tag):
            head = ("HTTP/1.1 304 Not Modified\r\nCache-Control: no-cache\r\n{}{}Connection: close\r\n\r\n"
                    .format(extra, SECURITY_HEADERS))
            writer.write(head.encode())
            return await writer.drain()
        payload = raw
        if use_gz and gz is not None:
            extra += "Content-Encoding: gzip\r\n"
            payload = gz
        await self._send(writer, 200, payload, ctype, extra, "no-cache")

    def _cached(self, f: Path, textual: bool) -> Tuple[str, bytes, Optional[bytes]]:
        """``(etag, bytes, gzip bytes or None)`` of a static file, computed once and again only when its mtime or
        size changes."""
        st = f.stat()
        key = str(f)
        hit = self._static.get(key)
        if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2], hit[3], hit[4]
        raw = f.read_bytes()
        etag = hashlib.sha256(raw).hexdigest()[:32]
        gz = None
        if textual and len(raw) >= GZIP_MIN:
            packed = gzip.compress(raw, 6, mtime=0)
            gz = packed if len(packed) < len(raw) else None
        self._static[key] = (st.st_mtime_ns, st.st_size, etag, raw, gz)
        return etag, raw, gz

    async def _sse(self, writer) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     + SECURITY_HEADERS.encode() + b"Connection: close\r\n\r\n: exo-command\n\n")
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


def _accepts(value: str) -> set:
    """Content codings a client accepts, without those it refuses with q=0."""
    out = set()
    for part in value.lower().split(","):
        name, _, params = part.partition(";")
        q = params.replace(" ", "")
        if name.strip() and q not in ("q=0", "q=0.0", "q=0.00", "q=0.000"):
            out.add(name.strip())
    return out


def _etag_matches(header: str, tag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    want = tag[2:] if tag.startswith("W/") else tag
    return any((t.strip()[2:] if t.strip().startswith("W/") else t.strip()) == want for t in header.split(","))


def _public_text(text: Any) -> Any:
    """A status sentence for /api/health: no URL userinfo, no known key value, no absolute filesystem path."""
    if not isinstance(text, str) or not text:
        return text
    return _ABS_PATH.sub("[path]", redact_secrets(_USERINFO.sub(r"\1", text)))


def _is_loopback_bind(host: str) -> bool:
    return (host or "").strip("[]").lower() in ("127.0.0.1", "localhost", "::1") or (host or "").startswith("127.")


def _origin(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    if ":" in shown and not shown.startswith("["):
        shown = "[{}]".format(shown)
    return "http://{}:{}".format(shown, port)


def _loopback_shown(host: str) -> bool:
    """Whether the address `exo web` shows for this bind passes the loopback Host check of /api/launch."""
    return prov._host_name(urlsplit(_origin(host, 0)).netloc) in prov.LOOPBACK_NAMES


def _frame(ev: Dict[str, Any]) -> bytes:
    return "id: {}\ndata: {}\n\n".format(ev["seq"], json.dumps(ev, default=_json_default)).encode()


def _json_default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "__iter__"):
        return list(o)
    return str(o)
