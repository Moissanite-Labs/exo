import argparse
import errno
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from exo import consolidate as C
from exo import crypto
from exo.ingest import ingest
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


def test_repeated_signal_after_rejection_creates_a_new_proposal(template_store, led):
    led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")
    first = run(template_store, led).proposals[0]
    C.proposal_reject(template_store, led, first, "Not a standing preference", author="principal")
    later = led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")

    result = run(template_store, led)

    assert result.reinforced == []
    assert len(result.proposals) == 1 and result.proposals[0] != first
    views = {v.id: v for v in C.proposals_list(template_store, led, include_decided=True)}
    assert views[first].status == "rejected"
    assert views[result.proposals[0]].status == "proposed"
    assert views[result.proposals[0]].evidence == [later["id"]]
    assert not [e for e in led.iter(kind="note") if e["body"].get("type") == "belief_reinforced"]


def _rejected(store, led, text):
    """A ruling consolidated into a proposal that the principal then rejects."""
    ev = led.append("ruling", {"text": text}, "principal")
    (pid,) = run(store, led).proposals
    C.proposal_reject(store, led, pid, "Not a standing preference", author="principal")
    return ev, pid


def test_rejected_belief_does_not_hide_an_active_conflicting_one(template_store, led):
    # #861 case 1: the rejected p1 matches the repeat better than the accepted, guarded p2 it contradicts.
    _, p1 = _rejected(template_store, led, "Use tables with headers, not bullet lists.")
    led.append("ruling", {"text": "Use bullet lists, not tables, in public GitHub comments."}, "principal")
    (p2,) = run(template_store, led).proposals
    C.proposal_accept(template_store, led, p2, author="principal")
    led.append("ruling", {"text": "Use tables with headers, not bullet lists."}, "principal")

    result = run(template_store, led)

    assert result.reinforced == [] and len(result.questions) == 1 and len(result.proposals) == 1
    (p3,) = result.proposals
    views = {v.id: v for v in C.proposals_list(template_store, led, include_decided=True)}
    assert views[p3].status == "held" and views[p3].question == result.questions[0]
    assert views[p2].status == "accepted" and views[p1].status == "rejected" and len(views[p1].evidence) == 1
    body = next(e["body"] for e in proposed(led) if e["body"]["proposal_id"] == p3)
    assert body["supersedes"] == p2
    q = next(e["body"] for e in led.iter(kind="question") if e["id"] == result.questions[0])
    assert q["older"] == p2 and q["newer"] == p3 and q["default"] == 1   # guarded: keep the older one


def test_rejected_belief_does_not_hide_an_active_agreeing_one(template_store, led):
    # #861 case 1b: the repeat reinforces the open p2 instead of opening a second, overlapping proposal.
    _, p1 = _rejected(template_store, led, "Use tables with headers, not bullet lists.")
    led.append("ruling", {"text": "Use tables, not bullet lists or long prose."}, "principal")
    (p2,) = run(template_store, led).proposals
    later = led.append("ruling", {"text": "Use tables with headers, not bullet lists."}, "principal")

    result = run(template_store, led)

    assert result.proposals == [] and result.reinforced == [p2]
    views = {v.id: v for v in C.proposals_list(template_store, led, include_decided=True)}
    assert later["id"] in views[p2].evidence and views[p1].status == "rejected" and len(views[p1].evidence) == 1


def test_repeated_signal_reinforces_an_accepted_belief(template_store, led):
    led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")
    (p1,) = run(template_store, led).proposals
    C.proposal_accept(template_store, led, p1, author="principal")
    later = led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")

    result = run(template_store, led)

    assert result.proposals == [] and result.reinforced == [p1]
    views = {v.id: v for v in C.proposals_list(template_store, led, include_decided=True)}
    assert views[p1].status == "accepted" and later["id"] in views[p1].evidence


def test_rule_of_three_still_counts_a_rejected_belief_as_known(template_store, led):
    # Repeating an instruction in chat does not re-raise a rejected lesson; a new ruling or correction does.
    _, p1 = _rejected(template_store, led, "Use tables, not bullet lists.")
    for s in "abc":
        say(led, "Use tables, not bullet lists.", s)

    result = run(template_store, led)

    assert result.proposals == [] and result.reinforced == []
    views = {v.id: v for v in C.proposals_list(template_store, led, include_decided=True)}
    assert views[p1].status == "rejected" and len(views[p1].evidence) == 1


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


# --- imported history (exo ingest) and the rule of three -------------------------------------------

