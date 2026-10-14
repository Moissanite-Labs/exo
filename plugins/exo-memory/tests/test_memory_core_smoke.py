"""Smoke test on the Exo core: the plugin loads through the real PluginManager, fills the memory
slot as an ``exo.core.slots.MemoryProvider``, and a CoreEngine session with the fake model
drives it (prompt block, capture of a correction, recall). No network: env={}."""
import asyncio
import json

from conftest import PLUGIN_ROOT

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.slots import MemoryProvider

MODELS = {"fake": {"reply": "Noted."},
          "roles": {"main": ["fake:scripted"], "aux": ["fake:scripted"], "decision": []}}


def _manager(store, tmp_path):
    cfg = {"exo-memory": {}}
    pm = PluginManager(config=cfg)
    rec = pm.load_folder(PLUGIN_ROOT)
    assert rec.ok, rec.error
    return pm


def test_plugin_fills_the_memory_slot(store, tmp_path):
    pm = _manager(store, tmp_path)
    mem = pm.slots.memory
    assert isinstance(mem, MemoryProvider) and mem.name == "exo-memory"
    assert "exo" in pm.commands  # /exo root command from the dispatcher
    assert mem.pre_compress_checkpoint_api_version == 2


def test_session_on_the_core_with_fake_model(store, tmp_path):
    pm = _manager(store, tmp_path)
    pm.slots.memory._overrides.update({"store_root": str(store.root), "principal": "pat",
                                       "agent_id": "agent-one", "compact_enabled": False})
    eng = CoreEngine(tmp_path / "exo-home", MODELS, plugins=pm, env={})

    async def run():
        info = await eng.loop.create_session(title="t")
        first = await eng.loop.send(info.id, "Fix the parser.")
        second = await eng.loop.send(info.id, "Stop. Never use em dashes in the docs.")
        assert first.stop_reason == second.stop_reason == "end", (first.error, second.error)

    asyncio.run(run())
    mem = pm.slots.memory
    st = mem._state()
    assert st is not None, "initialize must have run with exo_home"
    texts = [e["body"].get("text", "") for e in st.rt.ledger.iter(kind=("ruling", "verdict"))]
    assert any("Never use em dashes" in t for t in texts), texts
    assert "# Principal pack" in mem.system_prompt_block()
    assert json.loads(mem.handle_tool_call("exo_recall", {"query": "em dashes"}))
    assert st.rt.ledger.verify()["ok"]
    mem.shutdown()
