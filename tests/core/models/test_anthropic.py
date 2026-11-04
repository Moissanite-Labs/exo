import re

from exo.core.models.anthropic import (
    AnthropicProvider, apply_cache_control, convert_messages, convert_tools, normalize_model, thinking_params,
)
from exo.core.types import ContentPart, Message, ToolCall, ToolResult, ToolSpec

from .conftest import Recorder, collect, fixture_bytes


def test_stream_parses_thinking_text_and_tool_call():
    rec = Recorder((200, fixture_bytes("anthropic_tool_stream.sse"), {}))
    prov = AnthropicProvider(api_key="k-test", client=rec.client())
    events = collect(prov.stream([Message.system("sys"), Message.user("read README")], [], model="claude-opus-5-5"))
    kinds = [e.kind for e in events]
    assert kinds.count("done") == 1 and kinds[-1] == "done" and "error" not in kinds
    assert "".join(e.text for e in events if e.kind == "reasoning") == "Need to read the file."
    assert "".join(e.text for e in events if e.kind == "text") == "Reading it now."
    calls = [e.tool_call for e in events if e.kind == "tool_call"]
    assert len(calls) == 1 and calls[0].id == "toolu_01A"
    assert calls[0].arguments == {"path": "README.md", "limit": 40}
    done = events[-1]
    assert done.stop_reason == "tool_use"
    msg = done.message
    assert msg.content[0].type == "reasoning" and msg.content[0].signature == "EqQBCgIYAhIM"
    assert msg.text() == "Reading it now." and msg.tool_calls == calls
    assert not any(k.startswith("_") for k in msg.meta)
    assert done.usage.input_tokens == 120 and done.usage.output_tokens == 87
    assert done.usage.cache_read_tokens == 5000 and done.usage.cache_write_tokens == 2000
    req = rec.requests[0]
    assert req.headers["x-api-key"] == "k-test" and req.headers["anthropic-version"] == "2023-06-01"
    assert rec.body()["stream"] is True


def test_http_429_maps_to_retryable_error_with_retry_after():
    rec = Recorder((429, b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}',
                    {"retry-after": "30", "content-type": "application/json"}))
    events = collect(AnthropicProvider(api_key="k", client=rec.client()).stream([Message.user("hi")], [], model="m"))
    assert [e.kind for e in events] == ["error"]
    ev = events[0]
    assert ev.status == 429 and ev.retryable and "slow down" in ev.error
    assert ev.retry_after == 30


def test_in_stream_overloaded_error_is_retryable():
    rec = Recorder((200, fixture_bytes("anthropic_overloaded.sse"), {}))
    events = collect(AnthropicProvider(api_key="k", client=rec.client()).stream([Message.user("hi")], [], model="m"))
    assert events[-1].kind == "error" and events[-1].status == 529 and events[-1].retryable
    assert [e.kind for e in events].count("error") == 1


def test_400_is_not_retryable():
    rec = Recorder((400, b'{"error":{"message":"bad"}}', {}))
    ev = collect(AnthropicProvider(api_key="k", client=rec.client()).stream([Message.user("x")], [], model="m"))[0]
    assert ev.kind == "error" and not ev.retryable and ev.status == 400


