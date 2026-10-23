"""Where the learning loop keeps its state: the principal store, a signed ledger and the settings.

The store root comes from config `learn.store`. If that is not set it falls back to the exo-memory
plugin's own setting (plugin config, its profile file, then `EXO_STORE` or `EXO_PRINCIPAL` in the
environment), then to the active principal in config.json (`principal`, which `exo init` sets), the same
rule exo-memory uses. With none of these, learning is off; `Learner.notice` says so once.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from exo.config import Config, ConfigError
from exo.store import PrincipalStore, principal_root

AGENT = "exo-learn"
PRINCIPAL = "principal"
PID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "fast_loop": {
        "enabled": True,
        "every_turns": 10,       # user turns since the last review that trigger one without a signal
        "deadline": 90.0,        # seconds a review may run
        "daily_cap": 20,         # reviews per day
        "max_chars": 12000,      # prompt size cap
        "critic": True,
    },
    "promote": {
        "min_sessions": 2,       # independent sessions needed for auto-promotion (5.6 rule 4)
        "stale_days": 14,        # an open proposal older than this may become a question (rule 6)
        "eval_gate_for": ["skill"],   # proposal types that must pass the eval gate (rule 5)
        "guarded_domains": [],
        "high_impact_domains": [],    # taste in these domains also needs the gate
        "voi_floor": 0.3,
    },
    "climb": {"candidates": 10, "judge_samples": 3, "margin": 0.01},
}


def _merge(base: dict, extra: Mapping) -> dict:
    for k, v in extra.items():
        if isinstance(v, Mapping) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        elif k in base:
            base[k] = v
    return base


def load_config(engine: Any) -> Config | None:
    try:
        return Config.load(getattr(engine, "home", None))
    except (ConfigError, OSError):
        return None


def load_settings(engine: Any) -> dict:
    """Apply the same capability restrictions to lazy feedback handlers as to startup.

    A running engine's selected profile is an upper bound: changing config before
    the first feedback message cannot attach a service that startup omitted.
    """
    from exo.harness import resolve

    cfg = load_config(engine)
    configured = resolve(cfg.data).settings.get("learn") if cfg else None
    settings = _merge(copy.deepcopy(DEFAULTS), configured or {})
    plan = getattr(engine, "harness_plan", None)
    if plan is not None and not plan.enabled("learning"):
        settings["enabled"] = False
    return settings


def resolve_store(engine: Any, env: Mapping[str, str] | None = None) -> Path | None:
    """The principal store root for this engine, or None when learning has nowhere to write. A principal id maps to
    its store through `exo.store.principal_root`, which honours the explicit roots in config `principals`."""
    env = os.environ if env is None else env
    home = Path(getattr(engine, "home", None) or env.get("EXO_HOME") or Path.home() / ".exo")
    cfg = load_config(engine)
    explicit = cfg.get("learn.store") if cfg else None
    if explicit:
        return Path(str(explicit)).expanduser()
    plugin_cfg: dict = {}
    plugins = getattr(engine, "plugins", None)
    if plugins is not None:
        plugin_cfg = dict((getattr(plugins, "config", None) or {}).get("exo-memory") or {})
    try:
        plugin_cfg = {**json.loads((home / "exo-memory" / "config.json").read_text("utf-8")), **plugin_cfg}
    except (OSError, ValueError):
        pass
    root = plugin_cfg.get("store_root") or env.get("EXO_STORE")
    if root:
        return Path(str(root)).expanduser()
    # Last, the active principal in config.json (`exo init` sets it), a string only, as exo-memory reads it.
    active = cfg.data.get("principal") if cfg else None
    pid = plugin_cfg.get("principal") or env.get("EXO_PRINCIPAL") or (active if isinstance(active, str) else None)
    if pid and PID_RE.match(str(pid)):
        return principal_root(str(pid), home)
    return None


class Runtime:
    """A store with a signed ledger. Keys live at `<home>/keys/<agent>.key` (made on first use)."""

    def __init__(self, root: Path, home: Path, agent: str = AGENT) -> None:
        self.root = Path(root)
        self.home = Path(home)
        self.agent = agent
        self.store = PrincipalStore(root)
        kp = self.home / "keys" / f"{agent}.key"
        from exo.ledger_authority.factory import open_writable_ledger
        self.ledger = open_writable_ledger(self.store.ledger_path, agent, kp,
                                           writer="exo-learn", auto_keygen=True)
        self.signer = self.ledger.signer
        self.lock = threading.RLock()
        # Capture keys read from the ledger, with the seq and id of the last row read (signals.known_keys).
        self.known: tuple[int, str | None, set] = (0, None, set())

    def close(self) -> None:
        self.ledger.close()