TESTS_FIRST = ["Always run the tests before pushing.", "please run the tests before you push",
               "Run tests before pushing, every time."]


def _gpt_msg(nid, role, text, at):
    return {"id": nid, "author": {"role": role}, "create_time": at,
            "content": {"content_type": "text", "parts": [text]}, "recipient": "all"}


def chatgpt_export(path, texts, start=1767261600, step=10):
    """A conversations.json with one conversation per text: the principal says it, the assistant answers."""
    convs, reply = [], "Run the tests before pushing? Will do."
    for i, t in enumerate(texts):
        convs.append({"id": f"c{i}", "title": "t", "current_node": f"a{i}", "mapping": {
            f"u{i}": {"id": f"u{i}", "parent": None, "children": [f"a{i}"],
                      "message": _gpt_msg(f"mu{i}", "user", t, start + step * i)},
            f"a{i}": {"id": f"a{i}", "parent": f"u{i}", "children": [],
                      "message": _gpt_msg(f"ma{i}", "assistant", reply, start + step * i + 1)}}})
    path.write_text(json.dumps(convs))
    return path


def chatgpt_edited(path, texts):
    """One conversation whose first prompt was edited: each text is a version, the last one is current."""
    mapping = {"root": {"id": "root", "parent": None, "children": [f"u{i}" for i in range(len(texts))],
                        "message": None}}
    for i, t in enumerate(texts):
        mapping[f"u{i}"] = {"id": f"u{i}", "parent": "root", "children": [f"a{i}"],
                            "message": _gpt_msg(f"mu{i}", "user", t, 1767261600 + 10 * i)}
        mapping[f"a{i}"] = {"id": f"a{i}", "parent": f"u{i}", "children": [],
                            "message": _gpt_msg(f"ma{i}", "assistant", "Will do.", 1767261601 + 10 * i)}
    path.write_text(json.dumps([{"id": "c0", "title": "t", "current_node": f"a{len(texts) - 1}",
                                 "mapping": mapping}]))
    return path


def claude_code_transcript(path, texts):
    """A Claude Code project dir with one transcript of typed messages."""
    path.mkdir(parents=True)
    rows = [{"type": "user", "sessionId": "cc1", "uuid": f"u{i}", "timestamp": f"2026-01-01T10:00:0{i}.000Z",
             "origin": {"kind": "human"}, "message": {"role": "user", "content": t}} for i, t in enumerate(texts)]
    (path / "cc1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def hermes_db(path, rows):
    """A Hermes state.db with one interactive session per row: (role, text, compressed_summary[, active])."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT, model TEXT, profile_name TEXT,
      started_at REAL NOT NULL);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL,
      content TEXT, timestamp REAL NOT NULL, _compressed_summary INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1);""")
    for i, (role, text, summary, *active) in enumerate(rows):
        c.execute("INSERT INTO sessions VALUES (?, 'cli', 'Chat', 'm', NULL, 1767261600.0)", (f"s{i}",))
        c.execute("INSERT INTO messages (session_id, role, content, timestamp, _compressed_summary, active) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (f"s{i}", role, text, 1767261601.0 + i, int(summary),
                                                int(active[0]) if active else 1))
    c.commit()
    c.close()
    return path


def evidence_ids(led):
    return [{e["id"] for e in p["body"]["evidence"]} for p in proposed(led) if p["body"]["kind"] == "rule_of_three"]


@pytest.mark.parametrize("kind,build", [("chatgpt", chatgpt_export), ("claude_code", claude_code_transcript)])
def test_ingested_history_feeds_rule_of_three(template_store, led, tmp_path, kind, build):
    src = build(tmp_path / ("conversations.json" if kind == "chatgpt" else "project"), TESTS_FIRST)
    stats = ingest(kind, src, led, template_store.root.name)      # `exo ingest` authors rows as the principal id
    assert stats["added_principal"] == 3
    res = run(template_store, led)
    assert len(res.proposals) == 1 and res.not_principal == stats["added"] - 3   # the assistant's replies
    mine = {e["id"] for e in led.iter(kind="utterance") if e["body"]["role"] == "principal"}
    assert evidence_ids(led) == [mine]                            # the principal's rows, never the assistant's
    b = proposed(led)[0]["body"]
    assert b["claim"].startswith("The principal wants the agent to run the tests before pushing")


