"""Harness self-observation (D-025): read a session's trace, flag anomalies, report caught errors.

Three jobs, one module, so the CLI (`exo trace`), the `exo_trace` tool and the `/trace` command agree:

- ``build`` turns a session's events into per-turn rows. Sessions recorded before D-025 have no
  ``model.call`` events; their rows are derived from timestamps (turn.started to turn.completed,
  tool.started to tool.completed, usage.updated) and marked ``derived``.
- ``detect`` applies the anomaly rules. Every flag has a stable ``signature`` so the findings store
  can count a recurrence instead of listing it twice.
- ``report`` and ``scrub`` are for code that catches an exception: it becomes a ``harness.error``
  event, never a silent pass, and never carries a key, header or environment value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import traceback
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from exo.core.slots import NotConfigured
from exo.protocol import schema as ev

logger = logging.getLogger(__name__)

SKIP_TYPES = ("message.delta", "reasoning.delta", "tool.output")  # high volume; only old sessions need the deltas
_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{12,}|xox[abp]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?i:(?:api[_-]?key|token|secret|password|passwd|authorization|x-api-key)[\"']?\s*[=:]\s*)[\"']?(?:(?i:bearer|basic)\s+)?[^\s\"',;]{6,})")


def scrub(text: Any, limit: int = 400) -> str:
    """Plain text with anything that looks like a key, token or auth header removed, cut to ``limit``."""
    out = _SECRET.sub("[redacted]", str(text or ""))
    return out if len(out) <= limit else out[: limit - 3] + "..."


# ---------------------------------------------------------------- caught errors

Sink = Callable[[Any, str | None], None]
# Engines register one: publish(body, session). A bound method is held weakly, so an engine nobody closed can
# still be collected (with its open databases) instead of living, and receiving every report, until exit.
# The list is an immutable tuple replaced whole (copy on write): add_sink and remove_sink build a new one under the
# lock, and report reads it once with no lock. A garbage-collector finalizer can run inside any allocation and call
# report, add_sink or remove_sink on the thread that holds the lock, so the lock is re-entrant and nothing waits on
# it inside report. No sink callback ever runs under the lock.
_sinks: tuple[Callable[[], Sink | None], ...] = ()
_sinks_lock = threading.RLock()


def add_sink(fn: Sink) -> None:
    """Send every later report to ``fn(body, session)`` until ``remove_sink(fn)``.

    A bound method is held weakly and does not keep its owner alive: once nothing else holds the owner, its
    reports stop. A plain function, or a method whose owner cannot be weakly referenced (a ``__slots__`` class
    without ``__weakref__``), is held strongly. A report already delivering may still call a sink removed after
    it began.
    """
    global _sinks
    ref: Callable[[], Sink | None] | None = None
    if hasattr(fn, "__self__") and hasattr(fn, "__func__"):
        try:
            ref = weakref.WeakMethod(fn)  # type: ignore[arg-type]
        except TypeError:
            ref = None
    if ref is None:
        def strong() -> Sink | None:
            return fn
        ref = strong
    with _sinks_lock:
        while True:  # a finalizer may add or remove on this thread while the tuple is built: retry, never overwrite
            snapshot = _sinks
            new = (*snapshot, ref)
            if _sinks is snapshot:
                _sinks = new
                return


def remove_sink(fn: Sink) -> None:
    global _sinks
    with _sinks_lock:  # bound methods compare equal when they wrap the same function and object
        while True:  # compare and retry, as in add_sink
            snapshot = _sinks
            new = tuple(ref for ref in snapshot if (live := ref()) is not None and live != fn)
            if _sinks is snapshot:
                _sinks = new
                return


def _live_sinks() -> list[Sink]:
    global _sinks
    snapshot = _sinks  # one atomic read, no lock
    live = [(ref, ref()) for ref in snapshot]
    if any(fn is None for _, fn in live) and _sinks_lock.acquire(blocking=False):
        try:  # prune dead weak refs only if nobody else holds the lock; a re-entrant add or remove is never lost
            pruned = tuple(ref for ref, fn in live if fn is not None)
            if _sinks is snapshot:
                _sinks = pruned
        finally:
            _sinks_lock.release()
    return [fn for _, fn in live if fn is not None]


def error_body(where: str, exc: BaseException | str, error_type: str = "", tb: str = "") -> ev.HarnessError:
    if isinstance(exc, BaseException):
        error_type = error_type or type(exc).__name__
        tb = tb or "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-4:])
    return ev.HarnessError(where=scrub(where, 120), error_type=scrub(error_type, 80), message=scrub(str(exc), 300),
                           traceback=scrub(tb, 1200))


def report(where: str, exc: BaseException | str, session: str | None = None, **kw: Any) -> ev.HarnessError:
    """Record a caught exception as a ``harness.error`` event (and in the log). Safe to call from any hook.
    A ``NotConfigured`` (a setup step not taken) is logged at info only: no event, no finding and no notice."""
    body = error_body(where, exc, **kw)
    if isinstance(exc, NotConfigured):
        logger.info("%s is off until set up: %s", body.where, body.message)
        return body
    logger.warning("harness error in %s: %s: %s", body.where, body.error_type, body.message)
    for fn in _live_sinks():
        try:
            fn(body, session)
        except Exception:  # the reporter itself must never raise into the code it is watching
            logger.warning("harness.error sink failed", exc_info=True)
    return body


# ---------------------------------------------------------------- harness version

_VERSION: list[str] = []


def harness_version(refresh: bool = False) -> str:
    """The git short sha of the running source tree, else the package version. ``$EXO_HARNESS_VERSION`` wins."""
    if os.environ.get("EXO_HARNESS_VERSION"):
        return os.environ["EXO_HARNESS_VERSION"]
    if _VERSION and not refresh:
        return _VERSION[0]
    from exo import __version__

    repo = _repo()
    sha = _git(repo, "rev-parse", "--short", "HEAD")[1] if repo else ""
    _VERSION[:] = [sha.strip() or f"exo-{__version__}"]
    return _VERSION[0]


def _repo() -> Path | None:
    root = Path(__file__).resolve().parents[3]
    return root if (root / ".git").exists() else None


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=5)
        return r.returncode, r.stdout if r.returncode == 0 else ""
    except Exception:
        return 128, ""


def at_or_after(running: str, fixed_in: str | None) -> bool:
    """Is the running harness at or after the commit a finding was fixed in?

    Same sha, or ``fixed_in`` is an ancestor of the running sha (git). When that cannot be told (no git
    checkout, unknown sha, a version string) the fix is assumed to be live."""
    if not fixed_in or not running:
        return True
    if running.startswith(fixed_in) or fixed_in.startswith(running):
        return True
    repo = _repo()
    if repo is None or not re.fullmatch(r"[0-9a-f]{4,40}", fixed_in) or not re.fullmatch(r"[0-9a-f]{4,40}", running):
        return True
    return _git(repo, "merge-base", "--is-ancestor", fixed_in, running)[0] != 1  # 128 = unknown, assume live


# ---------------------------------------------------------------- thresholds


@dataclass
class Thresholds:
    """Anomaly and verification limits. Config: the ``trace`` table of config.json; a seat's ``[trace]`` table
    in seat.toml overrides it key by key (D-032).

    ``turn_wall_s`` limits harness overhead (wall minus tool, approval and model-call time); ``model_call_s`` one
    model call (``model.slow``), ``model_call_routes`` per route; ``turn_total_wall_s`` the whole turn."""

    turn_wall_s: float = 30.0
    model_call_s: float = 60.0
    model_call_routes: dict[str, float] = field(default_factory=dict)
    turn_total_wall_s: float = 1800.0
    model_wait_s: float = 30.0
    subagent_s: float = 90.0
    verify_turns: int = 20
    verify_days: float = 3.0

    @classmethod
    def of(cls, cfg: dict[str, Any] | None) -> Thresholds:
        out = cls()
        for k, v in (cfg or {}).items():
            if k == "model_call_routes" and isinstance(v, dict):
                out.model_call_routes = {str(r): float(x) for r, x in v.items()
                                         if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0}
            elif hasattr(out, k) and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                setattr(out, k, type(getattr(out, k))(v))
        return out


# ---------------------------------------------------------------- trace rows


@dataclass
class Call:
    iteration: int = 0
    role: str = ""
    requested: str = ""
    route: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    ttft_ms: int | None = None
    total_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""
    session: str = ""
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float | None = None  # from the route's catalog price (or the provider's own figure); None = unknown
    undercounted: bool = False  # a route in this call stopped on its spend limit: the figure may be short of the real one

    @property
    def spent(self) -> bool:
        """Did the call use tokens at all? A call that failed before any output costs nothing and is not 'unknown'."""
        return bool(self.tokens_in or self.tokens_out or self.cache_read or self.cache_write)


@dataclass
class ToolRow:
    name: str
    call_id: str = ""
    args_key: str = ""
    ms: int = 0
    is_error: bool = False
    error: str = ""
    session: str = ""
    masked: str = ""  # the "note: pipeline stage N exited C" line a bash result carried, if any
    epoch: int = 0  # how many file-changing calls came before this one in the turn (a repeat across a change is a retest)


@dataclass
class TaskRow:
    task_id: str
    title: str = ""
    status: str = ""
    child: str | None = None
    start_ms: float = 0.0
    end_ms: float = 0.0

    @property
    def ms(self) -> int:
        return int(max(0.0, self.end_ms - self.start_ms))


@dataclass
class Flag:
    signature: str
    severity: str
    title: str
    detail: str = ""
    session: str = ""
    turn: str = ""
    model: str = ""


@dataclass
class TurnTrace:
    session: str
    turn_id: str
    started: str = ""
    t0: float = 0.0  # epoch ms
    wall_ms: int = 0
    complete: bool = False
    stop_reason: str = ""
    shown: str = ""
    served: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    tools: list[ToolRow] = field(default_factory=list)
    tasks: list[TaskRow] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    timing: dict[str, Any] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    approval_ms: int = 0
    fallbacks: int = 0
    derived: bool = False  # no model.call events: rows come from timestamps
    children: list[TurnTrace] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)

    @property
    def harness_ms(self) -> int:
        """Harness overhead: wall minus tool, approval and model-call time (D-032, amended 2026-10-12). Old sessions
        without a timing summary use the recorded tool and call durations."""
        if "tool_ms" in self.timing:
            return int(max(0, self.wall_ms - self.timing["tool_ms"] - self.approval_ms - self.timing.get("model_ms", 0)))
        return int(max(0, self.wall_ms - sum(t.ms for t in self.tools) - sum(c.total_ms for c in self.calls)))

    def cost(self) -> tuple[float | None, int]:
        """(USD, calls with no price) over this turn and its subagent turns. USD is None when no call is priced."""
        calls = [c for x in self.everything() for c in x.calls if c.spent]
        priced = [c.cost_usd for c in calls if c.cost_usd is not None]
        return (round(sum(priced), 8) if priced else None), len(calls) - len(priced)

    def undercounted(self) -> int:
        """Calls whose figure may be short: a route in the call stopped on its spend limit after doing work this turn
        is not billed for. Counted with the unpriced calls, because the sum cannot be trusted for the same reason."""
        return sum(1 for x in self.everything() for c in x.calls if c.spent and c.undercounted)

    def everything(self) -> list[TurnTrace]:
        return [self, *[x for c in self.children for x in c.everything()]]

    def all_tools(self) -> list[ToolRow]:
        return [t for x in self.everything() for t in x.tools]

    def served_set(self) -> list[str]:
        out: list[str] = []
        for x in self.everything():
            out += [r for r in x.served if r and r not in out]
        return out

    def summary(self) -> dict[str, Any]:
        tools = self.all_tools()
        usd, unpriced = self.cost()
        under = self.undercounted()
        return {"cost_usd": usd, "cost": money(usd, unpriced + under), "unpriced_calls": unpriced,
                "undercounted_calls": under,
                "harness_ms": self.harness_ms, "session": self.session, "turn": self.turn_id, "started": self.started, "wall_ms": self.wall_ms,
                "complete": self.complete, "stop_reason": self.stop_reason, "shown": self.shown,
                "served": self.served_set(), "model_calls": sum(len(x.calls) for x in self.everything()),
                "fallbacks": sum(x.fallbacks for x in self.everything()), "tool_calls": len(tools),
                "tool_errors": sum(1 for t in tools if t.is_error), "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "subagents": len(self.tasks), "derived": self.derived,
                "timing": self.timing, "flags": [f.signature for f in self.flags]}


def money(usd: float | None, unpriced: int = 0) -> str:
    """'$0.0123'; '$0.0123+' when the figure is a floor (a call had no price, or a route hit its spend limit);
    'unknown' (never '$0') when none did."""
    if usd is None:
        return "unknown"
    return (f"${usd:.4f}" if usd < 1 else f"${usd:.2f}") + ("+" if unpriced else "")


def _ms(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return 0.0


_MASKED = re.compile(r"^note: pipeline stage (\d+) exited (\d+)", re.M)


def _masked(text: str) -> str:
    """The pipeline note the bash tool appends when an earlier stage failed but the last one succeeded."""
    m = _MASKED.search(text or "")
    return m.group(0) if m else ""


def _canon_cmd(cmd: str) -> str:
    """A shell command reduced to what it runs: no leading cd, timeout wrapper, pipes, redirects, echo tail or
    common flags, so a test suite run twice with different wrappers still counts as the same call."""
    prefix = r"^\s*(?:cd\s+\S+|ulimit(?:\s+-\S+)*(?:\s+\S+)?|(?:export\s+)?[A-Za-z_]\w*=\S*|set\s+-\S+)\s*(?:&&|;)\s*"
    while (stripped := re.sub(prefix, "", cmd, count=1)) != cmd:  # setup steps before the real command
        cmd = stripped
    cmd = re.split(r"\s*(?:\||;|&&)\s*", cmd, maxsplit=1)[0]
    cmd = re.sub(r"^timeout\s+\d+\s+", "", cmd)
    cmd = re.sub(r"\s2>&1|\s-p\s+\S+|\s-r[a-zA-Z]+|\s-q+\b", "", cmd)
    return re.sub(r"\s+", " ", cmd).strip()


_FILE_TOOLS = frozenset({"write_file", "edit_file", "patch"})
_BASH_WRITE = re.compile(
    r"\bsed\s+(?:-\w+\s+)*-i|\btee\b|\bgit\s+(?:apply|checkout|restore|stash|reset|pull|merge|rebase|cherry-pick|am)\b"
    r"|\bpatch\s|\b(?:mv|cp|rm|touch)\s|(?<![0-9&<>])>>?\s*(?!&|/dev/null)\S")


def _changes_files(name: str, args: Any, error: bool) -> bool:
    """Did this completed call (probably) change files? Then a later identical command is a retest, not a repeat."""
    if error:
        return False
    if name in _FILE_TOOLS:
        return True
    return name == "bash" and isinstance(args, dict) and bool(_BASH_WRITE.search(str(args.get("command", ""))))


def _args_key(args: Any) -> str:
    if isinstance(args, dict) and isinstance(args.get("command"), str):
        args = {"command": _canon_cmd(args["command"])}  # the timeout argument does not make it a different call
    return hashlib.sha1(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _norm(text: str) -> str:
    """A tool error reduced to its kind: lower case, paths and numbers blanked, first line, 50 chars."""
    line = (text.strip().splitlines() or [""])[0].lower()
    return re.sub(r"\d+", "#", re.sub(r"(~|\.{0,2})/\S+", "<path>", line))[:50].strip()


class _Parse:
    """One session's events to TurnTrace rows."""

    def __init__(self, session: str) -> None:
        self.session = session
        self.turns: list[TurnTrace] = []
        self.cur: TurnTrace | None = None
        self.recorded: list[Call] = []
        self.guess: list[Call] = []
        self.starts: dict[str, tuple[float, ev.ToolStarted]] = {}
        self.asks: dict[str, float] = {}
        self.fell = 0
        self.pending: list[dict[str, Any]] = []  # fallback notices waiting for the usage event of their call
        self.epoch = 0  # file-changing calls so far this turn
        self.mark = 0.0  # when the model started waiting: turn start or the last tool/usage boundary
        self.first = 0.0  # first streamed text or reasoning since mark

    def feed(self, e: ev.Event) -> None:
        b, ts = e.body, _ms(e.ts)
        if isinstance(b, ev.TurnStarted):
            self.cur = TurnTrace(self.session, b.turn_id, e.ts, ts, shown=b.model)
            self.turns.append(self.cur)
            self.recorded, self.guess, self.fell, self.mark, self.first, self.pending = [], [], 0, ts, 0.0, []
            self.epoch = 0
            return
        t = self.cur
        if t is None:
            return
        t.wall_ms = int(max(t.wall_ms, ts - t.t0))  # a turn that never completed ends at its last event
        if isinstance(b, (ev.MessageDelta, ev.ReasoningDelta)):
            self.first = self.first or ts
        elif isinstance(b, ev.ModelCall):
            u = b.usage
            self.recorded.append(Call(b.iteration, b.role, b.requested, b.route, list(b.attempts), b.ttft_ms,
                                      b.total_ms, u.input_tokens if u else 0, u.output_tokens if u else 0,
                                      b.error, self.session, u.cache_read_tokens if u else 0,
                                      u.cache_write_tokens if u else 0, u.cost_usd if u else None,
                                      any(a.get("outcome") == "spend_limit" for a in b.attempts)))
        elif isinstance(b, ev.UsageUpdated):
            u = b.usage
            t.tokens_in += u.input_tokens
            t.tokens_out += u.output_tokens
            tries = self.pending + ([{"route": b.model, "outcome": "ok", "error": "", "ms": 0}] if self.pending else [])
            self.guess.append(Call(len(self.guess) + 1, "", t.shown, b.model, tries, int(self.first - self.mark)
                                   if self.first else None, int(max(0.0, ts - self.mark)), u.input_tokens,
                                   u.output_tokens, "", self.session, u.cache_read_tokens, u.cache_write_tokens,
                                   u.cost_usd))
            self.pending = []
            if b.model and b.model not in t.served:
                t.served.append(b.model)
            self.mark, self.first = ts, 0.0
        elif isinstance(b, ev.Notice) and "; trying " in b.text and ("did not answer" in b.text or "failed" in b.text):
            self.fell += 1  # the router's fallback notice (the only trace of a fallback before D-025)
            self.pending.append({"route": b.text.split(" ")[0], "ms": 0, "error": scrub(b.text, 160),
                                 "outcome": "timeout" if "did not answer" in b.text else "fallback"})
        elif isinstance(b, ev.ToolStarted):
            self.starts[b.call.id] = (ts, b)
        elif isinstance(b, ev.ToolCompleted):
            began, st = self.starts.pop(b.result.call_id, (ts, None))
            err = b.result.is_error or b.is_error
            call = st.call if st else None
            t.tools.append(ToolRow(call.name if call else "?", b.result.call_id,
                                   _args_key(call.arguments) if call else "", b.duration_ms or int(ts - began), err,
                                   scrub(b.result.text(), 300) if err else "", self.session,
                                   _masked(b.result.text()) if call and call.name == "bash" else "", self.epoch))
            if call and _changes_files(call.name, call.arguments, bool(err)):
                self.epoch += 1
            self.mark, self.first = ts, 0.0
        elif isinstance(b, ev.ApprovalRequested):
            self.asks[b.request_id] = ts
        elif isinstance(b, ev.ApprovalResolved):
            t.approval_ms += int(ts - self.asks.pop(b.request_id, ts))
        elif isinstance(b, ev.TaskUpdated):
            row = next((r for r in t.tasks if r.task_id == b.task_id), None)
            if row is None:
                row = TaskRow(b.task_id, b.title, b.status, b.child_session, ts, ts)
                t.tasks.append(row)
            row.status, row.end_ms = b.status, ts
            row.child = b.child_session or row.child
        elif isinstance(b, ev.HarnessError):
            t.errors.append({"where": b.where, "type": b.error_type, "message": b.message})
        elif isinstance(b, ev.TurnCompleted):
            self._flush()
            t.complete, t.stop_reason, t.timing = True, b.stop_reason, dict(b.timing or {})
            t.wall_ms = int(ts - t.t0)
            self._settle(t)
            if "wall_ms" in t.timing:
                t.wall_ms = int(t.timing["wall_ms"])
            if "approval_ms" in t.timing:
                t.approval_ms = int(t.timing["approval_ms"])
            self.cur = None

    def _flush(self) -> None:
        if self.pending and self.cur is not None:  # a fallback with no usage after it: the call never finished
            self.guess.append(Call(len(self.guess) + 1, "", self.cur.shown, "", self.pending, None, 0, 0, 0, "",
                                   self.session))
            self.pending = []

    def _settle(self, t: TurnTrace) -> None:
        """Calls, served routes and fallbacks from the recorded model calls, else from the derived guesses."""
        t.calls, t.derived = (self.recorded or self.guess), not self.recorded
        if self.recorded:
            t.served = list(dict.fromkeys(c.route for c in self.recorded if c.route))
            t.fallbacks = sum(1 for c in t.calls for a in c.attempts if a.get("outcome") not in ("ok", None))
        else:
            t.fallbacks = self.fell

    def done(self) -> list[TurnTrace]:
        t = self.cur
        self._flush()
        if t is not None and not t.complete:  # still running, or the process died mid turn
            self._settle(t)
        return self.turns


