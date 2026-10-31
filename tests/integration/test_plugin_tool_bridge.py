"""The application plugin binder preserves direct CoreRuntime behavior and shims."""
import asyncio
import inspect

import pytest

from exo.core.engine import CoreRuntime
from exo.core.plugins import PluginManager
from exo.core.slots import NotConfigured
from exo.core.tools.registry import ToolContext
from exo.core.types import ToolCall, ToolSpec

MODELS = {"fake": {"reply": "offline"}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


class Memory:
    name = "memory"

    def __init__(self, failure=None):
        self.failure, self.initialized, self.calls = failure, [], []

    def is_available(self):
        return True

    def initialize(self, session, **kwargs):
        self.initialized.append((session, kwargs))
        if self.failure:
            raise self.failure

    def get_tool_schemas(self):
        return [{"name": "memory_probe", "description": "Inspect the memory call context."}]

    def handle_tool_call(self, name, args, **kwargs):
        self.calls.append((name, args, kwargs))
        return "memory result"


def test_direct_runtime_keeps_memory_initialization_private_scope_and_call_metadata(tmp_path):
    manager = PluginManager()
    memory = Memory()
    manager.slots.fill("memory", memory, "test-memory")
    seen = []
    manager.hooks.register("pre_tool_call", lambda **kwargs: seen.append(kwargs), "test-allow")
    engine = CoreRuntime(tmp_path / "home", MODELS, plugins=manager, env={})
    assert memory.initialized == [("", {"exo_home": str(engine.home), "hermes_home": str(engine.home),
                                       "platform": "exo", "agent_log": engine.log_view})]

    async def run():
        try:
            private = await engine.loop.create_session(cwd=str(tmp_path), kind="private", agent=engine.agent)
            child = await engine.loop.create_session(cwd=str(tmp_path), parent=private.id, kind="subagent", agent=engine.agent)
            normal = await engine.loop.create_session(cwd=str(tmp_path), agent=engine.agent)
            for session in (child, normal):
                call = ToolCall(session.id, "memory_probe", {})
                result = await engine.run_tool(call, {"session": session.id, "turn_id": "turn"})
                assert result.text() == "memory result" and not result.is_error
                kwargs = memory.calls[-1][2]
                assert (kwargs.get("agent_context") == "private") is (session is child)
                assert kwargs["agent_log"] is engine.log_view
                assert kwargs["session_id"] == session.id
                assert engine._memory_kw(session.id).get("agent_context") == kwargs.get("agent_context")
                assert seen[-1]["cwd"] == str(tmp_path) and seen[-1]["turn_id"] == "turn"
            # Metadata comes from the current call and registered spec, not a startup snapshot.
            original = engine.registry.get("memory_probe")
            engine.registry.register(ToolSpec(name="memory_probe", description="Current spec", risk="high"),
                                     original.handler, replace=True)
            metadata = engine._hook_kw(ToolCall("later", "memory_probe", {"changed": True}),
                                       ToolContext(cwd="new/cwd", session=normal.id, meta={"turn_id": "later-turn"}))
            assert metadata["risk"] == "high" and metadata["args"] == {"changed": True}
            assert metadata["cwd"] == "new/cwd" and metadata["turn_id"] == "later-turn"
        finally:
            await engine.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure,off,error", [
    (NotConfigured("set up memory"), "set up memory", ""),
    (RuntimeError("memory startup failed"), "", "memory startup failed"),
])
def test_initialization_diagnostics_are_still_exposed_on_core_runtime(tmp_path, failure, off, error):
    manager = PluginManager()
    manager.slots.fill("memory", Memory(failure), "test-memory")
    engine = CoreRuntime(tmp_path / "home", MODELS, plugins=manager, env={})
    try:
        assert (engine.memory_off, engine.memory_init_error) == (off, error)
        assert engine.registry.get("memory_probe") is not None
    finally:
        asyncio.run(engine.close())


def test_plugin_handler_compatibility_shim_preserves_async_identity_and_keywords():
    async def handler(args, **kwargs):
        return args, kwargs

    wrapped = CoreRuntime._plugin_handler(handler)
    assert inspect.iscoroutinefunction(wrapped)
    assert asyncio.run(wrapped({"hello": 1}, ToolContext(session="session", call_id="call"))) == (
        {"hello": 1}, {"session_id": "session", "task_id": "session", "tool_call_id": "call"})


def test_existing_named_adapter_imports_remain_available():
    from exo.core import engine, plugins
    from exo.core.tools.registry import Block
    assert engine.call_plugin is plugins.call_plugin
    assert engine.safe_plugin_call is plugins.safe_plugin_call
    assert engine.NotConfigured is NotConfigured and engine.Block is Block
