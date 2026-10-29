import errno
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from exo import consolidate as C
from exo import taste
from exo.ledger import Ledger

AUTHOR = "exo-learn"


@pytest.fixture
def led(template_store, signer):
    ledger = Ledger(template_store.ledger_path, signer)
    yield ledger
    ledger.close()


def edit(led, said, session="s1"):
    out = led.append("utterance", {"text": "old output", "role": "assistant"}, "agent", session=session)
    return led.append("verdict", {"verdict": "edit", "target": out["id"], "text": said}, "principal", session=session)


def item(ev, ptype="taste", **kw):
    base = {"type": ptype, "claim": "The principal wants tables, not bullet lists, because they scan faster.",
            "wants": "tables", "avoid": "bullet lists", "because": "they scan faster", "domain": "mind",
            "evidence": [ev["id"]]}
    base.update(kw)
    return base


@pytest.mark.parametrize("text,want,avoid", [
    ("Important correction: use tables for the summary, NOT bullet lists.", "use tables for the summary", "bullet lists"),
    ("Not a PR, a draft issue.", "a draft issue", "a pr"),
    ("Actually, send it to the team channel.", "send it to the team channel", None),
    ("Important correction: the deploy target is staging, NOT prod.", "the deploy target is staging", "prod"),
])
def test_parse_the_missed_correction_shapes(text, want, avoid):
    w, a, _ = C.parse_instruction(text)
    assert (w, a) == (want, avoid)


def test_a_correction_with_the_missed_shape_becomes_a_proposal(template_store, led):
    edit(led, "Important correction: use tables for the summary, NOT bullet lists.")
    res = C.consolidate_run(template_store, led, author=AUTHOR)
    (p,) = [e["body"] for e in led.iter(kind="belief_proposed")]
    assert len(res.proposals) == 1 and "bullet lists" in p["claim"] and p["avoid"] == "bullet lists"


def test_typed_taste_and_fact_use_the_reviewers_words(template_store, led):
    e1, e2 = edit(led, "tables please"), edit(led, "release is from main", "s2")
    out = C.propose_typed(template_store, led, [item(e1), item(e2, "fact", claim="Releases ship from main.",
                                                          wants="releases ship from main", avoid=None,
                                                          because="they said so")], author=AUTHOR)
    assert [o["status"] for o in out] == ["proposed", "proposed"]
    bodies = {e["body"]["ptype"]: e["body"] for e in led.iter(kind="belief_proposed")}
    assert bodies["taste"]["claim"].startswith("The principal wants tables") and bodies["taste"]["type"] == "preference"
    assert bodies["fact"]["type"] == "fact" and bodies["fact"]["claim"] == "Releases ship from main."
    assert bodies["taste"]["evidence"][0]["id"] == e1["id"]
    for b in bodies.values():
        assert (template_store.root / "proposals" / f"{b['proposal_id']}.json").exists()
    # a fact proposal can be accepted into a valid node
    r = C.proposal_accept(template_store, led, bodies["fact"]["proposal_id"])
    assert template_store.nodes[r["node_id"]]["type"] == "fact"


def test_one_event_can_teach_two_lessons_but_not_the_same_one_twice(template_store, led):
    e = edit(led, "tables, and the release is from main")
    out = C.propose_typed(template_store, led, [item(e), item(e, "fact", claim="Releases ship from main.",
                                                            wants="releases ship from main", avoid=None)], author=AUTHOR)
    assert [o["status"] for o in out] == ["proposed", "proposed"]
    again = C.propose_typed(template_store, led, [item(e)], author=AUTHOR)
    assert again[0]["status"] != "proposed" and len(list(led.iter(kind="belief_proposed"))) == 2


def test_evidence_must_exist_and_types_are_checked(template_store, led):
    e = edit(led, "tables please")
    out = C.propose_typed(template_store, led, [item(e, evidence=["0" * 64]), item(e, "weird"), item(e, evidence=[])],
                          author=AUTHOR)
    assert [o["status"] for o in out] == ["skipped"] * 3 and list(led.iter(kind="belief_proposed")) == []


