"""Accepted end-turn intent closes tool use without manufacturing a new turn or wake."""
from __future__ import annotations

import asyncio

from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.types import Message, ToolCall, ToolSpec
from tests.core.test_continuation_delegate_wake import setup, wakes
from tests.core.test_continuation_engine import begin, drain


class FinishingProvider(FakeProvider):
    """An advisory finish instruction is ignored; the provider honors explicit tool_choice."""
    async def stream(self, messages, tools, **opts):
        if opts.get("tool_choice") == "none":
            self.script = ["Finished; no follow-up remains."]
        async for event in super().stream(messages, tools, **opts):
            yield event


def test_explicit_end_turn_prevents_repeated_same_turn_tool_rounds(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        provider = FinishingProvider([tool_turn("exo_end_turn", {"followup_pending": True})] * 8 + ["finished"])
        e.loop.provider = provider
        try:
            result = await begin(e, sid)
            await drain(e)
            assert len(provider.calls) == 2
            assert provider.calls[1]["opts"]["tool_choice"] == "none"
            assert result.message.text() == "Finished; no follow-up remains."
            assert result.stop_reason == "end" and not result.error
            assert result.usage.output_tokens > 0
            assert not wakes(e, sid) and not e.continuations.pending(sid)
            assert sum(ev.type == "turn.started" for ev in e.store.events_since(session=sid)) == 1
        finally:
            await e.close()
    asyncio.run(run())


def test_full_tool_batch_finishes_and_stray_final_calls_are_not_executed_or_stored(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        ran = []
        e.registry.register(ToolSpec(name="record", description="test", parameters={}), lambda args, ctx: ran.append(1) or "ok")
        first = Message.assistant("", [ToolCall("end", "exo_end_turn", {}), ToolCall("sibling", "record", {})])
        final = tool_turn("record", call_id="stray", text="Finished.")
        reason = {"type": "reasoning", "id": "reason", "encrypted_content": "opaque"}
        native_text = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Finished."}]}
        final.meta = {"responses_origin": "fixture", "responses_output": [reason, native_text,
            {"type": "function_call", "call_id": "stray", "name": "record", "arguments": "{}"},
            {"type": "custom_tool_call", "call_id": "stray-custom", "name": "record", "input": "test"}]}
        provider = FakeProvider([first, final])
        e.loop.provider = provider
        try:
            result = await begin(e, sid)
            assert ran == [1] and len(provider.calls) == 2
            assert result.stop_reason == "end" and result.message.text() == "Finished."
            messages = e.store.messages(sid)
            assert not result.message.tool_calls
            from exo.core.models.responses import convert_input
            replay = convert_input(messages, "fixture")
            assert not any(item.get("type") == "function_call" and item.get("call_id") == "stray" for item in replay)
            assert result.message.meta["responses_output"] == [reason, native_text]
            assert reason in replay and native_text in replay
            assert [r.call_id for m in messages if m.role == "tool" for r in m.tool_results] == ["end", "sibling"]
        finally:
            await e.close()
    asyncio.run(run())


def test_invalid_end_turn_does_not_disable_tools(tmp_path):
    async def run():
        e, sid, _ = await setup(tmp_path)
        provider = FakeProvider([tool_turn("exo_end_turn", {"wake_after": 0}), "corrected"])
        e.loop.provider = provider
        try:
            result = await begin(e, sid)
            assert "tool_choice" not in provider.calls[1]["opts"]
            assert result.message.text() == "corrected"
        finally:
            await e.close()
    asyncio.run(run())


def test_principal_steer_during_final_reply_restores_tools_for_new_work(tmp_path):
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    from tests.core.test_continuation_engine import until
    async def run():
        e, sid, _ = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        class PausedProvider(FakeProvider):
            async def stream(self, messages, tools, **opts):
                if len(self.calls) == 1:
                    entered.set()
                    await release.wait()
                async for event in super().stream(messages, tools, **opts):
                    yield event
        provider = PausedProvider([tool_turn("exo_end_turn", {"continue_now": True}), "old final", "new work finished"])
        e.loop.provider = provider
        task = asyncio.create_task(begin(e, sid))
        try:
            await until(entered.is_set)
            response = await Dispatcher(e).run(s.Command.of(s.TurnSend(session=sid, text="new work", mode="steer")), "principal:tui")
            assert response.ok
            release.set()
            result = await task
            await drain(e)
            assert result.message.text() == "new work finished"
            assert provider.calls[1]["opts"]["tool_choice"] == "none"
            assert "tool_choice" not in provider.calls[2]["opts"]
            assert not wakes(e, sid) and not e.continuations.pending(sid)
        finally:
            release.set()
            await task
            await e.close()
    asyncio.run(run())


def test_stop_during_final_reply_suppresses_requested_wake(tmp_path):
    from exo.protocol import schema as s
    from tests.core.test_continuation_engine import until
    async def run():
        e, sid, _ = await setup(tmp_path)
        entered = asyncio.Event()
        class PausedProvider(FakeProvider):
            async def stream(self, messages, tools, **opts):
                if len(self.calls) == 1:
                    entered.set()
                    await asyncio.Event().wait()
                async for event in super().stream(messages, tools, **opts):
                    yield event
        e.loop.provider = PausedProvider([tool_turn("exo_end_turn", {"continue_now": True}), "unreachable"])
        task = asyncio.create_task(begin(e, sid))
        try:
            await until(entered.is_set)
            await e._turn_interrupt(s.TurnInterrupt(session=sid))
            result = await task
            await drain(e)
            assert result.stop_reason == "interrupted"
            assert not wakes(e, sid) and not e.continuations.pending(sid)
        finally:
            await task
            await e.close()
    asyncio.run(run())


def test_same_batch_permission_denial_suppresses_end_turn_wake(tmp_path):
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    from tests.core.test_continuation_engine import until
    async def run():
        e, sid, _ = await setup(tmp_path)
        e.registry.register(ToolSpec(name="needs_permission", description="test", parameters={}),
                            lambda args, ctx: (_ for _ in ()).throw(AssertionError("denied tool ran")))
        e.plugins.hooks.register("pre_tool_call", lambda tool_name, **kw:
            {"action": "approve", "message": "permission?"} if tool_name == "needs_permission" else None)
        first = Message.assistant("", [ToolCall("end", "exo_end_turn", {"continue_now": True}),
                                       ToolCall("permission", "needs_permission", {})])
        provider = FakeProvider([first, "Permission denied; stopped."])
        e.loop.provider = provider
        task = asyncio.create_task(begin(e, sid))
        try:
            await until(lambda: bool(e.approvals.open))
            response = await Dispatcher(e).run(s.Command.of(s.ApprovalRespond(
                request_id=next(iter(e.approvals.open)), decision="deny")), "principal:tui")
            assert response.ok
            result = await task
            await drain(e)
            assert result.message.text() == "Permission denied; stopped."
            assert provider.calls[1]["opts"]["tool_choice"] == "none"
            assert not wakes(e, sid) and not e.continuations.pending(sid)
            events=e.store.events_since(session=sid)
            assert any(isinstance(ev.body, s.ToolCompleted) and ev.body.result.is_error for ev in events)
        finally:
            await task
            await e.close()
    asyncio.run(run())


def test_worker_or_scheduler_end_turn_rejection_does_not_finalize(tmp_path):
    from exo.core.continuation import register
    async def run():
        e, sid, _ = await setup(tmp_path)
        register(e.registry, e.continuations, allowed=lambda session: False)
        provider = FakeProvider([tool_turn("exo_end_turn", {}), "worker finished normally"])
        e.loop.provider = provider
        try:
            result = await begin(e, sid)
            assert "tool_choice" not in provider.calls[1]["opts"]
            assert result.message.text() == "worker finished normally"
            assert not e.continuations.ending(sid, result.turn_id)
        finally:
            await e.close()
    asyncio.run(run())


def test_principal_steer_before_final_call_clears_old_finish_intent(tmp_path):
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    from tests.core.test_continuation_engine import until
    async def run():
        e, sid, _ = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause(tool_name, **kw):
            if tool_name == "exo_end_turn":
                entered.set()
                await release.wait()
        e.plugins.hooks.register("post_tool_call", pause)
        provider = FakeProvider([tool_turn("exo_end_turn", {"continue_now": True}), "new request finished"])
        e.loop.provider = provider
        task = asyncio.create_task(begin(e, sid))
        try:
            await until(entered.is_set)
            response = await Dispatcher(e).run(s.Command.of(s.TurnSend(session=sid, text="new work", mode="steer")), "principal:tui")
            assert response.ok
            release.set()
            result = await task
            await drain(e)
            assert result.message.text() == "new request finished"
            assert "tool_choice" not in provider.calls[1]["opts"]
            assert not wakes(e, sid) and not e.continuations.pending(sid)
        finally:
            release.set()
            await task
            await e.close()
    asyncio.run(run())
