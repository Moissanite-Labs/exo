"""Exo wire protocol, version 1 (docs/STANDALONE.md section 5).

Commands in, events out. One ordered event stream per server; every event has
a ``seq`` so a client resumes with ``events.subscribe(since=seq)``.

The interface started fixed at wave 1 and has grown additively since. Stdlib only. Rules:
- At most 30 commands and under 30 event types (tests enforce the cap; D-026 used the last command slot).
  Today there are 30 commands and 29 events. New features add fields or a ``command.run`` built-in, not a type.
- Additive changes only within a protocol version: new optional fields with
  defaults, or new types. Never rename or remove.
- Commands are strict: an unknown type, an unknown field or a wrong-typed field is rejected.
- Events decode strictly by default; clients pass ``strict=False`` to accept
  newer servers (unknown fields dropped, unknown types kept as raw dicts, unknown Literal values kept as
  sent). ``Client.events`` skips and logs an event it still cannot decode, and the feed goes on.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypeVar, dataclass_transform

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
        dataclass(cls)
        cls.TYPE = name  # type: ignore[attr-defined]
        table[name] = cls
        return cls

    return deco


@dataclass_transform()
def command(name: str) -> Callable[[type[T]], type[T]]:
    return _register(COMMANDS, name, MAX_COMMANDS)


@dataclass_transform()
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
    status: str = ""  # set by session.list, session.get and session.resume: "live" while a turn runs, else "idle"
    agent: str | None = None  # the agent (seat) whose one memory this thread belongs to (D-028)
    kind: str = ""  # thread, private, scheduled or subagent; empty for a session outside any agent


@dataclass
class ErrorInfo:
    code: str
    message: str
    retryable: bool = False


# ---------------------------------------------------------------- commands (30, the cap)


@command("hello")
class Hello:
    """Handshake. Server replies with its protocol version and capabilities."""

    protocol: int = PROTOCOL_VERSION
    client: str = ""
    capabilities: list[str] = field(default_factory=list)  # diff, markdown, images, voice
    screen_cols: int | None = None


@command("session.create")
class SessionCreate:
    """Start a thread of the agent: a new focus in the same memory (D-028). ``kind="private"`` makes a thread
    whose entries are logged but never enter the agent's tree, other threads or search."""

    title: str = ""
    seat: str | None = None
    model: str | None = None
    cwd: str | None = None
    kind: str = ""


@command("session.list")
class SessionList:
    limit: int = 50
    seat: str | None = None


@command("approvals.list")
class ApprovalsList:
    """Approvals still waiting for an answer (a client that connects late can show and answer them)."""

    session: str | None = None


@command("session.get")
class SessionGet:
    session: str


@command("session.fork")
class SessionFork:
    session: str
    at_message: str | None = None  # fork after this message id; None = at the end
    title: str = ""


@command("session.resume")
class SessionResume:
    session: str


@command("session.undo")
class SessionUndo:
    session: str
    turns: int = 1


@command("turn.send")
class TurnSend:
    """Send user input. mode: send (start a turn), queue (run after the current
    turn), steer (inject into the running turn at the next tool boundary)."""

    session: str
    text: str = ""
    parts: list[ContentPart] = field(default_factory=list)
    mode: Literal["send", "queue", "steer"] = "send"


@command("turn.interrupt")
class TurnInterrupt:
    session: str


@command("approval.respond")
class ApprovalRespond:
    request_id: str
    decision: Literal["allow", "deny", "allow_always"]
    note: str = ""
    client: str = ""  # a label, or seat:<name> with sig; who answered is stamped from the connection (D-039)
    binding: str = ""  # D-033: the ApprovalRequested.binding the answer was given for; a mismatch is refused
    sig: str = ""  # D-033: a seat's Ed25519 signature (exo.core.approvals.sign_approval); required for seat:<name>


@command("question.answer")
class QuestionAnswer:
    question_id: str
    answer: str


