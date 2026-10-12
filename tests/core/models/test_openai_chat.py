import json

from exo.core.models.openai_chat import OpenAIChatProvider, convert_messages
from exo.core.types import ContentPart, Message, ToolCall, ToolResult, ToolSpec

from .conftest import Recorder, collect, fixture_bytes


def test_stream_assembles_interleaved_tool_call_deltas():
    rec = Recorder((200, fixture_bytes("openai_tool_stream.sse"), {}))
    prov = OpenAIChatProvider("https://openrouter.ai/api/v1", "or-key", client=rec.client())
    events = collect(prov.stream([Message.user("weather in Oslo?")], [ToolSpec("weather", "w")],
                                 model="x/y", reasoning="low", max_tokens=100))
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert [(c.id, c.name, c.arguments) for c in calls] == [("call_abc", "weather", {"city": "Oslo"}),
                                                           ("call_def", "time", {})]
    assert "".join(e.text for e in events if e.kind == "reasoning") == "Look up weather."
    done = events[-1]
    assert done.kind == "done" and done.stop_reason == "tool_use"
    assert done.message.text() == "Checking." and done.message.content[0].type == "reasoning"
    assert done.usage.input_tokens == 50 and done.usage.cache_read_tokens == 32 and done.usage.reasoning_tokens == 5
    body = rec.body()
    assert body["reasoning"] == {"effort": "low"} and body["max_tokens"] == 100
    assert body["tools"][0]["function"]["name"] == "weather"
    assert rec.requests[0].headers["authorization"] == "Bearer or-key"
    assert rec.requests[0].url.path == "/api/v1/chat/completions"


def test_local_llamacpp_text_stream_without_key():
    rec = Recorder((200, fixture_bytes("llamacpp_text_stream.sse"), {}))
    prov = OpenAIChatProvider("http://127.0.0.1:8080/v1", client=rec.client())
    events = collect(prov.stream([Message.user("hi")], [], model="qwen3", reasoning="medium"))
    assert "".join(e.text for e in events if e.kind == "text") == "Hello there"
    assert events[-1].stop_reason == "end" and events[-1].usage.output_tokens == 2
    assert "authorization" not in rec.requests[0].headers
    assert rec.body()["reasoning_effort"] == "medium"


def test_in_stream_provider_error_and_connection_error():
    rec = Recorder((200, b'data: {"error":{"code":429,"message":"upstream limited"}}\n\n', {}))
    ev = collect(OpenAIChatProvider("http://h/v1", client=rec.client()).stream([Message.user("x")], []))[-1]
    assert ev.kind == "error" and ev.status == 429 and ev.retryable
    import httpx
    rec = Recorder((0, httpx.ConnectError("refused"), {}))
    ev = collect(OpenAIChatProvider("http://h/v1", client=rec.client()).stream([Message.user("x")], []))[-1]
    assert ev.kind == "error" and ev.status is None and ev.retryable


def test_convert_messages_splits_tool_results_and_serializes_args():
    out = convert_messages([
        Message.system("s"),
        Message(role="user", content=[ContentPart.of_text("see"), ContentPart(type="image", data="aGk=", mime="image/png")]),
        Message.assistant("", [ToolCall(id="c1", name="a", arguments={"k": 1}), ToolCall(id="c2", name="b")]),
        Message.tool([ToolResult.of_text("c1", "one"), ToolResult.of_text("c2", "two")]),
    ])
    assert out[0] == {"role": "system", "content": "s"}
    assert out[1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert out[2]["content"] is None and json.loads(out[2]["tool_calls"][0]["function"]["arguments"]) == {"k": 1}
    assert [m["tool_call_id"] for m in out[3:]] == ["c1", "c2"]
