import asyncio
import textwrap
from pathlib import Path

import pytest

from exo.core.hooks import HookError, HookRegistry
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


def test_existing_exo_plugins_load():
    """The six Exo plugins' register(ctx) runs on this ctx unchanged (wave 2 is import changes)."""
    pm = PluginManager()
    recs = {r.manifest.name: r for r in pm.load_all(dirs=[REPO / "plugins"], entry_points=False)}
    assert set(recs) == {"exo-bus", "exo-command", "exo-context", "exo-gate", "exo-memory", "exo-orchestra"}
    assert all(r.ok for r in recs.values()), {n: r.error for n, r in recs.items() if r.error}
    assert pm.slots.memory.name == "exo-memory" and pm.slots.context.name == "exo-context"
    assert {"bus_post", "bus_inbox"} <= set(pm.tools)
    assert {"orchestra", "exo-command"} <= set(pm.commands)
    assert pm.hooks.has("pre_tool_call") and pm.hooks.has("post_approval_response")
