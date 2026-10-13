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
``checkpoint(session_id=, messages=, reason=)``. ``author`` is the turn message's author (exo.provenance, D-040);
each message dict carries ``author`` and, for user messages, ``principal``. Keywords a provider does not
declare are dropped.
Context call sites: ``note(session_id)``, ``await build(session_id, system)``,
optional ``on_turn_complete(session_id, usage)`` and an ``on_event`` attribute.
"""

from __future__ import annotations

import asyncio
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


@dataclass
class TurnResult:
    turn_id: str
    stop_reason: str
    message: Message | None = None
    usage: Usage = field(default_factory=Usage)
    error: str | None = None


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


WRAP_UP_PROMPT = ("[harness] You have used all {n} tool rounds for this turn; tools are now off. In a short reply: what "
                  "you finished, what is still open, and the next step (the user can say 'continue' for fresh rounds).")


class _SessionState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.interrupt = asyncio.Event()
        self.steer: list[Message] = []
        self.running = False
        self.total = Usage()
        self.rounds: int | None = None  # /rounds: this session's tool-round cap, set by the principal (D-064)
        self.stats: dict[str, int] = {}  # this turn's timing summary, filled as it runs (D-025)


class Loop:
    def __init__(self, store: SessionStore, provider: Provider, *, bus: EventBus | None = None,
                 context: Any = None, memory: Any = None,
                 tool_specs: Iterable[ToolSpec] | Callable[[], Iterable[ToolSpec]] = (),
                 run_tool: ToolRunner | None = None, system_prompt: str = "",
                 model: str | None = None, max_iterations: int = 64, max_retries: int = 3,
                 retry_delay: float = 0.5, provider_opts: dict[str, Any] | None = None, wrap_up: bool = True):
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
        self.wrap_up = wrap_up  # at the round cap, one call with tool use off to report progress and what remains
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.provider_opts = dict(provider_opts or {})
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
        """Reattach after a restart: repair a half-finished tool round, restart summaries."""
        info = self.store.get_session(session)
        if info is None:
            raise KeyError(session)
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
        """Put a message into the running turn at its next tool round. False when no turn is running
        (the caller queues it instead): unlike ``send(mode="steer")`` this never starts a turn."""
        st = self.state(session)
        if not st.running:
            return False
        st.steer.append(msg)
        return True

    def unsteer(self, session: str, message_id: str = "", principal: bool = True) -> list[Message]:
        """Pull queued steer messages back before they are delivered (D-045): the one with ``message_id``, or
        with no id the newest. A message already added to the conversation is not here any more: nothing is
        removed and the list is empty. ``principal=False`` (a local caller) never removes the principal's words."""
        st = self.state(session)
        mine = [m for m in st.steer if principal or not provenance.is_principal_author(provenance.author_of(m))]
        if message_id:
            gone = [m for m in mine if m.id == message_id]
        else:
            gone = mine[-1:]
        for m in gone:
            st.steer.remove(m)
        return gone

    async def send(self, session: str, text: str = "", parts: list[ContentPart] | None = None,
                   mode: str = "send", meta: dict[str, Any] | None = None) -> TurnResult | None:
        """Run a turn. ``steer`` while a turn runs injects the message between tool
        rounds and returns None (the message is the newest of ``state(session).steer``); ``queue`` and ``send`` wait for the running turn."""
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
        if mode == "steer" and st.running:
            msg.id = msg.id or new_id("m")  # a client can name it to cancel it (D-045)
            st.steer.append(msg)
            return None
        async with st.lock:
            st.running = True
            st.interrupt.clear()
            try:
                return await self._turn(session, msg, st)
            finally:
                st.running = False
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

    async def _turn(self, session: str, user: Message, st: _SessionState) -> TurnResult:
        turn_id, t0 = new_id("t"), time.monotonic()
        st.stats = {"model_ms": 0, "tool_ms": 0, "model_calls": 0, "fallbacks": 0, "tool_errors": 0}
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
        turn_no = sum(1 for _ in self.store.messages(session) if _.role == "user" and not _.meta.get("note"))
        author = provenance.author_of(user)
        await self._memory("on_turn_start", turn_no, user.text(), session_id=session, author=author)
        result = TurnResult(turn_id=turn_id, stop_reason="end")
        try:
            await self._rounds(session, turn_id, user, st, result)
            while st.steer and result.stop_reason == "end":  # arrived between the last call and the turn's end
                late = list(st.steer)
                st.steer.clear()
                for m in late:
                    await self._add(session, m, turn_id)
                await self._rounds(session, turn_id, late[-1], st, result)
        except _Interrupted:
            result.stop_reason = "interrupted"
        except Exception as exc:  # unexpected bug in a collaborator: end the turn cleanly
            result.stop_reason, result.error = "error", str(exc) or type(exc).__name__
            await self._emit(ev.Error(code="turn_failed", message=result.error), session)
            await self._emit(trace.error_body("turn loop", exc), session)
        if st.steer:  # a message that arrived after the last tool round is context for the next turn, not lost
            for m in st.steer:
                await self._add(session, m, turn_id)
            st.steer.clear()
        wall, approval = int((time.monotonic() - t0) * 1000), self.approval_ms.get(session, 0) - waited
        timing = {"wall_ms": wall, "model_ms": st.stats["model_ms"], "approval_ms": approval,
                  "tool_ms": max(0, st.stats["tool_ms"] - approval), "model_calls": st.stats["model_calls"],
                  "fallbacks": st.stats["fallbacks"], "tool_errors": st.stats["tool_errors"]}
        if st.stats.get("interrupted_tool"):
            timing["interrupted_tool"] = st.stats["interrupted_tool"]
        await self._emit(ev.TurnCompleted(turn_id=turn_id, stop_reason=result.stop_reason,
                                          usage=result.usage, timing=timing), session)
        reply = result.message.text() if result.message else ""
        await self._memory("sync_turn", user.text(), reply, session_id=session, turn_author={"author": author})
        if self.memory is not None and hasattr(self.memory, "checkpoint"):
            recent = [provenance.flat(m) for m in self.store.messages(session)[-40:]]  # D-040: author per message
            await self._memory("checkpoint", session_id=session, messages=recent, reason="turn")
        if hasattr(self.context, "on_turn_complete"):
            await maybe_await(self.context.on_turn_complete(session, result.usage))
        info = self.store.update_session(session, **({"model": self.model} if self.model else {}))
        await self._emit(ev.SessionUpdated(session=info), session)
        if self.on_turn_end is not None:
            try:
                await self.on_turn_end(session, turn_id)
            except Exception as exc:  # observation never breaks a turn, but it is recorded
                await self._emit(trace.error_body("turn observer", exc), session)
        return result

    def _takes_recall(self) -> bool:
        """True when the context engine places per-turn recall itself (AgentContext, D-028), so the system
        message stays byte-stable for the prompt cache."""
        try:
            params = inspect.signature(self.context.build).parameters
        except (TypeError, ValueError):
            return False
        return "recall" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    async def base_system(self) -> str:
        """The stable part of the system text: system prompt and the memory provider's block (no recall)."""
        extra = self.system_extra() if self.system_extra else ""  # last: never moves what comes before it (D-030)
        return "\n\n".join(p for p in (self.system_prompt, await self._memory("system_prompt_block") or "", extra) if p)

    async def _system(self, session: str, query: str) -> tuple[str, str]:
        """(system text, recall). Recall is folded into the system text unless the context engine takes it."""
        base = await self.base_system()
        parts = [base]
        recall = await self._memory("prefetch", query, session_id=session) or ""
        if recall and not self._takes_recall():
            parts.append("# Recalled memory\n" + recall)
            recall = ""
        return "\n\n".join(p for p in parts if p), recall

    async def _rounds(self, session: str, turn_id: str, user: Message, st: _SessionState,
                      result: TurnResult) -> None:
        system, recall = await self._system(session, user.text())
        extra: dict[str, Any] = {"recall": recall, "turn_id": turn_id} if self._takes_recall() else {}
        cap = st.rounds or self.max_iterations
        for _ in range(cap):
            if hasattr(self.context, "note"):
                await maybe_await(self.context.note(session))
            messages = await self.context.build(session, system, **extra)
            msg_id = new_id("m")
            stream = await self._call(session, messages, msg_id, st, result)
            await self._record_call(session, stream, _, st, result)
            if stream.error is not None:
                result.stop_reason, result.error = "error", stream.error.error or "provider error"
                if stream.text or stream.reasoning:  # the user saw this text: keep it, marked, like an interrupt
                    partial = self._assemble(_Stream(text=stream.text, reasoning=stream.reasoning), msg_id)
                    partial.meta["interrupted"] = True
                    partial.meta["error"] = trace.scrub(result.error, 300)
                    result.message = await self._add(session, partial, turn_id)
                await self._emit(ev.Error(code="provider_error", message=result.error,
                                          retryable=stream.error.retryable), session)
                return
            reply = self._assemble(stream, msg_id)
            result.message = await self._add(session, reply, turn_id)
            if not reply.tool_calls:
                result.stop_reason = stream.done.stop_reason or "end"
                if result.stop_reason == "tool_use":
                    result.stop_reason = "end"
                if st.steer and result.stop_reason == "end":  # a message came in during this call: answer it now
                    for m in st.steer:
                        await self._add(session, m, turn_id)
                    st.steer.clear()
                    continue
                return
            await self._tools(session, turn_id, reply.tool_calls, st)
            if cap - _ - 1 == max(2, cap // 8):  # nearing the cap: say so once (D-064)
                await self._emit(ev.Notice(text=f"tool round {_ + 1} of {cap}; /rounds N raises the cap"), session)
            if st.steer:
                for m in st.steer:
                    await self._add(session, m, turn_id)
                st.steer.clear()
        result.stop_reason = "max_iterations"
        await self._emit(ev.Notice(text=f"stopped after {cap} tool rounds", level="warn"), session)
        if not self.wrap_up:
            return
        # One last call that may not use tools, so the turn ends with an answer. The tool specs stay: the history
        # holds tool blocks, and Anthropic rejects those without tool definitions. The prompt is never stored (D-064).
        messages = [*await self.context.build(session, system, **extra), Message.user(WRAP_UP_PROMPT.format(n=cap))]
        msg_id = new_id("m")
        stream = await self._call(session, messages, msg_id, st, result, tool_choice="none")
        await self._record_call(session, stream, cap, st, result)
        if stream.error is None and (stream.text or stream.reasoning):  # any tool call in the reply is dropped
            reply = self._assemble(_Stream(text=stream.text, reasoning=stream.reasoning), msg_id)
            reply.meta["wrap_up"] = True
            result.message = await self._add(session, reply, turn_id)

    async def _record_call(self, session: str, s: _Stream, iteration: int, st: _SessionState,
                           result: TurnResult) -> None:
        """The model.call event (what was asked, who served, every attempt) and the turn's running totals."""
        role = self.provider.name.split(":", 1)[1] if self.provider.name.startswith("router:") else ""
        st.stats["model_calls"] += 1
        st.stats["model_ms"] += s.total_ms
        st.stats["fallbacks"] += sum(1 for a in s.attempts if a.get("outcome") not in ("ok", None))
        served = ""
        if s.error is None:  # the route the router stamped, else the attempt that succeeded (D-025)
            served = s.route or next((a.get("route", "") for a in reversed(s.attempts) if a.get("outcome") == "ok"), "")
        await self._emit(ev.ModelCall(
            iteration=iteration + 1, role=role, requested=self.model or "", route=served,
            attempts=s.attempts, ttft_ms=s.first_ms, total_ms=s.total_ms, usage=s.usage,
            stop_reason=(s.done.stop_reason or "") if s.done is not None else "",
            error=trace.scrub(s.error.error, 300) if s.error is not None else "", retries=s.retries), session)
        if s.usage is not None:
            result.usage, st.total = result.usage + s.usage, st.total + s.usage
            await self._emit(ev.UsageUpdated(usage=s.usage, session_total=st.total, model=s.route or self.model or ""), session)

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
                    result: TurnResult, **call_opts: Any) -> _Stream:
        """Stream one model call with retry. Retries only while nothing was shown."""
        opts = {**self.provider_opts, **call_opts}  # tool_choice="none": the wrap-up call
        if self.model:
            opts["model"] = self.model
        # providers that route or cache per conversation send it as a header; all threads of one agent are one
        # conversation to them (D-028), so they share the provider's prompt cache
        key = getattr(self.context, "cache_key", None)
        opts["session_id"] = (key(session) if callable(key) else None) or session
        specs = self._specs()
        attempt, history, t0 = 0, [], time.monotonic()
        while True:
            s = _Stream(t0=t0)
            try:
                await self._interruptible(self._consume(session, messages, specs, opts, msg_id, s), st)
            except _Interrupted:
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
                                       f" {s.error.error}", level="warn"), session)
            await self._interruptible(asyncio.sleep(self.retry_delay * 2 ** (attempt - 1)), st)

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
                elif pe.kind == "notice":
                    await self._emit(ev.Notice(text=pe.text, level="warn"), session)
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

    async def _tools(self, session: str, turn_id: str, calls: list[ToolCall], st: _SessionState) -> None:
        risks = {s.name: s.risk for s in self._specs()}
        results: list[ToolResult] = []
        try:
            for call in calls:
                await self._emit(ev.ToolStarted(call=call, risk=risks.get(call.name, "low")), session)
                t0 = time.monotonic()
                try:
                    res = await self._interruptible(self._run_one(session, turn_id, call), st)
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

    async def _run_one(self, session: str, turn_id: str, call: ToolCall) -> ToolResult:
        if self.run_tool is None:
            return ToolResult.of_text(call.id, f"no tool runner; cannot run {call.name}", True)

        async def output(text: str) -> None:
            await self._emit(ev.ToolOutput(call_id=call.id, text=text), session)

        ctx = {"session": session, "turn_id": turn_id, "emit_output": output, "store": self.store}
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

