"""Composition layer: build the CoreEngine ``exo serve`` runs (docs/ENGINE-SPLIT.md, step 3).

Reads ``$EXO_HOME/config.json`` and the seat, loads the executor and plugins (the trusted inventory in
required-executor mode, which demands exo-gate), then attaches product integrations such as learning.
``exo.core.engine:create`` still works and forwards here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from exo.config import Config
from exo.core.engine import SYSTEM_PROMPT, CoreEngine
from exo.core.plugins import PluginManager
from exo.core.seats import SeatError
from exo.core.seats import load as load_seat
from exo.core.skills import pack_dir
from exo.core.tools.executor import ExecutorConfigurationError, load_executor


def create(seat: str | None = None) -> CoreEngine:
    """Factory for ``exo serve`` (``--engine exo.compose:create``): reads ``$EXO_HOME/config.json``.

    ``seat`` (or ``$EXO_SEAT``, which ``exo serve --seat`` sets) names a file in ``$EXO_HOME/seats``."""
    cfg = Config.load()
    executor, executor_required = load_executor()  # required failures precede all plugin imports
    st = None
    if seat or os.environ.get("EXO_SEAT"):
        try:
            st = load_seat(cfg.home, seat or os.environ["EXO_SEAT"])
        except SeatError as exc:
            raise SystemExit(f"exo: {exc}") from None
        if st.principal and not os.environ.get("EXO_PRINCIPAL"):
            os.environ["EXO_PRINCIPAL"] = st.principal
    pm = PluginManager(config=cfg.data.get("plugin_config") or {},
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
    pm.load_all(dirs=plugin_dirs, enabled=enabled)
    if executor_required:
        missing = [name for name in required_plugins if name not in pm.plugins or pm.plugins[name].error]
        if missing:
            raise ExecutorConfigurationError(f"required executor plugins failed to load: {', '.join(missing)}")
    models = cfg.models()
    folders: list[str | os.PathLike[str]] = [*(st.skill_folders if st else []), cfg.home / "skills"]
    if pack := pack_dir():
        folders.append(pack)
    from exo import rounds
    cap = rounds.configured(os.environ, st.loop if st else None, cfg.get("loop"))  # D-064
    loop_kw: dict[str, Any] = {"max_iterations": cap} if cap else {}
    eng = CoreEngine(cfg.home, models, plugins=pm, executor=executor, executor_required=executor_required,
                      cwd=cfg.get("cwd"), seat=st, skill_folders=folders,
                      mcp=cfg.get("mcp"), channels=cfg.get("channels"), system_prompt=cfg.get("system_prompt") or SYSTEM_PROMPT,
                      delegation=cfg.get("delegation"), trace_cfg=cfg.get("trace"), schedule=cfg.get("schedule", True) is not False, peers=cfg.get("peers"), jobs=cfg.get("jobs"),
                      tree_cfg={k: v for k, v in (cfg.get("compression") or {}).items()
                            if k in ("leaf_size", "tail_messages", "view_budget", "min_compact_tokens")},
                      **loop_kw)
    eng.claim_writer()  # the one writer of this agent's log and tree (D-028); WriterBusy if another process is
    eng.peer_autostart = True
    if cfg.get("learn", {}).get("enabled", True) is not False:
        from exo import learn
        learn.attach(eng)
    return eng
