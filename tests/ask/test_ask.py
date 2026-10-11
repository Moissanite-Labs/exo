import json
from datetime import datetime, timedelta, timezone

import pytest

from exo import ask
from exo.ask import QuestionQueue, should_ask, voi_score
from exo.ledger import Ledger

T0 = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "policies").mkdir()
    return tmp_path


def write_policy(root, text):
    (root / "policies" / "interruptions.yaml").write_text(text)


# ask or act ---------------------------------------------------------------------------------

def test_agreement_acts_and_split_asks():
    pol = {"ask_threshold": {"default": 0.5}}
    d = should_ask({"domain": "work"}, ["ship", "ship", "Ship "], policy=pol)
    assert not d.ask and d.disagreement == 0 and d.choice == "ship"
    d = should_ask({"domain": "work"}, ["ship", "wait", "ship", "wait"], policy=pol)
    assert d.ask and d.disagreement == pytest.approx(1.0) and d.vote_split == pytest.approx(0.5)


def test_threshold_per_domain_from_policy(root):
    write_policy(root, "ask_threshold:\n  default: 0.9\n  public: 0.1\n")
    preds = ["a", "a", "a", "b"]           # normalized entropy ~0.81
    assert not should_ask({"domain": "work"}, preds, root=root).ask
    assert should_ask({"domain": "public"}, preds, root=root).ask
    assert should_ask({"domain": "public/github"}, preds, root=root).threshold == 0.1


def test_single_prediction_asks_but_distribution_is_measured():
    assert should_ask({}, ["a"], policy={}).ask
    d = should_ask({}, [{"probs": {"a": 0.97, "b": 0.03}}], policy={})
    assert not d.ask and d.choice == "a"
    d = should_ask({}, [{"choice": "a", "weight": 3}, {"choice": "b", "weight": 1}], policy={})
    assert d.distribution == {"a": 0.75, "b": 0.25}


# voi ----------------------------------------------------------------------------------------

def test_voi_formula_and_recent_corrections_raise_rank(root, signer):
    assert voi_score(1, 0.5, 0) == 0.5
    assert voi_score(1, 0.5, 2) == 1.5
    assert voi_score(2, -1, 0) == 0
    led = Ledger(root / "ledger.sqlite", signer)
    out = led.append("artifact", {"text": "draft"})
    led.append("verdict", {"verdict": "edit", "target": out["id"], "node": "mind/style"})
    led.append("verdict", {"verdict": "accept", "target": out["id"], "node": "mind/other"})
    q = QuestionQueue(root, policy={}, ledger=led)
    a = q.propose("Style question?", "mind/style", impact=0.5, uncertainty=0.5)
    b = q.propose("Other question?", "mind/other", impact=0.6, uncertainty=0.5)
    assert a.voi == pytest.approx(0.25 * 2, rel=0.01)   # one fresh correction: R ~= 1
    assert b.voi == pytest.approx(0.3)                  # accept is not a correction
    assert [x.id for x in q.open()] == [a.id, b.id]
    corr = ask.corrections_by_node(led)
    old = ask.recency("mind/style", corr, ask.utcnow() + timedelta(days=14), half_life_days=14)
    assert old == pytest.approx(0.5, rel=0.01)


# queue and budget ---------------------------------------------------------------------------

def test_queue_is_append_only_events(root):
    q = QuestionQueue(root, policy={})
    x = q.propose("Q1?", "a/b", impact=0.9, uncertainty=0.9, qid="q1")
    q.mark_asked(["q1"], "chat")
    q.drop("q1", "moot")
    ops = [json.loads(l)["op"] for l in (root / "queues" / "questions.jsonl").read_text().splitlines()]
    assert ops == ["proposed", "asked", "dropped"]
    assert q.get("q1").status == "dropped" and x.domain == "a"
    with pytest.raises(ValueError):
        q.propose("again", qid="q1")


