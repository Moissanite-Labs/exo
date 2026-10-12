import argparse
import json

import pytest

from exo import consolidate as C
from exo import crypto
from exo.ledger import Ledger

AUTHOR = "exo-consolidate"


@pytest.fixture
def led(template_store, signer):
    ledger = Ledger(template_store.ledger_path, signer)
    yield ledger
    ledger.close()


def say(led, text, session="s1"):
    return led.append("utterance", {"text": text, "role": "user"}, "principal", session=session)


def agent(led, text, session="s1"):
    return led.append("utterance", {"text": text, "role": "assistant"}, "agent", session=session)


def run(store, led, **kw):
    return C.consolidate_run(store, led, author=AUTHOR, **kw)


def proposed(led):
    return [e for e in led.iter(kind="belief_proposed")]


@pytest.fixture
def corrections(led):
    """Fixture ledger: two corrections on agent output and one ruling."""
    out1 = agent(led, "Here is the PR comment. Signed, proteus")
    v1 = led.append("verdict", {"verdict": "edit", "target": out1["id"],
                                "text": "No, don't sign external comments with agent names.",
                                "cues": ["no", "dont"]}, "principal", session="s1")
    out2 = agent(led, "Ready for your approval.")
    v2 = led.append("verdict", {"verdict": "should_have_been", "target": out2["id"],
                                "wanted": "one line per item with the review link and a default",
                                "reason": "I cannot approve what I cannot see"}, "principal", session="s1")
    r1 = led.append("ruling", {"text": "From now on, use tables, not bullet lists, because they scan faster.",
                               "cues": ["from-now-on"]}, "principal", session="s1")
    led.append("note", {"type": "consolidate_pending", "messages": 6}, "agent", session="s1")
    return {"out1": out1, "v1": v1, "out2": out2, "v2": v2, "r1": r1}


def test_corrections_become_positive_proposals_with_evidence(template_store, led, corrections):
    res = run(template_store, led)
    assert len(res.proposals) == 3 and res.pending_sessions == 1
    by_ev = {e["body"]["evidence"][0]["id"]: e for e in proposed(led)}

    sign = by_ev[corrections["v1"]["id"]]["body"]
    assert sign["claim"].startswith("The principal wants work done without signing external comments")
    assert ", not output like " in sign["claim"] and ", because " in sign["claim"]
    assert sign["type"] == "preference" and sign["domain"] == "relationship"
    assert sign["implication"] and "public" in sign["implication"]
    assert sign["pair"]["rejected"] == "Here is the PR comment. Signed, proteus"
    assert sign["pair"]["chosen"] == sign["claim"]
    assert sign["pair"]["evidence"] == [corrections["v1"]["id"]]
    assert sign["confidence"] in ("high", "medium", "low") and 0 < sign["weight"] <= 1

    review = by_ev[corrections["v2"]["id"]]["body"]
    assert review["claim"] == ("The principal wants one line per item with the review link and a default, "
                               'not output like "Ready for your approval.", because I cannot approve what I cannot see.')

    rule = by_ev[corrections["r1"]["id"]]["body"]
    assert rule["claim"] == "The principal wants the agent to use tables, not bullet lists, because they scan faster."
    assert rule["type"] == "rule" and rule["domain"] == "mind"

    for ev in proposed(led):
        b = ev["body"]
        assert not C.re.match(r"The principal wants (not|no|don'?t|never)\b", b["claim"])
        assert all(e["id"] in ev["refs"] for e in b["evidence"])
        draft = template_store.root / "proposals" / f"{b['proposal_id']}.md"
        assert draft.is_file() and draft.read_text().startswith("---\nid: ")
        assert (template_store.root / "proposals" / f"{b['proposal_id']}.json").is_file()
    assert len(res.pairs) == 3


