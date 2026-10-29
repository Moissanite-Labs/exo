import json
import re

import pytest

from exo import eval as E
from exo.consolidate import consolidate_run, load_state, proposal_accept, proposal_reject, proposals_list
from exo.ingest import ingest
from exo.learn import export_taste, learned_since, revert
from exo.learn.promote import accepted_rules, apply_policy

from helpers import chatgpt_export, correction, propose, ruling, scripted_model, write_qa


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


@pytest.mark.parametrize("ptype,said,wants", [
    ("taste", "For weekly notes, use plain subject lines.", "plain subject lines"),
    ("fact", "The status format is one plain line.", "the status format is one plain line"),
])
def test_rule1_ordinary_principal_lesson_still_promotes(learner, ptype, said, wants):
    rt = learner.runtime
    r = ruling(rt, said)
    res = propose(rt, ptype, evidence=[r["id"]], claim=said, wants=wants, avoid=None)
    (d,) = apply_policy(learner)
    assert (d.action, d.rule) == ("promoted", 1)
    assert status(learner, res["id"]) == "accepted"


def test_rule1_citation_cannot_bypass_skill_gate_even_if_unconfigured(learner):
    rt = learner.runtime
    learner.settings["promote"]["eval_gate_for"] = []
    r = ruling(rt, "For weekly notes, use plain subject lines.")
    res = propose(rt, "skill", evidence=[r["id"]], skill="image-conversion",
                  patch="For image conversions, preserve the original bytes.", claim="Preserve original bytes.")
    (d,) = apply_policy(learner)
    assert (d.action, d.rule) == ("wait_eval", 5)
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == ("gate_failed", 5) and "no held-out" in d.reason
    assert status(learner, res["id"]) == "proposed"
    assert not (rt.root / "skills").exists() and learned_since(learner) == []
    assert rt.ledger.get(r["id"])["body"]["text"] == "For weekly notes, use plain subject lines."


def test_rule1_citation_cannot_bypass_guard_and_asks_once(learner, monkeypatch):
    rt = learner.runtime
    r = ruling(rt, "For weekly notes, use plain subject lines.")
    res = propose(rt, evidence=[r["id"]], guarded=True, claim="Publish financial reports without review.",
                  wants="publishing financial reports without review", avoid="review")
    def unexpected_gate(*args):
        pytest.fail("guarded lessons must ask before evaluation")
    monkeypatch.setattr("exo.learn.climb.gate_proposal", unexpected_gate)
    for allow_eval in (False, True):
        (d,) = apply_policy(learner, allow_eval=allow_eval)
        assert (d.action, d.rule) == ("question", 2)
    assert status(learner, res["id"]) == "proposed"
    assert learned_since(learner) == []
    assert (rt.root / "queues" / "questions.jsonl").read_text().count(f"learn:{res['id']}") == 1
    # Explicit principal acceptance is a separate, trusted path.
    proposal_accept(rt.store, rt.ledger, res["id"], author="principal")
    assert status(learner, res["id"]) == "accepted" and apply_policy(learner) == []


@pytest.mark.parametrize("ptype,setting", [("taste", "high_impact_domains"), ("fact", "high_impact_domains"),
                                         ("taste", "eval_gate_for")])
def test_rule1_configured_gate_still_applies(learner, ptype, setting):
    rt = learner.runtime
    learner.settings["promote"][setting] = ["mind" if setting == "high_impact_domains" else ptype]
    r = ruling(rt, "Write plain status lines.", "s1")
    c = correction(rt, "plain status lines", "s2")
    res = propose(rt, ptype, evidence=[r["id"], c["id"]])
    (d,) = apply_policy(learner)
    assert (d.action, d.rule) == ("wait_eval", 5)
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == ("gate_failed", 5) and "no held-out" in d.reason
    assert status(learner, res["id"]) == "proposed"
    write_qa(rt.root)
    learner._complete = scripted_model()
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == ("auto_promoted", 5)
    assert status(learner, res["id"]) == "accepted"


@pytest.mark.parametrize("patch,action", [("Write plain status lines.", "auto_promoted"),
                                        ("Add emoticons everywhere.", "gate_failed")])