@command("command.run")
class CommandRun:
    """Run a slash command, e.g. name="exo", args="handoff". The core answers some built-in commands itself
    (the command cap is full, so new ones go here, not in new command types): ``trace``, ``findings``,
    ``context`` (what the next model call for ``session`` carries, read-only) and ``compact`` (summarize now
    what is eligible in the agent's memory; runs in the core that holds the agent lock) and ``unsteer`` (pull
    back a queued steer message before it is delivered: args is its id, empty means the newest, D-045) and ``rename``
    (set the session title to args, principal only, announced as ``session.updated``, D-069)."""

    name: str
    args: str = ""
    session: str | None = None


@command("commands.list")
class CommandsList:
    session: str | None = None


@command("events.subscribe")
class EventsSubscribe:
    """Stream events with seq > since. session=None means all sessions."""

    since: int = 0
    session: str | None = None


@command("search")
class Search:
    query: str
    scope: Literal["sessions", "store", "all"] = "all"
    limit: int = 20


@command("config.get")
class ConfigGet:
    key: str | None = None  # None = whole effective config


@command("config.set")
class ConfigSet:
    key: str
    value: Any = None


@command("models.list")
class ModelsList:
    pass


@command("models.set")
class ModelsSet:
    model: str
    role: str = "main"  # router role, e.g. main, summarize, review
    session: str | None = None  # None = default for new sessions


@command("tools.list")
class ToolsList:
    session: str | None = None


@command("skills.list")
class SkillsList:
    pass


@command("tasks.list")
class TasksList:
    session: str | None = None


@command("seat.list")
class SeatList:
    pass


@command("taste.list")
class TasteList:
    pass


@command("taste.edit")
class TasteEdit:
    action: Literal["add", "update", "remove"]
    rule_id: str | None = None
    text: str = ""


@command("feedback.send")
class FeedbackSend:
    """The principal's verdict on an answer: the learning loop's main signal (D-009, D-022).
    edit and should_have_been carry the better version in text."""

    session: str
    verdict: Literal["accept", "edit", "reject", "should_have_been"]
    message_id: str | None = None  # None = the last assistant message
    text: str = ""


@command("ui.respond")
class UiRespond:
    """What the user did with a panel. Dismissed and ignored panels teach too (D-014)."""

    panel_id: str
    action: Literal["opened", "dismissed", "acted", "ignored"]
    value: dict[str, Any] = field(default_factory=dict)  # e.g. {"choice": "b"} or {"line": 12, "comment": "..."}


@command("schedule.edit")
class ScheduleEdit:
    """Cron jobs. A run is a fresh session that reports through task.updated (D-022).
    when: a 5-field cron line ("0 9 * * *") or "every <n>m|h|d"."""

    action: Literal["list", "add", "remove", "pause", "resume", "run_now"]
    job_id: str | None = None
    when: str = ""
    prompt: str = ""
    title: str = ""
    seat: str | None = None
    tier: Literal["fast", "standard", "deep"] = "fast"
    deliver: str = "notice"  # notice, or a channel id


@command("peers")
class Peers:
    """Live sessions messaging each other (D-026). One command with an action, because the command cap leaves
    one slot: ``list`` shows peers, ``send`` posts a message from this daemon (``via`` says whether a person
    or a model wrote it), ``deliver`` and ``receipt`` are the daemon-to-daemon calls (a signed envelope in,
    a delivery receipt back). ``bus`` is the bus channel handing in one inbox envelope (D-040): the core checks it
    against the seat's pinned key and puts it through the same hold and trust policy as a peer message."""

    action: Literal["list", "send", "deliver", "receipt", "bus"] = "list"
    to: str = ""  # send: a peer name from list, or a bus seat; bus: the chat id (seat, team:<name> or group)
    text: str = ""  # send; bus: the envelope's text, decrypted by the channel
    session: str | None = None  # send: the sending session; list: the asking session; bus: the chat's session
    via: Literal["agent", "human"] = "human"  # send: who wrote it. A person typing /send is the principal.
    local_only: bool = False  # list: this daemon's sessions only (what another daemon asks)
    envelope: dict[str, Any] = field(default_factory=dict)  # deliver, receipt; bus: the inbox envelope as received
    pub: str = ""  # bus: the sender seat's key from the bus registry (pinned on first sight, D-040)


