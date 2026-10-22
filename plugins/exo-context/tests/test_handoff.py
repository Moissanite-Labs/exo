from datetime import datetime, timezone

from conftest import BRIEF, CORRECTION, RULING
from exo import handoff as hf
from exo.store import PrincipalStore

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_handoff_contains_rulings_verbatim(convo, tmp_path):
    path = hf.write_handoff("Billing importer", convo, None, tmp_path / "h", now=NOW)
    assert path.name == "billing-importer-20260102.md"
    text = path.read_text()
    assert f"> {RULING}" in text
    assert f"> {CORRECTION}" in text
    assert f"> {BRIEF}" in text
    assert "Should I open the PR now or wait for the review?" in text      # open decision
    assert "pid 4242" in text                                              # live process
    assert "written: /srv/project/importer.py" in text                     # files touched
    assert "1. Run the migration on staging" in text                       # next steps
    assert "/exo resume billing-importer" in text                          # how to resume
    for section in ("## How to resume", "## Decisions made", "## Open decisions", "## State",
                    "## Live processes mentioned", "## Files touched", "## Next steps"):
        assert section in text


def test_load_handoff_round_trip(convo, tmp_path):
    path = hf.write_handoff("billing", convo, None, tmp_path, session="s-1", now=NOW)
    h = hf.load_handoff(path)
    assert h.topic == "billing"
    assert h.meta["session"] == "s-1"
    assert h.rulings == [RULING]
    assert h.corrections == [CORRECTION]
    assert h.open_decisions == ["Should I open the PR now or wait for the review?"]
    assert h.next_steps == ["Run the migration on staging", "Write the docs"]
    assert hf.find_handoff("billing", tmp_path) == path


def test_default_dir_uses_principal(convo, tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_PRINCIPAL", "p1")
    path = hf.write_handoff("x", convo, now=NOW)
    assert path.parent == tmp_path / "exo-home" / "principals" / "p1" / "handoffs"
    store = PrincipalStore(tmp_path / "somewhere")
    assert hf.default_handoff_dir(store) == store.root / "handoffs"


def test_pinned_block_round_trip_and_carry():
    sig = hf.Signals(rulings=[hf.Signal("ruling", "Line one.\n\nLine two after a blank.")],
                     corrections=[hf.Signal("correction", CORRECTION)])
    block = hf.render_pinned_block(sig, "/x/h.md")
    parsed = hf.parse_pinned_blocks("before\n" + block + "\nafter")
    assert parsed["ruling"] == ["Line one.\n\nLine two after a blank."]
    assert parsed["correction"] == [CORRECTION]
    # A later transcript that only holds exo-context's own block, opening a system message, still
    # yields the signals. The same block inside a summary row yields nothing (#385): that row is
    # indistinguishable from a stock summary exo-context never escaped, so pins carry instead.
    later = [{"role": "system", "content": block}]
    carried = hf.extract_signals(later)
    assert [s.text for s in carried.rulings] == ["Line one.\n\nLine two after a blank."]
    assert carried.rulings[0].carried
    stock = [{"role": "user", "content": "[CONTEXT COMPACTION] summary\n" + block}]
    assert hf.extract_signals(stock).rulings == []


def test_pinned_block_budget_drops_oldest_first():
    sig = hf.Signals(rulings=[hf.Signal("ruling", f"rule {i} " + "x" * 200) for i in range(20)])
    block = hf.render_pinned_block(sig, None, char_budget=1500)
    assert len(block) <= 1700
    assert "rule 19" in block and "rule 0 " not in block
    assert "omitted here for space" in block


def test_resume_text_includes_handoff_and_retrieval(convo, tmp_path):
    store = PrincipalStore.create(tmp_path / "store")
    path = hf.write_handoff("billing", convo, store, now=NOW)
    text = hf.resume_text(hf.load_handoff(path), store)
    assert RULING in text
    assert text.startswith('Resuming "billing"')
