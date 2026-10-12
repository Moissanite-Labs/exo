"""Importer for Hermes dashboard data: read-only readers over a Hermes home.

Hermes keeps sessions, subagent delegations, kanban boards and per-session cost in SQLite files
(``state.db`` per profile, ``kanban.db`` per board). The Exo command centre no longer reads them
live: its data comes from the Exo protocol (docs/STANDALONE.md section 4). These readers stay
for migration: they turn a Hermes home into plain dicts that an importer can replay into Exo
sessions, tasks and usage records, and the command centre's project clustering can scan them.

Every database opens through ``open_ro``: a ``file:<path>?mode=ro`` URI plus ``PRAGMA query_only=ON``.
When a WAL database cannot be opened read-only (no ``-shm`` file and no right to create one) the
fallback adds ``immutable=1``: still read-only, but it skips the WAL, so the result is flagged as a
snapshot. Nothing here writes to a Hermes database.

Sections: read-only access (``ro``), live agents (``live_agents``), kanban (``kanban_tasks``) and
cost per session (``cost_per_session``).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import quote

ROOT_PROFILE = "default"


def ro_uri(path: os.PathLike | str, immutable: bool = False) -> str:
    abs_path = os.path.abspath(os.path.expanduser(str(path)))
    uri = "file:{}?mode=ro".format(quote(abs_path, safe="/"))
    if immutable:
        uri += "&immutable=1"
    return uri


class RoConnection:
    """A read-only connection plus whether it had to fall back to an immutable snapshot."""

    def __init__(self, conn: sqlite3.Connection, snapshot: bool):
        self.conn = conn
        self.snapshot = snapshot

    def execute(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def close(self) -> None:
        self.conn.close()


def _connect(uri: str) -> sqlite3.Connection:
    if "mode=ro" not in uri:
        raise ValueError("refusing to open a Hermes database without mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    # Force a real read so a WAL file that cannot be mapped fails here, not mid-query.
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def open_ro(path: os.PathLike | str) -> RoConnection:
    if not os.path.exists(os.path.expanduser(str(path))):
        raise FileNotFoundError(str(path))
    try:
        return RoConnection(_connect(ro_uri(path)), snapshot=False)
    except sqlite3.OperationalError:
        return RoConnection(_connect(ro_uri(path, immutable=True)), snapshot=True)


@contextmanager
def ro(path: os.PathLike | str) -> Iterator[RoConnection]:
    c = open_ro(path)
    try:
        yield c
    finally:
        c.close()


def columns(conn: RoConnection, table: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute('PRAGMA table_info("{}")'.format(table.replace('"', "")))]
    except sqlite3.Error:
        return []


def select_cols(wanted: Sequence[str], have: Sequence[str]) -> List[str]:
    hs = set(have)
    return [c for c in wanted if c in hs]


# --------------------------------------------------------------------------- discovery

@dataclass
class ProfileDb:
    name: str
    path: Path


def discover_profiles(home: Path, only: Sequence[str] = (), include_root: bool = True) -> List[ProfileDb]:
    """``<home>/state.db`` as ``default`` plus ``<home>/profiles/<name>/state.db``, sorted.

    ``only`` filters by name when non-empty. Names are taken verbatim from the directory.
    """
    out: List[ProfileDb] = []
    want = set(only or [])
    root_db = home / "state.db"
    if include_root and root_db.is_file() and (not want or ROOT_PROFILE in want):
        out.append(ProfileDb(ROOT_PROFILE, root_db))
    pdir = home / "profiles"
    if pdir.is_dir():
        for entry in sorted(pdir.iterdir(), key=lambda p: p.name):
            db = entry / "state.db"
            if entry.is_dir() and db.is_file() and (not want or entry.name in want):
                out.append(ProfileDb(entry.name, db))
    return out


def discover_boards(home: Path) -> List[Tuple[str, Path, Optional[Path]]]:
    """``(slug, kanban.db, board.json or None)``: ``default`` at ``<home>/kanban.db``, others
    under ``<home>/kanban/boards/<slug>/``. Mirrors Hermes's board layout."""
    out: List[Tuple[str, Path, Optional[Path]]] = []
    root_db = home / "kanban.db"
    if root_db.is_file() and root_db.stat().st_size > 0:
        meta = home / "kanban" / "boards" / "default" / "board.json"
        out.append(("default", root_db, meta if meta.is_file() else None))
    bdir = home / "kanban" / "boards"
    if bdir.is_dir():
        for child in sorted(bdir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name in ("default", "_archived") or child.name.startswith("."):
                continue
            db = child / "kanban.db"
            if db.is_file() and db.stat().st_size > 0:
                meta = child / "board.json"
                out.append((child.name, db, meta if meta.is_file() else None))
    return out


# ----------------------------------------------------------------------------
# live agents
# ----------------------------------------------------------------------------

OUTPUT_CAP = 1200
THINK_CAP = 600
TERMINAL_DELEGATION = {"completed", "complete", "done", "error", "errored", "failed", "cancelled",
                       "canceled", "interrupted", "timeout", "unknown"}

_AGENT_COLS = ("id", "source", "title", "model", "parent_session_id", "started_at", "ended_at",
         "end_reason", "last_activity_at", "last_activity_description", "message_count",
         "tool_call_count", "cwd", "estimated_cost_usd", "actual_cost_usd")


def _agent_status(row: dict, now: float, live_window: float) -> str:
    if row.get("ended_at"):
        reason = (row.get("end_reason") or "").lower()
        if any(k in reason for k in ("error", "fail", "exception")):
            return "errored"
        if any(k in reason for k in ("interrupt", "cancel", "kill", "timeout")):
            return "interrupted"
        return "done"
    last = row.get("last_activity_at") or row.get("started_at") or 0
    return "running" if now - float(last) <= live_window else "idle"


def _tail(c, sid: str) -> Dict[str, Optional[str]]:
    """Newest tool line, newest assistant output, newest reasoning, first user goal."""
    out: Dict[str, Optional[str]] = {"tool": None, "output": None, "thinking": None, "goal": None}
    mcols = set(columns(c, "messages"))
    if not {"role", "content"} <= mcols:
        return out
    reason_col = "reasoning" if "reasoning" in mcols else None
    try:
        if "tool_name" in mcols:
            r = c.execute("SELECT tool_name, substr(content,1,200) AS content FROM messages "
                          "WHERE session_id=? AND role='tool' AND tool_name IS NOT NULL "
                          "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if r:
                line = " ".join((r["content"] or "").split())[:160]
                out["tool"] = "{}: {}".format(r["tool_name"], line) if line else r["tool_name"]
        if out["tool"] is None and "tool_calls" in mcols:
            r = c.execute("SELECT tool_calls FROM messages WHERE session_id=? AND role='assistant' "
                          "AND tool_calls IS NOT NULL ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if r:
                out["tool"] = _tool_name(r["tool_calls"])
        sel = "substr(content,1,{}) AS content".format(OUTPUT_CAP)
        if reason_col:
            sel += ", substr({},1,{}) AS reasoning".format(reason_col, THINK_CAP)
        r = c.execute("SELECT {} FROM messages WHERE session_id=? AND role='assistant' "
                      "AND COALESCE(content,'')<>'' ORDER BY id DESC LIMIT 1".format(sel), (sid,)).fetchone()
        if r:
            out["output"] = r["content"]
            if reason_col:
                out["thinking"] = r["reasoning"]
        r = c.execute("SELECT substr(content,1,400) AS content FROM messages WHERE session_id=? "
                      "AND role='user' ORDER BY id ASC LIMIT 1", (sid,)).fetchone()
        if r:
            out["goal"] = " ".join((r["content"] or "").split())[:300]
    except sqlite3.Error:
        pass
    return out


def _tool_name(raw: Any) -> Optional[str]:
    try:
        calls = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(calls, list) and calls:
            fn = calls[-1].get("function") or {}
            return fn.get("name") or calls[-1].get("name")
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def _card(profile: str, row: dict, tail: Dict[str, Optional[str]], status: str) -> dict:
    cost = row.get("actual_cost_usd") or row.get("estimated_cost_usd")
    return {
        "profile": profile,
        "session_id": row.get("id"),
        "kind": "subagent" if (row.get("parent_session_id") or row.get("source") == "subagent") else "session",
        "parent_session_id": row.get("parent_session_id"),
        "source": row.get("source"),
        "title": row.get("title") or None,
        "goal": tail.get("goal") or row.get("title") or None,
        "model": row.get("model"),
        "status": status,
        "started_at": row.get("started_at"),
        "ended_at": row.get("ended_at"),
        "end_reason": row.get("end_reason"),
        "last_activity_at": row.get("last_activity_at") or row.get("started_at"),
        "activity": row.get("last_activity_description"),
        "messages": int(row.get("message_count") or 0),
        "tool_calls": int(row.get("tool_call_count") or 0),
        "tool": tail.get("tool"),
        "thinking": tail.get("thinking"),
        "output": tail.get("output"),
        "cost_usd": float(cost) if cost else None,
        "cwd": row.get("cwd"),
    }


def _delegations(c, profile: str, now: float, past_cutoff: float) -> List[dict]:
    have = columns(c, "async_delegations")
    if not have:
        return []
    cols = select_cols(("delegation_id", "parent_session_id", "origin_session", "state",
                        "dispatched_at", "completed_at", "updated_at", "task_json"), have)
    out = []
    try:
        rows = c.execute("SELECT {} FROM async_delegations WHERE COALESCE(completed_at, updated_at, "
                         "dispatched_at) >= ? ORDER BY dispatched_at DESC LIMIT 100".format(
                             ", ".join(cols)), (past_cutoff,)).fetchall()
    except sqlite3.Error:
        return []
    for r in rows:
        d = dict(r)
        goal, goals = None, []
        try:
            task = json.loads(d.get("task_json") or "{}")
            goal = task.get("goal")
            goals = [str(g)[:300] for g in (task.get("goals") or [])][:12]
        except (ValueError, AttributeError):
            pass
        state = (d.get("state") or "unknown").lower()
        out.append({
            "profile": profile,
            "delegation_id": d.get("delegation_id"),
            "parent_session_id": d.get("parent_session_id") or d.get("origin_session"),
            "state": state,
            "active": state not in TERMINAL_DELEGATION and not d.get("completed_at"),
            "goal": (goal or "")[:300] or None,
            "workers": len(goals) or 1,
            "goals": goals,
            "dispatched_at": d.get("dispatched_at"),
            "completed_at": d.get("completed_at"),
        })
    return out


def live_agents(profiles: Sequence[ProfileDb], *, live_window: int = 900, past_hours: int = 24,
         now: Optional[float] = None, past_limit: int = 40) -> dict:
    now = now if now is not None else time.time()
    past_cutoff = now - past_hours * 3600
    active: List[dict] = []
    past: List[dict] = []
    delegations: List[dict] = []
    sources: List[dict] = []
    for p in profiles:
        st = {"profile": p.name, "status": "ok", "snapshot": False}
        try:
            with ro(p.path) as c:
                st["snapshot"] = c.snapshot
                have = columns(c, "sessions")
                if not have:
                    st["status"] = "no sessions table"
                    sources.append(st)
                    continue
                cols = select_cols(_AGENT_COLS, have)
                act = "COALESCE(last_activity_at, started_at)" if "last_activity_at" in have else "started_at"
                ended = "ended_at" in have
                # live: not ended, moved within the window
                q_live = "SELECT {} FROM sessions WHERE {} >= ?{} ORDER BY {} DESC LIMIT 60".format(
                    ", ".join(cols), act, " AND ended_at IS NULL" if ended else "", act)
                for r in c.execute(q_live, (now - live_window,)):
                    row = dict(r)
                    active.append(_card(p.name, row, _tail(c, row["id"]), _agent_status(row, now, live_window)))
                # past: subagents that ended inside the past window
                if ended and "parent_session_id" in have:
                    q_past = ("SELECT {} FROM sessions WHERE ended_at IS NOT NULL AND ended_at >= ? "
                              "AND (parent_session_id IS NOT NULL OR source='subagent') "
                              "ORDER BY ended_at DESC LIMIT ?").format(", ".join(cols))
                    for r in c.execute(q_past, (past_cutoff, past_limit)):
                        row = dict(r)
                        past.append(_card(p.name, row, _tail(c, row["id"]), _agent_status(row, now, live_window)))
                delegations.extend(_delegations(c, p.name, now, past_cutoff))
        except (OSError, sqlite3.Error) as exc:
            st["status"] = "error: {}".format(exc)
        sources.append(st)
    active.sort(key=lambda a: -(a["last_activity_at"] or 0))
    past.sort(key=lambda a: -(a["ended_at"] or 0))
    past = past[:past_limit]
    return {
        "active": active,
        "past": past,
        "delegations": sorted(delegations, key=lambda d: (not d["active"], -(d["dispatched_at"] or 0))),
        "counts": {"active": len(active),
                   "running": sum(1 for a in active if a["status"] == "running"),
                   "subagents": sum(1 for a in active if a["kind"] == "subagent"),
                   "past": len(past),
                   "delegations_active": sum(1 for d in delegations if d["active"])},
        "live_window_seconds": live_window,
        "sources": sources,
    }


# ----------------------------------------------------------------------------
# kanban
# ----------------------------------------------------------------------------

STATUS_ORDER = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done")
OPEN_ATTENTION = ("blocked", "review", "triage")  # statuses that usually need a person

_KANBAN_COLS = ("id", "title", "body", "assignee", "status", "priority", "created_by", "created_at",
         "started_at", "completed_at", "last_failure_error", "block_kind", "last_heartbeat_at",
         "consecutive_failures", "session_id", "result")


def _board_name(slug: str, meta: Optional[Path]) -> str:
    if meta is None:
        return slug
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        return str(data.get("name") or slug)
    except (OSError, ValueError):
        return slug


def kanban_tasks(home: Path, *, done_limit: int = 20) -> dict:
    boards: List[dict] = []
    totals: Dict[str, int] = {s: 0 for s in STATUS_ORDER}
    for slug, db, meta in discover_boards(home):
        b = {"slug": slug, "name": _board_name(slug, meta), "status": "ok", "snapshot": False,
             "columns": [], "counts": {}}
        try:
            with ro(db) as c:
                b["snapshot"] = c.snapshot
                have = columns(c, "tasks")
                if not have:
                    b["status"] = "no tasks table"
                    boards.append(b)
                    continue
                cols = select_cols(_KANBAN_COLS, have)
                sel = ", ".join("substr(body,1,300) AS body" if k == "body" else
                                "substr(result,1,300) AS result" if k == "result" else k for k in cols)
                rows = [dict(r) for r in c.execute(
                    "SELECT {} FROM tasks WHERE status <> 'archived' ORDER BY "
                    "COALESCE(completed_at, started_at, created_at) DESC".format(sel))]
                comment_counts: Dict[str, int] = {}
                if columns(c, "task_comments"):
                    for r in c.execute("SELECT task_id, count(*) AS n FROM task_comments GROUP BY task_id"):
                        comment_counts[r["task_id"]] = r["n"]
        except (OSError, sqlite3.Error) as exc:
            b["status"] = "error: {}".format(exc)
            boards.append(b)
            continue
        by_status: Dict[str, List[dict]] = {}
        for t in rows:
            t["comments"] = comment_counts.get(t["id"], 0)
            by_status.setdefault(t.get("status") or "todo", []).append(t)
        extra = sorted(s for s in by_status if s not in STATUS_ORDER)
        for s in list(STATUS_ORDER) + extra:
            items = by_status.get(s, [])
            n = len(items)
            if s == "done":
                items = items[:done_limit]
            elif s != "done":
                items.sort(key=lambda t: (-(t.get("priority") or 0), -(t.get("created_at") or 0)))
            b["counts"][s] = n
            totals[s] = totals.get(s, 0) + n
            if n:
                b["columns"].append({"status": s, "total": n, "tasks": items})
        boards.append(b)
    attention = sum(totals.get(s, 0) for s in OPEN_ATTENTION)
    open_n = sum(v for k, v in totals.items() if k != "done")
    return {"boards": boards, "totals": totals, "open": open_n, "needs_attention": attention}


# ----------------------------------------------------------------------------
# cost per session
# ----------------------------------------------------------------------------

DAY = 86400.0
_COST_S_COLS = ("id", "title", "source", "model", "parent_session_id", "started_at", "last_activity_at",
           "message_count", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
           "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd", "cost_status", "billing_provider")
_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


def _row_cost(row: dict) -> float:
    actual = row.get("actual_cost_usd") or 0
    return float(actual) if actual and actual > 0 else float(row.get("estimated_cost_usd") or 0)


def cost_per_session(profiles: Sequence[ProfileDb], *, window_days: int = 7, top: int = 25,
                now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    since = now - window_days * DAY
    sessions: List[dict] = []
    by_profile: Dict[str, dict] = {}
    by_model: Dict[str, dict] = defaultdict(lambda: {"cost_usd": 0.0, "sessions": 0, "api_calls": 0,
                                                    **{t: 0 for t in _TOKENS}})
    sources: List[dict] = []
    available = False
    for p in profiles:
        st = {"profile": p.name, "status": "ok", "has_session_costs": False, "has_model_usage": False}
        try:
            with ro(p.path) as c:
                have = columns(c, "sessions")
                st["has_session_costs"] = "estimated_cost_usd" in have
                usage_cols = columns(c, "session_model_usage")
                st["has_model_usage"] = bool(usage_cols)
                if not (st["has_session_costs"] or usage_cols):
                    sources.append(st)
                    continue
                available = True
                cols = select_cols(_COST_S_COLS, have)
                act = "COALESCE(last_activity_at, started_at)" if "last_activity_at" in have else "started_at"
                rows = [dict(r) for r in c.execute(
                    "SELECT {} FROM sessions WHERE {} >= ?".format(", ".join(cols), act), (since,))]
                usage: Dict[str, List[dict]] = defaultdict(list)
                if usage_cols:
                    ucols = select_cols(("session_id", "model", "task", "api_call_count", *_TOKENS,
                                         "estimated_cost_usd", "actual_cost_usd", "cost_status"), usage_cols)
                    ids = [r["id"] for r in rows]
                    for i in range(0, len(ids), 500):
                        chunk = ids[i:i + 500]
                        q = "SELECT {} FROM session_model_usage WHERE session_id IN ({})".format(
                            ", ".join(ucols), ",".join("?" * len(chunk)))
                        for u in c.execute(q, chunk):
                            usage[u["session_id"]].append(dict(u))
                prof = by_profile.setdefault(p.name, {"profile": p.name, "cost_usd": 0.0, "sessions": 0,
                                                      "priced_sessions": 0, **{t: 0 for t in _TOKENS}})
                for r in rows:
                    u_rows = usage.get(r["id"], [])
                    cost = _row_cost(r)
                    if not cost and u_rows:
                        cost = sum(_row_cost(u) for u in u_rows)
                    models = sorted({u["model"] for u in u_rows if u.get("model")}) or (
                        [r["model"]] if r.get("model") else [])
                    toks = {t: int(r.get(t) or 0) for t in _TOKENS}
                    prof["sessions"] += 1
                    prof["cost_usd"] += cost
                    prof["priced_sessions"] += 1 if cost else 0
                    for t in _TOKENS:
                        prof[t] += toks[t]
                    for u in u_rows:
                        m = by_model[u.get("model") or "unknown"]
                        m["cost_usd"] += _row_cost(u)
                        m["sessions"] += 1
                        m["api_calls"] += int(u.get("api_call_count") or 0)
                        for t in _TOKENS:
                            m[t] += int(u.get(t) or 0)
                    if not u_rows and r.get("model"):
                        m = by_model[r["model"]]
                        m["cost_usd"] += cost
                        m["sessions"] += 1
                        for t in _TOKENS:
                            m[t] += toks[t]
                    sessions.append({
                        "profile": p.name, "session_id": r["id"], "title": r.get("title"),
                        "source": r.get("source"), "models": models,
                        "subagent": bool(r.get("parent_session_id")),
                        "last_activity": r.get("last_activity_at") or r.get("started_at"),
                        "messages": int(r.get("message_count") or 0),
                        "cost_usd": round(cost, 6), "cost_status": r.get("cost_status"),
                        "estimated": not ((r.get("actual_cost_usd") or 0) > 0),
                        **toks,
                    })
        except (OSError, sqlite3.Error) as exc:
            st["status"] = "error: {}".format(exc)
        sources.append(st)
    total = sum(s["cost_usd"] for s in sessions)
    priced = [s for s in sessions if s["cost_usd"] > 0]
    sessions.sort(key=lambda s: -s["cost_usd"])
    for pr in by_profile.values():
        pr["cost_usd"] = round(pr["cost_usd"], 4)
    models = sorted(({"model": k, **{kk: (round(vv, 4) if kk == "cost_usd" else vv) for kk, vv in v.items()}}
                     for k, v in by_model.items()), key=lambda m: -m["cost_usd"])
    return {
        "available": available,
        "window_days": window_days,
        "total_usd": round(total, 4),
        "sessions": len(sessions),
        "priced_sessions": len(priced),
        "mean_per_priced_session_usd": round(total / len(priced), 4) if priced else 0.0,
        "top": sessions[:top],
        "by_profile": sorted(by_profile.values(), key=lambda x: -x["cost_usd"]),
        "by_model": models[:15],
        "sources": sources,
    }

