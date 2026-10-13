import re

import pytest

from exo.consolidate import load_state, proposals_list
from exo.learn import export_taste, learned_since, revert
from exo.learn.promote import accepted_rules, apply_policy

from helpers import correction, propose, ruling, scripted_model, write_qa


def status(lr, pid):
    return next((v.status for v in proposals_list(lr.runtime.store, lr.runtime.ledger, include_decided=True)
                 if v.id == pid), None)


def test_rule1_a_ruling_promotes_now(learner):
    rt = learner.runtime
    r = ruling(rt, "From now on, status lines are plain, not decorated, because they get pasted into tickets.")
    res = propose(rt, evidence=[r["id"]])
    (d,) = apply_policy(learner)
    assert d.id == res["id"] and d.action == "promoted" and d.rule == 1
    assert status(learner, res["id"]) == "accepted"
    assert [t for t in rt.ledger.iter(kind="note") if t["body"]["type"] == "learn_promotion"][0]["body"]["auto"] is False
    assert "plain status lines" in (rt.root / "taste.md").read_text()


def test_rule2_guarded_never_auto_promotes_and_asks_once(learner):
    rt = learner.runtime
    ev = [correction(rt, "publish only after I say so", "s1")["id"], correction(rt, "never publish early", "s2")["id"]]
    res = propose(rt, evidence=ev, claim="The principal wants publishing only after approval, not early posts.",
                  wants="publishing only after approval", avoid="early posts", guarded=True)
    for _ in range(2):
        (d,) = apply_policy(learner)
        assert d.action == "question" and d.rule == 2
    assert status(learner, res["id"]) == "proposed"
    qs = (rt.root / "queues" / "questions.jsonl").read_text()
    assert qs.count(f"learn:{res['id']}") == 1


def test_rule3_conflict_with_a_guarded_belief_stays_held(learner):
    rt = learner.runtime
    g = ruling(rt, "Always ask before you deploy.")
    older = propose(rt, evidence=[g["id"]], wants="asking before deploying", avoid="deploying without asking")
    assert apply_policy(learner)[0].action == "promoted"
    c = correction(rt, "Stop asking, just deploy.", "s9")
    res = propose(rt, evidence=[c["id"]], wants="deploying without asking", avoid="asking before deploying")
    assert res["status"] == "held"
    (d,) = apply_policy(learner)
    assert d.id == res["id"] and d.action == "held" and d.rule == 3
    assert status(learner, older["id"]) == "accepted" and status(learner, res["id"]) == "held"
    q = [e for e in rt.ledger.iter(kind="question") if e["body"].get("type") == "belief_conflict"]
    assert len(q) == 1 and "Default 1" in q[0]["body"]["text"]   # guarded: keep the older one


def test_rule4_two_sessions_high_weight_auto_promotes(learner):
    rt = learner.runtime
    ev = [correction(rt, "plain status lines please", "s1")["id"], correction(rt, "No emoticons in status", "s2")["id"]]
    res = propose(rt, evidence=ev)
    (d,) = apply_policy(learner)
    assert d.action == "auto_promoted" and d.rule == 4
    assert status(learner, res["id"]) == "accepted"
    lines = learned_since(learner.engine)
    assert len(lines) == 1 and "auto-promoted" in lines[0]


def test_rule4_needs_two_sessions_and_no_objection(learner):
    rt = learner.runtime
    ev = [correction(rt, "plain status lines please", "s1")["id"], correction(rt, "No emoticons in status", "s1")["id"]]
    res = propose(rt, evidence=ev)
    (d,) = apply_policy(learner)
    assert d.action == "wait" and d.rule == 6 and status(learner, res["id"]) == "proposed"
    ev2 = [correction(rt, "tables please", "s3")["id"], correction(rt, "tables, not bullets", "s4")["id"]]
    res2 = propose(rt, evidence=ev2, wants="tables", avoid="bullets", critic_note="looks like a one-off")
    d2 = next(d for d in apply_policy(learner) if d.id == res2["id"])
    assert d2.action == "wait" and status(learner, res2["id"]) == "proposed"


def test_rule5_skill_patch_needs_the_gate(learner):
    rt = learner.runtime
    ev = [correction(rt, "write status plain", "s1")["id"], correction(rt, "plain status", "s2")["id"]]
    res = propose(rt, "skill", evidence=ev, skill="status-update", patch="Write plain status lines, always.",
                  claim="Status lines are plain.")
    (d,) = apply_policy(learner)
    assert d.action == "wait_eval" and d.rule == 5
    (d,) = apply_policy(learner, allow_eval=True)              # no held-out cases at all
    assert d.action == "gate_failed" and "no held-out" in d.reason
    assert not (rt.root / "skills").exists()

    write_qa(rt.root)
    learner._complete = scripted_model()
    (d,) = apply_policy(learner, allow_eval=True)
    assert d.action == "auto_promoted" and d.rule == 5
    text = (rt.root / "skills" / "status-update" / "SKILL.md").read_text()
    assert "Write plain status lines, always." in text and res["id"] in text
    assert status(learner, res["id"]) == "accepted"


