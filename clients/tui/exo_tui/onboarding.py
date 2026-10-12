"""First run, tutorial hints and their state (D-030). Client side only: no model tokens, nothing in the transcript
the model sees.

State file: ``$EXO_HOME/state/onboarding.json`` = ``{"onboarded": <iso time or "skipped">, "hints_seen": [...]}``.
The home counts as onboarded once ``onboarded`` is set. Tutorial mode itself (on or off) is the config value
``tutorial.enabled`` in config.json, because the core reads it to add its one system line.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

CATALOG = Path(__file__).with_name("hints.toml")
INTRO_PROMPT = (
    "/intro\n\n"
    "The user ran /intro (or this is their first launch of Exo). Load the exo-intro skill with the skill tool and "
    "give them an interactive orientation, taught from that skill's facts. Rules: be short and staged. Start with a "
    "brief overview, then ask what they are used to (Claude Code, ChatGPT, Hermes, something else) and tailor "
    "the rest to it, and offer to go deeper on any part; one topic per reply. Let them ask questions. End by "
    "mentioning /tutorial (tips that appear the first time something comes up).")


def state_path(home: str | os.PathLike) -> Path:
    return Path(home).expanduser() / "state" / "onboarding.json"


def load_catalog() -> dict[str, dict[str, str]]:
    return tomllib.loads(CATALOG.read_text(encoding="utf-8"))


class Onboarding:
    """Read and write the onboarding state of one EXO_HOME."""

    def __init__(self, home: str | os.PathLike) -> None:
        self.path = state_path(home)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".onboarding.", dir=self.path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    @property
    def onboarded(self) -> bool:
        return bool(self._read().get("onboarded"))

    def mark_onboarded(self, how: str = "") -> None:
        data = self._read()
        data["onboarded"] = how or datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._write(data)

    def seen(self) -> set[str]:
        return {str(k) for k in self._read().get("hints_seen", [])}

    def mark_seen(self, key: str) -> None:
        data = self._read()
        got = list(data.get("hints_seen", []))
        if key not in got:
            data["hints_seen"] = got + [key]
            self._write(data)

    def reset_hints(self) -> None:
        data = self._read()
        data["hints_seen"] = []
        self._write(data)
