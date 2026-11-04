"""Scheduler: cron-style jobs that run as fresh sessions and report through task.updated (D-022).

Jobs live in ``$EXO_HOME/schedule.sqlite``. ``when`` is a 5-field cron line (minute hour day-of-month
month day-of-week; ``*``, lists, ranges, steps) or ``every <n>m|h|d``. A run is a new session at the
job's tier; the final answer goes out as a ``notice`` (deliver="notice") or to a channel by id.
Runs missed while the engine was down happen once on start, not once per missed slot.

A product feature on the engine's public turn API, outside the core (docs/ENGINE-SPLIT.md, step 4): the
composition layer builds it (``CoreEngine(attach_scheduler=Scheduler)``), and the engine only calls ``start``,
``stop``, ``interrupt`` and ``edit`` (the ``schedule.edit`` command).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, TypeAlias

from exo.core import trace
from exo.core.agents.delegate import BLOCKED_TOOLS
from exo.core.hooks import maybe_await
from exo.core.loop import Loop
from exo.core.private import private_file
from exo.core.models.router import TIERS
from exo.core.seats import list_seats
from exo.core.sessions import new_id
from exo.core.types import ToolCall, ToolResult
from exo.protocol import schema as s
from exo.principal import require_principal

COLS = ("id", "title", "when", "prompt", "seat", "tier", "deliver", "paused", "last_run", "next_run", "last_status")
EVERY = re.compile(r"^every\s+(\d+)\s*([mhd])$", re.I)
UNIT = {"m": 60, "h": 3600, "d": 86400}
RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
# Aliases: the method Scheduler.list shadows the builtin in the annotations that follow it.
Jobs: TypeAlias = list[dict[str, Any]]
Strs: TypeAlias = list[str]
PROMPT = "You are running a scheduled job unattended. Do the task, then reply with a short plain-English result."


def _field(text: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        rng, _, step = part.partition("/")
        if step and not step.isdigit() or step == "0":
            raise ValueError(f"bad step in {part!r}")
        if rng == "*":
            a, b = lo, hi
        elif m := re.fullmatch(r"(\d+)-(\d+)", rng):
            a, b = int(m[1]), int(m[2])
        elif rng.isdigit():
            a = int(rng)
            b = hi if step else a
        else:
            raise ValueError(f"bad cron field {part!r}")
        if a < lo or b > hi or a > b:
            raise ValueError(f"{part!r} is out of range {lo}-{hi}")
        out.update(range(a, b + 1, int(step or 1)))
    return out


def parse_cron(line: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    """Five sets (minute, hour, day, month, weekday 0-6 with 0 = Sunday) and whether day and weekday were limited."""
    parts = line.split()
    if len(parts) != 5:
        raise ValueError("a cron line needs 5 fields: minute hour day month weekday")
    f = [_field(p, *r) for p, r in zip(parts, RANGES)]
    f[4] = {d % 7 for d in f[4]}
    return f[0], f[1], f[2], f[3], f[4], parts[2] != "*", parts[4] != "*"


def next_run(when: str, after: float) -> float:
    """The first fire time strictly after ``after`` (epoch seconds; cron fields are local time), across DST too."""
    when = when.strip()
    if m := EVERY.match(when):
        n = int(m[1])
        if n < 1:
            raise ValueError("every needs a number of 1 or more")
        return after + n * UNIT[m[2].lower()]
    mi, hr, dom, mon, dow, dom_set, dow_set = parse_cron(when)
    t = datetime.fromtimestamp(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 5):  # days; a valid line fires within five years (Feb 29)
        wd = (t.weekday() + 1) % 7
        day_ok = ((t.day in dom or wd in dow) if dom_set and dow_set else t.day in dom and wd in dow)
        if t.month in mon and day_ok:
            for h in sorted(hr):
                for m_ in sorted(mi):
                    c = t.replace(hour=h, minute=m_)
                    if c >= t:  # naive: when clocks go back, the hour's first pass (fold 0) can be before `after`
                        for fold in (0, 1):
                            if (ts := c.replace(fold=fold).timestamp()) > after:
                                return ts
        t = t.replace(hour=0, minute=0) + timedelta(days=1)
    raise ValueError("this cron line never fires")


class Scheduler:
    def __init__(self, engine: Any, clock: Callable[[], float] = time.time) -> None:
        self.engine, self.clock = engine, clock
        self.db = sqlite3.connect(str(private_file(engine.home / "schedule.sqlite")), check_same_thread=False)
        self._closed = False
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, title TEXT NOT NULL, "when" TEXT NOT NULL,'
                        ' prompt TEXT NOT NULL, seat TEXT, tier TEXT NOT NULL, deliver TEXT NOT NULL,'
                        ' paused INTEGER NOT NULL DEFAULT 0, last_run REAL, next_run REAL, last_status TEXT)')
        self.db.commit()
        self.running: dict[str, asyncio.Task] = {}
        self.loops: dict[str, Loop] = {}  # scheduled child session -> live turn loop
        self._wake = asyncio.Event()
        self._main: asyncio.Future[None] | None = None
        self.error_pause = 5.0  # seconds to wait after a failed tick, so a persistent fault does not spin
        self.poll = 60.0  # longest sleep: a job another seat's core adds or resumes in this home starts in time

    # ------------------------------------------------------------ jobs

    def list(self) -> list[dict[str, Any]]:
        return [{**dict(r), "paused": bool(r["paused"])} for r in self.db.execute("SELECT * FROM jobs ORDER BY rowid")]

    def get(self, job_id: str | None) -> dict[str, Any]:
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if r is None:
            raise ValueError(f"no scheduled job {job_id!r}")
        return {**dict(r), "paused": bool(r["paused"])}

    def add(self, when: str, prompt: str, title: str = "", seat: str | None = None, tier: str = "fast",
            deliver: str = "notice") -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("a job needs a prompt")
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {', '.join(TIERS)}")
        if seat and not self.mine({"seat": seat}) and seat not in {x["name"] for x in list_seats(self.engine.home)}:
            raise ValueError(f"no seat {seat!r}: a job bound to it would never run")
        nxt = next_run(when, self.clock())  # also validates the line
        jid = new_id("j")
        self.db.execute("INSERT INTO jobs (id,title,\"when\",prompt,seat,tier,deliver,paused,next_run) VALUES (?,?,?,?,?,?,?,0,?)",
                        (jid, title.strip() or prompt.strip()[:60], when.strip(), prompt, seat, tier, deliver or "notice", nxt))
        self.db.commit()
        self._wake.set()
        return self.get(jid)

    def remove(self, job_id: str | None) -> None:
        self.get(job_id)
        self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self.db.commit()
        self._wake.set()

    def pause(self, job_id: str | None, paused: bool = True) -> dict[str, Any]:
        job = self.get(job_id)
        nxt = job["next_run"] if paused else next_run(job["when"], self.clock())  # resume skips what was missed
        self.db.execute("UPDATE jobs SET paused=?, next_run=? WHERE id=?", (int(paused), nxt, job_id))
        self.db.commit()
        self._wake.set()
        return self.get(job_id)

    # ------------------------------------------------------------ running

    def mine(self, job: dict[str, Any]) -> bool:
        """Seats of one home share the jobs table: a job bound to a seat runs only in that seat's core."""
        return not job["seat"] or job["seat"] == getattr(self.engine, "agent", None)

    def due(self, now: float | None = None) -> Jobs:
        now = self.clock() if now is None else now
        return [j for j in self.list() if self.mine(j) and not j["paused"] and j["next_run"] is not None
                and j["next_run"] <= now]

    async def tick(self) -> Strs:
        """Start every due job (once each, however many slots were missed); returns the job ids started."""
        started = []
        for job in self.due():
            if job["id"] in self.running:
                continue
            # Claim the slot: another seat's timer may have moved next_run (or someone paused it) since due() read it.
            cur = self.db.execute("UPDATE jobs SET next_run=? WHERE id=? AND next_run=? AND paused=0",
                                  (next_run(job["when"], self.clock()), job["id"], job["next_run"]))
            self.db.commit()
            if cur.rowcount != 1:
                continue
            self.launch(job)
            started.append(job["id"])
        return started

    def launch(self, job: dict[str, Any]) -> str | None:
        """Start a run in the background. None if this job is already running."""
        if job["id"] in self.running:
            return None
        task_id = new_id("t")
        t = self.running[job["id"]] = asyncio.ensure_future(self.run(job, task_id))
        t.add_done_callback(lambda _t: self.running.pop(job["id"], None))
        return task_id

    def _emit(self, job: dict[str, Any], task_id: str, status: str, detail: str, child: str | None = None) -> None:
        self.engine.hub.publish(s.TaskUpdated(task_id=task_id, title=job["title"], status=status,
                                              child_session=child, detail=detail))

    def interrupt(self, session: str) -> bool:
        loop = self.loops.get(session)
        return bool(loop and loop.interrupt(session))

    async def run(self, job: dict[str, Any], task_id: str) -> str:
        e, sid, status, answer = self.engine, None, "failed", ""
        self._emit(job, task_id, "queued", f"Scheduled job {job['title']!r} is waiting to start.")
        try:
            provider, model = e.agents.route(job["tier"])
            names = frozenset(t.name for t in e.registry.specs() if t.name not in BLOCKED_TOOLS)

            async def run_tool(call: ToolCall, lctx: dict[str, Any]) -> ToolResult:
                if call.name not in names:
                    return ToolResult.of_text(call.id, f"Tool {call.name!r} is not available to this scheduled run", True)
                return await e.run_tool(call, lctx)

            # same turn path as every other turn (audit 2026-10-12): the engine's context tree and memory,
            # its soul refresh and system extras, and the turn hooks (through ``e.run_turn``). Only the model,
            # the narrowed toolset and the unattended-job prompt differ.
            loop = Loop(e.store, provider, bus=e.loop.bus, context=e.loop.context, memory=e.loop.memory,
                        tool_specs=lambda: e.registry.specs(names=sorted(names)), run_tool=run_tool,
                        system_prompt="\n\n".join(p for p in (e.loop.system_prompt, PROMPT) if p), model=model,
                        max_retries=e.loop.max_retries, retry_delay=e.loop.retry_delay)
            loop.approval_ms, loop.on_turn_end = e.loop.approval_ms, e.observe_turn
            # The engine's session table: one lock, steer queue and running flag per session, whichever loop runs
            # it, so turn.send, session.list, resume and job or peer notices see this run as the live turn it is.
            loop._state = e.loop._state

            def refresh_prompt() -> str:
                soul = e.loop.before_turn() if e.loop.before_turn is not None else ""
                # Refresh may replace the main prompt. Rebuild this run's snapshot afterward; never append to
                # the main prompt or to the child's previous snapshot (which already carries PROMPT).
                loop.system_prompt = "\n\n".join(p for p in (e.loop.system_prompt, PROMPT) if p)
                return soul or ""

            loop.before_turn, loop.system_extra = refresh_prompt, e.loop.system_extra
            loop.workspace_extra = e.loop.workspace_extra
            info = await loop.create_session(title=f"scheduled: {job['title'][:60]}", model=model, cwd=e.cwd,
                                             agent=getattr(e, "agent", None), kind="scheduled")
            sid = info.id
            self.loops[sid] = loop
            self._emit(job, task_id, "running", f"Running {job['title']!r}.", sid)
            res = await e.run_turn(s.TurnSend(session=sid, text=job["prompt"]), meta={"author": "schedule"},
                                loop=loop)  # D-041 normal path; D-040 not a live principal turn
            answer = res.message.text() if res is not None and res.message is not None else ""
            stop = getattr(res, "stop_reason", "error")
            if stop == "end":
                status = "done"
            else:
                wrapped = stop == "max_iterations" and answer and res.message.meta.get("wrap_up")  # not a tool round
                report = f"\n\n{answer}" if wrapped else ""  # the round-cap wrap-up
                answer = f"it stopped early ({stop}). " + str(getattr(res, "error", "") or "") + report
        except asyncio.CancelledError:
            status, answer = "failed", "it was stopped before it finished."
            raise
        except Exception as exc:
            answer = f"{type(exc).__name__}: {exc}"
        finally:
            if sid is not None:
                self.loops.pop(sid, None)  # cleanup must happen even if final database bookkeeping fails
            self.db.execute("UPDATE jobs SET last_run=?, last_status=? WHERE id=?", (self.clock(), status, job["id"]))
            self.db.commit()
            ok = status == "done"
            self._emit(job, task_id, status, f"{job['title']!r} finished." if ok else f"{job['title']!r} failed: {answer}", sid)
        if ok:
            await self._deliver(job, answer or "(no answer)")
        else:
            e.hub.publish(s.Notice(text=f"Scheduled job {job['title']!r} failed: {answer}", level="warn"))
        return status

    async def _deliver(self, job: dict[str, Any], text: str) -> None:
        e, to = self.engine, job["deliver"]
        msg = f"{job['title']}: {text}"
        chan = e.channels.get(to) if to != "notice" else None
        if chan is not None and hasattr(chan, "deliver"):
            try:
                await maybe_await(chan.deliver(msg))
                return
            except Exception as exc:
                e.hub.publish(s.Notice(text=f"Could not deliver to {to!r}: {exc}", level="warn"))
        elif to != "notice":
            e.hub.publish(s.Notice(text=f"Channel {to!r} cannot take deliveries here; showing the result here.", level="warn"))
        e.hub.publish(s.Notice(text=msg))

    # ------------------------------------------------------------ loop

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("the scheduler is closed; make a new one to run jobs again")
        if self._main is None:
            self._main = asyncio.ensure_future(self._run_loop())
            self._main.add_done_callback(self._loop_ended)

    def _loop_ended(self, task: asyncio.Future) -> None:
        """The loop only ends by stop(); any other end is a bug that must not pass quietly."""
        if task.cancelled() or self._main is None:
            return
        exc = task.exception()
        trace.report("scheduler loop", exc or "the loop ended without being stopped")
        self.engine.hub.publish(s.Notice(text="The scheduler stopped; scheduled jobs will not run until restart.",
                                         level="warn"))

    def tasks(self) -> tuple[asyncio.Future[Any], ...]:
        """The scheduler's own loop and the jobs it runs, for an owner that cancels them (the engine's close)."""
        return tuple(t for t in (self._main, *self.running.values()) if t is not None)

    async def stop(self) -> None:
        main, self._main = self._main, None
        tasks = [t for t in [main, *self.running.values()] if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.running.clear()

    def close(self) -> None:
        """Close schedule.sqlite. Call after stop(). It is final: start() refuses afterwards, and any other
        call that reads or writes jobs raises sqlite3.ProgrammingError. stop() alone keeps it restartable."""
        self._closed = True
        self.db.close()

    async def wait(self) -> None:
        """Wait for runs in flight (tests, shutdown)."""
        await asyncio.gather(*list(self.running.values()), return_exceptions=True)

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.tick()
                nxt = [j["next_run"] for j in self.list()
                       if self.mine(j) and not j["paused"] and j["next_run"] is not None]  # not another seat's
                delay = max(0.0, min(nxt) - self.clock()) if nxt else 3600.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad tick (a bad row, a full disk) must not end every future job
                trace.report("scheduler tick", exc)
                await asyncio.sleep(self.error_pause)
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), min(delay, self.poll) + 0.05)
            except asyncio.TimeoutError:
                pass
            task = asyncio.current_task()
            if task is not None and task.cancelling():  # Python 3.11's wait_for returns when a wake meets a cancel
                raise asyncio.CancelledError  # so close() and stop() still end the loop before it claims a slot


    async def edit(self, body: Any) -> dict[str, Any]:
        """The ``schedule.edit`` command; ``{"error": ...}`` for a bad request."""
        return await handle_schedule_edit(self, body)

