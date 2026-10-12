"""Scripted provider for tests and offline runs. No network.

Each call to ``stream`` consumes one scripted turn. A turn is one of:
- a ``str``: streamed as text, then ``done``;
- a ``Message``: its reasoning, text and tool calls streamed, then ``done``;
- a ``list[ProviderEvent]``: yielded as is (add your own ``done`` or ``error``);
- a ``ProviderEvent`` of kind ``error``: yielded alone (use for 429s and outages).
When the script runs out, the last turn repeats (``repeat=True``) or an error ends the call.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from exo.core.types import Message, ProviderEvent, ToolCall, ToolSpec, Usage

Turn = str | Message | list[ProviderEvent] | ProviderEvent


def tool_turn(name: str, arguments: dict[str, Any] | None = None, call_id: str = "call_1", text: str = "") -> Message:
    return Message.assistant(text, [ToolCall(id=call_id, name=name, arguments=arguments or {})])


def rate_limited(retry_after: float | None = None) -> ProviderEvent:
    from exo.core.models.base import error_event

    return error_event("HTTP 429: rate limited", 429, retry_after=retry_after)


class FakeProvider:
    name = "fake"

    def __init__(self, script: list[Turn] | None = None, *, name: str | None = None, repeat: bool = False,
                 chunk: int = 0) -> None:
        self.script = list(script or [])
        self.repeat = repeat
        self.chunk = chunk  # split text into deltas of this many chars (0 = one delta)
        self.calls: list[dict[str, Any]] = []
        self._last: Turn | None = None
        if name:
            self.name = name

    def _chunks(self, text: str) -> list[str]:
        if not self.chunk:
            return [text]
        return [text[i:i + self.chunk] for i in range(0, len(text), self.chunk)]

    async def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        self.calls.append({"messages": list(messages), "tools": list(tools), "opts": dict(opts)})
        if self.script:
            turn = self._last = self.script.pop(0)
        elif self.repeat and self._last is not None:
            turn = self._last
        else:
            yield ProviderEvent(kind="error", error="fake provider: script exhausted", retryable=False)
            return
        if isinstance(turn, ProviderEvent):
            yield turn
            return
        if isinstance(turn, list):
            for ev in turn:
                yield ev
            return
        msg = Message.assistant(turn) if isinstance(turn, str) else turn
        for p in msg.content:
            if p.type in ("text", "reasoning") and p.text:
                for piece in self._chunks(p.text):
                    yield ProviderEvent(kind="text" if p.type == "text" else "reasoning", text=piece)
        for tc in msg.tool_calls:
            yield ProviderEvent(kind="tool_call", tool_call=tc)
        usage = Usage(input_tokens=sum(len(m.text()) for m in messages) // 4, output_tokens=len(msg.text()) // 4)
        yield ProviderEvent(kind="usage", usage=usage)
        yield ProviderEvent(kind="done", stop_reason="tool_use" if msg.tool_calls else "end", message=msg, usage=usage)


class RuleFake:
    """Restart-safe scripted provider, configured as data (``models.fake`` in config.json).

    ``rules`` is a list of ``{"match": "substring", "steps": [...]}``. The latest user message
    picks the first rule whose ``match`` it contains; the number of assistant replies since
    that message picks the step, so a restarted server continues where it was. A step is a
    string (text reply) or ``{"tool": name, "args": {...}, "text": ""}``. ``reply`` is the
    answer when no rule matches.
    """

    name = "fake"
    DEFAULT = ("Exo is running on the offline fake model. Set ANTHROPIC_API_KEY, or models "
               "in config.json, to use a real model.")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.rules = list(config.get("rules") or [])
        self.reply = config.get("reply") or self.DEFAULT

    def _turn(self, messages: list[Message]) -> Turn:
        users = [i for i, m in enumerate(messages) if m.role == "user"]
        if not users:
            return self.reply
        last = users[-1]
        step = sum(1 for m in messages[last + 1:] if m.role == "assistant")
        text = messages[last].text()
        for rule in self.rules:
            if rule.get("match", "") in text:
                steps = rule.get("steps") or [self.reply]
                spec = steps[min(step, len(steps) - 1)]
                if isinstance(spec, dict):
                    call_id = f"call_{len(messages)}_{step}"
                    return tool_turn(spec["tool"], spec.get("args"), call_id, spec.get("text", ""))
                return str(spec)
        return self.reply

    def stream(self, messages: list[Message], tools: list[ToolSpec], **opts: Any) -> AsyncIterator[ProviderEvent]:
        return FakeProvider([self._turn(messages)]).stream(messages, tools, **opts)
