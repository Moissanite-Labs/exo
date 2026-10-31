import asyncio
import ast
import textwrap
from pathlib import Path

import pytest

from exo.core.hooks import KNOWN_UNFIRED_HOOKS, HookError, HookRegistry, VALID_HOOKS
from exo.core.plugins import PluginManager, discover
from exo.core.slots import ContextEngine, MemoryProvider, SlotError, Slots, spawn_context_thread

REPO = Path(__file__).resolve().parents[3]

SAMPLE_INIT = '''
from .impl import Mem, handler, on_pre

def register(ctx):
    ctx.register_tool(name="sample_echo", toolset="sample", handler=handler,
                      schema={"name": "sample_echo", "description": "Echo.",
                              "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}},
                      check_fn=lambda: True, risk="medium")
    ctx.register_hook("pre_tool_call", on_pre)
    ctx.register_command("/Sample Cmd", lambda raw="": "ran " + raw, description="d", args_hint="<x>")
    ctx.register_memory_provider(Mem(NAME))
    ctx.register_channel("loopback", lambda **kw: "chan", description="test channel")
    ctx.register_skill("hello", SKILL, "says hi", {"name": "hello"})
    ctx.registered_greeting = ctx.get_config("greeting", "none")
'''

SAMPLE_IMPL = '''
from exo.core.slots import MemoryProvider

class Mem(MemoryProvider):
    def __init__(self, n):
        self._n = n
    @property
    def name(self):
        return self._n
    def is_available(self):
        return True
    def initialize(self, session_id, **kw):
        pass
    def get_tool_schemas(self):
        return []

def handler(args, **kw):
    return args.get("x", "")

def on_pre(tool_name="", args=None, **kw):
    if tool_name == "danger":
        return {"action": "block", "message": "no"}
'''


def make_plugin(root: Path, name: str, mem: str | None = None, extra_yaml: str = "", body: str | None = None):
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(f'name: {name}\nversion: "1.0"\ndescription: >\n  A test plugin.\n{extra_yaml}')
    (d / "SKILL.md").write_text("---\nname: hello\n---\nhi\n")
    init = body if body is not None else SAMPLE_INIT.replace("NAME", repr(mem or name)).replace(
        "SKILL", repr(str(d / "SKILL.md")))
    (d / "__init__.py").write_text(textwrap.dedent(init))
    (d / "impl.py").write_text(SAMPLE_IMPL)
    return d


def test_load_sample_plugin(tmp_path):
    make_plugin(tmp_path, "sample-one", extra_yaml="config_schema:\n  greeting:\n    type: str\n    default: hi\n")
    pm = PluginManager()
    [rec] = pm.load_all(dirs=[tmp_path], entry_points=False)
    assert rec.ok, rec.error
    assert rec.manifest.description == "A test plugin."
    assert ("tool", "sample_echo") in rec.registered
    t = pm.tools["sample_echo"]
    assert t.spec.toolset == "sample" and t.spec.risk == "medium"
    assert t.spec.parameters["properties"]["x"]["type"] == "string"
    assert t.handler({"x": "a"}) == "a"
    assert [s.name for s in pm.tool_specs()] == ["sample_echo"]
    assert pm.commands["sample-cmd"].handler("z") == "ran z"
    assert pm.commands["sample-cmd"].args_hint == "<x>"
    assert pm.slots.memory.name == "sample-one" and pm.slots.owners["memory"] == "sample-one"
    assert pm.channels["loopback"].factory() == "chan"
    assert pm.skills["sample-one:hello"].frontmatter == {"name": "hello"}
    assert pm.hooks.first("pre_tool_call", tool_name="danger", args={})["action"] == "block"
    assert pm.hooks.fire("pre_tool_call", tool_name="safe", args={}) == []


def test_get_config_override_and_default(tmp_path):
    body = '''
def register(ctx):
    ctx.register_command("cfg", lambda raw="": str((ctx.get_config("greeting"), ctx.get_config("other", 7))))
'''
    make_plugin(tmp_path, "cfg", body=body, extra_yaml="config_schema:\n  greeting:\n    type: str\n    default: hi\n")
    pm = PluginManager()
    pm.load_all(dirs=[tmp_path], entry_points=False)
    assert pm.commands["cfg"].handler() == "('hi', 7)"
    pm2 = PluginManager(config={"cfg": {"greeting": "yo"}})
    pm2.load_all(dirs=[tmp_path], entry_points=False)
    assert pm2.commands["cfg"].handler() == "('yo', 7)"


def test_memory_slot_exclusive_and_rolls_back(tmp_path):
    make_plugin(tmp_path, "a-first")
    body = SAMPLE_INIT.replace("NAME", "'b-second'").replace("sample_echo", "b_echo").replace(
        "/Sample Cmd", "bcmd").replace("loopback", "bchan").replace("SKILL", repr(str(tmp_path / "b-second/SKILL.md")))
    make_plugin(tmp_path, "b-second", body=body)
    pm = PluginManager()
    a, b = pm.load_all(dirs=[tmp_path], entry_points=False)
    assert a.ok
    assert not b.ok and "SlotError" in b.error and "a-first" in b.error
    assert "b_echo" not in pm.tools and "bcmd" not in pm.commands and "bchan" not in pm.channels
    assert len(pm.hooks.entries("pre_tool_call")) == 1
    assert pm.slots.memory.name == "a-first"
    assert pm.errors() == {"b-second": b.error}


