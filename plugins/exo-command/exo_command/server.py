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
import hmac
import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import api
from . import credentials
from . import providers as prov
from .service import SECTIONS, CommandCentre, CoreConnection
from .source import SourceError, core_message

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 64 * 1024
HEARTBEAT = 15.0
# One policy for every response (#162): script, style, images and requests from this server only. No inline script,
# style attribute or eval, no plugins, no <base>, no native form posts (every form is handled in script), no framing.
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = ("Content-Security-Policy: {}\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
                    .format(CSP))
REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           501: "Not Implemented", 502: "Bad Gateway"}
DECISIONS = ("allow", "deny", "allow_always")
STREAMS = ("/api/events", "/api/stream")
LAUNCH = "/api/launch"
LAUNCH_TTL = 120.0
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
        self._feeds: list = []
        self._hosts: set = set()
        self._codes: Dict[str, float] = {}   # unused launch codes -> monotonic deadline
        self._spent: set = set()             # redeemed launch codes: one coming back means it leaked
        self._shown: Optional[Tuple[str, int]] = None

    # ------------------------------------------------------------ lifecycle

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._client, host, port)
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
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
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
        refused = self._refuse(headers, path, q)
        if refused is not None:
            return await self._json(writer, *refused)
        c = self.centre
        if path == "/" and method == "GET":
            return await self._file(writer, STATIC / "index.html")
        if path.startswith("/static/") and method == "GET":
            f = (STATIC / path[len("/static/"):]).resolve()
            if STATIC not in f.parents or not f.is_file():
                return await self._json(writer, 404, {"error": "not found"})
            return await self._file(writer, f)
        if path == "/api/health" and method == "GET":
            return await self._json(writer, 200, {"ok": True, "connected": c.connected, "core_state": c.core_state, "error": c.last_error,
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
        if path == "/api/credentials":
            peer = writer.get_extra_info("peername")
            refused = prov.guard(headers, peer[0] if isinstance(peer, (tuple, list)) and peer else None, self.token)
            if refused is not None:
                return await self._json(writer, *refused)
            body = {}
            if method == "POST":
                body = await self._body(reader, writer, headers)
                if body is None:
                    return
            return await self._json(writer, *(await credentials.handle(c, method, body)))
        if path == "/api/reasoning":
            return await self._reasoning(reader, writer, headers, method)
        if path == "/api/providers" or path.startswith("/api/providers/"):
            return await self._providers(reader, writer, headers, method, path)
        if path == "/api/complete/files":   # file names: same guard as the key routes (loopback, Host, token)
            peer = writer.get_extra_info("peername")
            refused = prov.guard(headers, peer[0] if isinstance(peer, (tuple, list)) and peer else None, self.token)
            if refused is not None:
                return await self._json(writer, *refused)
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

    async def _reasoning(self, reader, writer, headers, method):
        from . import reasoning
        peer = writer.get_extra_info("peername")
        refused = prov.guard(headers, peer[0] if isinstance(peer, (tuple, list)) and peer else None, self.token)
        if refused is not None:
            return await self._json(writer, *refused)
        body = {}
        if method == "POST":
            body = await self._body(reader, writer, headers)
            if body is None:
                return
        status, data = await reasoning.handle(self.centre, method, body)
        return await self._json(writer, status, data)

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
            body = json.loads(await reader.readexactly(n)) if 0 < n <= 1024 else None
            status, data = self.redeem(body.get("code") if isinstance(body, dict) else None)
        except Exception:
            status, data = 403, {"error": "launch code refused"}
        return await self._json(writer, status, data)

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
                     + SECURITY_HEADERS.encode() + b"Connection: close\r\n\r\n: exo-command events\n")
        q = self.centre.subscribe_events()
        writer.write(_core_frame(self.centre.core_state))
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
                if isinstance(ev, CoreConnection):
                    writer.write(_core_frame(ev.state))
                    await writer.drain()
                    continue
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
        if not self._current(headers.get(prov.TOKEN_HEADER, "")):   # replaced while the body was on its way
            return await self._json(writer, 403, BAD_TOKEN)
        decision = body.get("decision")
        if decision not in DECISIONS:
            return await self._json(writer, 400, {"error": "decision must be one of {}".format(", ".join(DECISIONS))})
        try:
            await self.centre.respond_approval(rid, decision, str(body.get("note") or ""))
        except SourceError as exc:
            return await self._json(writer, 404 if exc.code == "not_found" else 502,
                                    {"error": exc.message, "code": exc.code})
        except Exception as exc:
            return await self._json(writer, 502, {"error": core_message(exc)})
        return await self._json(writer, 200, {"ok": True})

    async def _send(self, writer, status: int, body: bytes, ctype: str) -> None:
        head = ("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nCache-Control: no-store\r\n"
                "{}Connection: close\r\n\r\n").format(status, REASONS.get(status, ""), ctype, len(body), SECURITY_HEADERS)
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


def _origin(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    if ":" in shown and not shown.startswith("["):
        shown = "[{}]".format(shown)
    return "http://{}:{}".format(shown, port)


def _loopback_shown(host: str) -> bool:
    """Whether the address `exo web` shows for this bind passes the loopback Host check of /api/launch."""
    return prov._host_name(urlsplit(_origin(host, 0)).netloc) in prov.LOOPBACK_NAMES


def _core_frame(state: str) -> bytes:
    # No raw exception, protocol event ID or cursor advancement belongs here.
    return b"event: core-state\ndata: " + json.dumps({"state": state}).encode() + b"\n\n"


def _frame(ev: Dict[str, Any]) -> bytes:
    return "id: {}\ndata: {}\n\n".format(ev["seq"], json.dumps(ev, default=_json_default)).encode()


def _json_default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if hasattr(o, "__iter__"):
        return list(o)
    return str(o)
