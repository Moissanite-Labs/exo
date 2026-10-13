"""Broker dispatch order, immutable approvals and fail-closed behavior."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from exo.core.tools import POST_TOOL_CALL, PRE_TOOL_CALL, Block, Registry, ToolContext
from exo.core.tools.executor import (
    ExecutionRequest, ExecutorConfigurationError, load_executor, thaw_arguments,
)
from exo.core.types import ToolCall, ToolResult


class Broker:
    async def execute(self, request, tool, run_trusted):
        return await run_trusted()


def test_gates_validation_broker_handler_and_post_order():
    seen = []

    class Recording(Broker):
        async def execute(self, request, tool, run_trusted):
            seen.append(("broker", request.tool_name, request.arguments["value"]))
            return await run_trusted()

    reg = Registry(executor=Recording(), executor_required=True)
    reg.tool("t", "demo", {"required": ["value"], "properties": {"value": {"type": "integer"}}})(
        lambda args, ctx: seen.append(("handler", args["value"])) or "done")

    async def approve(call, spec, ctx):
        seen.append("approval")
        await asyncio.sleep(0)
        call.arguments["value"] = 7

    reg.add_hook(PRE_TOOL_CALL, approve)
    reg.add_hook(POST_TOOL_CALL, lambda call, result, ctx: seen.append(("post", result.text())))
    result = reg.call_sync(ToolCall("c", "t", {"value": "rewritten"}))
    assert result.text() == "done"
    assert seen == ["approval", ("broker", "t", 7), ("handler", 7), ("post", "done")]


@pytest.mark.parametrize("gate", ["block", "invalid"])
def test_blocked_or_invalid_never_reaches_broker(gate):
    class Never(Broker):
        async def execute(self, *args):
            pytest.fail("broker reached before gate or validation")

    reg = Registry(executor=Never(), executor_required=True)
    reg.tool("t", "demo", {"required": ["value"]})(lambda *args: pytest.fail("handler ran"))
    if gate == "block":
        reg.add_hook(PRE_TOOL_CALL, lambda *args: Block("denied"))
    assert reg.call_sync(ToolCall("c", "t")).is_error


def test_deep_snapshot_detaches_call_and_context_during_broker_wait():
    call = ToolCall("c", "t", {"nested": {"list": ["approved"]}})
    ctx = ToolContext(cwd="/approved", session="s")
    seen = []

    class Snapshot(Broker):
        async def execute(self, request, tool, run_trusted):
            with pytest.raises(TypeError):
                request.arguments["nested"]["list"][0] = "bad"
            with pytest.raises(FrozenInstanceError):
                request.cwd = "/bad"
            call.arguments["nested"]["list"][0] = "changed"
            ctx.cwd = "/changed"
            await asyncio.sleep(0)
            assert thaw_arguments(request.arguments) == {"nested": {"list": ["approved"]}}
            return await run_trusted()

    reg = Registry(executor=Snapshot())
    reg.tool("t", "demo")(lambda args, trusted_ctx: seen.append((args, trusted_ctx.cwd)) or "ok")
    assert reg.call_sync(call, ctx).text() == "ok"
    assert seen == [({"nested": {"list": ["approved"]}}, "/approved")]


@pytest.mark.parametrize("failure", ["exception", "bad_result", "missing"])
def test_executor_failure_never_falls_back_and_runs_post(failure):
    class Failing(Broker):
        async def execute(self, request, tool, run_trusted):
            await asyncio.sleep(0)
            if failure == "exception":
                raise RuntimeError("offline")
            return "malformed"

    reg = Registry(executor=None if failure == "missing" else Failing(), executor_required=True)
    reg.tool("t", "demo")(lambda *args: pytest.fail("handler fallback"))
    seen = []
    reg.add_hook(POST_TOOL_CALL, lambda call, result, ctx: seen.append(result))
    result = reg.call_sync(ToolCall("c", "t"))
    assert result.is_error and seen == [result]


def test_unknown_tool_and_name_rewrite_fail_closed():
    class Never(Broker):
        async def execute(self, *args):
            pytest.fail("broker must not dispatch unknown or rewritten identity")

    reg = Registry(executor=Never())
    reg.tool("t", "demo")(lambda *args: pytest.fail("handler ran"))
    assert reg.call_sync(ToolCall("c", "unknown")).is_error
    reg.add_hook(PRE_TOOL_CALL, lambda call, spec, ctx: setattr(call, "name", "unknown"))
    assert "call identity" in reg.call_sync(ToolCall("c", "t")).text()


def test_cancel_propagates_and_never_falls_back():
    async def main():
        entered = asyncio.Event()

        class Waiting(Broker):
            async def execute(self, request, tool, run_trusted):
                entered.set()
                await asyncio.Future()

        reg = Registry(executor=Waiting(), executor_required=True)
        reg.tool("t", "demo")(lambda *args: pytest.fail("handler ran"))
        ctx = ToolContext()
        task = asyncio.create_task(reg.call(ToolCall("c", "t"), ctx))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ctx.cancel.is_set()

    asyncio.run(main())


def test_request_rejects_non_json_and_freezes_constructed_values():
    source = {"items": [1]}
    request = ExecutionRequest("c", "t", source, "/w", None)
    source["items"].append(2)
    assert request.arguments["items"] == (1,)
    with pytest.raises(ValueError):
        ExecutionRequest("c", "t", {"object": object()}, "/w", None)


def test_configured_optional_broker_cannot_disappear_into_host_fallback():
    reg = Registry(executor=Broker())
    reg.tool("t", "demo")(lambda *args: pytest.fail("host fallback"))
    reg.executor = None
    assert reg.call_sync(ToolCall("c", "t")).is_error


@pytest.mark.parametrize("name", ["", "module:factory", "/tmp/executor.py", "missing"])
def test_required_loader_refuses_missing_and_arbitrary_paths(monkeypatch, name):
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [])
    with pytest.raises(ExecutorConfigurationError):
        load_executor({"EXO_TOOL_EXECUTOR_REQUIRED": "1", "EXO_TOOL_EXECUTOR": name})


@pytest.mark.parametrize("factory", [lambda: object(), lambda: 1 / 0])
def test_optional_explicit_executor_also_fails_closed(monkeypatch, factory):
    class Entry:
        name = "test"

        def load(self):
            return factory

    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [Entry()])
    with pytest.raises(ExecutorConfigurationError):
        load_executor({"EXO_TOOL_EXECUTOR": "test"})


def test_named_loader_validates_factory_and_duplicate_names(monkeypatch):
    class Entry:
        name = "test"

        def load(self):
            return Broker

    def entries(**kw):
        assert kw == {"group": "exo.executors"}
        return [Entry()]

    monkeypatch.setattr("importlib.metadata.entry_points", entries)
    broker, required = load_executor({"EXO_TOOL_EXECUTOR": "test", "EXO_TOOL_EXECUTOR_REQUIRED": "1"})
    assert isinstance(broker, Broker) and required
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [Entry(), Entry()])
    with pytest.raises(ExecutorConfigurationError):
        load_executor({"EXO_TOOL_EXECUTOR": "test"})


def test_broker_results_are_capped_identified_and_postprocessed():
    class ResultBroker(Broker):
        async def execute(self, *args):
            return ToolResult.of_text("wrong", "x" * 100_005)

    reg = Registry(executor=ResultBroker())
    reg.tool("t", "demo")(lambda *args: pytest.fail("handler ran"))
    result = reg.call_sync(ToolCall("c", "t"))
    assert result.call_id == "c" and result.text().endswith("[truncated 5 chars]")
