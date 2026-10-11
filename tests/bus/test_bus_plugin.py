"""exo-bus Hermes plugin: tools against a live relay; platform adapter against a Hermes checkout."""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from .conftest import write_keys

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "exo-bus"


def load_plugin():
    spec = importlib.util.spec_from_file_location("exo_bus_plugin", PLUGIN / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exo_bus_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, handler, description, schema, emoji="", check_fn=None):
        assert schema["name"] == name and schema["parameters"]["type"] == "object"
        self.tools[name] = (handler, check_fn, toolset)


@pytest.fixture
def seat_env(bus, tmp_path, monkeypatch):
    keys = tmp_path / "seat-keys"
    write_keys(keys, {"beta": bus.keys["beta"]})
    for k, v in {"EXO_BUS_URL": bus.url, "EXO_BUS_SEAT": "beta", "EXO_BUS_KEYS_DIR": str(keys),
                 "EXO_BUS_STATE_DIR": str(tmp_path / "state")}.items():
        monkeypatch.setenv(k, v)
    return tmp_path


def test_manifest_lists_the_registered_tools():
    import re
    manifest = (PLUGIN / "plugin.yaml").read_text()
    declared = re.findall(r"^\s+- (bus_\w+)$", manifest, re.M)
    ctx = Ctx()
    load_plugin().register(ctx)  # no register_platform on this ctx: tools only
    assert sorted(ctx.tools) == sorted(declared) == ["bus_inbox", "bus_post", "bus_thread", "bus_verify"]
    assert {t[2] for t in ctx.tools.values()} == {"exo-bus"}


def test_tools_hidden_until_configured(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("EXO_BUS_", "DIVAN_")):
            monkeypatch.delenv(k)
    ctx = Ctx()
    load_plugin().register(ctx)
    assert ctx.tools["bus_post"][1]() is False
    assert json.loads(ctx.tools["bus_post"][0]({"to": ["group"], "text": "x"}))["ok"] is False


def test_tools_post_read_thread_verify(bus, seat_env):
    ctx = Ctx()
    load_plugin().register(ctx)
    call = {name: t[0] for name, t in ctx.tools.items()}
    assert ctx.tools["bus_inbox"][1]() is True
    bus.post("gamma", "team:core", "hello beta", thread="t9")
    r = json.loads(call["bus_post"]({"to": ["gamma"], "text": "hi gamma", "thread": "t9"}))
    assert r["ok"] and r["seq"] == 2
    first = json.loads(call["bus_inbox"]({}))
    assert [m["text"] for m in first["messages"]] == ["hello beta"]
    assert json.loads(call["bus_inbox"]({}))["messages"] == []  # cursor advanced
    assert len(json.loads(call["bus_inbox"]({"since": 0}))["messages"]) == 1
    assert [m["text"] for m in json.loads(call["bus_thread"]({"thread": "t9"}))["messages"]] == ["hello beta", "hi gamma"]
    assert json.loads(call["bus_verify"]({}))["checked"] == 2
    assert json.loads(call["bus_post"]({"to": "nobody", "text": "x"}))["ok"] is False


def test_tools_sym2p_post_renders_for_readers(bus, seat_env):
    sym2p = pytest.importorskip("exo.protocols.sym2p")
    ctx = Ctx()
    load_plugin().register(ctx)
    line = sym2p.encode_brief(id="b441", tk="mr1", cf=88, cv=70, scope=("sym-2p/1.0", 3, 5), rq="ack")
    assert json.loads(ctx.tools["bus_post"][0]({"to": ["gamma"], "text": line, "sym2p": True}))["ok"]
    assert json.loads(ctx.tools["bus_post"][0]({"to": ["gamma"], "text": "not a packet", "sym2p": True}))["ok"] is False
    env = bus.client("gamma").inbox()[0]
    assert "Scope:" in bus.client("gamma").text(env)


