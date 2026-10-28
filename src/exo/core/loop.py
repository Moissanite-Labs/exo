"""The async turn loop.

One turn: store the user message, build a fresh model input (context engine),
stream the reply, run any tool calls, and loop until the model stops, the
iteration cap is hit, or the turn is interrupted. Every step is an event,
written to sessions.sqlite before it is published (``EventBus.emit``).

Injected, never imported: the provider (any ``exo.core.types.Provider``), the
tool specs and tool runner, the memory provider and the context engine. That
keeps the loop independent of the registry, router and slot modules.

Memory call sites (all optional, sync or async): ``system_prompt_block()``,
``prefetch(query, session_id=)``, ``on_turn_start(turn_number, message, session_id=, author=)``,
``sync_turn(user_content, assistant_content, session_id=, turn_author={"author": ...})``,
``checkpoint(session_id=, messages=, reason=)``; the last three never in or under a private thread (D-028).
``author`` is the turn message's author (exo.provenance, D-040); each message dict carries ``author`` and,
for user messages, ``principal``. Keywords a provider does not declare are dropped.
Context call sites: ``note(session_id)``, ``await build(session_id, system)``,
optional ``on_turn_complete(session_id, usage)`` with the usage of the turn's last model call (its prompt size, not
the turn's sum) and an ``on_event`` attribute.
"""

from __future__ import annotations

import asyncio
from itertools import count
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from exo import provenance
from exo.core import trace
from exo.core.context import TreeContext
from exo.core.hooks import accepted, maybe_await
from exo.core.sessions import EventBus, SessionStore, new_id
from exo.core.types import ContentPart, Message, Provider, ToolCall, ToolResult, ToolSpec, Usage
from exo.protocol import schema as ev

ToolRunner = Callable[[ToolCall, dict[str, Any]], "ToolResult | Awaitable[ToolResult]"]
# The session's real cwd, as the authoritative value: a CLI route's own environment note names its private folder.
WORKDIR_LINE = ("Your working directory is {cwd}. Tools run there and relative paths resolve against it; "
                "this overrides any other working directory an environment note names.")


@dataclass
class TurnResult:
    turn_id: str
    stop_reason: str
    message: Message | None = None
    usage: Usage = field(default_factory=Usage)
    error: str | None = None
    last_usage: Usage = field(default_factory=Usage)  # the turn's last model call that reported usage: its prompt


class _Interrupted(Exception):
    pass


@dataclass
class _Stream:
    text: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    route: str = ""  # provider:model that served the call (set by the router; empty for a bare provider)
    done: Any = None
    error: Any = None
    attempts: list[dict[str, Any]] = field(default_factory=list)  # the router's tries (D-025)
    t0: float = 0.0
    first_ms: int | None = None  # time to the first streamed text, reasoning or tool call
    total_ms: int = 0
    retries: int = 0


WRAP_UP_PROMPT = ("[harness] You have used all {n} tool rounds for this turn; tools are now off. "
                  "In a short reply: what you finished, what is still open, and the next step to continue "
                  "(the user can say 'continue' to start a fresh set of rounds).")


class _SessionState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.interrupt = asyncio.Event()
        self.steer: list[Message] = []
        self.running = self.accepting_steers = False
        self.unlimited = False
        self.total = Usage()
        self.reasoning_opts: dict[str, Any] = {}
        self.stats: dict[str, int] = {}  # this turn's timing summary, filled as it runs (D-025)


