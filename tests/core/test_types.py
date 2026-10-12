import asyncio
import json

import pytest

from exo.core.types import (
    ContentPart,
    Message,
    Provider,
    ProviderEvent,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    from_dict,
    to_dict,
)


def roundtrip(obj):
    return from_dict(type(obj), json.loads(json.dumps(to_dict(obj))))


def test_message_roundtrip_with_tools_and_reasoning():
    msg = Message(
        role="assistant",
        content=[
            ContentPart(type="reasoning", text="think", signature="sig"),
            ContentPart.of_text("hello"),
            ContentPart(type="image", data="aGk=", mime="image/png"),
        ],
        tool_calls=[ToolCall(id="t1", name="read", arguments={"path": "a.txt"})],
        id="m1",
        meta={"cache": True},
    )
    back = roundtrip(msg)
    assert back == msg
    assert isinstance(back.content[0], ContentPart)
    assert isinstance(back.tool_calls[0], ToolCall)
    assert back.text() == "hello"


def test_tool_message_roundtrip():
    msg = Message.tool([ToolResult.of_text("t1", "ok"), ToolResult.of_text("t2", "boom", is_error=True)])
    back = roundtrip(msg)
    assert back == msg
    assert back.tool_results[1].is_error and back.tool_results[0].text() == "ok"


@pytest.mark.parametrize(
    "obj",
    [
        Usage(input_tokens=3, output_tokens=4, cost_usd=0.01),
        ToolSpec(name="bash", description="run", risk="high", toolset="shell"),
        ProviderEvent(kind="text", text="hi"),
        ProviderEvent(kind="tool_call", tool_call=ToolCall(id="a", name="b")),
        ProviderEvent(kind="done", stop_reason="end", message=Message.assistant("x"), usage=Usage(1, 2)),
        ProviderEvent(kind="error", error="429", retryable=True, status=429),
    ],
)
def test_roundtrip(obj):
    assert roundtrip(obj) == obj


def test_strict_rejects_unknown_and_bad_literal():
    with pytest.raises(ValueError):
        from_dict(Usage, {"input_tokens": 1, "bogus": 2})
    assert from_dict(Usage, {"input_tokens": 1, "bogus": 2}, strict=False).input_tokens == 1
    with pytest.raises(ValueError):
        from_dict(Message, {"role": "robot"})
    with pytest.raises(ValueError):
        from_dict(ToolCall, {"id": "x"})  # name missing


def test_usage_add():
    total = Usage(1, 2) + Usage(3, 4, cost_usd=0.5)
    assert (total.input_tokens, total.output_tokens, total.cost_usd) == (4, 6, 0.5)
    assert (Usage() + Usage()).cost_usd is None


class EchoProvider:
    name = "echo"

    async def stream(self, messages, tools, **opts):
        text = messages[-1].text()
        yield ProviderEvent(kind="text", text=text)
        yield ProviderEvent(kind="done", stop_reason="end", message=Message.assistant(text))


def test_provider_protocol():
    p = EchoProvider()
    assert isinstance(p, Provider)

    async def run():
        return [e async for e in p.stream([Message.user("hi")], [])]

    events = asyncio.run(run())
    assert [e.kind for e in events] == ["text", "done"]
    assert events[-1].message.text() == "hi"