def test_accept_writes_node_that_passes_lint(template_store, led, corrections):
    run(template_store, led)
    pid = next(e["body"]["proposal_id"] for e in proposed(led)
               if e["body"]["evidence"][0]["id"] == corrections["v1"]["id"])
    out = C.proposal_accept(template_store, led, pid, author="principal")
    node = template_store.nodes_dir / f"{out['node_id']}.md"
    assert node.is_file() and out["action"] == "create"
    template_store.reload()
    report = template_store.lint()
    assert report.errors == []
    n = template_store.get(out["node_id"])
    assert n["type"] == "preference" and n["sources"][0]["ref"].startswith("event:")
    assert "**Claim.** The principal wants" in n["_body"]
    acc = list(led.iter(kind="belief_accepted"))
    assert acc[-1]["body"]["proposal_id"] == pid and acc[-1]["refs"]
    pairs = (template_store.root / "training/preference-pairs.jsonl").read_text().splitlines()
    assert json.loads(pairs[-1])["node"] == out["node_id"]
    assert (template_store.root / "proposals/archive" / f"{pid}.md").is_file()
    assert out["node_id"] in (template_store.root / "INDEX.md").read_text()
    views = {v.id: v.status for v in C.proposals_list(template_store, led, include_decided=True)}
    assert views[pid] == "accepted"
    with pytest.raises(C.ConsolidateError):
        C.proposal_accept(template_store, led, pid)


def test_update_existing_node_is_a_diff_then_an_edit(template_store, led):
    node = template_store.nodes_dir / "mind" / "formatting.md"
    node.write_text("""---
id: mind/formatting
title: Formatting of tables and bullet lists
type: preference
domain: mind
summary: How the principal wants tables and bullet lists used in replies.
load_when: Formatting a reply.
confidence: low
stability: durable
valid_from: 2026-01-01
sources:
  - {kind: inferred, ref: "template:example"}
links:
  - {to: person/identity, rel: part-of}
---

Tables and lists.
""")
    template_store.reload()
    before = node.read_text()
    led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")
    run(template_store, led)
    p = proposed(led)[0]["body"]
    assert p["action"] == "update" and p["node_id"] == "mind/formatting"
    diff = (template_store.root / "proposals" / f"{p['proposal_id']}.diff").read_text()
    assert "+++ b/nodes/mind/formatting.md" in diff and "+**Claim.** The principal wants" in diff
    assert node.read_text() == before              # nothing written before review
    C.proposal_accept(template_store, led, p["proposal_id"])
    template_store.reload()
    n = template_store.get("mind/formatting")
    assert len(n["sources"]) == 2 and "Tables and lists." in n["_body"]
    assert template_store.lint().errors == []


def test_reject_records_reason_and_leaves_nodes(template_store, led, corrections):
    run(template_store, led)
    pid = proposed(led)[0]["body"]["proposal_id"]
    nodes_before = sorted(p.name for p in template_store.nodes_dir.rglob("*.md"))
    with pytest.raises(C.ConsolidateError):
        C.proposal_reject(template_store, led, pid, "  ")
    C.proposal_reject(template_store, led, pid, "too narrow", author="principal")
    note = [e for e in led.iter(kind="note") if e["body"].get("type") == "belief_rejected"][-1]
    assert note["body"]["reason"] == "too narrow"
    assert sorted(p.name for p in template_store.nodes_dir.rglob("*.md")) == nodes_before
    assert pid not in {v.id for v in C.proposals_list(template_store, led)}


def test_rule_of_three_across_runs(template_store, led):
    say(led, "Always run the tests before pushing.", "a")
    say(led, "please run the tests before you push", "b")
    res = run(template_store, led)
    assert res.proposals == []
    say(led, "Run tests before pushing, every time.", "c")
    res = run(template_store, led)
    assert len(res.proposals) == 1
    b = proposed(led)[0]["body"]
    assert b["kind"] == "rule_of_three" and b["type"] == "rule" and len(b["evidence"]) == 3
    assert b["claim"].startswith("The principal wants the agent to run the tests before pushing")
    assert "Repeated 3 times" in b["implication"]
    # A fourth repeat reinforces nothing new: the cluster is already proposed.
    say(led, "run the tests before pushing", "d")
    assert run(template_store, led).proposals == []


def test_rule_of_three_skips_encoded_and_rebuilds_cache(template_store, led):
    led.append("ruling", {"text": "Always run the tests before pushing."}, "principal")
    run(template_store, led)
    (template_store.root / "proposals" / "state.json").unlink()   # cache lost: rebuilt from the ledger
    for s in "xyz":
        say(led, "run the tests before pushing", s)
    res = run(template_store, led)
    assert res.proposals == []                                    # already encoded by the ruling


