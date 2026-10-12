import copy

from conftest import CORRECTION, RULING
from exo import handoff as hf
from exo_context.engine import END_MARKER, SUMMARY_FLAG, ExoContextEngine

FULL_END = END_MARKER + " respond to the message below, not the summary above ---"


class FakeCompressor:
    """Stands in for an inner compressor: keeps system + last 2, summarises the middle."""

    def __init__(self, **kw):
        self.init_kw = kw
        self.calls = []
        self.last_prompt_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0
        self.threshold_percent = 0.5
        self.protect_first_n = 3
        self.protect_last_n = 20
        self.emit_automatic_compaction_status = True
        self._micro_compact_enabled = False
        self.noop = False

    def update_model(self, model, context_length, **kw):
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)

    def update_from_response(self, usage):
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)

    def should_compress(self, prompt_tokens=None):
        return (prompt_tokens or self.last_prompt_tokens) >= self.threshold_tokens

    def compress(self, messages, current_tokens=None, focus_topic=None, force=False, memory_context=""):
        self.calls.append(dict(current_tokens=current_tokens, focus_topic=focus_topic, force=force))
        if self.noop:
            return messages
        self.compression_count += 1
        summary = {"role": "user", "content": "[CONTEXT COMPACTION] The agent built an importer.\n\n" + FULL_END,
                   SUMMARY_FLAG: True, "api_content": "stale"}
        return [messages[0], summary] + [dict(m) for m in messages[-2:]]


def make(tmp_path, **kw):
    made = {}

    def factory(**mk):
        made["inner"] = FakeCompressor(**mk)
        return made["inner"]

    eng = ExoContextEngine(inner_factory=factory, handoff_dir=tmp_path / "handoffs", **kw)
    eng.model_thresholds = {"tiny": 0.9}           # set before update_model, as Hermes does
    eng.update_model("some-model", 100_000)
    return eng, made["inner"]


def test_compress_keeps_pinned_block(convo, tmp_path):
    eng, inner = make(tmp_path)
    out = eng.compress(convo, current_tokens=60_000, focus_topic="billing")
    assert inner.calls == [{"current_tokens": 60_000, "focus_topic": "billing", "force": False}]
    summary = out[1]["content"]
    assert hf.PIN_OPEN in summary and RULING in summary and CORRECTION in summary
    # The pinned block sits inside the summary, before the end marker.
    assert summary.index(hf.PIN_CLOSE) < summary.index(END_MARKER)
    assert "api_content" not in out[1]
    # A handoff was written and linked from the block.
    assert eng.last_handoff.exists() and str(eng.last_handoff) in summary
    assert RULING in eng.last_handoff.read_text()


def test_second_compaction_carries_rulings(convo, tmp_path):
    eng, _ = make(tmp_path)
    first = eng.compress(convo)
    later = first + [{"role": "assistant", "content": "More work."},
                     {"role": "user", "content": "Continue."},
                     {"role": "assistant", "content": "Done."}]
    second = eng.compress(later)
    assert RULING in second[1]["content"]
    assert second[1]["content"].count(hf.PIN_OPEN) == 1


def test_noop_returns_unchanged(convo, tmp_path):
    eng, inner = make(tmp_path)
    inner.noop = True
    assert eng.compress(convo) is convo
    assert eng.last_handoff is None


def test_attributes_route_to_inner(tmp_path):
    eng, inner = make(tmp_path)
    assert inner.model_thresholds == {"tiny": 0.9}  # pending attr applied on build
    eng.update_from_response({"prompt_tokens": 70_000})
    assert eng.last_prompt_tokens == 70_000 and eng.threshold_tokens == 50_000
    assert eng.should_compress() is True
    eng.last_prompt_tokens = -1
    assert inner.last_prompt_tokens == -1
    assert hasattr(eng, "_micro_compact_enabled")
    eng._micro_compact_enabled = True                # host-style setattr after hasattr
    assert inner._micro_compact_enabled is True
    assert eng.name == "exo-context"


def test_clone_and_deepcopy(tmp_path):
    shared = ExoContextEngine(inner_factory=FakeCompressor, handoff_dir=tmp_path)
    clone = shared.clone_for_agent()
    assert clone is not shared and clone.inner is None
    copy.deepcopy(shared)
    clone.update_model("m", 1000)
    assert isinstance(clone.inner, FakeCompressor) and shared.inner is None


def test_select_context_injects_resume_block(tmp_path):
    eng, _ = make(tmp_path)
    req = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert eng.select_context(req) is None
    eng.resume_block = "RESUME BLOCK"
    out = eng.select_context(req)
    assert out[0]["content"].endswith("RESUME BLOCK") and req[0]["content"] == "sys"
