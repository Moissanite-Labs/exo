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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from exo.core import trace
from exo.core.ingress import Ingress, IngressPath, Receipt
from exo.core.models import keys
from exo.core.peer_target import EngineTarget
from exo.core.sessions import new_id
from exo.core.tools.builtin.shell import kill_process_group, masked_note, open_side, tool_env, wrap_pipestatus
from exo.core.tools.registry import ToolContext, ToolError
from exo.core.types import ToolSpec
from exo.protocol import schema as s

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine

ACTIVE = "running"
KEEP_DAYS = 14
ROTATED = "[output rotated: earlier output is in {}.1]\n"  # the live log's first line after it rotates


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
        raw, c = raw or {}, cls()
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
    """The last ``lines`` lines of a log, at most ``chars`` characters (the end is kept), with the key ``values`` cut
    out before any cut. After a rotation the end of ``<id>.log.1`` stands in for the note and grows until no match in
    it can change the live log's spans. The live log's own spans are cut out too (docs/JOBS.md, the D-039 amendment)."""
    try:
        data = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""
    note, m, pre = ROTATED.format(path.name), max(map(len, values), default=0), ""
    n = m - 1 if data.startswith(note) else 0
    while n > 0:  # a character takes 4 bytes at most, so the last n characters of 4n bytes are those of the file
        whole, u = True, 0  # also when <id>.log.1 cannot be read: then pre stays as it was
        with contextlib.suppress(OSError), open(path.with_suffix(".log.1"), "rb") as f:
            whole = not f.seek(max(0, f.seek(0, os.SEEK_END) - 4 * n))
            pre = f.read().decode("utf-8", errors="replace")[0 if whole else -n:]
        text = pre + data[len(note):len(note) + m]  # a match that starts in pre ends in here
        for v in values:  # in redaction order, u is where spans may change, from pre's start
            u = max(u, len(v) - 1)
            while (i := text.rfind(v, 0, u + len(v) - 1)) + len(v) > u:  # the last match that starts before u
                u = i + len(v)
        n = 0 if whole or u <= len(pre) else 2 * n
    text, at, parts, red = pre + data[len(note):] if pre else data, len(pre), [note] if pre else [], -1
    live = [(a + at, b + at) for a, b in keys.secret_spans(text[at:], values)[0]] if pre else []
    for a, b in sorted(keys.secret_spans(text, values)[0] + live):  # with live, never less than the log alone
        if b > at:  # a span inside pre goes with it, and one that crosses into the log is cut out from its start
            parts += (text[at:max(a, at)], keys.REDACTED) if a >= red else ()  # one that overlaps the last joins it
            at = red = b
    data = ("".join(parts) + text[at:]).rstrip("\n")
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

    def _meta(self, ing: Ingress) -> dict[str, Any]:
        return {"job": {"id": ing.meta.get("job_id"), "state": ing.meta.get("state"), "msg_id": ing.id},
                "author": "job"}  # D-040: the harness, not the principal

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
        self._order, self._pool = asyncio.Lock(), ThreadPoolExecutor(1, "exo-notice")  # its own: tools never hold it
        self.recovered = False

    # ---------------------------------------------------------------- start

    def _threadsafe(self, fn: Any, *args: Any) -> None:
        loop = self.loop
        if loop is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(fn, *args)

    def _known(self) -> list[str]:
        with contextlib.suppress(Exception):  # a failed key lookup never loses a notice or an event
            return keys.known_secret_values()  # cut out before each cut (D-039)
        return []

    def _body(self, job_id: str, state: str, command: str, code: int | None, started: float, ended: float | None,
              detail: str = "", log: str | None = None) -> s.JobUpdated:
        return s.JobUpdated(job_id=job_id, state=state, command=keys.redact_secrets(command, self._known())[:300],
                            exit_code=code, elapsed_ms=int(_elapsed(started, ended) * 1000),
                            log=log or str(self.log_path(job_id)), detail=detail)

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
        body = self._body(job_id, ACTIVE, command, None, h.started, None, f"pid {proc.pid}, limit {fmt_elapsed(wall)}")
        self._threadsafe(self.e.hub.publish, body, session)
        return {"id": job_id, "pid": proc.pid, "log": str(log), "wall_s": wall}

    # ---------------------------------------------------------------- run

    def _pump(self, h: Handle, log: Path) -> None:
        """Copy the job's output to its log; past the cap the log rotates (one older file is kept)."""
        cap, size, out = self.cfg.output_cap, 0, open(log, "ab", buffering=0)
        try:
            for chunk in iter(lambda: h.proc.stdout.read1(65536), b""):  # type: ignore[union-attr]
                while chunk:
                    if size >= cap:
                        out.close()
                        os.replace(log, log.with_suffix(".log.1"))
                        out = open(log, "ab", buffering=0)
                        out.write(note := ROTATED.format(log.name).encode())
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
        if self.live.pop(h.id, None) is None:
            return
        ended = time.time()
        note = masked_note(h.side) if h.side else ""  # also removes the side file
        if h.reason == "killed":
            state, detail = "killed", f"stopped by {h.kill_by or 'job_kill'}"
        elif h.reason == "timeout":
            state, detail = "timeout", f"killed after {fmt_elapsed(self.cfg.max_wall_s)} (the job wall limit)"
        else:
            state, detail = ("done", "") if code == 0 and not note else ("failed", note)
        self.e.store._q("UPDATE jobs SET state=?, exit_code=?, ended=?, detail=? WHERE id=?", (state, code, ended, detail, h.id))
        self._spawn(self._announce(h, state, code, ended, detail))

    def _spawn(self, coro: Any) -> None:
        self._tasks.add(task := asyncio.ensure_future(coro))  # close() waits for these
        task.add_done_callback(self._tasks.discard)

    async def _announce(self, h: Handle, state: str, code: int, ended: float, detail: str) -> None:
        try:
            if state != "killed" or h.kill_by != "job_kill":  # an agent that killed it knows: no notice for that
                rec = await self.path.submit(await self._built(h.id, state, code, h.command, h.started, ended,
                                                               h.session, detail))
                detail = (detail + "; " if detail else "") + f"notice {rec.mode or rec.state}"
        except Exception as exc:  # a delivery problem never loses the job's end
            trace.report("jobs.notice", exc, h.session)
            detail = (detail + "; " if detail else "") + f"notice failed: {exc}"
        finally:  # nor does a hard stop that cancels this task while the notice is built
            self.e.hub.publish(self._body(h.id, state, h.command, code, h.started, ended, detail), h.session)

    async def _built(self, *args: Any) -> Ingress:
        """``_notice`` in the notice thread, one at a time, so each is handed on before the next, as the jobs ended."""
        async with self._order:
            return await asyncio.get_running_loop().run_in_executor(self._pool, self._notice, *args)

    async def _queue(self, *args: Any) -> None:
        try:  # in order, as _built hands each on. A turn that started meanwhile takes it at its next tool round
            if not await self.target.steer(args[6], ing := await self._built(*args)):
                await self.target.queue(args[6], ing)  # never a wake
        except Exception as exc:  # a lost job's notice that fails is reported, as _announce reports one
            trace.report("jobs.notice", exc, args[6])

    def _notice(self, job_id: str, state: str, code: int | None, command: str, started: float, ended: float | None,
                session: str | None, detail: str = "") -> Ingress:
        head = {"done": "finished, exit 0", "failed": ("failed earlier stage, final exit 0" if code == 0 else f"failed, exit {code}"), "timeout": "timed out and was killed",
                "killed": "was killed", "lost": "lost"}.get(state, state)
        head += f", {fmt_elapsed(_elapsed(started, ended))}"
        vals = self._known()
        out = tail_text(self.log_path(job_id), self.cfg.notice_lines, self.cfg.notice_chars, vals)
        body = (f"command: {keys.redact_secrets(command, vals)[:300]}\n" + (f"{detail}\n" if detail else "")
                + (f"last output:\n{out}\n" if out else "no output\n") + f"full log: {self.log_path(job_id)} "
                f"(job_output {job_id})")
        return Ingress("job", "harness", "local", body, session=session, id=new_id("jn"),
                       meta={"local": True, "job_id": job_id, "state": state, "head": head})

    # ---------------------------------------------------------------- queries and control

    def rows(self, limit: int = 30) -> list[dict[str, Any]]:
        return [dict(r) for r in self.e.store._q("SELECT * FROM jobs WHERE agent=? ORDER BY (state='running') DESC, "
                                                 "started DESC LIMIT ?", (self.e.agent, limit))]

    def get(self, job_id: str) -> dict[str, Any]:
        rs = self.e.store._q("SELECT * FROM jobs WHERE agent=? AND id=?", (self.e.agent, job_id.strip()))
        if not rs:
            raise ToolError(f"no job {job_id!r}; `jobs` lists them")
        return dict(rs[0])

    def read_log(self, job_id: str) -> str:
        p = self.log_path(job_id)
        parts: list[str] = []
        for f in (p.with_suffix(".log.1"), p):  # with .log.1 read, the live log's note goes, so a split key is whole
            with contextlib.suppress(OSError):
                text = f.read_bytes().decode("utf-8", errors="replace")
                parts.append(text.removeprefix(ROTATED.format(p.name)) if parts else text)
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
        try:
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
                self.e.hub.publish(self._body(h.id, "killed", h.command, h.proc.returncode, h.started, None,
                                              "stopped when exod shut down"), h.session)
            await asyncio.gather(*self._tasks, return_exceptions=True)  # notices of jobs that ended just before
            with contextlib.suppress(RuntimeError):  # an earlier close() shut the pool down already
                await asyncio.wrap_future(self._pool.submit(int))  # after every build before it, cancelled or not
        finally:  # a hard stop at any await here: the pool still takes no more work, and its build runs on to its end
            self._pool.shutdown(wait=False)
        self._pool.shutdown()  # its one thread is idle now, so this joins it at once
        await self.path.close()

    async def recover(self) -> list[str]:
        """After a restart: jobs the database says were running are ``lost``. Each owning thread gets a notice that
        is steered or queued, never a wake. Old finished jobs and logs are pruned. No awaits, so start() runs once."""
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
            self.e.hub.publish(self._body(r["id"], "lost", r["command"], None, r["started"], now, detail, r["log"]), sid)
            if sid and self.e.store.get_session(sid) is not None:  # built in a task close() waits for
                self._spawn(self._queue(r["id"], "lost", None, r["command"], r["started"], now, sid, detail))
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
