"""ExoMemoryProvider against a temp principal store and ledger."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from exo_memory import ExoMemoryProvider
from exo_memory.provider import trim_pack

U = lambda t: {"role": "user", "content": t}  # noqa: E731
A = lambda t: {"role": "assistant", "content": t}  # noqa: E731


def evs(p, kind=None):
    return list(p._state().rt.ledger.iter(kind=kind))


def body_types(p, kind="note"):
    return [e["body"].get("type") for e in evs(p, kind)]


# --- setup and system prompt ------------------------------------------------------


def test_unavailable_without_principal(tmp_path):
    p = ExoMemoryProvider()
    assert not p.is_available()
    assert "EXO_PRINCIPAL" in p.unavailable_reason()
    p = ExoMemoryProvider({"principal": "nobody"})
    assert "exo init" in p.unavailable_reason()


def test_principal_id_resolves_under_exo_home(tmp_path, monkeypatch):
    from exo.store import PrincipalStore
    PrincipalStore.create(tmp_path / "exo-home" / "principals" / "pat")
    monkeypatch.setenv("EXO_PRINCIPAL", "pat")
    p = ExoMemoryProvider({"compact_enabled": False})
    assert p.is_available(), p.unavailable_reason()
    p.initialize("s1", hermes_home=str(tmp_path / "hermes-home"))
    try:
        assert p._state().rt.root == (tmp_path / "exo-home" / "principals" / "pat").resolve()
        assert (tmp_path / "exo-home" / "keys" / "exo-memory.key").exists()
    finally:
        p.shutdown()


def test_config_round_trip(tmp_path):
    home = tmp_path / "hermes-home"
    p = ExoMemoryProvider()
    keys = {f["key"] for f in p.get_config_schema()}
    assert {"principal", "exo_home", "pack_budget", "prefetch_budget"} <= keys
    p.save_config({"principal": "pat", "pack_budget": 900, "bogus": 1}, str(home))
    saved = json.loads((home / "exo-memory" / "config.json").read_text())
    assert saved == {"principal": "pat", "pack_budget": 900}
    assert oct((home / "exo-memory" / "config.json").stat().st_mode & 0o777) == "0o600"
    assert p._cfg(str(home))["pack_budget"] == 900


def test_system_prompt_block_template_store(provider):
    block = provider.system_prompt_block()
    assert block.startswith("# Principal pack (exo-memory)")
    assert "**Who.**" in block and "<!--" not in block  # curator comments stripped
    assert "exo_recall" in block.splitlines()[-1]
    assert len(block) // 4 <= 1500 + 100
    assert provider.system_prompt_block() == block  # byte-stable


def test_pack_trimmed_to_budget(make_provider, store):
    (store.root / "PRINCIPAL.md").write_text("# P\n\n" + "\n\n".join(f"Rule {i}: " + "word " * 40 for i in range(200)))
    p = make_provider(pack_budget=300)
    block = p.system_prompt_block()
    pack = block.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    assert len(pack) <= 300 * 4
    assert "pack trimmed to budget" in pack
    assert trim_pack("short", 100) == "short"


# --- turn capture -----------------------------------------------------------------


def test_sync_turn_writes_utterances_and_episodic(provider, store, tmp_path):
    msgs = [{"role": "system", "content": "sys"}, U("deploy staging"), A("deployed, all green")]
    provider.on_turn_start(1, "deploy staging")
    provider.sync_turn("deploy staging", "deployed, all green", session_id="s1", messages=msgs)
    utt = evs(provider, "utterance")
    assert [(e["author"], e["body"]["role"], e["body"]["text"]) for e in utt] == [
        ("pat", "user", "deploy staging"), ("agent-one", "assistant", "deployed, all green")]
    assert utt[1]["refs"] == [utt[0]["id"]]
    assert all(e["session"] == "s1" for e in utt)
    epi = provider._state().rt.episodic
    assert [m["kind"] for m in epi.log.iter_all()] == ["user", "talk"]
    # the same message list again adds nothing to the episodic log
    provider.sync_turn("", "", session_id="s1", messages=msgs)
    assert len(epi) == 2
    # the log lives in the store, never in HERMES_HOME
    assert (store.root / "episodic" / "main").is_dir()
    assert (store.root / "episodic" / ".gitignore").read_text().strip().endswith("*")
    assert not any("main" in d for _, d, _ in os.walk(tmp_path / "hermes-home"))
    assert provider._state().rt.ledger.verify()["ok"]


def test_resumed_session_logs_only_the_new_turn(provider):
    history = [U(f"old question {i}") if i % 2 == 0 else A(f"old answer {i}") for i in range(10)]
    msgs = history + [U("new question"), A("new answer")]
    provider.sync_turn("new question", "new answer", session_id="s1", messages=msgs)
    assert [m["text"] for m in provider._state().rt.episodic.log.iter_all()] == ["new question", "new answer"]


def test_non_primary_context_does_not_write(make_provider):
    p = make_provider(agent_context="subagent")
    p.sync_turn("hi there", "hello", session_id="s1")
    p.on_turn_start(1, "hi there")
    p.on_session_end([U("x")])
    assert evs(p, "utterance") == [] and evs(p, "note") == []


def test_other_people_are_not_the_principal(make_provider):
    p = make_provider(principal_user_ids="111,222")
    p.on_turn_start(1, "from the principal", author_id="111")
    p.on_turn_start(2, "from a guest", author_id="999")
    p.on_turn_start(3, "from a bot", author_id="b1", author_is_bot=True)
    assert [e["author"] for e in evs(p, "utterance")] == ["pat", "user:999", "bot:b1"]


def test_memory_write_delegation_session_end(provider):
    provider.on_memory_write("add", "user", "prefers tables for numbers", {"write_origin": "tool", "tool_name": "memory"})
    provider.on_delegation("audit the repo", "3 issues found", child_session_id="child-1")
    provider.on_session_end([U("a"), A("b")])
    notes = evs(provider, "note")
    mw = next(e for e in notes if e["body"]["type"] == "memory_write")
    assert mw["body"]["content"] == "prefers tables for numbers" and mw["body"]["tool_name"] == "memory"
    act = evs(provider, "action")[0]
    assert act["body"] == {"type": "delegation", "task": "audit the repo", "result": "3 issues found",
                           "child_session": "child-1"}
    end = notes[-1]
    assert end["body"] == {"type": "consolidate_pending", "messages": 2} and end["session"] == "s1"
    assert [m["kind"] for m in provider._state().rt.episodic.log.iter_all()] == ["work"]


def test_session_switch_records_lineage(provider):
    provider.on_session_switch("s2", parent_session_id="s1")
    provider.sync_turn("next", "ok then", session_id="s2")
    sw = [e for e in evs(provider, "note") if e["body"]["type"] == "session_switch"][0]
    assert sw["session"] == "s2" and sw["body"]["parent_session"] == "s1"
    assert {e["session"] for e in evs(provider, "utterance")} == {"s2"}


# --- pre-compress capture ---------------------------------------------------------

CONVO = [
    {"role": "system", "content": "sys"},
    U("Draft the release note."),
    A("Here is a long draft with five sections and a migration guide."),
    U("No, that's too long. Keep it to three lines."),
    A("Shorter: sync is live; run the migration; report issues."),
    U("Rulings for this project:\n1. Never push to main without a green CI run.\n2. Ask me before any public post."),
    A("Understood. Should I open the PR now or wait for review?"),
]


def test_on_pre_compress_extracts_rulings(provider):
    # the first assistant turn was synced, so the correction targets its ledger event
    provider.sync_turn(CONVO[1]["content"], CONVO[2]["content"], session_id="s1")
    draft_id = evs(provider, "utterance")[-1]["id"]
    block = provider.on_pre_compress(CONVO, require_checkpoint=True)
    assert "Never push to main without a green CI run." in block
    assert "Ask me before any public post." in block
    assert "Should I open the PR now or wait for review?" in block
    rulings = evs(provider, "ruling")
    assert [r["body"]["text"] for r in rulings] == ["Never push to main without a green CI run.",
                                                   "Ask me before any public post."]
    assert all(r["author"] == "pat" and r["body"]["source"] == "pre_compress" for r in rulings)
    verdicts = evs(provider, "verdict")
    assert len(verdicts) == 1
    v = verdicts[0]["body"]
    assert v["verdict"] == "reject" and v["target"] == draft_id and v["text"].startswith("No, that's too long")
    q = evs(provider, "question")
    assert q[0]["body"]["open"] is True and q[0]["author"] == "agent-one"
    # a second compression over the same span adds no duplicates but still returns the block
    n = len(evs(provider))
    assert provider.on_pre_compress(CONVO) == block
    assert len(evs(provider)) == n
    assert provider._state().rt.ledger.verify()["ok"]


def test_correction_of_unlogged_output_backfills_target(provider):
    provider.on_pre_compress([U("Write it."), A("A sonnet about databases."), U("Wrong. Use prose instead.")])
    v = evs(provider, "verdict")[0]
    target = provider._state().rt.ledger.get(v["body"]["target"])
    assert target["kind"] == "utterance" and target["body"]["source"] == "pre_compress_backfill"
    assert target["body"]["text"] == "A sonnet about databases."


def test_pre_compress_dedup_survives_restart(make_provider, store):
    p = make_provider()
    p.on_pre_compress(CONVO)
    n = len(evs(p))
    p.shutdown()
    q = make_provider(session="s2")
    q.on_pre_compress(CONVO)
    assert len([e for e in evs(q) if e["kind"] in ("ruling", "verdict", "question")]) == n - 1 - 1  # minus session_start, backfill


# --- prefetch ---------------------------------------------------------------------


def _populate(store, n=40):
    d = store.root / "nodes" / "work"
    for i in range(n):
        (d / f"deploy-{i}.md").write_text(
            f"---\nid: work/deploy-{i}\ntitle: Deploy rule {i}\ntype: rule\ndomain: work\n"
            f"summary: How deploys work, variant {i}.\nload_when: deploy staging release\nconfidence: high\n"
            f"stability: durable\nvalid_from: 2026-01-01\nsources: [test]\nlinks: []\n---\n"
            + ("Deploy staging first, then verify, then release. " * 30))
    store.reload()


@pytest.mark.parametrize("budget", [200, 600, 1500, 4000])
def test_prefetch_respects_budget(make_provider, store, budget):
    _populate(store)
    p = make_provider(prefetch_budget=budget, recent_skip=0)
    for i in range(60):
        p.sync_turn(f"how do we deploy staging, case {i}? " + "detail " * 30, f"deploy answer {i} " + "x " * 50,
                    session_id="s1")
    out = p.prefetch("deploy staging release")
    assert out and len(out) // 4 <= budget and len(out) <= budget * 4
    assert "## Principal beliefs" in out
    if budget >= 600:
        assert "## Episodic memory" in out
    assert p.recall_status() is None or p.recall_status().count > 0


def test_prefetch_labels_and_trivial(make_provider, store):
    _populate(store, 3)
    provider = make_provider(recent_skip=0)
    provider.sync_turn("remember the staging bucket is blue", "noted", session_id="s1")
    assert provider.prefetch("ok") == ""
    out = provider.prefetch("deploy staging")
    assert out.index("## Principal beliefs") < out.index("## Episodic memory")
    assert "staging bucket is blue" in out


def test_prefetch_template_store(provider):
    out = provider.prefetch("communication style and reporting")
    assert out.startswith("## Principal beliefs") and len(out) // 4 <= 1500
    assert "## Episodic memory" not in out  # nothing logged yet
    assert provider.prefetch("zzzqqq") == ""


# --- tools ------------------------------------------------------------------------


def call(p, name, **args):
    return json.loads(p.handle_tool_call(name, args))


def test_tool_schemas(provider):
    names = [t["name"] for t in provider.get_tool_schemas()]
    assert names == ["exo_recall", "exo_note", "exo_should_have_been", "exo_zoom", "exo_date"]
    for t in provider.get_tool_schemas():
        assert t["parameters"]["type"] == "object" and t["description"]


def test_exo_note(provider):
    r = call(provider, "exo_note", text="the staging bucket is blue")
    ev = provider._state().rt.ledger.get(r["id"])
    assert ev["kind"] == "note" and ev["author"] == "agent-one" and ev["body"]["text"] == "the staging bucket is blue"
    r = call(provider, "exo_note", text="pricing is the principal's call", kind="ruling")
    assert provider._state().rt.ledger.get(r["id"])["kind"] == "ruling"
    assert "error" in call(provider, "exo_note", text="x", kind="belief_accepted")
    assert "error" in call(provider, "exo_note", text="")


def test_exo_should_have_been(provider):
    provider.sync_turn("summarize the thread", "A long summary in nine paragraphs.", session_id="s1")
    last = evs(provider, "utterance")[-1]["id"]
    r = call(provider, "exo_should_have_been", target="last", wanted="three bullet points", reason="reads on phone")
    v = provider._state().rt.ledger.get(r["id"])
    assert v["kind"] == "verdict" and v["author"] == "pat"
    assert v["body"]["verdict"] == "should_have_been" and v["body"]["target"] == last and v["refs"] == [last]
    assert v["body"]["wanted"] == "three bullet points" and v["body"]["reason"] == "reads on phone"
    r2 = call(provider, "exo_should_have_been", target=last, wanted="shorter")
    assert r2["target"] == last
    r3 = call(provider, "exo_should_have_been", target="the PR description from this morning", wanted="no emoji")
    art = provider._state().rt.ledger.get(r3["target"])
    assert art["kind"] == "artifact" and art["body"]["description"] == "the PR description from this morning"
    assert "error" in call(provider, "exo_should_have_been", target="last", wanted="")
    assert provider._state().rt.ledger.verify()["ok"]


def test_exo_recall_zoom_date(provider, store):
    _populate(store, 3)
    provider.sync_turn("the staging bucket is blue", "noted", session_id="s1")
    r = call(provider, "exo_recall", query="deploy staging", budget=400)
    assert r["node_ids"] and r["tokens"] <= 400 and "## Principal beliefs" in r["result"]
    assert call(provider, "exo_zoom", id=0, n=1)["result"] == "0+0|user: the staging bucket is blue"
    assert call(provider, "exo_date", id=0)["result"][:4].isdigit()
    assert "error" in call(provider, "exo_zoom", id="x", n=1)
    assert "error" in call(provider, "exo_recall", query="")


# --- compactor and real stores ----------------------------------------------------


def test_compactor_builds_summaries(make_provider):
    calls = []

    def stub(messages):
        calls.append(messages)
        return "user: asked about a long thing; talk: answered"

    p = make_provider(compact_enabled=True, summarizer=stub, view_chars=200)
    for i in range(4):
        p.sync_turn("long question " + "word " * 200, "long answer " + "word " * 200, session_id="s1")
    epi = p._state().rt.episodic
    deadline = time.time() + 10
    while time.time() < deadline and not all(epi.tree.built(0, i) for i in range(8)):
        epi.notify()
        time.sleep(0.05)
    assert calls and all(epi.tree.built(0, i) for i in range(8))
    assert "asked about a long thing" in epi.render(4000)


SRC = Path(os.environ.get("EXO_ACCEPTANCE_STORE") or Path.home() / "hafiz")


@pytest.mark.skipif(not (SRC / "nodes").is_dir(), reason=f"no example store at {SRC}")
def test_with_copy_of_example_store(make_provider, tmp_path):
    dst = tmp_path / "example"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".git", "private", "_drafts", "episodic",
                                                            "ledger.sqlite*"))
    p = make_provider(root=dst)
    block = p.system_prompt_block()
    assert len(block) // 4 <= 1500 + 100
    out = p.prefetch("external posting github comment")
    assert "## Principal beliefs" in out and len(out) // 4 <= 1500
    p.sync_turn("post the release note", "drafted, waiting on approval", session_id="s1")
    assert p._state().rt.ledger.verify()["ok"]
