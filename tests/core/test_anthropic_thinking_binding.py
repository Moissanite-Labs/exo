"""Signed thinking is bound to the model that wrote it; a model switch must not replay it."""
import pytest

from exo.core.models.anthropic import AnthropicProvider, convert_messages
from exo.core.types import ContentPart, Message, ToolCall, ToolResult


def _hist(made_by):
    meta = {"thinking_model": made_by} if made_by else {}
    return [Message(role="user", content=[ContentPart(type="text", text="hi")]),
            Message(role="assistant", meta=meta, content=[
                ContentPart(type="reasoning", text="t", signature="sig"),
                ContentPart(type="text", text="hello")]),
            Message(role="user", content=[ContentPart(type="text", text="again")])]


def _kinds(msgs):
    return [b["type"] for m in msgs if m["role"] == "assistant" for b in m["content"]]


def test_foreign_thinking_dropped_on_model_switch():
    _, msgs = convert_messages(_hist("claude-opus-5-5"), model="claude-fable-5-2")
    assert _kinds(msgs) == ["text"]


def test_own_thinking_kept():
    _, msgs = convert_messages(_hist("claude-fable-5-2"), model="claude-fable-5-2")
    assert _kinds(msgs) == ["thinking", "text"]


def test_untagged_history_kept_and_asks_api_to_drop_mismatch():
    p = AnthropicProvider(base_url="https://api.anthropic.com")
    body = p.build_body(_hist(None), [], model="claude-fable-5-2", max_tokens=1024, reasoning="medium")
    assert _kinds(body["messages"]) == ["thinking", "text"]
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert p.headers()["anthropic-beta"] == "thinking-binding-controls-2026-08-01"


def test_third_party_host_gets_no_beta():
    p = AnthropicProvider(base_url="https://example.invalid")
    assert "anthropic-beta" not in p.headers()
    assert "block_binding" not in p.build_body(_hist(None), [], model="claude-fable-5-2", max_tokens=1024, reasoning="medium").get("thinking", {})


@pytest.mark.parametrize("base_url", [
    "https://api.anthropic.com.evil.test",
    "https://evil-anthropic.com",
    "https://evil.test/anthropic.com",
    "https://evil.test?provider=anthropic.com",
    "https://api.anthropic.com@evil.test",
    "https://user:password@api.anthropic.com",
    "http://api.anthropic.com",
    "https://api.anthropic.com:444",
    "https://api.anthropic.com:invalid",
])
def test_untrusted_origin_never_replays_thinking_or_sends_binding_beta(base_url):
    p = AnthropicProvider(base_url=base_url)
    assert "anthropic-beta" not in p.headers()
    history = _hist(None)
    history[1].content.insert(1, ContentPart(type="reasoning", data="redacted-payload"))
    body = p.build_body(history, [], model="claude-fable-5-2", reasoning="medium")
    assert _kinds(body["messages"]) == ["text"]
    assert "block_binding" not in body.get("thinking", {})


@pytest.mark.parametrize("base_url", ["https://api.anthropic.com", "https://api.anthropic.com:443",
                                      "https://API.ANTHROPIC.COM/"])
def test_exact_https_api_origin_preserves_both_thinking_types(base_url):
    p = AnthropicProvider(base_url=base_url)
    history = _hist(None)
    history[1].content.insert(1, ContentPart(type="reasoning", data="redacted-payload"))
    body = p.build_body(history, [], model="claude-fable-5-2", reasoning="medium")
    assert _kinds(body["messages"]) == ["thinking", "redacted_thinking", "text"]
    assert body["messages"][1]["content"][0]["signature"] == "sig"
    assert body["messages"][1]["content"][1]["data"] == "redacted-payload"
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert "anthropic-beta" in p.headers()


@pytest.mark.parametrize("reasoning,max_tokens", [(None, 8000), ("none", 8000), ("medium", 1024)])
@pytest.mark.parametrize("tool_pending", [False, True])
def test_no_thinking_config_drops_signed_blocks_and_preserves_text_and_tool_pairs(reasoning, max_tokens,
                                                                                tool_pending):
    history = _hist(None)
    history[1].content.insert(1, ContentPart(type="reasoning", data="redacted-payload"))
    if tool_pending:
        history[1].tool_calls = [ToolCall(id="call1", name="lookup", arguments={"q": "value"})]
        history[2] = Message.tool([ToolResult.of_text("call1", "result")])
    p = AnthropicProvider(cache=False)
    body = p.build_body(history, [], model="claude-sonnet-4-5", reasoning=reasoning, max_tokens=max_tokens)
    assert "thinking" not in body
    assert _kinds(body["messages"]) == (["text", "tool_use"] if tool_pending else ["text"])
    assert body["messages"][1]["content"][0]["text"] == "hello"
    if tool_pending:
        call = body["messages"][1]["content"][1]
        result = body["messages"][2]["content"][0]
        assert call == {"type": "tool_use", "id": "call1", "name": "lookup", "input": {"q": "value"}}
        assert result["tool_use_id"] == call["id"]
        assert result["content"] == [{"type": "text", "text": "result"}]
    # Serialization must not delete the saved reasoning or its signature.
    assert history[1].content[0].signature == "sig"
    assert history[1].content[1].data == "redacted-payload"
