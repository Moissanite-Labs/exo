"""Signed thinking is bound to the model that wrote it; a model switch must not replay it."""
from exo.core.models.anthropic import AnthropicProvider, convert_messages
from exo.core.types import ContentPart, Message


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
