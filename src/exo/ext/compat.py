"""Plugin helpers built on the public core API only (wave 2).

Each item here is something a plugin needs that the core does not offer yet. When the core
grows the real hook, the item moves there and this module shrinks. Candidates, in order:

1. ``run_sync`` / ``aux_summarize``: a sync-callable aux call for plugin background threads.
2. ``is_compaction_summary_message``: the compaction marker test (the core only has the
   ``VIEW_HEADER`` constant, no predicate).
3. ``ensure_root_command``: one ``/exo`` command per plugin manager.
4. ``get_router``: the aux router for a plugin that was not handed one at register time.
"""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Coroutine, Mapping

from exo.core.context import HISTORICAL_TASK_HEADING, VIEW_HEADER
from exo.core.types import ContentPart, Message

# ---------------------------------------------------------------- compaction marker

_MARKERS = (VIEW_HEADER, HISTORICAL_TASK_HEADING)


def is_compaction_summary_message(msg: Mapping[str, Any] | Message) -> bool:
    """True for a message that carries the core's compaction view or a lifted summary."""
    text = msg.text() if isinstance(msg, Message) else _text(msg.get("content"))
    head = text.lstrip()
    return any(m in text for m in _MARKERS) or head.startswith(("[CONTEXT COMPACTION", "[Context summary"))


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return "" if content is None else str(content)


# ---------------------------------------------------------------- sync bridge

class _BgLoop:
    """One daemon thread with its own event loop, for sync callers (plugin threads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                threading.Thread(target=run, name="exo-ext-loop", daemon=True).start()
                ready.wait(5)
                self._loop = loop
            return self._loop


_BG = _BgLoop()


def run_sync(coro: Coroutine[Any, Any, Any], timeout: float | None = 120.0) -> Any:
    """Run ``coro`` on the shared background loop and wait. Never call from that loop."""
    fut: Future = asyncio.run_coroutine_threadsafe(coro, _BG.loop())
    return fut.result(timeout)


# ---------------------------------------------------------------- aux router

_router: Any = None
_router_lock = threading.Lock()


def set_router(router: Any) -> None:
    """Use this Router for aux calls (tests, or a host that already built one)."""
    global _router
    with _router_lock:
        _router = router


def get_router(home: str | os.PathLike | None = None) -> Any:
    """The shared aux Router: the one set with ``set_router``, else built from config.json."""
    global _router
    with _router_lock:
        if _router is None:
            from exo.config import Config
            from exo.core.models.fake import RuleFake
            from exo.core.models.router import Router

            models = Config.load(home).models()
            _router = Router.from_config(models, instances={"fake": RuleFake(models.get("fake"))})
        return _router


def aux_summarize(messages: list[dict[str, Any]], *, router: Any = None, role: str = "aux",
                  timeout: float | None = 120.0, **opts: Any) -> str:
    """Blocking aux call over OpenAI-style dict messages; returns the text."""
    from exo.core.models.router import aux

    msgs = [Message(role=m["role"], content=[ContentPart.of_text(_text(m.get("content")))])
            for m in messages if m.get("role") in ("system", "user", "assistant")]
    opts.setdefault("temperature", 0)
    opts.setdefault("max_tokens", 1024)
    return run_sync(aux(router or get_router(), msgs, role=role, **opts), timeout)


# ---------------------------------------------------------------- /exo root command

def ensure_root_command(ctx: Any) -> bool:
    """Register ``/exo`` once per plugin manager (``register_root`` is once per process, which
    breaks when a second manager loads the plugins). Returns True when this call registered it."""
    from exo import dispatcher

    commands = getattr(getattr(ctx, "manager", None), "commands", None)
    if commands is not None:
        if dispatcher.ROOT in commands:
            return False
        return dispatcher.register_root(ctx, force=True)
    return dispatcher.register_root(ctx)
