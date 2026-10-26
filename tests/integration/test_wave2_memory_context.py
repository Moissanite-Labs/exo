"""Wave 2 acceptance for exo-memory and exo-context (docs/STANDALONE.md section 6).

An Engine with both plugins and the fake model runs a session. The principal states a
correction, the session is compacted by the core's summary tree (whose fake summarizer
keeps nothing of it), and the correction must still be in the next turn's prompt, word
for word. Engines are built with env={} so nothing touches the network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


from exo.core.context import SUMMARIZER_PREAMBLE, VIEW_HEADER
from exo.core.engine import CoreEngine
from exo.core.models.fake import RuleFake
from exo.core.plugins import PluginManager
from exo.core.slots import ContextEngine, MemoryProvider
from exo.store import PrincipalStore

REPO = Path(__file__).resolve().parents[2]
MODELS = {"fake": {"reply": "Understood."},
          "roles": {"main": ["fake:scripted"], "aux": ["fake:scripted"], "decision": []}}
CORRECTION = "Stop. Never use em dashes in anything you write."


class Recorder(RuleFake):
    """The fake model, remembering every prompt it was given."""

    def __init__(self, config=None):
        super().__init__(config)
        self.main: list[list] = []
        self.aux: list[list] = []

    def stream(self, messages, tools, **opts):
        is_aux = any(SUMMARIZER_PREAMBLE in m.text() for m in messages)
        (self.aux if is_aux else self.main).append(list(messages))
        return super().stream(messages, tools, **opts)


def _engine(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    store = PrincipalStore.create(tmp_path / "store")
    pm = PluginManager(config={"exo-context": {"handoff_dir": str(tmp_path / "handoffs")}})
    for name in ("exo-memory", "exo-context"):
        rec = pm.load_folder(REPO / "plugins" / name)
        assert rec.ok, rec.error
    mem = pm.slots.memory
    mem._overrides.update({"store_root": str(store.root), "principal": "pat", "agent_id": "agent-one",
                           "compact_enabled": False})
    eng = CoreEngine(tmp_path / "home", MODELS, plugins=pm, env={})
    rec = Recorder({"reply": "Understood."})
    eng.router.instances["fake"] = rec
    tree = eng.loop.context.tree
    tree.leaf_size, tree.tail_messages, tree.min_compact_tokens = 2, 4, 0  # compact early: a short session is enough
    return eng, pm, rec


def test_correction_survives_compaction_verbatim(tmp_path, monkeypatch):
    eng, pm, rec = _engine(tmp_path, monkeypatch)
    assert isinstance(pm.slots.memory, MemoryProvider) and pm.slots.memory.name == "exo-memory"
    assert isinstance(pm.slots.context, ContextEngine) and pm.slots.context.name == "exo-context"

    async def run():
        info = await eng.loop.create_session(title="wave2")
        await eng.loop.send(info.id, "Please tidy the release notes.")
        await eng.loop.send(info.id, CORRECTION)
        for i in range(8):  # enough turns for the correction to fall out of the verbatim tail
            await eng.loop.send(info.id, f"Next, item {i}: list the open tasks.")
        await eng.loop.context.tree.settle(info.id)
        await eng.loop.send(info.id, "What are we doing now?")
        return info.id

    sid = asyncio.run(run())

    # compaction happened: the summarizer ran and the prompt carries the summary view
    assert rec.aux, "no summary was requested"
    last = rec.main[-1]
    system = last[0].text()
    assert VIEW_HEADER in system
    # the fake summarizer kept nothing of the correction, and the verbatim message is gone
    assert all(CORRECTION not in m.text() for m in last if m.role == "user")
    # but the next turn's prompt holds it, word for word, in the pinned block
    assert "<exo-pinned>" in system and CORRECTION in system
    pinned = system[system.index("<exo-pinned>"):system.index("</exo-pinned>")]
    assert CORRECTION in pinned
    # a handoff was written when the summary tree grew, and it quotes the correction
    files = list((tmp_path / "handoffs").glob("*.md"))
    assert files and any(CORRECTION in f.read_text() for f in files)
    # exo-memory captured it as a signed ledger event as the turn ended
    st = pm.slots.memory._state()
    texts = [e["body"].get("text", "") for e in st.rt.ledger.iter(kind=("ruling", "verdict"))]
    assert any("Never use em dashes" in t for t in texts), texts
    assert st.rt.ledger.verify()["ok"]
    assert eng.store.get_session(sid) is not None
    pm.slots.memory.shutdown()


def test_a_hosts_undo_reaches_exo_context_through_exo_memory(tmp_path, monkeypatch):
    """#389: Hermes tells only its memory provider about /undo. With both plugins loaded as a host loads them,
    exo-memory's switch notice reaches exo-context, which drops the undone correction's pin, and the next turn's
    block leaves it out."""
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    pm = PluginManager(config={"exo-context": {"handoff_dir": str(tmp_path / "handoffs")}})
    for name in ("exo-memory", "exo-context"):
        rec = pm.load_folder(REPO / "plugins" / name)
        assert rec.ok, rec.error
    ctx, mem = pm.slots.context, pm.slots.memory
    convo = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Please tidy the release notes."},
             {"role": "assistant", "content": "Done."}]
    ctx.on_session_start("hermes-1")
    ctx.select_context(convo + [{"role": "user", "content": CORRECTION}])
    pinned = tmp_path / "home" / "context-pins" / "hermes-1.json"
    assert CORRECTION in pinned.read_text()
    mem.on_session_switch("hermes-1", rewound=True)  # what Hermes's /undo does, and all it does
    assert CORRECTION not in pinned.read_text()
    out = ctx.select_context(convo + [{"role": "user", "content": "What are we doing now?"}])
    assert out is None or CORRECTION not in out[0]["content"]


def test_pins_survive_a_restart(tmp_path, monkeypatch):
    """A new process (new engine, same EXO_HOME and session) still pins the correction."""
    eng, pm, rec = _engine(tmp_path, monkeypatch)

    async def first():
        info = await eng.loop.create_session(title="wave2")
        await eng.loop.send(info.id, CORRECTION)
        return info.id

    sid = asyncio.run(first())
    pm.slots.memory.shutdown()
    ctx = pm.slots.context
    fresh = type(ctx)(principal=None, handoff_dir=str(tmp_path / "handoffs"))
    fresh.on_session_start(sid)
    flat = [{"role": "system", "content": f"{VIEW_HEADER}\n\n## Messages 1-6\nsummary"},
            {"role": "user", "content": "what now?"}]
    out = fresh.select_context(flat)
    assert out is not None and CORRECTION in out[0]["content"]
