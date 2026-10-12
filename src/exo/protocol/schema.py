"""Exo wire protocol, version 1 (docs/STANDALONE.md section 5).

Commands in, events out. One ordered event stream per server; every event has
a ``seq`` so a client resumes with ``events.subscribe(since=seq)``.

Wave 1 interface: fixed before the wave. Stdlib only. Rules:
- Under 30 commands and under 30 event types (tests enforce the cap).
- Additive changes only within a protocol version: new optional fields with
  defaults, or new types. Never rename or remove.
- Commands are strict: unknown type or unknown field is rejected.
- Events decode strictly by default; clients pass ``strict=False`` to accept
  newer servers (unknown fields dropped, unknown types kept as raw dicts).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypeVar

from exo.core.types import ContentPart, Message, ToolCall, ToolResult, Usage, from_dict, to_dict

PROTOCOL_VERSION = 1
MAX_COMMANDS = 30
MAX_EVENTS = 30

COMMANDS: dict[str, type] = {}
EVENTS: dict[str, type] = {}

T = TypeVar("T")


class ProtocolError(ValueError):
    """Raised on bad input. ``code`` is one of: bad_json, bad_envelope,
    unknown_command, unknown_event, bad_body."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _register(table: dict[str, type], name: str, cap: int) -> Callable[[type[T]], type[T]]:
    def deco(cls: type[T]) -> type[T]:
        if name in table:
            raise RuntimeError(f"duplicate protocol type {name}")
        if len(table) >= cap:
            raise RuntimeError(f"protocol cap {cap} reached; add fields, not types")
        cls.TYPE = name  # type: ignore[attr-defined]
        table[name] = cls
        return cls

    return deco


def command(name: str) -> Callable[[type[T]], type[T]]:
    return _register(COMMANDS, name, MAX_COMMANDS)


def event(name: str) -> Callable[[type[T]], type[T]]:
    return _register(EVENTS, name, MAX_EVENTS)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- shared shapes


@dataclass
class SessionInfo:
    id: str
    title: str = ""
    seat: str | None = None
    model: str | None = None
    cwd: str | None = None
    parent: str | None = None  # session this was forked from
    created: str = ""
    updated: str = ""
    message_count: int = 0


@dataclass
class ErrorInfo:
    code: str
    message: str
    retryable: bool = False


# ---------------------------------------------------------------- commands (25)


@command("hello")
@dataclass
class Hello:
    """Handshake. Server replies with its protocol version and capabilities."""

    protocol: int = PROTOCOL_VERSION
    client: str = ""
    capabilities: list[str] = field(default_factory=list)  # diff, markdown, images, voice
    screen_cols: int | None = None


@command("session.create")
@dataclass
class SessionCreate:
    title: str = ""
    seat: str | None = None
    model: str | None = None
    cwd: str | None = None


@command("session.list")
@dataclass
class SessionList:
    limit: int = 50
    seat: str | None = None


@command("session.get")
@dataclass
class SessionGet:
    session: str


@command("session.fork")
@dataclass
class SessionFork:
    session: str
    at_message: str | None = None  # fork after this message id; None = at the end
    title: str = ""


@command("session.resume")
@dataclass
class SessionResume:
    session: str


@command("session.undo")
@dataclass
class SessionUndo:
    session: str
    turns: int = 1


@command("turn.send")
@dataclass
class TurnSend:
    """Send user input. mode: send (start a turn), queue (run after the current
    turn), steer (inject into the running turn at the next tool boundary)."""

    session: str
    text: str = ""
    parts: list[ContentPart] = field(default_factory=list)
    mode: Literal["send", "queue", "steer"] = "send"


@command("turn.interrupt")
@dataclass
class TurnInterrupt:
    session: str


@command("approval.respond")
@dataclass
class ApprovalRespond:
    request_id: str
    decision: Literal["allow", "deny", "allow_always"]
    note: str = ""
    client: str = ""  # who answered (a client or channel id); reaches post_approval_response


@command("question.answer")
@dataclass
class QuestionAnswer:
    question_id: str
    answer: str


