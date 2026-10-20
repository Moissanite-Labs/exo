"""Bus configuration. Every path and URL comes from the environment or the caller.

Each setting reads `EXO_BUS_<NAME>` first, then the legacy `DIVAN_<NAME>` so seats and relays set up
for Divan Bus keep working unchanged. Defaults hold no person, machine or organisation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import overload

DEFAULT_URL = "http://127.0.0.1:8787"
DEFAULT_BIND = "127.0.0.1:8787"


@overload
def env(name: str, default: str, environ=None) -> str: ...
@overload
def env(name: str, default: None = None, environ=None) -> str | None: ...
def env(name: str, default: str | None = None, environ=None) -> str | None:
    environ = os.environ if environ is None else environ
    for key in (f"EXO_BUS_{name}", f"DIVAN_{name}"):
        if environ.get(key):
            return environ[key]
    return default


def exo_home(environ=None) -> Path:
    environ = os.environ if environ is None else environ
    return Path(os.path.expanduser(environ.get("EXO_HOME", "~/.exo")))


def bus_home(environ=None) -> Path:
    return exo_home(environ) / "bus"


def keys_dir(environ=None) -> Path:
    """Where seat keys live: EXO_BUS_KEYS_DIR, else DIVAN_KEYS_DIR, else $EXO_HOME/keys."""
    value = env("KEYS_DIR", environ=environ)
    return Path(os.path.expanduser(value)) if value else exo_home(environ) / "keys"


@dataclass
class ClientConfig:
    url: str = DEFAULT_URL
    seat: str | None = None
    keys_dir: Path = field(default_factory=keys_dir)

    @classmethod
    def from_env(cls, environ=None) -> "ClientConfig":
        return cls(url=env("URL", DEFAULT_URL, environ), seat=env("SEAT", None, environ), keys_dir=keys_dir(environ))


@dataclass
class RelayConfig:
    db: Path
    registry: Path
    leader_pubkey: str | None = None
    relay_key: str | None = None  # key file path or inline base64 seed
    keys_dir: Path | None = None  # seat keys the relay may sign with for MCP callers
    bind: str = DEFAULT_BIND
    title: str = "Exo Bus"
    mcp: bool = True
    web_bodies: bool = False  # the unauthenticated web view at / shows plaintext bodies only when this is set

    @classmethod
    def from_env(cls, environ=None) -> "RelayConfig":
        home = bus_home(environ)
        kd = env("KEYS_DIR", environ=environ)
        return cls(
            db=Path(os.path.expanduser(env("DB", str(home / "relay.sqlite"), environ))),
            registry=Path(os.path.expanduser(env("REGISTRY", str(home / "registry.json"), environ))),
            leader_pubkey=env("LEADER_PUBKEY", None, environ),
            relay_key=env("RELAY_KEY", None, environ),
            keys_dir=Path(os.path.expanduser(kd)) if kd else None,
            bind=env("BIND", DEFAULT_BIND, environ),
            title=env("TITLE", "Exo Bus", environ),
            mcp=env("MCP", "1", environ) not in ("0", "false", "no"),
            web_bodies=(env("WEB_BODIES", "", environ) or "").lower() in ("1", "true", "yes"),
        )
