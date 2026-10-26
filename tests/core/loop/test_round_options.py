import asyncio

from exo.core.types import ToolCall
from .test_loop import SPEC, Script, echo_tool, make, reply


def test_explicit_round_limits_do_not_change_other_turns(tmp_path):
    tool = ToolCall(id='call', name='echo', arguments={})
    provider = Script(*([reply('', [tool])] * 30 + [reply('done')]))
    store, loop, _ = make(tmp_path, provider, tool_specs=[SPEC], run_tool=echo_tool)

    async def go():
        session = await loop.create_session()
        result = await loop.send(session.id, 'long task', max_rounds=0)
        return result

    result = asyncio.run(go())
    assert result.stop_reason == 'end' and len(provider.calls) == 31
    assert loop.max_iterations == 24


def test_finite_override_wrap_up_uses_its_own_limit(tmp_path):
    tool = ToolCall(id='call', name='echo', arguments={})
    provider = Script(reply('', [tool]), reply('', [tool]), reply('summary'))
    store, loop, _ = make(tmp_path, provider, tool_specs=[SPEC], run_tool=echo_tool)

    async def go():
        session = await loop.create_session()
        return await loop.send(session.id, 'short task', max_rounds=2)

    result = asyncio.run(go())
    assert result.stop_reason == 'max_iterations'
    assert 'all 2 tool rounds' in provider.calls[-1][-1].text()


def test_unlimited_turn_remains_interruptible_after_24_rounds(tmp_path):
    from exo.core.types import ProviderEvent
    tool = ToolCall(id='call', name='echo', arguments={})
    provider = Script(*([reply('', [tool])] * 25), [ProviderEvent(kind='text', text='waiting'), 'WAIT'])
    store, loop, seen = make(tmp_path, provider, tool_specs=[SPEC], run_tool=echo_tool)

    async def go():
        session = await loop.create_session()
        task = asyncio.create_task(loop.send(session.id, 'long task', max_rounds=0))
        while not any(e.type == 'message.delta' and e.body.text == 'waiting' for e in seen):
            await asyncio.sleep(0.001)
        assert loop.interrupt(session.id)
        return await asyncio.wait_for(task, 2)

    result = asyncio.run(go())
    assert len(provider.calls) == 26 and result.stop_reason == 'interrupted'
