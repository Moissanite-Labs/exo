"""Explicit owner-published group knowledge with revision CAS and member reads."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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


def snapshot(knowledge):
    """Both durable tables, including content/provenance, for no-write assertions."""
    return tuple(
        [dict(row) for row in knowledge.store._q(f"SELECT * FROM {table} ORDER BY group_id, key, revision")]
        for table in ("swarm_knowledge", "swarm_knowledge_revisions")
    )


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
    before = snapshot(knowledge)

    # Another owner cannot publish to or read from this group at all.
    with pytest.raises(ValueError, match="^no group$"):
        knowledge.publish(
            group.id, owner="other", key="new-key", text="hijack", sources=["session:x"]
        )
    assert snapshot(knowledge) == before
    assert knowledge.keys(group.id, owner="host", member="alpha") == ["plan"]
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
    assert knowledge.keys(other.id, owner="other", member="alpha") == []
    assert knowledge.history(other.id, owner="other", member="alpha", key="plan") == []
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


@pytest.mark.parametrize("revision", [-1, True, False, 1.5, "0", None])
def test_invalid_revision_on_new_key_is_validation_not_conflict(stores, revision):
    knowledge, group, _ = stores
    publish(knowledge, group)
    before = snapshot(knowledge)
    with pytest.raises(ValueError, match="^expected_revision must be a nonnegative integer$") as error:
        publish(knowledge, group, key="new-key", expected_revision=revision)
    assert not isinstance(error.value, RevisionConflict)
    assert snapshot(knowledge) == before
    assert knowledge.keys(group.id, owner="host", member="alpha") == ["plan"]


@pytest.mark.parametrize("fields,message", [
    ({"text": "x" * 100_001}, "knowledge text is too long"),
    ({"sources": [f"doc:{i}" for i in range(51)]}, "between 1 and 50"),
    ({"sources": []}, "between 1 and 50"),
    ({"sources": ["x" * 501]}, "invalid source reference"),
    ({"sources": ["doc:x", "doc:x"]}, "duplicate source reference"),
    ({"sources": [" "]}, "invalid source reference"),
    ({"key": "a" * 129}, "invalid knowledge key"),
    ({"key": "plan\n"}, "invalid knowledge key"),
])
def test_rejected_bounds_leave_both_tables_unchanged(stores, fields, message):
    knowledge, group, _ = stores
    publish(knowledge, group)
    before = snapshot(knowledge)
    with pytest.raises(ValueError, match=message):
        publish(knowledge, group, **{**dict(key="new-key"), **fields})
    assert snapshot(knowledge) == before


@pytest.mark.parametrize("maximum", [False, True])
def test_exact_boundaries_round_trip_without_normalizing(stores, maximum):
    knowledge, group, _ = stores
    key = "a" * 128 if maximum else "a"
    text = "界" * 100_000 if maximum else "x"
    sources = [f"{i:03d}" + "s" * 497 for i in range(50)] if maximum else ["x"]
    item = publish(knowledge, group, key=key, text=text, sources=sources)
    assert item.revision == 1 and item.text == text and item.sources == tuple(sources)
    assert knowledge.read(group.id, owner="host", member="alpha", key=key) == item
    history = knowledge.history(group.id, owner="host", member="beta", key=key)
    assert [(row.revision, row.text, row.sources, row.publisher) for row in history] == [
        (1, text, tuple(sources), "host")
    ]
    assert [len(rows) for rows in snapshot(knowledge)] == [1, 1]


def test_same_owner_groups_with_overlapping_members_keep_keys_and_history_separate(stores):
    knowledge, group, _ = stores
    other = knowledge.groups.create(owner="host", mode="socratic", members=["alpha", "beta"],
                                    known_profiles=["alpha", "beta"])
    publish(knowledge, group, key="only-first")
    publish(knowledge, group, text="first revision")
    publish(knowledge, group, text="second revision", expected_revision=1)
    assert knowledge.keys(other.id, owner="host", member="alpha") == []
    assert knowledge.history(other.id, owner="host", member="alpha", key="plan") == []
    publish(knowledge, other, text="other group's revision")
    assert knowledge.keys(other.id, owner="host", member="alpha") == ["plan"]
    assert [r.text for r in knowledge.history(other.id, owner="host", member="beta", key="plan")] == [
        "other group's revision"
    ]
    assert [r.text for r in knowledge.history(group.id, owner="host", member="alpha", key="plan")] == [
        "first revision", "second revision"
    ]


@pytest.mark.parametrize("member", ["host", "gamma"])
def test_owner_authority_does_not_grant_member_read_access(stores, member):
    knowledge, group, _ = stores
    publish(knowledge, group)
    for read in (lambda: knowledge.read(group.id, owner="host", member=member, key="plan"),
                 lambda: knowledge.keys(group.id, owner="host", member=member),
                 lambda: knowledge.history(group.id, owner="host", member=member, key="plan")):
        with pytest.raises(ValueError, match="^unknown member$"):
            read()


@pytest.mark.parametrize("existing", [False, True])
def test_revision_insert_failure_rolls_back_current_item_too(stores, existing):
    knowledge, group, _ = stores
    if existing:
        publish(knowledge, group)
    before = snapshot(knowledge)
    knowledge.store._q("CREATE TRIGGER reject_knowledge_revision BEFORE INSERT ON swarm_knowledge_revisions "
                       "BEGIN SELECT RAISE(ABORT, 'fixture revision abort'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture revision abort"):
        publish(knowledge, group, text="must roll back", expected_revision=int(existing))
    assert snapshot(knowledge) == before
    assert knowledge.keys(group.id, owner="host", member="alpha") == (["plan"] if existing else [])


@pytest.mark.parametrize("initial_revision", [0, 1])
def test_concurrent_publish_cas_is_atomic_across_connections(tmp_path, initial_revision):
    path = tmp_path / "knowledge.sqlite"
    sessions = [SessionStore(path), SessionStore(path)]
    try:
        stores = [GroupKnowledge(GroupStore(session)) for session in sessions]
        group = stores[0].groups.create(owner="host", mode="socratic", members=["alpha", "beta"],
                                        known_profiles=["alpha", "beta"])
        if initial_revision:
            publish(stores[0], group, text="original")
        barrier = Barrier(8)

        def attempt(index):
            barrier.wait(timeout=5)
            try:
                return publish(stores[index % 2], group, text=f"writer-{index}",
                               sources=[f"doc:{index}"], expected_revision=initial_revision)
            except RevisionConflict:
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(attempt, index) for index in range(8)]
            results = [future.result(timeout=10) for future in futures]
        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        winner = winners[0]
        assert winner.revision == initial_revision + 1
        assert stores[1].read(group.id, owner="host", member="alpha", key="plan") == winner
        history = stores[0].history(group.id, owner="host", member="beta", key="plan")
        assert [row.revision for row in history] == list(range(1, initial_revision + 2))
        assert (history[-1].text, history[-1].sources) == (winner.text, winner.sources)
        assert [len(rows) for rows in snapshot(stores[0])] == [1, initial_revision + 1]
    finally:
        for session in sessions:
            session.close()