def parse(events: list[ev.Event], session: str) -> list[TurnTrace]:
    p = _Parse(session)
    for e in events:
        p.feed(e)
    return p.done()


def _events(store: Any, session: str, since: int = 0, deltas: bool = True) -> list[ev.Event]:
    """Deltas are kept for ``build`` (old sessions derive time to first token from them) and skipped at turn end."""
    return store.events_since(since, session, limit=1 << 30, skip=SKIP_TYPES if not deltas else SKIP_TYPES[2:])


def build(store: Any, session: str, last: int | None = None, th: Thresholds | None = None,
          depth: int = 2) -> list[TurnTrace]:
    """Rows for every turn of ``session`` (or the last ``last``), with subagent sessions rolled in and flags set."""
    turns = parse(_events(store, session), session)
    if last:
        turns = turns[-last:]
    for t in turns:
        _children(store, t, depth)
        t.flags = detect(t, th or Thresholds())
    return turns


def _children(store: Any, t: TurnTrace, depth: int, deltas: bool = True) -> None:
    if depth <= 0:
        return
    for task in t.tasks:
        if task.child:
            kids = parse(_events(store, task.child, deltas=deltas), task.child)
            for k in kids:
                _children(store, k, depth - 1, deltas)
            t.children += kids


def last_turn(store: Any, session: str) -> TurnTrace | None:
    """The latest turn of ``session`` with its subagent turns; reads only that turn's events (cheap at every turn end)."""
    rows = store._q("SELECT seq FROM events WHERE session=? AND type='turn.started' ORDER BY seq DESC LIMIT 1",
                    (session,))
    turns = parse(_events(store, session, rows[0]["seq"] - 1, deltas=False), session) if rows else []
    if turns:
        _children(store, turns[-1], 2, deltas=False)
    return turns[-1] if turns else None


