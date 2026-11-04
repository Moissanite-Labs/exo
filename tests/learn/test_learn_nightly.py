import asyncio
import json

import pytest

from exo import eval as E
from exo.consolidate import proposals_list
from exo.learn import learned_since, nightly
from exo.learn.climb import candidates_for, check_regression, current_config
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


@pytest.mark.parametrize("setting,ptype,extra", [
    ({"high_impact_domains": ["mind"]}, "taste", {"critic_note": "one-off: they asked once in one session"}),
    ({"high_impact_domains": ["mind"]}, "taste", {}),
    ({"eval_gate_for": ["skill", "fact"]}, "fact", {}),
    ({}, "taste", {"critic_note": "one-off: they asked once in one session"}),
], ids=["gated-critic", "gated-one-session", "gated-type", "ungated-critic"])
def test_nightly_climb_leaves_gated_and_objected_lessons_to_the_policy(learner, setting, ptype, extra):
    """Rule 5 and D-054: a gated lesson keeps its critic, weight, session and held-out checks; the climb is no way
    around them. No auto path promotes over a critic objection."""
    rt = learner.runtime
    learner.settings["promote"].update(setting)
    write_qa(rt.root)
    learner._complete = scripted_model()
    res = propose(rt, ptype, evidence=[correction(rt, "plain status lines please", "s1")["id"]], **extra)
    out = run(learner)
    assert [d["action"] for d in out["promotions"]] in (["wait"], ["hint"])
    assert out["climb"]["accepted"] == [] and not any(c["id"] == f"add:{res['id']}" for c in out["climb"]["candidates"])
    assert status(learner, res["id"]) == "proposed"


