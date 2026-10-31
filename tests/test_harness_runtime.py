"""Disabled modules have no workers, stay unavailable, and leave foreground work usable."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from exo.core.tools.builtin import shell
from exo.core.tools.registry import PRE_TOOL_CALL, Registry, ToolContext, ToolError
from exo.core.types import ToolCall, ToolResult
from exo.harness_runtime import DisabledDelegator, DisabledJobs


def shell_engine():
    registry = Registry()
    shell.register(registry)
    return SimpleNamespace(registry=registry)


def test_disabled_jobs_copy_schema_without_changing_shared_tool_or_availability():
    engine = shell_engine()
    original = engine.registry.get("bash")
    assert original is not None
    original.check_fn = lambda: True
    parameters = deepcopy(original.spec.parameters)
    other = Registry()
    other.register(original.spec, original.handler, original.check_fn)

    DisabledJobs(engine)

    narrowed = engine.registry.get("bash")
    assert narrowed is not None and narrowed.spec is not original.spec
    assert narrowed.handler is original.handler and narrowed.check_fn is original.check_fn
    assert narrowed.spec.risk == original.spec.risk and narrowed.spec.toolset == original.spec.toolset
    assert "background" not in narrowed.spec.parameters["properties"]
    assert "job_output" not in narrowed.spec.description and "background: true" not in narrowed.spec.description
    assert original.spec.parameters == parameters
    assert "background" in other.specs()[0].parameters["properties"]
    narrowed.spec.parameters["properties"]["command"]["description"] = "local change"
    assert original.spec.parameters == parameters


def test_forged_background_argument_cannot_execute_when_jobs_are_disabled(tmp_path, monkeypatch):
    engine = shell_engine()
    manager = DisabledJobs(engine)

    def forbidden(*args, **kwargs):
        raise AssertionError("a disabled background call must not start a process")

    monkeypatch.setattr(shell, "run_command", forbidden)
    ctx = ToolContext(cwd=str(tmp_path), meta={"jobs": manager})
    # Registry validation permits unknown arguments, so schema omission alone is insufficient.
    result = asyncio.run(engine.registry.call(
        ToolCall("forged", "bash", {"command": "touch should-not-exist", "background": True}), ctx))
    assert result.is_error and "disabled" in result.text()
    assert not (tmp_path / "should-not-exist").exists()
    with pytest.raises(ToolError, match="disabled"):
        manager.start("touch should-not-exist", str(tmp_path), None)


def test_foreground_shell_still_executes_with_jobs_disabled(tmp_path):
    engine = shell_engine()
    manager = DisabledJobs(engine)
    result = asyncio.run(engine.registry.call(
        ToolCall("foreground", "bash", {"command": "printf profile-foreground"}),
        ToolContext(cwd=str(tmp_path), meta={"jobs": manager})))
    assert not result.is_error
    assert "profile-foreground" in result.text() and "[exit 0]" in result.text()


def test_disabled_jobs_block_before_external_executor_but_allow_foreground(tmp_path):
    calls = []

    class Broker:
        async def execute(self, request, tool, run_trusted):
            calls.append(request)
            return ToolResult.of_text(request.call_id, "executed foreground")

    registry = Registry(executor=Broker())
    shell.register(registry)
    DisabledJobs(SimpleNamespace(registry=registry))
    ctx = ToolContext(cwd=str(tmp_path))
    blocked = asyncio.run(registry.call(
        ToolCall("background", "bash", {"command": "sleep 1", "background": True}), ctx))
    assert blocked.is_error and "disabled" in blocked.text()
    assert calls == []
    foreground = asyncio.run(registry.call(ToolCall("foreground", "bash", {"command": "printf hi"}), ctx))
    assert not foreground.is_error and foreground.text() == "executed foreground"
    assert [request.call_id for request in calls] == ["foreground"]


def test_disabled_jobs_guard_sees_plugin_argument_rewrites(tmp_path, monkeypatch):
    engine = shell_engine()

    def rewrite(call, spec, ctx):
        call.arguments["background"] = True

    engine.registry.add_hook(PRE_TOOL_CALL, rewrite)
    manager = DisabledJobs(engine)

    def forbidden(*args, **kwargs):
        raise AssertionError("a plugin must not turn a foreground command into a disabled job")

    monkeypatch.setattr(shell, "run_command", forbidden)
    result = asyncio.run(engine.registry.call(
        ToolCall("rewritten", "bash", {"command": "sleep 1"}),
        ToolContext(cwd=str(tmp_path), meta={"jobs": manager})))
    assert result.is_error and "disabled" in result.text()


def test_late_hook_cannot_reintroduce_background_before_external_executor(tmp_path):
    class Broker:
        async def execute(self, request, tool, run_trusted):
            raise AssertionError("rewritten broker requests must never dispatch")

    registry = Registry(executor=Broker())
    shell.register(registry)
    DisabledJobs(SimpleNamespace(registry=registry))

    def rewrite(call, spec, ctx):
        call.arguments["background"] = True

    registry.add_hook(PRE_TOOL_CALL, rewrite)
    result = asyncio.run(registry.call(
        ToolCall("rewritten", "bash", {"command": "sleep 1"}), ToolContext(cwd=str(tmp_path))))
    assert result.is_error and "cannot change" in result.text()


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_background_guard_matches_shell_boolean_semantics(value, tmp_path, monkeypatch):
    engine = shell_engine()
    manager = DisabledJobs(engine)
    executed = []

    def foreground(command, *args, **kwargs):
        executed.append(command)
        return 0, "foreground", ""

    monkeypatch.setattr(shell, "run_command", foreground)
    result = asyncio.run(engine.registry.call(
        ToolCall("foreground", "bash", {"command": "printf hi", "background": value}),
        ToolContext(cwd=str(tmp_path), meta={"jobs": manager})))
    assert not result.is_error and executed == ["printf hi"]


def test_disabled_services_need_no_store_or_workers_and_support_lifecycle():
    engine = shell_engine()  # deliberately has no store, home, event hub or router
    jobs = DisabledJobs(engine)
    delegation = DisabledDelegator(engine)

    async def lifecycle():
        jobs.loop = asyncio.get_running_loop()
        assert await jobs.recover() == []
        assert jobs.rows() == [] and jobs.rows(limit=100) == []
        await jobs.close()
        await jobs.close()
        assert jobs.target.closed
        delegation.install()
        delegation.fail_stale()
        assert delegation.list("missing") == []
        assert not delegation.interrupt("missing")
        assert delegation.depth("missing") == 0
        with pytest.raises(ToolError, match="disabled"):
            await delegation.tool({"goal": "run a child"}, ToolContext())

    asyncio.run(lifecycle())
    assert engine.registry.get("delegate") is None
    assert not jobs.live and not delegation.loops


def test_runtime_accepts_disabled_factories_without_initializing_managers(tmp_path, monkeypatch):
    from exo.core.agents.delegate import Delegator
    from exo.core.engine import CoreRuntime
    from exo.core.jobs import JobManager

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled managers must not be initialized")

    monkeypatch.setattr(JobManager, "__init__", forbidden)
    monkeypatch.setattr(Delegator, "__init__", forbidden)

    async def run():
        engine = CoreRuntime(tmp_path / "runtime", {
            "fake": {"reply": "offline"},
            "roles": {"main": ["fake:scripted"], "aux": ["fake:scripted"]},
        }, env={}, attach_jobs=DisabledJobs, attach_agents=DisabledDelegator)
        try:
            engine.claim_writer()
            session = await engine.loop.create_session(agent=engine.agent)
            await engine.loop.send(session.id, "hello")
            assert engine.store.messages(session.id)[-1].text() == "offline"
            assert not engine.jobs.rows() and not engine.agents.list()
            assert engine.registry.get("delegate") is None
            assert "background" not in engine.registry.get("bash").spec.parameters["properties"]
            assert not (engine.home / "run" / "jobs").exists()
        finally:
            await engine.close()

    asyncio.run(run())