def eligible(signature: str, t: TurnTrace) -> bool | None:
    """Could turn ``t`` (subagent turns included) have tripped ``signature``? Only a turn that could counts toward
    verifying a fix (D-025): a ``model.*:<route>`` rule needs a call that tried that route, ``model.mismatch`` a served
    call, a ``tool.*:<tool>`` rule a call of that tool, ``shell.masked_exit`` a bash call, ``subagent.slow`` a task,
    ``turn.slow`` and ``turn.wall`` a finished turn. None: no rule can tell (``harness.error``, an agent's or the
    principal's report), so the turn counts only as a day of activity."""
    kind, _, key = signature.partition(":")
    xs, tools = t.everything(), {r.name for r in t.all_tools()}
    rule = {"turn.slow": t.complete, "turn.wall": t.complete, "subagent.slow": any(x.tasks for x in xs),
            "model.mismatch": any(x.shown and x.served for x in xs), "shell.masked_exit": "bash" in tools,
            "tool.error": key.split(":")[0] in tools, "tool.repeat": key in tools}
    if kind in ("model.wait", "model.slow", "model.fallback", "model.timeout"):
        return any(key in (c.route or x.shown, *(a.get("route") for a in c.attempts)) for x in xs for c in x.calls)
    return rule.get(kind)


