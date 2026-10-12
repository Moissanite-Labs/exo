"""exo-context on the Exo core: loader, slot, pins, inner compressor, handoff on compaction.
No network: the aux model is the scripted fake."""
import json

from conftest import PLUGIN_DIR

from exo.core.context import VIEW_HEADER
from exo.core.models.fake import RuleFake
from exo.core.models.router import Router
from exo.core.plugins import PluginManager
from exo.core.slots import ContextEngine
from exo.ext import compat
from exo_context import signals as sg
from exo_context.engine import ExoContextEngine
from exo_context.inner import END_MARKER, SUMMARY_FLAG, CoreInner

RULE = "Never use em dashes in anything you write."


def test_plugin_loads_and_fills_the_slot(tmp_path):
    pm = PluginManager(config={"exo-context": {"handoff_dir": str(tmp_path / "h"), "pin_char_budget": 500}})
    rec = pm.load_folder(PLUGIN_DIR)
    assert rec.ok, rec.error
    eng = pm.slots.context
    assert isinstance(eng, ContextEngine) and eng.name == "exo-context"
    assert eng._x_pin_budget == 500
    assert {"exo"} <= set(pm.commands)


def test_signals_use_capture_and_keep_whole_short_messages(convo):
    s = sg.extract(convo)
    assert [x.text for x in s.rulings][:1] == [convo[3]["content"]]  # whole message, both sentences
    assert [x.text for x in s.corrections] == [convo[7]["content"]]


def test_select_context_pins_what_the_summary_dropped(tmp_path):
    eng = ExoContextEngine(handoff_dir=tmp_path)
    eng.on_session_start("s1")
    tail = [{"role": "system", "content": "sys"}, {"role": "user", "content": RULE},
            {"role": "assistant", "content": "ok"}]
    assert eng.select_context(tail) is None            # still verbatim: nothing to add
    later = [{"role": "system", "content": "sys\n\n" + VIEW_HEADER + "\n\n## Messages 1-2\nnotes"},
             {"role": "user", "content": "carry on"}]
    out = eng.select_context(later)
    assert RULE in out[0]["content"] and "<exo-pinned>" in out[0]["content"]
    assert later[0]["content"].count("exo-pinned") == 0  # input not mutated
    # the pins are on disk, per session
    saved = json.loads(next((tmp_path / "exo-home" / "context-pins").glob("s1.json")).read_text())
    assert saved["ruling"] == [RULE]


def test_core_inner_compresses_with_the_aux_role():
    fake = RuleFake({"reply": "SUMMARY OF THE MIDDLE"})
    router = Router({"aux": ["fake:scripted"]}, instances={"fake": fake}, env={})
    inner = CoreInner(router=router, protect_first_n=1, protect_last_n=1)
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(6)]
    out = inner.compress(msgs)
    assert len(out) == 3 and out[1][SUMMARY_FLAG] and "SUMMARY OF THE MIDDLE" in out[1]["content"]
    assert END_MARKER in out[1]["content"] and out[0] is msgs[0] and out[-1] is msgs[-1]
    assert inner.compression_count == 1


def test_engine_compress_default_inner_pins_rulings(tmp_path, convo):
    router = Router({"aux": ["fake:scripted"]}, instances={"fake": RuleFake({"reply": "short notes"})}, env={})
    compat.set_router(router)
    try:
        eng = ExoContextEngine(handoff_dir=tmp_path / "h", compressor_kwargs={"protect_first_n": 1, "protect_last_n": 2})
        out = eng.compress(convo)
        summary = out[1]["content"]
        assert "short notes" in summary and convo[3]["content"] in summary and convo[7]["content"] in summary
    finally:
        compat.set_router(None)