def test_memory_choice_selects_without_error(tmp_path):
    make_plugin(tmp_path, "a-first")
    body = SAMPLE_INIT.replace("NAME", "'b-second'").replace("sample_echo", "b_echo").replace(
        "/Sample Cmd", "bcmd").replace("loopback", "bchan").replace("SKILL", repr(str(tmp_path / "b-second/SKILL.md")))
    make_plugin(tmp_path, "b-second", body=body)
    pm = PluginManager(memory_choice="b-second")
    recs = pm.load_all(dirs=[tmp_path], entry_points=False)
    assert all(r.ok for r in recs)
    assert pm.slots.memory.name == "b-second"
    assert set(pm.slots.candidates["memory"]) == {"a-first", "b-second"}


class Eng(ContextEngine):
    def __init__(self, n):
        self._n = n

    @property
    def name(self):
        return self._n

    def update_from_response(self, usage):
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)

    def should_compress(self, prompt_tokens=None):
        return (prompt_tokens or self.last_prompt_tokens) > self.threshold_tokens

    def compress(self, messages, current_tokens=None, focus_topic=None, force=False, memory_context=""):
        return messages[-1:]


def test_context_slot_exclusive_and_shape():
    s = Slots()
    assert s.fill("context", Eng("one"), "p1")
    with pytest.raises(SlotError, match="already held by 'one'"):
        s.fill("context", Eng("two"), "p2")
    with pytest.raises(SlotError, match="lacks"):
        s.fill("memory", object(), "p3")
    with pytest.raises(SlotError):
        s.fill("nope", Eng("x"))
    e = s.context
    e.model_thresholds = {"small": 0.5}
    e.update_model("vendor/small-model", 1000)
    assert e.threshold_tokens == 500
    e.update_model("big", 1000)
    assert e.threshold_tokens == 750
    e.update_from_response({"prompt_tokens": 800})
    assert e.should_compress() and e.get_status()["usage_percent"] == 80
    e.on_session_reset()
    assert e.last_prompt_tokens == 0
    s.release("context", e)
    assert s.context is None



def test_context_engine_sees_the_whole_prompt_after_a_turn():
    """D-080: a context engine decides when to compress from OpenAI-style prompt_tokens, which count the cached
    tokens. Usage keeps cache reads and writes out of input_tokens, so the adapter adds them back."""
    from types import SimpleNamespace

    from exo.core.types import Usage
    from exo.plugin_context import ContextAdapter

    class Seen(Eng):
        def on_turn_complete(self, messages, usage=None, **kw):
            self.turn_usage = usage

    e = Seen("ctx")
    e.update_model("m", 100_000)  # compress above 75,000 prompt tokens
    adapter = ContextAdapter(SimpleNamespace(transcript=lambda session, last: []), e)
    adapter.on_turn_complete("s", Usage(input_tokens=12, cache_read_tokens=80_000, cache_write_tokens=12_000,
                                        output_tokens=5))
    assert e.last_prompt_tokens == 92_012 and e.should_compress()
    assert e.turn_usage == {"prompt_tokens": 92_012, "completion_tokens": 5, "total_tokens": 92_017}


def test_context_engine_gets_the_last_prompt_after_a_tool_using_turn(tmp_path):
    """Three calls of a 30,012-token prompt (two tool rounds, then a reply) hand the engine one prompt, 30,012
    tokens, not the turn's sum (90,036), so a 75,000 threshold in a 100,000 window does not fire."""
    from exo.core.context import TreeContext
    from exo.core.loop import Loop
    from exo.core.sessions import SessionStore
    from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult, ToolSpec, Usage
    from exo.plugin_context import ContextAdapter

    class Seen(Eng):
        def on_turn_complete(self, messages, usage=None, **kw):
            self.turn_usage = usage

    call = Usage(input_tokens=12, cache_read_tokens=28_000, cache_write_tokens=2_000, output_tokens=50)

    class TwoRounds:
        name = "fake"
        n = 0

        async def stream(self, messages, tools, **opts):
            self.n += 1
            calls = [ToolCall(id=f"c{self.n}", name="echo", arguments={})] if self.n < 3 else []
            for c in calls:
                yield ProviderEvent(kind="tool_call", tool_call=c)
            yield ProviderEvent(kind="usage", usage=call)
            yield ProviderEvent(kind="done", stop_reason="tool_use" if calls else "end",
                                message=Message.assistant("" if calls else "done", tool_calls=calls))

    async def echo(c, ctx):
        return ToolResult.of_text(c.id, "ok")

    e = Seen("ctx")
    e.update_model("m", 100_000)  # compress above 75,000 prompt tokens
    store = SessionStore(tmp_path / "s.sqlite")
    loop = Loop(store, TwoRounds(), context=ContextAdapter(TreeContext(store, None), e), retry_delay=0,
                tool_specs=[ToolSpec(name="echo", description="echo", risk="low")], run_tool=echo)

    async def go():
        s = await loop.create_session(title="t")
        return await loop.send(s.id, "hi")

    res = asyncio.run(go())
    assert res.stop_reason == "end" and res.usage.cache_read_tokens == 84_000  # the turn's sum is still the sum
    assert e.last_prompt_tokens == 30_012 and not e.should_compress()
    assert e.turn_usage == {"prompt_tokens": 30_012, "completion_tokens": 50, "total_tokens": 30_062}


