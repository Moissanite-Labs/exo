"""Background shell jobs (D-032, docs/JOBS.md).

``bash`` with ``background: true`` returns a job id at once. The command runs in its own process group,
its output goes to ``$EXO_HOME/run/jobs/<id>.log`` (capped, with one rotation), and it belongs to the agent,
not to the turn: a turn ending or being interrupted does not touch it, ``job_kill`` does. When it ends the
core tells the owning thread through the same ingress path peers use (``core/ingress.py``): steered into a
running turn, else queued for the next one, and a turn is started only when the seat allows it.

Jobs end with exod: closing the engine kills every running job's process group. After a crash the next
start reports what was running as ``lost`` (the process may still be alive; its pid is in the notice).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from exo.core.ingress import Ingress, IngressPath, Receipt
from exo.core.peer_target import EngineTarget
from exo.core.sessions import new_id
from exo.core.tools.builtin.shell import kill_process_group, masked_note, open_side, tool_env, wrap_pipestatus
from exo.core.tools.registry import ToolContext, ToolError
from exo.core.types import ContentPart, Message, ToolSpec
from exo.protocol import schema as s

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine

ACTIVE = "running"
KEEP_DAYS = 14


@dataclass
class JobConfig:
    max_concurrent: int = 4          # running jobs per agent
    max_wall_s: int = 7200           # a job running longer is killed (state timeout)
    output_cap: int = 5_000_000      # bytes in the live log; past it the log rotates to <id>.log.1 (one kept)
    wake: bool | None = None         # None: off for an interactive seat, on for a headless one (as peers)
    notice_lines: int = 20           # last lines of output a completion notice carries
    notice_chars: int = 1500

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> "JobConfig":
        raw = raw or {}
        c = cls()
        for k in ("max_concurrent", "max_wall_s", "output_cap", "notice_lines", "notice_chars"):
            if raw.get(k) not in (None, ""):
                setattr(c, k, max(1, int(raw[k])))
        c.wake = raw.get("wake")
        return c


@dataclass
class Handle:
    """A running job's process and threads (the database row is the record that survives)."""

    id: str
    proc: subprocess.Popen
    session: str | None
    command: str
    started: float
    reason: str = ""                  # killed or timeout, set before the process group is signalled
    kill_by: str = ""
    side: str = ""                    # side file with the final pipeline's PIPESTATUS (see shell.wrap_pipestatus)
    timer: threading.Timer | None = field(default=None, repr=False)


def _elapsed(start: float, end: float | None) -> float:
    return max(0.0, (end or time.time()) - start)


