"""Core value types shared by models, tools, loop, sessions and protocol.

Wave 1 interface: fixed before the wave so parallel agents code against it.
Stdlib only. Every type round-trips through ``to_dict`` / ``from_dict``.
Change fields only additively (new optional fields with defaults).
"""

from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]
PartType = Literal["text", "image", "reasoning"]
Risk = Literal["low", "medium", "high"]
StopReason = Literal["end", "tool_use", "max_tokens", "interrupted", "error"]
ProviderEventKind = Literal["text", "reasoning", "tool_call", "usage", "done", "error", "notice"]


@dataclass
class ContentPart:
    """One piece of message content.

    text:      ``text`` holds the text.
    image:     ``data`` is base64, ``mime`` its type (or ``url`` instead of data).
    reasoning: ``text`` holds the thinking; ``signature`` is the provider's opaque
               token that must be sent back unchanged (Anthropic thinking blocks).
    """

    type: PartType
    text: str = ""
    data: str | None = None
    mime: str | None = None
    url: str | None = None
    signature: str | None = None

    @classmethod
    def of_text(cls, text: str) -> ContentPart:
        return cls(type="text", text=text)


class BadArguments(dict[str, Any]):
    """Tool-call arguments that were not a JSON object. It stays empty, so a replay to the provider sends ``{}``.
    ``error`` says what was wrong. The registry answers the call with it, and no hook or tool runs."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


@dataclass
class ToolCall:
    """A model's request to run a tool. ``arguments`` is already parsed JSON."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Outcome of one tool call, matched to it by ``call_id``."""

    call_id: str
    content: list[ContentPart] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def of_text(cls, call_id: str, text: str, is_error: bool = False) -> ToolResult:
        return cls(call_id=call_id, content=[ContentPart.of_text(text)], is_error=is_error)

    def text(self) -> str:
        return "".join(p.text for p in self.content if p.type == "text")


@dataclass
class Message:
    """One conversation message in Exo's provider-neutral shape.

    assistant messages may carry ``tool_calls``; ``tool`` messages carry
    ``tool_results`` (one or more; transports split or merge as their API needs).
    ``meta`` is free-form and never sent to a provider (cache hints, origin, seq).
    """

    role: Role
    content: list[ContentPart] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def system(cls, text: str) -> Message:
        return cls(role="system", content=[ContentPart.of_text(text)])

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role="user", content=[ContentPart.of_text(text)])

    @classmethod
    def assistant(cls, text: str = "", tool_calls: list[ToolCall] | None = None) -> Message:
        parts = [ContentPart.of_text(text)] if text else []
        return cls(role="assistant", content=parts, tool_calls=list(tool_calls or []))

    @classmethod
    def tool(cls, results: list[ToolResult]) -> Message:
        return cls(role="tool", tool_results=list(results))

    def text(self) -> str:
        return "".join(p.text for p in self.content if p.type == "text")


@dataclass
class Usage:
    """Token and cost accounting for one call (or a running total). ``input_tokens`` is the uncached input on every
    transport; cache reads and writes are counted apart, so the prompt is the sum of the three (D-080)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None

    def __add__(self, other: Usage) -> Usage:
        cost = None
        if self.cost_usd is not None or other.cost_usd is not None:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        counts = {f.name: getattr(self, f.name) + getattr(other, f.name)
                  for f in dataclasses.fields(self) if f.name != "cost_usd"}
        return Usage(**counts, cost_usd=cost)


@dataclass
class ToolSpec:
    """What a model sees of a tool. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: Risk = "low"
    toolset: str = "core"


