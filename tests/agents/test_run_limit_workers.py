"""Core-wide worker overrides preserve independent budgets and admitted timers."""
import asyncio

from exo.core.models.fake import FakeProvider, tool_turn
from exo.protocol import schema as s
from exo.wire import decode
from .test_delegate import engine, task_events


def receipt(eng):
    return decode(task_events(eng)[-1].body.packet)


def test_global_zero_reaches_real_child_loop_and_defaults_restore_worker_cap(tmp_path):
    async def go():
        rules = [('PARENT', [tool_turn('delegate', {'goal':'CHILD synthetic'}), 'parent done']),
                 ('CHILD', [tool_turn('glob', {'pattern':'no-match'}) for _ in range(28)] + ['child done'])]
        eng, fake = engine(tmp_path, rules, max_turns=2, max_tokens=10_000_000)
        eng.agents.sems[1] = asyncio.Semaphore(0)
        child = FakeProvider([tool_turn('glob', {'pattern':'no-match'}) for _ in range(28)] + ['child done'])
        eng.agents.route = lambda tier: (child, 'fake:child')
        sid = (await eng.loop.create_session()).id
        pending = asyncio.create_task(eng.run_turn(s.TurnSend(session=sid,text='PARENT synthetic')))
        while not task_events(eng): await asyncio.sleep(.01)
        eng.run_limits.update('rounds off')
        eng.agents.sems[1].release()
        await pending
        r = receipt(eng)
        assert r.status == 'done' and r.rounds == 28 and r.tool_calls == 28
        assert eng.agents.limits.max_turns == 2 and eng.agents.limits.timeout_s == 900
        eng.run_limits.update('rounds default')
        child.script = [tool_turn('glob', {'pattern':'no-match'}) for _ in range(4)]
        sid = (await eng.loop.create_session()).id
        await eng.run_turn(s.TurnSend(session=sid,text='PARENT synthetic'))
        r = receipt(eng)
        assert r.status == 'failed' and r.error_code == 'turns' and r.rounds == 2
        await eng.close()
    asyncio.run(go())


def test_existing_worker_deadline_not_extended_but_new_worker_can_have_wall_off(tmp_path):
    async def go():
        hold = asyncio.Event()
        rules = [('PARENT', [tool_turn('delegate', {'goal':'CHILD synthetic'}), 'parent done']), ('CHILD', [hold])]
        eng, fake = engine(tmp_path, rules, timeout_s=1, max_tokens=10_000_000)
        sid = (await eng.loop.create_session()).id
        task = asyncio.create_task(eng.run_turn(s.TurnSend(session=sid,text='PARENT synthetic')))
        while not fake.active: await asyncio.sleep(.01)
        eng.run_limits.update('worker-timeout off')
        await asyncio.wait_for(task, 3)
        assert receipt(eng).error_code == 'timeout'
        sid = (await eng.loop.create_session()).id
        task = asyncio.create_task(eng.run_turn(s.TurnSend(session=sid,text='PARENT synthetic')))
        while not fake.active: await asyncio.sleep(.01)
        await asyncio.sleep(1.1)
        assert not task.done()
        hold.set(); await asyncio.wait_for(task, 3)
        assert receipt(eng).status == 'done'
        assert eng.agents.limits.timeout_s == 1
        await eng.close()
    asyncio.run(go())


def test_round_and_wall_off_do_not_remove_token_depth_or_concurrency_limits(tmp_path):
    async def go():
        rules = [('PARENT', [tool_turn('delegate', {'goal':'CHILD synthetic'}), 'done']),
                 ('CHILD', [tool_turn('glob',{'pattern':'nothing'})]*30)]
        eng, _ = engine(tmp_path, rules, max_tokens=1, max_depth=1, max_concurrent=2)
        eng.run_limits.update('rounds off'); eng.run_limits.update('worker-timeout off')
        sid = (await eng.loop.create_session()).id
        await eng.run_turn(s.TurnSend(session=sid,text='PARENT synthetic'))
        assert receipt(eng).error_code == 'budget'
        assert (eng.agents.limits.max_tokens,eng.agents.limits.max_depth,eng.agents.limits.max_concurrent)==(1,1,2)
        await eng.close()
    asyncio.run(go())
