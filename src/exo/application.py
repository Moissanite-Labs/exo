"""Application composition for the standalone core engine.

Principal/config selection, required plugins and product integrations are wired
here. The core engine keeps turn orchestration and its public lifecycle.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Any
from pathlib import Path

from exo.config import Config
from exo.core.engine import CoreRuntime, SYSTEM_PROMPT
from exo.core.plugins import PluginManager
from exo.core.seats import SeatError, load as load_seat
from exo.core.skills import pack_dir
from exo.core.tools.executor import ExecutorConfigurationError, load_executor
from exo.model_spend import resolve_spend_policy
from exo.plugin_context import ContextAdapter


class CoreEngine(CoreRuntime):
    """Standalone application runtime with the optional plugin context bridge.

    The public constructor retains CoreRuntime's signature and type hints. Supplying
    context_factory replaces only this bridge; the runtime still owns its tree.
    """

    @wraps(CoreRuntime.__init__)
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        plugins = kwargs["plugins"] = kwargs.get("plugins") or PluginManager()
        if kwargs.get("context_factory") is None:
            kwargs["context_factory"] = lambda tree: ContextAdapter(tree, plugins.slots.context, plugins.slots.memory)
        super().__init__(*args, **kwargs)


def create(seat: str | None = None) -> CoreEngine:
    """Factory for ``exo serve --engine exo.core.engine:create``: reads ``$EXO_HOME/config.json``.

    ``seat`` (or ``$EXO_SEAT``, which ``exo serve --seat`` sets) names a file in ``$EXO_HOME/seats``."""
    cfg = Config.load()
    executor, executor_required = load_executor()  # required failures precede all plugin imports
    st = None
    if seat or os.environ.get("EXO_SEAT"):
        try:
            st = load_seat(cfg.home, seat or os.environ["EXO_SEAT"])
        except SeatError as exc:
            raise SystemExit(f"exo: {exc}") from None
    if st and st.principal and os.environ.get("EXO_PRINCIPAL") not in (None, "", st.principal):
        raise ValueError("seat principal conflicts with EXO_PRINCIPAL")
    principal = (st.principal if st else None) or cfg.principal
    from exo.model_spend import configured
    selection = resolve_spend_policy((cfg.data.get("models") or {}).get("spend"), principal=principal)
    spend = configured(selection.config, required=selection.required)
    if spend is not None and spend.principal != principal:
        raise ValueError("model-spend policy principal does not match the configured principal")
    if st and st.principal and not os.environ.get("EXO_PRINCIPAL"):
        os.environ["EXO_PRINCIPAL"] = st.principal
    pm = PluginManager(config=cfg.data.get("plugin_config") or {},
                       executor_required=executor_required,
                       memory_choice=cfg.get("memory_provider"), context_choice=cfg.get("context_engine"))
    enabled = st.plugins if st and st.plugins is not None else cfg.data.get("core_plugins")
    plugin_dirs = None
    if executor_required:
        # The installed executor owns this trusted inventory; user discovery is disabled.
        raw_dirs = getattr(executor, "trusted_plugin_dirs", ())
        if not isinstance(raw_dirs, (tuple, list)):
            raise ExecutorConfigurationError("executor trusted_plugin_dirs must be a tuple/list of absolute paths")
        plugin_dirs = tuple(raw_dirs)
        if not all(isinstance(path, str) and Path(path).is_absolute() for path in plugin_dirs):
            raise ExecutorConfigurationError("executor trusted_plugin_dirs must contain absolute paths")
        raw_required = getattr(executor, "required_plugins", ())
        if not isinstance(raw_required, (tuple, list)):
            raise ExecutorConfigurationError("executor required_plugins must be a tuple/list of plugin names")
        if not all(isinstance(name, str) and name for name in raw_required):
            raise ExecutorConfigurationError("executor required_plugins must contain plugin names")
        # The gate supplies the committed decision/approval evidence required mode promises.
        required_plugins = tuple(dict.fromkeys(("exo-gate", *raw_required)))
        if enabled is not None and not set(required_plugins).issubset(enabled):
            raise ExecutorConfigurationError("plugin configuration disables a required executor plugin")
    pm.load_all(dirs=plugin_dirs, enabled=enabled, entry_points=not executor_required)
    if executor_required:
        missing = [name for name in required_plugins if name not in pm.plugins or pm.plugins[name].error]
        if missing:
            raise ExecutorConfigurationError(f"required executor plugins failed to load: {', '.join(missing)}")
    models = cfg.models()
    folders = [*(st.skill_folders if st else []), cfg.home / "skills"]
    if pack := pack_dir():
        folders.append(pack)
    eng = CoreEngine(cfg.home, models, plugins=pm, executor=executor, executor_required=executor_required,
                      cwd=cfg.get("cwd"), seat=st, skill_folders=folders,
                      mcp=cfg.get("mcp"), channels=cfg.get("channels"), system_prompt=cfg.get("system_prompt") or SYSTEM_PROMPT,
                      delegation=cfg.get("delegation"), trace_cfg=cfg.get("trace"), schedule=cfg.get("schedule", True) is not False, peers=cfg.get("peers"), jobs=cfg.get("jobs"),
                      tree_cfg={k: v for k, v in (cfg.get("compression") or {}).items()
                            if k in ("leaf_size", "tail_messages", "view_budget", "min_compact_tokens")})
    eng.claim_writer()  # the one writer of this agent's log and tree (D-028); WriterBusy if another process is
    eng.peer_autostart = True
    if cfg.get("learn", {}).get("enabled", True) is not False:
        from exo import learn
        learn.attach(eng)
    return eng
