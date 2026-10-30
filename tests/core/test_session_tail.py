"""Tail reads keep visibility/provenance and decode only the requested rows."""
import pytest

from exo.core.context import TreeContext
from exo.core.sessions import SessionStore
from exo.core.types import Message


def test_tail_matches_full_transcript_after_undo_and_on_other_sessions(monkeypatch):
    store = SessionStore()
    try:
        sid = store.create_session().id
        other = store.create_session().id
        for i in range(100):
            msg = Message.user(str(i))
            msg.meta["author"] = "principal"
            store.append_message(sid, msg)
        store.append_message(other, Message.user("other thread"))
        store.truncate(sid, 93)
        expected = store.messages(sid)[-5:]
        import exo.core.sessions as sessions
        decode, decoded = sessions.from_dict, []
        def record(*args, **kwargs):
            decoded.append(1)
            return decode(*args, **kwargs)
        monkeypatch.setattr(sessions, "from_dict", record)
        actual = TreeContext(store, None).transcript(sid, 5)
        assert actual == expected and len(decoded) == 5
        assert [m.text() for m in actual] == [str(i) for i in range(88, 93)]
        assert store.last_messages("missing", 5) == []
        assert store.last_messages(sid, 0) == []
    finally:
        store.close()


@pytest.mark.parametrize("limit", [-1, True, 1.5, "5"])
def test_tail_rejects_unbounded_or_ambiguous_limits(limit):
    store = SessionStore()
    try:
        with pytest.raises(ValueError):
            store.last_messages("s", limit)
    finally:
        store.close()


def test_context_preserves_legacy_zero_negative_and_custom_store_contract():
    class CustomStore:
        def messages(self, sid):
            return [1, 2, 3]
    context = TreeContext(CustomStore(), None)
    assert context.transcript("s", 2) == [2, 3]
    assert context.transcript("s", 0) == [1, 2, 3]
    assert context.transcript("s", -1) == [2, 3]