def test_rule1_skill_ruling_obeys_eval_result(learner, patch, action):
    rt = learner.runtime
    r = ruling(rt, patch)
    res = propose(rt, "skill", evidence=[r["id"]], skill="status-update", patch=patch, claim=patch)
    write_qa(rt.root)
    learner._complete = scripted_model()
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == (action, 5)
    assert status(learner, res["id"]) == ("accepted" if action == "auto_promoted" else "proposed")
    assert (rt.root / "skills" / "status-update" / "SKILL.md").exists() == (action == "auto_promoted")


@pytest.mark.parametrize("ptype", ["skill", "taste"])
def test_rule1_gated_lesson_cannot_skip_critic_objection(learner, monkeypatch, ptype):
    rt = learner.runtime
    learner.settings["promote"]["high_impact_domains"] = ["mind"]
    r = ruling(rt, "Write plain status lines.")
    res = propose(rt, ptype, evidence=[r["id"]], skill="status-update", patch="Write plain status lines.",
                  critic_note="unsupported generalization")
    def unexpected_gate(*args):
        pytest.fail("critic objections must be resolved before evaluation")
    monkeypatch.setattr("exo.learn.climb.gate_proposal", unexpected_gate)
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == ("wait", 5) and "critic objection" in d.reason
    assert status(learner, res["id"]) == "proposed"


def test_explicit_principal_accept_can_apply_skill_without_auto_promotion(learner):
    rt = learner.runtime
    r = ruling(rt, "For weekly notes, use plain subject lines.")
    res = propose(rt, "skill", evidence=[r["id"]], skill="image-conversion",
                  patch="For image conversions, preserve the original bytes.", claim="Preserve original bytes.")
    assert apply_policy(learner)[0].action == "wait_eval"
    proposal_accept(rt.store, rt.ledger, res["id"], author="principal")
    assert status(learner, res["id"]) == "accepted" and apply_policy(learner) == []
    assert "preserve the original bytes" in (rt.root / "skills" / "image-conversion" / "SKILL.md").read_text()


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
    proposal_accept(rt.store, rt.ledger, older["id"], author="principal")
    c = ruling(rt, "Stop asking, just deploy.", "s9")
    res = propose(rt, evidence=[c["id"]], wants="deploying without asking", avoid="asking before deploying")
    assert res["status"] == "held"
    (d,) = apply_policy(learner)
    assert d.id == res["id"] and d.action == "held" and d.rule == 3
    assert status(learner, older["id"]) == "accepted" and status(learner, res["id"]) == "held"
    q = [e for e in rt.ledger.iter(kind="question") if e["body"].get("type") == "belief_conflict"]
    assert len(q) == 1 and "Default 1" in q[0]["body"]["text"]   # guarded: keep the older one


def test_rule3_a_rejected_belief_cannot_route_a_ruling_past_a_guarded_conflict(learner):
    # #861: the repeated ruling matches the rejected p1 best, but it contradicts the accepted, guarded p2.
    # It must be held on a question (rule 3), not promoted at once by rule 1 beside the belief it contradicts.
    rt = learner.runtime
    ruling(rt, "Use tables with headers, not bullet lists.")
    (p1,) = consolidate_run(rt.store, rt.ledger, author="exo-learn").proposals
    proposal_reject(rt.store, rt.ledger, p1, "Not a standing preference", author="principal")
    ruling(rt, "Use bullet lists, not tables, in public GitHub comments.")
    (p2,) = consolidate_run(rt.store, rt.ledger, author="exo-learn").proposals
    proposal_accept(rt.store, rt.ledger, p2, author="principal")
    ruling(rt, "Use tables with headers, not bullet lists.")
    (p3,) = consolidate_run(rt.store, rt.ledger, author="exo-learn").proposals
    (d,) = apply_policy(learner)
    assert d.id == p3 and d.action == "held" and d.rule == 3
    assert [status(learner, p) for p in (p1, p2, p3)] == ["rejected", "accepted", "held"]


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


TABLES = ["Answer with a table, not bullet points.", "please answer with a table, not bullet points",
          "Answer with a table rather than bullet points, every time."]


