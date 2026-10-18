"""Command centre state, folded from the Exo protocol.

``State`` is a pure reducer: feed it the answers of ``session.list`` and ``tasks.list`` for a
starting picture, then every event from the stream (``apply``). No I/O, no clock reads except where a
``now`` is passed in, so it is easy to test and the same code serves a live stream and a replay.

Events and records are plain dicts (``Event.to_dict()`` shape: seq, session, type, ts, body), so it
does not matter whether they came off SSE, a one-shot ``events.subscribe`` replay or stdio.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, Iterable, List, Optional

OUTPUT_CAP = 1200
PROBE_TAIL = 6
TERMINAL_TASK = {"done", "failed", "cancelled"}
TASK_ORDER = ("queued", "running", "done", "failed", "cancelled")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


def epoch(ts: Any) -> Optional[float]:
    """ISO 8601 (as the server writes it) or a number to epoch seconds."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(message: Dict[str, Any]) -> str:
    return "".join(p.get("text", "") for p in message.get("content") or [] if p.get("type") == "text")


def _short(v: Any, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _args_line(args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    for k in ("command", "path", "query", "goal", "url", "pattern"):
        if args.get(k):
            return _short(args[k], 120)
    return _short(next(iter(args.values())), 120)


class State:
    def __init__(self, probe_tail: int = PROBE_TAIL) -> None:
        self.seq = 0
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.resolved: Deque[Dict[str, Any]] = deque(maxlen=20)
        self.decisions: Dict[str, Dict[str, Any]] = {}
        self.notices: Deque[Dict[str, Any]] = deque(maxlen=20)
        self.compactions = 0
        self.probe_tail = probe_tail

    # ------------------------------------------------------------ bootstrap from queries

    def _session(self, sid: str) -> Dict[str, Any]:
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = {
                "id": sid, "title": "", "seat": None, "model": None, "cwd": None, "parent": None,
                "started_at": None, "last_activity_at": None, "message_count": 0, "tool_call_count": 0,
                "running": False, "turn_id": None, "turn_model": None, "tool": None, "output": "",
                "pending_approvals": 0, "stop_reason": None, "usage": {k: 0 for k in USAGE_KEYS},
                "cost_usd": 0.0, "priced": False, "head": None, "tail": deque(maxlen=self.probe_tail),
            }
        return s

    def load_sessions(self, infos: Iterable[Dict[str, Any]]) -> None:
        for i in infos:
            self._merge_info(i)

    def _merge_info(self, info: Dict[str, Any]) -> None:
        s = self._session(info["id"])
        for k in ("title", "seat", "model", "cwd", "parent"):
            if info.get(k) is not None:
                s[k] = info[k]
        s["message_count"] = max(s["message_count"], int(info.get("message_count") or 0))
        created, updated = epoch(info.get("created")), epoch(info.get("updated"))
        if created and not s["started_at"]:
            s["started_at"] = created
        if updated and (s["last_activity_at"] or 0) < updated:
            s["last_activity_at"] = updated

    def load_tasks(self, tasks: Iterable[Dict[str, Any]]) -> None:
        for t in tasks:
            cur = self.tasks.setdefault(t["task_id"], {"task_id": t["task_id"], "events": 0})
            cur.update(parent=t.get("parent"), child_session=t.get("child_session"), depth=t.get("depth"),
                       title=t.get("title") or cur.get("title", ""), status=t.get("status") or "",
                       detail=t.get("detail") or "")
            cur.setdefault("created_at", None)
            self._flag_child(cur)

    def _flag_child(self, task: Dict[str, Any]) -> None:
        child = task.get("child_session")
        if child:
            self._session(child)["task_id"] = task["task_id"]

    # ------------------------------------------------------------ the event stream

    def apply(self, ev: Dict[str, Any]) -> None:
        seq = int(ev.get("seq") or 0)
        if seq and seq <= self.seq:
            return  # folded already (a replay after the stream dropped): counts must not double
        typ, body, sid, ts = ev.get("type"), ev.get("body") or {}, ev.get("session"), epoch(ev.get("ts"))
        self.seq = max(self.seq, seq)
        fn = getattr(self, "_on_" + str(typ).replace(".", "_"), None)
        if sid and typ not in ("session.created", "session.updated"):
            s = self._session(sid)
            if ts:
                s["last_activity_at"] = ts
                s["started_at"] = s["started_at"] or ts
        if fn:
            fn(sid, body, ts)

    def apply_all(self, events: Iterable[Dict[str, Any]]) -> None:
        for e in events:
            self.apply(e)

    def _on_session_created(self, sid, b, ts):
        self._merge_info(b.get("session") or {})

    _on_session_updated = _on_session_created

    def _on_turn_started(self, sid, b, ts):
        s = self._session(sid)
        s.update(running=True, turn_id=b.get("turn_id"), turn_model=b.get("model") or s["model"], output="",
                 stop_reason=None)

    def _on_turn_completed(self, sid, b, ts):
        s = self._session(sid)
        s.update(running=False, stop_reason=b.get("stop_reason") or "end", tool=None)

    def _on_message_delta(self, sid, b, ts):
        s = self._session(sid)
        s["output"] = (s["output"] + (b.get("text") or ""))[-OUTPUT_CAP:]

    def _on_message_completed(self, sid, b, ts):
        m = b.get("message") or {}
        s = self._session(sid)
        text = _text(m)
        s["message_count"] += 1
        if m.get("role") == "assistant" and text:
            s["output"] = text[-OUTPUT_CAP:]
        if m.get("role") in ("user", "assistant") and text.strip():
            row = (m["role"], text[:600], ts, "head")
            if s["head"] is None:
                s["head"] = row
            s["tail"].append((m["role"], text[:600], ts, "tail"))

    def _on_tool_started(self, sid, b, ts):
        s = self._session(sid)
        call = b.get("call") or {}
        line = _args_line(call.get("arguments"))
        s["tool"] = "{}: {}".format(call.get("name"), line) if line else call.get("name")
        s["tool_call_count"] += 1

    def _on_tool_completed(self, sid, b, ts):
        pass

    def _on_approval_requested(self, sid, b, ts):
        rid = b.get("request_id")
        self.approvals[rid] = {"request_id": rid, "session": sid, "summary": b.get("summary") or "",
                               "risk": b.get("risk") or "medium", "call": b.get("call"),
                               "options": b.get("options") or [], "requested_at": ts, "seq": self.seq}
        if sid:
            self._session(sid)["pending_approvals"] += 1

    def _on_approval_resolved(self, sid, b, ts):
        a = self.approvals.pop(b.get("request_id"), None)
        if a is None:
            return
        self.resolved.appendleft({**a, "decision": b.get("decision"), "by": b.get("by") or "", "resolved_at": ts})
        if a.get("session") and a["session"] in self.sessions:
            s = self.sessions[a["session"]]
            s["pending_approvals"] = max(0, s["pending_approvals"] - 1)

    def forget_approval(self, rid: str) -> None:
        """The core no longer holds this request (D-045): drop it as a resolution nobody announced."""
        self._on_approval_resolved(None, {"request_id": rid, "decision": "gone", "by": "core"}, None)

    def _on_task_updated(self, sid, b, ts):
        tid = b.get("task_id")
        t = self.tasks.setdefault(tid, {"task_id": tid, "events": 0, "created_at": ts})
        t["parent"] = sid or t.get("parent")
        for k in ("title", "child_session", "detail"):
            if b.get(k):
                t[k] = b[k]
        t["status"] = b.get("status") or t.get("status", "")
        t["updated_at"] = ts
        t["events"] += 1
        if t["status"] in TERMINAL_TASK:
            t["finished_at"] = ts
        self._flag_child(t)

    def _on_usage_updated(self, sid, b, ts):
        if not sid:
            return
        s = self._session(sid)
        total = b.get("session_total")
        if total:
            usage = total
        else:
            usage = {k: s["usage"][k] + int((b.get("usage") or {}).get(k) or 0) for k in USAGE_KEYS}
            usage["cost_usd"] = s["cost_usd"] + float((b.get("usage") or {}).get("cost_usd") or 0)
        s["usage"] = {k: int(usage.get(k) or 0) for k in USAGE_KEYS}
        cost = usage.get("cost_usd")
        if cost is not None:
            s["cost_usd"] = float(cost)
            s["priced"] = s["priced"] or float(cost) > 0

    def _on_context_compacted(self, sid, b, ts):
        self.compactions += 1

    def _on_decision_queued(self, sid, b, ts):
        did = b.get("decision_id")
        self.decisions[did] = {"id": did, "title": b.get("title") or did, "context": b.get("detail") or "",
                               "source": b.get("source") or "", "opened_at": ts, "session": sid}

    def _on_notice(self, sid, b, ts):
        self.notices.appendleft({"text": b.get("text"), "level": b.get("level") or "info", "ts": ts})

    _on_error = lambda self, sid, b, ts: self.notices.appendleft(  # noqa: E731
        {"text": "{}: {}".format(b.get("code"), b.get("message")), "level": "error", "ts": ts})

    # ------------------------------------------------------------ views

    def child_sessions(self) -> set:
        return {t["child_session"] for t in self.tasks.values() if t.get("child_session")}

    def session_records(self) -> List[Dict[str, Any]]:
        """Plain records for the projects clusterer and the cost panel."""
        kids = self.child_sessions()
        out = []
        for s in self.sessions.values():
            probe = ([s["head"]] if s["head"] else []) + list(s["tail"])
            out.append({"id": s["id"], "title": s["title"], "seat": s["seat"], "model": s["model"],
                        "cwd": s["cwd"], "parent": s["parent"], "started_at": s["started_at"],
                        "last_activity_at": s["last_activity_at"], "message_count": s["message_count"],
                        "tool_call_count": s["tool_call_count"], "subagent": s["id"] in kids, "probe": probe})
        return out