# ---------------------------------------------------------------- events (29, tests require fewer than 30)


@event("session.created")
class SessionCreated:
    session: SessionInfo


@event("session.updated")
class SessionUpdated:
    session: SessionInfo


@event("turn.started")
class TurnStarted:
    turn_id: str
    model: str = ""
    soul: str = ""  # short hash of the seat's SOUL.md this turn ran under (it is re-read when the file changes)


@event("turn.completed")
class TurnCompleted:
    turn_id: str
    stop_reason: str = "end"
    usage: Usage | None = None
    # wall_ms, model_ms, tool_ms, approval_ms, model_calls, fallbacks, tool_errors (D-025), and interrupted_tool when a tool was cut short; empty on old sessions
    timing: dict[str, Any] = field(default_factory=dict)


@event("message.delta")
class MessageDelta:
    message_id: str
    text: str


@event("message.completed")
class MessageCompleted:
    message: Message


@event("reasoning.delta")
class ReasoningDelta:
    message_id: str
    text: str


@event("tool.started")
class ToolStarted:
    call: ToolCall
    risk: str = "low"


@event("tool.output")
class ToolOutput:
    """Partial output while a tool runs (shell stdout, progress)."""

    call_id: str
    text: str


@event("tool.completed")
class ToolCompleted:
    result: ToolResult
    duration_ms: int = 0
    is_error: bool = False  # same as result.is_error, for readers that skip the result body


@event("approval.requested")
class ApprovalRequested:
    """Server-to-client request; any client answers with approval.respond."""

    request_id: str
    summary: str
    risk: str = "medium"
    call: ToolCall | None = None
    options: list[str] = field(default_factory=lambda: ["allow", "deny", "allow_always"])
    # D-033: who may answer ("principal", "seat:<name>"); empty = any local client that is not a peer.
    approvers: list[str] = field(default_factory=list)
    # D-033: sha256 of the exact outbound text, target and arguments; an edit gives a new hash and a new ask.
    binding: str = ""


@event("approval.resolved")
class ApprovalResolved:
    request_id: str
    decision: str
    by: str = ""  # client or rule that decided


@event("steer.cancelled")
class SteerCancelled:
    """A queued steer message was pulled back before it was delivered (D-045); clients drop its pending line.
    A delivered one shows up as ``message.completed`` instead."""

    message_id: str
    text: str = ""


@event("question.asked")
class QuestionAsked:
    question_id: str
    text: str
    options: list[str] = field(default_factory=list)


@event("context.compacted")
class ContextCompacted:
    before_tokens: int
    after_tokens: int
    summary_message_id: str | None = None


@event("memory.summarized")
class MemorySummarized:
    """One node of a summary tree finished (D-028): a leaf over log entries ``start``-``end`` or a merge of two
    nodes. Upkeep of memory, not a compaction of the live context (that is ``context.compacted``)."""

    node_id: str
    level: int
    start: int
    end: int
    before_tokens: int
    after_tokens: int
    tree: str = ""  # agent:<id> for an agent's tree, else the session id


@event("job.updated")
class JobUpdated:
    """A background shell job changed state (D-032, docs/JOBS.md): ``running`` when it starts, then one of
    ``done`` (exit 0), ``failed`` (non-zero exit), ``killed``, ``timeout`` or ``lost`` (exod restarted).
    One type with a state, as ``task.updated`` does, because the protocol has few event slots left."""

    job_id: str
    state: str = "running"
    command: str = ""
    exit_code: int | None = None
    elapsed_ms: int = 0
    log: str = ""
    detail: str = ""  # plain English, e.g. why it ended


@event("handoff.written")
class HandoffWritten:
    path: str
    summary: str = ""


@event("task.updated")
class TaskUpdated:
    """Subagents and board tasks."""

    task_id: str
    title: str = ""
    status: str = ""  # queued, running, done, failed, cancelled
    child_session: str | None = None
    detail: str = ""  # plain English for people (docs/AGENT-WIRE.md)
    packet: str = ""  # the SYM-2P brief or receipt line, for replay and audit; never shown raw


