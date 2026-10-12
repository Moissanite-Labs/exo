import asyncio
import json

from exo import eval as E
from exo.consolidate import load_state, proposals_list
from exo.learn import nightly
from exo.learn.climb import check_regression, current_config
from exo.learn.promote import apply_policy

from helpers import correction, propose, ruling, scripted_model, write_qa


def status(lr, pid):
    return next((v.status for v in proposals_list(lr.runtime.store, lr.runtime.ledger, include_decided=True)
                 if v.id == pid), None)


def run(lr, **kw):
    return asyncio.run(lr.nightly(**kw))


def test_nightly_keeps_a_helpful_lesson_and_records_it(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    res = propose(rt, evidence=[correction(rt, "plain status lines please", "s1")["id"]])   # one session: stays a proposal
    assert apply_policy(learner)[0].action in ("hint", "wait")
    out = run(learner)
    assert out["status"] == "done" and out["climb"]["accepted"] == [f"add:{res['id']}"]
    assert status(learner, res["id"]) == "accepted"
    assert "plain status lines" in (rt.root / "taste.md").read_text()
    sb = E.load_jsonl(rt.root / "reports" / "scoreboard.jsonl")
    assert sb[0]["composite"] == 1.0 and any((r.get("change") or {}).get("status") == "kept" for r in sb)
    assert (rt.root / "reports" / "scoreboard.html").exists()
    assert any(e["body"]["type"] == "learn_climb" for e in rt.ledger.iter(kind="note"))
    # a second night has nothing left to do and keeps the incumbent
    again = run(learner)
    assert again["climb"]["accepted"] == [] and again["climb"]["incumbent"] == out["climb"]["incumbent"]


def test_nightly_leaves_a_useless_lesson_alone(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model(good_marker="never-matches")
    res = propose(rt, evidence=[correction(rt, "plain status lines please", "s1")["id"]])
    out = run(learner)
    assert out["climb"]["accepted"] == [] and status(learner, res["id"]) == "proposed"


def test_nightly_drops_a_harmful_auto_promoted_lesson(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model(base_good=True)
    ev = [correction(rt, "emoticons please", "s1")["id"], correction(rt, "more emoticons", "s2")["id"]]
    res = propose(rt, evidence=ev, wants="emoticons in status lines", avoid="plain text",
                  claim="The principal wants emoticons in status lines, not plain text.")
    assert apply_policy(learner)[0].action == "auto_promoted"
    out = run(learner)
    assert out["climb"]["accepted"] == [f"drop:{res['id']}"]
    assert status(learner, res["id"]) == "rejected"
    assert current_config(learner)["rules"] == []
    assert "emoticons" not in (rt.root / "taste.md").read_text()


def test_regression_check_reverts_the_culprit_but_only_asks_for_principal_lessons(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model(base_good=True)
    E.scoreboard(rt.root, {"incumbent": "a", "composite": 0.95})
    ev = [correction(rt, "emoticons please", "s1")["id"], correction(rt, "more emoticons", "s2")["id"]]
    res = propose(rt, evidence=ev, wants="emoticons in status lines", avoid="plain text",
                  claim="The principal wants emoticons in status lines, not plain text.")
    apply_policy(learner)
    E.scoreboard(rt.root, {"incumbent": "b", "composite": 0.40})
    out = check_regression(learner, E.Budget())
    assert out == [{"proposal_id": res["id"], "action": "reverted", "event": out[0]["event"]}]
    assert status(learner, res["id"]) == "rejected"

    # a lesson the principal ruled on is not reverted behind their back: a question instead
    E.scoreboard(rt.root, {"incumbent": "c", "composite": 0.95})
    r = ruling(rt, "From now on emoticons in every status line.")
    res2 = propose(rt, evidence=[r["id"]], wants="emoji and emoticons sprinkled around", avoid="a sober tone",
                   claim="The principal wants emoji and emoticons sprinkled around, not a sober tone.")
    apply_policy(learner)
    E.scoreboard(rt.root, {"incumbent": "d", "composite": 0.40})
    out = check_regression(learner, E.Budget())
    assert out == [{"proposal_id": res2["id"], "action": "question"}]
    assert status(learner, res2["id"]) == "accepted"


def test_nightly_promotes_a_skill_that_passes_the_gate(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    ev = [correction(rt, "write status plain", "s1")["id"], correction(rt, "plain status", "s2")["id"]]
    res = propose(rt, "skill", evidence=ev, skill="status-update", patch="Write plain status lines, always.",
                  claim="Status lines are plain.")
    out = run(learner)
    assert [p["action"] for p in out["promotions"]] == ["auto_promoted"]
    assert (rt.root / "skills" / "status-update" / "SKILL.md").exists()
    assert status(learner, res["id"]) == "accepted"


def test_nightly_respects_the_caps(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    learner.settings["climb"].update(candidates=2, judge_samples=1)
    lessons = [("tables for comparisons", "bullets for comparisons"), ("ISO dates", "month names"),
               ("greetings omitted", "chatty openers"), ("links inline", "footnote markers"),
               ("metric units", "imperial units")]
    for k, (want, avoid) in enumerate(lessons):
        propose(rt, evidence=[correction(rt, f"use {want}", f"s{k}")["id"]], wants=want, avoid=avoid,
                claim=f"The principal wants {want}, not {avoid}.", domain="mind")
    assert len([v for v in proposals_list(rt.store, rt.ledger)]) == 5
    out = run(learner)
    assert len(out["climb"]["candidates"]) == 2


def test_nightly_off_without_a_store(make_engine):
    eng = make_engine(store=False)
    out = asyncio.run(nightly(eng))
    assert out["status"] == "off" and "learning is off" in out["reason"]
