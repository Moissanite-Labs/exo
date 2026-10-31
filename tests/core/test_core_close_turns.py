"""Explicit runtime shutdown, not UI disconnect: synthetic providers only."""
import asyncio
import contextlib

from exo.core.engine import CoreEngine
from exo.core.types import Message, ProviderEvent
from exo.protocol import schema as s


class HeldProvider:
    name = 'held-fixture'
    def __init__(self):
        self.entered = asyncio.Event()
        self.stopped = asyncio.Event()
        self.calls = 0

    async def stream(self, *args, **kwargs):
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.Event().wait()
            yield ProviderEvent(kind='done')
        finally:
            self.stopped.set()


def make(tmp_path):
    eng = CoreEngine(tmp_path / 'home', {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}}, env={},
                     cwd=str(tmp_path), trace_cfg={'enabled': False})
    model = HeldProvider()
    eng.loop.provider = model
    sid = eng.store.create_session(agent=eng.agent).id
    return eng, model, sid


def test_close_finishes_provider_before_store_and_preserves_accepted_steer(tmp_path):
    async def go():
        eng, model, sid = make(tmp_path)
        snapshot = {}
        original = eng.store.close
        def close_store():
            snapshot['provider_stopped'] = model.stopped.is_set()
            snapshot['messages'] = [m.text() for m in eng.store.messages(sid)]
            snapshot['terminal'] = [e.body.stop_reason for e in eng.store.events_since(0, session=sid)
                                    if e.type == 'turn.completed']
            original()
        eng.store.close = close_store
        eng.start_turn(sid, 'original request')
        try:
            await asyncio.wait_for(model.entered.wait(), 2)
            assert eng.loop.steer(sid, Message.user('accepted steer'))
            await asyncio.wait_for(eng.close(), 3)
            assert snapshot['provider_stopped'], 'close returned while the provider request was still active'
            assert snapshot['terminal'] == ['interrupted']
            assert 'accepted steer' in snapshot['messages'] and not eng._tasks
            assert model.calls == 1
        finally:
            for task in list(eng._tasks): task.cancel()
            await asyncio.gather(*eng._tasks, return_exceptions=True)
            with contextlib.suppress(Exception): await eng.close()
    asyncio.run(go())


def test_close_preserves_queued_send_without_dispatching_it(tmp_path):
    async def go():
        eng, model, sid = make(tmp_path)
        captured = []
        original = eng.store.close
        def close_store():
            captured.extend(eng.store.messages(sid)); original()
        eng.store.close = close_store
        eng.start_turn(sid, 'active request')
        await model.entered.wait()
        eng.start_turn(sid, 'accepted queued request')
        await asyncio.sleep(0)  # admit the second task to the held session lock
        await asyncio.wait_for(eng.close(), 3)
        assert model.calls == 1 and model.stopped.is_set() and not eng._tasks
        queued = [m for m in captured if m.text() == 'accepted queued request']
        assert len(queued) == 1 and queued[0].meta['shutdown_pending'] is True
    asyncio.run(go())


def test_close_blocks_new_ingress_and_still_cleans_up_after_step_failure(tmp_path):
    from exo.server.engine import CommandFailed
    async def go():
        eng, model, sid = make(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        store_closed = []
        original = eng.store.close
        def close_store():
            store_closed.append(model.stopped.is_set()); original()
        eng.store.close = close_store
        async def failed_peer_stop():
            entered.set(); await release.wait()
            raise RuntimeError('synthetic peer stop failure')
        eng._stop_peers = failed_peer_stop
        eng.start_turn(sid, 'active request')
        await model.entered.wait()
        closing = asyncio.create_task(eng.close())
        await entered.wait()
        eng.start_turn(sid, 'late background notice')
        try:
            await eng._turn_send(s.TurnSend(session=sid, text='late user request'))
        except CommandFailed as error:
            assert error.code == 'closed'
        else:
            raise AssertionError('shutdown accepted new work')
        release.set()
        try:
            await closing
        except RuntimeError as error:
            assert str(error) == 'synthetic peer stop failure'
        assert store_closed == [True] and model.calls == 1 and not eng._tasks
    asyncio.run(go())


def test_forced_context_cancellation_preserves_accepted_steer(tmp_path, monkeypatch):
    from exo.core import engine as module
    monkeypatch.setattr(module, 'TURN_CLOSE_GRACE', 0.01)
    async def go():
        eng, model, sid = make(tmp_path)
        entered = asyncio.Event()
        async def blocked_build(*args, **kwargs):
            entered.set(); await asyncio.Event().wait()
        eng.loop.context.build = blocked_build
        captured = []
        original = eng.store.close
        def close_store():
            captured.extend(eng.store.messages(sid)); original()
        eng.store.close = close_store
        eng.start_turn(sid, 'original')
        await entered.wait()
        assert eng.loop.steer(sid, Message.user('accepted before shutdown'))
        eng.start_turn(sid, 'accepted queued before shutdown')
        await asyncio.sleep(0)  # both calls have entered send before the close cancels either
        await asyncio.wait_for(eng.close(), 1)
        assert not eng._tasks and model.calls == 0
        [message] = [m for m in captured if m.text() == 'accepted before shutdown']
        assert message.meta['shutdown_pending'] is True
        queued = [m for m in captured if m.text() == 'accepted queued before shutdown']
        assert len(queued) == 1 and queued[0].meta['shutdown_pending'] is True
    asyncio.run(go())


def test_stubborn_extension_keeps_store_and_writer_open_and_reports_incomplete_close(tmp_path, monkeypatch):
    from exo.core import engine as module
    monkeypatch.setattr(module, 'TURN_CLOSE_GRACE', 0.01)
    monkeypatch.setattr(module, 'TURN_CANCEL_GRACE', 0.01)
    async def go():
        eng, model, sid = make(tmp_path)
        eng.claim_writer()
        writer = eng._writer
        entered, release = asyncio.Event(), asyncio.Event()
        async def ignores_cancel(*args, **kwargs):
            entered.set()
            while not release.is_set():
                try: await release.wait()
                except asyncio.CancelledError: pass
            return []
        eng.loop.context.build = ignores_cancel
        closed = []
        original = eng.store.close
        def close_store():
            closed.append(True); original()
        eng.store.close = close_store
        eng.start_turn(sid, 'original')
        await entered.wait()
        try:
            await asyncio.wait_for(eng.close(), 1)
        except RuntimeError as error:
            assert 'store remains open' in str(error)
        else:
            raise AssertionError('incomplete cancellation was reported as successful close')
        assert not closed and eng._writer is writer and eng.store.get_session(sid) is not None
        assert model.calls == 0
        release.set()
        await asyncio.gather(*eng._tasks)
        await eng.close()
        assert closed == [True] and eng._writer is None
    asyncio.run(go())
