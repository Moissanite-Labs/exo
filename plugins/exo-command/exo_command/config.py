"""Settings for exo-command.

Resolution order for every key, first hit wins:

1. explicit overrides passed by the caller (tests, CLI),
2. Hermes config: ``plugins.entries.<plugin id>.settings.<key>`` (the plugin config schema),
3. environment variables ``EXO_COMMAND_<KEY>`` (upper case),
4. built-in defaults.

Nothing here names a principal, a profile or a machine. The principal store has no default
path unless ``EXO_PRINCIPAL_STORE`` or ``EXO_HOME`` plus ``EXO_PRINCIPAL`` are set, so a fresh
install shows an empty, labelled decisions panel instead of guessing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

PLUGIN_ID = "exo-command"

DEFAULTS: Dict[str, Any] = {
    "hermes_home": "",            # empty: Hermes default root, then $HERMES_HOME, then ~/.hermes
    "profiles": [],               # empty: every profile with a state.db, plus the root one
    "include_root_profile": True,  # the root <home>/state.db, shown as "default"
    "principal_store": "",        # empty: $EXO_PRINCIPAL_STORE, else $EXO_HOME/principals/$EXO_PRINCIPAL
    "decisions_queue": "queues/decisions.jsonl",  # relative paths resolve inside the store
    "live_window_seconds": 900,   # a session counts as live if it moved in this window
    "past_window_hours": 24,      # finished subagents shown under "past"
    "cost_window_days": 7,
    "cost_top_sessions": 25,
    "kanban_done_limit": 20,      # newest done tasks kept per board
    "project_stall_days": 7,
    "project_abandon_days": 60,
    "project_window_days": 90,    # sessions older than this are not clustered
    "cache_seconds": 20,
    "generic_paths": [],          # extra paths that must never become a cluster key
}


@dataclass
class Settings:
    hermes_home: Path
    profiles: List[str] = field(default_factory=list)
    include_root_profile: bool = True
    principal_store: Optional[Path] = None
    decisions_queue: Optional[Path] = None
    live_window_seconds: int = 900
    past_window_hours: int = 24
    cost_window_days: int = 7
    cost_top_sessions: int = 25
    kanban_done_limit: int = 20
    project_stall_days: int = 7
    project_abandon_days: int = 60
    project_window_days: int = 90
    cache_seconds: int = 20
    generic_paths: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = str(v) if isinstance(v, Path) else v
        return out


def _hermes_settings() -> Mapping[str, Any]:
    """``plugins.entries.exo-command.settings`` from the live Hermes config, if importable."""
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore
    except Exception:
        return {}
    try:
        cfg = load_config_readonly() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
        settings = entry.get("settings") or entry.get("config") or {}
        return settings if isinstance(settings, Mapping) else {}
    except Exception:
        return {}


def _env_value(key: str, default: Any) -> Any:
    raw = os.environ.get("EXO_COMMAND_" + key.upper())
    if raw is None:
        return None
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return None
    if isinstance(default, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return raw


def default_hermes_home() -> Path:
    try:
        from hermes_constants import get_default_hermes_root  # type: ignore
        return Path(get_default_hermes_root())
    except Exception:
        pass
    env = os.environ.get("HERMES_HOME")
    if env:
        p = Path(env).expanduser()
        # a profile-scoped HERMES_HOME is <root>/profiles/<name>
        if p.parent.name == "profiles":
            return p.parent.parent
        return p
    return Path("~/.hermes").expanduser()


def default_principal_store() -> Optional[Path]:
    env = os.environ.get("EXO_PRINCIPAL_STORE")
    if env:
        return Path(env).expanduser()
    pid = os.environ.get("EXO_PRINCIPAL")
    if pid:
        home = Path(os.environ.get("EXO_HOME") or "~/.exo").expanduser()
        return home / "principals" / pid
    return None


def load_settings(overrides: Optional[Mapping[str, Any]] = None,
                  use_hermes_config: bool = True) -> Settings:
    overrides = dict(overrides or {})
    hermes = _hermes_settings() if use_hermes_config else {}
    merged: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if key in overrides and overrides[key] is not None:
            merged[key] = overrides[key]
        elif key in hermes and hermes[key] not in (None, ""):
            merged[key] = hermes[key]
        else:
            env = _env_value(key, default)
            merged[key] = env if env not in (None, "") else default

    home = Path(str(merged["hermes_home"])).expanduser() if merged["hermes_home"] else default_hermes_home()
    store = (Path(str(merged["principal_store"])).expanduser()
             if merged["principal_store"] else default_principal_store())
    queue_raw = str(merged["decisions_queue"] or "")
    queue: Optional[Path] = None
    if queue_raw:
        q = Path(queue_raw).expanduser()
        if q.is_absolute():
            queue = q
        elif store is not None:
            queue = store / q
    profiles = merged["profiles"]
    if isinstance(profiles, str):
        profiles = [p.strip() for p in profiles.split(",") if p.strip()]

    def _int(k: str) -> int:
        try:
            return int(merged[k])
        except (TypeError, ValueError):
            return int(DEFAULTS[k])

    return Settings(
        hermes_home=home,
        profiles=[str(p) for p in (profiles or [])],
        include_root_profile=bool(merged["include_root_profile"]),
        principal_store=store,
        decisions_queue=queue,
        live_window_seconds=_int("live_window_seconds"),
        past_window_hours=_int("past_window_hours"),
        cost_window_days=_int("cost_window_days"),
        cost_top_sessions=_int("cost_top_sessions"),
        kanban_done_limit=_int("kanban_done_limit"),
        project_stall_days=_int("project_stall_days"),
        project_abandon_days=_int("project_abandon_days"),
        project_window_days=_int("project_window_days"),
        cache_seconds=_int("cache_seconds"),
        generic_paths=[str(p) for p in (merged["generic_paths"] or [])],
    )
