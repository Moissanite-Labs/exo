"""Turn and summary retries wait for the real router's known cooldown (#660)."""
import asyncio

import pytest

from exo.core.context import DefaultCompressor
from exo.core.loop import Loop
from exo.core.models.router import Router
from exo.core.sessions import EventBus, SessionStore
from exo.core.types import Message, ProviderEvent


class CooldownThenAnswer:
    name = "fixture"
    def __init__(self, delay=0.03):
        self.delay, self.calls = delay, 0
    async def stream(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(kind="error", error="rate limited", status=429, retryable=True, retry_after=self.delay)
        else:
            yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant("recovered"))


def routed(provider):
    return Router(roles={"main": ["fixture:m"]}, instances={"fixture": provider}, catalog={}, env={}).provider("main")


def test_actual_router_cooldown_does_not_exhaust_turn_retry_budget(tmp_path):
    async def exercise():
        provider = CooldownThenAnswer()
        store = SessionStore(tmp_path / "sessions.sqlite")
        loop = Loop(store, routed(provider), max_retries=2, retry_delay=0)
        session = await loop.create_session()
        result = await asyncio.wait_for(loop.send(session.id, "fixture"), 1)
        assert result.stop_reason == "end" and result.message.text() == "recovered"
        assert provider.calls == 2
        store.close()
    asyncio.run(exercise())


def test_actual_router_cooldown_does_not_exhaust_summary_retry_budget():
    async def exercise():
        provider = CooldownThenAnswer()
        compressor = DefaultCompressor(routed(provider), retries=2, retry_delay=0)
        assert await asyncio.wait_for(compressor.summarize("fixture"), 1) == "recovered"
        assert provider.calls == 2
    asyncio.run(exercise())


def test_excessive_cooldown_fails_promptly_without_spending_retries(tmp_path):
    async def exercise():
        provider = CooldownThenAnswer(delay=120)
        store = SessionStore(tmp_path / "sessions.sqlite")
        loop = Loop(store, provider, max_retries=3, retry_delay=0)
        session = await loop.create_session()
        result = await asyncio.wait_for(loop.send(session.id, "fixture"), 1)
        assert result.stop_reason == "error" and provider.calls == 1
        calls = [event.body for event in store.events_since(0, session.id) if event.type == "model.call"]
        assert len(calls) == 1 and calls[0].retries == 0
        notices = [event.body.text for event in store.events_since(0, session.id) if event.type == "notice"]
        assert any("cooldown" in text.lower() for text in notices)
        store.close()
    asyncio.run(exercise())


def test_cooldown_wait_is_interruptible(tmp_path):
    async def exercise():
        provider = CooldownThenAnswer(delay=45)
        store = SessionStore(tmp_path / "sessions.sqlite")
        bus = EventBus(store)
        waiting = asyncio.Event()
        bus.subscribe(lambda event: waiting.set() if event.type == "notice" and "retrying model call" in event.body.text else None)
        loop = Loop(store, routed(provider), bus=bus, max_retries=3, retry_delay=0)
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, "fixture"))
        await asyncio.wait_for(waiting.wait(), 1)
        assert loop.interrupt(session.id)
        assert (await asyncio.wait_for(task, 1)).stop_reason == "interrupted"
        assert provider.calls == 1
        store.close()
    asyncio.run(exercise())


def test_excessive_summary_cooldown_is_propagated_without_empty_retries():
    from exo.core.context import SummaryError
    async def exercise():
        provider = CooldownThenAnswer(delay=120)
        compressor = DefaultCompressor(provider, retries=3, retry_delay=0)
        with pytest.raises(SummaryError) as caught:
            await asyncio.wait_for(compressor.summarize("fixture"), 1)
        assert provider.calls == 1
        assert caught.value.retry_after > 119
    asyncio.run(exercise())


def test_background_summary_retry_does_not_run_before_the_provider_hint(tmp_path):
    import time
    from exo.core.context import SummaryError, TreeContext
    async def exercise():
        calls = []
        class Unavailable:
            async def summarize(self, *args, **kwargs):
                calls.append(1)
                raise SummaryError("fixture cooldown", retry_after=120)
        store = SessionStore(tmp_path / "sessions.sqlite")
        session = store.create_session()
        for i in range(6):
            store.append_message(session.id, Message.user(f"fixture {i}"))
        context = TreeContext(store, Unavailable(), leaf_size=2, tail_messages=2, min_compact_tokens=0, retry_backoff=0)
        context.note(session.id)
        await context.settle(session.id)
        assert calls
        assert all(at > time.monotonic() + 119 for at in context._retry_at.values())
        before = len(calls)
        context.note(session.id)
        await context.settle(session.id)
        assert len(calls) == before
        store.close()
    asyncio.run(exercise())