@dataclass
class ProviderEvent:
    """One item from ``Provider.stream``. A tagged union keyed by ``kind``.

    text       ``text`` is a delta of visible output.
    reasoning  ``text`` is a delta of thinking output.
    tool_call  ``tool_call`` is a complete call (providers assemble argument deltas).
    usage      ``usage`` for the call (may arrive more than once; last one wins).
    done       last event. ``stop_reason`` set; ``message`` is the assembled
               assistant Message (with reasoning signatures intact).
    notice     ``text`` is a plain-words warning (the router abandoned a route); the loop shows it.
    done, error also carry ``route`` and ``attempts`` from the router (D-025); bare providers leave them empty.
    error      ``error`` text; ``retryable``, ``status`` (HTTP code if any) and
               ``retry_after`` (seconds, if the provider said) drive router fallback.
    """

    kind: ProviderEventKind
    text: str = ""
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    stop_reason: StopReason | None = None
    message: Message | None = None
    error: str | None = None
    retryable: bool = False
    status: int | None = None
    retry_after: float | None = None  # seconds until the provider says to retry (error only)
    route: str = ""  # done, usage, error: the provider:model that served (or last failed) the call; set by the router
    attempts: list[dict[str, Any]] = field(default_factory=list)  # done, error, fallback notice: [{route, outcome, error, ms}]


@runtime_checkable
class Provider(Protocol):
    """A model transport. Implementations live in ``exo.core.models``.

    ``stream`` yields ProviderEvents and ends with exactly one ``done`` or
    ``error``. Common ``opts``: ``model``, ``max_tokens``, ``temperature``,
    ``reasoning`` (effort string), ``stop``, ``tool_choice`` (``"none"``: the
    tools stay declared but may not be called). Unknown opts are ignored.
    System prompts are ``Message(role="system")`` items in ``messages``.
    """

    name: str

    def stream(
        self, messages: list[Message], tools: list[ToolSpec], **opts: Any
    ) -> AsyncIterator[ProviderEvent]: ...


# ---------------------------------------------------------------- codec


def to_dict(obj: Any) -> Any:
    """Dataclass (or list/dict of them) to plain JSON-ready data."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


_HINTS: dict[type, dict[str, Any]] = {}
_PRIMITIVES: dict[Any, tuple[type, ...]] = {int: (int,), float: (int, float), str: (str,), bool: (bool,)}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def _decode(tp: Any, value: Any, strict: bool) -> Any:
    origin = typing.get_origin(tp)
    if origin is Literal:
        if strict and value not in typing.get_args(tp):  # loose decoding keeps a newer peer's value (forward compat)
            raise ValueError(f"{value!r} not one of {typing.get_args(tp)}")
        return value
    if origin in (typing.Union, types.UnionType):
        if value is None:
            return None
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _decode(args[0], value, strict) if len(args) == 1 else value
    if origin is list:
        (arg,) = typing.get_args(tp) or (Any,)
        if not isinstance(value, list):
            raise ValueError(f"expected list, got {type(value).__name__}")
        return [_decode(arg, v, strict) for v in value]
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return from_dict(tp, value, strict=strict)
    if strict and tp in _PRIMITIVES and (not isinstance(value, _PRIMITIVES[tp]) or
                                         (isinstance(value, bool) and tp is not bool)):
        raise ValueError(f"expected {tp.__name__}, got {type(value).__name__}")
    return value


def from_dict(cls: type, data: Any, strict: bool = True) -> Any:
    """Build dataclass ``cls`` from plain data. Nested types are decoded.

    strict=True rejects unknown fields and wrong-typed int, float, str and bool fields (a bool is not an int;
    an int is a float); strict=False drops unknown fields and takes primitives as they come (forward compat).
    Missing fields fall back to defaults; missing required fields raise.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__}: expected object, got {type(data).__name__}")
    names = {f.name for f in dataclasses.fields(cls)}
    extra = set(data) - names
    if extra and strict:
        raise ValueError(f"{cls.__name__}: unknown fields {sorted(extra)}")
    hints = _hints(cls)
    kwargs = {k: _decode(hints[k], v, strict) for k, v in data.items() if k in names}
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"{cls.__name__}: {exc}") from None
