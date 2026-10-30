"""Principal run-limit policy; real loop with fake providers and disposable stores only."""
import asyncio

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.run_limits import RunLimits
from exo.protocol import schema as s
from exo.server.app import Dispatcher


def engine(path, **kwargs):
    return CoreEngine(path, {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}}, env={}, **kwargs)


@pytest.mark.parametrize('args', ['rounds -1', 'rounds true', 'rounds NaN', 'rounds inf', 'rounds 1.5',
    'rounds 2147483648', 'rounds 1m', 'worker-timeout 9999999999h', 'worker-timeout -1m',
    'worker-timeout 1e9', 'worker-timeout 1.5h', 'rounds 0 extra', 'depth off', 'worker-timeout １２'])
def test_strict_parse_is_atomic(args):
    policy = RunLimits(); policy.update('rounds 8'); before = policy.snapshot()
    with pytest.raises(ValueError):
        policy.update(args)
    assert policy.snapshot() == before


def test_independent_overrides_defaults_and_units():
    p = RunLimits(); p.update('rounds off all'); p.update('worker-timeout 15m')
    assert p.snapshot().rounds_for(24, 3) == 0 and p.snapshot().timeout_for(42) == 900
    p.update('worker-timeout off'); assert p.snapshot().rounds == 0
    p.update('rounds default'); assert p.snapshot().rounds_for(24, 3) == 3
    p.update('worker-timeout 1h'); assert p.snapshot().timeout_for(42) == 3600
    p.update('default'); assert p.snapshot().timeout_for(42) == 42
    state = p.status(7, 9, 123)
    assert (state['main_rounds'], state['worker_rounds'], state['worker_timeout_s']) == (7, 9, 123)
    assert state['ephemeral'] and state['running_work_unchanged']


def test_only_principal_changes_core_policy_all_ingress_types(tmp_path):
    async def go():
        eng = engine(tmp_path/'home')
        for caller in ('local', 'channel', 'delegate', 'peer:test', 'unknown'):
            dispatch = Dispatcher(eng, caller=caller)
            status = await dispatch.run(s.Command.of(s.CommandRun(name='limits', args='json')))
            assert status.ok and status.result['output']['supported']
            denied = await dispatch.run(s.Command.of(s.CommandRun(name='limits', args='rounds off')))
            assert not denied.ok and denied.error.code == 'forbidden'
            assert eng.run_limits.snapshot().rounds is None
        principal = Dispatcher(eng, caller='principal:test')
        changed = await principal.run(s.Command.of(s.CommandRun(name='limits', args='rounds off')))
        assert changed.ok and eng.run_limits.snapshot().rounds == 0
        bad = await principal.run(s.Command.of(s.CommandRun(name='limits', args='worker-timeout NaN')))
        assert not bad.ok and eng.run_limits.snapshot().worker_timeout_s is None
        other = engine(tmp_path/'other'); assert other.run_limits.snapshot().rounds is None
        await eng.close(); await other.close()
    asyncio.run(go())


def test_global_off_real_loop_passes_24_and_default_preserves_explicit_window_limit(tmp_path):
    async def go():
        eng = engine(tmp_path/'home'); eng.run_limits.update('rounds off')
        provider = FakeProvider([tool_turn('glob', {'pattern':'no-match'}) for _ in range(28)] + ['done'])
        eng.loop.provider = provider
        sid = (await eng.loop.create_session()).id
        result = await eng.run_turn(s.TurnSend(session=sid,text='synthetic'), max_rounds=1)
        assert result.stop_reason == 'end' and len(provider.calls) == 29 and eng.loop.max_iterations == 24
        eng.run_limits.update('rounds default')
        eng.loop.provider = FakeProvider([tool_turn('glob', {'pattern':'no-match'}), 'wrap up'])
        result = await eng.run_turn(s.TurnSend(session=sid,text='synthetic'), max_rounds=1)
        assert result.stop_reason == 'max_iterations' and len(eng.loop.provider.calls) == 2
        await eng.close()
    asyncio.run(go())


def test_running_turn_keeps_admitted_limit_during_policy_change(tmp_path):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        class Held(FakeProvider):
            async def stream(self,*args,**kw):
                if not self.calls:
                    entered.set(); await release.wait()
                async for event in super().stream(*args,**kw): yield event
        eng = engine(tmp_path/'home'); eng.run_limits.update('rounds 2')
        provider = Held([tool_turn('glob',{'pattern':'nothing'})]*3); eng.loop.provider = provider
        sid = (await eng.loop.create_session()).id
        task = asyncio.create_task(eng.run_turn(s.TurnSend(session=sid,text='synthetic')))
        await entered.wait(); eng.run_limits.update('rounds off'); release.set()
        result = await task
        assert result.stop_reason == 'max_iterations' and len(provider.calls) == 3
        await eng.close()
    asyncio.run(go())


def test_expired_principal_context_cannot_change_policy(tmp_path):
    from exo import diagnostics, principal
    from exo.server.engine import CommandFailed
    async def go():
        eng = engine(tmp_path/'home')
        token = principal.CALLER.set(principal.Caller('principal:test',live=False))
        try:
            with pytest.raises(CommandFailed) as error:
                await diagnostics.builtin_command(eng,s.CommandRun(name='limits',args='rounds off'))
            assert error.value.code == 'forbidden' and eng.run_limits.snapshot().rounds is None
        finally:
            principal.CALLER.reset(token)
            await eng.close()
    asyncio.run(go())