def test_rule4_imported_history_alone_waits_for_review(learner, tmp_path):
    """The first import is a batch the principal reviews: repeats found only in `exo ingest` history, here from
    2023, never auto-promote. Before, they were applied on the next policy run with no review."""
    rt = learner.runtime
    ingest("chatgpt", chatgpt_export(tmp_path / "conversations.json", TABLES), rt.ledger, "principal")
    (pid,) = consolidate_run(rt.store, rt.ledger, author="exo-learn").proposals
    (d,) = apply_policy(learner)
    assert (d.id, d.action, d.rule) == (pid, "wait", 4) and "imported" in d.reason
    assert status(learner, pid) == "proposed" and learned_since(learner.engine) == []


def test_rule4_imported_history_with_a_live_repeat_auto_promotes(learner, tmp_path):
    rt = learner.runtime
    ingest("chatgpt", chatgpt_export(tmp_path / "conversations.json", TABLES[:2]), rt.ledger, "principal")
    rt.ledger.append("utterance", {"text": TABLES[2], "role": "user"}, "principal", session="live")
    (pid,) = consolidate_run(rt.store, rt.ledger, author="exo-learn").proposals
    (d,) = apply_policy(learner)
    assert (d.id, d.action, d.rule) == (pid, "auto_promoted", 4)
    assert status(learner, pid) == "accepted"


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


def test_rule5_agent_verdicts_are_not_held_out_cases(learner):
    """D-040: a peer's question and the agent's exo_should_have_been verdict on its reply are not the principal's,
    so they cannot become the held-out cases that let a skill through."""
    rt = learner.runtime
    ev = [correction(rt, "write status plain", "s1")["id"], correction(rt, "plain status", "s2")["id"]]
    propose(rt, "skill", evidence=ev, skill="status-update", patch="Write plain status lines, always.",
            claim="Status lines are plain.")
    learner._complete = scripted_model()
    for k in range(4):
        rt.ledger.append("utterance", {"text": f"Write status line {k}", "role": "user"}, "peer:other", session=f"p{k}")
        a = rt.ledger.append("utterance", {"text": f"Status fine {k}", "role": "assistant"}, "agent", session=f"p{k}")
        rt.ledger.append("verdict", {"verdict": "should_have_been", "target": a["id"], "wanted": f"Status ok {k}"},
                         "agent", session=f"p{k}")
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == ("gate_failed", 5) and "no held-out" in d.reason
    assert not (rt.root / "skills").exists()


@pytest.mark.parametrize("kind", ["replay", "pair"])
def test_rule5_skill_gate_ignores_its_own_evidence(learner, monkeypatch, kind):
    """The case a lesson was learned from is not held out from it: re-scoring it proves nothing."""
    rt = learner.runtime
    rt.ledger.append("utterance", {"text": "Write status line 7", "role": "user"}, "principal", session="s1")
    out = rt.ledger.append("utterance", {"text": "Status fine 7", "role": "assistant"}, "agent", session="s1")
    edit = rt.ledger.append("verdict", {"verdict": "edit", "target": out["id"], "chosen": "Status ok 7"}, "principal",
                            session="s1")
    pairs = rt.root / "training" / "preference-pairs.jsonl"
    pairs.parent.mkdir(parents=True, exist_ok=True)
    pairs.write_text(json.dumps({"id": "pp-own", "context": "Write status line 7", "rejected": "Status fine 7",
                                 "chosen": "Status ok 7", "evidence": [edit["id"]]}) + "\n")
    res = propose(rt, "skill", evidence=[edit["id"]], skill="status-update", patch="Write plain status lines.",
                  claim="Status lines are plain.")
    learner._complete = scripted_model()
    own = (lambda i: i.get("source") == edit["id"]) if kind == "replay" else (lambda i: i["id"] == "pp-own")
    monkeypatch.setattr(E, "split", lambda items, **kw: {"train": [], "dev": [i for i in items if not own(i)],
                                                         "held": [i for i in items if own(i)]})
    (d,) = apply_policy(learner, allow_eval=True)
    assert (d.action, d.rule) == ("gate_failed", 5) and "no held-out" in d.reason
    assert status(learner, res["id"]) == "proposed" and not (rt.root / "skills").exists()


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
