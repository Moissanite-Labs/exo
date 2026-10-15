"""Where protocol data comes from: a running ``exod`` (socket or TCP) or an in-process dispatcher.

Both give the command centre the same two things: ``call(body)`` runs one command and returns its
result dict, ``stream(since)`` yields events (as plain dicts) with ``seq > since``. Importing this
module needs only the Exo core; ``httpx`` is pulled in only when a ``ClientSource`` is built.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Protocol

from exo.protocol import schema as s


def core_message(exc: BaseException) -> str:
    """A sentence for the page; never a raw ConnectError."""
    name = type(exc).__name__
    if isinstance(exc, (ConnectionError, FileNotFoundError)) or name in ("ConnectError", "ReadError", "RemoteProtocolError"):
        return ("Exo core is not reachable. `exo web` starts it for you; if this stays, run `exo serve` in a "
                "terminal or check $EXO_HOME/logs/exod.log.")
    if isinstance(exc, RuntimeError):
        return str(exc)
    return "{}: {}".format(name, exc)


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__("{}: {}".format(code, message))
        self.code, self.message = code, message


class Source(Protocol):
    async def call(self, body: Any) -> Dict[str, Any]: ...

    def stream(self, since: int = 0) -> AsyncIterator[Dict[str, Any]]: ...

    async def aclose(self) -> None: ...


class ClientSource:
    """Over HTTP + SSE, through ``exo.protocol.client.Client``."""

    executor_required = True  # remote completion has no authority to execute workspace Git

    def __init__(self, socket: Optional[str] = None, url: Optional[str] = None, token: Optional[str] = None):
        from exo.protocol.client import Client
        self.socket = socket
        # D-039: the web client answers approvals as the principal; its browsers prove themselves with the page token
        self.client = Client(socket=socket, url=url, token=token, principal="web")

    async def call(self, body: Any) -> Dict[str, Any]:
        resp = await self.client.send(body)
        if not resp.ok:
            err = resp.error
            raise SourceError(err.code if err else "unknown", err.message if err else "")
        return resp.result

    async def stream(self, since: int = 0) -> AsyncIterator[Dict[str, Any]]:
        async for ev in self.client.events(since=since, reconnect=False):
            yield ev.to_dict()

    async def aclose(self) -> None:
        await self.client.aclose()


class DispatcherSource:
    """In process: a ``exo.server.app.Dispatcher`` in the same event loop (tests, embedded use)."""

    def __init__(self, dispatcher: Any):
        self.dispatcher = dispatcher

    @property
    def executor_required(self) -> bool:
        return bool(getattr(self.dispatcher.engine, "executor_required", True))

    async def call(self, body: Any) -> Dict[str, Any]:
        resp = await self.dispatcher.run(s.Command.of(body), "principal:web")
        if not resp.ok:
            err = resp.error
            raise SourceError(err.code if err else "unknown", err.message if err else "")
        return resp.result

    async def stream(self, since: int = 0) -> AsyncIterator[Dict[str, Any]]:
        sub = self.dispatcher.hub.subscribe(since)
        try:
            async for ev in sub:
                yield ev.to_dict()
        finally:
            sub.close()

    async def aclose(self) -> None:
        return None
