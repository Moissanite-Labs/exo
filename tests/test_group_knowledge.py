"""Explicit owner-published group knowledge with revision CAS and member reads."""

import pytest

from exo.core.group_knowledge import GroupKnowledge, RevisionConflict
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from exo.core.types import Message


@pytest.fixture
def stores():
    sessions = SessionStore()
    groups = GroupStore(sessions)
    knowledge = GroupKnowledge(groups)
    group = groups.create(
        owner="host",
        mode="socratic",
        members=["alpha", "beta"],
        known_profiles=["alpha", "beta"],
    )
    other = groups.create(
        owner="other",
        mode="socratic",
        members=["alpha", "beta"],
        known_profiles=["alpha", "beta"],
    )
    yield knowledge, group, other
    sessions.close()


def publish(knowledge, group, **kw):
    args = dict(
        owner="host",
        key="plan",
        text="Ship M02 on Friday",
        sources=["session:s-1", "doc:roadmap.md"],
    )
    args.update(kw)
    return knowledge.publish(group.id, **args)


def test_publish_and_member_read(stores):
    knowledge, group, _ = stores
    item = publish(knowledge, group)
    assert item.revision == 1
    assert item.publisher == "host"
    assert item.sources == ("session:s-1", "doc:roadmap.md")

    seen = knowledge.read(group.id, owner="host", member="alpha", key="plan")
    assert seen == item
    assert knowledge.keys(group.id, owner="host", member="beta") == ["plan"]
    assert knowledge.read(group.id, owner="host", member="alpha", key="missing") is None


def test_revision_conflict_rejected_and_nothing_written(stores):
    knowledge, group, _ = stores
    publish(knowledge, group)
    updated = knowledge.publish(
        group.id,
        owner="host",
        key="plan",
        text="Ship M03 instead",
        sources=["session:s-2"],
        expected_revision=1,
    )
    assert updated.revision == 2

    with pytest.raises(RevisionConflict):
        knowledge.publish(
            group.id,
            owner="host",
            key="plan",
            text="stale write",
            sources=["session:s-3"],
            expected_revision=1,
        )
    with pytest.raises(RevisionConflict):
        knowledge.publish(
            group.id,
            owner="host",
            key="fresh",
            text="not new",
            sources=["session:s-3"],
            expected_revision=1,
        )

    # The conflict changed nothing, and every accepted revision is preserved.
    assert knowledge.read(group.id, owner="host", member="alpha", key="plan") == updated
    assert knowledge.keys(group.id, owner="host", member="alpha") == ["plan"]
    history = knowledge.history(group.id, owner="host", member="beta", key="plan")
    assert [(r.revision, r.text) for r in history] == [
        (1, "Ship M02 on Friday"),
        (2, "Ship M03 instead"),
    ]
    assert history[0].sources == ("session:s-1", "doc:roadmap.md")
    assert history[1].sources == ("session:s-2",)


def test_unauthorized_access_fails_closed(stores):
    knowledge, group, other = stores
    publish(knowledge, group)

    # Another owner cannot publish to or read from this group at all.
    with pytest.raises(ValueError):
        knowledge.publish(
            group.id, owner="other", key="plan", text="hijack", sources=["session:x"]
        )
    with pytest.raises(ValueError):
        knowledge.read(group.id, owner="other", member="alpha", key="plan")
    with pytest.raises(ValueError):
        knowledge.keys(group.id, owner="other", member="alpha")
    with pytest.raises(ValueError):
        knowledge.history(group.id, owner="other", member="alpha", key="plan")

    # Non-members of the group cannot read, even with the right owner.
    with pytest.raises(ValueError):
        knowledge.read(group.id, owner="host", member="gamma", key="plan")
    with pytest.raises(ValueError):
        knowledge.keys(group.id, owner="host", member="host")

    # Members only reach their own group's knowledge: same members, other group.
    assert knowledge.read(other.id, owner="other", member="alpha", key="plan") is None
    with pytest.raises(ValueError):
        knowledge.read(other.id, owner="host", member="alpha", key="plan")


def test_validation(stores):
    knowledge, group, _ = stores
    for bad in ("", "Plan", "plan key", "plan!", "a" * 129, "-lead", 7, None):
        with pytest.raises(ValueError):
            publish(knowledge, group, key=bad)
    for bad in ("", "   ", 5, None):
        with pytest.raises(ValueError):
            publish(knowledge, group, text=bad)
    for bad in ([], "doc:x", ["doc:x", "doc:x"], [""], ["ok", None], ["x" * 501]):
        with pytest.raises(ValueError):
            publish(knowledge, group, sources=bad)
    for bad in (-1, 1.5, "0"):
        with pytest.raises((ValueError, RevisionConflict)):
            publish(knowledge, group, expected_revision=bad)
    with pytest.raises(ValueError):
        knowledge.read(group.id, owner="host", member="alpha", key="Bad Key")


def test_persistence_across_reopen(tmp_path):
    path = tmp_path / "sessions.sqlite"
    sessions = SessionStore(path)
    knowledge = GroupKnowledge(GroupStore(sessions))
    group = knowledge.groups.create(
        owner="host",
        mode="socratic",
        members=["alpha", "beta"],
        known_profiles=["alpha", "beta"],
    )
    knowledge.publish(group.id, owner="host", key="plan", text="v1", sources=["doc:a"])
    knowledge.publish(
        group.id,
        owner="host",
        key="plan",
        text="v2",
        sources=["doc:b"],
        expected_revision=1,
    )
    sessions.close()

    reopened = SessionStore(path)
    try:
        knowledge = GroupKnowledge(GroupStore(reopened))
        item = knowledge.read(group.id, owner="host", member="beta", key="plan")
        assert (item.revision, item.text, item.sources) == (2, "v2", ("doc:b",))
        assert [
            r.text
            for r in knowledge.history(
                group.id, owner="host", member="alpha", key="plan"
            )
        ] == ["v1", "v2"]
    finally:
        reopened.close()


def test_no_automatic_publication(stores):
    """Writing private member sessions never creates group knowledge."""
    knowledge, group, _ = stores
    sessions = knowledge.store
    member = group.members[0]
    sessions.append_message(member.session, Message.user("private note about the plan"))
    assert knowledge.keys(group.id, owner="host", member="alpha") == []
    assert knowledge.read(group.id, owner="host", member="beta", key="plan") is None
    assert knowledge.history(group.id, owner="host", member="alpha", key="plan") == []
