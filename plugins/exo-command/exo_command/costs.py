"""Cost per session, from ``usage.updated`` events.

The core prices every model call and publishes it as ``usage.updated`` (the call's ``usage`` plus the
running ``session_total``). ``state.State`` folds those per session; this module aggregates them into
the panel: total, top sessions, by seat and by model. A session counts as priced when the provider
reported a cost above zero. A session the core could not price (a local model, an unknown rate)
still shows its tokens, so the panel is never silently empty.

The Hermes version of this panel, which read ``state.db``, lives in
``exo.ingest.hermes_dashboard.cost_per_session`` for migration.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from .state import USAGE_KEYS

DAY = 86400.0


def per_session(sessions: Iterable[Dict[str, Any]], *, window_days: int = 7, top: int = 25,
                now: Optional[float] = None) -> dict:
    """``sessions`` are ``State.sessions`` values."""
    now = now if now is not None else time.time()
    since = now - window_days * DAY
    rows: List[dict] = []
    by_seat: Dict[str, dict] = {}
    by_model: Dict[str, dict] = defaultdict(lambda: {"cost_usd": 0.0, "sessions": 0, **{k: 0 for k in USAGE_KEYS}})
    for s in sessions:
        last = s.get("last_activity_at") or s.get("started_at") or 0
        usage = s.get("usage") or {}
        if last < since or not (s.get("cost_usd") or any(usage.get(k) for k in USAGE_KEYS)):
            continue
        cost = float(s.get("cost_usd") or 0)
        model = s.get("turn_model") or s.get("model") or "unknown"
        seat = s.get("seat") or "main"
        sb = by_seat.setdefault(seat, {"seat": seat, "cost_usd": 0.0, "sessions": 0, "priced_sessions": 0,
                                       **{k: 0 for k in USAGE_KEYS}})
        sb["sessions"] += 1
        sb["cost_usd"] += cost
        sb["priced_sessions"] += 1 if cost > 0 else 0
        mb = by_model[model]
        mb["sessions"] += 1
        mb["cost_usd"] += cost
        for k in USAGE_KEYS:
            sb[k] += int(usage.get(k) or 0)
            mb[k] += int(usage.get(k) or 0)
        rows.append({"session_id": s["id"], "title": s.get("title") or None, "seat": seat, "models": [model],
                     "subagent": bool(s.get("task_id") or s.get("parent")), "last_activity": last,
                     "messages": int(s.get("message_count") or 0), "cost_usd": round(cost, 6),
                     "priced": cost > 0, **{k: int(usage.get(k) or 0) for k in USAGE_KEYS}})
    total = sum(r["cost_usd"] for r in rows)
    priced = [r for r in rows if r["priced"]]
    rows.sort(key=lambda r: (-r["cost_usd"], -(r["input_tokens"] + r["output_tokens"])))
    for b in by_seat.values():
        b["cost_usd"] = round(b["cost_usd"], 4)
    models = sorted(({"model": k, **{kk: (round(v, 4) if kk == "cost_usd" else v) for kk, v in d.items()}}
                     for k, d in by_model.items()), key=lambda m: -m["cost_usd"])
    return {
        "available": True,
        "window_days": window_days,
        "total_usd": round(total, 4),
        "sessions": len(rows),
        "priced_sessions": len(priced),
        "mean_per_priced_session_usd": round(total / len(priced), 4) if priced else 0.0,
        "top": rows[:top],
        "by_seat": sorted(by_seat.values(), key=lambda x: -x["cost_usd"]),
        "by_model": models[:15],
    }