def test_ingested_unverified_and_summary_rows_never_count(template_store, led, tmp_path):
    db = hermes_db(tmp_path / "hermes" / "state.db", [
        ("user", "You are a build agent. Always run the tests before pushing.", False),   # low: an agent's brief
        ("user", "always run the tests before pushing", True),                            # compressed summary
        ("user", "run the tests before you push, every time", True),                      # compressed summary
        ("user", "always run the tests before pushing.", False),
        ("user", "please run the tests before you push", False),
        ("user", "run tests before pushing, every time.", False)])
    stats = ingest("hermes", db, led, "principal")
    assert stats["added_user"] == 1 and stats["added_principal"] == 5 and stats["added_secondary"] == 2
    res = run(template_store, led)
    assert res.not_principal == 1 and len(res.proposals) == 1
    typed = {e["id"] for e in led.iter(kind="utterance")
             if e["author"] == "principal" and not e["body"].get("secondary")}
    assert len(typed) == 3 and evidence_ids(led) == [typed]


def test_an_edited_chatgpt_prompt_counts_once(template_store, led, tmp_path):
    """ChatGPT keeps every version of an edited prompt (`branch: alt`). Only the current one is the principal's
    word now: two edits are not three repetitions."""
    ingest("chatgpt", chatgpt_edited(tmp_path / "conversations.json", TESTS_FIRST), led, "principal")
    rows = [e["body"] for e in led.iter(kind="utterance") if e["body"]["role"] == "principal"]
    assert [b["branch"] for b in rows] == ["alt", "alt", "main"]
    assert run(template_store, led).proposals == [] and proposed(led) == []
    (template_store.root / "proposals" / "state.json").unlink()   # a cache rebuild skips them too
    say(led, "please run the tests before you push", "live")
    assert run(template_store, led).proposals == []
    say(led, "Run tests before pushing, every time.", "live2")
    assert len(run(template_store, led).proposals) == 1


def test_inactive_hermes_rows_never_count(template_store, led, tmp_path):
    db = hermes_db(tmp_path / "hermes" / "state.db", [
        ("user", "always run the tests before pushing.", False, 0),   # superseded in Hermes
        ("user", "please run the tests before you push", False, 0),
        ("user", "run tests before pushing, every time.", False, 1)])
    assert ingest("hermes", db, led, "principal")["added_principal"] == 3
    assert run(template_store, led).proposals == []


def test_ingested_rows_are_dated_by_their_source_time(template_store, led, tmp_path):
    """Imported history is dated when it was said, not when it was imported: evidence and quotes carry the source
    time, so a preference from it decays from then."""
    old = 1672531200   # 2023-01-01
    ingest("chatgpt", chatgpt_export(tmp_path / "conversations.json", TESTS_FIRST, start=old, step=86400), led,
           "principal")
    assert len(run(template_store, led).proposals) == 1
    b = proposed(led)[0]["body"]
    assert sorted(e["ts"][:10] for e in b["evidence"]) == ["2023-01-01", "2023-01-02", "2023-01-03"]
    assert sorted(q["ts"][:10] for q in b["quotes"]) == ["2023-01-01", "2023-01-02", "2023-01-03"]
    # a source time from the future, or one with no time zone, is not believed: the ledger time stands
    for src in ("2999-01-01T00:00:00.000000Z", "2023-01-01T00:00:00", "garbage", None):
        ev = {"ts": "2026-10-18T00:00:00.000000Z", "body": {"source_ts": src}}
        assert C.said_at(ev) == "2026-10-18T00:00:00.000000Z"


def test_rule_of_three_cache_rebuild_counts_ingested_rows(template_store, led, tmp_path):
    ingest("chatgpt", chatgpt_export(tmp_path / "conversations.json", TESTS_FIRST[:2]), led, "principal")
    assert run(template_store, led).proposals == []
    (template_store.root / "proposals" / "state.json").unlink()   # rebuilt from the rows already read
    say(led, TESTS_FIRST[2], "live")
    assert len(run(template_store, led).proposals) == 1


def test_rule_of_three_cache_rebuild_skips_non_principal(template_store, led):
    # D-040: a rebuild reads only the authors a run reads. Unverified ingest rows and peer turns stay out.
    for author, s in (("hermes:unverified-user", "a"), ("hermes:unverified-user", "b"), ("peer:x", "c")):
        led.append("utterance", {"text": "Always run the tests before pushing.", "role": "user"}, author, session=s)
    assert run(template_store, led).not_principal == 3
    (template_store.root / "proposals" / "state.json").unlink()
    say(led, "run the tests before pushing", "live")
    res = run(template_store, led)
    assert res.proposals == [] and proposed(led) == []
    # The principal's own repeats still count: two more complete the three.
    say(led, "please run the tests before you push", "d")
    say(led, "Run tests before pushing, every time.", "e")
    assert len(run(template_store, led).proposals) == 1
    mine = {e["id"] for e in led.iter(kind="utterance") if e["author"] == "principal"}
    assert evidence_ids(led) == [mine]


