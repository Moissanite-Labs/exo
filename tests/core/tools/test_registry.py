import asyncio
import json

import pytest

from exo.core.tools import POST_TOOL_CALL, PRE_TOOL_CALL, Block, Registry, ToolContext, ToolError
from exo.core.types import ToolCall, ToolResult, ToolSpec, from_dict


def test_schema_output_is_json_and_roundtrips(reg):
    schemas = reg.schemas()
    names = [s["name"] for s in schemas if s["name"] != "code_find"]  # code_find shows only where jg runs
    assert names == sorted(names)
    assert set(names) == {"read_file", "write_file", "edit_file", "apply_patch", "glob", "grep", "bash"}
    for s in json.loads(json.dumps(schemas)):
        spec = from_dict(ToolSpec, s)
        assert spec.parameters["type"] == "object"
        assert set(spec.parameters.get("required", [])) <= set(spec.parameters["properties"])
        assert spec.description
    risks = {s["name"]: s["risk"] for s in schemas}
    assert risks["read_file"] == "low" and risks["edit_file"] == "medium" and risks["bash"] == "high"


def test_toolsets_filter(reg):
    assert reg.toolsets()["search"] == ["code_find", "glob", "grep"]
    assert [s.name for s in reg.specs(toolsets=["shell"])] == ["bash"]
    assert [s.name for s in reg.specs(names=["grep", "nope"])] == ["grep"]


def test_check_fn_hides_and_refuses():
    reg = Registry()
    reg.tool("gone", "x", check_fn=lambda: False)(lambda a, c: "ran")
    reg.tool("boom", "x", check_fn=lambda: 1 / 0)(lambda a, c: "ran")
    assert reg.specs() == []
    assert len(reg.specs(available_only=False)) == 2
    res = reg.call_sync(ToolCall(id="1", name="gone"))
    assert res.is_error and "not available" in res.text()


def test_duplicate_register_rejected():
    reg = Registry()
    reg.tool("a", "x")(lambda a, c: "")
    with pytest.raises(ValueError):
        reg.tool("a", "x")(lambda a, c: "")


def test_unknown_tool_and_bad_args(reg):
    res = reg.call_sync(ToolCall(id="1", name="nope"))
    assert res.is_error and res.call_id == "1"
    res = reg.call_sync(ToolCall(id="2", name="read_file", arguments={}))
    assert res.is_error and "missing required argument 'path'" in res.text()
    res = reg.call_sync(ToolCall(id="3", name="read_file", arguments={"path": 5}))
    assert res.is_error and "must be string" in res.text()
    res = reg.call_sync(ToolCall(id="4", name="bash", arguments={"command": "true", "timeout": True}))
    assert res.is_error and "must be number" in res.text()


def test_handler_errors_become_error_results():
    reg = Registry()
    reg.tool("expected", "x")(lambda a, c: (_ for _ in ()).throw(ToolError("nicely")))
    reg.tool("crash", "x")(lambda a, c: 1 / 0)
    assert reg.call_sync(ToolCall(id="1", name="expected")).text() == "nicely"
    res = reg.call_sync(ToolCall(id="2", name="crash"))
    assert res.is_error and "ZeroDivisionError" in res.text()


def test_async_handler_and_tool_result_passthrough():
    reg = Registry()

    @reg.tool("a", "x")
    async def a(args, ctx):
        await asyncio.sleep(0)
        return ToolResult.of_text("ignored", f"cwd={ctx.cwd}")

    res = reg.call_sync(ToolCall(id="9", name="a"), ToolContext(cwd="/w"))
    assert res.call_id == "9" and res.text() == "cwd=/w"


def test_pre_hook_can_block_and_handler_never_runs(tmp_path):
    reg = Registry()
    ran = []
    reg.tool("write", "x", risk="high")(lambda a, c: ran.append(1) or "done")
    seen = []
    reg.add_hook(PRE_TOOL_CALL, lambda call, spec, ctx: Block("needs approval") if spec.risk == "high" else None)
    reg.add_hook(POST_TOOL_CALL, lambda call, result, ctx: seen.append(result.text()))
    res = reg.call_sync(ToolCall(id="1", name="write"))
    assert res.is_error and res.text() == "Blocked: needs approval"
    assert ran == [] and seen == ["Blocked: needs approval"]


def test_async_pre_hook_string_block_and_failing_hook_fails_closed():
    reg = Registry()
    reg.tool("t", "x")(lambda a, c: "ran")

    async def deny(call, spec, ctx):
        return "no"
    reg.add_hook(PRE_TOOL_CALL, deny)
    assert reg.call_sync(ToolCall(id="1", name="t")).text() == "Blocked: no"
    reg.remove_hook(PRE_TOOL_CALL, deny)
    reg.add_hook(PRE_TOOL_CALL, lambda *a: 1 / 0)
    res = reg.call_sync(ToolCall(id="2", name="t"))
    assert res.is_error and "hook failed" in res.text()


def test_pre_hook_can_rewrite_args_and_post_hook_can_replace():
    reg = Registry()
    reg.tool("echo", "x", {"type": "object", "properties": {"s": {"type": "string"}}})(
        lambda a, c: a.get("s", ""))
    reg.add_hook(PRE_TOOL_CALL, lambda call, spec, ctx: call.arguments.update(s="rewritten"))
    reg.add_hook(POST_TOOL_CALL, lambda call, r, ctx: ToolResult.of_text(call.id, r.text().upper()))
    reg.add_hook(POST_TOOL_CALL, lambda *a: 1 / 0)  # ignored
    assert reg.call_sync(ToolCall(id="1", name="echo", arguments={"s": "orig"})).text() == "REWRITTEN"


def test_unknown_hook_point():
    with pytest.raises(ValueError):
        Registry().add_hook("on_whatever", lambda *a: None)
