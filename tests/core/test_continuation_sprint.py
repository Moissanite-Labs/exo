"""Offline engine regressions: terminal failures and real non-main owners."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.test_continuation_engine import begin, drain, setup, users
from exo.core.continuation import ORIGIN
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.tools.registry import ToolContext
from exo.core.types import ProviderEvent, ToolCall
from exo.model_spend import SpendDenied
from exo.scheduler import Scheduler


@pytest.mark.parametrize("failure", ["provider", "spend"])
def test_requested_continuation_cancelled_by_engine_failure(tmp_path, failure):
    async def run():
        engine, sid = await setup(tmp_path)
        requested, checked, sleeps = [], [], []
        original_spend = engine.router.spend

        def refuse_spend():
            checked.append(True)
            raise SpendDenied("sprint spend boundary stopped")

        async def after_request(tool_name, **kw):
            if tool_name != "exo_end_turn":
                return
            assert engine.continuations._s[sid].request.wakes
            requested.append(True)
            if failure == "spend":
                # Inject the dedicated spend exception at the real router's check,
                # AFTER the continuation request, not a generic provider error.
                engine.router.spend = SimpleNamespace(check=refuse_spend)
            else:
                engine.loop.provider = FakeProvider([ProviderEvent(
                    kind="error", error="sprint terminal provider failure", retryable=False)])

        async def unexpected_sleep(delay):
            sleeps.append(delay)

        engine.plugins.hooks.register("post_tool_call", after_request)
        engine.continuations.sleep = unexpected_sleep
        try:
            result = await begin(engine, sid)
            await drain(engine)
            assert requested == [True]
            assert result.stop_reason == "error" and result.error
            if failure == "spend":
                assert checked == [True]
                errors = [e.body for e in engine.hub.since(0) if e.type == "error"]
                assert any(e.code == "model_spend_refused" for e in errors), (errors, result.error)
                assert "sprint spend boundary stopped" in result.error
            else:
                assert not checked
                assert "sprint terminal provider failure" in result.error
            assert not sleeps
            assert not engine.continuations.pending(sid)
            assert engine.continuations._s[sid].request is None
            assert len(users(engine, sid)) == 1
            assert not any(m.meta.get("origin") == ORIGIN for m in users(engine, sid))
            assert not engine._tasks
        finally:
            engine.router.spend = original_spend
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize("owner", ["scheduler", "subagent"])
def test_real_non_main_run_rejects_continuation_at_both_boundaries(tmp_path, owner):
    async def run():
        engine, parent = await setup(tmp_path)
        seen, rejected, children = [], [], []
        scripted = FakeProvider([tool_turn("exo_end_turn", {"continue_now": True},
                                                  call_id="forbidden"), "done"])

        class Inspect:
            name = "fake"

            async def stream(self, messages, tools, **opts):
                seen.append({tool.name for tool in tools})
                loops = engine.scheduler.loops if owner == "scheduler" else engine.agents.loops
                assert len(loops) == 1
                child = next(iter(loops))
                assert child != parent
                children.append(child)
                assert engine.store.get_session(child).kind == (
                    "scheduled" if owner == "scheduler" else "subagent")
                # Direct registry dispatch deliberately bypasses tool discovery;
                # the real owner's live loops map must still fence this session.
                denied = await engine.registry.call(ToolCall(id="direct", name="exo_end_turn",
                    arguments={"continue_now": True}),
                    ToolContext(session=child, meta={"turn_id": "direct-probe"}))
                assert denied.is_error and "subagent or scheduled run" in denied.text()
                rejected.append(denied)
                async for event in scripted.stream(messages, tools, **opts):
                    yield event

        engine.loop.provider = Inspect()
        try:
            assert "exo_end_turn" in {tool.name for tool in engine._specs()}
            if owner == "scheduler":
                engine.scheduler = Scheduler(engine)
                assert await engine.scheduler.run({"id": "sprint", "title": "sprint",
                    "tier": "standard", "prompt": "work", "deliver": "notice"},
                    "sprint-task") == "done"
            else:
                brief = engine.agents._brief(parent, 0, {"goal": "work"}, str(tmp_path))
                await engine.agents.run(parent, brief)
            await drain(engine)
            assert len(seen) == 2 and len(rejected) == 2
            assert all("exo_end_turn" not in names for names in seen)
            assert len(set(children)) == 1
            child = children[0]
            results = [r for m in engine.store.messages(child) for r in m.tool_results]
            denied = [r for r in results if r.call_id == "forbidden"]
            assert len(denied) == 1 and denied[0].is_error
            assert ("not available to this scheduled run" if owner == "scheduler" else
                    "not available to this subagent") in denied[0].text()
            assert not engine.continuations.pending(child)
            assert not any(m.meta.get("origin") == ORIGIN for m in users(engine, child))
        finally:
            await engine.close()
    asyncio.run(run())
