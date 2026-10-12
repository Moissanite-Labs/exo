"""How the TUI reaches exod: the HTTP client, or an in-process engine with the same surface.

One agent has one writer (D-028). With no exod running, the first window runs the core in process, takes the
agent's writer lock and serves ``$EXO_HOME/run/agent-<seat>.sock``; later windows of the same seat join it there.
If the window holding the core closes, a joined window reconnects to whoever took over, or takes over itself.
"""

from __future__ import annotations

import asyncio
import os
import socket as _socket
import time
from pathlib import Path
from typing import Any, AsyncIterator

from exo.protocol import schema as s


class LocalClient:
    """Same methods as exo.protocol.client.Client (send, call, events) over a Dispatcher in this process."""

    def __init__(self, engine: Any, serve: str | os.PathLike | None = None):
        from exo.server.app import Dispatcher

        self.engine = engine
        self.dispatcher = Dispatcher(engine)
        self.last_seq = 0
        self.serve_path = Path(serve) if serve else None  # other windows of this agent join here
        self._server: Any = None

    async def _serve(self) -> None:
        if self.serve_path is None or self._server is not None:
            return
        from exo.server.app import HttpServer

        self._server = HttpServer(self.dispatcher)
        self.serve_path.parent.mkdir(parents=True, exist_ok=True)
        self.serve_path.unlink(missing_ok=True)  # this process holds the writer lock, so any old socket is stale
        try:
            await self._server.start_unix(self.serve_path)
        except Exception:  # joining is a convenience; this window works without it
            self._server = None

    async def send(self, body: Any) -> s.Response:
        await self._serve()
        return await self.dispatcher.run(s.Command.of(body))

    async def call(self, body: Any) -> dict[str, Any]:
        from exo.protocol.client import ClientError

        resp = await self.send(body)
        if not resp.ok:
            raise ClientError(resp.error)
        return resp.result

    async def events(self, since: int = 0, session: str | None = None,
                     reconnect: bool = False, backoff: float = 0.5) -> AsyncIterator[s.Event]:
        await self._serve()
        sub = self.dispatcher.hub.subscribe(since, session)
        try:
            async for ev in sub:
                self.last_seq = ev.seq
                yield ev
        finally:
            sub.close()

    async def aclose(self) -> None:
        if self._server is not None:
            await self._server.close()
            self._server = None
            if self.serve_path is not None:
                self.serve_path.unlink(missing_ok=True)
        self.dispatcher.hub.close()
        close = getattr(self.engine, "close", None)
        if close:
            await close()


def _alive(path: Path) -> bool:
    if not path.exists():
        return False
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _open(home: Path, agent: str, wait: float = 5.0) -> Any:
    """A protocol Client on the window that runs this agent, or a LocalClient that runs it here."""
    from exo.core.agentlog import WriterBusy, socket_path
    from exo.protocol.client import Client
    from exo.server.app import load_engine

    path = socket_path(home, agent)
    exod = home / "run" / "exod.sock"
    for p in (exod, path):
        if _alive(p):
            return Client(socket=str(p))
    try:
        return LocalClient(load_engine("exo.core.engine:create"), serve=path)
    except WriterBusy:  # another window is starting it: wait for its socket
        end = time.monotonic() + wait
        while time.monotonic() < end:
            for p in (exod, path):
                if _alive(p):
                    return Client(socket=str(p))
            time.sleep(0.05)
        raise


class SharedClient:
    """A window that joined another window's core. Same surface as Client; after the core's window closes it
    reconnects (or starts the core here) and resumes the event stream where it stopped."""

    def __init__(self, home: Path, agent: str, inner: Any):
        self.home, self.agent, self.inner = home, agent, inner
        self.last_seq = 0

    async def _failover(self) -> None:
        try:
            await self.inner.aclose()
        except Exception:
            pass
        self.inner = _open(self.home, self.agent)

    async def send(self, body: Any) -> s.Response:
        import httpx

        try:
            return await self.inner.send(body)
        except (httpx.TransportError, OSError):
            await self._failover()
            return await self.inner.send(body)

    async def call(self, body: Any) -> dict[str, Any]:
        from exo.protocol.client import ClientError

        resp = await self.send(body)
        if not resp.ok:
            raise ClientError(resp.error)
        return resp.result

    async def events(self, since: int = 0, session: str | None = None,
                     reconnect: bool = False, backoff: float = 0.5) -> AsyncIterator[s.Event]:
        import httpx

        self.last_seq = since
        while True:
            try:
                async for ev in self.inner.events(since=self.last_seq, session=session):
                    self.last_seq = ev.seq
                    yield ev
            except (httpx.TransportError, httpx.StreamError, OSError):
                pass
            if not reconnect:
                return
            await asyncio.sleep(backoff)
            try:
                await self._failover()
            except Exception:  # nobody can run it yet; try again after the backoff
                continue

    async def aclose(self) -> None:
        await self.inner.aclose()


def default_socket() -> Path:
    home = Path(os.environ.get("EXO_HOME") or Path.home() / ".exo").expanduser()
    return home / "run" / "exod.sock"


def connect(socket: str | None = None, url: str | None = None, token_file: str | None = None,
            seat: str | None = None) -> Any:
    """Client for a running exod; with no socket and no server running, an in-process core."""
    token = os.environ.get("EXOD_TOKEN")
    if url:
        from exo.protocol.client import Client

        if not token:
            p = Path(token_file).expanduser() if token_file else default_socket().with_name("exod.token")
            token = p.read_text().strip() if p.exists() else None
        return Client(url=url, token=token)
    path = Path(socket).expanduser() if socket else default_socket()
    if socket or _alive(path):  # a stale exod.sock left by a crash is not a server: fall through to in-process
        from exo.protocol.client import Client

        return Client(socket=str(path))
    if seat:
        os.environ["EXO_SEAT"] = seat
    home = Path(os.environ.get("EXO_HOME") or Path.home() / ".exo").expanduser()
    agent = seat or os.environ.get("EXO_SEAT") or "default"
    client = _open(home, agent)
    return client if isinstance(client, LocalClient) else SharedClient(home, agent, client)
