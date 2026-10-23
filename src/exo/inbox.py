"""Bounded, local operations snapshot (D-085); no runtime or transport is started."""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import stat
import threading
import time
import unicodedata
from functools import partial
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from exo.core.sessions import SessionStore

SECTIONS = ("sessions", "tasks", "jobs", "schedules", "questions", "approvals", "findings")
QUESTION_BYTES = 2 * 1024 * 1024
QUESTION_EVENTS = 5000
APPROVAL_EVENTS = 1000
ROW_BYTES = 64 * 1024


class BudgetExceeded(ValueError):
    pass


def short(value: Any) -> str:
    """Plain terminal text, including when a stored title contains control characters."""
    if isinstance(value, str):
        text = value[:640]
    elif isinstance(value, (int, float, bool)):
        text = str(value)[:640]
    else:
        text = "" if value is None else "[invalid value]"
    return " ".join("".join(c if not unicodedata.category(c).startswith("C") else " "
                            for c in text).split())[:160]


def timestamp(value: Any) -> str | int | float | None:
    """Accept bounded ISO timestamps or finite epoch seconds, never arbitrary source text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    if isinstance(value, str) and len(value) <= 64:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return None


class Snapshot(SessionStore):
    """Use SessionStore's read API without its schema creation, migrations or private_file writes."""

    def __init__(self, path: Path, deadline: float):
        self.path, self.deadline = str(path), deadline
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = self._open()

    def _open(self) -> sqlite3.Connection:
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("query time budget exhausted")
        if not Path(self.path).is_file():
            raise FileNotFoundError(self.path)
        db = sqlite3.connect(Path(self.path).absolute().as_uri() + "?mode=ro", uri=True,
                             timeout=0.05, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, ROW_BYTES)
        db.execute("PRAGMA query_only=ON")
        db.set_progress_handler(lambda: int(time.monotonic() >= self.deadline), 1000)
        db.execute("BEGIN")
        return db

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("query time budget exhausted")
        return super()._q(sql, args)

    def select(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._q(sql, args)]


def item(kind: str, ident: Any, title: Any, recorded: Any, *, terminal: bool = False,
         updated: Any = None, session: Any = None) -> dict[str, Any]:
    return {"kind": kind, "id": short(ident), "title": short(title),
            "state": short(recorded) if terminal else "unknown", "recorded_state": short(recorded),
            "updated_at": timestamp(updated), "session": short(session) if session is not None else None}


def section(rows: list[dict[str, Any]], limit: int, note: str = "") -> dict[str, Any]:
    return {"status": "partial" if len(rows) > limit else "ok", "source": "persisted",
            "truncated": len(rows) > limit, "note": note, "items": rows[:limit]}


def unknown(note: str) -> dict[str, Any]:
    return {"status": "unknown", "source": "persisted", "truncated": False, "note": note, "items": []}


def _sessions(db: Snapshot, agent: str, limit: int) -> dict[str, Any]:
    rows = [item("session", s.id, s.title, db.status(s.id), updated=s.updated, session=s.id)
            for s in db.list_sessions(limit + 1, agent=agent)]
    return section(rows, limit, "Recent threads; running and waiting state require the live client.")


def _tasks(db: Snapshot, agent: str, limit: int) -> dict[str, Any]:
    rows = db.select("SELECT id,parent,title,status,updated FROM tasks WHERE parent IN "
                     "(SELECT id FROM sessions WHERE agent=?) OR child IN "
                     "(SELECT id FROM sessions WHERE agent=?) ORDER BY updated DESC,id LIMIT ?",
                     (agent, agent, limit + 1))
    return section([item("task", r["id"], r["title"], r["status"],
                         terminal=r["status"] in ("done", "failed", "cancelled"),
                         updated=r["updated"], session=r["parent"]) for r in rows], limit,
                   "Nonterminal task records cannot establish whether a worker is still running.")


