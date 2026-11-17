import sqlite3

import pytest

from exo.core.group_messages import GroupMessages
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


def setup(store, mode="conductor"):
    groups = GroupStore(store)
    group = groups.create(owner="host", mode=mode, members=["lead", "one", "two"],
                          known_profiles=["lead", "one", "two"],
                          conductor="lead" if mode == "conductor" else None)
    return group.id, GroupMessages(groups)


def test_private_channels_and_exact_ack():
    store = SessionStore()
    try:
        gid, messages = setup(store)
        first = messages.send(gid, owner="host", sender="one", audience=["lead"], text="private")
        second = messages.send(gid, owner="host", sender="two", audience=["lead"], text="other")
        with pytest.raises(ValueError):
            messages.consume(gid, owner="host", member="lead", message_id=first.id)
        assert messages.pending(gid, owner="host", member="two") == []
        assert messages.pending(gid, owner="host", member="lead") == [first, second]
        messages.consume(gid, owner="host", member="lead", message_id=second.id)
        messages.consume(gid, owner="host", member="lead", message_id=second.id)
        assert messages.pending(gid, owner="host", member="lead") == [first]
        with pytest.raises(ValueError):
            messages.consume(gid, owner="host", member="two", message_id=first.id)
    finally:
        store.close()


@pytest.mark.parametrize("sender,audience", [
    ("one", ["two"]), ("one", ["lead", "two"]), ("lead", ["one", "two"]),
    ("outsider", ["lead"]), ("lead", ["lead"]), ("lead", []),
    ("lead", ["one", "one"]), ("lead", ["outsider"]),
])
def test_bad_audiences_write_nothing(sender, audience):
    store = SessionStore()
    try:
        gid, messages = setup(store)
        with pytest.raises(ValueError):
            messages.send(gid, owner="host", sender=sender, audience=audience, text="x")
        assert store._q("SELECT * FROM swarm_messages") == []
        assert store._q("SELECT * FROM swarm_delivery") == []
    finally:
        store.close()


def test_restart_replays_unconsumed_only(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    gid, messages = setup(store, "socratic")
    message = messages.send(gid, owner="host", sender="one", audience=["lead", "two"], text="shared")
    assert messages.pending(gid, owner="host", member="lead") == [message]
    messages.consume(gid, owner="host", member="lead", message_id=message.id)
    assert messages.pending(gid, owner="host", member="two") == [message]
    store.close()
    store = SessionStore(path)
    try:
        messages = GroupMessages(GroupStore(store))
        assert messages.pending(gid, owner="host", member="lead") == []
        assert messages.pending(gid, owner="host", member="two") == [message]
        with pytest.raises(ValueError, match="no group"):
            messages.pending(gid, owner="other", member="two")
        with pytest.raises(ValueError, match="unknown member"):
            messages.pending(gid, owner="host", member="outsider")
    finally:
        store.close()


def test_send_rolls_back_message_and_first_receipt_when_second_receipt_fails():
    store = SessionStore()
    try:
        gid, messages = setup(store, "socratic")
        store._q("CREATE TRIGGER abort_second_receipt BEFORE INSERT ON swarm_delivery "
                 "WHEN (SELECT COUNT(*) FROM swarm_delivery)=1 "
                 "BEGIN SELECT RAISE(ABORT, 'second receipt'); END")
        with pytest.raises(sqlite3.IntegrityError, match="second receipt"):
            messages.send(gid, owner="host", sender="lead", audience=["one", "two"], text="atomic")
        # The trigger fired only after one receipt existed; rollback must remove that receipt and its message.
        message_rows = store._q("SELECT * FROM swarm_messages")
        receipt_rows = store._q("SELECT * FROM swarm_delivery")
        assert (message_rows, receipt_rows) == ([], [])
    finally:
        store.close()


@pytest.mark.parametrize("limit", [0, 1001, True, 1.0, "5", -1])
def test_invalid_pending_limit_does_not_deliver(limit):
    store = SessionStore()
    try:
        gid, messages = setup(store)
        message = messages.send(gid, owner="host", sender="one", audience=["lead"], text="queued")
        with pytest.raises(ValueError, match="limit"):
            messages.pending(gid, owner="host", member="lead", limit=limit)
        rows = store._q("SELECT seq, delivered, consumed FROM swarm_delivery")
        assert [(row["seq"], row["delivered"], row["consumed"]) for row in rows] == [(message.seq, 0, 0)]
    finally:
        store.close()


@pytest.mark.parametrize("limit", [1, 1000])
def test_pending_accepts_limit_endpoints(limit):
    store = SessionStore()
    try:
        gid, messages = setup(store)
        sent = [messages.send(gid, owner="host", sender="one", audience=["lead"], text=text)
                for text in ("first", "second")]
        assert messages.pending(gid, owner="host", member="lead", limit=limit) == sent[:limit]
    finally:
        store.close()


@pytest.mark.parametrize("text", ["", "   ", None, b"x", 5])
def test_invalid_message_text_writes_nothing(text):
    store = SessionStore()
    try:
        gid, messages = setup(store)
        with pytest.raises(ValueError, match="message text"):
            messages.send(gid, owner="host", sender="one", audience=["lead"], text=text)
        assert store._q("SELECT * FROM swarm_messages") == []
        assert store._q("SELECT * FROM swarm_delivery") == []
    finally:
        store.close()