def hermes_python():
    d = Path(os.path.expanduser(os.environ.get("EXO_HERMES_DIR", "~/.hermes/hermes-agent")))
    py = d / "venv" / "bin" / "python"
    return (d, py) if (d / "gateway" / "platforms" / "base.py").exists() and py.exists() else (None, None)


ADAPTER_SCRIPT = textwrap.dedent("""
    import asyncio, json, sys
    sys.path[:0] = [sys.argv[1], sys.argv[2], sys.argv[3]]
    from gateway.config import PlatformConfig
    import importlib.util
    spec = importlib.util.spec_from_file_location("exo_bus_plugin", sys.argv[3] + "/__init__.py",
                                                  submodule_search_locations=[sys.argv[3]])
    mod = importlib.util.module_from_spec(spec); sys.modules["exo_bus_plugin"] = mod; spec.loader.exec_module(mod)
    from exo_bus_plugin.exo_bus_hermes.adapter import ExoBusAdapter, register_platform
    extra = json.loads(sys.argv[4])
    class Ctx:
        tools, platforms = {}, {}
        def register_tool(self, name, **kw): self.tools[name] = kw
        def register_platform(self, name, label, adapter_factory, check_fn, validate_config=None,
                              required_env=None, install_hint="", **kw):
            # what PluginContext.register_platform does, minus the plugin manager bookkeeping
            from gateway.platform_registry import PlatformEntry, platform_registry
            entry = PlatformEntry(name=name, label=label, adapter_factory=adapter_factory, check_fn=check_fn,
                                  validate_config=validate_config, required_env=required_env or [],
                                  install_hint=install_hint, source="plugin", plugin_name="exo-bus", **kw)
            platform_registry.register(entry)
            self.platforms[name] = entry
    ctx = Ctx(); mod.register(ctx)
    assert ctx.platforms["exo_bus"].check_fn()
    a = ExoBusAdapter(PlatformConfig(enabled=True, extra=extra))
    got = []
    async def capture(event): got.append((event.text, event.source.chat_id, event.source.chat_type,
                                           event.source.user_id, event.source.thread_id))
    a.handle_message = capture
    async def main():
        assert await a.connect()
        await a.disconnect()
        a._running = True
        n = await a.poll_once()
        again = await a.poll_once()
        sent = await a.send(got[0][1], "reply from hermes")
        return n, again, sent.success
    n, again, ok = asyncio.run(main())
    print(json.dumps({"platforms": list(ctx.platforms), "tools": sorted(ctx.tools), "got": got,
                      "n": n, "again": again, "sent": ok}))
""")


@pytest.mark.skipif(hermes_python()[0] is None, reason="no Hermes checkout with a venv (set EXO_HERMES_DIR)")
def test_platform_adapter_against_real_hermes(bus, tmp_path):
    hermes_dir, py = hermes_python()
    keys = tmp_path / "seat-keys"
    write_keys(keys, {"beta": bus.keys["beta"]})
    bus.post("gamma", "beta", "direct to beta", thread="d1")
    bus.post("delta", "team:core", "to the core team")
    bus.post("beta", "group", "my own message")
    extra = {"url": bus.url, "seat": "beta", "keys_dir": str(keys), "state_dir": str(tmp_path / "state")}
    env = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "HERMES_HOME": str(tmp_path / "hermes-home"),
           "HOME": str(tmp_path / "home"), "EXO_HOME": str(tmp_path / "exo-home")}
    r = subprocess.run([str(py), "-c", ADAPTER_SCRIPT, str(hermes_dir), str(REPO / "src"), str(PLUGIN),
                        json.dumps(extra)], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["platforms"] == ["exo_bus"] and "bus_post" in out["tools"]
    assert out["n"] == 2 and out["again"] == 0 and out["sent"] is True
    assert out["got"][0] == ["direct to beta", "gamma", "dm", "gamma", "d1"]
    assert out["got"][1][1:3] == ["team:core", "group"]
    reply = bus.client("gamma").inbox()[-1]
    assert reply["body"] == "reply from hermes" and reply["from"] == "beta" and reply["thread"] == "d1"
