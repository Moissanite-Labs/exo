"""Application composition for the standalone core engine.

Principal/config selection, required plugins and product integrations are wired
here. The core engine keeps turn orchestration and its public lifecycle.
"""
from __future__ import annotations

from functools import update_wrapper
import inspect
from typing import Any

from exo.core.engine import CoreRuntime
from exo.core.plugins import PluginManager
from exo.plugin_context import ContextAdapter


class CoreEngine(CoreRuntime):
    """Standalone application runtime with the optional plugin context bridge.

    The public constructor retains CoreRuntime's signature and type hints. Supplying
    context_factory replaces only this bridge; the runtime still owns its tree.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        plugins = kwargs["plugins"] = kwargs.get("plugins") or PluginManager()
        if kwargs.get("context_factory") is None:
            kwargs["context_factory"] = lambda tree: ContextAdapter(tree, plugins.slots.context, plugins.slots.memory)
        if kwargs.get("provider_auth") is None:
            from exo import chatgpt_auth
            bound = inspect.signature(CoreRuntime.__init__).bind_partial(None, *args, **kwargs).arguments
            home = bound["home"]
            seat = bound.get("seat")
            name = seat.name if seat else "default"
            kwargs["provider_auth"] = {"chatgpt": (
                lambda: chatgpt_auth.status(home, name)["available"],
                lambda: chatgpt_auth.request_headers(home, name),
                lambda: chatgpt_auth.continuation_scope(home, name))}
        super().__init__(*args, **kwargs)


# Preserve public introspection without changing the static constructor type.
update_wrapper(CoreEngine.__init__, CoreRuntime.__init__)


def create(seat: str | None = None) -> CoreEngine:
    """Compatibility factory; application configuration lives in ``exo.compose``."""
    from exo.compose import create as compose
    return compose(seat)
