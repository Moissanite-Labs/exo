"""exo-memory on a host that keeps the agent's one log (Exo core, D-028): no second episodic log; recall and
zoom read the host's log; the ledger is unchanged."""
import json

from exo.core.agentlog import AgentLogView
from exo.core.sessions import SessionStore
from exo.core.types import Message

from exo_memory import ExoMemoryProvider


def make(tmp_path, store, view):
    conf = {"store_root": str(store.root), "principal": "pat", "agent_id": "agent-one", "compact_enabled": False, "recent_skip": 2}
    p = ExoMemoryProvider(conf)
    p.initialize("", exo_home=str(tmp_path / "exo-home"), platform="exo", agent_log=view)
    return p


def test_the_host_log_is_the_only_episodic_log(tmp_path, store):
    db = SessionStore()
    t = db.create_session(agent="razi", title="w1")
    for text in ("we picked the cobalt theme for the site", "noted", "unrelated chatter", "ok", "more", "fine"):
        db.append_message(t.id, Message.user(text))
    p = make(tmp_path, store, AgentLogView(db, "razi"))
    try:
        st = p._state()
        assert st.rt.episodic is None and not (store.root / "episodic" / "main").exists()
        p.on_turn_start(1, "which theme did we pick for the site?", session_id=t.id)
        p.sync_turn("which theme did we pick for the site?", "cobalt", session_id=t.id)
        assert not (store.root / "episodic" / "main").exists()  # nothing written to a second log
        utt = list(st.rt.ledger.iter(kind="utterance"))
        assert [e["body"]["role"] for e in utt] == ["user", "assistant"]  # the turn's user line once, not twice
        assert all(e["session"] == t.id for e in utt)
        out = p.prefetch("cobalt theme")
        assert "## Your log" in out and "#1 " in out and "Summary view" not in out
        names = {t["name"] for t in p.get_tool_schemas()}
        assert "exo_recall" in names and "exo_zoom" not in names  # the host's memory_zoom opens the log
        assert "memory_zoom" in p.system_prompt_block()
        z = json.loads(p.handle_tool_call("exo_zoom", {"id": 1, "n": 1}))
        assert "cobalt theme" in z["result"]
        rec = json.loads(p.handle_tool_call("exo_recall", {"query": "cobalt"}))
        assert 1 in rec["episodic_ids"]
    finally:
        p.shutdown()