@command("command.run")
@dataclass
class CommandRun:
    """Run a slash command, e.g. name="exo", args="handoff"."""

    name: str
    args: str = ""
    session: str | None = None


@command("commands.list")
@dataclass
class CommandsList:
    session: str | None = None


@command("events.subscribe")
@dataclass
class EventsSubscribe:
    """Stream events with seq > since. session=None means all sessions."""

    since: int = 0
    session: str | None = None


@command("search")
@dataclass
class Search:
    query: str
    scope: Literal["sessions", "store", "all"] = "all"
    limit: int = 20


@command("config.get")
@dataclass
class ConfigGet:
    key: str | None = None  # None = whole effective config


@command("config.set")
@dataclass
class ConfigSet:
    key: str
    value: Any = None


@command("models.list")
@dataclass
class ModelsList:
    pass


@command("models.set")
@dataclass
class ModelsSet:
    model: str
    role: str = "main"  # router role, e.g. main, summarize, review
    session: str | None = None  # None = default for new sessions


@command("tools.list")
@dataclass
class ToolsList:
    session: str | None = None


@command("skills.list")
@dataclass
class SkillsList:
    pass


@command("tasks.list")
@dataclass
class TasksList:
    session: str | None = None


@command("seat.list")
@dataclass
class SeatList:
    pass


@command("taste.list")
@dataclass
class TasteList:
    pass


@command("taste.edit")
@dataclass
class TasteEdit:
    action: Literal["add", "update", "remove"]
    rule_id: str | None = None
    text: str = ""


# ---------------------------------------------------------------- events (21)


@event("session.created")
@dataclass
class SessionCreated:
    session: SessionInfo


@event("session.updated")
@dataclass
class SessionUpdated:
    session: SessionInfo


@event("turn.started")
@dataclass
class TurnStarted:
    turn_id: str
    model: str = ""


@event("turn.completed")
@dataclass
class TurnCompleted:
    turn_id: str
    stop_reason: str = "end"
    usage: Usage | None = None


@event("message.delta")
@dataclass
class MessageDelta:
    message_id: str
    text: str


@event("message.completed")
@dataclass
class MessageCompleted:
    message: Message


@event("reasoning.delta")
@dataclass
class ReasoningDelta:
    message_id: str
    text: str


@event("tool.started")
@dataclass
class ToolStarted:
    call: ToolCall
    risk: str = "low"


@event("tool.output")
@dataclass
class ToolOutput:
    """Partial output while a tool runs (shell stdout, progress)."""

    call_id: str
    text: str


@event("tool.completed")
@dataclass
class ToolCompleted:
    result: ToolResult
    duration_ms: int = 0


@event("approval.requested")
@dataclass
class ApprovalRequested:
    """Server-to-client request; any client answers with approval.respond."""

    request_id: str
    summary: str
    risk: str = "medium"
    call: ToolCall | None = None
    options: list[str] = field(default_factory=lambda: ["allow", "deny", "allow_always"])


@event("approval.resolved")
@dataclass
class ApprovalResolved:
    request_id: str
    decision: str
    by: str = ""  # client or rule that decided


@event("question.asked")
@dataclass
class QuestionAsked:
    question_id: str
    text: str
    options: list[str] = field(default_factory=list)


@event("context.compacted")
@dataclass
class ContextCompacted:
    before_tokens: int
    after_tokens: int
    summary_message_id: str | None = None


@event("handoff.written")
@dataclass
class HandoffWritten:
    path: str
    summary: str = ""


@event("task.updated")
@dataclass
class TaskUpdated:
    """Subagents and board tasks."""

    task_id: str
    title: str = ""
    status: str = ""  # queued, running, done, failed, cancelled
    child_session: str | None = None
    detail: str = ""  # plain English for people (docs/AGENT-WIRE.md)
    packet: str = ""  # the SYM-2P brief or receipt line, for replay and audit; never shown raw


@event("usage.updated")
@dataclass
class UsageUpdated:
    usage: Usage  # this call
    session_total: Usage | None = None


