"""Tool-call arguments that are not a JSON object fail the call visibly (exo-desktop #52).

They used to become ``{}``, so the tool ran with no arguments. A tool with nothing required then did something
the model never asked for, and a truncated call (cut at max_tokens) ran as an empty one.
"""
import asyncio
import json

from exo.core.loop import Loop
from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.base import parse_json_args
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.sessions import EventBus, SessionStore
from exo.core.tools import PRE_TOOL_CALL, Registry, ToolContext
from exo.core.types import Message, ToolCall, to_dict

from .conftest import Recorder, collect

BAD = ('{"path": "a.txt", "limit"', "[1, 2]", '"a.txt"', "12", "null", "{'path': 'a.txt'}")


def _sse(*chunks):
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def _openai_call(args):
    tool = {"index": 0, "id": "call_1", "type": "function", "function": {"name": "list_notes", "arguments": args}}
    return _sse({"choices": [{"index": 0, "delta": {"tool_calls": [tool]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})


def _anthropic_call(args):
    rows = [("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "id": "toolu_1", "name": "list_notes", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "input_json_delta", "partial_json": args}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                               "usage": {"output_tokens": 3}}),
            ("message_stop", {"type": "message_stop"})]
    return b"".join(f"event: {e}\ndata: {json.dumps(d)}\n\n".encode() for e, d in rows)


def _bad(args):
    """A refused set of arguments: still an empty dict for replay and hooks, with the reason beside it."""
    return isinstance(args, dict) and args == {} and bool(getattr(args, "error", ""))


def test_parse_keeps_good_and_empty_arguments_and_marks_the_rest():
    assert parse_json_args('{"path": "a.txt"}') == {"path": "a.txt"}
    for empty in ("", "  ", "{}"):
        args = parse_json_args(empty)
        assert args == {} and not getattr(args, "error", ""), repr(empty)  # no arguments is a valid call
    for raw in BAD:
        args = parse_json_args(raw)
        assert _bad(args), raw
        assert json.dumps(args) == "{}" and to_dict(args) == {} and type(to_dict(args)) is dict


def test_transports_mark_malformed_arguments_instead_of_emptying_them():
    for raw in BAD:
        rec = Recorder((200, _openai_call(raw), {}))
        events = collect(OpenAIChatProvider("http://h/v1", client=rec.client()).stream([Message.user("x")], []))
        call = next(e.tool_call for e in events if e.kind == "tool_call")
        assert call.name == "list_notes" and _bad(call.arguments), raw
        rec = Recorder((200, _anthropic_call(raw), {}))
        events = collect(AnthropicProvider(api_key="k", client=rec.client()).stream(
            [Message.user("x")], [], model="claude-opus-5-5"))
        call = next(e.tool_call for e in events if e.kind == "tool_call")
        assert call.name == "list_notes" and _bad(call.arguments), raw
    rec = Recorder((200, _openai_call('{"folder": "inbox"}'), {}))
    events = collect(OpenAIChatProvider("http://h/v1", client=rec.client()).stream([Message.user("x")], []))
    assert next(e.tool_call for e in events if e.kind == "tool_call").arguments == {"folder": "inbox"}


def _registry(ran, hooked):
    reg = Registry()

    @reg.tool("list_notes", "list notes", parameters={"type": "object", "properties": {"folder": {"type": "string"}}})
    def list_notes(args, ctx):
        ran.append(dict(args))
        return "notes: " + str(args.get("folder") or "all")

    reg.add_hook(PRE_TOOL_CALL, lambda call, spec, ctx: hooked.append(call.id))
    return reg


def test_registry_refuses_a_call_whose_arguments_did_not_parse():
    ran, hooked = [], []
    reg = _registry(ran, hooked)
    for raw in BAD:
        res = reg.call_sync(ToolCall(id="c1", name="list_notes", arguments=parse_json_args(raw)))
        assert res.is_error and res.call_id == "c1", raw
        assert res.text().startswith("Invalid arguments for list_notes: "), res.text()
    assert ran == [] and hooked == []  # nothing ran, and no approval was asked for a call that cannot run
    assert reg.call_sync(ToolCall(id="c2", name="list_notes", arguments=parse_json_args(""))).text() == "notes: all"
    assert ran == [{}] and hooked == ["c2"]


def test_a_malformed_call_reaches_the_model_as_an_error_and_never_runs(tmp_path):
    """End to end: the Chat Completions stream, the loop, the registry, and the next request's replay."""
    ran, hooked = [], []
    reg = _registry(ran, hooked)
    rec = Recorder((200, _openai_call('{"folder": "inbox"'), {}),  # cut short, as at max_tokens
                   (200, _sse({"choices": [{"index": 0, "delta": {"content": "sorry"}, "finish_reason": "stop"}]},
                              {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1}}), {}))

    async def run_tool(call, lctx):
        return await reg.call(call, ToolContext(cwd=str(tmp_path), session=lctx["session"]))

    async def go():
        async with rec.client() as client:
            store = SessionStore(tmp_path / "sessions.sqlite")
            loop = Loop(store, OpenAIChatProvider("http://h/v1", client=client), bus=EventBus(store),
                        tool_specs=reg.specs(), run_tool=run_tool, retry_delay=0)
            s = await loop.create_session(title="t")
            res = await loop.send(s.id, "list my inbox notes")
            return res, store.messages(s.id)

    res, msgs = asyncio.run(go())
    assert res.stop_reason == "end" and res.message.text() == "sorry"
    assert ran == [] and hooked == []
    result = msgs[2].tool_results[0]
    assert msgs[2].role == "tool" and result.is_error and "not valid JSON" in result.text()
    replay = rec.body(1)["messages"]
    assert replay[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert replay[2]["role"] == "tool" and "Invalid arguments for list_notes" in replay[2]["content"]
