"""Current finite-round boundary through dispatcher ingress; policy choice #692 remains open."""
import asyncio

import pytest

from exo.core.engine import CoreEngine
from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult, ToolSpec
from exo.protocol import schema as s
from exo.server.app import Dispatcher


class FortyRounds:
    name = "authority-fixture"

    def __init__(self):
        self.calls = 0

    async def stream(self, messages, tools, **options):
        self.calls += 1
        calls = ([ToolCall(id=f"proof-{self.calls}", name="proof", arguments={})]
                 if self.calls <= 40 and options.get("tool_choice") != "none" else [])
        for call in calls:
            yield ProviderEvent(kind="tool_call", tool_call=call)
        message = Message.assistant("fixture complete" if not calls else "", tool_calls=calls)
        yield ProviderEvent(kind="done", stop_reason="tool_use" if calls else "end", message=message)


@pytest.mark.parametrize("caller", ["unknown", "local", "stdio", "seat:helper", "principal:tui"])
@pytest.mark.parametrize("explicit_limit", [None, 3])
def test_dispatcher_turn_retains_finite_baseline(tmp_path, caller, explicit_limit):
    """An attached watcher is not proof of principal authority or permission for unbounded spend."""
    async def run():
        models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"reply": "ok"}}
        engine = CoreEngine(tmp_path / "home", models, cwd=str(tmp_path), env={})
        provider = FortyRounds()
        tools_run = []

        async def harmless(call, context):
            tools_run.append(call.id)
            return ToolResult.of_text(call.id, "fixture")

        engine.loop.provider = provider
        engine.loop.tool_specs = [ToolSpec(name="proof", description="in-memory fixture")]
        engine.loop.run_tool = harmless
        engine.loop.watching = lambda: True  # #363's observer condition, not an authority grant
        if explicit_limit is not None:
            engine.loop.max_iterations = explicit_limit
        try:
            dispatcher = Dispatcher(engine, caller=caller)
            created = await dispatcher.run(s.Command.of(s.SessionCreate()))
            assert created.ok
            sid = created.result["session"]["id"]
            accepted = await dispatcher.run(s.Command.of(s.TurnSend(session=sid, text="run fixture")))
            assert accepted.ok
            await asyncio.wait_for(asyncio.gather(*list(engine._tasks)), 5)
            completed = [event for event in engine.store.events_since(session=sid)
                         if event.type == "turn.completed"]
            assert len(completed) == 1
            assert completed[0].body.stop_reason == "max_iterations"
            expected = explicit_limit if explicit_limit is not None else 24
            assert len(tools_run) == expected
            assert provider.calls == expected + 1  # finite rounds plus the tool-less report
            user = [m for m in engine.store.messages(sid) if m.role == "user"][0]
            assert user.meta["author"] == ("principal" if caller.startswith("principal:") else "local")
        finally:
            await engine.close()

    asyncio.run(run())
