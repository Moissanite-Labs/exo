"""The async turn loop.

One turn: store the user message, build a fresh model input (context engine),
stream the reply, run any tool calls, and loop until the model stops, the
iteration cap is hit, or the turn is interrupted. Every step is an event,
written to sessions.sqlite before it is published (``EventBus.emit``).

Injected, never imported: the provider (any ``exo.core.types.Provider``), the
tool specs and tool runner, the memory provider and the context engine. That
keeps the loop independent of the registry, router and slot modules.

Memory call sites (all optional, sync or async): ``system_prompt_block()``,
``prefetch(query, session_id=)``, ``on_turn_start(turn_number, message, session_id=)``,
``sync_turn(user_content, assistant_content, session_id=)``.
Context call sites: ``note(session_id)``, ``await build(session_id, system)``,
optional ``on_turn_complete(session_id, usage)`` and an ``on_event`` attribute.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from exo.core import trace
from exo.core.context import TreeContext, _maybe_await
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


class _SessionState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.interrupt = asyncio.Event()
        self.steer: list[Message] = []
        self.running = False
        self.total = Usage()
        self.stats: dict[str, int] = {}  # this turn's timing summary, filled as it runs (D-025)


class Loop:
    def __init__(self, store: SessionStore, provider: Provider, *, bus: EventBus | None = None,
                 context: Any = None, memory: Any = None,
                 tool_specs: Iterable[ToolSpec] | Callable[[], Iterable[ToolSpec]] = (),
                 run_tool: ToolRunner | None = None, system_prompt: str = "",
                 model: str | None = None, max_iterations: int = 24, max_retries: int = 3,
                 retry_delay: float = 0.5, provider_opts: dict[str, Any] | None = None):
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
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.provider_opts = dict(provider_opts or {})
        self.approval_ms: dict[str, int] = {}  # session -> time spent waiting on approvals; the engine adds to it
        self.on_turn_end: Callable[[str, str], Awaitable[None]] | None = None  # (session, turn_id), after the turn
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
            await _maybe_await(self.context.note(session))
        return self.store.get_session(session)  # type: ignore[return-value]

    def interrupt(self, session: str) -> bool:
        st = self.state(session)
        if st.running:
            st.interrupt.set()
        return st.running

    async def send(self, session: str, text: str = "", parts: list[ContentPart] | None = None,
                   mode: str = "send") -> TurnResult | None:
        """Run a turn. ``steer`` while a turn runs injects the message between tool
        rounds and returns None; ``queue`` and ``send`` wait for the running turn."""
        if self.store.get_session(session) is None:
            raise KeyError(session)
        self.store.assert_open(session)
        msg = Message(role="user", content=list(parts or []))
        if text:
            msg.content.insert(0, ContentPart.of_text(text))
        st = self.state(session)
        if mode == "steer" and st.running:
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
        self.store.append_message(session, msg, turn_id)
        await self._emit(ev.MessageCompleted(message=msg), session)
        return msg

    async def _turn(self, session: str, user: Message, st: _SessionState) -> TurnResult:
        turn_id, t0 = new_id("t"), time.monotonic()
        st.stats = {"model_ms": 0, "tool_ms": 0, "model_calls": 0, "fallbacks": 0, "tool_errors": 0}
        waited = self.approval_ms.get(session, 0)
        await self._repair(session)
        await self._add(session, user, turn_id)
        await self._emit(ev.TurnStarted(turn_id=turn_id, model=self.model or ""), session)
        turn_no = sum(1 for _ in self.store.messages(session) if _.role == "user")
        await self._memory("on_turn_start", turn_no, user.text(), session_id=session)
        result = TurnResult(turn_id=turn_id, stop_reason="end")
        try:
            await self._rounds(session, turn_id, user, st, result)
        except _Interrupted:
            result.stop_reason = "interrupted"
        except Exception as exc:  # unexpected bug in a collaborator: end the turn cleanly
            result.stop_reason, result.error = "error", str(exc) or type(exc).__name__
            await self._emit(ev.Error(code="turn_failed", message=result.error), session)
            await self._emit(trace.error_body("turn loop", exc), session)
        wall, approval = int((time.monotonic() - t0) * 1000), self.approval_ms.get(session, 0) - waited
        timing = {"wall_ms": wall, "model_ms": st.stats["model_ms"], "approval_ms": approval,
                  "tool_ms": max(0, st.stats["tool_ms"] - approval), "model_calls": st.stats["model_calls"],
                  "fallbacks": st.stats["fallbacks"], "tool_errors": st.stats["tool_errors"]}
        await self._emit(ev.TurnCompleted(turn_id=turn_id, stop_reason=result.stop_reason,
                                          usage=result.usage, timing=timing), session)
        reply = result.message.text() if result.message else ""
        await self._memory("sync_turn", user.text(), reply, session_id=session)
        if self.memory is not None and hasattr(self.memory, "checkpoint"):
            recent = [{"role": m.role, "content": m.text()} for m in self.store.messages(session)[-40:]]
            await self._memory("checkpoint", session_id=session, messages=recent, reason="turn")
        if hasattr(self.context, "on_turn_complete"):
            await _maybe_await(self.context.on_turn_complete(session, result.usage))
        info = self.store.update_session(session)
        await self._emit(ev.SessionUpdated(session=info), session)
        if self.on_turn_end is not None:
            try:
                await self.on_turn_end(session, turn_id)
            except Exception as exc:  # observation never breaks a turn, but it is recorded
                await self._emit(trace.error_body("turn observer", exc), session)
        return result

    async def _system(self, session: str, query: str) -> str:
        parts = [self.system_prompt]
        parts.append(await self._memory("system_prompt_block") or "")
        recall = await self._memory("prefetch", query, session_id=session) or ""
        if recall:
            parts.append("# Recalled memory\n" + recall)
        return "\n\n".join(p for p in parts if p)

    async def _rounds(self, session: str, turn_id: str, user: Message, st: _SessionState,
                      result: TurnResult) -> None:
        system = await self._system(session, user.text())
        for _ in range(self.max_iterations):
            if hasattr(self.context, "note"):
                await _maybe_await(self.context.note(session))
            messages = await self.context.build(session, system)
            msg_id = new_id("m")
            stream = await self._call(session, messages, msg_id, st, result)
            await self._record_call(session, stream, _, st)
            if stream.usage is not None:
                result.usage = result.usage + stream.usage
                st.total = st.total + stream.usage
                await self._emit(ev.UsageUpdated(usage=stream.usage, session_total=st.total, model=stream.route or self.model or ""), session)
            if stream.error is not None:
                result.stop_reason, result.error = "error", stream.error.error or "provider error"
                await self._emit(ev.Error(code="provider_error", message=result.error,
                                          retryable=stream.error.retryable), session)
                return
            reply = self._assemble(stream, msg_id)
            result.message = await self._add(session, reply, turn_id)
            if not reply.tool_calls:
                result.stop_reason = stream.done.stop_reason or "end"
                if result.stop_reason == "tool_use":
                    result.stop_reason = "end"
                return
            await self._tools(session, turn_id, reply.tool_calls, st)
            if st.steer:
                for m in st.steer:
                    await self._add(session, m, turn_id)
                st.steer.clear()
        result.stop_reason = "max_iterations"
        await self._emit(ev.Notice(text=f"stopped after {self.max_iterations} tool rounds",
                                   level="warn"), session)

    async def _record_call(self, session: str, s: _Stream, iteration: int, st: _SessionState) -> None:
        """The model.call event (what was asked, who served, every attempt) and the turn's running totals."""
        role = self.provider.name.split(":", 1)[1] if self.provider.name.startswith("router:") else ""
        st.stats["model_calls"] += 1
        st.stats["model_ms"] += s.total_ms
        st.stats["fallbacks"] += sum(1 for a in s.attempts if a.get("outcome") not in ("ok", None))
        await self._emit(ev.ModelCall(
            iteration=iteration + 1, role=role, requested=self.model or "", route=s.route if s.error is None else "",
            attempts=s.attempts, ttft_ms=s.first_ms, total_ms=s.total_ms, usage=s.usage,
            stop_reason=(s.done.stop_reason or "") if s.done is not None else "",
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
                    result: TurnResult) -> _Stream:
        """Stream one model call with retry. Retries only while nothing was shown."""
        opts = dict(self.provider_opts)
        if self.model:
            opts["model"] = self.model
        opts["session_id"] = session  # providers that route or cache per conversation send it as a header
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
                res = await self._interruptible(self._run_one(session, turn_id, call), st)
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
            res = await _maybe_await(self.run_tool(call, ctx))
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
        try:
            return await _maybe_await(fn(*args, **kw))
        except Exception as exc:  # memory is best effort; a failing provider never breaks a turn, but it is recorded
            await self._emit(trace.error_body(f"memory.{method}", exc), kw.get("session_id"))
            return None


def _err(text: str, retryable: bool) -> Any:
    from exo.core.types import ProviderEvent

    return ProviderEvent(kind="error", error=text, retryable=retryable)

