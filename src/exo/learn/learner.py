"""The learner: one per engine. Holds the store runtime, settings, hooks and the running reviews."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .runtime import Runtime, load_settings, resolve_store

logger = logging.getLogger("exo.learn")
# No principal at all is a setup step not taken yet, and exo-memory already says so in the client: logged at info,
# so it is not printed over the terminal before the client draws.
OFF_NOTICE = "learning is off: no principal store yet. Run exo init <id> to make one; learning turns on at the next start."


class Learner:
    """`Learner(engine, env=None)`. `complete` is an async `(role, prompt, system) -> text` model call; the
    default sends it through the engine's router (aux and eval roles)."""

    def __init__(self, engine: Any, env: Mapping[str, str] | None = None,
                 complete: Callable[..., Any] | None = None) -> None:
        self.engine = engine
        self.env = os.environ if env is None else env
        self.settings = load_settings(engine)
        self._complete = complete
        self._runtime: Runtime | None = None
        self._resolved = False
        self.notices: list[str] = []
        self.tasks: dict[str, asyncio.Task] = {}
        self.turns: dict[str, int] = {}      # user turns seen per session since its last review
        self.reviews: list[dict] = []        # results of finished reviews, newest last
        self.attached = False

    # -- state ---------------------------------------------------------------

    def notice(self, text: str, level: int = logging.WARNING) -> None:
        """Say something once (the log, and `self.notices` for a client to show)."""
        if text not in self.notices:
            self.notices.append(text)
            logger.log(level, text)

    @property
    def runtime(self) -> Runtime | None:
        if not self._resolved:
            self._resolved = True
            if not self.settings.get("enabled", True):
                self.notice("learning is off: learn.enabled is false.")
                return None
            root = resolve_store(self.engine, self.env)
            if root is None:
                self.notice(OFF_NOTICE, logging.INFO)
                return None
            if not (Path(root) / "nodes").is_dir():
                self.notice(f"learning is off: no principal store at {root} (run exo init).")
                return None
            home = Path(getattr(self.engine, "home", None) or self.env.get("EXO_HOME") or Path.home() / ".exo")
            self._runtime = Runtime(root, home)
        return self._runtime

    @property
    def on(self) -> bool:
        return self.runtime is not None

    def close(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        if self._runtime is not None:
            self._runtime.close()
            self._runtime = None
        self._resolved = False

    # -- model calls ---------------------------------------------------------

    async def complete(self, role: str, prompt: str, system: str | None = None) -> str:
        """One model call on `role` ("aux" for the reviewer, "eval" for the judge)."""
        if self._complete is not None:
            out = self._complete(role, prompt, system)
            return await out if asyncio.iscoroutine(out) else out
        from exo.core.models.router import aux
        return await aux(self.engine.router, prompt, system=system, role=role)

    def complete_sync(self, role: str, prompt: str, system: str | None = None) -> str:
        """Blocking model call for graders and responders running in a worker thread."""
        if self._complete is not None:
            out = self._complete(role, prompt, system)
            if asyncio.iscoroutine(out):
                return asyncio.run(out)
            return out
        from exo.core.models.router import aux_sync
        return aux_sync(self.engine.router, prompt, system=system, role=role)

    # -- transcript ----------------------------------------------------------

    def messages(self, session: str) -> list[Any]:
        store = getattr(self.engine, "store", None)
        try:
            return list(store.messages(session)) if store is not None else []
        except Exception:
            return []

    def last_messages(self, session: str, limit: int) -> list[Any]:
        """The newest ``limit`` messages, in order. The session store reads only those rows (#254)."""
        tail = getattr(getattr(self.engine, "store", None), "last_messages", None)
        try:
            return list(tail(session, limit)) if tail is not None else self.messages(session)[-limit:]
        except Exception:
            return []

    def session_info(self, session: str) -> Any:
        store = getattr(self.engine, "store", None)
        try:
            return store.get_session(session) if store is not None else None
        except Exception:
            return None

    def private(self, session: str | None) -> bool:
        """A private thread is not remembered (D-028): learning neither reads it nor records a signal from it, nor
        from a session under it (its delegate child, which holds a brief built from it, at any depth)."""
        store = getattr(self.engine, "store", None)
        if not session or store is None:
            return False
        try:
            return bool(store.private(session))
        except Exception:
            return True  # it cannot tell: keep out, as if private

    # -- triggers and hooks --------------------------------------------------

    def trigger(self, session: str | None, reason: str = "signal") -> bool:
        """Start a review in the background. False when there is no running event loop, learning is off, or the
        session is unattended. A review already running for the session is kept."""
        from .review import is_unattended, run_review
        if not self.on or not self.settings["fast_loop"].get("enabled", True):
            return False
        if session and (is_unattended(self, session) or self.private(session)):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        key = session or ""
        if key in self.tasks and not self.tasks[key].done():
            return True
        task = loop.create_task(run_review(self, session, reason))
        self.tasks[key] = task
        task.add_done_callback(lambda t, k=key: self.tasks.pop(k, None) if self.tasks.get(k) is t else None)
        return True

    def on_turn_start(self, **kw: Any) -> None:
        """A live turn cancels any review in flight, so learning never competes with the principal."""
        for t in list(self.tasks.values()):
            if not t.done():
                t.cancel()
        sid = kw.get("session_id") or ""
        if sid:
            self.turns[sid] = self.turns.get(sid, 0) + 1

    def on_turn_end(self, **kw: Any) -> None:
        """After a turn: capture corrections from the chat, then start a review if a signal or the turn count
        says so. Never raises."""
        from .review import is_unattended, needs_review, new_signals
        from .signals import record_chat_signals
        sid = kw.get("session_id") or None
        if not sid or not self.on or is_unattended(self, sid) or self.private(sid):
            return
        try:
            record_chat_signals(self, sid, kw.get("messages"))
            why = needs_review(new_signals(self), self.turns.get(sid, 0), int(self.settings["fast_loop"]["every_turns"]))
        except Exception as exc:
            self.notice(f"learning: capture failed ({type(exc).__name__}: {exc})")
            return
        if why and self.trigger(sid, why):
            self.turns[sid] = 0

    async def nightly(self, **kw: Any) -> dict:
        """The slow loop: promote what the gate allows, climb, check for regressions. See `learn.climb.nightly`."""
        from .climb import nightly
        return await nightly(self, **kw)

    def attach(self) -> "Learner":
        """Register the hooks on the engine's plugin hook registry. Safe to call twice."""
        if self.attached:
            return self
        hooks = getattr(getattr(self.engine, "plugins", None), "hooks", None)
        if hooks is None:
            self.notice("learning: the engine has no hook registry; the fast loop will not run on its own")
            return self
        hooks.register("on_turn_start", lambda **kw: self.on_turn_start(**kw), plugin="exo-learn")
        hooks.register("on_turn_end", lambda **kw: self.on_turn_end(**kw), plugin="exo-learn")
        self.attached = True
        self.on  # noqa: B018  resolves the store now, so "learning is off" is said once at start
        return self


def get_learner(engine: Any, env: Mapping[str, str] | None = None) -> Learner:
    """The engine's learner, made on first use (handlers work without `attach`)."""
    lr = getattr(engine, "_learn", None)
    if not isinstance(lr, Learner):
        lr = Learner(engine, env)
        try:
            engine._learn = lr
        except Exception:
            pass
    return lr

