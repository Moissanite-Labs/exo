"""The principal client's Unix transport and its event-stream reader (D-060).

``PrincipalTransport`` opens each connection itself, asks the kernel who serves it (``principal.verify_daemon``)
and only then writes the request, with the token from the launch record it just checked, on that same connection.
A failed check is an ``httpx.ConnectError`` and no byte is written: there is no fallback to an unauthenticated
principal and no check-one-socket, send-on-another.

``sse_frames`` reads complete Server-Sent Events frames under an absolute idle deadline that only a complete
frame (an event or the core's heartbeat comment) resets. Partial bytes do not keep a dead feed alive.

This module sits outside the counted core folders, as ``exo.principal`` does.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpcore
import httpx

from exo import principal as pr

MAX_FRAME = 16 * 1024 * 1024  # one SSE frame; a bound, not a format limit
_OURS = (pr.HEADER.lower().encode(), b"x-exo-client")


def _mapped(exc: Exception) -> Exception:
    """httpcore and httpx name their transport errors alike; callers catch the httpx ones."""
    kind = getattr(httpx, type(exc).__name__, None)
    return kind(str(exc)) if isinstance(kind, type) and issubclass(kind, httpx.TransportError) else exc


class _Body(httpx.AsyncByteStream):
    def __init__(self, resp: httpcore.Response, conn: httpcore.AsyncHTTP11Connection):
        self._resp, self._conn = resp, conn

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._resp.aiter_stream():
                yield chunk
        except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError) as exc:
            raise _mapped(exc) from exc

    async def aclose(self) -> None:
        try:
            await self._resp.aclose()
        finally:
            await self._conn.aclose()


class PrincipalTransport(httpx.AsyncBaseTransport):
    """One verified connection per request to the core's Unix socket; the credential goes only on ``/command``."""

    def __init__(self, socket: str, label: str):
        self.socket, self.label = socket, label
        self.instance = ""  # launch instance of the core last verified
        self._backend = httpcore.AnyIOBackend()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        timeout: dict[str, Any] = request.extensions.get("timeout", {})
        try:
            stream = await self._backend.connect_unix_socket(self.socket, timeout=timeout.get("connect"))
        except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
            raise _mapped(exc) from exc
        try:
            rec = pr.verify_daemon(stream.get_extra_info("socket"), self.socket)
        except pr.PeerRefused as exc:
            await stream.aclose()
            raise httpx.ConnectError(f"principal connection refused: {exc}", request=request) from None
        self.instance = rec.get("instance", "")
        headers = [(k, v) for k, v in request.headers.raw if k.lower() not in _OURS]
        if request.url.path == "/command":
            headers += [(pr.HEADER.encode(), rec["token"].encode()), (b"X-Exo-Client", self.label.encode())]
        conn = httpcore.AsyncHTTP11Connection(origin=httpcore.Origin(b"http", b"exod", 80), stream=stream)
        req = httpcore.Request(method=request.method, url=httpcore.URL(
            scheme=request.url.raw_scheme, host=request.url.raw_host, port=request.url.port,
            target=request.url.raw_path), headers=headers, content=request.stream, extensions=request.extensions)
        try:
            resp = await conn.handle_async_request(req)
        except Exception as exc:
            await conn.aclose()
            raise _mapped(exc) from exc
        return httpx.Response(status_code=resp.status, headers=resp.headers, stream=_Body(resp, conn),
                              extensions=resp.extensions)


async def sse_frames(lines: AsyncIterator[str], idle: float | None,
                     limit: int = MAX_FRAME) -> AsyncIterator[tuple[list[str], list[str]]]:
    """``(comments, data lines)`` per complete frame. Raises ``httpx.ReadTimeout`` when no complete frame arrives
    within ``idle`` seconds, and ``httpx.RemoteProtocolError`` for a frame over ``limit`` bytes."""
    it = lines.__aiter__()
    loop = asyncio.get_running_loop()
    while True:
        deadline = None if idle is None else loop.time() + idle
        comments: list[str] = []
        data: list[str] = []
        size = 0
        while True:
            left = None if deadline is None else deadline - loop.time()
            try:
                if left is not None and left <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(anext(it), left)
            except StopAsyncIteration:
                return
            except TimeoutError:
                raise httpx.ReadTimeout(f"no event or heartbeat from the core in {idle:g}s") from None
            size += len(line) + 1
            if size > limit:
                raise httpx.RemoteProtocolError("event frame too large")
            if line == "":
                if comments or data:
                    yield comments, data
                    break
            elif line.startswith(":"):
                comments.append(line[1:].strip())
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))


def stream_info(comments: list[str]) -> dict[str, str]:
    """``key=value`` words of the core's opening comment (``: exod instance=... heartbeat=15``)."""
    out: dict[str, str] = {}
    for c in comments:
        if c.startswith("exod"):
            for word in c.split()[1:]:
                key, eq, value = word.partition("=")
                if eq:
                    out[key] = value
    return out