def test_same_lesson_from_a_second_event_reinforces(template_store, led):
    e1, e2 = edit(led, "tables please"), edit(led, "tables again", "s2")
    C.propose_typed(template_store, led, [item(e1)], author=AUTHOR)
    out = C.propose_typed(template_store, led, [item(e2)], author=AUTHOR)
    assert out[0]["status"] == "reinforced" and len(list(led.iter(kind="belief_proposed"))) == 1
    st = C.load_state(led)
    assert len(next(iter(st.beliefs.values())).evidence) == 2


def test_skill_proposal_has_a_diff_and_accepting_writes_the_skill(template_store, led):
    e = edit(led, "write status plain")
    (res,) = C.propose_typed(template_store, led, [{
        "type": "skill", "skill": "Status Update", "patch": "Write one plain line.", "claim": "Status is plain.",
        "wants": "plain status", "evidence": [e["id"]], "critic_note": "maybe a one-off"}], author=AUTHOR)
    body = next(led.iter(kind="belief_proposed"))["body"]
    assert body["ptype"] == "skill" and body["target"] == "status-update" and body["critic_note"] == "maybe a one-off"
    diff = (template_store.root / "proposals" / f"{res['id']}.diff").read_text()
    assert "+## Lesson" in diff and "Write one plain line." in diff
    r = C.proposal_accept(template_store, led, res["id"])
    skill = (template_store.root / "skills" / "status-update" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: status-update") and "Write one plain line." in skill and r["action"] == "create"
    assert not template_store.lint().errors or all("skills" not in x for x in template_store.lint().errors)


def test_question_proposal_goes_to_the_queue_and_cannot_be_accepted(template_store, led):
    e = edit(led, "hmm")
    (res,) = C.propose_typed(template_store, led, [{"type": "question", "question": "Do status lines get links?",
                                                    "claim": "links?", "evidence": [e["id"]]}], author=AUTHOR)
    assert res["status"] == "held"
    assert "Do status lines get links?" in (template_store.root / "queues" / "questions.jsonl").read_text()
    with pytest.raises(C.ConsolidateError):
        C.proposal_accept(template_store, led, res["id"])


def test_revert_restores_an_updated_node(template_store, led):
    e1, e2 = edit(led, "tables please"), edit(led, "use a table for numbers", "s2")
    (a,) = C.propose_typed(template_store, led, [item(e1)], author=AUTHOR)
    r1 = C.proposal_accept(template_store, led, a["id"])
    path = template_store.nodes_dir / f"{r1['node_id']}.md"
    before = path.read_text()
    (b,) = C.propose_typed(template_store, led, [item(e2, wants="tables for numbers", avoid="prose for numbers",
                                                      claim="The principal wants tables for numbers, not prose.")],
                           author=AUTHOR)
    r2 = C.proposal_accept(template_store, led, b["id"])
    if r2["node_id"] == r1["node_id"]:
        assert path.read_text() != before
        out = C.proposal_revert(template_store, led, b["id"], "that was a mistake", follow_up=False)
        assert out["file"] == "restored" and path.read_text() == before
    else:
        out = C.proposal_revert(template_store, led, b["id"], "that was a mistake", follow_up=False)
        assert out["file"] == "restored" and not (template_store.nodes_dir / f"{r2['node_id']}.md").exists()
    note = [x for x in led.iter(kind="note") if x["body"].get("reverted")][-1]
    assert note["body"]["reason"] == "that was a mistake"
    assert C.proposals_list(template_store, led, include_decided=True)  # still readable after a revert


def test_revert_cli(template_store, led, capsys, monkeypatch):
    import argparse
    e = edit(led, "tables please")
    (a,) = C.propose_typed(template_store, led, [item(e)], author=AUTHOR)
    C.proposal_accept(template_store, led, a["id"])
    a_ns = argparse.Namespace(action="revert", id=a["id"], reason="no longer true", all=False, seat="principal", key=None)
    monkeypatch.setattr(C, "_open_ledger", lambda a, store, write: led)
    monkeypatch.setattr(led, "close", lambda: None)
    assert C.cmd_proposals(a_ns, template_store) == 0
    assert "reverted" in capsys.readouterr().out


# --- taste: export and per-domain half-life -------------------------------------------------------

def test_export_taste_md_groups_and_hides_low_confidence():
    text = taste.export_taste_md([("mind", "Wants tables, not bullets.", 0.82), ("mind", "Wants short replies", 0.50),
                                  ("work", "Wants small PRs", 0.9), ("work", "Wants noise", 0.2)])
    assert text.splitlines() == ["# Taste", "", "## mind", "- Wants tables, not bullets. confidence: 0.82",
                                 "- Wants short replies. confidence: 0.50", "", "## work",
                                 "- Wants small PRs. confidence: 0.90"]
    assert "noise" not in text
    assert "noise" in taste.export_taste_md([("w", "Wants noise", 0.2)], min_label="low")


def test_per_domain_half_life_from_policy():
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    old = (now - timedelta(days=14)).isoformat()
    b = taste.Belief("p", frozenset(), "preference", "c", old, [taste.Evidence("e", old, 1.0)], domain="mind")
    slow = b.weight(now, {"domain:mind": 100.0})
    fast = b.weight(now, {"domain:mind": 7.0})
    plain = b.weight(now, {})
    assert fast == pytest.approx(0.25, abs=0.01) and slow > plain > fast
    rule = taste.Belief("p", frozenset(), "rule", "c", old, [taste.Evidence("e", old, 1.0)], domain="mind")
    assert rule.weight(now, {"domain:mind": 7.0}) == pytest.approx(1.0)       # rulings never decay


def test_domain_half_life_applies_to_new_proposals(template_store, led):
    (template_store.root / "policies" / "consolidate.yaml").write_text("half_life_days:\n  domain:\n    mind: 1\n")
    e = edit(led, "tables please")
    (res,) = C.propose_typed(template_store, led, [item(e)], author=AUTHOR,
                             now=(datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    body = next(led.iter(kind="belief_proposed"))["body"]
    assert body["half_life"] == 1.0 and body["confidence"] == "low"


@pytest.mark.parametrize("where", ["ledger", "write"])
def test_a_failed_skill_accept_restores_the_skill_and_a_retry_adds_the_lesson_once(template_store, led, monkeypatch,
                                                                                   where):
    path = template_store.root / "skills" / "status-update" / "SKILL.md"
    e1, e2 = edit(led, "write status plain"), edit(led, "status has a link", "s2")
    (a,) = C.propose_typed(template_store, led, [{"type": "skill", "skill": "Status Update", "patch": "Write one plain line.",
                                                  "claim": "Status is plain.", "evidence": [e1["id"]]}], author=AUTHOR)
    C.proposal_accept(template_store, led, a["id"])
    before = path.read_text()
    (b,) = C.propose_typed(template_store, led, [{"type": "skill", "skill": "Status Update", "patch": "Add the link.",
                                                  "claim": "Status has a link.", "evidence": [e2["id"]]}], author=AUTHOR)
    calls = []
    if where == "ledger":
        real = led.append

        def append(kind, *args, **kw):   # the ledger write after the skill file fails once
            if kind == "belief_accepted" and not calls:
                calls.append(kind)
                raise OSError("ledger is read-only")
            return real(kind, *args, **kw)
        monkeypatch.setattr(led, "append", append)
    else:
        real_write = Path.write_text

        def write_text(self, data, *a, **kw):   # the skill file write stops halfway on a full disk, once
            if self == path and not calls:
                calls.append(1)
                real_write(self, data[: len(data) // 2], *a, **kw)
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(self, data, *a, **kw)
        monkeypatch.setattr(Path, "write_text", write_text)
    with pytest.raises(OSError):
        C.proposal_accept(template_store, led, b["id"])
    assert path.read_text() == before
    C.proposal_accept(template_store, led, b["id"])
    assert path.read_text().count("Add the link.") == 1
