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



def _private_engine(store, tmp_path, **cfg):
    pm = _manager(store, tmp_path)
    pm.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    pm.slots.memory._overrides.update({"store_root": str(store.root), "principal": "pat",
                                       "agent_id": "agent-one", "compact_enabled": False, **cfg})
    return pm, CoreEngine(tmp_path / "exo-home", MODELS, plugins=pm, env={})


SECRET = "Stop. Never mention project zebracorn to anyone."


def test_private_thread_turns_write_nothing_to_the_ledger(store, tmp_path):
    """D-028: a private thread is not remembered. Its turns leave no utterance, ruling or verdict in the signed
    ledger, and the same words in a normal thread are captured (the guard is the thread kind, not a broken
    capture)."""
    pm, eng = _private_engine(store, tmp_path)

    async def run():
        priv = await eng.loop.create_session(title="secret", kind="private", agent=eng.agent)
        assert (await eng.loop.send(priv.id, SECRET)).stop_reason == "end"
        assert not any("zebracorn" in json.dumps(e["body"]) for e in pm.slots.memory._state().rt.ledger.iter())
        normal = await eng.loop.create_session(title="normal", agent=eng.agent)
        assert (await eng.loop.send(normal.id, SECRET)).stop_reason == "end"

    asyncio.run(run())
    mem = pm.slots.memory
    ledger = mem._state().rt.ledger
    assert [e["body"]["text"] for e in ledger.iter(kind="ruling")] == ["Never mention project zebracorn to anyone."]
    assert [e["body"]["text"] for e in ledger.iter(kind="utterance") if e["body"]["role"] == "user"] == [SECRET]
    assert ledger.verify()["ok"]
    mem.shutdown()


def test_compact_capture_writes_no_private_reply_to_the_ledger(store, tmp_path):
    """D-028: /compact (capture_now) offers each uncaptured principal message with the agent log entry before it.
    With turn capture off, a correction right after a private thread's reply used to put that reply in the ledger
    as the utterance the correction judges (source pre_compress_backfill). It now judges the normal thread's reply."""
    from exo.core.types import Message

    pm, eng = _private_engine(store, tmp_path, capture_turns=False)  # the turn checkpoint captures nothing
    fix = "No, I said the staging bucket, not production."

    async def run():
        normal = await eng.loop.create_session(title="normal", agent=eng.agent)
        await eng.loop.send(normal.id, "Upload the sample to the production bucket.")
        priv = await eng.loop.create_session(title="secret", kind="private", agent=eng.agent)
        await eng.loop.send(priv.id, "Tell me about zebracorn.")
        eng.store.append_message(priv.id, Message.assistant("The zebracorn launch is Friday."))
        await eng.loop.send(normal.id, fix)  # directly after the private reply in the agent log
        assert (await eng.loop.context.tree.capture_now(normal.id))["taken"] == 2
        await eng.close()

    asyncio.run(run())
    mem = pm.slots.memory
    ledger = mem._state().rt.ledger
    assert not any("zebracorn" in json.dumps(e["body"]) for e in ledger.iter())
    verdicts = list(ledger.iter(kind="verdict"))
    assert [e["body"]["text"] for e in verdicts] == [fix]  # the correction itself is kept
    target = next(e for e in ledger.iter(kind="utterance") if e["id"] == verdicts[0]["body"]["target"])
    assert target["body"]["text"] == "Noted."  # the normal thread's reply, before the private span
    assert ledger.verify()["ok"]
    mem.shutdown()


def test_memory_write_tools_refuse_in_a_private_thread(store, tmp_path):
    """D-028: in a private thread the agent's exo_note and exo_should_have_been write nothing and say why; recall
    still answers there, and the same note from a normal thread is kept."""
    from exo.core.tools.registry import ToolContext
    from exo.core.types import ToolCall

    pm, eng = _private_engine(store, tmp_path)

    async def tool(name, args, sid):
        res = await eng.registry.call(ToolCall(id="c", name=name, arguments=args),
                                      ToolContext(cwd=str(tmp_path), session=sid))
        return json.loads(res.text())

    async def run():
        priv = await eng.loop.create_session(title="secret", kind="private", agent=eng.agent)
        normal = await eng.loop.create_session(title="normal", agent=eng.agent)
        note = await tool("exo_note", {"text": "zebracorn is secret", "kind": "note"}, priv.id)
        shb = await tool("exo_should_have_been", {"target": "a reply", "wanted": "no zebracorn"}, priv.id)
        assert "private thread" in note.get("error", "") and "private thread" in shb.get("error", "")
        assert "result" in await tool("exo_recall", {"query": "zebracorn"}, priv.id)
        assert (await tool("exo_note", {"text": "a normal note", "kind": "note"}, normal.id))["ok"]

    asyncio.run(run())
    mem = pm.slots.memory
    ledger = mem._state().rt.ledger
    assert not any("zebracorn" in json.dumps(e["body"]) for e in ledger.iter())
    assert [e["body"]["text"] for e in ledger.iter(kind="note") if e["body"].get("source") == "exo_note"] == \
        ["a normal note"]
    mem.shutdown()