def fmt_elapsed(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m"


def tail_text(path: Path, lines: int, chars: int, values: Sequence[str] = ()) -> str:
    """The last ``lines`` lines of a log, at most ``chars`` characters (the end is kept). The whole file is read and
    the key ``values`` are cut out as ``[redacted]`` before any cut, so no cut here splits a key."""
    from exo.core.models.keys import redact_secrets
    try:
        data = redact_secrets(path.read_bytes().decode("utf-8", errors="replace"), values).rstrip("\n")
    except OSError:
        return ""
    out = "\n".join(data[-chars * 4:].splitlines()[-lines:])  # splitting all of data gives the same out
    return out if len(out) <= chars else "..." + out[-chars:]


class JobTarget(EngineTarget):
    """The ingress target for job notices: the engine's own delivery, with the job wake rule and no peer events."""

    def __init__(self, engine: "CoreEngine", cfg: JobConfig) -> None:
        super().__init__(engine)
        self.cfg = cfg
        self.closed = False  # exod is shutting down: a notice is kept for the next turn and never starts one

    def can_wake(self, session: str) -> bool:
        if self.closed:
            return False
        if self.cfg.wake is None:  # a client on the event stream means somebody is watching
            return not self.e.hub._subs
        return bool(self.cfg.wake)

    def _message(self, ing: Ingress) -> Message:
        from exo.core.ingress import frame

        msg = Message(role="user", content=[ContentPart.of_text(frame(ing))])
        msg.meta.update(self._meta(ing))
        return msg

    def _meta(self, ing: Ingress) -> dict[str, Any]:
        return {"job": {"id": ing.meta.get("job_id"), "state": ing.meta.get("state"), "msg_id": ing.id},
                "author": "job"}  # D-040: the harness, not the principal

    async def wake(self, session: str, ing: Ingress) -> None:
        from exo.core.ingress import frame

        self.e.start_turn(session, frame(ing), self._meta(ing))

    async def record(self, ing: Ingress, receipt: Receipt, notify: bool) -> None:
        pass  # the job.updated event carries the delivery mode


class JobManager:
    def __init__(self, engine: "CoreEngine", cfg: dict[str, Any] | None = None) -> None:
        self.e = engine
        self.cfg = JobConfig.of(cfg)
        self.dir = Path(engine.home) / "run" / "jobs"
        self.live: dict[str, Handle] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.target = JobTarget(engine, self.cfg)
        self.path = IngressPath(self.target, lambda ing: ("auto", "a job the agent started"))
        self._lock = threading.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.recovered = False

    # ---------------------------------------------------------------- start

    def _threadsafe(self, fn: Any, *args: Any) -> None:
        loop = self.loop
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(fn, *args)

    def _publish(self, h_id: str, state: str, command: str, session: str | None, code: int | None, started: float,
                 ended: float | None, detail: str = "") -> None:
        body = s.JobUpdated(job_id=h_id, state=state, command=command[:300], exit_code=code,
                            elapsed_ms=int(_elapsed(started, ended) * 1000), log=str(self.log_path(h_id)),
                            detail=detail)
        self._threadsafe(self.e.hub.publish, body, session)

    def log_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.log"

    def start(self, command: str, cwd: str, session: str | None, timeout: float | None = None) -> dict[str, Any]:
        """Start ``command`` as a job of ``session``'s agent. Thread-safe: the bash tool calls it from a worker."""
        if not command.strip():
            raise ToolError("command is empty")
        if self.loop is None:
            with contextlib.suppress(RuntimeError):
                self.loop = asyncio.get_running_loop()
        if session:
            _agent, kind = self.e.store.thread(session)
            if kind == "subagent":
                raise ToolError("background jobs are for the main agent's threads; a subagent runs its commands in the "
                                "foreground")
        wall = min(float(timeout or self.cfg.max_wall_s), float(self.cfg.max_wall_s))
        with self._lock:
            if len(self.live) >= self.cfg.max_concurrent:
                raise ToolError(f"{len(self.live)} jobs are already running (limit {self.cfg.max_concurrent}). Wait for "
                                "one to finish or stop one with job_kill; `jobs` lists them.")
            job_id = "j_" + new_id("")[1:7]
            while self.log_path(job_id).exists() or job_id in self.live:
                job_id = "j_" + new_id("")[1:7]
            self.dir.mkdir(parents=True, exist_ok=True)
            log = self.log_path(job_id)
            log.write_bytes(b"")
            os.chmod(log, 0o600)
            side_fd, side = open_side()
            env = tool_env(allow=getattr(self.e, "env_allow", ()), deny=getattr(self.e, "env_deny", ()))
            try:
                proc = subprocess.Popen(wrap_pipestatus(command, side_fd, env), cwd=cwd, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                                        pass_fds=(side_fd,), env=env)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(side)
                raise
            finally:
                os.close(side_fd)
            h = Handle(job_id, proc, session, command, time.time(), side=side)
            self.live[job_id] = h
        self.e.store._q("INSERT INTO jobs (id, agent, session, command, cwd, state, pid, started, log) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", (job_id, self.e.agent, session, command, cwd, ACTIVE, proc.pid,
                                                       h.started, str(log)))
        pump = threading.Thread(target=self._pump, args=(h, log), daemon=True)
        pump.start()
        h.timer = threading.Timer(wall, self._expire, args=(h,))
        h.timer.daemon = True
        h.timer.start()
        threading.Thread(target=self._watch, args=(h, pump), daemon=True).start()
        self._publish(job_id, ACTIVE, command, session, None, h.started, None, f"pid {proc.pid}, limit {fmt_elapsed(wall)}")
        return {"id": job_id, "pid": proc.pid, "log": str(log), "wall_s": wall}

    # ---------------------------------------------------------------- run

    def _pump(self, h: Handle, log: Path) -> None:
        """Copy the job's output to its log; past the cap the log rotates (one older file is kept)."""
        cap = self.cfg.output_cap
        out = open(log, "ab", buffering=0)
        size = 0
        try:
            for chunk in iter(lambda: h.proc.stdout.read1(65536), b""):  # type: ignore[union-attr]
                while chunk:
                    if size >= cap:
                        out.close()
                        os.replace(log, log.with_suffix(".log.1"))
                        out = open(log, "ab", buffering=0)
                        note = f"[output rotated: earlier output is in {log.name}.1]\n".encode()
                        out.write(note)
                        size = len(note)
                    part, chunk = chunk[:max(1, cap - size)], chunk[max(1, cap - size):]
                    out.write(part)
                    size += len(part)
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                out.close()

    def _watch(self, h: Handle, pump: threading.Thread) -> None:
        h.proc.wait()
        pump.join(timeout=2)
        if pump.is_alive():  # stragglers still hold the pipe: the job is over when its shell is
            kill_process_group(h.proc, grace=0.2)
            pump.join(timeout=2)
        with contextlib.suppress(Exception):
            h.proc.stdout.close()  # type: ignore[union-attr]
        if h.timer:
            h.timer.cancel()
        self._threadsafe(self._finish, h, h.proc.returncode if h.proc.returncode is not None else -1)

    def _expire(self, h: Handle) -> None:
        if h.proc.poll() is None:
            h.reason = h.reason or "timeout"
            kill_process_group(h.proc)

    def _finish(self, h: Handle, code: int) -> None:
        """On the event loop: record the end, tell clients, and tell the owning thread."""
        if h.id not in self.live:
            return
        self.live.pop(h.id, None)
        ended = time.time()
        note = masked_note(h.side) if h.side else ""  # also removes the side file
        if h.reason == "killed":
            state, detail = "killed", f"stopped by {h.kill_by or 'job_kill'}"
        elif h.reason == "timeout":
            state, detail = "timeout", f"killed after {fmt_elapsed(self.cfg.max_wall_s)} (the job wall limit)"
        else:
            state = "done" if code == 0 else "failed"
            detail = note if code == 0 else ""
        self.e.store._q("UPDATE jobs SET state=?, exit_code=?, ended=?, detail=? WHERE id=?", (state, code, ended, detail, h.id))
        task = asyncio.ensure_future(self._announce(h, state, code, ended, detail))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _announce(self, h: Handle, state: str, code: int, ended: float, detail: str) -> None:
        _mode = ""
        if state != "killed" or h.kill_by != "job_kill":  # an agent that killed it knows: no notice for that
            try:
                rec = await self.path.submit(self._notice(h.id, state, code, h.command, h.started, ended, h.session,
                                                          detail))
                _mode = rec.mode or rec.state
                detail = (detail + "; " if detail else "") + f"notice {rec.mode or rec.state}"
            except Exception as exc:  # a delivery problem never loses the job's end
                from exo.core import trace

                trace.report("jobs.notice", exc, h.session)
                detail = (detail + "; " if detail else "") + f"notice failed: {exc}"
        self.e.hub.publish(s.JobUpdated(job_id=h.id, state=state, command=h.command[:300], exit_code=code,
                                        elapsed_ms=int(_elapsed(h.started, ended) * 1000), log=str(self.log_path(h.id)),
                                        detail=detail), h.session)

    def _notice(self, job_id: str, state: str, code: int | None, command: str, started: float, ended: float | None,
                session: str | None, detail: str = "") -> Ingress:
        head = {"done": "finished, exit 0", "failed": f"failed, exit {code}", "timeout": "timed out and was killed",
                "killed": "was killed", "lost": "lost"}.get(state, state)
        head += f", {fmt_elapsed(_elapsed(started, ended))}"
        from exo.core.models.keys import known_secret_values, redact_secrets
        vals: list[str] = []
        with contextlib.suppress(Exception):  # a failed key lookup never loses a notice
            vals = known_secret_values()  # cut out before each cut below (D-039)
        out = tail_text(self.log_path(job_id), self.cfg.notice_lines, self.cfg.notice_chars, vals)
        body = (f"command: {redact_secrets(command, vals)[:300]}\n" + (f"{detail}\n" if detail else "")
                + (f"last output:\n{out}\n" if out else "no output\n") + f"full log: {self.log_path(job_id)} "
                f"(job_output {job_id})")
        return Ingress("job", "harness", "local", body, session=session, id=new_id("jn"),
                       meta={"local": True, "job_id": job_id, "state": state, "head": head})

    # ---------------------------------------------------------------- queries and control

    def rows(self, limit: int = 30) -> list[dict[str, Any]]:
        rs = self.e.store._q("SELECT * FROM jobs WHERE agent=? ORDER BY (state='running') DESC, started DESC LIMIT ?",
                             (self.e.agent, limit))
        return [dict(r) for r in rs]

    def get(self, job_id: str) -> dict[str, Any]:
        rs = self.e.store._q("SELECT * FROM jobs WHERE agent=? AND id=?", (self.e.agent, job_id.strip()))
        if not rs:
            raise ToolError(f"no job {job_id!r}; `jobs` lists them")
        return dict(rs[0])

    def read_log(self, job_id: str) -> str:
        p = self.log_path(job_id)
        old = p.with_suffix(".log.1")
        parts = []
        for f in (old, p):
            with contextlib.suppress(OSError):
                parts.append(f.read_bytes().decode("utf-8", errors="replace"))
        return "".join(parts)

    def kill(self, job_id: str, by: str = "job_kill") -> bool:
        h = self.live.get(job_id)
        if h is None or h.proc.poll() is not None:
            return False
        h.reason, h.kill_by = "killed", by
        kill_process_group(h.proc)
        return True

    async def close(self) -> None:
        """Jobs end with exod: stop every running job's process group, then record it once, with no notice."""
        self.target.closed = True
        with self._lock:  # out of live first, so _finish leaves these to this loop when the watcher sees them end
            handles = list(self.live.values())
            self.live.clear()
        for h in handles:
            h.reason, h.kill_by = "killed", "exod shutting down"
            with contextlib.suppress(Exception):
                await asyncio.to_thread(kill_process_group, h.proc)
            if h.timer:
                h.timer.cancel()
            masked_note(h.side)  # removes the side file, as _finish would
            self.e.store._q("UPDATE jobs SET state='killed', exit_code=?, ended=?, detail=? WHERE id=?",
                            (h.proc.returncode, time.time(), "stopped when exod shut down", h.id))
            self.e.hub.publish(s.JobUpdated(job_id=h.id, state="killed", command=h.command[:300], exit_code=h.proc.returncode,
                                            elapsed_ms=int(_elapsed(h.started, None) * 1000), log=str(self.log_path(h.id)),
                                            detail="stopped when exod shut down"), h.session)
        await asyncio.gather(*self._tasks, return_exceptions=True)  # notices of jobs that ended just before
        await self.path.close()

    async def recover(self) -> list[str]:
        """After a restart: jobs the database says were running are ``lost``. Each owning thread gets a notice
        (queued, never waking it). Old finished jobs and their logs are pruned."""
        if self.recovered:
            return []
        self.recovered = True
        lost = []
        for r in self.e.store._q("SELECT * FROM jobs WHERE agent=? AND state='running'", (self.e.agent,)):
            if r["id"] in self.live:
                continue
            now = time.time()
            detail = f"exod restarted while it ran; its process (pid {r['pid']}) may still be running and was not touched"
            self.e.store._q("UPDATE jobs SET state='lost', ended=?, detail=? WHERE id=?", (now, detail, r["id"]))
            lost.append(r["id"])
            sid = r["session"]
            self.e.hub.publish(s.JobUpdated(job_id=r["id"], state="lost", command=r["command"][:300],
                                            elapsed_ms=int(_elapsed(r["started"], now) * 1000), log=r["log"],
                                            detail=detail), sid)
            if sid and self.e.store.get_session(sid) is not None:
                ing = self._notice(r["id"], "lost", None, r["command"], r["started"], now, sid, detail)
                await self.target.queue(sid, ing)
        cutoff = time.time() - KEEP_DAYS * 86400
        for r in self.e.store._q("SELECT id, log FROM jobs WHERE agent=? AND state!='running' AND ended<?",
                                 (self.e.agent, cutoff)):
            for f in (Path(r["log"]), Path(r["log"] + ".1")):
                with contextlib.suppress(OSError):
                    f.unlink()
            self.e.store._q("DELETE FROM jobs WHERE id=?", (r["id"],))
        return lost


def job_text(store: Any, session: str, last: int = 20) -> str:
    """Job events of a session for ``exo trace`` and ``/trace`` (D-032): start, end and how the notice was delivered."""
    from exo.core import trace

    rows = [e for e in trace._events(store, session, deltas=False) if e.type == "job.updated"][-last:]
    if not rows:
        return ""
    lines = ["jobs"]
    for e in rows:
        b = e.body
        code = "" if b.exit_code is None else f" exit {b.exit_code}"
        lines.append(f"  {(e.ts or '')[11:19]}  {b.job_id} {b.state}{code} {fmt_elapsed(b.elapsed_ms / 1000)}  "
                     f"{trace.scrub(b.command, 50)}" + (f"  ({trace.scrub(b.detail, 80)})" if b.detail else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- the tools

JOBS = ToolSpec(
    name="jobs", toolset="shell", risk="low",
    description=("List your background shell jobs (started with bash `background: true`): id, state (running, done, "
                 "failed, killed, timeout, lost), elapsed time, exit code and command."),
    parameters={"type": "object", "properties": {}})
JOB_OUTPUT = ToolSpec(
    name="job_output", toolset="shell", risk="low",
    description=("Read a background job's output so far, from its log. `tail` keeps the last N lines (default 40); "
                 "`grep` keeps only lines matching a regular expression (case-insensitive) before the tail."),
    parameters={"type": "object", "required": ["id"], "properties": {
        "id": {"type": "string", "description": "A job id from `jobs` or the bash result, like j_3fa91c."},
        "tail": {"type": "integer", "description": "Last N lines (default 40, at most 500)."},
        "grep": {"type": "string", "description": "Regular expression; only matching lines are kept."}}})
JOB_KILL = ToolSpec(
    name="job_kill", toolset="shell", risk="medium",
    description=("Stop a running background job: its whole process group gets TERM, then KILL after a second. "
                 "Stopping a turn never stops a job; this is the only way to stop one early."),
    parameters={"type": "object", "required": ["id"], "properties": {
        "id": {"type": "string", "description": "A running job's id from `jobs`."}}})


def attach(engine: "CoreEngine", cfg: dict[str, Any] | None) -> JobManager:
    """Make the engine job-aware: the manager, and the jobs, job_output and job_kill tools. ``bash`` finds the
    manager in ``ctx.meta['jobs']`` (set by ``CoreEngine.run_tool``)."""
    mgr = JobManager(engine, cfg)

    def listing(args: dict[str, Any], ctx: ToolContext) -> str:
        rows = mgr.rows()
        if not rows:
            return "No background jobs."
        now = time.time()
        lines = [f"{len(mgr.live)} running, limit {mgr.cfg.max_concurrent}; {len(rows)} listed:"]
        for r in rows:
            end = r["ended"] if r["state"] != ACTIVE else None
            code = "-" if r["exit_code"] is None else str(r["exit_code"])
            lines.append(f"- {r['id']}  {r['state']:<8} {fmt_elapsed(_elapsed(r['started'], end or now)):>7}  exit {code}  "
                         f"{r['command'][:100]}")
        return "\n".join(lines)

    def output(args: dict[str, Any], ctx: ToolContext) -> str:
        row = mgr.get(str(args.get("id") or ""))
        n = max(1, min(int(args.get("tail") or 40), 500))
        text = mgr.read_log(row["id"])
        lines = text.splitlines()
        if args.get("grep"):
            try:
                rx = re.compile(str(args["grep"]), re.I)
            except re.error as exc:
                raise ToolError(f"grep is not a valid regular expression: {exc}") from None
            lines = [ln for ln in lines if rx.search(ln)]
        shown = "\n".join(lines[-n:])
        if len(shown) > 20_000:
            shown = "..." + shown[-20_000:]
        end = row["ended"] if row["state"] != ACTIVE else None
        code = "" if row["exit_code"] is None else f", exit {row['exit_code']}"
        head = (f"job {row['id']}: {row['state']}{code}, {fmt_elapsed(_elapsed(row['started'], end))}; "
                f"{len(lines)} line(s){' matching' if args.get('grep') else ''}, showing the last {min(n, len(lines))}")
        return head + "\n" + (shown if shown else "(no output yet)")

    async def kill(args: dict[str, Any], ctx: ToolContext) -> str:
        row = mgr.get(str(args.get("id") or ""))
        if row["state"] != ACTIVE:
            return f"job {row['id']} is not running ({row['state']}); nothing to stop."
        ok = await asyncio.to_thread(mgr.kill, row["id"], "job_kill")
        return (f"job {row['id']} stopped (was running {fmt_elapsed(_elapsed(row['started'], None))})." if ok
                else f"job {row['id']} had just ended on its own.")

    engine.registry.register(JOBS, listing, replace=True)
    engine.registry.register(JOB_OUTPUT, output, replace=True)
    engine.registry.register(JOB_KILL, kill, replace=True)
    return mgr
