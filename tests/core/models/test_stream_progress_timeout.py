"""SSE comments cannot keep an otherwise silent model call alive (#668)."""
import asyncio
import json

import httpx

from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.types import Message


class CommentStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False
    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.005)
            yield b": keepalive\n\n"
    async def aclose(self):
        self.closed = True


def test_sse_comments_expire_at_the_meaningful_progress_deadline():
    async def exercise():
        stream = CommentStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=stream))) as client:
            provider = OpenAIChatProvider("https://example.invalid", client=client, read_timeout=0.04)
            async def collect():
                return [event async for event in provider.stream([Message.user("fixture")], [])]
            events = await asyncio.wait_for(collect(), 0.3)
            assert events[-1].kind == "error" and events[-1].retryable
            assert "progress" in events[-1].error.lower()
            assert not any(event.kind == "done" for event in events)
            assert stream.closed
    asyncio.run(exercise())


class RecordsStream(httpx.AsyncByteStream):
    def __init__(self, records, repeat=False):
        self.records, self.repeat, self.closed = records, repeat, False
    async def __aiter__(self):
        while True:
            for record in self.records:
                await asyncio.sleep(0.015)
                yield ("data: " + (record if isinstance(record, str) else json.dumps(record)) + "\n\n").encode()
            if not self.repeat:
                return
    async def aclose(self):
        self.closed = True


def test_text_and_buffered_tool_arguments_reset_the_progress_deadline():
    async def exercise(records, expected):
        stream = RecordsStream(records)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=stream))) as client:
            provider = OpenAIChatProvider("https://example.invalid", client=client, progress_timeout=0.05)
            events = [event async for event in provider.stream([Message.user("fixture")], [])]
            assert events[-1].kind == "done" and stream.closed
            assert any(event.kind == expected for event in events)
    text = [{"choices": [{"delta": {"content": str(i)}}]} for i in range(5)]
    text += [{"choices": [{"finish_reason": "stop"}]}, "[DONE]"]
    asyncio.run(exercise(text, "text"))
    tools = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                "function": {"name": "fixture", "arguments": "{"}}]}}]}]
    tools += [{"choices": [{"delta": {"tool_calls": [{"index": 0,
                "function": {"arguments": part}}]}}]} for part in ['"x"', ':', '1', '}']]
    tools += [{"choices": [{"finish_reason": "tool_calls"}]}, "[DONE]"]
    asyncio.run(exercise(tools, "tool_call"))


def test_unrecognized_data_and_anthropic_pings_do_not_count_as_progress():
    from exo.core.models.anthropic import AnthropicProvider
    async def exercise(provider_class):
        stream = RecordsStream([{"type": "ping"}], repeat=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=stream))) as client:
            provider = provider_class("https://example.invalid", client=client, progress_timeout=0.04)
            async def collect():
                return [event async for event in provider.stream([Message.user("fixture")], [])]
            events = await asyncio.wait_for(collect(), 0.3)
            assert events[-1].kind == "error" and "progress" in events[-1].error
            assert stream.closed
    asyncio.run(exercise(OpenAIChatProvider))
    asyncio.run(exercise(AnthropicProvider))


def test_anthropic_buffered_tool_json_is_meaningful_progress():
    from exo.core.models.anthropic import AnthropicProvider
    async def exercise():
        records = [{"type": "message_start", "message": {"usage": {}}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "id": "c1", "name": "fixture"}}]
        records += [{"type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": part}} for part in ['{', '"x"', ':', '1', '}']]
        records += [{"type": "content_block_stop", "index": 0}, {"type": "message_stop"}]
        stream = RecordsStream(records)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=stream))) as client:
            provider = AnthropicProvider("https://example.invalid", client=client, progress_timeout=0.05)
            events = [event async for event in provider.stream([Message.user("fixture")], [])]
            assert events[-1].kind == "done" and stream.closed
            assert next(event.tool_call.arguments for event in events if event.kind == "tool_call") == {"x": 1}
    asyncio.run(exercise())


def test_progress_guard_can_be_disabled_without_preventing_cancellation():
    async def exercise():
        stream = CommentStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=stream))) as client:
            provider = OpenAIChatProvider("https://example.invalid", client=client, read_timeout=0.01, progress_timeout=0)
            async def collect():
                return [event async for event in provider.stream([Message.user("fixture")], [])]
            task = asyncio.create_task(collect())
            await asyncio.sleep(0.04)
            assert not task.done()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert stream.closed
    asyncio.run(exercise())


def test_configured_comment_only_route_times_out_and_falls_back_before_output():
    from exo.core.models.router import Router
    async def exercise():
        stream = CommentStream()
        requests = []
        def handler(request):
            requests.append(request.url.host)
            if request.url.host == "silent.invalid":
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                b'data: {"choices":[{"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            router = Router.from_config({"providers": {
                "silent": {"base_url": "https://silent.invalid", "progress_timeout": 0.04, "read_timeout": 5},
                "healthy": {"base_url": "https://healthy.invalid", "progress_timeout": 0}},
                "roles": {"main": ["silent:fixture", "healthy:fixture"]}}, env={}, client=client)
            async def collect():
                return [event async for event in router.provider("main").stream([Message.user("fixture")], [])]
            events = await asyncio.wait_for(collect(), 1)
            assert events[-1].kind == "done" and events[-1].route == "healthy:fixture"
            assert requests == ["silent.invalid", "healthy.invalid"] and stream.closed
            assert router.credentials("silent")[0].provider._progress_timeout == 0.04
            assert router.credentials("healthy")[0].provider._progress_timeout == 0
    asyncio.run(exercise())