# ---------------------------------------------------------------- anomaly rules


def _fmt_ms(ms: float) -> str:
    s = ms / 1000
    return f"{s:.1f} s" if s < 60 else f"{int(s // 60)} min {int(s % 60)} s"


def detect(t: TurnTrace, th: Thresholds, child: bool = False) -> list[Flag]:
    """Apply the anomaly rules to one turn (and its subagent turns). Signatures are stable across sessions."""
    out: list[Flag] = []
    model = (t.served[0] if t.served else t.shown) or ""

    def add(sig: str, sev: str, title: str, detail: str = "") -> None:
        if not any(f.signature == sig for f in out):
            out.append(Flag(sig, sev, title, detail, t.session, t.turn_id, model))

    if not child and t.harness_ms > th.turn_wall_s * 1000:  # the harness's share: work, approvals, model time do not count
        slow = t.harness_ms > th.turn_wall_s * 5000
        add("turn.slow", "bug" if slow else "papercut", f"Harness overhead was {_fmt_ms(t.harness_ms)} in one turn",
            f"{_fmt_ms(t.harness_ms)} of {_fmt_ms(t.wall_ms)} wall was neither tools, approvals nor model calls; "
            f"limit {th.turn_wall_s:g} s. model {_fmt_ms(t.timing.get('model_ms', 0))}, "
            f"tools {_fmt_ms(t.timing.get('tool_ms', 0))}, approvals {_fmt_ms(t.approval_ms)}."
            if t.timing else f"{_fmt_ms(t.harness_ms)} of {_fmt_ms(t.wall_ms)} wall was outside tools and model calls; "
            f"limit {th.turn_wall_s:g} s (old session: no timing summary).")
    if not child and t.wall_ms > th.turn_total_wall_s * 1000:
        add("turn.wall", "papercut", f"Turn ran {_fmt_ms(t.wall_ms)} in total",
            f"{_fmt_ms(t.wall_ms)} wall, work included; cap {th.turn_total_wall_s:g} s.")
    for c in t.calls:
        wait = c.ttft_ms if c.ttft_ms is not None else (0 if t.derived else c.total_ms)
        if wait > th.model_wait_s * 1000:
            add(f"model.wait:{c.route or t.shown}", "bug" if wait > th.model_wait_s * 5000 else "papercut",
                f"Model waited {_fmt_ms(wait)} for the first token",
                f"{c.route or t.shown}: {_fmt_ms(wait)} before output; limit {th.model_wait_s:g} s.")
        timed_out = any(a.get("outcome") == "timeout" for a in c.attempts)  # model.timeout names that route
        for r, ms in ([(a["route"], a.get("ms", 0)) for a in c.attempts if a.get("outcome") != "timeout" and a.get("route")]
                      or ([] if timed_out else [(c.route or t.shown, c.total_ms)])):  # a slow route, whichever try it was
            limit = th.model_call_routes.get(r, th.model_call_s)
            if ms > limit * 1000:
                add(f"model.slow:{r}", "bug" if ms > limit * 5000 else "papercut", f"{r} took {_fmt_ms(ms)} for one call",
                    f"{r}: {_fmt_ms(ms)} in one model call; limit {limit:g} s. The route is slow, not the harness.")
        for a in c.attempts:
            if a.get("outcome") == "timeout":
                add(f"model.timeout:{a['route']}", "bug", f"{a['route']} timed out",
                    f"{a.get('error', '')} (after {_fmt_ms(a.get('ms', 0))})".strip())
            elif a.get("outcome") in ("fallback", "error", "spend_limit"):
                cool = a.get("cooldown_s")  # the route's circuit breaker opened on this failure
                # A spend limit keeps the `model.fallback` signature on purpose: the route that failed is still the
                # subject, so a finding filed before this outcome existed reopens instead of reading as fixed.
                said = (f"{a['route']} is over its provider spend limit; the call fell back"
                        if a.get("outcome") == "spend_limit" else f"{a['route']} failed; the call fell back")
                add(f"model.fallback:{a['route']}", "bug", said,
                    str(a.get("error", "")) + (f" (route skipped for {_fmt_ms(cool * 1000)})" if cool else ""))
    for tr in t.tools:
        if tr.is_error:
            add(f"tool.error:{tr.name}:{_norm(tr.error)}", "papercut", f"Tool {tr.name} failed", tr.error)
    for tr in t.tools:
        if tr.masked:
            add("shell.masked_exit", "papercut", "A pipeline failure was hidden by its last stage", tr.masked)
    seen: dict[tuple[str, str], int] = {}
    for tr in t.tools:
        k = (tr.name, tr.args_key, tr.epoch)  # the same call after an edit is a retest: not a repeat
        seen[k] = seen.get(k, 0) + 1
    for (name, _, _), n in seen.items():
        if n > 1:
            slowest = max((x.ms for x in t.tools if x.name == name), default=0)
            add(f"tool.repeat:{name}", "papercut", f"Tool {name} ran the same call {n} times in one turn",
                f"{n} same or near-same {name} calls (slowest {_fmt_ms(slowest)}).")
    for e in t.errors:
        add(f"harness.error:{e['where']}:{e['type']}", "bug", f"Harness error in {e['where']}",
            f"{e['type']}: {e['message']}")
    for task in t.tasks:
        if task.ms > th.subagent_s * 1000:
            add("subagent.slow", "papercut", f"Subagent task took {_fmt_ms(task.ms)}",
                f"{task.title[:80]}: {_fmt_ms(task.ms)}; limit {th.subagent_s:g} s.")
    if t.shown and t.served and not t.fallbacks and any(r != t.shown for r in t.served):
        add("model.mismatch", "bug", "Shown model differs from the model that served",
            f"shown {t.shown}; served {', '.join(t.served)}.")
    if not child:
        for k in t.children:
            for f in detect(k, th, child=True):
                if not any(x.signature == f.signature for x in out):
                    out.append(f)
    return out


