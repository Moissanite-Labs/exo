"""Application composition for the standalone core engine.

Principal/config selection, required plugins and product integrations are wired
here. The core engine keeps turn orchestration and its public lifecycle.
"""
from __future__ import annotations

from functools import update_wrapper
import logging
from typing import Any

from exo.core.engine import CoreRuntime
from exo.core.plugins import PluginManager
from exo.plugin_context import ContextAdapter


class CoreEngine(CoreRuntime):
    """Standalone application runtime with the optional plugin context bridge.

    The public constructor retains CoreRuntime's signature and type hints. Supplying
    context_factory replaces only this bridge; the runtime still owns its tree.
    """

    harness_plan: Any = None  # fixed composition selected at startup
    workspace_instructions: Any = None  # optional composition-layer prompt provider

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        plugins = kwargs["plugins"] = kwargs.get("plugins") or PluginManager()
        if kwargs.get("context_factory") is None:
            kwargs["context_factory"] = lambda tree: ContextAdapter(tree, plugins.slots.context, plugins.slots.memory)
        try:
            super().__init__(*args, **kwargs)
        except BaseException:
            self.abort_startup()
            plugins.close()
            raise

    async def close(self) -> None:
        """Close for the owner (exod, the terminal's in-process core), retried until it completes.

        ``CoreRuntime.close`` is bounded. When a synchronous tool outlives the bound, it has already stopped jobs,
        channels, peers, the scheduler and MCP, and it keeps plugin resources, the store and the writer claim.
        Python cannot stop that thread, and the process waits for it at exit anyway, so this logs and closes
        again until the tool returns. Then plugin cleanups run once and the writer claim is released.

        A hard stop is not retried. When a stop step or the wait met a second Ctrl-C or SIGTERM (``CancelledError``,
        ``KeyboardInterrupt`` or ``SystemExit``), that error is raised after one bounded attempt. If a tool outlived
        the bound, the rest stays as after a timeout.
        """
        from exo.core.shutdown import ShutdownTimeout
        while True:
            try:
                return await super().close()
            except ShutdownTimeout as exc:
                stop = next((error for error in exc.errors if not isinstance(error, Exception)), None)
                if stop is not None:
                    raise stop from exc
                logging.getLogger(__name__).warning("%s; closing again", exc)

    def abort_startup(self) -> None:
        """Release an incompletely composed, unstarted runtime synchronously.

        The factory can run inside an event loop. None of its services has started
        yet, so shutdown needs no coroutine runner or extra event loop. A running
        engine must use ``await close()`` instead.
        """
        from exo.core import agentlog, trace
        jobs = getattr(self, "jobs", None)
        registry = getattr(self, "registry", None)
        loop = getattr(self, "loop", None)
        if (getattr(self, "_started", None) is not None or bool(getattr(self, "_tasks", None))
                or jobs is not None and jobs.loop is not None
                or registry is not None and registry.workers
                or loop is not None and loop.busy()):
            raise RuntimeError("abort_startup requires an unstarted engine; use await close()")
        steps = []
        scheduler = getattr(self, "scheduler", None)
        learner = getattr(self, "_learn", None)
        if jobs is not None:
            steps.append(jobs.abort_startup)
        if scheduler is not None:
            steps.append(scheduler.close)
        if learner is not None:
            steps.append(learner.close)
        plugins = getattr(self, "plugins", None)
        if plugins is not None:
            steps.append(plugins.close)
        steps.append(lambda: agentlog.release_writer(getattr(self, "_writer", None)))
        steps.append(lambda: trace.remove_sink(self._on_harness_error))
        store = getattr(self, "store", None)
        if store is not None:
            steps.append(store.close)
        for step in steps:
            try:
                step()
            except BaseException:
                logging.getLogger(__name__).warning("startup rollback failed", exc_info=True)
        self._writer = None


# Preserve public introspection without changing the static constructor type.
update_wrapper(CoreEngine.__init__, CoreRuntime.__init__)


def create(seat: str | None = None) -> CoreEngine:
    """Compatibility factory; application configuration lives in ``exo.compose``."""
    from exo.compose import create as compose
    return compose(seat)