def _jobs(db: Snapshot, agent: str, limit: int) -> dict[str, Any]:
    rows = db.select("SELECT id,session,state,started,ended FROM jobs WHERE agent=? "
                     "ORDER BY (state='running') DESC,started DESC,id LIMIT ?", (agent, limit + 1))
    return section([item("job", r["id"], "Background shell job", r["state"],
                         terminal=r["state"] in ("done", "failed", "killed", "timeout", "lost"),
                         updated=r["ended"] or r["started"], session=r["session"]) for r in rows], limit,
                   "Commands, logs and PIDs are omitted; process liveness is not inferred.")


def _findings(db: Snapshot, agent: str, limit: int) -> dict[str, Any]:
    rows = db.select("SELECT id,title,status,last_seen,session FROM findings WHERE session IN "
                     "(SELECT id FROM sessions WHERE agent=?) AND status IN ('open','reopened','fixed') "
                     "ORDER BY last_seen DESC,id LIMIT ?", (agent, limit + 1))
    return section([item("finding", r["id"], r["title"], r["status"], terminal=True,
                         updated=r["last_seen"], session=r["session"]) for r in rows], limit,
                   "Unverified findings whose latest recorded session belongs to this agent; global findings omitted.")


def _approvals(db: Snapshot, agent: str, limit: int) -> dict[str, Any]:
    rows = db.select("SELECT seq,session,type,ts,substr(body,1,16384) AS body FROM events WHERE session IN "
                     "(SELECT id FROM sessions WHERE agent=?) AND type IN "
                     "('approval.requested','approval.resolved') ORDER BY seq DESC LIMIT ?",
                     (agent, APPROVAL_EVENTS + 1))
    pending = {}
    for r in reversed(rows[:APPROVAL_EVENTS]):
        body = json.loads(r["body"])
        rid = body["request_id"]
        if r["type"] == "approval.requested":
            pending[rid] = item("approval", rid, body.get("summary"), "requested",
                                updated=r["ts"], session=r["session"])
        else:
            pending.pop(rid, None)
    result = section(list(reversed(list(pending.values()))), limit)
    result.update(status="unknown", truncated=len(rows) > APPROVAL_EVENTS or result["truncated"],
                  note="Historical unresolved requests only; the live approvals list is authoritative. "
                       "An empty replay does not establish that no approvals are waiting.")
    return result


def _schedules(db: Snapshot, agent: str, limit: int) -> dict[str, Any]:
    rows = db.select('SELECT id,title,seat,paused,last_run,next_run,last_status FROM jobs WHERE seat=? '
                     'OR seat IS NULL OR seat=\'\' ORDER BY paused,next_run,id LIMIT ?', (agent, limit + 1))
    items = [{**item("schedule", r["id"], r["title"], "paused" if r["paused"] else "enabled",
                    terminal=bool(r["paused"]), updated=r["last_run"]),
              "next_run": timestamp(r["next_run"]), "last_status": short(r["last_status"]),
              "seat": short(r["seat"]) if r["seat"] is not None else None} for r in rows]
    return section(items, limit, "Stored schedules; due time does not prove a missed run. Unbound schedules are shared.")


def _questions(root: Path, limit: int, deadline: float) -> dict[str, Any]:
    from exo.ask import OPS, QUEUE_REL, QuestionQueue

    path = root / QUEUE_REL
    # Replay the whole bounded file: a tail alone could miss an earlier proposal or its answer.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("question queue is not a regular file")
        data = stream.read(QUESTION_BYTES + 1)
    if len(data) > QUESTION_BYTES:
        raise BudgetExceeded("question queue exceeds the 2 MiB replay budget")
    events, proposed = [], set()
    for line in data.decode("utf-8").splitlines():
        if time.monotonic() >= deadline:
            raise BudgetExceeded("query time budget exhausted")
        if not line.strip():
            continue
        ev = json.loads(line)
        if (not isinstance(ev, dict) or ev.get("op") not in OPS
                or not isinstance(ev.get("id"), str) or not ev["id"]):
            raise ValueError("invalid question event")
        if ev["op"] == "proposed":
            proposed.add(ev["id"])
        elif ev["id"] not in proposed:
            raise ValueError("question history lacks its proposal")
        events.append(ev)
        if len(events) > QUESTION_EVENTS:
            raise BudgetExceeded("question queue exceeds the 5000-event replay budget")

    class QueueSnapshot(QuestionQueue):
        def events(self) -> list[dict]:
            return events

    questions = QueueSnapshot(root, policy={}).state().values()
    rows = [item("question", q.id, q.text, q.status, terminal=True, updated=q.proposed_at)
            for q in questions if q.status in ("proposed", "asked")]
    rows.sort(key=lambda r: (r["recorded_state"] != "asked", r["updated_at"] or "", r["id"]))
    return section(rows, limit, "Open questions from this store only; no ledger, ranking refresh or asked marker writes.")


