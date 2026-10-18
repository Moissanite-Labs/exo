"""Python client for exod over HTTP + SSE (Unix socket or TCP)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

import httpx

from exo.peerauth import FrameTooLarge, PrincipalTransport, sse_frames, stream_info
from exo.protocol import schema as s

log = logging.getLogger(__name__)


class ClientError(Exception):
    def __init__(self, error: s.ErrorInfo | None):
        self.code = error.code if error else "unknown"
        self.message = error.message if error else ""
        super().__init__(f"{self.code}: {self.message}")


class Client:
    """``Client(socket=path)`` for the local daemon, ``Client(url=..., token=...)`` for TCP.

    ``principal="tui"`` (or any label) marks an interactive client of the principal (D-039). Over the Unix socket
    each connection is checked with the kernel before the core's credential goes on it (D-060, ``exo.peerauth``);
    the record is read fresh per connection, so a restarted core is re-joined. A failed check is a ConnectError.
    Without a label the core treats the connection as ``local``, which cannot answer approvals. ``idle`` ends an
    event stream that brings no event or heartbeat for that long."""

    def __init__(self, socket: str | None = None, url: str | None = None, token: str | None = None,
                 timeout: float = 30.0, principal: str | None = None, idle: float | None = 45.0):
        if not socket and not url:
            raise ValueError("need socket or url")
        transport: httpx.AsyncBaseTransport | None = None
        if socket and principal:
            transport = PrincipalTransport(str(socket), principal)
        elif socket:
            transport = httpx.AsyncHTTPTransport(uds=str(socket))
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(base_url=url or "http://exod", transport=transport,
                                       headers=headers, timeout=timeout)
        self.idle, self.instance, self.last_seq = idle, "", 0

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, body: Any) -> s.Response:
        """Send one command body (e.g. ``SessionCreate()``); return the Response."""
        return await self.send_raw(s.Command.of(body).to_json())

    async def send_raw(self, text: str) -> s.Response:
        r = await self._http.post("/command", content=text, headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            return s.Response(id="", ok=False, error=s.ErrorInfo(code=f"http_{r.status_code}", message=r.text))
        return s.Response.from_json(r.content)

    async def call(self, body: Any) -> dict[str, Any]:
        """Like ``send`` but returns ``result`` and raises ClientError on failure."""
        resp = await self.send(body)
        if not resp.ok:
            raise ClientError(resp.error)
        return resp.result

    async def events(self, since: int = 0, session: str | None = None,
                     reconnect: bool = False, backoff: float = 0.5) -> AsyncIterator[s.Event]:
        """Stream events with seq > since. With ``reconnect`` the stream resumes
        from the last seen seq after a drop, so nothing is missed or repeated. An event too large to read is
        skipped (``last_seq`` moves past it) and the stream reads on after it, with or without ``reconnect``."""
        self.last_seq = since
        while True:
            try:
                async for ev in self._stream(self.last_seq, session):
                    self.last_seq = ev.seq
                    yield ev
            except FrameTooLarge as exc:  # resuming at the same seq would replay the same frame forever: skip it
                if exc.seq is None:
                    raise
                if exc.seq > self.last_seq:  # read on: a caller with its own cursor would ask for it again
                    self.last_seq = exc.seq
                    continue
            except (httpx.TransportError, httpx.StreamError):
                pass
            except ClientError:  # a refusal or a 5xx while the core restarts: keep trying, never end the feed (D-045)
                if not reconnect:
                    raise
            if not reconnect:
                return
            await asyncio.sleep(backoff)

    async def _stream(self, since: int, session: str | None) -> AsyncIterator[s.Event]:
        params: dict[str, Any] = {"since": since}
        if session:
            params["session"] = session
        timeout = httpx.Timeout(self._http.timeout.connect, read=None)
        async with self._http.stream("GET", "/events", params=params, timeout=timeout) as r:
            if r.status_code != 200:
                raise ClientError(s.ErrorInfo(code=f"http_{r.status_code}", message=(await r.aread()).decode()))
            async for comments, data in sse_frames(r.aiter_lines(), self.idle):
                if data:
                    try:
                        ev = s.Event.from_json("\n".join(data), strict=False)
                    except s.ProtocolError as exc:  # skip one event it cannot read, never end the feed (D-045)
                        log.warning("skipped an event: %s", exc)
                    else:
                        yield ev
                elif (got := stream_info(comments).get("instance")) is not None:
                    self.instance = got  # a change means the core restarted: take a fresh approvals snapshot
