"""Reasoning settings use disposable stores and fake providers, never live inference."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from exo.core.loop import Loop
from exo.core.models import FakeProvider, Router
from exo.core.models.fake import rate_limited, tool_turn
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.models.reasoning import capability
from exo.core.reasoning import CAPABILITY, ReasoningSettings
from exo.core.sessions import SessionStore
from exo.core.types import Message, ToolResult, ToolSpec
from exo.principal import CALLER, Caller
from exo.server.engine import CommandFailed

SPEC = {'transport': 'openai', 'base_url': 'https://api.openai.com/v1'}
ROUTE = 'direct:gpt-5.1'


def router(primary, backup=None):
    return Router(roles={'main': [ROUTE] + (['backup:unknown'] if backup else []), 'aux': ['backup:unknown']},
                  providers={'direct': SPEC}, instances={'direct': primary, **({'backup': backup} if backup else {})},
                  catalog={}, env={}, key_sources=False)


def command(settings, engine, body, caller='principal:web'):
    token = CALLER.set(Caller(caller))
    try:
        return settings.command(engine, json.dumps(body))
    finally:
        CALLER.reset(token)


def setup(tmp_path):
    r = router(FakeProvider(['ok'], repeat=True))
    settings = ReasoningSettings(tmp_path, 'test', r)
    store = SessionStore(tmp_path / 'sessions.sqlite')
    engine = SimpleNamespace(loop=SimpleNamespace(model=ROUTE), store=store, agent='test')
    return settings, engine, r


def test_exact_capabilities_and_unknown_routes_are_honest():
    assert capability(SPEC, 'gpt-5.1')['levels'] == ['none', 'low', 'medium', 'high']
    assert not capability(SPEC, 'gpt-6.1-sol')['supported']
    for url in ('https://api.openai.com.evil/v1', 'https://evil.test/api.openai.com/v1', 'http://api.openai.com/v1',
                'https://api.openai.com:8080/v1', 'https://api.openai.com:bad/v1',
                'https://user:fake-password' '@api.openai.com/v1', 'https://api.openai.com/v1?foo',
                'https://api.openai.com/v1#fragment'):
        assert not capability({**SPEC, 'base_url': url}, 'gpt-5.1')['supported']
    assert 'gateway' in capability({**SPEC, 'base_url': 'https://opencode.ai/zen/go/v1'}, 'deepseek-v4-pro')['reason']
    assert '#616' in capability({**SPEC, 'base_url': 'https://api.deepseek.com'}, 'deepseek-v4-pro')['reason']


def test_principal_validation_persistence_inherit_and_route_race(tmp_path):
    settings, engine, r = setup(tmp_path)
    for caller in ('local', 'channel', 'remote', 'seat:test'):
        with pytest.raises(CommandFailed):
            command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': 'high'}, caller)
        assert not settings.path.exists()
    assert command(settings, engine, {'action': 'list'})['capability'] == CAPABILITY
    assert not settings.path.exists()
    for level in ('xhigh', 'ultra', 'invented', []):
        with pytest.raises(CommandFailed):
            command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': level})
    with pytest.raises(CommandFailed):
        command(settings, engine, {'action': 'set', 'route': 'stale:model', 'level': 'high'})
    command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': 'high'})
    assert ReasoningSettings(tmp_path, 'test', r).snapshot('session', ROUTE)['reasoning'] == 'high'
    assert ReasoningSettings(tmp_path, 'other-seat', r).snapshot('session', ROUTE) == {}
    command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': None})
    assert settings.snapshot('session', ROUTE) == {} and json.loads(settings.path.read_text()) == {}
    assert settings.path.stat().st_mode & 0o077 == 0


def test_main_turn_snapshot_stable_across_tool_rounds_and_other_loops_inherit(tmp_path):
    settings, engine, r = setup(tmp_path)
    p = FakeProvider([tool_turn('work'), 'finished', 'next turn'])
    r.instances['direct'] = p
    command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': 'high'})
    sid = engine.store.create_session(agent='test').id
    async def work(call, context):
        # A separate principal edit while this turn is between model calls.
        command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': 'low'})
        return ToolResult(call_id=call.id, content=[])
    loop = Loop(engine.store, r.provider('main'), model=ROUTE,
                tool_specs=[ToolSpec(name='work', description='test', parameters={})], run_tool=work)
    loop.reasoning_settings = settings
    asyncio.run(loop.send(sid, 'use work'))
    assert [c['opts']['reasoning'] for c in p.calls] == ['high', 'high']
    assert all('_exo_reasoning' not in c['opts'] for c in p.calls)
    last = engine.store.latest_model_call_reasoning()
    assert last['requested'] == last['sent'] == 'high'
    asyncio.run(loop.send(sid, 'next'))
    assert p.calls[-1]['opts']['reasoning'] == 'low'
    # A newly constructed auxiliary/scout loop never opts in to the settings hook.
    other = FakeProvider(['aux answer'])
    aux = Loop(engine.store, other)
    asyncio.run(aux.send(engine.store.create_session(agent='test').id, 'auxiliary'))
    assert 'reasoning' not in other.calls[0]['opts']


def test_fallback_uses_actual_route_contract_and_records_requested_vs_sent(tmp_path):
    first, backup = FakeProvider([rate_limited()]), FakeProvider(['backup'])
    r = router(first, backup)
    opts = {'reasoning': 'high', '_exo_reasoning': {'requested': 'high', 'route': ROUTE}}
    async def run():
        return [e async for e in r.stream('main', [Message.user('hello')], [], **opts)]
    done = asyncio.run(run())[-1]
    assert first.calls[0]['opts']['reasoning'] == 'high'
    assert 'reasoning' not in backup.calls[0]['opts']
    assert done.attempts[-1]['reasoning']['requested'] == 'high'
    assert done.attempts[-1]['reasoning']['sent'] is None
    assert 'unsupported' in done.attempts[-1]['reasoning']['status']


def test_inherit_preserves_wire_body_and_selected_level_reaches_adapter():
    provider = OpenAIChatProvider('https://api.openai.com/v1', None)
    assert 'reasoning_effort' not in provider.build_body([Message.user('hi')], [])
    assert provider.build_body([Message.user('hi')], [], reasoning='high')['reasoning_effort'] == 'high'


def test_selected_default_flows_through_loop_router_and_actual_http_body(tmp_path):
    import httpx
    settings, engine, r = setup(tmp_path)
    bodies = []
    def respond(request):
        bodies.append(json.loads(request.content))
        event = {'choices': [{'delta': {'content': 'mock answer'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n',
                              headers={'content-type': 'text/event-stream'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            r.instances['direct'] = OpenAIChatProvider(SPEC['base_url'], 'fake-test-only', client=client)
            loop = Loop(engine.store, r.provider('main'), model=ROUTE)
            loop.reasoning_settings = settings
            sid = engine.store.create_session(agent='test').id
            await loop.send(sid, 'inherit')
            command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': 'high'})
            await loop.send(sid, 'explicit high')
            command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': None})
            await loop.send(sid, 'inherit again')
    asyncio.run(run())
    assert len(bodies) == 3
    assert 'reasoning_effort' not in bodies[0] and 'reasoning_effort' not in bodies[2]
    assert bodies[1]['reasoning_effort'] == 'high'
    assert all(b['model'] == 'gpt-5.1' for b in bodies)


def test_real_engine_reserves_core_command_and_rejects_untrusted_caller(tmp_path, monkeypatch):
    from exo.core.engine import CoreEngine
    from exo.protocol import schema as wire
    from exo.server.app import Dispatcher
    monkeypatch.setenv('EXO_NO_KEYCHAIN', '1')
    monkeypatch.setenv('EXO_NO_SECRET_SERVICE', '1')
    monkeypatch.setenv('EXO_KEYS_FILE', str(tmp_path / 'unused-keys'))
    async def run():
        engine = CoreEngine(tmp_path / 'home', {'fake': {'reply': 'ok'}, 'roles': {'main': ['fake:scripted'], 'aux': ['fake:scripted']}}, cwd=str(tmp_path))
        dispatcher = Dispatcher(engine)
        request = wire.Command.of(wire.CommandRun(name='reasoning', args='{"action":"list"}'))
        try:
            refused = await dispatcher.run(request, 'local')
            assert not refused.ok and refused.error.code == 'forbidden'
            response = await dispatcher.run(request, 'principal:web')
            assert response.ok and response.result['output']['capability'] == CAPABILITY
            assert not response.result['output']['supported']
            assert not engine.reasoning_settings.path.exists()
        finally:
            await engine.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['malformed', 'deeply_nested', 'oversized', 'unreadable'])
def test_unavailable_preferences_never_drop_ingress_or_introduce_wire_options(tmp_path, monkeypatch, failure):
    from pathlib import Path
    settings, engine, r = setup(tmp_path)
    settings.path.parent.mkdir(parents=True)
    contents = {'malformed': '{', 'deeply_nested': '[' * 2000 + ']' * 2000, 'oversized': 'x' * 524289, 'unreadable': '{}'}
    settings.path.write_text(contents[failure])
    if failure == 'unreadable':
        original = Path.open
        def unavailable(path, *args, **kwargs):
            if path == settings.path:
                raise PermissionError('private-file-content-must-not-escape')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', unavailable)
    p = r.instances['direct']
    loop = Loop(engine.store, r.provider('main'), model=ROUTE)
    loop.reasoning_settings = settings
    sid = engine.store.create_session(agent='test').id
    result = asyncio.run(loop.send(sid, 'keep this ingress'))
    assert result.stop_reason == 'end'
    assert any(m.role == 'user' and m.text() == 'keep this ingress' for m in engine.store.messages(sid))
    assert 'reasoning' not in p.calls[0]['opts'] and '_exo_reasoning_warning' not in p.calls[0]['opts']
    events = engine.store.events_since(session=sid)
    assert any(e.type == 'notice' and 'preferences are unavailable' in e.body.text for e in events)
    assert all('private-file-content-must-not-escape' not in str(e) for e in events)
    last = engine.store.latest_model_call_reasoning()
    assert last['sent'] is None and 'unavailable' in last['status']
    listing = command(settings, engine, {'action': 'list'})
    assert listing['preferences_available'] is False and listing['warning']
    with pytest.raises(CommandFailed):
        command(settings, engine, {'action': 'set', 'route': ROUTE, 'level': 'high'})
    monkeypatch.undo()
    repaired = command(settings, engine, {'action': 'reset'})
    assert repaired['preferences_available'] and settings.load() == {}