def test_repeat_correction_reinforces_and_raises_weight(template_store, led):
    out = agent(led, "A long answer")
    led.append("verdict", {"verdict": "edit", "target": out["id"], "text": "Keep replies under five lines."},
               "principal")
    run(template_store, led)
    w1 = C.proposals_list(template_store, led)[0].weight
    out = agent(led, "Another long answer")
    led.append("verdict", {"verdict": "edit", "target": out["id"], "text": "keep replies under five lines again"},
               "principal")
    res = run(template_store, led)
    assert res.proposals == [] and len(res.reinforced) == 1
    v = C.proposals_list(template_store, led)[0]
    assert v.weight > w1 and len(v.evidence) == 2
    # Decay: 120 days later without reinforcement the taste belief is weak.
    old = C.proposals_list(template_store, led, now="2027-02-15T00:00:00+00:00")[0]
    assert old.weight < 0.1 and old.confidence == "low"


def test_conflict_on_guarded_belief_raises_question(template_store, led):
    led.append("ruling", {"text": "Never sign external comments with agent names."}, "principal")
    run(template_store, led)
    newer = led.append("ruling", {"text": "Always sign external comments with agent names."}, "principal")
    res = run(template_store, led)
    assert len(res.questions) == 1 and len(res.proposals) == 1
    q = led.get(res.questions[0])
    assert q["kind"] == "question" and q["body"]["type"] == "belief_conflict"
    assert q["body"]["default"] == 1 and "1) Keep:" in q["body"]["text"] and newer["id"] in q["refs"]
    held = {v.id: v for v in C.proposals_list(template_store, led)}[res.proposals[0]]
    assert held.status == "held" and held.question == res.questions[0]
    # Nothing was overwritten: the older proposal is still open.
    assert sum(1 for v in C.proposals_list(template_store, led) if v.status == "proposed") == 1
    led.append("answer", {"text": "2", "choice": 2}, "principal", refs=[res.questions[0]])
    res2 = run(template_store, led)
    assert res2.released == [held.id]
    assert {v.id: v.status for v in C.proposals_list(template_store, led)}[held.id] == "proposed"


def test_conflict_newer_wins_is_still_a_reviewed_supersede(template_store, led):
    led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")
    run(template_store, led)
    res = run(template_store, led)
    led.append("ruling", {"text": "Use bullet lists, not tables."}, "principal")
    res = run(template_store, led)
    assert res.questions == [] and len(res.proposals) == 1
    b = led.get(next(e["id"] for e in proposed(led) if e["body"]["proposal_id"] == res.proposals[0]))["body"]
    assert b["supersedes"] and b["status"] == "proposed"
    C.proposal_accept(template_store, led, res.proposals[0])
    statuses = {v.id: v.status for v in C.proposals_list(template_store, led, include_decided=True)}
    assert statuses[b["supersedes"]] == "superseded"


def test_conflict_policy_ask_always_asks(template_store, led):
    (template_store.root / "policies" / "consolidate.yaml").write_text("conflict_rule: ask\n")
    led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")
    run(template_store, led)
    led.append("ruling", {"text": "Use bullet lists, not tables."}, "principal")
    res = run(template_store, led)
    assert len(res.questions) == 1


def test_idempotent_by_watermark(template_store, led, corrections):
    first = run(template_store, led)
    n = len(led)
    second = run(template_store, led)
    assert second.read == 0 and second.proposals == [] and len(led) == n
    marks = [e for e in led.iter(kind="note") if e["body"].get("type") == "consolidate_run"]
    assert len(marks) == 1 and marks[0]["body"]["upto"] == first.upto
    # Only events after the watermark are read.
    out = agent(led, "Posted it.")
    led.append("verdict", {"verdict": "reject", "target": out["id"], "text": "No, never deploy on Fridays."},
               "principal")
    third = run(template_store, led)
    assert third.read == 2 and len(third.proposals) == 1          # the agent turn and the verdict
    assert len(proposed(led)) == 4