async def handle_schedule_edit(sch: Scheduler, body: Any) -> dict[str, Any]:
    """schedule.edit: list, add, remove, pause, resume, run_now (``Scheduler.edit``)."""
    b = asdict(body) if is_dataclass(body) and not isinstance(body, type) else dict(body)
    act = b.get("action")
    if act != "list":
        require_principal("change scheduled jobs")
    try:
        if act == "list":
            return {"jobs": sch.list()}
        if act == "add":
            return {"job": sch.add(b.get("when") or "", b.get("prompt") or "", b.get("title") or "", b.get("seat"),
                                   b.get("tier") or "fast", b.get("deliver") or "notice")}
        if act == "remove":
            sch.remove(b.get("job_id"))
            return {"removed": b.get("job_id")}
        if act in ("pause", "resume"):
            return {"job": sch.pause(b.get("job_id"), act == "pause")}
        if act == "run_now":
            job = sch.get(b.get("job_id"))
            if not sch.mine(job):
                return {"job": job, "error": f"this job belongs to seat {job['seat']!r}; run it from that seat"}
            task_id = sch.launch(job)
            return {"job": job, "task_id": task_id} if task_id else {"job": job, "error": "already running"}
    except ValueError as exc:
        return {"error": str(exc)}
    return {"error": f"unknown action {act!r}"}


__all__ = ["Scheduler", "handle_schedule_edit", "next_run", "parse_cron"]
