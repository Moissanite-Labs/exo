"""How the TUI reaches exod: the HTTP client, or an in-process engine with the same surface."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from exo.protocol import schema as s


class LocalClient:
    """Same methods as exo.protocol.client.Client (send, call, events) over a Dispatcher in this process."""

    def __init__(self, engine: Any):
        from exo.server.app import Dispatcher

        self.engine = engine
        self.dispatcher = Dispatcher(engine)
        self.last_seq = 0

    async def send(self, body: Any) -> s.Response:
        return await self.dispatcher.run(s.Command.of(body))

    async def call(self, body: Any) -> dict[str, Any]:
        from exo.protocol.client import ClientError

        resp = await self.send(body)
        if not resp.ok:
            raise ClientError(resp.error)
        return resp.result

    async def events(self, since: int = 0, session: str | None = None,
                     reconnect: bool = False, backoff: float = 0.5) -> AsyncIterator[s.Event]:
        sub = self.dispatcher.hub.subscribe(since, session)
        try:
            async for ev in sub:
                self.last_seq = ev.seq
                yield ev
        finally:
            sub.close()

    async def aclose(self) -> None:
        self.dispatcher.hub.close()
        close = getattr(self.engine, "close", None)
        if close:
            await close()


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
    if path.exists() or socket:
        from exo.protocol.client import Client

        return Client(socket=str(path))
    from exo.server.app import load_engine

    if seat:
        os.environ["EXO_SEAT"] = seat
    return LocalClient(load_engine("exo.core.engine:create"))
