"""Internal steer messages have an identity before clients snapshot or cancel them. No provider calls."""
import asyncio
import json

import pytest

from exo.core.engine import CoreEngine
from exo.core.loop import Loop
from exo.core.sessions import EventBus, SessionStore
from exo.core.types import Message
from exo.protocol import schema as s


class UnusedProvider:
    async def stream(self, *args, **kwargs):
        raise AssertionError('steer identity checks must not call a provider')
        yield  # pragma: no cover


@pytest.fixture
def rig(tmp_path):
    store = SessionStore(tmp_path / 'sessions.sqlite')
    bus = EventBus(store)
    events = []
    bus.subscribe(events.append)
    loop = Loop(store, UnusedProvider(), bus=bus)
    # Exercise the actual session.get projection without starting any runtime services.
    engine = CoreEngine.__new__(CoreEngine)
    engine.store, engine.loop, engine.cwd = store, loop, str(tmp_path)
    sid = store.create_session(cwd=str(tmp_path)).id
    loop.state(sid).running = loop.state(sid).accepting_steers = True
    yield engine, loop, store, events, sid
    store.close()


@pytest.mark.parametrize('existing_id', [None, 'm_caller_selected'])
def test_internal_steer_id_is_stable_in_snapshot_cancel_and_delivered_transcript(rig, existing_id):
    engine, loop, store, events, sid = rig
    msg = Message.user('internal job completion')
    msg.meta['author'] = 'job'
    msg.id = existing_id
    assert loop.steer(sid, msg)
    identity = msg.id
    assert isinstance(identity, str) and identity.startswith('m_')
    if existing_id:
        assert identity == existing_id

    async def snapshot():
        return await engine._session_get(s.SessionGet(session=sid))

    assert asyncio.run(snapshot())['steers'] == [identity]
    assert json.loads(json.dumps(asyncio.run(snapshot())))['steers'] == [identity]
    assert loop.unsteer(sid, identity) == [msg]
    assert msg.id == identity and asyncio.run(snapshot())['steers'] == []
    assert store.messages(sid) == []
    assert loop.steer(sid, msg) and msg.id == identity
    assert asyncio.run(snapshot())['steers'] == [identity]
    assert asyncio.run(loop._drain_steer(sid, 't_fixture', loop.state(sid))) == 'job'
    delivered = asyncio.run(snapshot())
    assert delivered['steers'] == [] and delivered['messages'][0]['id'] == identity
    assert store.messages(sid)[0].id == identity
    assert [event.body.message.id for event in events if event.type == 'message.completed'] == [identity]


@pytest.mark.parametrize('running', [False, True])
def test_refused_internal_steer_keeps_message_unassigned_and_queue_empty(rig, running):
    _, loop, store, _, sid = rig
    state = loop.state(sid)
    state.running, state.accepting_steers = running, False
    msg = Message.user('caller will queue this elsewhere')
    assert msg.id is None
    assert loop.steer(sid, msg) is False
    assert msg.id is None and state.steer == [] and store.messages(sid) == []
