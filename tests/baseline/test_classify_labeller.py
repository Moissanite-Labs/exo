"""The decision-model labeller (taste wave 2) on the choice-only decision API left by #124."""
import asyncio

from exo.baseline import classify, shift
from exo.core.models import decision


class Router:
    roles = {"decision": "jev"}


def fake_ask(answers):
    async def ask(router, state, question, options, **kw):
        probs = answers[question]
        assert set(probs) <= set(options)
        best = max(probs, key=probs.get)
        return decision.ChoiceResult(best, probs, "decision", "jev", max(probs.values()))
    return ask


def run(monkeypatch, answers, cls="method", kind="correction"):
    monkeypatch.setattr(decision, "ask_choice_result", fake_ask(answers))
    label = classify.decision_labeller(Router())
    item = classify.Candidate("h-1", "The user wants short replies.", frozenset({"short"}), cls)
    return asyncio.run(label(item, "write me the long version", "", kind))


BASE = {classify.Q_REL: {"contradict": 0.9, "agree": 0.1},
        classify.Q_SCOPE: {"general": 1.0},
        classify.Q_TASK: {shift.TASK_KINDS[0]: 1.0}}


def test_delta_is_the_expected_level(monkeypatch):
    lab = run(monkeypatch, {**BASE, classify.Q_DELTA: {"medium": 0.5, "large": 0.5}})  # level 2.5 rounds to 2
    assert lab.relation == "contradict" and lab.delta == shift.DELTAS[2] and lab.route == "jev"
    lab = run(monkeypatch, {**BASE, classify.Q_DELTA: {"opposite": 1.0}})  # a generative route's single pick
    assert lab.delta == shift.DELTAS[4]


def test_unrelated_asks_nothing_more(monkeypatch):
    lab = run(monkeypatch, {classify.Q_REL: {"unrelated": 1.0}})
    assert lab.relation == "unrelated" and lab.delta == 0.0


def test_no_decision_route_means_no_labeller():
    class Bare:
        roles = {}
    assert classify.decision_labeller(Bare()) is None
