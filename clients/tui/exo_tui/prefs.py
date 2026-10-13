"""Per-user UI preferences for the terminal client (D-046). Client side only.

State file: ``$EXO_HOME/state/ui.json`` = ``{"layout": "inverse" | "classic", "exosphere": true | false}``.
``EXO_LAYOUT`` overrides the layout for one run, ``EXO_EXOSPHERE=on|off`` the corner exosphere (D-062, on by default).

The experiment (D-046) makes ``inverse`` the default. To flip the default back to classic, change ``DEFAULT_LAYOUT``
below to ``"classic"``; a saved choice (``/layout classic`` or ``/layout inverse``) always wins over the default.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

LAYOUTS = ("inverse", "classic")
DEFAULT_LAYOUT = "inverse"  # the one line to change to end the experiment


def state_path(home: str | os.PathLike) -> Path:
    return Path(home).expanduser() / "state" / "ui.json"


class Prefs:
    """Read and write the UI preferences of one EXO_HOME."""

    def __init__(self, home: str | os.PathLike) -> None:
        self.path = state_path(home)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def layout(self) -> str:
        env = os.environ.get("EXO_LAYOUT", "").strip().lower()
        if env in LAYOUTS:
            return env
        saved = self._read().get("layout")
        return saved if saved in LAYOUTS else DEFAULT_LAYOUT

    def exosphere(self) -> bool:
        env = os.environ.get("EXO_EXOSPHERE", "").strip().lower()
        if env in ("on", "off"):
            return env == "on"
        saved = self._read().get("exosphere")
        return saved if isinstance(saved, bool) else True

    def set_exosphere(self, on: bool) -> None:
        self._write("exosphere", bool(on))

    def set_layout(self, name: str) -> None:
        if name not in LAYOUTS:
            raise ValueError(name)
        self._write("layout", name)

    def _write(self, key: str, value: object) -> None:
        data = self._read()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".ui.", dir=self.path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