@event("decision.queued")
@dataclass
class DecisionQueued:
    decision_id: str
    title: str
    source: str = ""
    detail: str = ""


@event("taste.updated")
@dataclass
class TasteUpdated:
    """Learned taste shown inline with a link to the editable file (D-012)."""

    rule_id: str
    action: Literal["add", "update", "remove"]
    text: str = ""
    path: str = ""


@event("notice")
@dataclass
class Notice:
    text: str
    level: Literal["info", "warn"] = "info"


@event("error")
@dataclass
class Error:
    code: str
    message: str
    retryable: bool = False
    command_id: str | None = None


# ---------------------------------------------------------------- envelopes


def _loads(text: str | bytes) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("bad_json", str(exc)) from None
    if not isinstance(data, dict):
        raise ProtocolError("bad_envelope", "envelope must be an object")
    return data


def _body(table: dict[str, type], kind: str, type_: Any, raw: Any, strict: bool) -> Any:
    cls = table.get(type_)
    if cls is None:
        if strict:
            raise ProtocolError(f"unknown_{kind}", repr(type_))
        return raw
    try:
        return from_dict(cls, raw if raw is not None else {}, strict=strict)
    except ValueError as exc:
        raise ProtocolError("bad_body", f"{type_}: {exc}") from None


@dataclass
class Command:
    """Client to server. ``id`` correlates the Response."""

    type: str
    body: Any
    id: str = field(default_factory=lambda: new_id("c"))

    @classmethod
    def of(cls, body: Any, id: str | None = None) -> Command:
        t = getattr(type(body), "TYPE", None)
        if COMMANDS.get(t) is not type(body):
            raise ProtocolError("unknown_command", repr(type(body).__name__))
        return cls(type=t, body=body, id=id or new_id("c"))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "body": to_dict(self.body)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | bytes) -> Command:
        data = _loads(text)
        extra = set(data) - {"id", "type", "body"}
        if extra or not isinstance(data.get("id"), str):
            raise ProtocolError("bad_envelope", "command needs id, type, body only")
        body = _body(COMMANDS, "command", data.get("type"), data.get("body"), strict=True)
        return cls(type=data["type"], body=body, id=data["id"])


@dataclass
class Response:
    """Server reply to one Command. ``result`` is command-specific JSON."""

    id: str
    ok: bool = True
    result: dict[str, Any] = field(default_factory=dict)
    error: ErrorInfo | None = None

    def to_json(self) -> str:
        return json.dumps(to_dict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | bytes) -> Response:
        try:
            return from_dict(cls, _loads(text), strict=False)
        except ValueError as exc:
            raise ProtocolError("bad_envelope", str(exc)) from None


@dataclass
class Event:
    """Server to clients. ``seq`` is assigned by the server, strictly increasing
    across the whole stream; ``session`` is None for global events."""

    seq: int
    type: str
    body: Any
    session: str | None = None
    ts: str = field(default_factory=now_ts)
    id: str = field(default_factory=lambda: new_id("e"))

    @classmethod
    def of(cls, body: Any, seq: int, session: str | None = None) -> Event:
        t = getattr(type(body), "TYPE", None)
        if EVENTS.get(t) is not type(body):
            raise ProtocolError("unknown_event", repr(type(body).__name__))
        return cls(seq=seq, type=t, body=body, session=session)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "session": self.session,
            "type": self.type,
            "ts": self.ts,
            "body": to_dict(self.body),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str | bytes, strict: bool = True) -> Event:
        data = _loads(text)
        if not isinstance(data.get("seq"), int) or not isinstance(data.get("type"), str):
            raise ProtocolError("bad_envelope", "event needs integer seq and string type")
        if strict and set(data) - {"id", "seq", "session", "type", "ts", "body"}:
            raise ProtocolError("bad_envelope", "unknown envelope fields")
        body = _body(EVENTS, "event", data["type"], data.get("body"), strict=strict)
        return cls(
            seq=data["seq"],
            type=data["type"],
            body=body,
            session=data.get("session"),
            ts=data.get("ts") or "",
            id=data.get("id") or "",
        )
