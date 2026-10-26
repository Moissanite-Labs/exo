"""Permission modes for the gate: ``ask`` (the default) and ``bypass`` (D-065, docs/GATE.md "Bypass mode").

In ``bypass`` the gate turns an ``ask`` into an ``allow`` so the agent never waits for an answer. It is never silent:
every such call is written to the signed ledger with ``decided_by: bypass`` and the call itself, and the clients show
a banner while it is on. Three things are not lifted:

- the floor: writes to Exo's own records (``store_write``: policies, ledger, keys, grants, seat files, the core
  sockets) and reading secrets (``credentials``) still ask the principal, every time;
- ``deny`` rules (and a decision-model deny) still block, as in Claude Code where deny rules hold in every mode;
- a public post that breaks the posting rules (an internal name, a home path, a model id) is held back with the
  list of fixes instead of asked about, so the agent rewrites the text and tries again. Nobody is waiting on it.

Who may switch it on: only a live principal connection (``exo.principal.require_principal``), through
``/permissions bypass`` or the launch flag ``exo --skip-permissions`` (which sends that same command), or the
principal's seat file read once when the core starts (``[permissions] mode = "bypass"``). A seat file is a store
path, so writing one is ``store_write``: bypass itself cannot be used to make bypass the default. A tool, peer,
bus message or the agent reaches the core as a local caller and is refused. Anyone may switch it off: that only
tightens. A required executor (Exo OS) refuses bypass. State is in memory: a restarted core starts in ``ask``.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from exo import gate

logger = logging.getLogger("exo_gate")

ASK, BYPASS = "ask", "bypass"
FLOOR = tuple(gate.NO_GRANT)  # credentials, store_write: asked in every mode
SUMMARY = "bypass auto-approves everything except changes to Exo's own records and reading secrets"
REQUIRED_REFUSAL = ("bypass is not available with a required tool executor (Exo OS): every call there must be "
                    "decided and committed to the ledger before it runs")
_CALL_LIMIT = 8000  # characters of the call kept in the ledger row; args_sha256 identifies the exact call


class Permissions:
    """The mode of this core run, with its counters. One per gate; not saved anywhere."""

    def __init__(self) -> None:
        self.mode = ASK
        self.since: Optional[float] = None
        self.by = ""
        self.via = ""
        self.auto_approved = 0
        self.held = 0         # posting-rule violations held back instead of asked
        self.floor_asks = 0   # floor calls that still asked while bypass was on

    def set(self, mode: str, by: str, via: str, now: float) -> None:
        self.mode, self.by, self.via = mode, by, via
        self.since = now if mode == BYPASS else None

    def snapshot(self, available: bool) -> dict:
        return {"mode": self.mode, "available": available, "auto_approved": self.auto_approved,
                "held": self.held, "floor_asks": self.floor_asks, "by": self.by or None, "via": self.via or None,
                "since": _iso(self.since) if self.since else None, "floor": list(FLOOR), "summary": SUMMARY}


def seat_default(env: Mapping[str, str] | None = None) -> str:
    """``bypass`` when the seat file asks for it at start (``[permissions] mode = "bypass"``), else ``ask``.

    Read once, when the gate loads, from ``$EXO_HOME/seats``. A seat file is on the store list, so only the
    principal can write one (``store_write`` asks the principal in every mode, bypass included)."""
    env = os.environ if env is None else env
    seat = env.get("EXO_SEAT") or ""
    if not seat:
        return ASK
    try:
        from exo.core.seats import ID_RE, _candidates, _read
        if not ID_RE.match(seat):
            return ASK
        home = Path(env.get("EXO_HOME") or Path.home() / ".exo").expanduser()
        for p in _candidates(home, seat):
            if p.is_file():
                table = _read(p).get("permissions")
                mode = str(table.get("mode") or "").strip().lower() if isinstance(table, dict) else ""
                return BYPASS if mode == BYPASS else ASK
    except Exception:
        logger.warning("exo-gate: could not read the seat's permissions default; using ask", exc_info=True)
    return ASK


def recorded_call(tool: str, args: Mapping) -> dict:
    """The call as the ledger keeps it: tool and arguments, obvious secrets redacted, long values cut."""
    try:
        from exo.ingest.common import redact
        text = json.dumps(args, sort_keys=True, default=str)
        text, n = redact(text)
    except Exception:
        text, n = "(arguments could not be recorded)", 0
    out: dict[str, Any] = {"tool": tool, "args": text[:_CALL_LIMIT]}
    if len(text) > _CALL_LIMIT:
        out["truncated"] = len(text)
    if n:
        out["redacted"] = n
    return out


def status_text(p: Permissions, on: bool, available: bool) -> str:
    if not on:
        line = "permissions: ask. The gate asks you before risky calls."
        if available:
            line += " /permissions bypass turns asking off for this run of the core."
        else:
            line += " " + REQUIRED_REFUSAL + "."
        return line
    via = f" via {p.via}" if p.via else ""
    by = f" by {p.by}" if p.by else ""
    return (f"permissions: bypass since {_clock(p.since)}{by}{via}. {SUMMARY[0].upper() + SUMMARY[1:]}.\n"
            f"auto-approved {p.auto_approved} call{'s' if p.auto_approved != 1 else ''} this session; "
            f"held {p.held} post{'s' if p.held != 1 else ''} that broke the posting rules; "
            f"{p.floor_asks} call{'s' if p.floor_asks != 1 else ''} still asked (the floor). "
            "Deny rules still block. Every auto-approved call is in the ledger as decided_by: bypass.\n"
            "/permissions ask turns asking back on.")


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat(timespec="seconds")


def _clock(ts: Optional[float]) -> str:
    return _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M") if ts else "?"
