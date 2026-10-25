"""Regressions found while restoring D-044 against the current runtime."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from exo.baseline import adapt, classify, command, layers, shift
from exo.core.models import decision
from tests.baseline import test_adapt as fixtures
from tests.baseline.test_adapt import B, INSTALL, at, ledger, pack, signal, verdict

clock = fixtures.clock
store = fixtures.store


def test_single_pasted_paragraph_and_feedback_do_not_become_preference(store, clock):
    article = "Use bullet lists instead of short paragraphs. " * 30
    assert classify.own_words(article) == ""
    calls = []
    with ledger(store) as led:
        clock.day(3)
        for text in (article, '> Always use bullet lists, not short paragraphs.',
                     '```\nuse bullet lists, not short paragraphs\n```'):
            verdict(led, text)
        asyncio.run(classify.record_signals(store, led, labeller=lambda *a: calls.append(a), now=at(3), pack=pack()))
    assert calls == []


def test_only_learner_notes_and_principal_behavior_count(store, clock):
    with ledger(store) as led:
        clock.day(2)
        ev = verdict(led, "use bullet lists, not short paragraphs")
        note = led.append("note", {"type": "taste_mode", "mode": "ask"}, author="principal")
        assistant = led.append("utterance", {"role": "assistant", "text": "bullet lists"}, author="principal")
        for evidence, author in ((ev, "peer:attacker"), (note, "exo-learn"), (assistant, "exo-learn")):
            led.append("note", {"type": "taste_signal", "item": "h-b", "evidence": evidence["id"],
                               "relation": "contradict", "confidence": 1, "delta": 1}, author=author)
        led.append("note", {"type": "taste_scan", "upto": 100000}, author="peer:attacker")
        assert adapt.read_ledger(store, {"principal"})["signals"] == []
        out = asyncio.run(classify.record_signals(store, led, now=at(2), pack=pack()))
        assert out["signals"] == 1


def test_failed_questions_consume_durable_daily_budget(store, clock):
    (store.root / "policies/consolidate.yaml").write_text('taste:\n  daily_cap: 2\n  timezone: UTC\n')
    calls = []

    async def failing(*args):
        calls.append(args)
        raise RuntimeError("model unavailable")

    with ledger(store) as led:
        clock.day(3)
        for _ in range(4):
            verdict(led, "bullet lists, not short paragraphs")
        first = asyncio.run(classify.record_signals(store, led, labeller=failing, now=at(3), pack=pack()))
        second = asyncio.run(classify.record_signals(store, led, labeller=failing, now=at(3), pack=pack()))
        assert first["capped"] and second["capped"] and len(calls) == 2
        clock.day(4)
        asyncio.run(classify.record_signals(store, led, labeller=failing, now=at(4), pack=pack()))
        assert len(calls) == 4


def test_real_decision_adapter_counts_each_question(store, clock, monkeypatch):
    (store.root / "policies/consolidate.yaml").write_text('taste:\n  daily_cap: 3\n  timezone: UTC\n')
    calls = []

    async def ask(router, state, question, options, **kwargs):
        calls.append(question)
        choice = {classify.Q_REL: "contradict", classify.Q_DELTA: "opposite",
                  classify.Q_SCOPE: "general", classify.Q_TASK: "writing"}[question]
        return decision.ChoiceResult(choice, {choice: .99}, "decision", "test", .99)

    monkeypatch.setattr(decision, "ask_choice_result", ask)
    labeller = classify.decision_labeller(SimpleNamespace(roles={"decision": ["test"]}))
    with ledger(store) as led:
        clock.day(3)
        verdict(led, "bullet lists, not short paragraphs")
        out = asyncio.run(classify.record_signals(store, led, labeller=labeller, now=at(3), pack=pack()))
        assert len(calls) == 3 and out["capped"] and out["signals"] == 0
        assert classify._model_calls_today(led, at(3)[:10]) == 3


@pytest.mark.parametrize("source,guarded", [("ruling", False), ("learned", True)])
def test_scoped_change_cannot_replace_guarded_item_or_ruling(source, guarded):
    origin = INSTALL.date()
    sigs = [shift.Signal("x", at(day), "contradict", 1, .6, project="demo", claim="new")
            for day in (7, 14, 21)]
    sub = shift.Subject("x", "existing", source=source, guarded=guarded)
    track = shift.replay(sub, sigs, origin=origin, today=origin + timedelta(days=28))
    assert [o.kind for o in track.outcomes] == ["question"]


@pytest.mark.parametrize("protected", ["ruling", "pinned"])
def test_learned_override_cannot_hide_ruling_or_pin(protected):
    first = layers.UserItem("old", "keep me", frozenset(), .8,
                            "ruling" if protected == "ruling" else "learned", at(0), at(0))
    later = layers.UserItem("new", "new", frozenset(), .9, "learned", at(0), at(0), overrides="old")
    got = layers.resolve([], [first, later], pins={"old"} if protected == "pinned" else set())
    assert [e.id for e in got] == ["old"]


def test_yes_uses_the_proposal_snapshot_and_identity():
    origin = INSTALL.date()
    sigs = [shift.Signal("x", at(day), "contradict", 1, .6, claim="FIRST SHOWN", evidence=f"e{day}")
            for day in range(7, 85, 7)]
    sub = shift.Subject("x", "old")
    proposed = shift.replay(sub, sigs, origin=origin, today=origin + timedelta(days=90), mode_at=lambda _: "ask")
    when = proposed.proposal["at"]
    sigs.append(shift.Signal("x", at(92), "contradict", 1, .6, claim="LATER UNSHOWN", evidence="later"))
    accepted = shift.replay(sub, sigs, origin=origin, today=origin + timedelta(days=101),
                            acts=[shift.Act(at(100), "yes", "x", when)], mode_at=lambda _: "ask")
    swap = next(o for o in accepted.outcomes if o.kind == "swapped")
    assert swap.data["claim"] == "FIRST SHOWN"
    assert "later" not in {e["id"] for e in swap.data["evidence"]}
    stale = shift.replay(sub, sigs, origin=origin, today=origin + timedelta(days=101),
                         acts=[shift.Act(at(100), "yes", "x", "different")], mode_at=lambda _: "ask")
    assert not any(o.kind == "swapped" for o in stale.outcomes)


def test_future_mode_and_value_signal_do_not_change_present(store, clock):
    with ledger(store) as led:
        signal(led, clock, "h-v", 5, delta=1, confidence=.99, voice="own", stated=True)
        clock.day(6)
        led.append("note", {"type": "taste_mode", "mode": "ask"}, author="principal")
    now = adapt.load(store, now=at(4), pack=pack())
    assert now.mode == "silent" and now.track("h-v").status == "active"


def test_restoring_house_item_removes_implicit_learned_override(store, clock):
    learned = layers.UserItem("implicit", "bullets", B.sig, .8, "learned", at(0), at(0))
    view = layers.TasteView(True, "released", pack(), [])
    view.state = adapt.State("ask", "default", layer=layers.UserLayer(items=[learned]))
    assert command._overriders(view, B.id) == ["implicit"]


def test_guarded_challenger_is_a_question_even_for_unguarded_subject():
    origin = INSTALL.date()
    sigs = [shift.Signal("x", at(day), "contradict", 1, .6, claim="Publish the report automatically")
            for day in range(7, 85, 7)]
    track = shift.replay(shift.Subject("x", "Write a short draft"), sigs,
                         origin=origin, today=origin + timedelta(days=90))
    assert [o.kind for o in track.outcomes] == ["question"]


def test_interrupted_replacement_reuses_proposal_and_acceptance(store, clock, monkeypatch):
    with ledger(store) as led:
        fixtures.sam(led, clock)
        clock.day(84)
        append = led.append
        failed = False

        def fail_before_outcome(kind, body, **kwargs):
            nonlocal failed
            if body.get("type") == "taste_replaced" and not failed:
                failed = True
                raise RuntimeError("interrupted")
            return append(kind, body, **kwargs)

        monkeypatch.setattr(led, "append", fail_before_outcome)
        with pytest.raises(RuntimeError, match="interrupted"):
            adapt.close_weeks(store, led, now=at(84), pack=pack())
        adapt.close_weeks(store, led, now=at(84), pack=pack())
        assert len(list(led.iter(kind="belief_proposed"))) == 1
        assert len(list(led.iter(kind="belief_accepted"))) == 1
        assert sum(e["body"].get("type") == "taste_replaced" for e in led.iter(kind="note")) == 1


def test_concurrent_scans_share_question_cap_and_do_not_duplicate_signal(store, clock):
    (store.root / "policies/consolidate.yaml").write_text('taste:\n  daily_cap: 2\n  timezone: UTC\n')
    calls = []

    async def label(*args):
        calls.append(args)
        await asyncio.sleep(0)
        return classify.Label("contradict", 1, confidence=.99)

    with ledger(store) as led:
        clock.day(3)
        for _ in range(2):
            verdict(led, "bullet lists, not short paragraphs")

        async def scans():
            return await asyncio.gather(*[
                classify.record_signals(store, led, labeller=label, now=at(3), pack=pack()) for _ in range(2)])

        asyncio.run(scans())
        assert len(calls) == 2
        notes = [e["body"] for e in led.iter(kind="note") if e["body"].get("type") == "taste_signal"]
        assert len(notes) == len({(b["item"], b["evidence"]) for b in notes})


def test_reset_all_preserves_facts_and_rulings(store, clock):
    from exo.consolidate import load_state
    from tests.baseline.conftest import add_belief
    with ledger(store) as led:
        clock.day(3)
        add_belief(led, "fact", "The release is Monday", "work/release", type_="fact", ts=at(3))
        add_belief(led, "ruling", "Always check the source", "work/source", kind="ruling", type_="rule", ts=at(3))
        add_belief(led, "preference", "Use short paragraphs", "mind/format", ts=at(3))
        assert {u.id for u in layers.user_layer(store, at(3)).items} == {"ruling", "preference"}
        command.run(store, "reset all confirm", principal=True, open_ledger=lambda: led, now=at(3), pack=pack())
        beliefs = load_state(led).beliefs
        assert beliefs["fact"].status == "accepted" and beliefs["ruling"].status == "accepted"
        assert beliefs["preference"].status != "accepted"


def test_adapter_does_not_inflate_uncertain_delta(monkeypatch):
    async def ask(router, state, question, options):
        choice = {classify.Q_REL: "contradict", classify.Q_DELTA: "opposite",
                  classify.Q_SCOPE: "general", classify.Q_TASK: "writing"}[question]
        conf = .7 if question == classify.Q_DELTA else .99
        return decision.ChoiceResult(choice, {choice: conf}, "decision", "test", conf)

    monkeypatch.setattr(decision, "ask_choice_result", ask)
    labeller = classify.decision_labeller(SimpleNamespace(roles={"decision": ["test"]}))
    label = asyncio.run(labeller(classify.Candidate("x", "old", frozenset()), "new", "", "verdict:edit"))
    assert label.delta == 1 and label.confidence == .7


def test_pending_scope_text_is_the_scope_frozen_in_the_proposal():
    track = shift.Track("x", "method", claim="newer claim", scope="project:new")
    track.proposal = {"kind": "scope", "snapshot": {"scope": "project:shown", "claim": "shown claim"}}
    state = adapt.State("ask", "default", items={"x": adapt.Item(shift.Subject("x", "old"), track, True,
                                                                   INSTALL.date())})
    view = layers.TasteView(True, "released", pack(), [], state=state)
    text = command.pending_line(view, "x")
    assert "project:shown: shown claim" in text and "project:new" not in text and "newer claim" not in text
