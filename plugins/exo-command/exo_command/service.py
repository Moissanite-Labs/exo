"""One façade for every panel. Each section is computed on its own and fails on its own, so one
unreadable database never blanks the whole command centre. Results are cached briefly per
section; the projects scan is the heaviest and gets a longer floor."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

from . import agents, costs, decisions, kanban, projects
from .config import Settings, load_settings
from .ro import discover_profiles

SECTIONS = ("projects", "agents", "kanban", "decisions", "costs")
PROJECTS_CACHE_FLOOR = 120


class CommandCentre:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or load_settings()
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    # -- sources
    def profiles(self):
        s = self.settings
        return discover_profiles(s.hermes_home, s.profiles, s.include_root_profile)

    # -- sections
    def _compute(self, name: str, now: Optional[float]) -> Dict[str, Any]:
        s = self.settings
        if name == "projects":
            return projects.board(self.profiles(), generic_paths=s.generic_paths,
                                  window_days=s.project_window_days, stall_days=s.project_stall_days,
                                  abandon_days=s.project_abandon_days, now=now)
        if name == "agents":
            return agents.live(self.profiles(), live_window=s.live_window_seconds,
                               past_hours=s.past_window_hours, now=now)
        if name == "kanban":
            return kanban.tasks(s.hermes_home, done_limit=s.kanban_done_limit)
        if name == "decisions":
            return decisions.read_queue(s.decisions_queue, now=now)
        if name == "costs":
            return costs.per_session(self.profiles(), window_days=s.cost_window_days,
                                     top=s.cost_top_sessions, now=now)
        raise KeyError(name)

    def section(self, name: str, *, now: Optional[float] = None, fresh: bool = False) -> Dict[str, Any]:
        if name not in SECTIONS:
            raise KeyError(name)
        ttl = self.settings.cache_seconds
        if name == "projects":
            ttl = max(ttl, PROJECTS_CACHE_FLOOR)
        t = time.time()
        with self._lock:
            hit = self._cache.get(name)
            if hit and not fresh and now is None and t - hit[0] < ttl:
                return hit[1]
        started = time.time()
        try:
            data = {"ok": True, **self._compute(name, now)}
        except Exception as exc:  # isolate: one bad section never blanks the page
            data = {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc),
                    "trace": traceback.format_exc(limit=3)}
        data["generated_at"] = time.time()
        data["took_ms"] = int((time.time() - started) * 1000)
        with self._lock:
            self._cache[name] = (t, data)
        return data

    def snapshot(self, *, now: Optional[float] = None, fresh: bool = False,
                 only: Optional[list] = None) -> Dict[str, Any]:
        names = [n for n in (only or SECTIONS) if n in SECTIONS]
        return {"settings": self.settings.public(),
                "profiles": [p.name for p in self.profiles()],
                **{n: self.section(n, now=now, fresh=fresh) for n in names}}

    def summary(self, now: Optional[float] = None) -> str:
        """Short plain-text digest for the slash command."""
        snap = self.snapshot(now=now, only=["decisions", "agents", "kanban", "projects"])
        lines = []
        d = snap["decisions"]
        if not d.get("configured"):
            lines.append("Decisions: no queue configured.")
        else:
            c = d.get("counts", {})
            lines.append("Decisions waiting: {} ({} overdue).".format(c.get("waiting", 0), c.get("overdue", 0)))
            for item in d.get("waiting", [])[:5]:
                lines.append("  - " + str(item.get("title")))
        a = snap["agents"]
        if a.get("ok"):
            c = a["counts"]
            lines.append("Live: {} sessions, {} subagents, {} background delegations.".format(
                c["active"], c["subagents"], c["delegations_active"]))
        k = snap["kanban"]
        if k.get("ok"):
            lines.append("Kanban: {} open, {} need a person.".format(k["open"], k["needs_attention"]))
        p = snap["projects"]
        if p.get("ok"):
            lanes = {l["id"]: len(l["cards"]) for l in p["lanes"]}
            lines.append("Projects: {} total, {} waiting on you, {} blocked, {} ongoing.".format(
                p["counts"]["projects"], lanes.get("waiting_on_you", 0), lanes.get("blocked", 0),
                lanes.get("ongoing", 0)))
        return "\n".join(lines)


_DEFAULT: Optional[CommandCentre] = None
_DEFAULT_LOCK = threading.Lock()


def default_centre() -> CommandCentre:
    """Process-wide instance for the dashboard and the slash command. Settings reload when the
    Hermes config changes them (cheap: load_settings reads the cached config)."""
    global _DEFAULT
    fresh = load_settings()
    with _DEFAULT_LOCK:
        if _DEFAULT is None or _DEFAULT.settings != fresh:
            _DEFAULT = CommandCentre(fresh)
        return _DEFAULT
