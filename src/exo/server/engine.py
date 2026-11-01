"""The Engine interface exod calls, and the Hub that orders and fans out events.

The server owns transport, auth, ``hello`` and ``events.subscribe``. Every other command goes to an Engine. The engine
publishes events through the Hub, which gives each one the next ``seq``, writes it to the event log first, then hands it
to live subscribers. One Hub per server, so ``seq`` is strictly increasing across the whole stream and events of one
session keep their order."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Iterable, Iterator, Protocol, runtime_checkable

from exo.protocol.schema import Command, Event, new_id


class CommandFailed(Exception):
    """Raise from an engine to fail one command with a typed error."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable


@runtime_checkable
class Engine(Protocol):
    """What the server needs from the core loop.

    ``handle`` runs one command and returns its ``Response.result``. Long work (a turn) should start in the background,
    return quickly (for example ``{"turn_id": ...}``) and report progress with ``hub.publish``. Raise ``CommandFailed``
    for expected failures; use code ``unsupported`` for commands the engine does not implement yet."""

    async def handle(self, command: Command, hub: Hub) -> dict[str, Any]: ...


@runtime_checkable
class EventLog(Protocol):
    """Durable event storage (sessions.sqlite in the real core).

    ``append`` must persist before returning: events are written before they
    are sent, so replay and the ledger mirror read the same record.
    """

    def append(self, event: Event) -> None: ...

    def read(self, since: int, session: str | None = None) -> Iterable[Event]: ...

    def last_seq(self) -> int: ...


def _matches(event: Event, session: str | None) -> bool:
    # A session filter still passes global events (session None): notices and
    # errors that are not tied to a session reach every client.
    return session is None or event.session is None or event.session == session


class MemoryLog:
    """In-memory EventLog for tests and the echo engine."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, event: Event) -> None:
        self._events.append(event)

    def read(self, since: int, session: str | None = None) -> Iterable[Event]:
        # seq is dense from 1 in a MemoryLog, so slice instead of scanning.
        return [e for e in self._events[max(since, 0):] if e.seq > since and _matches(e, session)]

    def last_seq(self) -> int:
        return self._events[-1].seq if self._events else 0


class Closed(Exception):
    """The stream ended: the hub shut down (``Hub.closed``), or the subscriber fell ``MAX_LAG`` events behind and
    resumes with since."""


# A subscriber this many unread events behind has stalled: its stream ends after the last event it read and it
# resumes with since, so a stopped reader cannot grow the core's memory. A reader that keeps up holds what one loop
# turn publishes: a delta per provider chunk (core/loop.py) and one tool.output per running tool (core/engine.py
# run_tool joins a tool thread's chunks), so a handful.
MAX_LAG = 4096


class Subscription:
    """Replay of ``seq > since`` followed by live events, with no gap or repeat.

    The queue is registered before the backlog is read, so an event published
    in between is seen once: duplicates are dropped by ``seq``. The backlog comes
    from ``Hub.replay``: the SQLite log reads it a page at a time, up to the newest
    event at subscribe, so a subscriber far behind holds one page (#254).
    """

    def __init__(self, hub: Hub, since: int, session: str | None):
        self.session = session
        self.last = since
        self._hub = hub
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        hub._subs.add(self._queue)
        try:
            self._backlog = hub.replay(since, session)
        except BaseException:  # a since the log cannot read leaves no queue behind to fill
            hub._subs.discard(self._queue)
            raise

    async def next(self, timeout: float | None = None) -> Event | None:
        """Next event, or None after ``timeout`` seconds idle. Raises Closed."""
        ev = next(self._backlog, None)
        if ev is not None:
            self.last = ev.seq
            return ev
        loop = asyncio.get_running_loop()
        end = None if timeout is None else loop.time() + timeout  # other sessions' events do not reset it
        while True:
            try:
                ev = await asyncio.wait_for(self._queue.get(), None if end is None else max(0.0, end - loop.time()))
            except TimeoutError:
                return None
            if ev is None:
                raise Closed()
            if ev.seq <= self.last or not _matches(ev, self.session):
                continue
            self.last = ev.seq
            return ev

    def close(self) -> None:
        self._hub._subs.discard(self._queue)

    def __aiter__(self) -> AsyncIterator[Event]:
        return self

    async def __anext__(self) -> Event:
        try:
            ev = await self.next()
        except Closed:
            self.close()
            raise StopAsyncIteration from None
        assert ev is not None
        return ev


class Hub:
    """Orders events, logs them, and fans them out. Use from one event loop."""

    def __init__(self, log: EventLog | None = None):
        self.log: EventLog = log if log is not None else MemoryLog()
        self.seq = self.log.last_seq()
        self.instance = new_id("i")  # this launch; a client that sees it change takes a fresh snapshot (D-060)
        self._subs: set[asyncio.Queue[Event | None]] = set()
        self.closed = False  # set by close(); a stream that ends while False was ended for lag: resume with since

    def publish(self, body: Any, session: str | None = None) -> Event:
        ev = Event.of(body, self.seq + 1, session)
        self.log.append(ev)
        self.seq = ev.seq
        for q in list(self._subs):
            if q.qsize() < MAX_LAG:
                q.put_nowait(ev)
            else:  # stalled: drop what it has not read and end its stream; it resumes with since, so no gap
                self._subs.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)
        return ev

    def since(self, seq: int, session: str | None = None) -> list[Event]:
        return list(self.replay(seq, session))

    def replay(self, seq: int, session: str | None = None) -> Iterator[Event]:
        """Events after ``seq``, read from the log as they are taken (#254). ``since`` and every
        ``Subscription`` replay through here, so a rule on what the hub replays goes here."""
        return iter(self.log.read(seq, session))

    def head(self) -> int:
        """The log's highest seq, the cursor a joining client starts from. Seats of one home share the log, so
        another seat's core may have written past this hub's last publish; a client never replays those."""
        self.seq = max(self.seq, self.log.last_seq())
        return self.seq

    def subscribe(self, since: int = 0, session: str | None = None) -> Subscription:
        return Subscription(self, since, session)

    def close(self) -> None:
        self.closed = True
        for q in self._subs:
            q.put_nowait(None)
        self._subs.clear()
