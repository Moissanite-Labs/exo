"""Versioned deterministic scoring of already-verified ledger records. No I/O."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Iterable, Mapping

VERSION = 1
FORMS = ("egg", "cell", "tile", "cube", "frame", "d4", "d5", "d6", "d7", "d8")
GATES = (1, 6, 16, 30, 45, 60, 72, 84, 92, 99)
LIMITATIONS = ["delegation_completion_not_recorded", "unlinked_promotion_notes_are_not_scored"]


def xp_for_level(level: int) -> int:
    if type(level) is not int or not 1 <= level <= 99:
        raise ValueError("level must be an integer from 1 to 99")
    return round(100000 * ((level - 1) / 98) ** 2)


def _time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _agent(author: Any, principals: frozenset[str]) -> bool:
    if not isinstance(author, str) or not author or author in principals:
        return False
    reserved = ("tool", "job", "system", "delegate", "schedule", "local", "unknown", "user", "bot")
    return author not in reserved and not author.startswith(tuple(name + ":" for name in (*reserved, "peer", "bus")))


def score(events: Iterable[Mapping[str, Any]], principals: Iterable[str], *,
          hatch_at: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    """Append-order replay; timestamps choose UTC caps, not processing order.

    Principal author eligibility is provided by the verified reader, never by body fields.
    Historical promotion count records distinct births, not active beliefs after reverts.
    Runtime supplies no hatch date. The explicit test input exercises later gate semantics.
    """
    who = frozenset(principals)
    seen: set[str] = set()
    users: dict[str, tuple[str, bytes, datetime]] = {}
    pending: dict[str, str] = {}
    answered: set[str] = set()
    repeated: dict[bytes, datetime] = {}
    turns: Counter[str] = Counter()
    proposals: dict[str, str] = {}
    accepted: dict[str, str] = {}
    learned: set[str] = set()
    day_raw: Counter[str] = Counter()
    day_units: Counter[str] = Counter()
    latest: datetime | None = None

    def award(weight: int, at: datetime) -> None:
        day = at.date().isoformat()
        used = day_raw[day]
        full = max(0, min(weight, 600 - used))
        half = max(0, min(weight - full, 1400 - max(used, 600)))
        day_units[day] += full * 2 + half
        day_raw[day] += weight

    for event in events:
        ident, body = event.get("id"), event.get("body")
        at = _time(event.get("ts"))
        if not isinstance(ident, str) or not ident or ident in seen or at is None or not isinstance(body, Mapping):
            continue
        seen.add(ident)
        latest = max(latest, at) if latest else at
        kind, principal = event.get("kind"), isinstance(event.get("author"), str) and event.get("author") in who
        sid = event.get("session")
        refs = event.get("refs") or []
        refs = [ref for ref in refs if isinstance(ref, str)] if isinstance(refs, (list, tuple)) else []
        text = body.get("text")
        if kind == "utterance" and any(mark in body for mark in ("source", "origin", "src", "source_ts", "imported", "copied", "forked_from")):
            continue
        if kind == "utterance" and isinstance(sid, str) and sid and isinstance(text, str) and text.strip():
            if principal and body.get("role") == "user":
                digest = hashlib.sha256(" ".join(text.split()).encode("utf-8")).digest()
                users[ident] = (sid, digest, at)
                pending[sid] = ident
            elif _agent(event.get("author"), who) and body.get("role") == "assistant":
                target = next((ref for ref in refs if ref in users and users[ref][0] == sid), None)
                target = target or (pending.get(sid) if not refs else None)
                if target and target not in answered:
                    answered.add(target)
                    _, digest, said_at = users[target]
                    previous = repeated.get(digest)
                    repeated[digest] = max(previous, said_at) if previous else said_at
                    if previous is None or said_at - previous >= timedelta(hours=24):
                        award(10, at)
                        turns[sid] += 1
                        if turns[sid] == 3:
                            award(5, at)
        elif principal and kind == "verdict" and isinstance(body.get("target"), str) and body["target"] in seen:
            verdict = body.get("verdict")
            weight = {"accept": 4, "reject": 4, "edit": 8, "should_have_been": 8}.get(verdict if isinstance(verdict, str) else "", 0)
            award(weight, at)
        elif principal and kind in ("ruling", "answer") and isinstance(text, str) and text.strip():
            award(8 if kind == "ruling" else 6, at)
        elif kind == "belief_proposed" and _agent(event.get("author"), who) and isinstance(body.get("proposal_id"), str):
            proposals[ident] = body["proposal_id"]
        elif kind == "belief_accepted" and (principal or _agent(event.get("author"), who)) and isinstance(body.get("proposal_id"), str):
            pid = body["proposal_id"]
            if pid and any(proposals.get(ref) == pid for ref in refs):
                accepted[ident] = pid
                if pid not in learned:
                    learned.add(pid)
                    award(15, at)
        elif kind == "note" and body.get("type") == "learn_promotion":
            pid = body.get("proposal_id")
            # A note alone cannot invent knowledge. The accepted event normally already earned the award.
            if isinstance(pid, str) and pid and any(accepted.get(ref) == pid for ref in refs) and pid not in learned:
                learned.add(pid)
                award(15, at)

    units = sum(day_units.values())
    xp = units // 2 if units % 2 == 0 else units / 2
    level = max(value for value in range(1, 100) if xp >= xp_for_level(value))
    earned = max(index for index, gate in enumerate(GATES) if level >= gate)
    form = 0
    hatch, until = _time(hatch_at), _time(as_of) if as_of else latest
    if hatch and until and hatch <= until:
        cumulative, reached = 0, {}
        for day, amount in sorted(day_units.items()):
            cumulative += amount
            for index in range(1, earned + 1):
                if index not in reached and cumulative >= xp_for_level(GATES[index]) * 2:
                    reached[index] = datetime.fromisoformat(day).replace(tzinfo=timezone.utc).date()
        previous_day = hatch.date() - timedelta(days=1)
        for index in range(1, earned + 1):
            eligible = max(hatch.date(), reached[index], previous_day + timedelta(days=1))
            if eligible > until.date():
                break
            form, previous_day = index, eligible
    next_index = min(form + 1, len(FORMS) - 1)
    threshold = xp_for_level(GATES[next_index])
    base = xp_for_level(GATES[form])
    progress = min(1.0, max(0.0, (xp - base) / max(1, threshold - base))) if form < 9 else 1.0
    return {"personal": {"xp": xp, "xp_half_units": units, "level": level, "earned_form": FORMS[earned]},
            "form": FORMS[form], "next_form": FORMS[next_index] if form < 9 else None,
            "next_form_xp": threshold if form < 9 else None, "progress": progress, "learned_count": len(learned)}
