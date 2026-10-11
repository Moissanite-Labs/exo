"""Decisions waiting on the principal: a JSONL queue in the principal store.

Format (v1). One JSON object per line, UTF-8, append-only. Each line is an event about one
decision. Producers (agents, ``exo.ask``, ``exo-gate``) append; nothing rewrites old lines.
The current state of a decision is the fold of its events in file order.

Common fields on every event:
  v    int     format version, 1
  op   str     open | update | snooze | resolve | withdraw
  id   str     stable decision id chosen by the producer, e.g. "dec-20261011-1a2b"
  ts   str|num ISO-8601 time or epoch seconds

``open`` fields (``title`` required, the rest optional):
  title       one line, the question as the principal should read it
  context     short plain text: why this needs the principal
  options     list of {"key": str, "label": str} or plain strings
  default     option key taken if the principal does not answer by ``deadline``
  deadline    ISO-8601 or epoch
  priority    high | normal | low            (default normal)
  reversible  bool                           (false sorts first)
  exposure    private | public | third-party
  cost_usd    number, money at stake if known
  asked_by    {"agent": str, "profile": str, "session_id": str}
  links       list of URLs or paths
  tags        list of str

``update`` merges any ``open`` field. ``snooze`` sets ``until``. ``resolve`` sets ``choice``,
optional ``note`` and ``by``. ``withdraw`` sets optional ``reason``. Unknown fields are kept.
Bad lines are counted and reported, never fatal. The JSON Schema lives in
``schemas/decision-event.schema.json``.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = 1
OPS = {"open", "update", "snooze", "resolve", "withdraw"}
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
OPEN_FIELDS = ("title", "context", "options", "default", "deadline", "priority", "reversible",
               "exposure", "cost_usd", "asked_by", "links", "tags")


def to_epoch(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def validate(ev: Any) -> Optional[str]:
    """Return an error string, or None when the event is well formed."""
    if not isinstance(ev, dict):
        return "not an object"
    if ev.get("op") not in OPS:
        return "op must be one of " + ", ".join(sorted(OPS))
    if not isinstance(ev.get("id"), str) or not ev["id"].strip():
        return "id required"
    if to_epoch(ev.get("ts")) is None:
        return "ts must be ISO-8601 or epoch seconds"
    if ev["op"] == "open" and not (isinstance(ev.get("title"), str) and ev["title"].strip()):
        return "open needs a title"
    if ev.get("v", VERSION) != VERSION:
        return "unsupported version {}".format(ev.get("v"))
    return None


def _norm_options(opts: Any) -> List[Dict[str, str]]:
    out = []
    for i, o in enumerate(opts or []):
        if isinstance(o, dict):
            out.append({"key": str(o.get("key") or i + 1), "label": str(o.get("label") or o.get("key") or "")})
        else:
            out.append({"key": str(i + 1), "label": str(o)})
    return out


def fold(lines: List[str]) -> Dict[str, Any]:
    items: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            errors.append({"line": n, "error": "invalid JSON"})
            continue
        err = validate(ev)
        if err:
            errors.append({"line": n, "error": err})
            continue
        did, op, ts = ev["id"], ev["op"], to_epoch(ev["ts"])
        cur = items.get(did)
        if op == "open":
            cur = {"id": did, "status": "open", "opened_at": ts, "events": 0}
            cur.update({k: v for k, v in ev.items() if k not in ("v", "op", "id", "ts")})
            items[did] = cur
        elif cur is None:
            errors.append({"line": n, "error": "{} before open for {}".format(op, did)})
            continue
        elif op == "update":
            cur.update({k: v for k, v in ev.items() if k not in ("v", "op", "id", "ts", "status")})
        elif op == "snooze":
            cur["snoozed_until"] = to_epoch(ev.get("until"))
        elif op == "resolve":
            cur.update(status="resolved", choice=ev.get("choice"), note=ev.get("note"),
                       resolved_by=ev.get("by"), closed_at=ts)
        elif op == "withdraw":
            cur.update(status="withdrawn", reason=ev.get("reason"), closed_at=ts)
        cur["events"] += 1
        cur["updated_at"] = ts
    return {"items": items, "errors": errors}


def read_queue(path: Optional[Path], *, now: Optional[float] = None, recent_closed: int = 10) -> Dict[str, Any]:
    now = now if now is not None else time.time()
    base: Dict[str, Any] = {"path": str(path) if path else None, "configured": path is not None,
                            "exists": False, "waiting": [], "snoozed": [], "recent": [],
                            "errors": [], "counts": {"waiting": 0, "overdue": 0, "snoozed": 0}}
    if path is None:
        return base
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return base
    except OSError as exc:
        base["errors"] = [{"line": 0, "error": str(exc)}]
        return base
    base["exists"] = True
    folded = fold(text.splitlines())
    waiting, snoozed, closed = [], [], []
    for d in folded["items"].values():
        d["options"] = _norm_options(d.get("options"))
        d["deadline_at"] = to_epoch(d.get("deadline"))
        d["overdue"] = bool(d["deadline_at"] and d["deadline_at"] < now and d["status"] == "open")
        d["priority"] = d.get("priority") if d.get("priority") in PRIORITY_RANK else "normal"
        if d["status"] != "open":
            closed.append(d)
        elif d.get("snoozed_until") and d["snoozed_until"] > now:
            snoozed.append(d)
        else:
            waiting.append(d)
    waiting.sort(key=lambda d: (not d["overdue"], PRIORITY_RANK[d["priority"]],
                                d.get("reversible") is not False,
                                d["deadline_at"] or float("inf"), d["opened_at"] or 0))
    snoozed.sort(key=lambda d: d["snoozed_until"])
    closed.sort(key=lambda d: -(d.get("closed_at") or 0))
    base.update(waiting=waiting, snoozed=snoozed, recent=closed[:recent_closed], errors=folded["errors"],
                counts={"waiting": len(waiting), "overdue": sum(1 for d in waiting if d["overdue"]),
                        "snoozed": len(snoozed)})
    return base


def append_event(path: Path, event: Dict[str, Any]) -> Dict[str, Any]:
    """Producer helper: validate and append one event line. The dashboard never calls this."""
    ev = {"v": VERSION, **event}
    ev.setdefault("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    err = validate(ev)
    if err:
        raise ValueError(err)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return ev
