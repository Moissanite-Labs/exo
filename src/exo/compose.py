"""Composition layer: build the CoreEngine ``exo serve`` runs (docs/ENGINE-SPLIT.md, step 3).

Reads ``$EXO_HOME/config.json`` and the seat, loads the executor and plugins (the trusted inventory in
required-executor mode, which demands exo-gate), then attaches product integrations such as learning.
``exo.core.engine:create`` still works and forwards here.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from exo import peers
from exo.config import Config, ConfigError
from exo.core.engine import SYSTEM_PROMPT
from exo.application import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.seats import SeatError
from exo.core.seats import load as load_seat
from exo.core.skills import pack_dir
from exo.core.tools.executor import ExecutorConfigurationError, load_executor
from exo.scheduler import Scheduler
from exo.model_spend import configured, resolve_spend_policy


def fitting_peers_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """A short private peers folder when the default one would push ``exo-<pid>.sock`` past the Unix socket limit
    (a long ``HOME``); ``None`` when the default fits or the folder was named with ``EXO_PEERS_DIR``."""
    env = dict(os.environ if env is None else env)
    default = peers.peers_dir(env)
    if env.get("EXO_PEERS_DIR") or len(os.fsencode(str(default / "exo-9999999.sock"))) < 104:
        return None
    short = Path(tempfile.gettempdir()) / f"exo-{os.getuid()}" / "peers"  # shared by every window of this user
    short.mkdir(parents=True, exist_ok=True, mode=0o700)
    return short


def create(seat: str | None = None) -> CoreEngine:
    """Factory for ``exo serve`` (``--engine exo.compose:create``): reads ``$EXO_HOME/config.json``.

    ``seat`` (or ``$EXO_SEAT``, which ``exo serve --seat`` sets) names a file in ``$EXO_HOME/seats``."""
    cfg = Config.load()
    from exo.work_checkpoint import CheckpointConfig
    checkpoint_config = CheckpointConfig(cfg.get("work_checkpoint"))
    notebook_config = None
    if "work_notes" in cfg.data:
        from exo.work_notebook import NotebookConfig
        if not isinstance(cfg.data["work_notes"], dict):
            raise ConfigError("work_notes must be an object")
        notebook_config = NotebookConfig(cfg.data["work_notes"])
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
    folders: list[str | os.PathLike[str]] = [*(st.skill_folders if st else []), cfg.home / "skills"]
    if pack := pack_dir():
        folders.append(pack)
    eng = CoreEngine(cfg.home, models, plugins=pm, executor=executor, executor_required=executor_required,
                      cwd=cfg.get("cwd"), seat=st, skill_folders=folders,
                      mcp=cfg.get("mcp"), channels=cfg.get("channels"), system_prompt=cfg.get("system_prompt") or SYSTEM_PROMPT,
                      delegation=cfg.get("delegation"), trace_cfg=cfg.get("trace"), schedule=cfg.get("schedule", True) is not False, peers=cfg.get("peers"), jobs=cfg.get("jobs"), attach_peers=peers.attach,
                      attach_scheduler=Scheduler,
                      tree_cfg={k: v for k, v in (cfg.get("compression") or {}).items()
                            if k in ("leaf_size", "tail_messages", "view_budget", "min_compact_tokens")})
    eng.claim_writer()  # the one writer of this agent's log and tree (D-028); WriterBusy if another process is
    if eng.peers is not None:
        eng.peers.autostart = True
    eng.peer_cfg_dir = eng.peer_cfg_dir or fitting_peers_dir()
    if notebook_config is not None and notebook_config.enabled:
        from exo.work_notebook import attach as attach_work_notebook
        attach_work_notebook(eng, notebook_config, principal=principal)
    from exo.work_checkpoint import attach as attach_work_checkpoint
    attach_work_checkpoint(eng, checkpoint_config, principal=principal or "")
    if cfg.get("learn", {}).get("enabled", True) is not False:
        from exo import learn
        learn.attach(eng)
    return eng
