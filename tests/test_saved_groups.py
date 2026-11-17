"""Saved group definitions do not share history or start participants."""
import asyncio
import sqlite3

import pytest

from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from test_session_seat_binding import _engine


def create(groups, **kw):
    args = dict(owner="host", mode="socratic", members=["alpha", "beta"],
                known_profiles=["alpha", "beta"])
    args.update(kw)
    return groups.create(**args)


def test_restart_owner_scope_and_private_sessions(tmp_path):
    path = tmp_path / "sessions.sqlite"
    with_store = SessionStore(path)
    groups = GroupStore(with_store)
    saved = create(groups, mode="conductor", conductor="alpha")
    for member in saved.members:
        info = with_store.get_session(member.session)
        assert info.agent == member.profile
        assert info.kind == "private"
        assert info.parent is None
        assert with_store.messages(member.session) == []
    with_store.close()
    reopened = SessionStore(path)
    try:
        groups = GroupStore(reopened)
        assert groups.get(saved.id, owner="host") == saved
        assert groups.listing(owner="host") == [saved]
        assert groups.get(saved.id, owner="other") is None
        assert groups.listing(owner="other") == []
        assert groups.get("missing", owner="host") is None
    finally:
        reopened.close()


@pytest.mark.parametrize("kw", [
    {"mode": "round-robin"}, {"owner": ""}, {"members": []},
    {"members": ["alpha"]}, {"members": ["alpha", "alpha"]},
    {"members": "alpha"}, {"members": ["alpha", "../beta"]},
    {"members": ["alpha", 1]}, {"known_profiles": ["alpha"]},
    {"conductor": "alpha"}, {"mode": "conductor"},
    {"mode": "conductor", "conductor": "outsider"},
    pytest.param({"members": ["a", "lph"], "known_profiles": "alpha"}, id="string-catalog-substrings"),
    pytest.param({"members": ["a", "lph"], "known_profiles": b"alpha"}, id="bytes-catalog"),
    pytest.param({"members": ["alpha", "../beta"], "known_profiles": ["alpha", "../beta"]},
                 id="invalid-id-present-in-catalog"),
    pytest.param({"members": "ab", "known_profiles": ["a", "b"]}, id="string-members-character-catalog"),
])
def test_invalid_definition_writes_nothing(kw):
    store = SessionStore()
    try:
        groups = GroupStore(store)
        with pytest.raises(ValueError):
            create(groups, **kw)
        assert groups.listing(owner="host") == []
        assert store.list_sessions() == []
    finally:
        store.close()


def test_failed_insert_rolls_back_member_sessions():
    store = SessionStore()
    try:
        groups = GroupStore(store)
        store._q("CREATE TRIGGER reject_group BEFORE INSERT ON swarm_groups "
                 "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            create(groups)
        assert store.list_sessions() == []
        assert groups.listing(owner="host") == []
    finally:
        store.close()


def test_groups_never_reuse_session_history():
    store = SessionStore()
    try:
        groups = GroupStore(store)
        existing = store.create_session(agent="alpha", kind="private")
        first, second = create(groups), create(groups)
        ids = {m.session for g in (first, second) for m in g.members}
        assert len(ids) == 4
        assert existing.id not in ids
        assert first.id != second.id
    finally:
        store.close()


def test_member_sessions_keep_the_profile_owner_boundary(tmp_path):
    async def run():
        eng = _engine(tmp_path, "worker")
        try:
            group = GroupStore(eng.store).create(owner="host", mode="socratic", members=["worker", "other"],
                                                 known_profiles=["worker", "other"])
            sessions = {m.profile: m.session for m in group.members}
            dispatcher = Dispatcher(eng)
            response = await dispatcher.run(s.Command.of(s.SessionList()))
            assert response.ok, response.error
            assert {i["id"] for i in response.result["sessions"]} == {sessions["worker"]}
            own = await dispatcher.run(s.Command.of(s.SessionGet(session=sessions["worker"])))
            assert own.ok, own.error
            assert eng.store.get_session(sessions["worker"]).kind == "private"
            for command in (s.SessionGet, s.SessionFork):
                foreign = await dispatcher.run(s.Command.of(command(session=sessions["other"])))
                assert not foreign.ok and foreign.error.code == "not_found"
            assert len(eng.store.list_sessions()) == 2  # refused fork created no copy
        finally:
            await eng.close()
    asyncio.run(run())
