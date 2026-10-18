"""Settings for exo-command.

Resolution order for every key, first hit wins:

1. explicit overrides passed by the caller (tests, CLI flags),
2. environment variables ``EXO_COMMAND_<KEY>`` (upper case),
3. built-in defaults.

Where the Exo core lives (``socket``, ``url``, ``token``) is just a setting; the default is the
socket ``exod`` creates under ``$EXO_HOME/run``. Nothing here names a principal, a seat or a machine.
The principal store has no default path unless ``EXO_PRINCIPAL_STORE`` or ``EXO_PRINCIPAL`` is set,
so a fresh install shows an empty, labelled decisions panel instead of guessing. ``EXO_PRINCIPAL``
maps to its store through ``exo.store.principal_root``, so a store made with ``exo init --root`` is found.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

PLUGIN_ID = "exo-command"

DEFAULTS: Dict[str, Any] = {
    "socket": "",                 # empty: $EXO_HOME/run/exod.sock
    "url": "",                    # TCP server instead of the socket, e.g. http://127.0.0.1:7341
    "token": "",                  # bearer token for url (or EXO_COMMAND_TOKEN_FILE / $EXO_TOKEN)
    "token_file": "",             # empty: $EXO_HOME/run/exod.token when it exists
    "host": "127.0.0.1",          # web client bind address
    "port": 7342,
    "principal_store": "",        # empty: $EXO_PRINCIPAL_STORE, else the store of $EXO_PRINCIPAL
    "decisions_queue": "queues/decisions.jsonl",  # relative paths resolve inside the store
    "live_window_seconds": 900,   # a session counts as live if it moved in this window
    "past_window_hours": 24,      # finished subagent sessions shown under "past"
    "cost_window_days": 7,
    "cost_top_sessions": 25,
    "project_stall_days": 7,
    "project_abandon_days": 60,
    "project_window_days": 90,    # sessions older than this are not clustered
    "cache_seconds": 20,
    "generic_paths": [],          # extra paths that must never become a cluster key
}


@dataclass
class Settings:
    socket: str = ""
    url: str = ""
    token: str = ""
    token_file: str = ""
    host: str = "127.0.0.1"
    port: int = 7342
    principal_store: Optional[Path] = None
    decisions_queue: Optional[Path] = None
    live_window_seconds: int = 900
    past_window_hours: int = 24
    cost_window_days: int = 7
    cost_top_sessions: int = 25
    project_stall_days: int = 7
    project_abandon_days: int = 60
    project_window_days: int = 90
    cache_seconds: int = 20
    generic_paths: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for f in fields(self):
            if f.name in ("token", "token_file"):
                continue  # never echo a secret to a page
            v = getattr(self, f.name)
            out[f.name] = str(v) if isinstance(v, Path) else v
        return out

    def resolved_socket(self) -> str:
        if self.socket:
            return str(Path(self.socket).expanduser())
        return str(exo_home() / "run" / "exod.sock")

    def resolved_token(self) -> Optional[str]:
        if self.token:
            return self.token
        if os.environ.get("EXO_TOKEN"):
            return os.environ["EXO_TOKEN"]
        path = Path(self.token_file).expanduser() if self.token_file else exo_home() / "run" / "exod.token"
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None


def exo_home() -> Path:
    return Path(os.environ.get("EXO_HOME") or "~/.exo").expanduser()


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


def default_principal_store() -> Optional[Path]:
    """``$EXO_PRINCIPAL_STORE``, else the store of ``$EXO_PRINCIPAL`` by the core's rule
    (``exo.store.principal_root``: config ``principals.<id>``, else ``$EXO_HOME/principals/<id>``)."""
    env = os.environ.get("EXO_PRINCIPAL_STORE")
    if env:
        return Path(env).expanduser()
    pid = os.environ.get("EXO_PRINCIPAL")
    if pid:
        from exo.store import principal_root
        try:
            return principal_root(pid, exo_home())
        except ValueError:  # not a valid principal id: no store rather than a guess
            return None
    return None


def load_settings(overrides: Optional[Mapping[str, Any]] = None) -> Settings:
    overrides = dict(overrides or {})
    merged: Dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if key in overrides and overrides[key] is not None:
            merged[key] = overrides[key]
        else:
            env = _env_value(key, default)
            merged[key] = env if env not in (None, "") else default

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

    def _int(k: str) -> int:
        try:
            return int(merged[k])
        except (TypeError, ValueError):
            return int(DEFAULTS[k])

    return Settings(
        socket=str(merged["socket"] or ""), url=str(merged["url"] or ""), token=str(merged["token"] or ""),
        token_file=str(merged["token_file"] or ""), host=str(merged["host"] or "127.0.0.1"),
        port=_int("port"), principal_store=store, decisions_queue=queue,
        live_window_seconds=_int("live_window_seconds"), past_window_hours=_int("past_window_hours"),
        cost_window_days=_int("cost_window_days"), cost_top_sessions=_int("cost_top_sessions"),
        project_stall_days=_int("project_stall_days"), project_abandon_days=_int("project_abandon_days"),
        project_window_days=_int("project_window_days"), cache_seconds=_int("cache_seconds"),
        generic_paths=[str(p) for p in (merged["generic_paths"] or [])],
    )
