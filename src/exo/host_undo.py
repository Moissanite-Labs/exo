"""A host's undo, passed from Exo's memory provider to Exo's context engine (#389).

Hermes's CLI and TUI report `/undo` only to their memory providers, as `on_session_switch(session, rewound=True)`.
The context engine never hears of it. exo-memory passes the notice on here and exo-context listens, so the engine
can drop the pins it took from the undone turns (D-041 amendment). Every plugin imports this module from the installed
`exo` package, so they all share one registry, as with `exo.dispatcher`.

The Exo core tells its context plugin about an undo itself, with every message left (D-041), and never calls a
memory provider's `on_session_switch`, so nothing passes through here on the core.
"""
from __future__ import annotations

import logging
import threading
import weakref
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_listeners: weakref.WeakSet[Any] = weakref.WeakSet()


def listen(listener: Any) -> None:
    """Call `listener.on_host_undo(session_id)` on each undo a host reports. The listener is held weakly: once
    nothing else holds it, it stops hearing."""
    with _lock:
        _listeners.add(listener)


def report(session_id: str) -> int:
    """Pass a host's undo of `session_id` to every listener and return how many heard it. A listener that fails
    is logged by its error type only and skipped, so the undo never fails on it."""
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener.on_host_undo(str(session_id or ''))
        except Exception as e:
            logger.warning('exo: a host undo listener failed (%s)', type(e).__name__)
    return len(listeners)