# ---------------------------------------------------------------- text

_SEV = {"blocker": 0, "bug": 1, "papercut": 2, "idea": 3}


def _short(route: str) -> str:
    return route.split(":", 1)[-1] if route else "-"


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _call_lines(t: TurnTrace, n: int) -> list[str]:
    lines = [f"calls in turn {n}"]
    for x in t.everything():
        for c in x.calls:
            cost = money(c.cost_usd, 1 if c.undercounted else 0) if c.spent else "-"
            lines.append(f"  {c.iteration:>2}  {_short(c.route or c.requested):<28} {_fmt_ms(c.total_ms):>9}  "
                         f"in {_k(c.tokens_in)} out {_k(c.tokens_out)} cache r/w {_k(c.cache_read)}/{_k(c.cache_write)}"
                         f"  {cost}" + (f"  [{c.error[:60]}]" if c.error else ""))
    return lines


def table(turns: list[TurnTrace], title: str = "", calls: bool = False) -> str:
    """The `exo trace` view: one row per turn, the calls of the last turn (all turns with ``calls``), then the flags.
    Cost is an estimate from catalog prices; 'unknown' means no price, never zero."""
    head = ("  #  when        wall  model (served)                 calls  fb  tools  err  tokens in/out      cost  flags")
    lines = [title, head] if title else [head]
    flags: list[Flag] = []
    for i, t in enumerate(turns, 1):
        s = t.summary()
        when = datetime.fromtimestamp(t.t0 / 1000).strftime("%H:%M:%S") if t.t0 else "-"
        served = ",".join(_short(r) for r in s["served"]) or f"{_short(t.shown)}?"
        mark = "~" if t.derived else " "
        end = "" if t.complete else "  (not finished)"
        lines.append(f"{i:3d}  {when}  {_fmt_ms(t.wall_ms):>10}  {served[:30]:<30} {s['model_calls']:>4}{mark} "
                     f"{s['fallbacks']:>3}  {s['tool_calls']:>5}  {s['tool_errors']:>3}  "
                     f"{_k(t.tokens_in):>6}/{_k(t.tokens_out):<6} {s['cost']:>9}  {len(t.flags) or ''}{end}")
        flags += t.flags
    lines.append("~ = derived from timestamps (session recorded before model.call events); "
                 "cost = estimate from catalog prices, + = some calls unpriced or short (a route hit its spend "
                 "limit), unknown = no price")
    for i, t in enumerate(turns, 1):
        if t.calls and (calls or t is turns[-1]):
            lines += ["", *_call_lines(t, i)]
    if flags:
        lines += ["", "flags"]
        for f in sorted(flags, key=lambda f: _SEV.get(f.severity, 9)):
            lines.append(f"  [{f.severity}] {f.signature}: {f.title}" + (f" ({f.detail})" if f.detail else ""))
    return "\n".join(lines)