def test_memory_provider_defaults_and_thread():
    class M(MemoryProvider):
        name = "m"

        def is_available(self):
            return True

        def initialize(self, session_id, **kw):
            pass

        def get_tool_schemas(self):
            return []

    m = M()
    assert m.prefetch("q") == "" and m.recall_status() is None
    with pytest.raises(NotImplementedError):
        m.handle_tool_call("x", {})
    box = []
    t = spawn_context_thread(lambda: box.append(1), name="t")
    t.start()
    t.join()
    assert box == [1]


def test_disabled_and_broken_plugins(tmp_path):
    make_plugin(tmp_path, "good")
    make_plugin(tmp_path, "broken", body="raise RuntimeError('boom')\n")
    make_plugin(tmp_path, "skipped", body="def register(ctx):\n    raise AssertionError\n")
    pm = PluginManager()
    recs = {r.manifest.name: r for r in pm.load_all(dirs=[tmp_path], disabled=["skipped"], entry_points=False)}
    assert set(recs) == {"good", "broken"}
    assert recs["good"].ok and "boom" in recs["broken"].error
    assert [m.name for m in discover([tmp_path])] == ["broken", "good", "skipped"]


def test_hooks_registry():
    h = HookRegistry()
    with pytest.raises(HookError):
        h.register("not_a_hook", lambda **k: None)
    h.register("post_tool_call", lambda **k: 1 / 0, "bad")
    h.register("post_tool_call", lambda **k: k["x"] + 1, "good")

    async def later(**k):
        return "async"
    e = h.register("post_tool_call", later, "async")
    assert h.fire("post_tool_call", x=1) == [2]
    assert asyncio.run(h.afire("post_tool_call", x=1)) == [2, "async"]
    h.unregister(e)
    assert len(h.entries("post_tool_call")) == 2 and h.names() == ["post_tool_call"]


@pytest.mark.parametrize("name", sorted(KNOWN_UNFIRED_HOOKS))
def test_known_unfired_hooks_are_rejected_by_default(name):
    with pytest.raises(HookError, match="known but is not fired by the Exo core"):
        HookRegistry().register(name, lambda **kw: None)


def test_custom_hook_registry_can_supply_its_own_fire_site():
    h = HookRegistry(valid={"pre_llm_call"})
    h.register("pre_llm_call", lambda **kw: kw["text"])
    assert h.fire("pre_llm_call", text="custom") == ["custom"]


def test_default_supported_hooks_match_runtime_fire_sites():
    trees = [ast.parse((REPO / path).read_text(encoding="utf-8"))
             for path in ("src/exo/core/engine.py", "src/exo/core/plugin_lifecycle.py")]
    fired = {
        call.args[0].value
        for tree in trees for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in {"fire", "afire"}
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }
    assert VALID_HOOKS == fired
    assert not VALID_HOOKS & KNOWN_UNFIRED_HOOKS


def test_plugin_context_rejects_a_known_unfired_hook_and_rolls_back(tmp_path):
    body = '''
def register(ctx):
    ctx.register_command("before-hook", lambda: "registered")
    ctx.register_hook("pre_llm_call", lambda **kw: None)
'''
    make_plugin(tmp_path, "bad-hook", body=body)

    pm = PluginManager()
    [rec] = pm.load_all(dirs=[tmp_path], entry_points=False)
    assert not rec.ok and "known but is not fired by the Exo core" in rec.error
    assert "before-hook" not in pm.commands


def test_existing_exo_plugins_load_with_bus_peers_disabled(monkeypatch):
    """The Exo plugins load with optional Hermes-only peer hooks disabled."""
    monkeypatch.delenv("EXO_PEERS", raising=False)
    pm = PluginManager()
    recs = {r.manifest.name: r for r in pm.load_all(dirs=[REPO / "plugins"], entry_points=False)}
    assert set(recs) == {"exo-bus", "exo-command", "exo-context", "exo-gate", "exo-memory", "exo-orchestra"}
    assert all(r.ok for r in recs.values()), {n: r.error for n, r in recs.items() if r.error}
    assert pm.slots.memory.name == "exo-memory" and pm.slots.context.name == "exo-context"
    assert {"bus_post", "bus_inbox"} <= set(pm.tools)
    assert {"orchestra", "exo-command"} <= set(pm.commands)
    assert not any(kind == "hook" for kind, _ in recs["exo-bus"].registered)
    assert pm.hooks.has("pre_tool_call") and pm.hooks.has("post_approval_response")