def collect(home: Path, agent: str, question_root: Path | None = None, *, limit: int = 20,
            timeout: float = 2.0) -> dict[str, Any]:
    """One bounded observation. A failed section never becomes a successful empty section."""
    if not 1 <= limit <= 100 or not 0.1 <= timeout <= 10:
        raise ValueError("limit must be 1..100 and timeout 0.1..10 seconds")
    deadline = time.monotonic() + timeout
    result: dict[str, Any] = {"version": 1, "observed_at": time.time(),
                             "scope": {"home": str(home), "agent": agent,
                                       "question_store": str(question_root) if question_root else None},
                             "live_state": "unknown", "limit_per_section": limit, "sections": {}}

    def read(name: str, fn: Callable[[], dict[str, Any]]) -> None:
        try:
            result["sections"][name] = fn()
        except BudgetExceeded as exc:
            result["sections"][name] = unknown(str(exc))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
            result["sections"][name] = unknown("Source missing, unreadable, busy or incompatible; no state inferred.")

    for filename, readers in (("sessions.sqlite", {"sessions": _sessions, "tasks": _tasks, "jobs": _jobs,
                                                  "approvals": _approvals, "findings": _findings}),
                              ("schedule.sqlite", {"schedules": _schedules})):
        try:
            db = Snapshot(home / filename, deadline)
        except (OSError, sqlite3.Error, BudgetExceeded):
            for name in readers:
                result["sections"][name] = unknown("Database unavailable; no state inferred.")
            continue
        try:
            for name, reader in readers.items():
                read(name, partial(reader, db, agent, limit))
        finally:
            db.close()
    if question_root is None:
        result["sections"]["questions"] = unknown("No selected principal store.")
    else:
        read("questions", lambda: _questions(question_root, limit, deadline))
    return result


def render(report: dict[str, Any]) -> str:
    scope = report["scope"]
    lines = [f"Operations inbox — agent {short(scope['agent'])}", f"Home: {short(scope['home'])}",
             f"Question store: {short(scope['question_store']) or 'none selected'}",
             "Persisted snapshot. Live state is unknown."]
    for name in SECTIONS:
        part = report["sections"][name]
        suffix = "; more records omitted" if part["truncated"] else ""
        lines.append(f"\n{name}: {part['status']} ({len(part['items'])} shown){suffix}")
        for row in part["items"]:
            state = row["state"]
            if state == "unknown":
                state += f" (recorded {row['recorded_state']})"
            lines.append(f"  {row['id']}  {state}  {row['title']}")
        if part["note"]:
            lines.append("  " + part["note"])
    return "\n".join(lines)


def register(sp: Any, common: Any) -> None:
    p = sp.add_parser("inbox", parents=[common], help="read a bounded local operations snapshot")
    p.add_argument("--json", action="store_true", help="print the versioned snapshot and source coverage")
    p.add_argument("--agent", default=os.environ.get("EXO_SEAT") or "default", help="agent in this Exo home")
    p.add_argument("--limit", type=int, choices=range(1, 101), metavar="1..100", default=20)
    p.add_argument("--timeout", type=float, default=2.0, help="read budget in seconds, 0.1..10 (default: 2)")


def cmd_inbox(a: argparse.Namespace) -> int:
    from exo.config import Config
    from exo.store import exo_home, principal_root

    home = exo_home()
    cfg = Config.load(home)
    principal = a.principal or cfg.principal
    root = Path(a.root).expanduser() if a.root else principal_root(principal, home) if principal else None
    try:
        report = collect(home, a.agent, root, limit=a.limit, timeout=a.timeout)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(report, indent=2, ensure_ascii=False) if a.json else render(report))
    return 0
