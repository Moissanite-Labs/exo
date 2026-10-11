"""Kanban tasks across every board, read-only.

Board layout follows Hermes: ``default`` at ``<home>/kanban.db``, the rest at
``<home>/kanban/boards/<slug>/kanban.db``. Columns follow Hermes's default workflow order.
Open tasks are all returned; done tasks are capped per board; archived tasks are skipped.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .ro import columns, discover_boards, ro, select_cols

STATUS_ORDER = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done")
OPEN_ATTENTION = ("blocked", "review", "triage")  # statuses that usually need a person

_COLS = ("id", "title", "body", "assignee", "status", "priority", "created_by", "created_at",
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


def tasks(home: Path, *, done_limit: int = 20) -> dict:
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
                cols = select_cols(_COLS, have)
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
