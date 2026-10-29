"""All event transports use an owner-filtered hub for loaded profiles."""
import asyncio

import pytest

from exo.protocol import schema as s
from exo.server.app import ChannelLink, Dispatcher
from exo.server.engine import Hub
from test_session_seat_binding import _engine


@pytest.mark.parametrize("override", [False, True])
def test_replay_and_live_filter_owner(tmp_path, override):
    async def run():
        eng = _engine(tmp_path, "worker")
        try:
            own = eng.store.create_session(agent="worker")
            child = eng.store.create_session(agent="worker", parent=own.id, kind="subagent")
            foreign = eng.store.create_session(agent="other", seat="worker")
            d = Dispatcher(eng, Hub() if override else None)
            for sid in (foreign.id, None, "missing", own.id, child.id):
                d.hub.publish(s.Notice(text="payload"), sid)
            response = await d.run(s.Command.of(s.EventsSubscribe()))
            assert response.ok
            assert [e["session"] for e in response.result["events"]] == [own.id, child.id]
            for sid in (foreign.id, "missing"):
                response = await d.run(s.Command.of(s.EventsSubscribe(session=sid)))
                assert response.ok
                assert response.result["events"] == []
            sub = d.hub.subscribe(d.hub.head())
            try:
                d.hub.publish(s.Notice(text="foreign"), foreign.id)
                d.hub.publish(s.Notice(text="unattributed"))
                expected = d.hub.publish(s.Notice(text="own"), own.id)
                assert await sub.next(timeout=0.1) == expected
                assert await sub.next(timeout=0.01) is None
            finally:
                sub.close()
            feed = ChannelLink(d).events()
            assert (await anext(feed)).session == own.id
            await feed.aclose()
        finally:
            await eng.close()
    asyncio.run(run())


def test_unseated_events_keep_compatibility(tmp_path):
    async def run():
        eng = _engine(tmp_path, None)
        try:
            eng.hub.publish(s.Notice(text="global"))
            eng.hub.publish(s.Notice(text="other"), "other")
            assert len(eng.hub.since(0)) == 2
        finally:
            await eng.close()
    asyncio.run(run())


def test_persisted_foreign_events_filtered_after_reopen(tmp_path):
    async def run():
        eng = _engine(tmp_path, None)
        own = eng.store.create_session(agent="worker")
        foreign = eng.store.create_session(agent="other")
        eng.hub.publish(s.Notice(text="other"), foreign.id)
        eng.hub.publish(s.Notice(text="global"))
        eng.hub.publish(s.Notice(text="own"), own.id)
        await eng.close()
        eng = _engine(tmp_path, "worker")
        try:
            assert [e.session for e in eng.hub.since(0)] == [own.id]
        finally:
            await eng.close()
    asyncio.run(run())
