"""The agent log in sessions.sqlite (D-028): positions, hash chain, hide on undo, forks, search, migration."""

import json
import sqlite3

from exo.core.sessions import SessionStore
from exo.core.types import Message


def test_threads_share_one_log_in_order():
    st = SessionStore()
    a = st.create_session(agent="razi")
    b = st.create_session(agent="razi")
    other = st.create_session(agent="canary")
    st.append_message(a.id, Message.user("one"))
    st.append_message(b.id, Message.user("two"))
    st.append_message(other.id, Message.user("elsewhere"))
    st.append_message(a.id, Message.assistant("three"))
    log = st.log("razi")
    assert [e.pos for e in log] == [1, 2, 3]
    assert [e.message.text() for e in log] == ["one", "two", "three"]
    assert [e.session for e in log] == [a.id, b.id, a.id]
    assert st.last_pos("canary") == 1
    assert st.verify_log("razi") == {"ok": True, "entries": 3, "error": ""}
    assert a.kind == "thread" and a.agent == "razi"


def test_session_without_agent_stays_out_of_any_log():
    st = SessionStore()
    s = st.create_session()
    st.append_message(s.id, Message.user("legacy"))
    assert st.messages(s.id)[0].meta.get("pos") is None
    assert st.agents() == []


def test_verify_catches_an_edit():
    st = SessionStore()
    a = st.create_session(agent="razi")
    for t in ("a", "b", "c"):
        st.append_message(a.id, Message.user(t))
    st._q("UPDATE messages SET data=replace(data, '\"b\"', '\"B\"') WHERE pos=2")
    out = st.verify_log("razi")
    assert not out["ok"] and "pos 2" in out["error"]


def test_undo_hides_log_entries_and_keeps_the_chain():
    st = SessionStore()
    a = st.create_session(agent="razi")
    st.append_message(a.id, Message.user("keep"))
    st.append_message(a.id, Message.assistant("ok"))
    st.append_message(a.id, Message.user("drop me"))
    st.append_message(a.id, Message.assistant("dropped"))
    assert st.undo(a.id) == 2
    assert [m.text() for m in st.messages(a.id)] == ["keep", "ok"]
    assert st.get_session(a.id).message_count == 2
    log = st.log("razi")
    assert [e.hidden for e in log] == [False, False, True, True]
    assert st.verify_log("razi")["ok"]
    assert st.search("dropped", agent="razi") == []
    nxt = st.append_message(a.id, Message.user("next"))
    assert nxt.meta["seq"] == 5 and nxt.meta["pos"] == 5


def test_fork_copies_stay_out_of_the_log():
    st = SessionStore()
    a = st.create_session(agent="razi")
    st.append_message(a.id, Message.user("hello"))
    child = st.fork(a.id)
    assert child.agent == "razi" and child.kind == "thread"
    assert len(st.messages(child.id)) == 1
    assert st.last_pos("razi") == 1
    st.append_message(child.id, Message.user("new in fork"))
    assert st.last_pos("razi") == 2


def test_private_and_subagent_threads():
    st = SessionStore()
    p = st.create_session(agent="razi", kind="private")
    sub = st.create_session(agent="razi", kind="subagent")
    st.append_message(p.id, Message.user("secret plan"))
    st.append_message(sub.id, Message.user("child chatter"))
    assert [e.kind for e in st.log("razi")] == ["private"]
    assert st.search("secret", agent="razi") == []
    assert st.search("secret")  # still searchable locally, by session


def test_agent_search_gives_positions():
    st = SessionStore()
    a = st.create_session(agent="razi")
    st.append_message(a.id, Message.user("the launch code word is heliotrope"))
    hits = st.search("heliotrope", agent="razi")
    assert hits and hits[0]["pos"] == 1


def _old_file(path):
    """A sessions.sqlite as written before D-028: no agent columns, user_version 0."""
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', seat TEXT, model TEXT, cwd TEXT,
        parent TEXT, fork_seq INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE messages (id TEXT PRIMARY KEY, session TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
        turn_id TEXT, created TEXT NOT NULL, data TEXT NOT NULL, UNIQUE (session, seq));
    CREATE TABLE tasks (id TEXT PRIMARY KEY, parent TEXT, child TEXT);
    """)

    def msg(mid, sid, seq, ts, text, meta=None):
        data = json.dumps({"role": "user", "content": [{"type": "text", "text": text}], "tool_calls": [],
                           "tool_results": [], "id": mid, "meta": {"seq": seq, **(meta or {})}})
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (mid, sid, seq, "user", None, ts, data))

    db.execute("INSERT INTO sessions (id, seat, title, created, updated) VALUES ('s1','canary','','t1','t1')")
    db.execute("INSERT INTO sessions (id, seat, title, created, updated) VALUES ('s2',NULL,'','t2','t2')")
    db.execute("INSERT INTO sessions (id, seat, title, parent, created, updated) VALUES ('s3',NULL,'v','s1','t3','t3')")
    db.execute("INSERT INTO sessions (id, seat, title, created, updated) VALUES ('s4','canary','scheduled: x','t4','t4')")
    db.execute("INSERT INTO tasks VALUES ('t','s1','s3')")
    msg("m1", "s1", 1, "2026-10-12T01:00:00.000Z", "first")
    msg("m2", "s2", 1, "2026-10-12T01:30:00.000Z", "no seat")
    msg("m3", "s1", 2, "2026-10-12T02:00:00.000Z", "second")
    msg("m4", "s3", 1, "2026-10-12T02:10:00.000Z", "child")
    msg("m5", "s4", 1, "2026-10-12T03:00:00.000Z", "cron")
    db.commit()
    db.close()


def test_migration_imports_sessions_in_time_order(tmp_path):
    path = tmp_path / "sessions.sqlite"
    _old_file(path)
    st = SessionStore(path)
    kinds = {i.id: (i.agent, i.kind) for i in st.list_sessions()}
    assert kinds == {"s1": ("canary", "thread"), "s2": ("default", "thread"), "s3": ("default", "subagent"),
                     "s4": ("canary", "scheduled")}
    log = st.log("canary")
    assert [e.message.text() for e in log] == ["first", "second", "cron"]
    assert all(e.message.meta["origin"] == "import" for e in log)
    assert [e.message.text() for e in st.log("default")] == ["no seat"]
    assert st.verify_log("canary")["ok"] and st.verify_log("default")["ok"]
    assert st.messages("s3")[0].text() == "child"  # nothing deleted
    st.close()
    again = SessionStore(path)  # once only
    assert again.last_pos("canary") == 3 and again.migrate_agents() == 0