def test_rule5_gate_blocks_a_skill_that_does_not_help(learner):
    rt = learner.runtime
    ev = [correction(rt, "x", "s1")["id"], correction(rt, "y", "s2")["id"]]
    propose(rt, "skill", evidence=ev, skill="noise", patch="Add emoticons everywhere.", claim="Emoticons.")
    write_qa(rt.root)
    learner._complete = scripted_model()
    (d,) = apply_policy(learner, allow_eval=True)
    assert d.action == "gate_failed" and not (rt.root / "skills").exists()


def test_rule6_medium_is_a_hint_and_stale_becomes_a_question(learner):
    rt = learner.runtime
    c = correction(rt, "plain status lines please", "s1")
    res = propose(rt, evidence=[c["id"]])
    (d,) = apply_policy(learner)
    assert d.action == "hint" and d.rule == 6
    (d,) = apply_policy(learner, now="2027-01-01T00:00:00.000000Z")      # months later: stale and low weight
    assert d.action == "question" and d.rule == 6
    assert status(learner, res["id"]) == "proposed"


def test_revert_a_created_node_restores_the_store_and_proposes_the_opposite(learner):
    rt = learner.runtime
    r = ruling(rt, "Use tables, not bullet lists.")
    res = propose(rt, evidence=[r["id"]], wants="tables", avoid="bullet lists")
    apply_policy(learner)
    node = next(p.node_id for p in load_state(rt.ledger).proposals.values())
    assert (rt.store.nodes_dir / f"{node}.md").exists()
    out = revert(learner.engine, res["id"], "No, use bullet lists, not tables, they paste better")
    assert out["reverted"] and out["file"] == "restored" and out["follow_up"]
    assert not (rt.store.nodes_dir / f"{node}.md").exists()
    assert status(learner, res["id"]) == "rejected"
    note = [e for e in rt.ledger.iter(kind="note") if e["body"].get("reverted")][0]
    assert note["body"]["proposal_id"] == res["id"]
    assert accepted_rules(learner) == []
    assert "tables" not in (rt.root / "taste.md").read_text()
    assert status(learner, out["follow_up"]) == "proposed"


def test_revert_an_update_and_a_skill(learner):
    rt = learner.runtime
    write_qa(rt.root)
    learner._complete = scripted_model()
    ev = [correction(rt, "write status plain", "s1")["id"], correction(rt, "plain status", "s2")["id"]]
    res = propose(rt, "skill", evidence=ev, skill="status-update", patch="Write plain status lines, always.",
                  claim="Status lines are plain.")
    apply_policy(learner, allow_eval=True)
    path = rt.root / "skills" / "status-update" / "SKILL.md"
    assert path.exists()
    out = revert(learner.engine, res["id"], "that skill is wrong", )
    assert out["file"] == "restored" and not path.exists()
    # a file that changed since the lesson loses only that lesson's section
    from exo.consolidate import proposal_accept
    r1 = propose(rt, "skill", evidence=ev, skill="other", patch="First lesson.", claim="First.")
    r2 = propose(rt, "skill", evidence=ev, skill="other", patch="Second lesson.", claim="Second.")
    proposal_accept(rt.store, rt.ledger, r1["id"])
    proposal_accept(rt.store, rt.ledger, r2["id"])
    other = rt.root / "skills" / "other" / "SKILL.md"
    assert "First lesson." in other.read_text() and "Second lesson." in other.read_text()
    out = revert(learner.engine, r1["id"], "first lesson was wrong")
    assert out["file"] == "section removed"
    assert "First lesson." not in other.read_text() and "Second lesson." in other.read_text()


def test_revert_needs_an_accepted_lesson_and_a_reason(learner):
    rt = learner.runtime
    c = correction(rt, "plain status lines please", "s1")
    res = propose(rt, evidence=[c["id"]])
    from exo.consolidate import ConsolidateError
    with pytest.raises(ConsolidateError):
        revert(learner.engine, res["id"], "why not")
    with pytest.raises(ConsolidateError):
        revert(learner.engine, res["id"], "")


def test_taste_md_format(learner):
    rt = learner.runtime
    for k, said in enumerate(["Use tables, not bullet lists", "Plain status lines, not emoticons"]):
        r = ruling(rt, said + ".", f"s{k}")
        propose(rt, evidence=[r["id"]], wants=["tables", "plain status lines"][k], avoid=["bullet lists", "emoticons"][k],
                domain="mind")
    apply_policy(learner)
    path = export_taste(learner.engine)
    text = path.read_text()
    assert text.startswith("# Taste") and "## mind" in text
    lines = [l for l in text.splitlines() if l.startswith("- ")]
    assert len(lines) == 2 and all(re.fullmatch(r"- .+\. confidence: \d\.\d\d", l) for l in lines)
