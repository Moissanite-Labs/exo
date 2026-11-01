"""#254 W2-4: a fork copies its messages, search rows and summaries in one write transaction."""

from __future__ import annotations

import json

import pytest

import exo.core.sessions as sessions
from exo.core.context import SummaryNode
from exo.core.sessions import SessionStore
from exo.core.types import ContentPart, Message, ToolCall, ToolResult
from exo.protocol.schema import new_id


def old_fork(store: SessionStore, sid: str, at_message: str | None = None, title: str = ""):
    """The fork before #254: one transaction per copied message, then one statement per summary."""
    parent = store.get_session(sid)
    upto = store.last_seq(sid) if at_message is None else store.message_seq(sid, at_message)
    child = store.create_session(title=title or parent.title, seat=parent.seat, model=parent.model, cwd=parent.cwd,
                                 parent=sid, fork_seq=upto, agent=parent.agent,
                                 kind=parent.kind if parent.kind != "scheduled" else "thread")
    for m in store.messages(sid, upto=upto):
        m.meta["forked_from"] = m.id
        m.meta["local"] = "fork"
        m.id = None
        store.append_message(child.id, m)
    for row in store._q("SELECT * FROM summaries WHERE session=? AND end_seq<=? AND status='done' AND end_seq=("
                        "SELECT COUNT(*) FROM messages m WHERE m.session=? AND hidden=0 AND seq<=summaries.end_seq)",
                        (sid, upto, sid)):
        store._q("INSERT INTO summaries (id, session, level, idx, start_seq, end_seq, text, status, lane)"
                 " VALUES (?,?,?,?,?,?,?,?,?)", (new_id("sum"), child.id, row["level"], row["idx"], row["start_seq"],
                                                 row["end_seq"], row["text"], row["status"], row["lane"]))
    return store.get_session(child.id)


def parent_thread(store: SessionStore, kind: str = "thread", tmp_cwd: str | None = None) -> str:
    """An agent thread with tool rounds, a context note, an empty message, an undone tail and summaries."""
    sid = store.create_session(title="parent", seat="s1", model="m1", cwd=tmp_cwd, agent="a", kind=kind).id
    for i in range(12):
        ask = Message.user(f"ask {i} ünïcode")
        ask.meta["author"] = "principal"
        store.append_message(sid, ask, turn_id=f"t{i}")
        call = ToolCall(id=f"c{i}", name="read_file", arguments={"path": f"f{i}.py"})
        store.append_message(sid, Message.assistant("", tool_calls=[call]), turn_id=f"t{i}")
        store.append_message(sid, Message.tool([ToolResult.of_text(call.id, f"body {i}")]), turn_id=f"t{i}")
        store.append_message(sid, Message.assistant(f"done {i}"), turn_id=f"t{i}")
        if i == 4:
            store.append_message(sid, Message("user", [ContentPart.of_text("news")], meta={"note": "threads"}))
            store.append_message(sid, Message("assistant", []))
    store.undo(sid, 2)
    for idx, (start, end, status) in enumerate([(1, 8, "done"), (9, 16, "done"), (17, 30, "pending"), (31, 60, "done")]):
        store.save_summary(SummaryNode(id=new_id("n"), session=sid, level=0, index=idx, start=start, end=end,
                                       text=f"sum {idx}", status=status, principal=2, captured=1, attempts=1))
    return sid


def shape(store: SessionStore, child: str) -> dict:
    """What a fork made, without its fresh ids and times."""
    s = store._q("SELECT title, seat, model, cwd, parent, fork_seq, agent, kind, meta FROM sessions WHERE id=?", (child,))
    ids: dict[str, str] = {}
    msgs = []
    for r in store._q("SELECT * FROM messages WHERE session=? ORDER BY seq", (child,)):
        ids[r["id"]] = f"#{r['seq']}"
        data = json.loads(r["data"])
        assert data.pop("id") == r["id"]
        msgs.append((r["seq"], r["role"], r["turn_id"], r["agent"], r["pos"], r["hash"], r["hidden"], r["copy_pos"],
                     json.dumps(data, sort_keys=False, ensure_ascii=False)))
    fts = sorted((ids[r["msg_id"]], r["text"]) for r in store._q(
        "SELECT msg_id, text FROM messages_fts WHERE session=?", (child,)))
    sums = [tuple(r) for r in store._q("SELECT level, idx, start_seq, end_seq, text, status, lane, principal, captured,"
                                        " attempts FROM summaries WHERE session=? ORDER BY level, idx", (child,))]
    return {"session": tuple(s[0]), "messages": msgs, "fts": fts, "summaries": sums}


@pytest.mark.parametrize("kind", ["thread", "scheduled", "private"])
@pytest.mark.parametrize("at", [None, 7, 3])
def test_fork_makes_what_the_per_message_fork_made(tmp_path, kind, at):
    store = SessionStore(tmp_path / "s.sqlite")
    try:
        sid = parent_thread(store, kind, str(tmp_path))
        at_id = None if at is None else store.messages(sid)[at].id
        old = old_fork(store, sid, at_id, title="")
        new = store.fork(sid, at_id)
        assert shape(store, new.id) == shape(store, old.id)
        assert (new.message_count, new.agent, new.kind, new.parent) == (old.message_count, old.agent, old.kind, old.parent)
        assert {h["message_id"] for h in store.search("body", session=new.id)}  # the copies are searchable
        assert len(store.search("done", session=new.id)) == len(store.search("done", session=old.id))
    finally:
        store.close()


def test_fork_is_one_write_transaction(monkeypatch):
    store = SessionStore()
    sid = parent_thread(store)
    calls = []
    tx = store._tx
    monkeypatch.setattr(store, "_tx", lambda fn: calls.append(1) or tx(fn))
    child = store.fork(sid)
    assert calls == [1] and child.message_count == len(store.messages(sid)) > 40


def test_a_failed_fork_leaves_nothing(monkeypatch):
    store = SessionStore()
    sid = parent_thread(store)
    before = store._q("SELECT (SELECT COUNT(*) FROM sessions), (SELECT COUNT(*) FROM messages),"
                      " (SELECT COUNT(*) FROM messages_fts), (SELECT COUNT(*) FROM summaries)")[0]
    seen = []
    fts_text = sessions._fts_text

    def fail_late(msg):
        seen.append(1)
        if len(seen) == 30:
            raise RuntimeError("disk full")
        return fts_text(msg)

    monkeypatch.setattr(sessions, "_fts_text", fail_late)
    with pytest.raises(RuntimeError, match="disk full"):
        store.fork(sid)
    after = store._q("SELECT (SELECT COUNT(*) FROM sessions), (SELECT COUNT(*) FROM messages),"
                     " (SELECT COUNT(*) FROM messages_fts), (SELECT COUNT(*) FROM summaries)")[0]
    assert tuple(after) == tuple(before)
    with pytest.raises(KeyError):
        store.fork(sid, "m_missing")
    assert store._q("SELECT COUNT(*) FROM sessions")[0][0] == before[0]
