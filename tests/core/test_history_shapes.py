"""Regression: a select_context hook must not strip tool calls/results, and claude-cli must not raise on odd tails."""
from exo.core.engine import _apply_selection
from exo.core.models.claude_cli import build_frames
from exo.core.types import Message, ToolCall, ToolResult


def _history():
    call = ToolCall(id="toolu_1", name="bash", arguments={"command": "x"})
    return [
        Message.system("sys"),
        Message.user("hello"),
        Message.assistant("", [call]),
        Message(role="tool", tool_results=[ToolResult.of_text("toolu_1", "out")]),
    ]


def _flat(msgs):
    return [{"role": m.role, "content": m.text()} for m in msgs]


def test_selection_injecting_system_keeps_tool_messages():
    msgs = _history()
    out = _flat(msgs)
    out[0] = {"role": "system", "content": "sys\n\npinned"}
    got = _apply_selection(msgs, _flat(msgs), out)
    assert [m.role for m in got] == ["system", "user", "assistant", "tool"]
    assert got[0].text() == "sys\n\npinned"
    assert got[2].tool_calls and got[3].tool_results
    build_frames(got, {"bash"})  # does not raise


def test_selection_inserting_new_system_message():
    msgs = _history()
    out = [{"role": "system", "content": "extra"}] + _flat(msgs)
    got = _apply_selection(msgs, _flat(msgs), out)
    assert [m.role for m in got] == ["system", "system", "user", "assistant", "tool"]
    assert got[4].tool_results


def test_frames_tolerate_empty_tool_and_trailing_assistant():
    msgs = _history() + [Message(role="tool", tool_results=[])]
    _, frames = build_frames(msgs, {"bash"})
    assert frames[-1]["type"] == "user" and frames[-1]["message"]["content"][0]["type"] == "tool_result"
    _, frames = build_frames(_history()[:3], {"bash"})
    assert frames[-1]["type"] == "user" and frames[-1]["message"]["content"]
    _, frames = build_frames([Message.user("a"), Message.assistant("note")], set())
    assert [f["type"] for f in frames] == ["user", "assistant", "user"]
