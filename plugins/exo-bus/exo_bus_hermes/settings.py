"""Settings and shared state for the exo-bus plugin. Reads the profile's scoped secrets when the
Hermes gateway is importable (multiplex safe), else the process environment."""
from __future__ import annotations

import json
import os
from pathlib import Path

from exo.bus import Client
from exo.bus.config import exo_home

NAMES = ("URL", "SEAT", "KEYS_DIR", "STATE_DIR", "POLL_SECONDS", "HOME_CHANNEL")


def setting(name: str, extra: dict | None = None, default: str = "") -> str:
    """`extra[name.lower()]`, else EXO_BUS_<NAME>, else legacy DIVAN_<NAME>, else default."""
    if extra and extra.get(name.lower()):
        return str(extra[name.lower()])
    try:
        from gateway.platforms._shared import get_scoped_secret
    except ImportError:
        get_scoped_secret = None
    for key in (f"EXO_BUS_{name}", f"DIVAN_{name}"):
        value = get_scoped_secret(key, "") if get_scoped_secret else os.environ.get(key, "")
        if value:
            return str(value).strip()
    return default


def configured(extra: dict | None = None) -> bool:
    return bool(setting("URL", extra) and setting("SEAT", extra))


def make_client(extra: dict | None = None) -> Client:
    keys_dir = setting("KEYS_DIR", extra) or None
    return Client(setting("URL", extra), setting("SEAT", extra), keys_dir=keys_dir)


class Cursor:
    """The last inbox seq this seat has handled, kept in a small JSON file."""

    def __init__(self, seat: str, extra: dict | None = None):
        base = setting("STATE_DIR", extra) or str(exo_home() / "bus")
        self.path = Path(os.path.expanduser(base)) / f"cursor-{seat}.json"

    def get(self) -> int:
        try:
            return int(json.loads(self.path.read_text())["since"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    def set(self, seq: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"since": int(seq)}))
        tmp.replace(self.path)
