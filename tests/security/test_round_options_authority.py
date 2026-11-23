import asyncio

import pytest

from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.core.engine import CoreEngine


def test_only_principal_may_override_round_limit(tmp_path):
    async def go():
        eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}, "fake": {"reply": "ok"}}, cwd=str(tmp_path), env={})
        principal = Dispatcher(eng, caller='principal:tui')
        local = Dispatcher(eng, caller='local')
        created = await principal.run(s.Command.of(s.SessionCreate()))
        sid = created.result['session']['id']
        denied = await local.run(s.Command.of(s.TurnSend(session=sid, text='test', max_rounds=0)))
        invalid = await principal.run(s.Command.of(s.TurnSend(session=sid, text='test', max_rounds=-1)))
        assert not denied.ok and denied.error.code == 'forbidden'
        assert not invalid.ok and invalid.error.code == 'bad_request'
        accepted = await principal.run(s.Command.of(s.TurnSend(session=sid, text='hello', max_rounds=0)))
        assert accepted.ok
        await asyncio.gather(*list(eng._tasks))
        state = eng.loop.state(sid)
        state.unlimited = state.accepting_steers = True
        refused = await local.run(s.Command.of(s.TurnSend(session=sid, text='steer', mode='steer')))
        assert not refused.ok and refused.error.code == 'forbidden'
        state.unlimited = state.accepting_steers = False
        await eng.close()
    asyncio.run(go())


@pytest.mark.parametrize("accepting", [False, True])
def test_steer_with_override_is_rejected_before_queuing(tmp_path, accepting):
    async def go():
        eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}, "fake": {"reply": "ok"}}, cwd=str(tmp_path), env={})
        principal = Dispatcher(eng, caller='principal:tui')
        sid = (await principal.run(s.Command.of(s.SessionCreate()))).result['session']['id']
        state = eng.loop.state(sid)
        state.accepting_steers = accepting
        response = await principal.run(s.Command.of(s.TurnSend(session=sid, text='steer', mode='steer', max_rounds=0)))
        assert not response.ok and response.error.code == 'bad_request'
        assert 'omit max_rounds' in response.error.message
        assert not state.steer and not eng._tasks
        state.accepting_steers = False
        await eng.close()
    asyncio.run(go())


def test_subagent_send_normalized_to_steer_rejects_override(tmp_path):
    async def go():
        eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}, "fake": {"reply": "ok"}}, cwd=str(tmp_path), env={})
        principal = Dispatcher(eng, caller='principal:tui')
        sid = (await principal.run(s.Command.of(s.SessionCreate()))).result['session']['id']
        eng.agents.loops[sid] = eng.loop
        eng.loop.state(sid).accepting_steers = True
        response = await principal.run(s.Command.of(s.TurnSend(session=sid, text='steer child', max_rounds=5)))
        assert not response.ok and response.error.code == 'bad_request'
        assert not eng.loop.state(sid).steer and not eng._tasks
        eng.agents.loops.pop(sid)
        eng.loop.state(sid).accepting_steers = False
        await eng.close()
    asyncio.run(go())
