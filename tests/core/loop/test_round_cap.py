"""The tool-round cap and its wrap-up call (D-064): configuration, /rounds, and a provider double that answers
like the Anthropic Messages API when tool blocks arrive without tool definitions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from exo import principal as pr
from exo import rounds
from exo.core.loop import Loop
from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.base import error_event
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.seats import parse as parse_seat
from exo.core.types import Message, ToolCall, ToolResult
from exo.server.engine import CommandFailed

from .test_loop import SPEC, Script, echo_tool, make, reply, run

SPIN = [ToolCall(id="c", name="echo", arguments={})]
REJECT = "messages: Requests which include `tool_use` or `tool_result` blocks must define tools."


class StrictAnthropic(Script):
    """Builds the real Anthropic request body, and returns the API's 400 for tool blocks without tools."""

    def __init__(self, *steps):
        super().__init__(*steps)
        self.bodies: list[dict] = []

    async def stream(self, messages, tools, **opts):
        body = AnthropicProvider(api_key="k-test").build_body(messages, tools, **opts)
        self.bodies.append(body)
        kinds = {b.get("type") for m in body["messages"] if isinstance(m["content"], list) for b in m["content"]}
        if kinds & {"tool_use", "tool_result"} and not body.get("tools"):
            self.calls.append(list(messages))
            yield error_event(f"anthropic 400: {{'type': 'invalid_request_error', 'message': '{REJECT}'}}", 400)
            return
        async for pe in super().stream(messages, tools, **opts):
            yield pe


def spin_then(tmp_path, final, cap=3, run_tool=echo_tool):
    prov = StrictAnthropic(*[reply("", SPIN) for _ in range(cap)], final)
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=run_tool, max_iterations=cap)

    async def go():
        s = await loop.create_session()
        return s, await loop.send(s.id, "spin")

    s, res = run(go())
    return prov, store, seen, s, res


def test_the_double_rejects_tool_history_without_tools():
    """The old wrap-up (tools=[]) against this double: the same 400 the live API gives."""
    history = [Message.user("go"), Message.assistant("", tool_calls=SPIN), Message.tool([ToolResult.of_text("c", "ok")])]

    async def go():
        return [pe async for pe in StrictAnthropic().stream(history, [])]

    events = run(go())
    assert events[-1].kind == "error" and events[-1].status == 400 and "must define tools" in events[-1].error


def test_wrap_up_keeps_tools_and_forces_none(tmp_path):
    prov, store, _, s, res = spin_then(tmp_path, reply("done: 3 rounds; open: X; next: finish X"))
    assert res.stop_reason == "max_iterations" and res.error is None
    assert res.message.text().startswith("done: 3 rounds") and res.message.meta.get("wrap_up")
    last = prov.bodies[-1]
    assert last["tools"] and last["tool_choice"] == {"type": "none"}
    assert all("tool_choice" not in b for b in prov.bodies[:-1])  # ordinary rounds stay on auto


def test_wrap_up_drops_tool_calls_in_the_reply(tmp_path):
    ran = []

    async def tool(call, ctx):
        ran.append(call.id)
        return await echo_tool(call, ctx)

    late = reply("summary", [ToolCall(id="late", name="echo", arguments={})])
    prov, store, _, s, res = spin_then(tmp_path, late, cap=2, run_tool=tool)
    assert res.message.text() == "summary" and not res.message.tool_calls
    assert "late" not in ran and not store.messages(s.id)[-1].tool_calls


def test_near_cap_notice_and_session_cap(tmp_path):
    prov = Script(*[reply("", SPIN) for _ in range(8)], reply("wrapped"))
    store, loop, seen = make(tmp_path, prov, tool_specs=[SPEC], run_tool=echo_tool, max_iterations=20)

    async def go():
        s = await loop.create_session()
        loop.state(s.id).rounds = 8  # what /rounds 8 sets
        return await loop.send(s.id, "spin")

    res = run(go())
    assert res.stop_reason == "max_iterations" and res.message.text() == "wrapped"
    notices = [e.body.text for e in seen if e.type == "notice"]
    assert "tool round 6 of 8; /rounds N raises the cap" in notices and "stopped after 8 tool rounds" in notices
    assert len(prov.calls) == 9


def test_default_cap_is_64():
    assert Loop.__init__.__kwdefaults__["max_iterations"] == 64


def test_configured_precedence(capsys):
    assert rounds.configured({}, None, None) is None
    assert rounds.configured({}, None, {"max_tool_rounds": 40}) == 40
    assert rounds.configured({}, {"max_tool_rounds": 90}, {"max_tool_rounds": 40}) == 90
    assert rounds.configured({"EXO_MAX_TOOL_ROUNDS": "120"}, {"max_tool_rounds": 90}, None) == 120
    assert rounds.configured({"EXO_MAX_TOOL_ROUNDS": "lots"}, {"max_tool_rounds": 0}, {"max_tool_rounds": 50}) == 50
    err = capsys.readouterr().err
    assert "EXO_MAX_TOOL_ROUNDS='lots'" in err and "seat [loop] max_tool_rounds=0" in err
    assert parse_seat({"loop": {"max_tool_rounds": 100}}, "razi").loop == {"max_tool_rounds": 100}
    assert [rounds.parse(v) for v in (True, "7", 0, 1001, "1000")] == [None, 7, None, None, 1000]


def test_rounds_command_principal_only(tmp_path):
    store, loop, _ = make(tmp_path, Script())
    eng = SimpleNamespace(store=store, loop=loop)
    s = run(loop.create_session())
    assert rounds.command(eng, s.id, "") == "tool rounds per turn: 64 (default; /rounds N sets this session's cap)"
    with pytest.raises(CommandFailed) as no:
        rounds.command(eng, s.id, "200")  # no live principal caller
    assert no.value.code == "forbidden" and loop.state(s.id).rounds is None
    tok = pr.CALLER.set(pr.Caller(pr.principal_as("tui")))
    try:
        assert "200 for this session" in rounds.command(eng, s.id, "200")
        assert loop.state(s.id).rounds == 200
        with pytest.raises(CommandFailed):
            rounds.command(eng, s.id, "0")
        assert "(default;" in rounds.command(eng, s.id, "default") and loop.state(s.id).rounds is None
        with pytest.raises(CommandFailed):
            rounds.command(eng, "nope", "5")
    finally:
        pr.CALLER.reset(tok)


def test_openai_body_forces_no_tool_use():
    body = OpenAIChatProvider("http://127.0.0.1:9", api_key="k").build_body([Message.user("hi")], [SPEC], tool_choice="none")
    assert body["tools"] and body["tool_choice"] == "none"
    assert "tool_choice" not in OpenAIChatProvider("http://127.0.0.1:9", api_key="k").build_body([Message.user("hi")], [SPEC])
