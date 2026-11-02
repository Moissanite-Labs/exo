"""#254 W2-4: a fork copies its messages, search rows and summaries in one write transaction."""

from __future__ import annotations

import json
import sqlite3

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


def plain_parent(store: SessionStore, n: int) -> str:
    """A session of no agent with ``n`` messages and a done summary for each 4 of them."""
    sid = store.create_session(title="plain").id
    for i in range(n):
        store.append_message(sid, Message.user(f"ask {i}") if i % 2 == 0 else Message.assistant(f"reply {i}"))
    for k in range(n // 4):
        store.save_summary(SummaryNode(id=new_id("n"), session=sid, level=0, index=k, start=4 * k + 1, end=4 * k + 4,
                                       text=f"sum {k}", status="done"))
    return sid


def test_a_second_store_opens_and_writes_during_a_fork(tmp_path, monkeypatch):
    """A fork builds its copies before it takes the write lock. Held through the build, the lock made another seat
    or a CLI command on the same home fail with "database is locked" after its 5 s wait."""
    path = tmp_path / "s.sqlite"
    store = SessionStore(path)
    store._db.execute("PRAGMA synchronous=OFF")  # build speed only
    sid = plain_parent(store, 3000)
    outcome: list[str] = []
    fts_text = sessions._fts_text

    def open_another(msg):
        if msg.meta.get("seq") == 1500:  # halfway through the copies
            try:
                other = SessionStore(path)
                try:
                    other.append_message(other.create_session(title="other seat").id, Message.user("hi"))
                finally:
                    other.close()
                outcome.append("ok")
            except sqlite3.OperationalError as exc:
                outcome.append(str(exc))
        return fts_text(msg)

    monkeypatch.setattr(sessions, "_fts_text", open_another)
    try:
        child = store.fork(sid)
        assert outcome == ["ok"]  # main: ["database is locked"], after 5 s
        assert child.message_count == 3000
        assert store._q("SELECT COUNT(*) FROM summaries WHERE session=?", (child.id,))[0][0] == 750
        assert [i.title for i in store.list_sessions()].count("other seat") == 1
    finally:
        store.close()


def test_the_summaries_a_fork_keeps_are_found_in_one_pass():
    """Each summary counted the rows up to its end, so the write transaction grew with summaries times messages.
    At four times the messages and summaries, it now does about four times the work. On main it did 13 times."""

    def steps(n: int) -> int:
        store = SessionStore()
        sid = plain_parent(store, n)
        count = [0]

        def tick() -> int:
            count[0] += 1
            return 0

        tx = store._tx

        def counted(fn):
            store._db.set_progress_handler(tick, 100)
            try:
                return tx(fn)
            finally:
                store._db.set_progress_handler(None, 100)

        store._tx = counted  # type: ignore[method-assign]
        try:
            assert store.fork(sid).message_count == n
        finally:
            store.close()
        return count[0]

    small = steps(500)
    assert steps(2000) <= 6 * small


@pytest.mark.parametrize("agent", [None, "a"])
def test_a_parent_that_changes_before_the_write_is_copied_as_it_is_then(monkeypatch, agent):
    """The copies are built before the write transaction. A turn undone and asked again in between (a plain
    session reuses the undone seqs) is found there, and the fork is built again under the lock."""
    store = SessionStore()
    sid = store.create_session(title="p", agent=agent).id
    for i in range(6):
        store.append_message(sid, Message.user(f"ask {i}"), turn_id=f"t{i}")
        store.append_message(sid, Message.assistant(f"reply {i}"), turn_id=f"t{i}")
    store.save_summary(SummaryNode(id=new_id("n"), session=sid, level=0, index=0, start=1, end=4, text="s",
                                   status="done"))
    tx = store._tx

    def change_first(fn):
        monkeypatch.setattr(store, "_tx", tx)
        store.undo(sid, 1)
        store.append_message(sid, Message.user("asked again"))
        return tx(fn)

    monkeypatch.setattr(store, "_tx", change_first)
    new = store.fork(sid)
    old = old_fork(store, sid)
    assert shape(store, new.id) == shape(store, old.id)
    assert [m.text() for m in store.messages(new.id)][-2:] == ["reply 4", "asked again"]
    store.close()


def test_a_fork_that_fails_at_its_last_write_leaves_nothing():
    """The summaries go in last. A fork that fails there still leaves no session, copy or search row."""
    store = SessionStore()
    sid = parent_thread(store)

    def counts() -> tuple:
        return tuple(store._q("SELECT (SELECT COUNT(*) FROM sessions), (SELECT COUNT(*) FROM messages),"
                              " (SELECT COUNT(*) FROM messages_fts), (SELECT COUNT(*) FROM summaries)")[0])

    before = counts()
    store._db.execute("CREATE TEMP TRIGGER no_room BEFORE INSERT ON summaries BEGIN SELECT RAISE(ABORT, 'no room'); END")
    with pytest.raises(sqlite3.DatabaseError, match="no room"):
        store.fork(sid)
    assert counts() == before
    store._db.execute("DROP TRIGGER no_room")
    child = store.fork(sid)  # the same fork without the trigger writes summaries, so the trigger fired on them
    assert store._q("SELECT COUNT(*) FROM summaries WHERE session=?", (child.id,))[0][0] > 0
    store.close()
