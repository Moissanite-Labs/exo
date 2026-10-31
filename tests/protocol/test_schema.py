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
    assert len(COMMANDS) <= 30 and len(EVENTS) < 30  # D-026 used the last command slot
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
        # a non-string type is unknown, not a TypeError out of the decoder
        (json.dumps({"id": "c", "type": [], "body": {}}), "unknown_command"),
        (json.dumps({"id": "c", "type": {}, "body": {}}), "unknown_command"),
        (json.dumps({"id": "c", "type": 1, "body": {}}), "unknown_command"),
        (json.dumps({"id": "c", "type": None, "body": {}}), "unknown_command"),
        pytest.param("[" * 100_000, "bad_json", id="nesting-too-deep"),
        # primitive fields are checked: int is int (not bool), float takes int, str is str, bool is bool
        (json.dumps({"id": "c", "type": "events.subscribe", "body": {"since": "5"}}), "bad_body"),
        (json.dumps({"id": "c", "type": "events.subscribe", "body": {"since": None}}), "bad_body"),
        (json.dumps({"id": "c", "type": "events.subscribe", "body": {"since": 1.5}}), "bad_body"),
        (json.dumps({"id": "c", "type": "events.subscribe", "body": {"since": True}}), "bad_body"),
        (json.dumps({"id": "c", "type": "events.subscribe", "body": {"since": [5]}}), "bad_body"),
        (json.dumps({"id": "c", "type": "events.subscribe", "body": {"session": 7}}), "bad_body"),
        (json.dumps({"id": "c", "type": "session.list", "body": {"limit": "x"}}), "bad_body"),
        (json.dumps({"id": "c", "type": "session.undo", "body": {"session": "s", "turns": 1.5}}), "bad_body"),
        (json.dumps({"id": "c", "type": "session.get", "body": {"session": 1}}), "bad_body"),
        (json.dumps({"id": "c", "type": "session.get", "body": {"session": None}}), "bad_body"),
        (json.dumps({"id": "c", "type": "hello", "body": {"screen_cols": "80"}}), "bad_body"),
        (json.dumps({"id": "c", "type": "turn.send", "body": {"session": "s", "parts": [{"type": "text", "text": 1}]}}),
         "bad_body"),
        (json.dumps({"id": "c", "type": "peers", "body": {"action": "list", "local_only": "yes"}}), "bad_body"),
        (json.dumps({"id": "c", "type": "peers", "body": {"action": "list", "local_only": 1}}), "bad_body"),
    ],
)
def test_bad_commands(raw, code):
    with pytest.raises(ProtocolError) as e:
        Command.from_json(raw)
    assert e.value.code == code


@pytest.mark.parametrize(
    "body,want",
    [
        ({"type": "events.subscribe", "body": {"since": 5, "session": None}}, s.EventsSubscribe(since=5)),
        ({"type": "session.list", "body": {"limit": 3, "seat": "a"}}, s.SessionList(limit=3, seat="a")),
        ({"type": "session.undo", "body": {"session": "s", "turns": 2}}, s.SessionUndo(session="s", turns=2)),
        ({"type": "hello", "body": {"screen_cols": None}}, s.Hello()),
        ({"type": "config.set", "body": {"key": "k", "value": [1, "x", True]}}, s.ConfigSet(key="k", value=[1, "x", True])),
    ],
)
def test_well_typed_commands_still_decode(body, want):
    assert Command.from_json(json.dumps({"id": "c", **body})).body == want


def test_float_and_bool_fields_are_checked():
    from exo.core.types import from_dict

    assert from_dict(Usage, {"cost_usd": 2}).cost_usd == 2  # an int is a float
    assert from_dict(Usage, {"cost_usd": 0.5}).cost_usd == 0.5
    for bad in ({"cost_usd": True}, {"cost_usd": "1"}, {"input_tokens": False}):
        with pytest.raises(ValueError):
            from_dict(Usage, bad)
    assert from_dict(ToolResult, {"call_id": "a", "is_error": True}).is_error is True
    with pytest.raises(ValueError):
        from_dict(ToolResult, {"call_id": "a", "is_error": 1})


def test_lenient_events_keep_wrong_typed_primitives():
    # Old rows in the event log decode with strict=False: the primitive check must not start rejecting them.
    raw = json.dumps({"id": "e", "seq": 1, "type": "tool.completed",
                      "body": {"result": {"call_id": 5, "content": []}, "duration_ms": "7"}})
    with pytest.raises(ProtocolError) as e:
        Event.from_json(raw)
    assert e.value.code == "bad_body"
    body = Event.from_json(raw, strict=False).body
    assert body.duration_ms == "7" and body.result.call_id == 5


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


def test_model_call_preserves_legacy_positional_constructor_order():
    usage = Usage(input_tokens=7)
    attempts = [{"route": "fixture:served", "outcome": "ok"}]
    call = s.ModelCall(2, "main", "fixture:asked", "fixture:served", attempts, 10, 20,
                       usage, "end", "", 3)
    assert (call.iteration, call.role, call.requested, call.route) == (
        2, "main", "fixture:asked", "fixture:served")
    assert call.attempts is attempts and call.usage is usage
    assert (call.ttft_ms, call.total_ms, call.stop_reason, call.error, call.retries) == (10, 20, "end", "", 3)
    assert call.call_id == ""
    assert s.ModelCall(call_id="m_fixture").call_id == "m_fixture"
