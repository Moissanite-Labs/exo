"""Session permission-mode draft (D-065, issue #96).

Adapted from mozi and Claude Opus 5.5, source commit 0239ecda82259ae07ede222ecaac8b167a46e908.

The existing approval desk binds consent to the session. Principal responses use
transport authentication (D-039/D-060); seat signatures remain exo.approval.v1.
No seat file or persistent default enables this mode.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from exo import gate

ASK, BYPASS = "ask", "bypass"
FLOOR = tuple(gate.NO_GRANT)
SUMMARY = "session bypass lowers ordinary asks; protected actions and decision-model asks still require approval"
REQUIRED_REFUSAL = "bypass is not available with a required tool executor (Exo OS)"
MISSING_APPROVAL = "this host cannot request session-bound mode approval"


@dataclass
class Permissions:
    """One session's in-memory status; never persisted or inherited by another session."""

    mode: str = ASK
    since: float | None = None
    by: str = ""
    via: str = ""
    auto_approved: int = 0
    revision: int = 0
    consent_id: str = ""
    floor_asks: int = 0

    def set(self, mode: str, by: str, via: str, now: float, consent_id: str = "") -> None:
        self.mode, self.by, self.via = mode, by, via
        self.since = now if mode == BYPASS else None
        self.consent_id = consent_id if mode == BYPASS else ""

    def snapshot(self, session_id: str = "", available: bool = True) -> dict:
        return {"mode": self.mode, "session": session_id or None, "available": available, "auto_approved": self.auto_approved,
                "floor_asks": self.floor_asks, "by": self.by or None,
                "via": self.via or None, "consent_id": self.consent_id or None, "since": _iso(self.since) if self.since else None,
                "floor": list(FLOOR), "summary": SUMMARY}


def protected(c: gate.Classification, d: gate.Decision, verdict: str | None = None,
              policies: dict | None = None) -> bool:
    """Conservative draft boundary, including every nested compound part.

    Retain any irreversible, public or third-party ask, not only P11's conjunction.
    Never lower a model ask, reserved hit, posting check, classification signal or nonordinary rule.
    """
    if (d.outcome != "ask" or verdict in ("ask", "deny") or d.reserved or d.violations
            or d.rule not in ("matrix", "category", "rule")
            or d.exposure == "public" or d.third_party or gate.body_unseen(c)):
        return True
    escalated = ((policies or {}).get("gates") or {}).get("escalate", {}).get("categories", [])
    pending, seen = [c], set()
    while pending:
        part = pending.pop()
        if id(part) in seen:
            continue
        seen.add(id(part))
        if (part.category in (*FLOOR, "opaque_exec", "unknown")
                or part.reversibility == "irreversible" or part.exposure == "public" or part.third_party
                or part.cost != 0 or part.category in escalated or part.signals):
            return True
        pending.extend(part.parts)
    return False


def status_text(p: Permissions, session_id: str = "") -> str:
    return (f"permissions: {p.mode}. {SUMMARY}. "
            f"Auto-approved {p.auto_approved} calls in this session. "
            "/permissions ask restores ordinary approval prompts.")


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat(timespec="seconds")
