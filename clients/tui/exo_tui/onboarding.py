"""First run, tutorial hints and their state (D-030). Client side only: no model tokens, nothing in the transcript
the model sees.

State file: ``$EXO_HOME/state/onboarding.json`` =
``{"onboarded": <iso time or "skipped">, "intro_started": <iso time>, "hints_seen": [...]}``.
The home counts as onboarded once ``onboarded`` is set. ``intro_started`` records that the automatic first-run intro
turn began, so one that fails or gets no reply is not sent again at every launch. Tutorial mode itself (on or off) is
the config value ``tutorial.enabled`` in config.json, because the core reads it to add its one system line.
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
INTRO_SKILL = "exo-intro"
# Shown by the client, with no model turn, when the core has no exo-intro skill: the same facts, short (D-030).
ORIENTATION = (
    "Exo is an agent harness: it runs a model, gives it tools and keeps its memory. Every window, channel and "
    "scheduled run is a thread of the same agent, so a second window already knows what the first one did.\n\n"
    "You do not manage context. Everything said is kept in one log, and old stretches fold into a summary tree in "
    "the background, so there is no limit to hit and nothing to compact. /context shows what the next call carries.\n\n"
    "Three things for day one:\n"
    "- Type while the agent works: your message steers it at the next tool round. ctrl+c or /stop stops a turn.\n"
    "- An approval gate guards tools that run commands, edit files or leave the machine. On a card: o once, "
    "a always, d deny.\n"
    "- /help lists every command. /model picks a model for this thread, /providers sets up a key.\n\n"
    "/tutorial turns short tips on or off: they appear the first time something comes up.")
ORIENTATION_NOTE = ("The exo-intro skill is not installed, so this is the short version. Run scripts/install.sh from "
                    "a checkout of the repository for the full, interactive one.")


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

    @property
    def intro_due(self) -> bool:
        """The automatic intro should run: not onboarded, and no earlier automatic intro turn ever started."""
        data = self._read()
        return not data.get("onboarded") and not data.get("intro_started")

    def mark_intro_started(self) -> None:
        data = self._read()
        if not data.get("intro_started"):
            data["intro_started"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._write(data)

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