@event("usage.updated")
class UsageUpdated:
    usage: Usage  # this call
    session_total: Usage | None = None
    model: str = ""  # the main route the turn was started on, provider:model


@event("decision.queued")
class DecisionQueued:
    decision_id: str
    title: str
    source: str = ""
    detail: str = ""


@event("taste.updated")
class TasteUpdated:
    """Learned taste shown inline with a link to the editable file (D-012)."""

    rule_id: str
    action: Literal["add", "update", "remove"]
    text: str = ""
    path: str = ""


@event("ui.panel")
class UiPanel:
    """A panel the model (via the ui tool) or a command opens beside the transcript (D-014).
    Every client renders what it can: full panel, compact text, or a link. data is kind-specific
    and plain JSON; text is the plain-English fallback every client must be able to show."""

    panel_id: str
    action: Literal["open", "update", "close"]
    kind: Literal["markdown", "diff", "table", "tasks", "approval", "taste", "choice", "file", "chart"] = "markdown"
    title: str = ""
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    reason: str = ""  # why it was opened, one line; feeds the learning loop


@event("peer.message")
class PeerMessage:
    """A message from another session reached this one (D-026). Clients show it with the sender, never as the
    user. ``trust`` is principal, same-principal, other, remote or unverified."""

    msg_id: str
    sender: str
    text: str
    trust: str = "other"
    sender_session: str | None = None
    mode: str = ""  # steered (into the running turn), queued (for the next turn) or started (a turn)


@event("peer.delivery")
class PeerDelivery:
    """Where a peer message stands (D-026). ``direction`` out is the sender's receipt, in is the receiver's own
    record. held waits for the principal; released, denied and expired close it."""

    msg_id: str
    state: Literal["delivered", "held", "released", "denied", "expired"]
    peer: str = ""  # the other side's name
    direction: Literal["in", "out"] = "out"
    detail: str = ""  # plain English


@event("model.call")
class ModelCall:
    """One model call as the harness saw it (D-025): what was asked, which route served it, every attempt.
    attempts: [{route, outcome: ok|timeout|spend_limit|error|fallback, error, ms}]. An outcome of spend_limit means
    the provider said the route is over its spend limit (HTTP 402), so the call's cost figure is a floor.
    ttft_ms is None when nothing streamed."""

    iteration: int = 0
    role: str = ""  # the router role asked for, when the provider is a router
    requested: str = ""  # the route the turn asked for, provider:model
    route: str = ""  # the route that served it ("" when none did)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    ttft_ms: int | None = None
    total_ms: int = 0
    usage: Usage | None = None
    stop_reason: str = ""
    error: str = ""
    retries: int = 0  # loop-level retries before this outcome
    call_id: str = ""  # assistant message ID identifying this model call


@event("harness.error")
class HarnessError:
    """An exception the harness caught in a hook, handler, plugin or client event path (D-025).
    Never holds keys, headers or environment values (scrubbed before it is written)."""

    where: str
    error_type: str = ""
    message: str = ""
    traceback: str = ""  # last few lines only


@event("notice")
class Notice:
    text: str
    level: Literal["info", "warn"] = "info"
    source: str = ""  # model_route identifies a transient routing warning
    call_id: str = ""  # matching ModelCall; empty on legacy/general notices


@event("error")
class Error:
    code: str
    message: str
    retryable: bool = False
    command_id: str | None = None


# ---------------------------------------------------------------- envelopes


def _loads(text: str | bytes) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError, RecursionError) as exc:  # RecursionError: nesting too deep
        raise ProtocolError("bad_json", str(exc)) from None
    if not isinstance(data, dict):
        raise ProtocolError("bad_envelope", "envelope must be an object")
    return data


def _body(table: dict[str, type], kind: str, type_: Any, raw: Any, strict: bool) -> Any:
    cls = table.get(type_) if isinstance(type_, str) else None  # a list or object type is unknown, not a crash
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