def test_convert_pairs_tools_merges_results_and_keeps_signed_thinking():
    msgs = [
        Message.system("be brief"),
        Message.user("go"),
        Message(role="assistant", content=[ContentPart(type="reasoning", text="hmm", signature="sig"),
                                           ContentPart(type="reasoning", text="unsigned"),
                                           ContentPart.of_text("calling")],
                tool_calls=[ToolCall(id="call.1", name="a"), ToolCall(id="c2", name="b", arguments={"x": 1})]),
        Message.tool([ToolResult.of_text("call.1", "ok")]),
        Message.tool([ToolResult.of_text("c2", "", is_error=True)]),
    ]
    system, out = convert_messages(msgs)
    assert system == [{"type": "text", "text": "be brief"}]
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    blocks = out[1]["content"]
    assert blocks[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    assert [b["type"] for b in blocks] == ["thinking", "text", "tool_use", "tool_use"]
    sid = blocks[2]["id"]
    assert sid.startswith("call_1_") and re.fullmatch(r"[a-zA-Z0-9_-]+", sid)
    results = out[2]["content"]
    assert [r["tool_use_id"] for r in results] == [sid, "c2"]
    assert results[1]["is_error"] and results[1]["content"] == "(no output)"


def test_convert_strips_orphans_and_ensures_leading_user():
    msgs = [Message.assistant("summary"), Message.assistant("", [ToolCall(id="t1", name="x")]), Message.user("next"),
            Message.tool([ToolResult.of_text("ghost", "late")])]
    _, out = convert_messages(msgs)
    assert out[0]["role"] == "user"
    flat = [b for m in out for b in m["content"]]
    assert not any(b["type"] in ("tool_use", "tool_result") for b in flat)
    assert all(b.get("text", "x").strip() for b in flat)


def test_third_party_host_drops_thinking():
    msg = Message(role="assistant", content=[ContentPart(type="reasoning", text="t", signature="s"),
                                             ContentPart.of_text("hi")])
    _, out = convert_messages([Message.user("q"), msg, Message.user("q2")], keep_thinking=False)
    assert all(b["type"] != "thinking" for b in out[1]["content"])


def test_tools_dedup_and_schema_normalized():
    tools = convert_tools([ToolSpec("a", "A", {"anyOf": [{"type": "object"}]}), ToolSpec("a", "dup"),
                           ToolSpec("b", "B", {"type": "object"})])
    assert [t["name"] for t in tools] == ["a", "b"]
    assert tools[0]["input_schema"] == {"type": "object", "properties": {}}
    assert tools[1]["input_schema"]["properties"] == {}


def test_cache_control_breakpoints_capped_at_four():
    system, msgs = convert_messages([Message.system("s"), Message.user("1"), Message.assistant("2"),
                                     Message.user("3"), Message.assistant("4"), Message.user("5")])
    tools = convert_tools([ToolSpec("a", "A")])
    apply_cache_control(system, msgs, tools)
    marks = sum("cache_control" in b for m in msgs for b in m["content"])
    assert "cache_control" in tools[-1] and "cache_control" in system[-1] and marks == 2
    assert "cache_control" in msgs[-1]["content"][-1] and "cache_control" in msgs[-2]["content"][-1]


def test_build_body_reasoning_and_model_normalization():
    prov = AnthropicProvider(api_key="k")
    body = prov.build_body([Message.user("x")], [], model="anthropic/claude-opus-5.5", reasoning="high",
                           temperature=0.3, max_tokens=1000)
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"]["type"] == "adaptive" and body["output_config"] == {"effort": "high"}
    assert "temperature" not in body
    assert thinking_params("claude-opus-4-6", "xhigh", 8000)["output_config"]["effort"] == "max"
    legacy = thinking_params("claude-sonnet-4-5", "high", 8000)
    assert legacy["thinking"] == {"type": "enabled", "budget_tokens": 6976}
    assert thinking_params("claude-opus-5-5", None, 8000) == {}
    assert normalize_model("gpt-5.4") == "gpt-5.4"


def test_legacy_thinking_budget_stays_below_max_tokens():
    """The API wants 1,024 <= budget_tokens < max_tokens. A cap of 1,024 or less left a 1,024 budget at or above
    max_tokens, a 400 that no fallback route can fix (exo-desktop #52). Thinking stays off when it cannot fit."""
    for effort in ("minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
        for max_tokens in (1, 100, 1023, 1024):
            assert thinking_params("claude-sonnet-4-5", effort, max_tokens) == {}, (effort, max_tokens)
        for max_tokens in (1025, 1500, 2048, 2049, 5000, 17000, 32000, 64000):
            budget = thinking_params("claude-sonnet-4-5", effort, max_tokens)["thinking"]["budget_tokens"]
            assert 1024 <= budget < max_tokens, (effort, max_tokens, budget)
    body = AnthropicProvider(api_key="k").build_body([Message.user("x")], [], model="claude-sonnet-4-5",
                                                     reasoning="high", max_tokens=600, temperature=0.2)
    assert "thinking" not in body and body["max_tokens"] == 600 and body["temperature"] == 0.2
    adaptive = AnthropicProvider(api_key="k").build_body([Message.user("x")], [], model="claude-opus-5-5",
                                                         reasoning="high", max_tokens=600)
    assert adaptive["thinking"]["type"] == "adaptive"  # no budget there: unchanged


def test_build_body_tool_choice_none_keeps_the_tools():
    prov, spec = AnthropicProvider(api_key="k"), ToolSpec(name="read_file", description="read")
    on = prov.build_body([Message.user("x")], [spec], model="claude-opus-5-5")
    off = prov.build_body([Message.user("x")], [spec], model="claude-opus-5-5", tool_choice="none")
    assert off["tools"] == on["tools"] and off["tool_choice"] == {"type": "none"} and "tool_choice" not in on
    assert "tool_choice" not in prov.build_body([Message.user("x")], [], model="claude-opus-5-5", tool_choice="none")
