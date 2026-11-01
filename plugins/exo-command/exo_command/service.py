"""One facade for every panel, fed by the Exo protocol.

``CommandCentre`` holds a ``State`` and keeps it current from a ``Source``:

* ``bootstrap()`` asks the core for the picture (``hello``, ``session.list``, ``tasks.list``) and
  replays recent events so approvals and usage are not missed; after a reconnect, every event since the last one
  applied, up to ``RESUME_GAP`` events back.
* ``follow()`` then folds the live event stream; ``sync()`` does one poll through
  ``events.subscribe`` for callers with no stream (tests, a one-shot digest).

Each section is computed on its own and fails on its own, so one bad input never blanks the page.
Sections read the in-memory state, so they are cheap; the projects scan gets a longer cache floor.
"""
from __future__ import annotations

import asyncio
import collections
import time
import traceback
from typing import Any, Dict, List, NamedTuple, Optional

from exo.protocol import schema as s
from exo.server.engine import MAX_LAG

from . import costs, decisions, projects
from .config import Settings, load_settings
from .source import Source, SourceError, core_message
from .state import TASK_ORDER, TERMINAL_TASK, State

SECTIONS = ("projects", "sessions", "tasks", "approvals", "decisions", "costs")
PROJECTS_CACHE_FLOOR = 120
REPLAY_WINDOW = 5000      # events replayed on start, newest first-seq minus this
RESUME_GAP = 4 * MAX_LAG  # a reconnect replays every missed event up to this many; a longer gap gets the window
SESSION_LIMIT = 200
EVENT_RING = 3000         # recent events kept for the chat stream (late browsers, reconnects)


class CoreConnection(NamedTuple):
    state: str  # local control frame, never a protocol event or durable cursor


