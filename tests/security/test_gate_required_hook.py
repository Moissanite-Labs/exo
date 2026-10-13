"""A missing or failing approval callback cannot execute a core tool."""
import asyncio

import pytest

from exo.core.engine import CoreEngine
from exo.core.hooks import HookRegistry
from exo.core.plugins import Manifest, PluginManager
from exo.core.tools.registry import Registry
from exo.core.types import ToolCall, ToolSpec


def engine(tmp_path, plugins=None):
    ran = []
    registry = Registry()
    registry.register(ToolSpec(name="danger", description="sentinel", risk="high"),
                      lambda args, ctx: ran.append(True) or "executed")
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     registry=registry, plugins=plugins, cwd=str(tmp_path), env={})
    return eng, ran


@pytest.mark.parametrize("gate", ["missing", "no-hook", "broken-registration"])
def test_missing_gate_blocks_tool_with_repair_guidance(tmp_path, gate):
    pm = PluginManager()
    if gate == "no-hook":
        pm._run_register(Manifest(name="exo-gate"), "dir", lambda ctx: None)
    elif gate == "broken-registration":
        def broken(ctx):
            ctx.register_hook("pre_tool_call", lambda **kw: None)
            raise RuntimeError("bad gate config")
        rec = pm._run_register(Manifest(name="exo-gate"), "dir", broken)
        assert not rec.ok
    eng, ran = engine(tmp_path, pm)
    result = asyncio.run(eng.registry.call(ToolCall(id="c", name="danger", arguments={})))
    assert result.is_error and "Reinstall" in result.text() and "enable exo-gate" in result.text()
    assert not ran


@pytest.mark.parametrize("async_callback", [False, True])
def test_failing_gate_callback_blocks_tool(tmp_path, async_callback):
    def fail(**kw):
        raise RuntimeError("callback failed")

    async def afail(**kw):
        fail(**kw)

    pm = PluginManager()
    pm.hooks.register("pre_tool_call", afail if async_callback else fail, "exo-gate")
    eng, ran = engine(tmp_path, pm)
    result = asyncio.run(eng.registry.call(ToolCall(id="c", name="danger", arguments={})))
    assert result.is_error and "approval hook failed" in result.text() and "exo-gate" in result.text()
    assert not ran


def test_explicit_test_gate_can_allow_synthetic_tool(tmp_path):
    pm = PluginManager()
    pm.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    eng, ran = engine(tmp_path, pm)
    result = asyncio.run(eng.registry.call(ToolCall(id="c", name="danger", arguments={})))
    assert not result.is_error and result.text() == "executed" and ran == [True]


@pytest.mark.parametrize("async_dispatch", [False, True])
def test_hook_registry_only_fails_closed_for_tool_approval(async_dispatch):
    hooks = HookRegistry()

    def fail(**kw):
        raise RuntimeError("callback failed")

    hooks.register("pre_tool_call", fail, "exo-gate")
    hooks.register("post_tool_call", fail, "other")
    dispatch = (lambda name: asyncio.run(hooks.afire(name))) if async_dispatch else hooks.fire
    assert dispatch("pre_tool_call")[0]["action"] == "block"
    assert dispatch("post_tool_call") == []


def test_sync_dispatch_of_async_gate_fails_closed():
    hooks = HookRegistry()

    async def allow(**kw):
        return None

    hooks.register("pre_tool_call", allow, "exo-gate")
    assert hooks.fire("pre_tool_call")[0]["action"] == "block"
