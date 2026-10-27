"""A host's undo, passed from Exo's memory provider to Exo's context engine (#389).

Hermes's CLI and TUI report `/undo` only to their memory providers, as `on_session_switch(session, rewound=True)`.
The context engine never hears of it. exo-memory passes the notice on here and exo-context listens, so the engine
can drop the pins it took from the undone turns (D-041 amendment). Every plugin imports this module from the installed
`exo` package, so they all share one registry, as with `exo.dispatcher`.

The Exo core tells its context plugin about an undo itself, with every message left (D-041), and never calls a
memory provider's `on_session_switch`, so nothing passes through here on the core.

Each listener also counts the turns it sees here (`turn`), so only the one that saw a session's latest turn acts on
an undo. Hermes can build a new agent on the same session (after `/reasoning` or `/fast`, say), and the old one
may live on.

A pending undo waits for its own session's next turn, not for one engine (#580). The listener that hears a notice
records it with `pend`, holding the window the notice left, and the listener that runs the session's next turn
takes it with `take` and finishes the drop, whichever agent Hermes built that listener for. A second notice before
that turn keeps the first record's window, and a compression move carries the record into the child session with
`move`. The record never outlives the session's next turn, and it can drop only the pins the window it holds
showed, so a stale record never reaches a later undo's pins.
"""
from __future__ import annotations

import logging
import threading
import weakref
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_listeners: weakref.WeakSet[Any] = weakref.WeakSet()
_turns: dict[str, int] = {}  # session -> number of its last turn any listener saw
# session -> the undo a host reported and no turn has settled yet: the window the notice left, without the undone
# turn, and the summary blocks the compactions since folded over its first row
_pending: dict[str, tuple[list[dict[str, Any]], set[str]]] = {}


def turn(session_id: str) -> int:
    """A listener saw a turn of `session_id`: count it and return its number. An undo of that session concerns the
    listener whose last turn has the number `latest` gives."""
    with _lock:
        n = _turns[str(session_id)] = _turns.get(str(session_id), 0) + 1
    return n


def latest(session_id: str) -> int:
    """The number of the last turn of `session_id` any listener saw, or 0."""
    with _lock:
        return _turns.get(str(session_id), 0)


def listen(listener: Any) -> None:
    """Call `listener.on_host_undo(session_id)` on each undo a host reports. The listener is held weakly: once
    nothing else holds it, it stops hearing."""
    with _lock:
        _listeners.add(listener)


def report(session_id: str) -> int:
    """Pass a host's undo of `session_id` to every listener and return how many heard it. A listener that fails
    is logged by its error type only and skipped, so the undo never fails on it. A host with no agent left, in a
    `/reasoning` gap say, reports here itself: the listeners still registered for the session hear it (#580)."""
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener.on_host_undo(str(session_id or ''))
        except Exception as e:
            logger.warning('exo: a host undo listener failed (%s)', type(e).__name__)
    return len(listeners)


def pend(session_id: str, before: list[dict[str, Any]], anchors: Any = ()) -> None:
    """Record a host's undo of `session_id` until its next turn, held by no single engine (#580). `before` is the
    window the notice left, without the undone turn; `anchors` are the digests of the summary blocks written since
    that folded the window's first row or its summary part. A record already there stays, so a second undo before
    the next turn keeps the window the first one left."""
    with _lock:
        _pending.setdefault(str(session_id or ''), (list(before), set(anchors)))


def pending(session_id: str) -> tuple[list[dict[str, Any]], set[str]] | None:
    """The undo recorded for `session_id` and not settled yet, or None."""
    with _lock:
        return _pending.get(str(session_id or ''))


def take(session_id: str) -> tuple[list[dict[str, Any]], set[str]] | None:
    """The session's next turn consumes its record, whatever the turn decides, a transcript cut short included,
    so a pending undo never outlives it and never acts twice."""
    with _lock:
        return _pending.pop(str(session_id or ''), None)


def move(old_session_id: str, new_session_id: str) -> None:
    """A compression move carries a pending undo from the parent session into the child, the way its pins go."""
    with _lock:
        record = _pending.pop(str(old_session_id or ''), None)
        if record is not None and str(new_session_id or ''):
            _pending.setdefault(str(new_session_id), record)
