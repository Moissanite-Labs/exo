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