def peer_text(store: Any, session: str, last: int = 20) -> str:
    """Peer traffic of a session (D-026): who sent what to whom, and where each message stands. '' when none."""
    rows = [e for e in _events(store, session, deltas=False) if e.type in ("peer.message", "peer.delivery")][-last:]
    if not rows:
        return ""
    lines = ["peers"]
    for e in rows:
        b = e.body
        when = (e.ts or "")[11:19]
        if e.type == "peer.message":
            lines.append(f"  {when}  from {b.sender} ({b.trust}) {b.mode or 'delivered'}: {scrub(b.text, 60)}")
        else:
            lines.append(f"  {when}  {'to' if b.direction == 'out' else 'from'} {b.peer}: {b.state}  {b.msg_id}"
                         + (f"  {scrub(b.detail, 80)}" if b.detail else ""))
    return "\n".join(lines)


def as_dict(session: str, turns: list[TurnTrace]) -> dict[str, Any]:
    return {"session": session, "turns": [t.summary() | {
        "flag_list": [vars(f) for f in t.flags],
        "call_list": [{"iteration": c.iteration, "route": c.route, "ms": c.total_ms, "tokens_in": c.tokens_in,
                       "tokens_out": c.tokens_out, "cache_read": c.cache_read, "cache_write": c.cache_write,
                       "cost_usd": c.cost_usd, "cost": money(c.cost_usd, 1 if c.undercounted else 0) if c.spent else "-",
                       "undercounted": c.undercounted, "error": c.error}
                      for x in t.everything() for c in x.calls]} for t in turns]}
