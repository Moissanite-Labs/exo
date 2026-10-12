import dataclasses
import json

import pytest

from exo.core.types import ContentPart, Message, ToolCall, ToolResult, Usage
from exo.protocol import schema as s
from exo.protocol.schema import COMMANDS, EVENTS, Command, Event, ProtocolError, Response

SPEC_COMMANDS = {
    "hello", "session.create", "session.list", "session.get", "session.fork", "session.resume",
    "turn.send", "turn.interrupt", "approval.respond", "question.answer", "command.run",
    "events.subscribe", "search", "config.get", "config.set", "models.list", "tools.list",
    "skills.list", "tasks.list", "seat.list",
}
SPEC_EVENTS = {
    "session.created", "session.updated", "turn.started", "turn.completed", "message.delta",
    "message.completed", "reasoning.delta", "tool.started", "tool.output", "tool.completed",
    "approval.requested", "approval.resolved", "question.asked", "context.compacted",
    "handoff.written", "task.updated", "usage.updated", "decision.queued", "notice", "error",
}


def test_surface_covers_spec_and_stays_small():
    assert SPEC_COMMANDS <= set(COMMANDS)
    assert SPEC_EVENTS <= set(EVENTS)
    assert len(COMMANDS) < 30 and len(EVENTS) < 30
    for table in (COMMANDS, EVENTS):
        for name, cls in table.items():
            assert dataclasses.is_dataclass(cls) and cls.TYPE == name


def test_cap_and_duplicates_enforced():
    with pytest.raises(RuntimeError):
        s.command("hello")(type("Dup", (), {}))


def _all_defaulted(cls):
    return all(
        f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
        for f in dataclasses.fields(cls)
    )


def test_every_defaultable_command_roundtrips():
    for name, cls in COMMANDS.items():
        if _all_defaulted(cls):
            cmd = Command.of(cls())
            back = Command.from_json(cmd.to_json())
            assert back == cmd and back.type == name


def test_command_roundtrip_nested():
    cmd = Command.of(s.TurnSend(session="s1", text="hi", parts=[ContentPart.of_text("x")], mode="steer"))
    back = Command.from_json(cmd.to_json())
    assert back == cmd
    assert isinstance(back.body.parts[0], ContentPart)


def test_unknown_command_rejected():
    with pytest.raises(ProtocolError) as e:
        Command.from_json(json.dumps({"id": "c1", "type": "session.nuke", "body": {}}))
    assert e.value.code == "unknown_command"


@pytest.mark.parametrize(
    "raw,code",
    [
        ("not json", "bad_json"),
        ("[1]", "bad_envelope"),
        (json.dumps({"type": "hello", "body": {}}), "bad_envelope"),
        (json.dumps({"id": "c", "type": "hello", "body": {}, "x": 1}), "bad_envelope"),
        (json.dumps({"id": "c", "type": "hello", "body": {"zzz": 1}}), "bad_body"),
        (json.dumps({"id": "c", "type": "session.get", "body": {}}), "bad_body"),
        (json.dumps({"id": "c", "type": "turn.send", "body": {"session": "s", "mode": "x"}}), "bad_body"),
    ],
)
def test_bad_commands(raw, code):
    with pytest.raises(ProtocolError) as e:
        Command.from_json(raw)
    assert e.value.code == code


def test_command_of_rejects_unregistered_body():
    with pytest.raises(ProtocolError):
        Command.of(s.SessionInfo(id="x"))


EVENT_SAMPLES = [
    s.SessionCreated(session=s.SessionInfo(id="s1", title="t")),
    s.TurnCompleted(turn_id="t1", usage=Usage(1, 2)),
    s.MessageCompleted(message=Message.assistant("hi", [ToolCall(id="a", name="read", arguments={"p": 1})])),
    s.ToolStarted(call=ToolCall(id="a", name="bash"), risk="high"),
    s.ToolCompleted(result=ToolResult.of_text("a", "ok"), duration_ms=5),
    s.ApprovalRequested(request_id="r1", summary="post tweet", call=ToolCall(id="a", name="post")),
    s.UsageUpdated(usage=Usage(1, 1), session_total=Usage(5, 5, cost_usd=0.1)),
    s.TasteUpdated(rule_id="r", action="add", text="no em dashes", path="taste.md"),
    s.Error(code="x", message="y"),
]


@pytest.mark.parametrize("body", EVENT_SAMPLES, ids=lambda b: b.TYPE)
def test_event_roundtrip(body):
    ev = Event.of(body, seq=7, session="s1")
    data = json.loads(ev.to_json())
    assert list(data) == ["id", "seq", "session", "type", "ts", "body"]
    assert Event.from_json(ev.to_json()) == ev


def test_event_forward_compat():
    raw = json.dumps({"id": "e", "seq": 1, "session": None, "type": "notice", "ts": "t",
                      "body": {"text": "hi", "future": 1}})
    with pytest.raises(ProtocolError):
        Event.from_json(raw)
    assert Event.from_json(raw, strict=False).body.text == "hi"
    raw2 = json.dumps({"id": "e", "seq": 2, "type": "future.thing", "body": {"a": 1}})
    with pytest.raises(ProtocolError) as e:
        Event.from_json(raw2)
    assert e.value.code == "unknown_event"
    assert Event.from_json(raw2, strict=False).body == {"a": 1}


def test_response_roundtrip():
    ok = Response(id="c1", result={"session": "s1"})
    bad = Response(id="c2", ok=False, error=s.ErrorInfo(code="unknown_command", message="nope"))
    assert Response.from_json(ok.to_json()) == ok
    assert Response.from_json(bad.to_json()) == bad
