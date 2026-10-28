"""Fork lookup reads indexed source positions, leaving signed message data untouched."""
import sqlite3

from exo.core.sessions import SessionStore
from exo.core.types import Message


def make_fork(path):
    st = SessionStore(path)
    parent = st.create_session(agent='probe')
    st.append_message(parent.id, Message.user('a' * 8192))
    st.append_message(parent.id, Message.assistant('answer'))
    child = st.fork(parent.id)
    return st, parent, child


def deny_json(action, first, second, database, trigger):
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_FUNCTION and second == 'json_extract' else sqlite3.SQLITE_OK


def test_copy_lookup_does_not_parse_every_message_json(tmp_path):
    st, parent, child = make_fork(tmp_path / 'sessions.sqlite')
    st.undo(parent.id)
    expected = [m.text() for m in st.orphaned_copies(child.id, st.last_seq(child.id))]
    st._db.set_authorizer(deny_json)
    try:
        assert [m.text() for m in st.orphaned_copies(child.id, 2)] == expected
        assert st.last_logged_seq(child.id, 2) == 0
    finally:
        st._db.set_authorizer(None)
        st.close()


def test_visible_originals_and_nonfork_local_messages_keep_their_meaning(tmp_path):
    st, parent, child = make_fork(tmp_path / 'sessions.sqlite')
    local = Message.user('local')
    local.meta.update(local='note', pos=1)
    st.append_message(child.id, local)
    st._db.set_authorizer(deny_json)
    try:
        assert st.orphaned_copies(child.id, 3) == []
        assert st.last_logged_seq(child.id, 2) == 2
    finally:
        st._db.set_authorizer(None)
        assert st.verify_log('probe')['ok']
        st.close()


def test_existing_database_backfills_positions_without_rewriting_signed_rows(tmp_path):
    path = tmp_path / 'sessions.sqlite'
    st, parent, child = make_fork(path)
    before = [tuple(r) for r in st._q('SELECT id,data,pos,hash FROM messages ORDER BY id')]
    # Remove only the derived schema to model the previous released database.
    if 'copy_pos' in {r[1] for r in st._q('PRAGMA table_info(messages)')}:
        st._q('DROP INDEX IF EXISTS messages_fork_copy')
        st._q('ALTER TABLE messages DROP COLUMN copy_pos')
    st.close()
    upgraded = SessionStore(path)
    assert [tuple(r) for r in upgraded._q('SELECT id,data,pos,hash FROM messages ORDER BY id')] == before
    assert [r[0] for r in upgraded._q('SELECT copy_pos FROM messages WHERE session=? ORDER BY seq', (child.id,))] == [1, 2]
    assert upgraded.verify_log('probe')['ok']
    upgraded.undo(parent.id)
    upgraded._db.set_authorizer(deny_json)
    try:
        assert len(upgraded.orphaned_copies(child.id, 2)) == 2
    finally:
        upgraded._db.set_authorizer(None)
        upgraded.close()