class Loop:
    def __init__(self, store: SessionStore, provider: Provider, *, bus: EventBus | None = None,
                 context: Any = None, memory: Any = None,
                 tool_specs: Iterable[ToolSpec] | Callable[[], Iterable[ToolSpec]] = (),
                 run_tool: ToolRunner | None = None, system_prompt: str = "",
                 model: str | None = None, max_iterations: int = 24, max_retries: int = 3,
                 retry_delay: float = 0.5, provider_opts: dict[str, Any] | None = None,
                 wrap_up: bool = True):
        self.store = store
        self.provider = provider
        self.bus = bus or EventBus(store)
        self.context = context if context is not None else TreeContext(store, None)
        if hasattr(self.context, "on_event"):
            self.context.on_event = self.bus.emit
        self.memory = memory
        self.tool_specs = tool_specs
        self.run_tool = run_tool
        self.system_prompt = system_prompt
        self.model = model
        self.max_iterations = max_iterations
        self.wrap_up = wrap_up  # at the round cap, one tool-less call to report progress and what remains
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.provider_opts = dict(provider_opts or {})
        self.reasoning_settings = None  # only the main engine loop opts in
        self.approval_ms: dict[str, int] = {}  # session -> time spent waiting on approvals; the engine adds to it
        self.on_turn_end: Callable[[str, str], Awaitable[None]] | None = None  # (session, turn_id), after the turn
        self.before_turn: Callable[[], str] | None = None  # runs at turn start; returns the soul tag for turn.started
        self.system_extra: Callable[[], str] | None = None  # text appended to the stable system part (D-030)
        self._state: dict[str, _SessionState] = {}

    # ---------------------------------------------------------------- public

    def state(self, session: str) -> _SessionState:
        return self._state.setdefault(session, _SessionState())

    async def create_session(self, **kw: Any) -> ev.SessionInfo:
        info = self.store.create_session(**kw)
        await self.bus.emit(ev.SessionCreated(session=info), info.id)
        return info

    async def fork(self, session: str, at_message: str | None = None, title: str = "") -> ev.SessionInfo:
        info = self.store.fork(session, at_message, title)
        await self.bus.emit(ev.SessionCreated(session=info), info.id)
        return info

    async def resume(self, session: str) -> ev.SessionInfo:
        """Reattach after a restart: repair a half-finished tool round (not a running turn's own), restart summaries."""
        info = self.store.get_session(session)
        if info is None:
            raise KeyError(session)
        if not self.state(session).running:
            await self._repair(session)
        if hasattr(self.context, "note"):
            await maybe_await(self.context.note(session))
        return self.store.get_session(session)  # type: ignore[return-value]

    def interrupt(self, session: str) -> bool:
        st = self.state(session)
        if st.running:
            st.interrupt.set()
        return st.running

    def steer(self, session: str, msg: Message) -> bool:
        """Put a message into the running turn at its next tool round. False when no round can take it (no turn runs,
        or the running one is finishing): the caller queues it. Unlike ``send(mode="steer")`` this never starts a turn."""
        st = self.state(session)
        if not st.accepting_steers:
            return False
        msg.id = msg.id or new_id("m")
        st.steer.append(msg)
        return True

    def unsteer(self, session: str, message_id: str = "", principal: bool = True) -> list[Message]:
        """Pull queued steer messages back before they are delivered (D-045): the one with ``message_id``, or
        with no id the newest. A message already added to the conversation is not here any more: nothing is
        removed and the list is empty. ``principal=False`` (a local caller) never removes the principal's words."""
        st = self.state(session)
        mine = [m for m in st.steer if principal or not provenance.is_principal_author(provenance.author_of(m))]
        gone = [m for m in mine if m.id == message_id] if message_id else mine[-1:]
        for m in gone:
            st.steer.remove(m)
        return gone

    async def send(self, session: str, text: str = "", parts: list[ContentPart] | None = None,
                   mode: str = "send", meta: dict[str, Any] | None = None,
                   max_rounds: int | None = None) -> TurnResult | None:
        """Run a turn. ``steer`` while a turn still takes steers injects the message between tool rounds and returns None
        (the newest of ``state(session).steer``); otherwise, as ``queue`` and ``send`` do, it waits for the running turn."""
        if self.store.get_session(session) is None:
            raise KeyError(session)
        self.store.assert_open(session)
        msg = Message(role="user", content=list(parts or []))
        if text:
            msg.content.insert(0, ContentPart.of_text(text))
        if meta:
            msg.meta.update(meta)
        # D-040: every message carries its author. The caller (the engine for a client turn, a peer, a job, the
        # scheduler, a delegation) names it; an untagged call keeps the old reading of its ingress tags.
        msg.meta.setdefault("author", provenance.infer("user", msg.meta))
        st = self.state(session)
        if mode == "steer" and st.accepting_steers:
            msg.id = msg.id or new_id("m")  # a client can name it to cancel it (D-045)
            st.steer.append(msg)
            return None
        async with st.lock:
            self.store.assert_open(session)  # locked while this send waited (a child that finished): no turn runs
            st.running = st.accepting_steers = True
            st.unlimited = (self.max_iterations if max_rounds is None else max_rounds) == 0
            st.interrupt.clear()
            try:
                return await self._turn(session, msg, st, self.max_iterations if max_rounds is None else max_rounds)
            finally:
                st.running = st.accepting_steers = False
                st.unlimited = False
                st.interrupt.clear()

    # ---------------------------------------------------------------- turn

    async def _emit(self, body: Any, session: str) -> None:
        await self.bus.emit(body, session)

    def _specs(self) -> list[ToolSpec]:
        specs = self.tool_specs() if callable(self.tool_specs) else self.tool_specs
        return list(specs or [])

    async def _add(self, session: str, msg: Message, turn_id: str) -> Message:
        msg.meta.setdefault("author", provenance.infer(msg.role, msg.meta))  # D-040: agent, tool, or the ingress's
        self.store.append_message(session, msg, turn_id)
        await self._emit(ev.MessageCompleted(message=msg), session)
        return msg

    async def _turn(self, session: str, user: Message, st: _SessionState, max_rounds: int) -> TurnResult:
        turn_id, t0 = new_id("t"), time.monotonic()
        st.stats = {"model_ms": 0, "tool_ms": 0, "model_calls": 0, "fallbacks": 0, "tool_errors": 0}
        st.reasoning_opts = self.reasoning_settings.snapshot(session, self.model or "") if self.reasoning_settings else {}
        waited = self.approval_ms.get(session, 0)
        await self._repair(session)
        await self._add(session, user, turn_id)
        soul = ""
        if self.before_turn is not None:
            try:
                soul = self.before_turn() or ""
            except Exception as exc:  # a failed refresh keeps the old prompt; the turn still runs
                await self._emit(trace.error_body("before_turn", exc), session)
        await self._emit(ev.TurnStarted(turn_id=turn_id, model=self.model or "", soul=soul), session)
        if warning := st.reasoning_opts.get("_exo_reasoning_warning"):
            await self._emit(ev.Notice(text=warning, level="warn"), session)
        turn_no = sum(1 for _ in self.store.messages(session) if _.role == "user" and not _.meta.get("note"))
        author = provenance.author_of(user)
        remembered = not self.store.private(session)  # D-028: no memory write sees a private thread or its child
        if remembered:
            await self._memory("on_turn_start", turn_no, user.text(), session_id=session, author=author)
        result = TurnResult(turn_id=turn_id, stop_reason="end")
        try:
            await self._rounds(session, turn_id, user, st, result, max_rounds)
            while st.steer and result.stop_reason == "end":  # arrived between the last call and the turn's end
                late = list(st.steer)
                st.steer.clear()
                for m in late:
                    await self._add(session, m, turn_id)
                await self._rounds(session, turn_id, late[-1], st, result, max_rounds)
        except _Interrupted:
            result.stop_reason = "interrupted"
        except Exception as exc:  # unexpected bug in a collaborator: end the turn cleanly
            result.stop_reason, result.error = "error", str(exc) or type(exc).__name__
            await self._emit(ev.Error(code="turn_failed", message=result.error), session)
            await self._emit(trace.error_body("turn loop", exc), session)
        st.accepting_steers = False  # no round can take a steer now: a later one waits for a new turn (#147)
        await self._drain_steer(session, turn_id, st)  # arrived after the last tool round: context for the next turn
        wall, approval = int((time.monotonic() - t0) * 1000), self.approval_ms.get(session, 0) - waited
        timing = {"wall_ms": wall, "model_ms": st.stats["model_ms"], "approval_ms": approval,
                  "tool_ms": max(0, st.stats["tool_ms"] - approval), "model_calls": st.stats["model_calls"],
                  "fallbacks": st.stats["fallbacks"], "tool_errors": st.stats["tool_errors"]}
        if st.stats.get("interrupted_tool"):
            timing["interrupted_tool"] = st.stats["interrupted_tool"]
        await self._emit(ev.TurnCompleted(turn_id=turn_id, stop_reason=result.stop_reason,
                                          usage=result.usage, timing=timing), session)
        reply = result.message.text() if result.message else ""
        if remembered:
            await self._memory("sync_turn", user.text(), reply, session_id=session, turn_author={"author": author})
        if remembered and self.memory is not None and hasattr(self.memory, "checkpoint"):
            recent = [provenance.flat(m) for m in self.store.messages(session)[-40:]]  # D-040: author per message
            await self._memory("checkpoint", session_id=session, messages=recent, reason="turn")
        if hasattr(self.context, "on_turn_complete"):
            await maybe_await(self.context.on_turn_complete(session, result.last_usage))  # the last prompt's size
        info = self.store.update_session(session, **({"model": self.model} if self.model else {}))
        await self._emit(ev.SessionUpdated(session=info), session)
        if self.on_turn_end is not None:
            try:
                await self.on_turn_end(session, turn_id)
            except Exception as exc:  # observation never breaks a turn, but it is recorded
                await self._emit(trace.error_body("turn observer", exc), session)
        return result

    async def _drain_steer(self, session: str, turn_id: str, st: _SessionState) -> str:
        """Add the queued steers as context. Returns the newest author drained ("" when none), so the rounds
        after a steer run for the steer's author (#598): bypass stops lowering once the turn takes anyone else's."""
        out = ""
        for m in st.steer:
            await self._add(session, m, turn_id)
            out = provenance.author_of(m)
        st.steer.clear()
        return out

    def _takes_recall(self) -> bool:
        """True when the context engine places per-turn recall itself (AgentContext, D-028), so the system
        message stays byte-stable for the prompt cache."""
        try:
            params = inspect.signature(self.context.build).parameters
        except (TypeError, ValueError):
            return False
        return "recall" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    async def base_system(self, session: str | None = None) -> str:
        """The stable part of the system text: system prompt, memory block (no recall) and the session's cwd line."""
        extra = self.system_extra() if self.system_extra else ""  # last: never moves what comes before it (D-030)
        info = self.store.get_session(session) if session else None
        where = WORKDIR_LINE.format(cwd=info.cwd) if info and info.cwd else ""
        parts = (self.system_prompt, await self._memory("system_prompt_block") or "", where, extra)
        return "\n\n".join(p for p in parts if p)

    async def _system(self, session: str, query: str) -> tuple[str, str]:
        """(system text, recall). Recall is folded into the system text unless the context engine takes it."""
        base = await self.base_system(session)
        parts = [base]
        # recalled memory is data (D-040): it may quote a peer, so its pinned-block tags are escaped (#279)
        recall = provenance.defuse_pins(await self._memory("prefetch", query, session_id=session) or "")
        if recall and not self._takes_recall():
            parts.append("# Recalled memory\n" + recall)
            recall = ""
        return "\n\n".join(p for p in parts if p), recall

    async def _rounds(self, session: str, turn_id: str, user: Message, st: _SessionState,
                      result: TurnResult, max_rounds: int) -> None:
        system, recall = await self._system(session, user.text())
        extra = {"recall": recall, "turn_id": turn_id} if self._takes_recall() else {}
        author = provenance.author_of(user)  # who the turn is for; a steer takes over (#598)
        for _ in (count() if max_rounds == 0 else range(max_rounds)):
            if hasattr(self.context, "note"):
                await maybe_await(self.context.note(session))
            messages = await self.context.build(session, system, **extra)
            msg_id = new_id("m")
            stream = await self._call(session, messages, msg_id, st, result, _)
            if await self._settle(session, turn_id, stream, msg_id, _, st, result):
                result.stop_reason = "error"
                return
            reply = self._assemble(stream, msg_id)
            result.message = await self._add(session, reply, turn_id)
            if not reply.tool_calls:
                result.stop_reason = stream.done.stop_reason or "end"
                if result.stop_reason == "tool_use":
                    result.stop_reason = "end"
                if st.steer and result.stop_reason == "end":  # a message came in during this call: answer it now
                    author = await self._drain_steer(session, turn_id, st) or author
                    continue
                return
            spend = getattr(getattr(self.provider, "router", None), "spend", None)
            if spend is not None:
                try:
                    spend.check()
                except Exception:
                    result.stop_reason, result.error = "error", "Model spend stopped; ask the principal to review the journal"
                    await self._emit(ev.Error(code="model_spend_stopped", message=result.error, retryable=False), session)
                    return
            await self._tools(session, turn_id, reply.tool_calls, st, author)
            author = await self._drain_steer(session, turn_id, st) or author
        result.stop_reason = "max_iterations"
        await self._emit(ev.Notice(text=f"stopped after {max_rounds} tool rounds",
                                   level="warn"), session)
        if self.wrap_up:
            await self._wrap_up(session, turn_id, system, extra, st, result, max_rounds)

    async def _wrap_up(self, session: str, turn_id: str, system: str, extra: dict[str, Any],
                       st: _SessionState, result: TurnResult, max_rounds: int) -> None:
        """One last call with tool use off, so a capped turn ends with a report, not a dangling tool round.

        The same tools stay declared and ``tool_choice="none"`` turns them off: removing them is an edit to the
        prompt a provider may reject (Anthropic's thinking check) and costs its prompt cache. The prompt is
        transient (never stored). stop_reason stays "max_iterations", also when the call fails (result.error
        and the Error event say why); an interrupt still ends the turn "interrupted", like any other call."""
        try:
            messages = await self.context.build(session, system, **extra)
            messages.append(Message.user(WRAP_UP_PROMPT.format(n=max_rounds)))
            msg_id = new_id("m")
            stream = await self._call(session, messages, msg_id, st, result, max_rounds, tool_choice="none")
            settled = await self._settle(session, turn_id, stream, msg_id, max_rounds, st, result)
            if settled or not (stream.text or stream.reasoning):
                return
            # Stray tool calls are dropped: the cap is spent, nothing runs. So is the signed thinking: it was
            # minted against a prompt that is never stored, so replaying it would fail the provider's check.
            reply = self._assemble(_Stream(text=stream.text, reasoning=stream.reasoning), msg_id)
            reply.meta.update(stream.done.message.meta if stream.done.message is not None else {}, wrap_up=True)
            result.message = await self._add(session, reply, turn_id)
        except _Interrupted:
            raise
        except Exception as exc:  # a failed report never turns a capped turn into a failed one
            await self._emit(trace.error_body("wrap-up", exc), session)

    async def _settle(self, session: str, turn_id: str, stream: _Stream, msg_id: str, iteration: int,
                      st: _SessionState, result: TurnResult) -> bool:
        """Record the call and its usage. On a provider error keep the text the user saw, emit the Error coded
        by attempt outcome and set result.error; True then (the caller sets stop_reason)."""
        await self._record_call(session, stream, iteration, st, call_id=msg_id)
        if stream.usage is not None:
            result.usage, result.last_usage = result.usage + stream.usage, stream.usage
            st.total = st.total + stream.usage
            await self._emit(ev.UsageUpdated(usage=stream.usage, session_total=st.total, model=stream.route or self.model or ""), session)
        if stream.error is None:
            return False
        result.error = stream.error.error or "provider error"
        if stream.text or stream.reasoning:  # the user saw this text: keep it, marked, like an interrupt
            partial = self._assemble(_Stream(text=stream.text, reasoning=stream.reasoning), msg_id)
            partial.meta["interrupted"] = True
            partial.meta["error"] = trace.scrub(result.error, 300)
            result.message = await self._add(session, partial, turn_id)
        outcomes = {a.get("outcome") for a in stream.error.attempts}
        code = ("model_spend_stopped" if "spend_stopped" in outcomes else
                "model_spend_refused" if "spend_refused" in outcomes else "provider_error")
        await self._emit(ev.Error(code=code, message=result.error, retryable=stream.error.retryable), session)
        return True

    async def _record_call(self, session: str, s: _Stream, iteration: int, st: _SessionState, stop: str = "", call_id: str = "") -> None:
        """The model.call event (what was asked, who served, every attempt) and the turn's running totals."""
        role = self.provider.name.split(":", 1)[1] if self.provider.name.startswith("router:") else ""
        st.stats["model_calls"] += 1
        st.stats["model_ms"] += s.total_ms
        served = ""
        if s.error is None:  # the route the router stamped, else the attempt that succeeded (D-025)
            served = s.route or next((a.get("route", "") for a in reversed(s.attempts) if a.get("outcome") == "ok"), "")
        st.stats["fallbacks"] += trace.recovered_fallbacks(s.attempts, served)
        selection = st.reasoning_opts.get("_exo_reasoning")
        reasoning = next((a["reasoning"] for a in reversed(s.attempts) if "reasoning" in a),
                         {"requested": selection["requested"], "sent": None, "requested_route": selection["route"],
                          "route": served, "status": "not sent"} if selection else {})
        if st.reasoning_opts.get("_exo_reasoning_warning"):
            reasoning = {"requested": "preference unavailable", "sent": None, "route": served,
                         "status": "inherited existing settings; saved preference unavailable"}
        await self._emit(ev.ModelCall(
            call_id=call_id, iteration=iteration + 1, role=role, requested=self.model or "", route=served,
            attempts=s.attempts, reasoning=reasoning, ttft_ms=s.first_ms, total_ms=s.total_ms, usage=s.usage,
            stop_reason=(s.done.stop_reason or "") if s.done is not None else stop,
            error=trace.scrub(s.error.error, 300) if s.error is not None else "", retries=s.retries), session)

    def _assemble(self, s: _Stream, msg_id: str) -> Message:
        msg = s.done.message if s.done is not None and s.done.message is not None else None
        if msg is None:
            content = []
            if s.reasoning:
                content.append(ContentPart(type="reasoning", text="".join(s.reasoning)))
            if s.text:
                content.append(ContentPart.of_text("".join(s.text)))
            msg = Message(role="assistant", content=content, tool_calls=list(s.calls))
        elif not msg.tool_calls and s.calls:
            msg.tool_calls = list(s.calls)
        msg.id = msg_id
        return msg

    # ---------------------------------------------------------------- provider call

    async def _call(self, session: str, messages: list[Message], msg_id: str, st: _SessionState,
                    result: TurnResult, iteration: int = 0, *, tool_choice: str = "") -> _Stream:
        """Stream one model call with retry. Retries only while nothing was shown."""
        if st.interrupt.is_set():  # stopped before the call began: nothing reached the provider, so no record
            raise _Interrupted()
        opts = {**self.provider_opts, **st.reasoning_opts}
        opts.pop("_exo_reasoning_warning", None)
        if self.model:
            opts["model"] = self.model
        # providers that route or cache per conversation send it as a header; all threads of one agent are one
        # conversation to them (D-028), so they share the provider's prompt cache
        key = getattr(self.context, "cache_key", None)
        opts["session_id"] = (key(session) if callable(key) else None) or session
        if tool_choice:  # "none": the tools stay declared, the model may not call them
            opts["tool_choice"] = tool_choice
        specs = self._specs()
        attempt, history, t0 = 0, [], time.monotonic()
        while True:
            s = _Stream(t0=t0)
            try:
                if attempt:  # the backoff is inside the try: a stop during it still records the failed tries
                    await self._interruptible(asyncio.sleep(self.retry_delay * 2 ** (attempt - 1)), st)
                await self._interruptible(self._consume(session, messages, specs, opts, msg_id, s), st)
            except _Interrupted:  # the wait up to the stop is model time, not harness time (like f_8e7f52cd)
                s.total_ms, s.retries, s.attempts = int((time.monotonic() - t0) * 1000), attempt, history + s.attempts
                await self._record_call(session, s, iteration, st, stop="interrupted", call_id=msg_id)
                if s.text or s.reasoning:
                    partial = self._assemble(_Stream(text=s.text, reasoning=s.reasoning), msg_id)
                    partial.meta["interrupted"] = True
                    result.message = await self._add(session, partial, result.turn_id)
                raise
            except Exception as exc:
                s.error = _err(str(exc) or type(exc).__name__, True)
            if s.done is None and s.error is None:
                s.error = _err("stream ended without done", True)
            s.total_ms, s.retries, s.attempts = int((time.monotonic() - t0) * 1000), attempt, history + s.attempts
            if s.error is None:
                return s
            shown = bool(s.text or s.reasoning or s.calls)
            if not s.error.retryable or shown or attempt >= self.max_retries:
                return s
            attempt += 1
            history = s.attempts
            await self._emit(ev.Notice(text=f"retrying model call ({attempt}/{self.max_retries}):"
                                       f" {s.error.error}", level="warn", source="model_route", call_id=msg_id), session)

    async def _consume(self, session: str, messages: list[Message], specs: list[ToolSpec],
                       opts: dict[str, Any], msg_id: str, s: _Stream) -> None:
        agen = self.provider.stream(messages, specs, **opts)
        try:
            async for pe in agen:
                if pe.kind in ("text", "reasoning", "tool_call") and s.first_ms is None and (pe.text or pe.tool_call):
                    s.first_ms = int((time.monotonic() - s.t0) * 1000)
                if pe.kind == "text" and pe.text:
                    s.text.append(pe.text)
                    await self._emit(ev.MessageDelta(message_id=msg_id, text=pe.text), session)
                elif pe.kind == "reasoning" and pe.text:
                    s.reasoning.append(pe.text)
                    await self._emit(ev.ReasoningDelta(message_id=msg_id, text=pe.text), session)
                elif pe.kind == "notice":  # a fallback notice carries the router's tries: a stop on the next route keeps them
                    s.attempts = pe.attempts or s.attempts
                    await self._emit(ev.Notice(text=pe.text, level="warn", source="model_route", call_id=msg_id), session)
                elif pe.kind == "tool_call" and pe.tool_call is not None:
                    s.calls.append(pe.tool_call)
                elif pe.kind == "usage" and pe.usage is not None:
                    s.usage = pe.usage
                    s.route = getattr(pe, "route", "") or s.route
                elif pe.kind == "done":
                    s.done = pe
                    s.route = getattr(pe, "route", "") or s.route
                    s.attempts = pe.attempts
                    if pe.usage is not None:
                        s.usage = pe.usage
                    break
                elif pe.kind == "error":
                    s.error = pe
                    s.attempts, s.route = pe.attempts, pe.route or s.route
                    break
        finally:
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass

    async def _interruptible(self, coro: Awaitable[Any], st: _SessionState) -> Any:
        if st.interrupt.is_set():  # an interrupt that arrived between steps must not lose a race with a fast step
            if asyncio.iscoroutine(coro):
                coro.close()
            raise _Interrupted()
        task = asyncio.ensure_future(coro)
        stop = asyncio.ensure_future(st.interrupt.wait())
        try:
            await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            stop.cancel()
        if task.done():
            return task.result()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        raise _Interrupted()

    # ---------------------------------------------------------------- tools

    async def _tools(self, session: str, turn_id: str, calls: list[ToolCall], st: _SessionState,
                     author: str = "") -> None:
        risks = {s.name: s.risk for s in self._specs()}
        results: list[ToolResult] = []
        try:
            for call in calls:
                await self._emit(ev.ToolStarted(call=call, risk=risks.get(call.name, "low")), session)
                t0 = time.monotonic()
                try:
                    res = await self._interruptible(self._run_one(session, turn_id, call, author), st)
                except _Interrupted:  # a tool cut short still spent the time up to the interrupt (f_8e7f52cd)
                    st.stats["tool_ms"] += int((time.monotonic() - t0) * 1000)
                    st.stats["interrupted_tool"] = call.name
                    raise
                results.append(res)
                ms = int((time.monotonic() - t0) * 1000)
                st.stats["tool_ms"] += ms
                st.stats["tool_errors"] += int(res.is_error)
                await self._emit(ev.ToolCompleted(result=res, duration_ms=ms, is_error=res.is_error), session)
        finally:  # every call gets a result, even on interrupt, so the transcript stays valid
            done = {r.call_id for r in results}
            results += [ToolResult.of_text(c.id, "interrupted before completion", True)
                        for c in calls if c.id not in done]
            await self._add(session, Message.tool(results), turn_id)

    async def _run_one(self, session: str, turn_id: str, call: ToolCall, author: str = "") -> ToolResult:
        spend = getattr(getattr(self.provider, "router", None), "spend", None)
        if spend is not None:
            try:
                spend.check()
            except Exception:
                return ToolResult.of_text(call.id, "Model spend stopped; ask the principal to review the journal", True)
        if self.run_tool is None:
            return ToolResult.of_text(call.id, f"no tool runner; cannot run {call.name}", True)

        async def output(text: str) -> None:
            await self._emit(ev.ToolOutput(call_id=call.id, text=text), session)

        # author: who started the turn (D-040); hooks read it, the gate lowers bypass only for the principal (#598)
        ctx = {"session": session, "turn_id": turn_id, "author": author, "emit_output": output, "store": self.store}
        try:
            res = await maybe_await(self.run_tool(call, ctx))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the registry turns tool failures into results, so this is a harness bug
            await self._emit(trace.error_body(f"tool runner ({call.name})", exc), session)
            return ToolResult.of_text(call.id, f"{type(exc).__name__}: {exc}", True)
        if not isinstance(res, ToolResult):
            res = ToolResult.of_text(call.id, str(res))
        res.call_id = call.id
        return res

    async def _repair(self, session: str) -> None:
        """After a crash mid tool round, close dangling tool calls with error results."""
        msgs = self.store.messages(session, since=max(0, self.store.last_seq(session) - 1))
        if msgs and msgs[-1].role == "assistant" and msgs[-1].tool_calls:
            results = [ToolResult.of_text(c.id, "not run: session was interrupted", True)
                       for c in msgs[-1].tool_calls]
            await self._add(session, Message.tool(results), msgs[-1].meta.get("turn_id") or "")

    async def _memory(self, method: str, *args: Any, **kw: Any) -> Any:
        fn = getattr(self.memory, method, None) if self.memory is not None else None
        if fn is None:
            return None
        kw = accepted(fn, kw)  # an older provider: drop what it does not take
        try:
            return await maybe_await(fn(*args, **kw))
        except Exception as exc:  # memory is best effort; a failing provider never breaks a turn, but it is recorded
            await self._emit(trace.error_body(f"memory.{method}", exc), kw.get("session_id"))
            return None


def _err(text: str, retryable: bool) -> Any:
    from exo.core.types import ProviderEvent

    return ProviderEvent(kind="error", error=text, retryable=retryable)

