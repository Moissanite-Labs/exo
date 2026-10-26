import asyncio

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
