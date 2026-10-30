"""CoreEngine adapters: plugin hooks on the tool registry, approvals, plugin tools, context slot."""

from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.core.hooks import VALID_HOOKS
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher

FAKE = {"rules": [
    {"match": "danger", "steps": [{"tool": "bash", "args": {"command": "echo hi"}}, "after danger"]},
    {"match": "ask", "steps": [{"tool": "bash", "args": {"command": "echo asked"}}, "after ask"]},
    {"match": "plug", "steps": [{"tool": "shout", "args": {"text": "hey"}}, "after plug"]},
]}


def _engine(tmp_path, register) -> CoreEngine:
    pm = PluginManager()
    pm._run_register(Manifest(name="t"), "dir", register)
    assert not pm.errors(), pm.errors()
    return CoreEngine(tmp_path / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                      plugins=pm, cwd=str(tmp_path))


async def _turn(d: Dispatcher, eng: CoreEngine, sid: str, text: str, answer: str | None = None) -> None:
    start = eng.hub.seq
    await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    while eng._tasks:
        if answer:
            for ev in eng.hub.since(start):
                if isinstance(ev.body, s.ApprovalRequested) and ev.body.request_id in eng.approvals.open:
                    resp = await d.run(s.Command.of(s.ApprovalRespond(request_id=ev.body.request_id,
                                                                      decision=answer)), "principal:tui")
                    assert resp.ok
        await asyncio.sleep(0.01)


def _results(eng: CoreEngine) -> list[str]:
    return [ev.body.result.text() for ev in eng.hub.since(0) if isinstance(ev.body, s.ToolCompleted)]


def test_plugin_hooks_block_approve_and_plugin_tools(tmp_path):
    seen: list[dict] = []
    fired: list[str] = []

    def register(ctx):
        def pre(tool_name="", args=None, **kw):
            fired.append("pre_tool_call")
            seen.append({"tool": tool_name, **kw})
            if "hi" in (args or {}).get("command", ""):
                return {"action": "block", "message": "no hi"}
            if "asked" in (args or {}).get("command", ""):
                return {"action": "approve", "message": "run echo?", "rule_key": "echo"}
            return None

        ctx.register_hook("pre_tool_call", pre)
        def approval(**kw):
            fired.append("post_approval_response")
            seen.append({"approval": kw["choice"]})

        ctx.register_hook("post_approval_response", approval)
        ctx.register_hook("post_tool_call", lambda **kw: fired.append("post_tool_call"))

        def transform(tool_name="", result="", **kw):
            fired.append("transform_tool_result")
            return "transformed" if tool_name == "shout" else None

        ctx.register_hook("transform_tool_result", transform)
        ctx.register_hook("on_turn_start", lambda **kw: fired.append("on_turn_start"))
        ctx.register_hook("on_turn_end", lambda **kw: fired.append("on_turn_end"))
        ctx.register_tool("shout", "demo", {"parameters": {"type": "object",
                                                          "properties": {"text": {"type": "string"}}}},
                          handler=lambda args, **kw: args["text"].upper())

    eng = _engine(tmp_path, register)

    async def main():
        d = Dispatcher(eng)
        assert d.hub is eng.hub
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        await _turn(d, eng, sid, "danger")
        await _turn(d, eng, sid, "ask", answer="allow")
        await _turn(d, eng, sid, "plug")
        tools = (await d.run(s.Command.of(s.ToolsList()))).result["tools"]
        assert "shout" in {t["name"] for t in tools} and "bash" in {t["name"] for t in tools}

    asyncio.run(main())
    res = _results(eng)
    assert res[0] == "Blocked: no hi"
    assert "asked" in res[1] and res[2] == "transformed"
    assert {"approval": "once"} in seen
    assert seen[0]["session_id"] and seen[0]["tool_call_id"]
    assert set(fired) == set(VALID_HOOKS) - {"delegated_approval_validate", "delegated_approval_audit"}  # explicit capability only


def test_context_slot_adapter_sees_turns(tmp_path):
    class Engine:
        name = "ctx"
        calls: list[str] = []

        def update_from_response(self, usage):
            self.calls.append(f"usage:{usage['total_tokens'] > 0}")

        def should_compress(self, prompt_tokens=None):
            return False

        def compress(self, messages, **kw):
            return messages

        def on_session_start(self, session_id, **kw):
            self.calls.append("start")

        def select_context(self, request_messages, **kw):
            self.calls.append(f"select:{len(request_messages)}")
            return None

    plugin = Engine()
    eng = _engine(tmp_path, lambda ctx: ctx.register_context_engine(plugin))

    async def main():
        d = Dispatcher(eng)
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        await _turn(d, eng, sid, "hello")

    asyncio.run(main())
    assert plugin.calls[0] == "start" and "usage:True" in plugin.calls
    assert any(c.startswith("select:") for c in plugin.calls)
