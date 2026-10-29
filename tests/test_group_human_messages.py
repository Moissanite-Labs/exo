import pytest

from exo.core.group_messages import GroupMessages
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)
    # Deliberate human/profile name collision: sender_kind must disambiguate.
    group = groups.create(owner="one", mode="conductor", members=["lead", "one", "two"],
                          known_profiles=["lead", "one", "two"], conductor="lead")
    yield store, group.id, GroupMessages(groups)
    store.close()


def test_owner_observation_does_not_ack(room):
    store, gid, messages = room
    private = messages.send(gid, owner="one", sender="two", audience=["lead"], text="private")
    assert messages.observe(gid, owner="one") == [private]
    assert messages.history(gid, owner="one", member="one") == []
    assert messages.history(gid, owner="one", member="two") == [private]
    assert messages.history(gid, owner="one", member="lead") == [private]
    assert [(r["delivered"], r["consumed"]) for r in store._q("SELECT * FROM swarm_delivery")] == [(0, 0)]
    with pytest.raises(ValueError, match="not been delivered"):
        messages.consume(gid, owner="one", member="lead", message_id=private.id)


def test_human_target_broadcast_and_collision(room):
    _, gid, messages = room
    targeted = messages.send_human(gid, owner="one", audience=["two"], text="change direction")
    assert targeted.sender == "one" and targeted.sender_kind == "human"
    assert messages.history(gid, owner="one", member="one") == []
    assert messages.pending(gid, owner="one", member="lead") == []
    assert messages.pending(gid, owner="one", member="two") == [targeted]
    broadcast = messages.send_human(gid, owner="one", audience=["lead", "one", "two"], text="pause work")
    assert messages.pending(gid, owner="one", member="lead") == [broadcast]
    assert messages.pending(gid, owner="one", member="one") == [broadcast]
    messages.consume(gid, owner="one", member="two", message_id=targeted.id)
    assert messages.pending(gid, owner="one", member="two") == [broadcast]
    assert messages.observe(gid, owner="one") == [targeted, broadcast]


def test_owner_boundary_and_no_member_impersonation(room):
    store, gid, messages = room
    for call in [lambda: messages.send_human(gid, owner="other", audience=["one"], text="x"),
                 lambda: messages.observe(gid, owner="other"),
                 lambda: messages.history(gid, owner="other", member="one")]:
        with pytest.raises(ValueError, match="no group"):
            call()
    with pytest.raises(ValueError):
        messages.send(gid, owner="one", sender="one", audience=["two"], text="x")
    assert store._q("SELECT * FROM swarm_messages") == []


@pytest.mark.parametrize("audience", [[], ["missing"], ["one", "one"], "one", [None]])
def test_invalid_human_audience(room, audience):
    store, gid, messages = room
    with pytest.raises(ValueError, match="invalid audience"):
        messages.send_human(gid, owner="one", audience=audience, text="x")
    assert store._q("SELECT * FROM swarm_messages") == []


@pytest.mark.parametrize("after,limit", [(-1, 1), (True, 1), (0, 0), (0, 1001), (0, False)])
def test_invalid_pages(room, after, limit):
    _, gid, messages = room
    with pytest.raises(ValueError):
        messages.observe(gid, owner="one", after=after, limit=limit)
    with pytest.raises(ValueError):
        messages.history(gid, owner="one", member="two", after=after, limit=limit)


def test_filter_before_page_limit_and_group_isolation(room):
    _, gid, messages = room
    hidden = messages.send(gid, owner="one", sender="one", audience=["lead"], text="hidden")
    visible = messages.send_human(gid, owner="one", audience=["two"], text="visible")
    assert messages.history(gid, owner="one", member="two", limit=1) == [visible]
    assert messages.observe(gid, owner="one", limit=1) == [hidden]
    assert messages.observe(gid, owner="one", after=hidden.seq, limit=1) == [visible]
    assert messages.history(gid, owner="one", member="two", after=visible.seq) == []
    second = messages.groups.create(owner="one", mode="socratic", members=["one", "two"],
                                    known_profiles=["one", "two"])
    assert messages.observe(second.id, owner="one") == []
    assert messages.history(second.id, owner="one", member="two") == []


def test_old_schema_migration_and_reopen(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    groups = GroupStore(store)
    group = groups.create(owner="human", mode="socratic", members=["one", "two"],
                          known_profiles=["one", "two"])
    store._q("""CREATE TABLE swarm_messages (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        group_id TEXT NOT NULL, sender TEXT NOT NULL, audience TEXT NOT NULL,
        text TEXT NOT NULL, created TEXT NOT NULL)""")
    store._q("INSERT INTO swarm_messages (id,group_id,sender,audience,text,created) VALUES (?,?,?,?,?,?)",
             ("old", group.id, "one", '["two"]', "old text", "old date"))
    messages = GroupMessages(groups)
    assert messages.observe(group.id, owner="human")[0].sender_kind == "member"
    human = messages.send_human(group.id, owner="human", audience=["two"], text="hello")
    store.close()
    store = SessionStore(path)
    try:
        messages = GroupMessages(GroupStore(store))
        assert messages.observe(group.id, owner="human")[-1] == human
        assert messages.pending(group.id, owner="human", member="two") == [human]
    finally:
        store.close()
