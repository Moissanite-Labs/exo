"""Unlimited rounds retain explicit caps and cancellation."""
import asyncio

import pytest

from .test_loop import Script, ToolCall, echo_tool, make, reply, SPEC
from exo.core.agents.delegate import Limits


@pytest.mark.parametrize("cap, expected", [(0, "end"), (2, "max_iterations")])
def test_rounds(tmp_path, cap, expected):
    provider = Script(*[reply("", [ToolCall(id=f"c{i}", name="echo", arguments={})]) for i in range(30)], reply("done"))
    store, loop, seen = make(tmp_path, provider, max_iterations=cap, wrap_up=False, tool_specs=[SPEC], run_tool=echo_tool)
    async def go():
        session = await loop.create_session()
        return await loop.send(session.id, "go")
    try:
        result = asyncio.run(go())
        assert result.stop_reason == expected
        assert len(provider.calls) == (31 if cap == 0 else 2)
    finally:
        store.close()


@pytest.mark.parametrize("bad", [-1, True, 1.5, "0"])
def test_invalid_round_limits(tmp_path, bad):
    with pytest.raises(ValueError):
        make(tmp_path, Script(), max_iterations=bad)
    with pytest.raises(ValueError):
        Limits.of({"max_turns": bad})


def test_child_unlimited_default():
    assert Limits.of(None).max_turns == 0
    assert Limits.of({"max_turns": 0}).max_turns == 0
    assert Limits.of({"max_turns": 2}).max_turns == 2


def test_unlimited_interrupt(tmp_path):
    provider = Script(["WAIT"])
    store, loop, seen = make(tmp_path, provider, max_iterations=0)
    async def go():
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, "go"))
        while not provider.calls:
            await asyncio.sleep(0)
        loop.interrupt(session.id)
        return await asyncio.wait_for(task, 2)
    try:
        assert asyncio.run(go()).stop_reason == "interrupted"
    finally:
        store.close()