@pytest.mark.parametrize("anchor", ["2026-10-31T13:52:00.000000Z", "2030-02-01T00:00:00.000000Z"])
def test_repeat_correction_reinforces_and_raises_weight(template_store, led, monkeypatch, anchor):
    monkeypatch.setattr(crypto, "now_ts", lambda: anchor)
    out = agent(led, "A long answer")
    led.append("verdict", {"verdict": "edit", "target": out["id"], "text": "Keep replies under five lines."},
               "principal")
    run(template_store, led, now=anchor)
    w1 = C.proposals_list(template_store, led, now=anchor)[0].weight
    out = agent(led, "Another long answer")
    correction = led.append("verdict", {"verdict": "edit", "target": out["id"],
                                        "text": "keep replies under five lines again"}, "principal")
    res = run(template_store, led, now=anchor)
    assert res.proposals == [] and len(res.reinforced) == 1
    v = C.proposals_list(template_store, led, now=anchor)[0]
    assert v.weight > w1 and len(v.evidence) == 2
    # Decay: 120 days later without reinforcement the taste belief is weak.
    last_evidence = C.taste.parse_ts(correction["ts"])
    old = C.proposals_list(template_store, led, now=(last_evidence + timedelta(days=120)).isoformat())[0]
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
    trailing = C.ModelReflector(lambda p: json.dumps({"wants": "unsigned comments", "avoid": None,
                                                     "because": "agent names leak the team",
                                                     "implication": "All public text.",
                                                     "domain": "mind", "type": "preference",
                                                     "title": "Unsigned"}) + " } trailing brace in prose }")
    got = trailing.reflect(obs, ctx)
    assert got.reflector == "model" and got.wants == "unsigned comments"
    assert isinstance(C.RuleReflector(), C.Reflector) and res.proposals


@pytest.mark.parametrize("wrap", [
    "Note [draft]: {}",
    'Options ["a", "b"] then {}',
    "Scores [1, 2, 3]: {}",
    "[{}]",
    "See [1]: {} } and a trailing brace }",
])
def test_model_reflector_reads_the_object_past_brackets_in_prose(wrap):
    # #835 review: a bracket or an array before the object must not hide it, as the greedy match did not.
    answer = json.dumps({"wants": "unsigned comments", "avoid": None, "because": "names leak",
                         "implication": "public text", "domain": "mind", "type": "preference",
                         "title": "Unsigned"})
    obs = C.Observation("e", 1, "2026-10-11T00:00:00Z", "correction", "verdict:edit", "Use tables.", "use tables")
    ctx = C.ReflectContext(["mind"], C.DEFAULT_POLICY, "2026-10-11")
    got = C.ModelReflector(lambda p: wrap.replace("{}", answer)).reflect(obs, ctx)
    assert got.reflector == "model" and got.wants == "unsigned comments"


def test_model_reflector_without_an_object_falls_back():
    obs = C.Observation("e", 1, "2026-10-11T00:00:00Z", "correction", "verdict:edit", "Use tables.", "use tables")
    ctx = C.ReflectContext(["mind"], C.DEFAULT_POLICY, "2026-10-11")
    for raw in ('["unsigned comments"]', "{not json} [1, 2]", "{" * 200):
        assert C.ModelReflector(lambda p, r=raw: r).reflect(obs, ctx).reflector == "rules"


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


# --- a failed accept leaves nothing behind (exo-desktop #54) ------------------------------------------

OTHER_NODE = """---
id: {nid}
title: Garden watering schedule
type: fact
domain: {dom}
summary: When the garden beds get watered in summer.
load_when: Planning garden chores.
confidence: low
stability: durable
valid_from: 2026-01-01
sources:
  - {{kind: inferred, ref: "template:example"}}
links:
  - {{to: person/identity, rel: part-of}}
---

Water the beds at dawn.
"""


def _write_other_node(store, nid):
    """Write node `nid` on disk, behind this store object's cache."""
    path = store.nodes_dir / f"{nid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = OTHER_NODE.format(nid=nid, dom=nid.split("/")[0])
    path.write_text(text, encoding="utf-8")
    return path, text


