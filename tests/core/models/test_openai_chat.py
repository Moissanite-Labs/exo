import json
import asyncio

import httpx

from exo.core.models.openai_chat import OpenAIChatProvider, convert_messages
from exo.core.models.base import HttpTransport
from exo.core.types import ContentPart, Message, ToolCall, ToolResult, ToolSpec, Usage

from .conftest import Recorder, collect, fixture_bytes


def test_http_error_names_the_requested_model_without_mutating_provider():
    rec = Recorder((400, b'{"error":{"message":"missing tags"}}', {}))
    prov = OpenAIChatProvider("https://example.invalid/v1", "k", name="nous-custom", client=rec.client())
    end = collect(prov.stream([Message.user("hi")], [], model="requested/free-model"))[-1]
    assert "upstream for requested/free-model is unavailable" in end.error
    assert "this model" not in end.error and prov.model is None


def test_concurrent_http_errors_keep_their_own_request_model():
    async def go():
        both_started = asyncio.Event()
        models = []

        async def respond(request):
            models.append(json.loads(request.content)["model"])
            if len(models) == 2:
                both_started.set()
            await both_started.wait()
            return httpx.Response(400, json={"error": {"message": "missing tags"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            prov = OpenAIChatProvider("https://example.invalid/v1", "k", name="nous-custom",
                                      model="provider-default", client=client)
            async def call(model):
                return [e async for e in prov.stream([Message.user("hi")], [], model=model)]
            a, b = await asyncio.gather(call("model-a"), call("model-b"))
            assert "upstream for model-a is unavailable" in a[-1].error
            assert "upstream for model-b is unavailable" in b[-1].error
            assert "model-b" not in a[-1].error and "model-a" not in b[-1].error
            assert prov.model == "provider-default"
    asyncio.run(go())


def test_legacy_two_argument_error_hook_still_runs():
    class CustomProvider(HttpTransport):
        def headers(self):
            return {}

        def build_body(self, messages, tools, **opts):
            return {"model": opts.get("model")}

        def explain_error(self, status, text):
            return "custom explanation: " + text

    rec = Recorder((400, b'{"error":{"message":"bad request"}}', {}))
    prov = CustomProvider("https://example.invalid/v1", client=rec.client())
    end = collect(prov.stream([Message.user("hi")], [], model="requested"))[-1]
    assert end.error == "HTTP 400: custom explanation: bad request"


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
    # prompt_tokens 50 includes 32 cached: input_tokens is the uncached 18, as on Anthropic (D-080)
    assert done.usage.input_tokens == 18 and done.usage.cache_read_tokens == 32 and done.usage.reasoning_tokens == 5
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


def _effort(base_url, reasoning):
    body = OpenAIChatProvider(base_url, "k").build_body([Message.user("hi")], [], model="m", reasoning=reasoning)
    return body.get("reasoning", {}).get("effort") if "openrouter.ai" in base_url else body.get("reasoning_effort")


def test_reasoning_effort_stays_on_the_wire_scale():
    """OpenAI and OpenRouter take none to xhigh. Exo's max and ultra used to go out as "max", a 400 that no
    fallback route can fix (exo-desktop #52). They send the top wire value now."""
    for base in ("https://api.openai.com/v1", "http://127.0.0.1:8080/v1", "https://openrouter.ai/api/v1"):
        for level in ("max", "ultra"):
            assert _effort(base, level) == "xhigh", (base, level)
        for level in ("minimal", "low", "medium", "high", "xhigh"):
            assert _effort(base, level) == level, (base, level)
        assert _effort(base, "huge") is None, base  # an unknown level is left out, as before
    assert _effort("https://api.openai.com/v1", "none") == "none"
    off = OpenAIChatProvider("https://openrouter.ai/api/v1").build_body([Message.user("hi")], [], reasoning="none")
    assert off["reasoning"] == {"enabled": False}


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


def _opencode_error(status, body, model):
    rec = Recorder((status, body, {}))
    prov = OpenAIChatProvider("https://opencode.ai/zen/go/v1", "k", name="opencode-go", model=model,
                              session_header="x-opencode-session", client=rec.client())
    events = collect(prov.stream([Message.user("hi")], [], session_id="s1", max_tokens=8))
    return rec, events[-1]


def test_opencode_go_request_shape_and_flaky_upstream_message():
    body = b'{"status":400,"message":"This request is not valid. Additional info: missing tags"}'
    rec, err = _opencode_error(400, body, "step-5-preview-free")
    assert rec.requests[0].url.path == "/zen/go/v1/chat/completions"
    assert rec.requests[0].headers["x-opencode-session"] == "s1"
    assert rec.body()["model"] == "step-5-preview-free" and rec.body()["stream"] is True
    assert err.kind == "error" and "upstream for step-5-preview-free is unavailable right now" in err.error
    _, err = _opencode_error(429, b'{"error":{"message":"Upstream request failed: Endpoint is unavailable."}}',
                             "step-5-preview-free")
    assert "unavailable right now" in err.error and err.retryable


def test_opencode_go_wrong_protocol_model_gets_plain_message():
    _, err = _opencode_error(400, b'{"type":"error","error":{"message":"Model does not support this protocol."}}',
                             "gpt-6-luna")
    assert "model gpt-6-luna is not served by Exo's OpenCode Go adapter yet" in err.error


def test_opencode_go_catalog_hides_responses_and_messages_models():
    from exo.core.models.router import Router
    hide = Router(env={}).providers["opencode-go"]["hide_models"]
    for m in ("gpt-6-luna", "grok-4.7", "muse-spark-1.3-contributor", "claude-haiku-5-5", "minimax-m3", "qwen3.8-max"):
        assert any(h in m for h in hide), m
    assert not any(h in "step-5-preview-free" for h in hide)


def test_nous_missing_tags_400_is_retryable_upstream():
    rec = Recorder((400, b'{"error":{"message":"Additional info: missing tags"}}', {}))
    prov = OpenAIChatProvider("https://inference-api.nousresearch.com/v1", "k", name="nous",
                              model="stepfun/step-5-preview:free", client=rec.client())
    err = collect(prov.stream([Message.user("hi")], [], max_tokens=8))[-1]
    assert err.status == 400 and err.retryable and "Nous's upstream for stepfun/step-5-preview:free" in err.error
    other = OpenAIChatProvider("https://x/v1", "k", name="openai", client=Recorder((400, b'{"error":{"message":"missing tags"}}', {})).client())
    assert not collect(other.stream([Message.user("hi")], []))[-1].retryable  # only relays that flake this way


def test_upstream_400_falls_back_and_cools_down_only_that_model():
    from exo.core.models import FakeProvider, Router
    from exo.core.types import ProviderEvent
    down = ProviderEvent(kind="error", error="HTTP 400: missing tags", status=400, retryable=True)
    p, b = FakeProvider([down, "x"], name="p"), FakeProvider(["ok", "ok2"], name="b")
    r = Router(roles={"main": ["p:free", "b:m"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"p": p, "b": b}, env={})
    events = collect(r.stream("main", [Message.user("hi")], []))
    assert events[-1].message.text() == "ok" and "p|free" in r.cooldowns and "p" not in r.cooldowns


def test_clean_close_without_finish_is_an_error_even_after_text():
    from exo.core.models.openai_chat import OpenAIChatProvider as P

    cut = b'data: {"choices":[{"delta":{"content":"half an ans"}}]}\n\n'
    rec = Recorder((200, cut, {}))
    events = collect(P("http://h/v1", client=rec.client()).stream([Message.user("x")], []))
    assert "".join(e.text for e in events if e.kind == "text") == "half an ans"  # shown text is still streamed
    assert events[-1].kind == "error" and "without a response" in events[-1].error and events[-1].retryable
    assert not any(e.kind == "done" for e in events)

    ok = b'data: {"choices":[{"delta":{"content":"fine"},"finish_reason":"stop"}]}\n\n'  # no [DONE], but a stop reason
    events = collect(P("http://h/v1", client=Recorder((200, ok, {})).client()).stream([Message.user("x")], []))
    assert events[-1].kind == "done" and events[-1].stop_reason == "end"


def test_build_body_tool_choice_none_keeps_the_tools():
    prov, spec = OpenAIChatProvider("http://h/v1"), ToolSpec(name="weather", description="w")
    on = prov.build_body([Message.user("x")], [spec], model="m")
    off = prov.build_body([Message.user("x")], [spec], model="m", tool_choice="none")
    assert off["tools"] == on["tools"] and off["tool_choice"] == "none" and "tool_choice" not in on
    assert "tool_choice" not in prov.build_body([Message.user("x")], [], model="m", tool_choice="none")


def _usage_stream(usage: dict) -> bytes:
    chunks = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}, {"choices": [], "usage": usage}]
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def _final(usage: dict, require_usage: bool) -> object:
    prov = OpenAIChatProvider("https://api.openai.com/v1", "k", client=Recorder((200, _usage_stream(usage), {})).client())
    return collect(prov.stream_prepared(prov.build_body([Message.user("hi")], [], model="m"),
                                        require_usage=require_usage))[-1]


def test_cached_usage_matches_anthropic_shape():
    """D-080: prompt_tokens counts the cached tokens too. The transport splits them out, so the same prompt gives
    the same Usage on both transports: input_tokens is the uncached input, cache reads are counted apart."""
    from exo.core.models.anthropic import AnthropicProvider

    ant = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in [
        {"type": "message_start", "message": {"usage": {"input_tokens": 2000, "cache_read_input_tokens": 90_000,
                                                        "cache_creation_input_tokens": 0, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "ok"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 300}},
        {"type": "message_stop"}]).encode()
    a = collect(AnthropicProvider("https://api.anthropic.com", "k", client=Recorder((200, ant, {})).client())
                .stream([Message.user("hi")], [], model="claude-opus-5-5"))[-1]
    o = _final({"prompt_tokens": 92_000, "completion_tokens": 300,
                "prompt_tokens_details": {"cached_tokens": 90_000}}, require_usage=True)
    assert a.kind == o.kind == "done"
    assert a.usage == o.usage == Usage(input_tokens=2000, output_tokens=300, cache_read_tokens=90_000)


def test_cache_split_edges():
    base = {"prompt_tokens": 50, "completion_tokens": 3}
    for details, split in ((None, (50, 0)), ({}, (50, 0)), ({"cached_tokens": None}, (50, 0)),
                           ({"cached_tokens": 0}, (50, 0)), ({"cached_tokens": 50}, (0, 50))):  # last: all cached
        end = _final({**base, "prompt_tokens_details": details}, require_usage=True)
        assert end.kind == "done" and (end.usage.input_tokens, end.usage.cache_read_tokens) == split, details


def test_cached_count_that_cannot_be_split_is_incomplete_usage():
    """A cached count above the prompt count, negative or not an integer cannot be split out of prompt_tokens.
    The usage is incomplete, so the spend boundary (require_usage) gets an error and retains its reservation;
    without it the full prompt stays uncached input. Nothing raises."""
    for cached in (60, -1, "5", 1.5, True):
        usage = {"prompt_tokens": 50, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": cached}}
        end = _final(usage, require_usage=True)
        assert end.kind == "error" and end.error == "provider did not return complete token usage", cached
        loose = _final(usage, require_usage=False)
        assert loose.kind == "done" and (loose.usage.input_tokens, loose.usage.cache_read_tokens) == (50, 0), cached
