"""exo-memory configuration.

Resolution order, lowest first: defaults, environment, the profile file
``<exo_home>/exo-memory/config.json`` (written by ``save_config``), then values
passed to the provider constructor. Nothing here names a person, a machine or a path of one.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "exo_home": "",            # default $EXO_HOME, else ~/.exo
    "principal": "",           # principal id; default $EXO_PRINCIPAL
    "store_root": "",          # explicit store dir; default $EXO_STORE, else <exo_home>/principals/<principal>
    "agent_id": "",            # ledger author for agent turns; default the seat identity
    "key_path": "",            # Ed25519 key; default <exo_home>/keys/<agent_id>.key
    "auto_keygen": True,       # create the agent key on first use if missing
    "pack_budget": 1500,       # tokens of PRINCIPAL.md in the system prompt
    "prefetch_budget": 1500,   # tokens of recall per turn
    "episodic_share": 0.4,     # share of the prefetch budget kept for episodic recall
    "episodic_hits": 5,        # keyword hits from the episodic log per recall
    "recent_skip": 20,         # newest log messages left out of keyword hits (still in context)
    "view_chars": 6000,        # merge target of the episodic summary view
    "capture_chars": 2000,     # max chars of the block on_pre_compress returns
    "capture_turns": True,     # capture rulings and corrections as each turn ends (the core has no pre-compress call)
    "compact_enabled": True,   # background summary-tree compactor (needs an auxiliary model)
    "principal_user_ids": "",  # comma list of platform user ids that are the principal; empty = any human
}

ENV = {"exo_home": "EXO_HOME", "principal": "EXO_PRINCIPAL", "store_root": "EXO_STORE"}
INTS = ("pack_budget", "prefetch_budget", "episodic_hits", "recent_skip", "view_chars", "capture_chars")
BOOLS = ("auto_keygen", "compact_enabled", "capture_turns")
SAFE_ID = re.compile(r"[^a-z0-9_-]+")
PID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def config_path(home: str | os.PathLike) -> Path:
    return Path(home) / "exo-memory" / "config.json"


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


def load(home: str | os.PathLike | None, overrides: dict | None = None) -> dict:
    cfg = dict(DEFAULTS)
    for key, var in ENV.items():
        if os.environ.get(var):
            cfg[key] = os.environ[var]
    if home:
        try:
            cfg.update({k: v for k, v in json.loads(config_path(home).read_text("utf-8")).items()
                        if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    cfg.update({k: v for k, v in (overrides or {}).items() if k in DEFAULTS})
    for k in INTS:
        cfg[k] = int(cfg[k])
    for k in BOOLS:
        cfg[k] = _bool(cfg[k])
    cfg["episodic_share"] = min(max(float(cfg["episodic_share"]), 0.0), 0.9)
    return cfg


def save(values: dict, home: str | os.PathLike) -> Path:
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        current = {}
    merged = {**current, **{k: v for k, v in values.items() if k in DEFAULTS}}
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def exo_home(cfg: dict) -> Path:
    return Path(cfg.get("exo_home") or Path.home() / ".exo").expanduser()


def store_root(cfg: dict) -> Path | None:
    if cfg.get("store_root"):
        return Path(cfg["store_root"]).expanduser()
    pid = cfg.get("principal") or ""
    if not pid:
        return None
    if not PID_RE.match(pid):
        raise ValueError(f"bad principal id {pid!r}: use lowercase letters, digits, - and _")
    return exo_home(cfg) / "principals" / pid


def principal_id(cfg: dict) -> str:
    return cfg.get("principal") or "principal"


def agent_id(cfg: dict, identity: str | None = None) -> str:
    raw = cfg.get("agent_id") or identity or "exo-memory"
    return SAFE_ID.sub("-", str(raw).lower()).strip("-") or "exo-memory"


def key_path(cfg: dict, agent: str) -> Path:
    return Path(cfg["key_path"]).expanduser() if cfg.get("key_path") else exo_home(cfg) / "keys" / f"{agent}.key"


def principal_user_ids(cfg: dict) -> set[str]:
    return {s.strip() for s in str(cfg.get("principal_user_ids") or "").split(",") if s.strip()}


SCHEMA = [
    {"key": "principal", "description": "Principal id (store at <exo_home>/principals/<id>).",
     "env_var": "EXO_PRINCIPAL", "required": True},
    {"key": "exo_home", "description": "Exo home directory (default ~/.exo).", "env_var": "EXO_HOME"},
    {"key": "store_root", "description": "Explicit principal store directory (overrides principal).",
     "env_var": "EXO_STORE"},
    {"key": "agent_id", "description": "Ledger author name for this agent's turns (default: agent identity)."},
    {"key": "pack_budget", "description": "Tokens of the principal pack in the system prompt.",
     "type": "integer", "default": 1500, "minimum": 200, "maximum": 8000, "step": 100},
    {"key": "prefetch_budget", "description": "Tokens of recall injected per turn.",
     "type": "integer", "default": 1500, "minimum": 200, "maximum": 8000, "step": 100},
    {"key": "episodic_share", "description": "Share of the recall budget kept for past sessions (0 to 0.9).",
     "type": "number", "default": 0.4, "minimum": 0, "maximum": 0.9, "step": 0.05},
    {"key": "compact_enabled", "description": "Build the episodic summary tree in the background (auxiliary model).",
     "type": "boolean", "default": True, "choices": ["true", "false"]},
    {"key": "principal_user_ids", "description": "Comma list of platform user ids that are the principal."},
]
