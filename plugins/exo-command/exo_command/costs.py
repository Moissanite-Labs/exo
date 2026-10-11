"""Cost per session, read-only, from what Hermes already records in ``state.db``.

Sources, used when present:
* ``sessions``: ``estimated_cost_usd``, ``actual_cost_usd``, ``cost_status`` and token columns.
* ``session_model_usage``: per session, per model, per task rows with the same cost fields.

A session's cost is ``actual_cost_usd`` when it is above zero, else ``estimated_cost_usd``.
The panel is marked unavailable when neither source exists, so older Hermes builds degrade
cleanly.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

from .ro import ProfileDb, columns, ro, select_cols

DAY = 86400.0
_S_COLS = ("id", "title", "source", "model", "parent_session_id", "started_at", "last_activity_at",
           "message_count", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
           "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd", "cost_status", "billing_provider")
_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


def _cost(row: dict) -> float:
    actual = row.get("actual_cost_usd") or 0
    return float(actual) if actual and actual > 0 else float(row.get("estimated_cost_usd") or 0)


def per_session(profiles: Sequence[ProfileDb], *, window_days: int = 7, top: int = 25,
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
                cols = select_cols(_S_COLS, have)
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
                    cost = _cost(r)
                    if not cost and u_rows:
                        cost = sum(_cost(u) for u in u_rows)
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
                        m["cost_usd"] += _cost(u)
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