def test_nightly_climb_fails_a_lesson_with_no_held_out_case_in_its_domain(learner):
    rt = learner.runtime
    rows = [{"id": f"q{k}", "question": f"Write status line {k}", "gold": f"Status ok {k}",
             "domain": "work" if E._hash_pct(f"q{k}") < 20 else "mind"} for k in range(24)]
    (rt.root / "training").mkdir(parents=True, exist_ok=True)
    (rt.root / "training" / "eval-qa.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    learner._complete = scripted_model()
    res = propose(rt, evidence=[correction(rt, "plain status lines please", "s1")["id"]])
    out = run(learner)
    (c,) = [c for c in out["climb"]["candidates"] if c["id"] == f"add:{res['id']}"]
    assert c["dev"]["better"] and (c["status"], c["reason"]) == ("gate_failed", "no held-out cases")
    assert status(learner, res["id"]) == "proposed"


def test_nightly_climb_records_what_it_kept(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    res = propose(rt, "fact", evidence=[correction(rt, "plain status lines please", "s1")["id"]])
    out = run(learner)
    assert out["climb"]["accepted"] == [f"add:{res['id']}"]
    (note,) = [e for e in rt.ledger.iter(kind="note") if e["body"]["type"] == "learn_promotion"]
    assert (note["body"]["ptype"], note["body"]["domain"], note["body"]["via"]) == ("fact", "mind", "climb")
    assert rt.ledger.get(note["refs"][0])["kind"] == "belief_accepted"
    assert any(line.startswith("Learned (fact, auto-promoted)") for line in learned_since(learner.engine))


def test_nightly_climb_ignores_replays_the_principal_did_not_write(learner):
    """D-040: a peer's question with the agent's exo_should_have_been verdict on the reply is not an eval case."""
    rt = learner.runtime
    learner._complete = scripted_model()
    for k in range(6):
        rt.ledger.append("utterance", {"text": f"Write status line {k}", "role": "user"}, "peer:other", session=f"p{k}")
        a = rt.ledger.append("utterance", {"text": f"Status fine {k}", "role": "assistant"}, "agent", session=f"p{k}")
        rt.ledger.append("verdict", {"verdict": "should_have_been", "target": a["id"], "wanted": f"Status ok {k}"},
                         "agent", session=f"p{k}")
    res = propose(rt, evidence=[correction(rt, "plain status lines please", "s1")["id"]])
    out = run(learner)
    assert out["climb"]["sizes"] == {"train": 0, "dev": 0, "held": 0}
    assert out["climb"]["accepted"] == [] and status(learner, res["id"]) == "proposed"


def test_nightly_rescores_the_incumbent_after_a_model_switch(learner):
    """The cached incumbent scores belong to the models that made them: a lesson that changes nothing is not kept
    because the main route got better overnight."""
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model(good_marker="never-matches")
    res = propose(rt, evidence=[correction(rt, "plain status lines please", "s1")["id"]])
    first = run(learner)
    assert first["climb"]["accepted"] == [] and first["climb"]["start_composite"] == 0.0
    learner.engine.router.roles["main"] = ["fake:newer"]
    learner._complete = scripted_model(good_marker="never-matches", base_good=True)
    out = run(learner)
    assert out["climb"]["start_composite"] == 1.0 and out["climb"]["accepted"] == []
    assert status(learner, res["id"]) == "proposed"


def _replay(rt, k, chosen, session):
    """The principal's question, the agent's answer and the principal's edit of it: one replay case."""
    rt.ledger.append("utterance", {"text": f"Next status line {k}", "role": "user"}, "principal", session=session)
    out = rt.ledger.append("utterance", {"text": f"Status fine {k}", "role": "assistant"}, "agent", session=session)
    return rt.ledger.append("verdict", {"verdict": "edit", "target": out["id"], "chosen": chosen}, "principal",
                            session=session)


def _held_out(monkeypatch, own, also=()):
    """Put the cases built from the events in `own`, and the items named in `also`, in the held-out set."""
    def split(items, **kw):
        held = [i for i in items if E.derived_from(i, own) or i["id"] in also]
        return {"train": [], "dev": [i for i in items if i not in held], "held": held}
    monkeypatch.setattr(E, "split", split)


def test_nightly_add_candidate_never_wins_on_its_own_evidence(learner, monkeypatch):
    """The climb hands the lesson's evidence to the eval (`exclude`): the replay of the verdict it cites is no
    held-out proof for it."""
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    edit = _replay(rt, 7, "Status ok 7", "s1")
    res = propose(rt, evidence=[edit["id"]])
    _held_out(monkeypatch, {edit["id"]})
    assert [(c["id"], c["exclude"]) for c in candidates_for(learner, 10)] == [(f"add:{res['id']}", [edit["id"]])]
    out = run(learner)
    (c,) = [c for c in out["climb"]["candidates"] if c["id"] == f"add:{res['id']}"]
    assert c["dev"]["better"] and (c["status"], c.get("reason")) == ("gate_failed", "no held-out cases")
    assert status(learner, res["id"]) == "proposed"


def test_candidates_for_filters_by_computed_weight_not_confidence(learner, monkeypatch):
    """issue 13 (#2): climb candidacy keys on the noisy-or weight label, not the stale `confidence` field. A
    low-weight lesson whose stored `confidence` says otherwise must not slip through as a climb candidate."""
    from exo import taste
    from exo.consolidate import load_state

    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    edit = correction(rt, "plain status lines please", "s1")
    res = propose(rt, evidence=[edit["id"]])
    assert load_state(rt.ledger).proposals[res["id"]].confidence != "low"

    # Force the computed weight label to "low" (as a genuinely low-weight lesson computes). The old
    # `p.confidence == "low"` check would have admitted it; the fix keys on the label.
    monkeypatch.setattr(taste, "label", lambda w, *a, **k: "low")
    assert all(c["proposal"] != res["id"] for c in candidates_for(learner, 10))

    # Control: with the real (medium) label it remains a candidate.
    monkeypatch.setattr(taste, "label", lambda w, *a, **k: "medium")
    assert any(c["proposal"] == res["id"] for c in candidates_for(learner, 10))


def test_candidates_for_drops_a_lesson_whose_evidence_has_decayed(learner, monkeypatch):
    """Nothing mocked but the clock: a year after its one correction a preference has decayed to a low weight
    (half-life 30 days), so it is no longer a climb candidate though its stored `confidence` never changed."""
    from datetime import datetime, timedelta, timezone

    from exo.consolidate import load_state

    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    res = propose(rt, evidence=[correction(rt, "plain status lines please", "s1")["id"]])
    assert any(c["proposal"] == res["id"] for c in candidates_for(learner, 10))   # fresh: medium weight
    later = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    monkeypatch.setattr("exo.learn.climb._now", lambda: later, raising=False)
    assert load_state(rt.ledger).proposals[res["id"]].confidence != "low"
    assert all(c["proposal"] != res["id"] for c in candidates_for(learner, 10))


def test_nightly_drop_candidate_is_not_held_back_by_its_own_evidence(learner, monkeypatch):
    """A harmful auto-promoted lesson is judged on the cases it was not learned from. Before, the replays of the
    verdicts it cites, and the preference pair its acceptance wrote, sat in the held-out set and kept it."""
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model(base_good=True)
    ev = [_replay(rt, 100 + k, f"Status ok {100 + k} :)", f"s{k}")["id"] for k in range(3)]
    res = propose(rt, evidence=ev, wants="emoticons in status lines", avoid="plain text",
                  claim="The principal wants emoticons in status lines, not plain text.")
    assert apply_policy(learner)[0].action == "auto_promoted"
    _held_out(monkeypatch, set(ev), also={"q0", "q1"})
    (drop,) = candidates_for(learner, 10)
    assert drop["id"] == f"drop:{res['id']}" and sorted(drop["exclude"]) == sorted(ev)
    out = run(learner)
    assert out["climb"]["accepted"] == [f"drop:{res['id']}"]
    assert status(learner, res["id"]) == "rejected" and current_config(learner)["rules"] == []
