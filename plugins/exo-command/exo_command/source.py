"""Where protocol data comes from: a running ``exod`` (socket or TCP) or an in-process dispatcher.

Both give the command centre the same two things: ``call(body)`` runs one command and returns its
result dict, ``stream(since)`` yields events (as plain dicts) with ``seq > since``. Importing this
module needs only the Exo core; ``httpx`` is pulled in only when a ``ClientSource`` is built.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional, Protocol, Tuple

from exo.protocol import schema as s


UNREACHABLE = ("Exo core is not reachable. `exo web` starts it for you; if this stays, run `exo serve` in a "
               "terminal or check $EXO_HOME/logs/exod.log.")
"""The one sentence for a core that never answered. No address, no errno, no exception name."""

REFUSAL_CODES = ("forbidden", "unauthorized")
"""Error codes a core answers with when the caller may not do this. The request arrived and was refused; asking
again changes nothing, so this is not a transport failure and no amount of retrying fixes it (D-091)."""

TOKEN_REFUSED = ("http_401", "http_403")
"""What a core on TCP answers when the bearer token this web process sent is wrong or missing (`exo web --url URL
--token-file FILE` with a stale token). The core is up and refused the request, so no retry fixes it (#856)."""

TRANSPORT_NAMES = ("ConnectError", "ConnectTimeout", "ReadError", "ReadTimeout", "RemoteProtocolError", "WriteError",
                   "WriteTimeout")
"""Exception class names that mean the core never answered. Named, not imported: httpx is optional here."""


def core_failure(exc: BaseException) -> Tuple[str, str]:
    """``(kind, sentence)`` for a core call that failed. ``kind`` is ``"authority"`` when the core answered and
    refused the caller, ``"transport"`` when it never answered, ``"other"`` when the core reported a failure of
    its own. The caller shows the sentence; only ``kind`` decides whether a retry can help (D-091)."""
    name, code = type(exc).__name__, getattr(exc, "code", "")
    if code in REFUSAL_CODES:
        return "authority", ("The core refused this request: it needs principal authority. Open the page from the "
                             "link `exo web` printed, or make this seat the principal with `exo init`.")
    if code in TOKEN_REFUSED:
        return "authority", ("The core refused this web client's bearer token. Start `exo web --url URL --token-file "
                             "FILE` with the core's token file (by default `$EXO_HOME/run/exod.token`).")
    if name == "PrincipalRefused":  # D-060: the core answers but failed the peer check; often a core from before it
        return "authority", ("{}. If an older Exo core is still running, restart it: run `exo stop` (or close the "
                             "Exo window that runs it), then reopen this page.".format(str(exc).rstrip(".")))
    if isinstance(exc, (ConnectionError, FileNotFoundError)) or name in TRANSPORT_NAMES:
        return "transport", UNREACHABLE
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():  # #856: the core's own words, without a class name or code
        return "other", message
    if isinstance(exc, RuntimeError):
        return "other", str(exc)
    return "other", "{}: {}".format(name, exc)


def core_message(exc: BaseException) -> str:
    """A sentence for the page; never a raw ConnectError."""
    return core_failure(exc)[1]


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
