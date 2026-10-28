"""Test-first model-context acceptance for #792; no runtime intervention is added here."""
import asyncio

import pytest

from exo.core.engine import CoreEngine
from exo.core.types import Message, ProviderEvent, ToolCall, ToolResult

MARKER = "[harness progress check]"  # proposed machine-recognizable envelope, not an accepted policy


class MissingProgressFeedback(AssertionError):
    pass


class Script:
    name = "repeat-fixture"

    def __init__(self, operations):
        self.operations = operations
        self.requests = []

    async def stream(self, messages, tools, **options):
        self.requests.append(list(messages))
        index = len(self.requests) - 1
        calls = []
        if index < len(self.operations):
            name, arguments, _error = self.operations[index]
            calls = [ToolCall(id=f"fixture-{index}", name=name, arguments=arguments)]
            yield ProviderEvent(kind="tool_call", tool_call=calls[0])
        yield ProviderEvent(kind="done", stop_reason="tool_use" if calls else "end",
                            message=Message.assistant("" if calls else "fixture finished", tool_calls=calls))


READ = ("bash", {"command": "cat repeated.txt"}, False)
WRITE = ("write_file", {"path": "fixture.txt", "content": "fixture"}, False)
FAILED_WRITE = ("write_file", {"path": "fixture.txt", "content": "fixture"}, True)


def run_script(tmp_path, operations):
    async def run():
        engine = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}}, cwd=str(tmp_path), env={})
        script = Script(operations)
        completed = []

        async def harmless(arguments, context):
            index = int(context.call_id.split("-")[-1])
            completed.append(context.call_id)
            return ToolResult.of_text(context.call_id, "synthetic read/write result", is_error=operations[index][2])

        engine.loop.provider = script
        engine.plugins.hooks.register("pre_tool_call", lambda **kw: None, "fixture-allow")
        for name in {operation[0] for operation in operations}:
            tool = engine.registry.get(name)
            assert tool is not None
            engine.registry.register(tool.spec, harmless, replace=True)
        # Keep CoreEngine.run_tool and its observers; replace only the actual I/O handlers.
        try:
            session = await engine.loop.create_session()
            result = await engine.loop.send(session.id, "inspect the synthetic task")
            assert result.stop_reason == "end" and len(completed) == len(operations)
            assert len(script.requests) == len(operations) + 1
            return script.requests
        finally:
            await engine.close()
    return asyncio.run(run())


@pytest.mark.parametrize("operations", [[READ] * 4, [READ, READ, FAILED_WRITE, READ, READ]])
@pytest.mark.xfail(strict=True, raises=MissingProgressFeedback,
                   reason="#792: main has no bounded model-visible progress check")
def test_repeated_inspection_reaches_model_context_before_the_turn_ends(tmp_path, operations):
    requests = run_script(tmp_path, operations)
    messages = [message for request in requests[3:] for message in request
                if message.role != "tool" and MARKER in message.text()]
    if not messages:
        raise MissingProgressFeedback("repeated completed reads never produced model-visible progress feedback")
    # Evidence and a neutral next-step request: never infer consent to write or publish.
    assert all("cat repeated.txt" in message.text() and "blocker" in message.text().lower()
               for message in messages)
    assert all(len(message.text()) <= 1200 for message in messages)


@pytest.mark.parametrize("operations", [
    [("bash", {"command": f"cat file-{i}.txt"}, False) for i in range(4)],
    [READ, READ, WRITE, READ, READ],
])
def test_distinct_work_and_retest_after_successful_write_do_not_trigger(tmp_path, operations):
    requests = run_script(tmp_path, operations)
    assert all(MARKER not in message.text() for request in requests for message in request)