def _fail_once(monkeypatch, obj, name, exc):
    """Make `obj.name` raise `exc` on its first call only, as a full disk or a lost index would."""
    real = getattr(obj, name)
    calls = []

    def once(*a, **kw):
        if not calls:
            calls.append(1)
            raise exc
        return real(*a, **kw)
    monkeypatch.setattr(obj, name, once)


def _cut_short_once(monkeypatch, path):
    """The first write to `path` stops halfway on a full disk, and leaves the file cut short as a real one does."""
    real, calls = Path.write_text, []

    def write_text(self, data, *a, **kw):
        if self == path and not calls:
            calls.append(1)
            real(self, data[: len(data) // 2], *a, **kw)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *a, **kw)
    monkeypatch.setattr(Path, "write_text", write_text)


def _fail(monkeypatch, where, store, led, path):
    if where == "index":
        _fail_once(monkeypatch, store, "index", OSError("no space left on device"))
    elif where == "ledger":
        _fail_once(monkeypatch, led, "append", OSError("ledger is read-only"))
    else:
        _cut_short_once(monkeypatch, path)


@pytest.mark.parametrize("where", ["index", "ledger", "write"])
def test_a_failed_accept_restores_the_node_and_a_retry_adds_the_lesson_once(template_store, led, monkeypatch, where):
    path, before = _write_other_node(template_store, "mind/formatting")
    path.write_text(before.replace("Garden watering schedule", "Formatting of tables and bullet lists")
                    .replace("When the garden beds get watered in summer.",
                             "How the principal wants tables and bullet lists used in replies."), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    template_store.reload()
    led.append("ruling", {"text": "Use tables, not bullet lists."}, "principal")
    run(template_store, led)
    p = proposed(led)[0]["body"]
    assert p["action"] == "update" and p["node_id"] == "mind/formatting"
    _fail(monkeypatch, where, template_store, led, path)
    with pytest.raises(OSError):
        C.proposal_accept(template_store, led, p["proposal_id"])
    assert path.read_text(encoding="utf-8") == before
    assert not list(led.iter(kind="belief_accepted"))
    C.proposal_accept(template_store, led, p["proposal_id"])
    assert path.read_text(encoding="utf-8").count("**Claim.** The principal wants") == 1


@pytest.mark.parametrize("where", ["index", "ledger", "write"])
def test_a_failed_accept_of_a_new_node_leaves_no_file(template_store, led, monkeypatch, where):
    led.append("ruling", {"text": "Never sign external comments with agent names."}, "principal")
    res = run(template_store, led)
    p = C.load_state(led).proposals[res.proposals[0]]
    path = template_store.nodes_dir / f"{p.node_id}.md"
    _fail(monkeypatch, where, template_store, led, path)
    with pytest.raises(OSError):
        C.proposal_accept(template_store, led, p.id)
    assert not path.exists() and p.node_id not in template_store.nodes
    assert p.node_id not in (template_store.root / "INDEX.md").read_text()
    out = C.proposal_accept(template_store, led, p.id)
    assert out["action"] == "create" and out["node_id"] == p.node_id


# --- a conflict question closes when its proposal is decided (exo-desktop #54) ---------------------------

@pytest.mark.parametrize("decide", ["accept", "reject"])
def test_deciding_a_held_proposal_closes_its_question(template_store, led, decide):
    """No client asks a belief_conflict question yet, so a direct accept or reject is how a held proposal ends."""
    led.append("ruling", {"text": "Never sign external comments with agent names."}, "principal")
    run(template_store, led)
    led.append("ruling", {"text": "Always sign external comments with agent names."}, "principal")
    res = run(template_store, led)
    (qid,), (pid,) = res.questions, res.proposals
    assert {v.id: v.status for v in C.proposals_list(template_store, led)}[pid] == "held"
    if decide == "accept":
        C.proposal_accept(template_store, led, pid, author="principal")
        closing = list(led.iter(kind="belief_accepted"))[-1]["body"]
        assert closing["conflict_resolved_by"] == "direct accept"
    else:
        C.proposal_reject(template_store, led, pid, "keep the older one", author="principal")
        closing = [e for e in led.iter(kind="note") if e["body"].get("type") == "belief_rejected"][-1]["body"]
    assert closing["question"] == qid
    assert qid in C.load_state(led).resolved
    led.append("answer", {"text": "1", "choice": 1}, "principal", refs=[qid])   # a late answer changes nothing
    later = run(template_store, led)
    assert later.released == [] and later.rejected == []
    assert {v.id: v.status for v in C.proposals_list(template_store, led, include_decided=True)}[pid] == \
        ("accepted" if decide == "accept" else "rejected")