def test_dry_run_writes_nothing(template_store, led, corrections):
    n = len(led)
    res = run(template_store, led, dry_run=True)
    assert len(res.proposals) == 3 and len(led) == n
    assert not (template_store.root / "proposals").exists()


def test_model_reflector_seam_and_fallback(template_store, led, corrections):
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return json.dumps({"wants": "unsigned comments in the principal's own voice", "avoid": None,
                           "because": "agent names leak the team", "implication": "All public text.",
                           "domain": "relationship", "type": "preference", "title": "Unsigned comments"})

    res = run(template_store, led, reflector=C.ModelReflector(fake))
    assert len(calls) == 3 and "positive local form" in calls[0]
    claims = [e["body"]["claim"] for e in proposed(led)]
    assert claims[0].startswith("The principal wants unsigned comments in the principal's own voice")
    assert all(e["body"]["reflector"] == "model" for e in proposed(led))

    bad = C.ModelReflector(lambda p: '{"wants": "don\'t sign", "because": "x"}')
    obs = C.Observation("e", 1, "2026-10-11T00:00:00Z", "correction", "verdict:edit", "Use tables.", "use tables")
    ctx = C.ReflectContext(["mind"], C.DEFAULT_POLICY, "2026-10-11")
    assert bad.reflect(obs, ctx).reflector == "rules"
    assert C.ModelReflector(lambda p: "not json").reflect(obs, ctx).reflector == "rules"
    assert isinstance(C.RuleReflector(), C.Reflector) and res.proposals


def test_parse_instruction_forms():
    assert C.parse_instruction("Use tables, not bullet lists, because they scan faster.") == \
        ("use tables", "bullet lists", "they scan faster")
    assert C.parse_instruction("No, don't sign comments.") == (None, "sign comments", None)
    assert C.parse_instruction("Instead of a new comment, edit the original") == ("edit the original",
                                                                                 "a new comment", None)
    assert C.parse_instruction("too long. keep replies under five lines")[0] == "keep replies under five lines"
    assert C.parse_instruction("way too long")[0] == "shorter output"
    w, a, _ = C.parse_instruction("dont give orders in repos we dont own. language should be plain, not forceful")
    assert w == "language that is plain" and a.startswith("give orders")


def test_cli_hook(template_store, led, corrections, tmp_path):
    kp = tmp_path / "keys" / "exo-consolidate.key"
    crypto.save_key(crypto.keygen(), kp)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--principal")
    common.add_argument("--root")
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd")
    C.register(sp, common)
    led.close()
    a = ap.parse_args(["consolidate", "--root", str(template_store.root), "--key", str(kp)])
    assert C.cmd_consolidate(a, template_store) == 0
    a = ap.parse_args(["proposals", "list", "--root", str(template_store.root)])
    assert C.cmd_proposals(a, template_store) == 0
    with Ledger(template_store.ledger_path) as ro:
        pid = C.proposals_list(template_store, ro)[0].id
        assert ro.verify()["ok"]
    a = ap.parse_args(["proposals", "accept", pid, "--root", str(template_store.root), "--key", str(kp)])
    assert C.cmd_proposals(a, template_store) == 0
    template_store.reload()
    assert template_store.lint().errors == []


def test_proposal_ids_that_look_like_phone_numbers_do_not_break_lint(template_store, led, corrections):
    """Proposal ids are random-looking hex; 11+ digits in a row trips the store's phone LEAKS pattern.
    Regression for a flaky accept (~1 run in 100): node text must never carry such a run."""
    import dataclasses
    from exo.store import LEAKS
    run(template_store, led)
    p = next(iter(C.load_state(led).proposals.values()))
    digits = "p-361364896092"
    for pid in (digits, "p-36136489609a", "p-123456789012"):
        q = dataclasses.replace(p, id=pid, supersedes=digits)
        for text in (C._render_body(q, f"T (2026-10-12, {C.proposal_ref(q.id)})"),
                     C._render_body(q), C.render_new_node(q)):
            assert not any(pat.search(text) for pat in LEAKS), text
    assert C.proposal_ref("p-1e0ab9625a43") == "p-1e0ab9625a43"
    assert C.proposal_ref(digits) == "p-3613648960"
