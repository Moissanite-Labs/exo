"""Idle provider-context reset is local to a conversation and never replays work (#677 slice)."""
import asyncio

import pytest

from exo.core.loop import Loop
from exo.core.sessions import SessionStore
from exo.core.types import Message, ProviderEvent


class Recording:
    name = "fixture"
    def __init__(self):
        self.contexts = []
        self.entered = self.release = None
    async def stream(self, messages, tools, **kwargs):
        self.contexts.append(kwargs.get("session_id"))
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        yield ProviderEvent(kind="done", message=Message.assistant("ok"), stop_reason="end")


def test_fresh_changes_only_selected_conversation_tag_and_keeps_all_records(tmp_path):
    async def exercise():
        provider = Recording()
        store = SessionStore(tmp_path / "sessions.sqlite")
        loop = Loop(store, provider)
        loop.context.cache_key = lambda session: "shared-agent-conversation"
        first = await loop.create_session()
        second = await loop.create_session()
        assert not loop.fresh_provider_context(first.id)
        await loop.send(first.id, "first")
        before = [(message.id, message.text(), message.meta) for message in store.messages(first.id)]
        assert loop.fresh_provider_context(first.id)
        assert before == [(message.id, message.text(), message.meta) for message in store.messages(first.id)]
        assert not loop.fresh_provider_context(first.id)
        await loop.send(first.id, "continue")
        await loop.send(second.id, "second")
        assert len(provider.contexts) == 3
        assert provider.contexts[0] == provider.contexts[2] == "shared-agent-conversation"
        assert provider.contexts[1] != provider.contexts[0]
        assert provider.contexts[1].startswith("shared-agent-conversation/fresh_")
        store.close()
    asyncio.run(exercise())


def test_fresh_refuses_running_turns_and_does_not_interrupt_them(tmp_path):
    async def exercise():
        provider = Recording()
        provider.entered, provider.release = asyncio.Event(), asyncio.Event()
        store = SessionStore(tmp_path / "sessions.sqlite")
        loop = Loop(store, provider)
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, "fixture"))
        await asyncio.wait_for(provider.entered.wait(), 1)
        with pytest.raises(ValueError, match="running turn"):
            loop.fresh_provider_context(session.id)
        assert loop.state(session.id).running and not loop.state(session.id).interrupt.is_set()
        provider.release.set()
        assert (await task).stop_reason == "end"
        assert loop.fresh_provider_context(session.id)
        store.close()
    asyncio.run(exercise())


def test_fresh_tag_reaches_configured_http_conversation_header(tmp_path):
    import httpx
    from exo.core.models.openai_chat import OpenAIChatProvider
    async def exercise():
        headers = []
        def respond(request):
            headers.append(request.headers["x-fixture-conversation"])
            return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                                  b'data: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = OpenAIChatProvider("https://example.invalid", client=client,
                                          session_header="x-fixture-conversation")
            store = SessionStore(tmp_path / "sessions.sqlite")
            loop = Loop(store, provider)
            session = await loop.create_session()
            await loop.send(session.id, "first")
            assert loop.fresh_provider_context(session.id)
            await loop.send(session.id, "continue")
            assert len(headers) == 2 and headers[0] != headers[1]
            assert headers[1].startswith(headers[0] + "/fresh_")
            store.close()
    asyncio.run(exercise())