class CommandCentre:
    def __init__(self, settings: Optional[Settings] = None, source: Optional[Source] = None):
        self.settings = settings or load_settings()
        self.source = source
        self.state = State()
        self._cache: Dict[str, tuple] = {}
        self._waiters: List[asyncio.Queue] = []
        self._core_state = "connecting"
        self.last_error: Optional[str] = None
        self.auto_core = False    # True when connect() chose the default socket: find or start the core
        self.ring: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=EVENT_RING)
        self.panels: Dict[str, Dict[str, Any]] = {}   # open ui.panel panels, by panel_id
        self._ring_seq = 0
        self._event_subs: List[asyncio.Queue] = []

    @property
    def core_state(self) -> str:
        return self._core_state

    @property
    def connected(self) -> bool:
        return self._core_state == "connected"

    @connected.setter
    def connected(self, value: bool) -> None:
        self.set_core_state("connected" if value else "unavailable")

    def set_core_state(self, state: str) -> None:
        if state not in ("connecting", "connected", "unavailable"):
            raise ValueError("invalid core connection state")
        if state == self._core_state:
            return
        self._core_state = state
        for q in list(self._event_subs):
            try:
                q.put_nowait(CoreConnection(state))
            except asyncio.QueueFull:
                self._end_stream(q)  # a reconnect receives the latest state immediately
        self._notify()

    # ------------------------------------------------------------ feeding the state

    async def bootstrap(self) -> None:
        src = self._need_source()
        hello = await src.call(s.Hello(client="exo-command"))
        seq = int(hello.get("seq") or 0)
        # A reconnect resumes where this page stopped, so every event published while the stream was down applies
        # (the core ends a stream that falls behind, D-079). A first start, a newer core whose log is behind this
        # page, or a gap above RESUME_GAP (a long outage, one large reply) replays only the newest window.
        resume = 0 < self.state.seq <= seq <= self.state.seq + RESUME_GAP
        since = self.state.seq if resume else max(0, seq - REPLAY_WINDOW)
        replay = await src.call(s.EventsSubscribe(since=since))
        if int(hello.get("seq") or 0) < self.state.seq:
            self._start_over()
        self.state.apply_all(replay.get("events") or [])
        for ev in replay.get("events") or []:
            self.record(ev)
        # A core that does not support a query yet (``unsupported``) just leaves that panel to the events.
        self.state.load_sessions((await self._optional(s.SessionList(limit=SESSION_LIMIT))).get("sessions") or [])
        self.state.load_tasks((await self._optional(s.TasksList())).get("tasks") or [])
        self.state.seq = max(self.state.seq, int(replay.get("seq") or 0))
        await self.reconcile_approvals()
        self.connected, self.last_error = True, None
        self._notify()

    async def reconcile_approvals(self) -> None:
        """D-045: the core's open list is the truth. A request replayed from the log whose answer was never
        announced (a restarted core, a cancelled turn on an older core) must not stay on the page as a card.
        The same list also adds each open request the page lacks: one asked before the replay window shows once."""
        seq0 = self.state.seq
        try:
            res = await self._need_source().call(s.ApprovalsList())
        except Exception:
            return  # an older core without the command, or down: the events still apply
        listed = res.get("approvals") or []
        live = {a.get("request_id") for a in listed}
        stale = [rid for rid, a in self.state.approvals.items()
                 if rid not in live and int(a.get("seq") or 0) <= seq0]
        for rid in stale:
            self.state.forget_approval(rid)
        # A request resolved while the listing was in flight is in that (older) listing: never re-add it.
        gone = self.state.resolved_since(seq0)
        added = [self.state.add_open_approval(a) for a in listed if a.get("request_id") not in gone]
        if stale or any(added):
            self._notify()

    async def _optional(self, body: Any) -> Dict[str, Any]:
        try:
            return await self._need_source().call(body)
        except SourceError as exc:
            if exc.code == "unsupported":
                return {}
            raise

    async def sync(self) -> int:
        """One poll: apply every event after ``state.seq``. Returns how many were applied."""
        src = self._need_source()
        res = await src.call(s.EventsSubscribe(since=self.state.seq))
        events = res.get("events") or []
        self.state.apply_all(events)
        for ev in events:
            self.record(ev)
        if events:
            self._notify()
        return len(events)

    async def follow(self) -> None:
        """Fold the live stream until cancelled. Reconnects are the source's job."""
        try:
            async for ev in self._need_source().stream(self.state.seq):
                self.state.apply(ev)
                self.record(ev)
                self._notify()
        finally:
            # Client.events can end normally after swallowing a transport error.
            # An open browser stream is not evidence that its core is reachable.
            self.set_core_state("unavailable")

    # ------------------------------------------------------------ the chat stream

    def record(self, ev: Dict[str, Any]) -> None:
        """Keep an event for chat clients, fold open panels, and hand it to live subscribers.
        An event at or below the last recorded seq is a replay and is ignored."""
        seq = int(ev.get("seq") or 0)
        if seq <= self._ring_seq:
            return
        self._ring_seq = seq
        self.ring.append(ev)
        if ev.get("type") == "ui.panel":
            body = ev.get("body") or {}
            pid = body.get("panel_id")
            if pid and body.get("action") == "close":
                self.panels.pop(pid, None)
            elif pid:
                cur = self.panels.get(pid, {}) if body.get("action") == "update" else {}
                self.panels[pid] = {**cur, **{k: v for k, v in body.items() if v not in (None, "", {})},
                                    "session": ev.get("session"), "seq": seq}
        for q in list(self._event_subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:   # a slow reader: end its stream, it resumes from Last-Event-ID
                self._end_stream(q)

    def _end_stream(self, q: asyncio.Queue) -> None:
        """End one reader's stream. Everything still queued is newer than the last id it wrote, so it is dropped
        and not written: the reader reconnects from that id and replays it from the ring or the core's log."""
        self.unsubscribe_events(q)
        while not q.empty():
            q.get_nowait()
        q.put_nowait(None)

    def _start_over(self) -> None:
        """The core's log is behind what this page holds (a new home, a wiped log): that picture is another log's.
        Start from nothing, and end every chat stream so it reconnects and gets the reset from ``replay_events``."""
        self.state, self.panels, self._ring_seq = State(), {}, 0
        self.ring.clear()
        self._cache.clear()
        for q in list(self._event_subs):
            self._end_stream(q)

    def events_since(self, since: int = 0, session: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e for e in self.ring if e["seq"] > since and (session is None or e.get("session") in (None, session))]

    async def replay_events(self, since: int, session: Optional[str] = None):
        """The ring is a cache; an expired cursor resumes from the core's durable event log."""
        # Zero is a fresh page, not a request to download the entire durable
        # log. It can arrive before bootstrap publishes its first cursor.
        # Sessions and open panels have their own snapshot/history routes.
        if since == 0 and session is None:
            return self.events_since(0), None
        gap = bool((self.ring and since < self.ring[0]["seq"] - 1) or since > self._ring_seq)
        if not gap:
            return self.events_since(since, session), None
        res = await self._need_source().call(s.EventsSubscribe(since=since, session=session))
        frontier = int(res.get("seq") or 0)
        reset = since > frontier
        if reset:
            res = await self._need_source().call(s.EventsSubscribe(since=0, session=session))
        return res.get("events") or [], {"since": since, "reset": reset}

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._event_subs.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subs:
            self._event_subs.remove(q)

    async def respond_approval(self, request_id: str, decision: str, note: str = "") -> Dict[str, Any]:
        return await self._need_source().call(s.ApprovalRespond(request_id=request_id, decision=decision, note=note))

    async def aclose(self) -> None:
        if self.source is not None:
            await self.source.aclose()

    def _need_source(self) -> Source:
        if self.source is None:
            raise RuntimeError("no protocol source: pass one, or call connect()")
        return self.source

    def connect(self) -> Source:
        from .source import ClientSource
        st = self.settings
        self.auto_core = not st.url and not st.socket
        self.source = (ClientSource(url=st.url, token=st.resolved_token()) if st.url
                       else ClientSource(socket=st.resolved_socket()))
        return self.source

    async def ensure_core(self) -> None:
        """Find the shared core, or start it (D-036); keep ``last_error`` a sentence the page can show."""
        from exo import ensure_core as ensure
        home = ensure.home_dir()
        agent = ensure.agent_name()
        if ensure.find_core(home, agent) is None:
            self.last_error = "Exo core is not running, starting it..."
            self._notify()
        try:
            sock = await asyncio.to_thread(ensure.ensure_core, home, agent)
        except ensure.CoreUnavailable as exc:
            raise RuntimeError(str(exc)) from None
        if self.source is not None and getattr(self.source, "socket", None) != str(sock):
            old, self.source = self.source, None
            await old.aclose()
            from .source import ClientSource
            self.source = ClientSource(socket=str(sock))

    def subscribe(self) -> asyncio.Queue:
        """A queue that receives the state ``seq`` after each change (for the web SSE feed)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._waiters.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._waiters:
            self._waiters.remove(q)

    def _notify(self) -> None:
        for q in self._waiters:
            if q.full():
                q.get_nowait()
            q.put_nowait(self.state.seq)

    # ------------------------------------------------------------ sections

    def _card(self, sess: Dict[str, Any], now: float) -> Dict[str, Any]:
        task = self.state.tasks.get(sess.get("task_id") or "")
        if sess["running"]:
            status = "running"
        elif sess["pending_approvals"]:
            status = "waiting"
        elif task and task["status"] in TERMINAL_TASK:
            status = {"failed": "errored", "cancelled": "interrupted"}.get(task["status"], "done")
        elif sess["stop_reason"] in ("error",):
            status = "errored"
        elif sess["stop_reason"] == "interrupted":
            status = "interrupted"
        else:
            status = "idle"
        head = sess["head"]
        return {
            "session_id": sess["id"], "kind": "subagent" if task else "session", "task_id": sess.get("task_id"),
            "seat": sess["seat"], "title": sess["title"] or None, "goal": _clip(head[1], 300) if head else None,
            "model": sess["turn_model"] or sess["model"], "status": status,
            "started_at": sess["started_at"], "last_activity_at": sess["last_activity_at"],
            "ended_at": task.get("finished_at") if task else None,
            "messages": sess["message_count"], "tool_calls": sess["tool_call_count"], "tool": sess["tool"],
            "output": sess["output"] or None, "cost_usd": sess["cost_usd"] or None,
            "pending_approvals": sess["pending_approvals"], "stop_reason": sess["stop_reason"], "cwd": sess["cwd"],
        }

    def _sessions(self, now: float) -> Dict[str, Any]:
        st, cfg = self.state, self.settings
        live_cut, past_cut = now - cfg.live_window_seconds, now - cfg.past_window_hours * 3600
        active, past = [], []
        for sess in st.sessions.values():
            card = self._card(sess, now)
            last = sess["last_activity_at"] or 0
            finished = card["status"] in ("done", "errored", "interrupted") and card["kind"] == "subagent"
            if finished:
                if (card["ended_at"] or last) >= past_cut:
                    past.append(card)
            elif card["status"] in ("running", "waiting") or last >= live_cut:
                active.append(card)
        active.sort(key=lambda c: -(c["last_activity_at"] or 0))
        past.sort(key=lambda c: -(c["ended_at"] or c["last_activity_at"] or 0))
        return {
            "active": active, "past": past[:40],
            "counts": {"active": len(active), "running": sum(1 for c in active if c["status"] == "running"),
                       "waiting": sum(1 for c in active if c["status"] == "waiting"),
                       "subagents": sum(1 for c in active if c["kind"] == "subagent"), "past": len(past[:40])},
            "live_window_seconds": cfg.live_window_seconds, "seq": st.seq,
        }

    def _tasks(self, now: float) -> Dict[str, Any]:
        by: Dict[str, List[dict]] = {}
        for t in self.state.tasks.values():
            by.setdefault(t.get("status") or "queued", []).append({
                "task_id": t["task_id"], "title": t.get("title") or t["task_id"], "status": t.get("status"),
                "detail": t.get("detail") or "", "parent": t.get("parent"), "child_session": t.get("child_session"),
                "depth": t.get("depth"), "updated_at": t.get("updated_at") or t.get("created_at"),
                "finished_at": t.get("finished_at"), "updates": t.get("events", 0)})
        order = list(TASK_ORDER) + sorted(k for k in by if k not in TASK_ORDER)
        cols, totals = [], {}
        for status in order:
            items = sorted(by.get(status, []), key=lambda x: -(x["updated_at"] or 0))
            totals[status] = len(items)
            if items:
                cols.append({"status": status, "total": len(items), "tasks": items[:30]})
        open_n = sum(v for k, v in totals.items() if k not in TERMINAL_TASK)
        return {"columns": cols, "totals": totals, "open": open_n, "needs_attention": totals.get("failed", 0),
                "count": len(self.state.tasks)}

    def _approvals(self, now: float) -> Dict[str, Any]:
        pending = sorted(self.state.approvals.values(), key=lambda a: a["requested_at"] or 0)
        titles = {k: v["title"] for k, v in self.state.sessions.items()}
        for a in pending:
            a["session_title"] = titles.get(a["session"]) or None
        return {"pending": pending, "recent": list(self.state.resolved)[:10],
                "counts": {"pending": len(pending)}}

    def _decisions(self, now: float) -> Dict[str, Any]:
        d = decisions.read_queue(self.settings.decisions_queue, now=now)
        known = {x["id"] for x in d["waiting"] + d["snoozed"] + d["recent"]}
        for q in self.state.decisions.values():
            if q["id"] in known:
                continue
            d["waiting"].append({"id": q["id"], "status": "open", "title": q["title"], "context": q["context"],
                                 "options": [], "default": None, "priority": "normal", "overdue": False,
                                 "reversible": True, "exposure": "private", "opened_at": q["opened_at"],
                                 "deadline_at": None, "asked_by": {"agent": q["source"]}, "links": [],
                                 "origin": "protocol"})
        d["counts"]["waiting"] = len(d["waiting"])
        d["configured"] = d["configured"] or bool(self.state.decisions)
        return d

    def _costs(self, now: float) -> Dict[str, Any]:
        c = self.settings
        return costs.per_session(self.state.sessions.values(), window_days=c.cost_window_days,
                                 top=c.cost_top_sessions, now=now)

    def _projects(self, now: float) -> Dict[str, Any]:
        c = self.settings
        rules = projects.Rules(c.generic_paths)
        recs = self.state.session_records()
        since = now - c.project_window_days * 86400
        recs = [r for r in recs if (r["last_activity_at"] or r["started_at"] or now) >= since]
        facts = projects.facts_from_sessions(recs, rules)
        probes = projects.probes_from_sessions(recs, rules)
        return projects.board_from_facts(facts, probes, [{"source": "exo protocol", "status": "ok",
                                                          "sessions": len(facts)}], rules,
                                         window_days=c.project_window_days, stall_days=c.project_stall_days,
                                         abandon_days=c.project_abandon_days, now=now)

    def section(self, name: str, *, now: Optional[float] = None, fresh: bool = False) -> Dict[str, Any]:
        if name not in SECTIONS:
            raise KeyError(name)
        t = time.time()
        use_now = now if now is not None else t
        ttl = self.settings.cache_seconds
        if name == "projects":
            ttl = max(ttl, PROJECTS_CACHE_FLOOR)
        if name == "projects":  # the heavy one is cached; the rest read memory
            hit = self._cache.get(name)
            if hit and not fresh and now is None and t - hit[0] < ttl:
                return hit[1]
        started = time.time()
        try:
            data = {"ok": True, **getattr(self, "_" + name)(use_now)}
        except Exception as exc:  # isolate: one bad section never blanks the page
            data = {"ok": False, "error": core_message(exc),
                    "trace": traceback.format_exc(limit=3)}
        data["generated_at"] = time.time()
        data["took_ms"] = int((time.time() - started) * 1000)
        if name == "projects":
            self._cache[name] = (t, data)
        return data

    def snapshot(self, *, now: Optional[float] = None, fresh: bool = False,
                 only: Optional[list] = None) -> Dict[str, Any]:
        names = [n for n in (only or SECTIONS) if n in SECTIONS]
        return {"settings": self.settings.public(), "seq": self.state.seq, "connected": self.connected, "core_state": self.core_state,
                "error": self.last_error,
                **{n: self.section(n, now=now, fresh=fresh) for n in names}}

    def summary(self, now: Optional[float] = None) -> str:
        """Short plain-text digest for the slash command."""
        snap = self.snapshot(now=now, only=["decisions", "approvals", "sessions", "tasks", "projects"])
        lines = []
        d = snap["decisions"]
        if not d.get("configured"):
            lines.append("Decisions: no queue configured.")
        else:
            c = d.get("counts", {})
            lines.append("Decisions waiting: {} ({} overdue).".format(c.get("waiting", 0), c.get("overdue", 0)))
            for item in d.get("waiting", [])[:5]:
                lines.append("  - " + str(item.get("title")))
        a = snap["approvals"]
        lines.append("Approvals pending: {}.".format(a["counts"]["pending"]))
        for item in a["pending"][:5]:
            lines.append("  - [{}] {}".format(item["risk"], item["summary"]))
        se = snap["sessions"]
        if se.get("ok"):
            c = se["counts"]
            lines.append("Live: {} sessions ({} running), {} subagents.".format(c["active"], c["running"], c["subagents"]))
        k = snap["tasks"]
        if k.get("ok"):
            lines.append("Tasks: {} open, {} failed.".format(k["open"], k["needs_attention"]))
        p = snap["projects"]
        if p.get("ok"):
            lanes = {l["id"]: len(l["cards"]) for l in p["lanes"]}
            lines.append("Projects: {} total, {} waiting on you, {} blocked, {} ongoing.".format(
                p["counts"]["projects"], lanes.get("waiting_on_you", 0), lanes.get("blocked", 0),
                lanes.get("ongoing", 0)))
        return "\n".join(lines)


def _clip(text: str, n: int) -> str:
    return " ".join((text or "").split())[:n]
