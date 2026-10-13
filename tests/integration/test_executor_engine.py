"""Engine startup, delegation and background paths obey the executor boundary."""

import asyncio

import pytest

from exo.compose import create
from exo.core.engine import CoreEngine
from exo.scheduler import Scheduler
from exo.core.plugins import LoadedPlugin, Manifest, PluginManager
from exo.core.tools.executor import ExecutorConfigurationError
from exo.core.types import ToolCall, ToolResult
from exo.wire import Brief


class DenyBroker:
    trusted_plugin_dirs = ()
    required_plugins = ()

    def __init__(self):
        self.seen = []

    async def execute(self, request, tool, run_trusted):
        self.seen.append(request)
        return ToolResult.of_text(request.call_id, "executor denied", True)


def test_required_engine_fails_before_store_or_optional_plugin_import(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    monkeypatch.setattr(PluginManager, "load_all", lambda *args, **kw: pytest.fail("plugin imports before executor"))
    with pytest.raises(ExecutorConfigurationError):
        create()
    home = tmp_path / "not_created"
    with pytest.raises(ExecutorConfigurationError):
        CoreEngine(home)
    assert not home.exists()


@pytest.mark.parametrize("server", [{"command": "touch", "args": ["/tmp/no"]}, {"url": "https://example.com/mcp"}])
def test_required_mcp_config_fails_before_startup(tmp_path, server):
    home = tmp_path / "not_created"
    with pytest.raises(ExecutorConfigurationError, match="MCP transport startup"):
        CoreEngine(home, executor=DenyBroker(), executor_required=True, mcp={"test": server})
    assert not home.exists()


def test_engine_main_child_and_background_share_broker(tmp_path, monkeypatch):
    broker = DenyBroker()
    engine = CoreEngine(tmp_path / "home", executor=broker, executor_required=True, cwd=str(tmp_path))
    monkeypatch.setattr(engine.jobs, "start", lambda *args, **kw: pytest.fail("host background handler ran"))

    async def main():
        try:
            for arguments in ({"command": "echo hi"}, {"command": "echo hi", "background": True}):
                result = await engine.run_tool(ToolCall("main", "bash", arguments), {})
                assert result.is_error and result.text() == "executor denied"
            child = engine.agents._loop(Brief(id="b", thread="parent", goal="test", tools=["bash"]),
                                        engine.router.provider("main"), "fake:x")
            result = await child.run_tool(ToolCall("child", "bash", {"command": "echo child"}), {})
            assert result.text() == "executor denied"
            assert [request.call_id for request in broker.seen] == ["main", "main", "child"]
        finally:
            await engine.close()

    asyncio.run(main())


def test_secure_plugin_discovery_uses_only_executor_roots(tmp_path, monkeypatch):
    broker = DenyBroker()
    broker.trusted_plugin_dirs = (str(tmp_path / "audited"),)
    monkeypatch.setattr("exo.compose.load_executor", lambda: (broker, True))
    seen = []

    def load_all(self, **kw):
        seen.append(kw)
        self.plugins["exo-gate"] = LoadedPlugin(Manifest(name="exo-gate"), "audited")

    monkeypatch.setattr(PluginManager, "load_all", load_all)
    engine = create()
    assert seen == [{"dirs": broker.trusted_plugin_dirs, "enabled": None}]
    asyncio.run(engine.close())


def test_missing_required_executor_plugin_refuses_factory(monkeypatch):
    broker = DenyBroker()
    broker.required_plugins = ("exo-gate",)
    monkeypatch.setattr("exo.compose.load_executor", lambda: (broker, True))
    monkeypatch.setattr(PluginManager, "load_all", lambda *args, **kw: [])
    with pytest.raises(ExecutorConfigurationError, match="required executor plugins failed"):
        create()


def test_required_factory_needs_gate_even_when_broker_omits_it(monkeypatch):
    broker = DenyBroker()  # required_plugins = (): the gate is still compulsory
    monkeypatch.setattr("exo.compose.load_executor", lambda: (broker, True))
    monkeypatch.setattr(PluginManager, "load_all", lambda *args, **kw: [])
    with pytest.raises(ExecutorConfigurationError, match="required executor plugins failed to load: exo-gate"):
        create()


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_plugin_approval_precedes_executor_and_post_hook(tmp_path, monkeypatch, decision):
    seen = []

    class Recording(DenyBroker):
        async def execute(self, request, tool, run_trusted):
            seen.append("executor")
            return await super().execute(request, tool, run_trusted)

    plugins = PluginManager()

    def register(ctx):
        ctx.register_hook("pre_tool_call", lambda **kw: seen.append("pre") or {"action": "approve"})
        ctx.register_hook("post_tool_call", lambda **kw: seen.append("post"))

    plugins._run_register(Manifest(name="test"), "dir", register)
    engine = CoreEngine(tmp_path / "home", plugins=plugins, executor=Recording(), executor_required=True)

    async def ask(*args, **kw):
        seen.append("approval")
        await asyncio.sleep(0)
        return decision

    monkeypatch.setattr(engine.approvals, "ask", ask)

    async def main():
        try:
            result = await engine.run_tool(ToolCall("c", "bash", {"command": "echo approved"}), {})
            assert result.is_error
            assert seen == (["pre", "approval", "executor", "post"] if decision == "allow"
                            else ["pre", "approval", "post"])
        finally:
            await engine.close()

    asyncio.run(main())


def test_secure_config_cannot_disable_required_plugins(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text('{"core_plugins": []}')
    monkeypatch.setenv("EXO_HOME", str(home))
    broker = DenyBroker()
    broker.required_plugins = ("exo-gate",)
    monkeypatch.setattr("exo.compose.load_executor", lambda: (broker, True))
    monkeypatch.setattr(PluginManager, "load_all", lambda *args, **kw: pytest.fail("disabled gate imported"))
    with pytest.raises(ExecutorConfigurationError, match="disables a required"):
        create()


def test_scheduler_tools_use_broker(tmp_path):
    models = {"fake": {"rules": [{"match": "probe", "steps": [
        {"tool": "bash", "args": {"command": "echo scheduled"}}, "done"]}]},
              "roles": {"main": ["fake:x"], "aux": ["fake:x"], "fast": ["fake:x"]}}
    broker = DenyBroker()
    engine = CoreEngine(tmp_path / "home", models, executor=broker, executor_required=True,
                        attach_scheduler=Scheduler)
    job = engine.scheduler.add("every 1m", "probe", tier="fast")

    async def main():
        try:
            await engine.scheduler.run(job, "task")
            assert len(broker.seen) == 1
            assert broker.seen[0].arguments["command"] == "echo scheduled"
            assert engine.store.get_session(broker.seen[0].session).kind == "scheduled"
        finally:
            await engine.close()

    asyncio.run(main())


def test_required_local_delegate_setup_has_no_workspace_execution(tmp_path, monkeypatch):
    seen = []

    class LocalDelegate(DenyBroker):
        async def execute(self, request, tool, run_trusted):
            seen.append(request.tool_name)
            if request.tool_name == "delegate":
                return await run_trusted()
            return await super().execute(request, tool, run_trusted)

    models = {"fake": {"rules": [{"match": "probe", "steps": [
        {"tool": "bash", "args": {"command": "echo child"}}, "done"]}]},
              "roles": {"main": ["fake:x"], "aux": ["fake:x"], "standard": ["fake:x"]}}
    engine = CoreEngine(tmp_path / "home", models, executor=LocalDelegate(), executor_required=True,
                        cwd=str(tmp_path))
    monkeypatch.setattr("exo.core.workspace.git_facts", lambda *a: pytest.fail("delegate host workspace Git"))
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("delegate setup host subprocess"))

    async def main():
        try:
            result = await engine.run_tool(ToolCall("parent", "delegate", {
                "goal": "probe __import__('os').system('touch /outside-marker')",
                "context": '{"constructor": "eval", "code": "untrusted payload"}',
                "tools": ["bash"]}), {})
            assert not result.is_error
            assert seen == ["delegate", "bash"]
            assert "done" in result.text()
        finally:
            await engine.close()

    asyncio.run(main())


def test_required_remote_delegate_refuses_host_bus_connection(tmp_path, monkeypatch):
    class LocalDelegate(DenyBroker):
        async def execute(self, request, tool, run_trusted):
            return await run_trusted()

    engine = CoreEngine(tmp_path / "home", executor=LocalDelegate(), executor_required=True)
    monkeypatch.setattr(engine.agents, "_remote", lambda *a: pytest.fail("host bus connection"))

    async def main():
        try:
            result = await engine.run_tool(ToolCall("parent", "delegate", {"goal": "probe", "seat": "other"}), {})
            assert result.is_error and "does not support remote delegation" in result.text()
            assert engine.agents.list() == []
        finally:
            await engine.close()

    asyncio.run(main())