def test_daily_and_window_budget(root):
    write_policy(root, "questions_per_day: 3\nwindow:\n  hours: 4\n  max: 2\ntimezone: UTC\n")
    q = QuestionQueue(root)
    for i in range(6):
        q.propose(f"Q{i}?", impact=1 - i / 10, uncertainty=0.5, qid=f"q{i}")
    first = q.next_questions(5, T0, mark=True)
    assert [x.id for x in first] == ["q0", "q1"]               # window allows 2
    assert q.next_questions(5, T0 + timedelta(hours=1)) == []
    later = q.next_questions(5, T0 + timedelta(hours=5), mark=True)
    assert [x.id for x in later] == ["q2"]                     # day cap 3
    assert q.budget(T0 + timedelta(hours=6))["remaining"] == 0
    assert len(q.next_questions(5, T0 + timedelta(days=1))) == 2


def test_quiet_hours_block(root):
    write_policy(root, 'timezone: UTC\nquiet_hours: ["23:00-08:00"]\n')
    q = QuestionQueue(root)
    q.propose("Q?")
    assert q.next_questions(3, T0.replace(hour=2)) == []
    assert len(q.next_questions(3, T0.replace(hour=12))) == 1


def test_front_loaded_falling_curve(root):
    write_policy(root, "questions_per_day: 2\ncurve:\n  start_per_day: 10\n  half_life_weeks: 1\n  start: 2026-10-01\n")
    q = QuestionQueue(root)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    caps = [q.daily_cap(start + timedelta(weeks=w)) for w in (0, 1, 2, 8)]
    assert caps == [10, 6, 4, 2]


def test_answer_writes_ledger_and_calls_belief_hook(root, signer):
    led = Ledger(root / "ledger.sqlite", signer)
    q = QuestionQueue(root, policy={}, ledger=led)
    q.propose("Morning or evening brief?", "relationship/reporting", qid="q1")
    q.mark_asked(["q1"], "brief", author="agent")
    calls = []

    def hook(answer_event, question):
        calls.append((answer_event, question))
        return {"id": "prop-1"}

    ev = q.answer("q1", "Morning.", author="principal", propose_belief=hook)
    assert ev["proposal"] == "prop-1"
    kinds = [e["kind"] for e in led.iter()]
    assert kinds == ["question", "answer"]
    ans = list(led.iter(kind="answer"))[0]
    qev = list(led.iter(kind="question"))[0]
    assert ans["refs"] == [qev["id"]] and ans["body"]["question"] == "q1"
    assert ans["body"]["node"] == "relationship/reporting" and ans["author"] == "principal"
    assert calls[0][0]["id"] == ans["id"] and calls[0][1]["id"] == "q1"
    assert q.get("q1").status == "answered" and q.open() == []
    assert led.verify()["ok"]


def test_answer_without_ledger_still_records(root):
    q = QuestionQueue(root, policy={})
    q.propose("Q?", qid="q1")
    seen = []
    q.answer("q1", "yes", propose_belief=lambda **kw: seen.append(kw) or "p")
    assert q.get("q1").answer == "yes" and seen[0]["answer_event"]["body"]["text"] == "yes"


SAMPLE = """# Open questions

1. **Approval line.** Which surfaces need review: repos or
   client work? → `relationship/autonomy`
2. **Spend.** What cost may an agent incur? → `mind/decision-style`
3. ~~Answered already~~ → `x/y`
"""


def test_seed_from_markdown(root):
    items = ask.parse_markdown_questions(SAMPLE)
    assert [i["node"] for i in items] == ["relationship/autonomy", "mind/decision-style"]
    assert items[0]["impact"] == 1.0 and items[1]["impact"] < 1.0
    assert "client work?" in items[0]["text"]
    q = QuestionQueue(root, policy={})
    assert len(ask.seed_from_markdown(q, SAMPLE)) == 2
    assert ask.seed_from_markdown(q, SAMPLE) == []
    assert q.open()[0].node == "relationship/autonomy"


def test_cli_hooks(root, capsys):
    import argparse
    from types import SimpleNamespace
    sp = argparse.ArgumentParser().add_subparsers(dest="cmd")
    ask.register(sp, argparse.ArgumentParser(add_help=False))
    store = SimpleNamespace(root=root, ledger_path=root / "ledger.sqlite")
    a = SimpleNamespace(ask_cmd="propose", text="Q?", node="a/b", rationale="", impact=0.5, uncertainty=0.5,
                        channel="brief", default=None)
    assert ask.cmd_ask(a, store) == 0
    assert ask.cmd_ask(SimpleNamespace(ask_cmd="budget"), store) == 0
    assert '"remaining"' in capsys.readouterr().out
