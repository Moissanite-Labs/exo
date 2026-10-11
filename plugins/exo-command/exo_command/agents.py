"""Live agents and tasks: read-only worker cards.

Ported from the subagent viewer's card model (goal, model, status, latest tool line,
accumulated output, Active and Past split). The desktop viewer listened to the live
``subagent.*`` event stream; a dashboard backend has no such stream, so the cards are built
from what Hermes persists in each profile's ``state.db``:

* ``sessions``: a session is *live* when it has no ``ended_at`` and moved inside the live
  window. Subagents are sessions with a ``parent_session_id`` or ``source = 'subagent'``.
* ``messages``: the newest tool call and the newest assistant text give the tool line and
  the output preview (capped, never the whole transcript).
* ``async_delegations``: background ``delegate_task`` fan-outs that are still dispatched.

Observe only. Nothing here steers, interrupts or spawns anything.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence

from .ro import ProfileDb, columns, ro, select_cols

OUTPUT_CAP = 1200
THINK_CAP = 600
TERMINAL_DELEGATION = {"completed", "complete", "done", "error", "errored", "failed", "cancelled",
                       "canceled", "interrupted", "timeout", "unknown"}

_COLS = ("id", "source", "title", "model", "parent_session_id", "started_at", "ended_at",
         "end_reason", "last_activity_at", "last_activity_description", "message_count",
         "tool_call_count", "cwd", "estimated_cost_usd", "actual_cost_usd")


def _status(row: dict, now: float, live_window: float) -> str:
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


def live(profiles: Sequence[ProfileDb], *, live_window: int = 900, past_hours: int = 24,
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
                cols = select_cols(_COLS, have)
                act = "COALESCE(last_activity_at, started_at)" if "last_activity_at" in have else "started_at"
                ended = "ended_at" in have
                # live: not ended, moved within the window
                q_live = "SELECT {} FROM sessions WHERE {} >= ?{} ORDER BY {} DESC LIMIT 60".format(
                    ", ".join(cols), act, " AND ended_at IS NULL" if ended else "", act)
                for r in c.execute(q_live, (now - live_window,)):
                    row = dict(r)
                    active.append(_card(p.name, row, _tail(c, row["id"]), _status(row, now, live_window)))
                # past: subagents that ended inside the past window
                if ended and "parent_session_id" in have:
                    q_past = ("SELECT {} FROM sessions WHERE ended_at IS NOT NULL AND ended_at >= ? "
                              "AND (parent_session_id IS NOT NULL OR source='subagent') "
                              "ORDER BY ended_at DESC LIMIT ?").format(", ".join(cols))
                    for r in c.execute(q_past, (past_cutoff, past_limit)):
                        row = dict(r)
                        past.append(_card(p.name, row, _tail(c, row["id"]), _status(row, now, live_window)))
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
