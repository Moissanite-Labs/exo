"""The runtime accepts a context view without importing application composition."""
import ast
import asyncio
import inspect
import subprocess
import sys
from pathlib import Path
from typing import get_type_hints

import pytest

from exo.application import CoreEngine
from exo.core.context import ContextTree, TurnContext
from exo.core.engine import CoreRuntime, HubBus
from exo.core.tools.executor import ExecutorConfigurationError
from exo.core.types import Message, ToolCall, ToolResult
from exo.plugin_context import ContextAdapter

REPO = Path(__file__).resolve().parents[2]
MODELS = {"fake": {"reply": "offline"}, "roles": {"main": ["fake:scripted"], "aux": ["fake:scripted"]}}


def test_plain_runtime_import_and_turn_do_not_load_composition(tmp_path):
    script = '''
import asyncio, importlib.abc, sys
class NoComposition(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        assert fullname not in ("exo.application", "exo.plugin_context"), fullname
sys.meta_path.insert(0, NoComposition())
from exo.core.engine import CoreRuntime
async def run():
    engine = CoreRuntime(sys.argv[1], {"fake": {"reply": "offline"},
        "roles": {"main": ["fake:scripted"], "aux": ["fake:scripted"]}}, env={})
    try:
        assert engine.loop.context is engine.context_tree
        session = await engine.loop.create_session(agent=engine.agent)
        await engine.loop.send(session.id, "hello")
        assert engine.store.messages(session.id)[-1].text() == "offline"
        assert "Next call for thread" in await engine.context_text(session.id)
    finally:
        await engine.close()
asyncio.run(run())
assert "exo.application" not in sys.modules and "exo.plugin_context" not in sys.modules
'''
    proc = subprocess.run([sys.executable, "-c", script, str(tmp_path / "runtime")],
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_import_direction_has_only_named_compatibility_exceptions():
    """Core never constructs the plugin bridge; legacy named imports may select it."""
    for path in (REPO / "src/exo/core").rglob("*.py"):
        module = ast.parse(path.read_text())
        parents = {child: node for node in ast.walk(module) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(module):
            names = ([node.module] if isinstance(node, ast.ImportFrom) else
                     [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            if not any(name in ("exo.application", "exo.plugin_context") for name in names):
                continue
            owner = parents[node]
            while not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                owner = parents[owner]
            assert path.name == "engine.py" and getattr(owner, "name", None) in ("create", "__getattr__")
    bridge = ast.parse((REPO / "src/exo/plugin_context.py").read_text())
    assert not any(isinstance(node, ast.ImportFrom) and node.module in ("exo.application", "exo.core.engine")
                   for node in ast.walk(bridge))


def test_legacy_exports_signatures_and_type_hints_remain_resolvable():
    from exo.core import engine
    from exo import application

    assert engine.CoreEngine is CoreEngine and engine.ContextAdapter is ContextAdapter
    assert issubclass(CoreEngine, CoreRuntime)
    assert inspect.signature(CoreEngine) == inspect.signature(CoreRuntime)
    assert get_type_hints(CoreEngine.__init__) == get_type_hints(CoreRuntime.__init__)
    assert get_type_hints(HubBus.__init__)["engine"] is CoreRuntime
    assert get_type_hints(application.create)["return"] is CoreEngine
    assert get_type_hints(engine.create)["return"] is CoreRuntime
    assert get_type_hints(ContextTree)["on_capture"]
    assert get_type_hints(TurnContext.build)["return"] == list[Message]
    old_keywords = {"home", "models", "registry", "plugins", "executor", "executor_required", "cwd",
                    "system_prompt", "env", "delegation", "seat", "skill_folders", "mcp", "channels",
                    "tree_cfg", "schedule", "trace_cfg", "peers", "jobs", "peers_dir", "loop_kw"}
    assert set(inspect.signature(CoreEngine).parameters) == old_keywords | {
        "context_factory", "attach_peers", "attach_scheduler", "attach_jobs", "attach_agents", "skills_enabled"}
    exports = {}
    exec("from exo.core.engine import *", exports)
    assert exports["CoreEngine"] is CoreEngine and exports["ContextAdapter"] is ContextAdapter


@pytest.mark.parametrize("engine_type", [CoreRuntime, CoreEngine])
def test_injected_context_uses_core_owned_tree_without_bridge(engine_type, tmp_path):
    received, calls = [], []

    def factory(tree: ContextTree) -> TurnContext:
        received.append(tree)

        class View:
            on_event = None

            def note(self, session):
                calls.append(("note", session))
                tree.note(session)

            async def build(self, session, system):
                calls.append(("build", session))
                return await tree.build(session, system)

        return View()  # deliberately has no .tree/.store; diagnostics use runtime ownership

    async def run():
        engine = engine_type(tmp_path, MODELS, context_factory=factory, env={})
        try:
            assert received == [engine.context_tree]
            assert engine.context_tree.on_capture is None
            assert not isinstance(engine.loop.context, ContextAdapter)
            session = await engine.loop.create_session(agent=engine.agent)
            await engine.loop.send(session.id, "hello")
            assert calls == [("note", session.id), ("build", session.id)]
            assert engine.store.messages(session.id)[-1].text() == "offline"
            assert "Next call for thread" in await engine.context_text(session.id)
        finally:
            await engine.close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_bridge_needs_only_tree_protocol_and_retains_original_messages():
    messages = [Message.user("hello"), Message.assistant("", [ToolCall("id", "bash", {"command": "ls"})]),
                Message(role="tool", tool_results=[ToolResult.of_text("id", "file")])]
    messages[0].meta["author"] = "principal"

    class Tree:
        on_event = on_capture = on_pre_compress = on_compacted = None

        def note(self, session):
            pass

        async def build(self, session, system):
            return messages

        def transcript(self, session, last):
            return messages[-last:]

    class Plugin:
        def select_context(self, flat):
            return [{"role": "system", "content": "extra"}, *flat]

    tree = Tree()
    adapter = ContextAdapter(tree, Plugin())
    result = asyncio.run(adapter.build("s", "system"))
    assert result[0].text() == "extra"
    assert all(actual is original for actual, original in zip(result[1:], messages))
    assert adapter.transcript("s", 1)[0]["role"] == "tool"
    assert adapter.cache_key("s") == "s"
    assert tree.on_capture is None and callable(tree.on_pre_compress) and callable(tree.on_compacted)


def test_undo_tells_the_context_plugin_what_the_thread_still_holds(tmp_path):
    """D-041: after session.undo the context plugin gets ``on_session_switch(session, rewound=True, messages=,
    summaries=)`` with every message left in the thread, each with its author (D-040), and the ids of the summary
    nodes still stored for the thread's tree, so it can drop what it kept from the undone turns. A bare tree (no
    bridge) has no such hook and undo still works."""
    from exo.protocol import schema as s

    seen = []

    class Plugin:
        def on_session_switch(self, new_session_id, *, rewound=False, messages=None, summaries=None, **kw):
            seen.append((new_session_id, rewound, messages, kw.get("session_id"), summaries))

    async def run(factory):
        engine = CoreRuntime(tmp_path / str(len(seen)), MODELS, context_factory=factory, env={})
        try:
            session = await engine.loop.create_session(agent=engine.agent)
            await engine.loop.send(session.id, "keep this", meta={"author": "principal"})
            await engine.loop.send(session.id, "undo this", meta={"author": "principal"})
            out = await engine._session_undo(s.SessionUndo(session=session.id, turns=1))
            assert out["removed"] == 2
            return session.id, engine.agent
        finally:
            await engine.close()

    sid, agent = asyncio.run(asyncio.wait_for(run(lambda tree: ContextAdapter(tree, Plugin())), 5))
    assert [(x[0], x[1], x[3]) for x in seen] == [(sid, True, sid)]
    assert seen[0][4] == {f"agent:{agent}": []}  # the agent's tree, no summary yet
    assert [(m["role"], m["content"], m["author"]) for m in seen[0][2]] == [
        ("user", "keep this", "principal"), ("assistant", "offline", "agent")]
    assert seen[0][2][0]["principal"] is True
    asyncio.run(asyncio.wait_for(run(None), 5))  # no bridge: nothing to tell
    assert len(seen) == 1


def test_undo_in_a_private_thread_tells_the_context_plugin_nothing(tmp_path):
    """D-028: a private thread is never shown to the context plugin, so an undo there hands it no messages."""
    from exo.protocol import schema as s

    seen = []

    class Plugin:
        def on_session_switch(self, new_session_id, *, rewound=False, messages=None, **kw):
            seen.append((new_session_id, rewound, messages))

    class PrivateAdapter(ContextAdapter):
        def _private(self, session):
            return True

    async def run():
        engine = CoreRuntime(tmp_path / "p", MODELS, context_factory=lambda tree: PrivateAdapter(tree, Plugin()), env={})
        try:
            session = await engine.loop.create_session(agent=engine.agent)
            await engine.loop.send(session.id, "my private note", meta={"author": "principal"})
            await engine.loop.send(session.id, "undo this", meta={"author": "principal"})
            await engine._session_undo(s.SessionUndo(session=session.id, turns=1))
        finally:
            await engine.close()

    asyncio.run(asyncio.wait_for(run(), 5))
    assert [x for x in seen if x[1]] == []


@pytest.mark.parametrize("engine_type", [CoreRuntime, CoreEngine])
def test_injected_factory_cannot_precede_required_gate_validation(engine_type, tmp_path):
    class Broker:
        async def execute(self, request, tool, run_trusted):
            pytest.fail("no tool can run during invalid startup")

    with pytest.raises(ExecutorConfigurationError, match="trusted folder exo-gate"):
        engine_type(tmp_path / "unopened", executor=Broker(), executor_required=True,
                    context_factory=lambda tree: pytest.fail("factory ran before required gate validation"))
    assert not (tmp_path / "unopened").exists()
