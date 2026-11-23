"""Loaded profiles cannot use the session history commands on another owner's data."""
import asyncio

import pytest

from exo.protocol import schema as s
from exo.server.app import Dispatcher
from test_session_seat_binding import _engine


@pytest.mark.parametrize("command", [s.SessionGet, s.SessionResume, s.SessionFork, s.SessionUndo, s.TurnSend])
def test_foreign_session_rejected_before_side_effects(tmp_path, command):
    async def run():
        eng = _engine(tmp_path, "worker")
        try:
            # A matching display label must not grant access to a different stored owner.
            foreign = eng.store.create_session(agent="other", seat="worker")
            before = len(eng.store.list_sessions())
            kw = {"session": foreign.id}
            if command is s.TurnSend:
                kw["text"] = "do not execute"
            response = await Dispatcher(eng).run(s.Command.of(command(**kw)))
            assert not response.ok
            assert response.error.code == "not_found"
            assert len(eng.store.list_sessions()) == before
            assert eng.store.messages(foreign.id) == []
            assert foreign.id not in eng.loop._state
        finally:
            await eng.close()
    asyncio.run(run())


def test_listing_filters_owner_before_limit_and_keeps_children(tmp_path):
    async def run():
        eng = _engine(tmp_path, "worker")
        try:
            own = eng.store.create_session(agent="worker", seat="worker")
            child = eng.store.create_session(agent="worker", parent=own.id, kind="subagent")
            eng.store.create_session(agent="other", seat="worker")
            d = Dispatcher(eng)
            response = await d.run(s.Command.of(s.SessionList(limit=2)))
            assert response.ok
            assert {i["id"] for i in response.result["sessions"]} == {own.id, child.id}
            for sid in (own.id, child.id):
                response = await d.run(s.Command.of(s.SessionGet(session=sid)))
                assert response.ok
        finally:
            await eng.close()
    asyncio.run(run())


def test_unseated_core_keeps_existing_access(tmp_path):
    async def run():
        eng = _engine(tmp_path, None)
        try:
            foreign = eng.store.create_session(agent="other")
            response = await Dispatcher(eng).run(s.Command.of(s.SessionGet(session=foreign.id)))
            assert response.ok
        finally:
            await eng.close()
    asyncio.run(run())
