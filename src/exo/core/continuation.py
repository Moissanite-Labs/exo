"""Turn continuation: the agent asks the harness to wake its own session after the turn ends.

The agent calls the ``exo_end_turn`` tool during a turn with structured flags. Nothing is parsed from reply prose.
When that turn ends cleanly (stop reason ``end``) the harness, not a fake user, starts the next turn:

* ``continue_now`` - wake again right away (one coalesced wake, never a parallel turn).
* ``wake_after`` - wake after N seconds (a timer fallback for waits on something outside the turn).
* ``followup_pending`` - recorded and reported only. It never wakes the session and never publishes anything.

``workers_pending`` is not offered: this slice does not verify the worker registry, and a toggle the harness
cannot check is not trusted (next step: count running child tasks from the Delegator before honouring it).

Safety rules:

* One pending wake per session; a new request replaces the old one (coalesced), so a double request is one wake.
* A wake never runs beside a turn: if a turn is running when the timer fires, the wake is dropped.
* An explicit stop (interrupt), an interrupted turn, an errored turn (spend stop, provider error, a failed turn)
  or a turn in which a tool was denied cancels the pending wake. Nothing restarts after those.
* A user / principal turn cancels any pending wake (the human takes over).
* Consecutive harness wakes are capped (``max_chain``) so a model that asks forever cannot loop forever.
* A turn that asks for nothing schedules nothing: no idle wake after all is clear.

Process-local MVP: pending wakes live in memory only. A restart of the core forgets them (documented, not
persisted; the session store has no safe scheduled-event mechanism to reuse without inventing one).
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from exo import provenance
from exo.core.tools.registry import Registry, ToolContext, ToolError
from exo.core.types import ToolSpec

ORIGIN = "harness_continuation"
# Task-local turn ownership survives async tools and Registry worker threads.
TURN_GENERATION: ContextVar[tuple[str, int] | None] = ContextVar("continuation_turn_generation", default=None)
MAX_WAKE_AFTER = 3600  # seconds; a longer wait belongs to the scheduler or a job
DEFAULT_MAX_CHAIN = 5

END_TURN = ToolSpec(
    name="exo_end_turn", toolset="harness", risk="low",
    description=("Tell the harness how to continue after this turn ends, then finish your reply. "
                 "continue_now: you have more work and want the harness to wake this session right after the turn "
                 "(no user message is needed). wake_after: seconds to wait before the harness wakes you (use it while "
                 "waiting on something that finishes later). followup_pending: you owe the user a follow-up; this is "
                 "only recorded, it never wakes you and never sends anything. Call it once, near the end of the turn. "
                 "A stop from the user, a denied tool or an error cancels the wake. Without this call nothing wakes you."),
    parameters={"type": "object", "properties": {
        "continue_now": {"type": "boolean", "description": "Wake this session again right after this turn."},
        "wake_after": {"type": "integer", "minimum": 1, "maximum": MAX_WAKE_AFTER,
                       "description": f"Wake after this many seconds (1-{MAX_WAKE_AFTER})."},
        "followup_pending": {"type": "boolean", "description": "Record that a follow-up is owed (no wake)."},
        "reason": {"type": "string", "description": "One line: what the next turn will do."}}})


@dataclass
class Request:
    turn_id: str
    continue_now: bool = False
    wake_after: int | None = None
    followup_pending: bool = False
    reason: str = ""
    generation: int | None = None

    @property
    def wakes(self) -> bool:
        return self.continue_now or self.wake_after is not None


@dataclass
class _Session:
    request: Request | None = None   # asked during the running turn, applied at its end
    timer: asyncio.Task | None = None
    chain: int = 0                   # consecutive harness wakes since the last non-harness turn
    denied: set[str] = field(default_factory=set)  # turn ids in which a tool was denied
    followup: str = ""               # last recorded follow-up reason (report only)
    gen: int = 0                     # fence: bumped by stop / human turn / close; a fired wake of an older gen is void


StartTurn = Callable[[str, str, dict[str, Any]], Awaitable[Any]]


class Continuations:
    """Owns the pending wakes of one engine. ``start`` runs a turn (awaitable); ``is_running(session)`` says
    whether a turn runs now; ``sleep`` is injectable for tests; ``notify(session, text)`` reports decisions."""

    def __init__(self, start: StartTurn, is_running: Callable[[str], bool], *,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 notify: Callable[[str, str], None] | None = None, max_chain: int = DEFAULT_MAX_CHAIN):
        self.start = start
        self.is_running = is_running
        self.sleep = sleep
        self.notify = notify or (lambda session, text: None)
        self.max_chain = max_chain
        self._s: dict[str, _Session] = {}
        self.closed = False

    def _st(self, session: str) -> _Session:
        return self._s.setdefault(session, _Session())

    # ------------------------------------------------------------ during a turn

    def request(self, session: str, turn_id: str, args: dict[str, Any]) -> str:
        owner = TURN_GENERATION.get()
        generation = owner[1] if owner is not None and owner[0] == session else None
        if generation is not None and generation != self._st(session).gen:
            raise ToolError("continuation cancelled: the user stopped or took over this turn")
        cont = args.get("continue_now", False)
        follow = args.get("followup_pending", False)
        wait = args.get("wake_after")
        if not isinstance(cont, bool) or not isinstance(follow, bool):
            raise ToolError("continue_now and followup_pending must be true or false")
        if wait is not None and (type(wait) is not int or not 1 <= wait <= MAX_WAKE_AFTER):
            raise ToolError(f"wake_after must be a whole number of seconds from 1 to {MAX_WAKE_AFTER}")
        if cont and wait is not None:
            raise ToolError("choose continue_now or wake_after, not both")
        reason = str(args.get("reason") or "").strip()[:300]
        req = Request(turn_id=turn_id, continue_now=cont, wake_after=wait, followup_pending=follow, reason=reason,
                      generation=generation)
        self._st(session).request = req  # a second call in the same turn replaces the first (coalesced)
        if req.wakes:
            when = "right after this turn" if cont else f"{wait}s after this turn"
            out = f"Recorded: the harness will wake this session {when} unless it is stopped. Finish your reply now."
        else:
            out = "Recorded: no wake is scheduled. Finish your reply now."
        if follow:
            out += " The follow-up is recorded only; nothing is sent."
        return out

    def note_denied(self, session: str, turn_id: str) -> None:
        """A tool was denied in this turn: its continuation request is void."""
        self._st(session).denied.add(turn_id)

    def turn_started(self, session: str, meta: dict[str, Any] | None) -> None:
        """Called before every engine turn. A turn not started by a wake (the user typed) cancels pending wakes."""
        if (meta or {}).get("origin") == ORIGIN:
            return
        st = self._st(session)
        st.gen += 1  # a wake that already fired (waiting for hooks or the session lock) is void now
        st.chain = 0
        st.request = None  # a turn queued behind the running one voids that turn's request (the human takes over)
        self._cancel_timer(st)

    # ------------------------------------------------------------ after a turn

    def turn_ended(self, session: str, turn_id: str, stop_reason: str, error: str | None = None) -> str:
        """Apply the turn's request. Returns what was decided (for the receipt and tests)."""
        st = self._st(session)
        req, st.request = st.request, None
        denied = turn_id in st.denied
        st.denied.discard(turn_id)
        if req is None or req.turn_id != turn_id:
            return "none"
        if req.generation is not None and req.generation != st.gen:
            return "cancelled:takeover"
        if req.followup_pending:
            st.followup = req.reason or "follow-up pending"
            self.notify(session, f"follow-up pending (recorded only): {st.followup}")
        if not req.wakes:
            return "followup" if req.followup_pending else "none"
        if self.closed:
            return "closed"
        if stop_reason != "end" or error:
            self.notify(session, f"continuation cancelled: the turn ended with {stop_reason}")
            return "cancelled:" + stop_reason
        if denied:
            self.notify(session, "continuation cancelled: a tool was denied in this turn")
            return "cancelled:denied"
        if st.chain >= self.max_chain:
            self.notify(session, f"continuation stopped after {self.max_chain} harness wakes in a row; "
                                 "a user message resumes the work")
            st.chain = 0
            return "capped"
        self._cancel_timer(st)  # coalesce: one pending wake per session
        delay = 0 if req.continue_now else float(req.wake_after or 0)
        st.timer = asyncio.ensure_future(self._fire(session, delay, req))
        self.notify(session, "continuation scheduled " + ("now" if delay == 0 else f"in {int(delay)}s"))
        return "scheduled"

    async def _fire(self, session: str, delay: float, req: Request) -> None:
        st = self._st(session)
        try:
            await self.sleep(delay)
        except asyncio.CancelledError:
            return
        if st.timer is asyncio.current_task():
            st.timer = None
        if self.closed:
            return
        if self.is_running(session):  # never a parallel turn; the running turn can ask again
            self.notify(session, "continuation dropped: a turn is already running")
            return
        st.chain += 1
        kind = "continue_now" if req.continue_now else f"wake_after={req.wake_after}s"
        text = (f"[harness continuation wake: {kind}; requested by you in turn {req.turn_id}"
                + (f"; your note: {req.reason}" if req.reason else "")
                + ". This is not a user message. Check real state before acting; call exo_end_turn again only if "
                  "more work remains.]")
        meta = {"author": provenance.SYSTEM, "origin": ORIGIN, "continuation_of": req.turn_id,
                "continuation_gen": st.gen}
        await self.start(session, text, meta)

    # ------------------------------------------------------------ stop

    def _cancel_timer(self, st: _Session) -> bool:
        t, st.timer = st.timer, None
        if t is not None and not t.done():
            t.cancel()
            return True
        return False

    def cancel(self, session: str) -> bool:
        """Explicit stop: drop the pending wake and the running turn's request."""
        st = self._s.get(session)
        if st is None:
            return False
        had = st.request is not None and st.request.wakes
        st.gen += 1  # also voids a wake that fired already but has not started its turn yet
        st.request = None
        st.chain = 0
        return self._cancel_timer(st) or had

    def live(self, session: str, meta: dict[str, Any] | None) -> bool:
        """May this turn run? Always True for a turn not started by a wake. A wake is live only while no stop,
        human turn or close happened since it fired (its ``continuation_gen`` still matches)."""
        meta = meta or {}
        if meta.get("origin") != ORIGIN:
            return True
        st = self._s.get(session)
        return (not self.closed and st is not None and meta.get("continuation_gen") == st.gen)

    def generation(self, session: str) -> int:
        """Capture ownership before a turn yields to hooks or the session lock."""
        return self._st(session).gen

    def pending(self, session: str) -> bool:
        st = self._s.get(session)
        return bool(st and st.timer is not None and not st.timer.done())

    def close(self) -> None:
        self.closed = True
        for st in self._s.values():
            st.gen += 1
            st.request = None
            self._cancel_timer(st)


def register(reg: Registry, cont: Continuations, allowed: Callable[[str], bool] = lambda session: True) -> None:
    """``allowed(session)`` is False for sessions the harness never wakes (subagents: their parent decides;
    scheduled runs: their own loop never applies a request)."""
    def end_turn(args: dict[str, Any], ctx: ToolContext) -> str:
        turn_id = str(ctx.meta.get("turn_id") or "")
        if not turn_id:
            raise ToolError("exo_end_turn works only inside a turn")
        if not allowed(ctx.session or ""):
            raise ToolError("exo_end_turn is for the main agent's sessions; a subagent or scheduled run just finishes its reply")
        return cont.request(ctx.session or "", turn_id, args)

    reg.register(END_TURN, end_turn, replace=True)
