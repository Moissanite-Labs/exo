"""Task cancellation preserves a started turn's terminal record (#667)."""
import asyncio

import pytest

from exo.core.loop import Loop
from exo.core.sessions import EventBus, SessionStore
from exo.core.types import Message, ProviderEvent


def test_cancelled_provider_turn_records_completion_and_allows_the_next_turn(tmp_path):
    async def exercise():
        entered, closed = asyncio.Event(), asyncio.Event()
        class Provider:
            name = "fixture"
            async def stream(self, messages, tools, **kwargs):
                try:
                    yield ProviderEvent(kind="text", text="partial")
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
        store = SessionStore(tmp_path / "sessions.sqlite")
        bus = EventBus(store)
        seen = []
        bus.subscribe(seen.append)
        loop = Loop(store, Provider(), bus=bus)
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, "go"))
        await asyncio.wait_for(entered.wait(), 2)
        assert loop.steer(session.id, Message.user("keep this correction"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        started = [event for event in seen if event.type == "turn.started"]
        ended = [event for event in seen if event.type == "turn.completed"]
        assert len(started) == len(ended) == 1
        assert ended[0].body.turn_id == started[0].body.turn_id
        assert ended[0].body.stop_reason == "interrupted"
        assert not loop.state(session.id).running and not loop.state(session.id).lock.locked()
        class Healthy:
            name = "fixture"
            async def stream(self, messages, tools, **kwargs):
                yield ProviderEvent(kind="done", message=Message.assistant("recovered"), stop_reason="end")
        loop.provider = Healthy()
        assert (await loop.send(session.id, "next")).stop_reason == "end"
        persisted = list(store.events_since(0, session.id))
        assert len([event for event in persisted if event.type == "turn.completed"]) == 2
        store.close()
    asyncio.run(exercise())


def test_cancellation_during_start_notification_still_has_one_terminal_event(tmp_path):
    async def exercise():
        started = asyncio.Event()
        store = SessionStore(tmp_path / "sessions.sqlite")
        bus = EventBus(store)
        async def block_start(event):
            if event.type == "turn.started":
                started.set()
                await asyncio.Event().wait()
        bus.subscribe(block_start)
        class UnusedProvider:
            name = "fixture"
            async def stream(self, messages, tools, **kwargs):
                raise AssertionError("provider must not run")
                yield
        loop = Loop(store, UnusedProvider(), bus=bus)
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, "go"))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        events = list(store.events_since(0, session.id))
        assert len([event for event in events if event.type == "turn.started"]) == 1
        assert len([event for event in events if event.type == "turn.completed"]) == 1
        store.close()
    asyncio.run(exercise())


def test_cancelling_a_completion_subscriber_does_not_duplicate_terminal_record(tmp_path):
    async def exercise():
        completed = asyncio.Event()
        store = SessionStore(tmp_path / "sessions.sqlite")
        bus = EventBus(store)
        async def block_complete(event):
            if event.type == "turn.completed":
                completed.set()
                await asyncio.Event().wait()
        bus.subscribe(block_complete)
        class Provider:
            name = "fixture"
            async def stream(self, messages, tools, **kwargs):
                yield ProviderEvent(kind="done", message=Message.assistant("done"), stop_reason="end")
        loop = Loop(store, Provider(), bus=bus)
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, "go"))
        await asyncio.wait_for(completed.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        ends = [event for event in store.events_since(0, session.id) if event.type == "turn.completed"]
        assert len(ends) == 1 and ends[0].body.stop_reason == "end"
        assert not loop.state(session.id).running
        store.close()
    asyncio.run(exercise())
