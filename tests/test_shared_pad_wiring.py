"""Offline composition/CommandRun coverage; no real config, plugins or providers."""
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from exo import compose
from exo.application import CoreEngine
from exo.config import Config, ConfigError
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.plugins import PluginManager
from exo.core.seats import Seat
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.shared_pad_adapter import attach

MODELS = {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith(('EXO_', 'DIVAN_', 'XDG_')):
            monkeypatch.delenv(key)
    for key in ('HOME', 'USERPROFILE', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'):
        monkeypatch.setenv(key, str(tmp_path / key))
    monkeypatch.setenv('EXO_HOME', str(tmp_path / 'unused-home'))
    monkeypatch.setenv('EXO_NO_KEYCHAIN', '1')
    monkeypatch.setenv('EXO_NO_SECRET_SERVICE', '1')
    monkeypatch.setenv('EXO_KEYS_FILE', str(tmp_path / 'absent-keys'))
    monkeypatch.setattr(compose, 'load_executor', lambda: (None, False))
    monkeypatch.setattr(PluginManager, 'load_all', lambda *a, **kw: None)
    monkeypatch.setattr(compose.peers, 'attach', lambda eng: None)
    monkeypatch.setattr(compose, 'Scheduler', lambda eng: None)
    monkeypatch.setattr(compose, 'fitting_peers_dir', lambda: None)
    monkeypatch.setattr(compose, 'pack_dir', lambda: None)


def build(tmp_path, monkeypatch, **settings):
    cfg = Config({'models': MODELS, 'cwd': str(tmp_path), 'learn': {'enabled': False},
                  'work_checkpoint': {'enabled': False}, 'schedule': False, **settings}, tmp_path / 'home')
    monkeypatch.setattr(compose.Config, 'load', lambda *a, **kw: cfg)
    return compose.create()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 15))


async def request(eng, args='', caller='principal:web', session=None, name='pad'):
    return await Dispatcher(eng, caller=caller).run(
        s.Command(type='command.run', id='wiring-test', body=s.CommandRun(name=name, args=args, session=session)))


@pytest.mark.parametrize('settings', [{}, {'shared_pad': {}}, {'shared_pad': {'enabled': False}}])
def test_disabled_is_reserved_and_fail_closed(tmp_path, monkeypatch, settings):
    eng = build(tmp_path, monkeypatch, **settings)
    eng.plugins.commands['pad'] = SimpleNamespace(handler=lambda args: pytest.fail('plugin fallback'))
    async def scenario():
        try:
            assert eng.registry.get('shared_pad') is None
            assert not hasattr(eng, 'shared_pad')
            for args in ('', '{"action":"history"}', '{"action":"update","expected_revision":0,"document":"x"}'):
                assert (await request(eng, args)).error.code == 'unsupported'
                assert (await request(eng, args, caller='local')).error.code == 'forbidden'
            assert not (eng.home / 'shared-pad').exists()
        finally:
            await eng.close()
    run(scenario())


@pytest.mark.parametrize('value', [None, False, [], 'true', {'enabled': 1}, {'enabled': 'true'},
                                    {'owner': 'x'}, {'enabled': True, 'path': '/untrusted'}])
def test_invalid_config_precedes_plugins_executor_and_engine(tmp_path, monkeypatch, value):
    cfg = Config({'shared_pad': value}, tmp_path / 'never-created')
    monkeypatch.setattr(compose.Config, 'load', lambda: cfg)
    def forbidden(*a, **kw):
        pytest.fail('configuration must fail before side effects')
    for key in ('load_executor', 'PluginManager', 'CoreEngine'):
        monkeypatch.setattr(compose, key, forbidden)
    with pytest.raises(ConfigError):
        compose.create()
    assert not cfg.home.exists()


@pytest.mark.parametrize('seat_principal', [None, 'seat-owner'])
def test_trusted_binding_and_command_roundtrip(tmp_path, monkeypatch, seat_principal, offline_pad_gate):
    if seat_principal:
        monkeypatch.setenv('EXO_SEAT', 'test-seat')
        monkeypatch.setattr(compose, 'load_seat', lambda *a: Seat(name='test-seat', principal=seat_principal))
    eng = build(tmp_path, monkeypatch, shared_pad={'enabled': True}, principal='config-owner',
                work_notes={'enabled': True})
    eng.plugins.hooks.register('pre_tool_call', offline_pad_gate, plugin='offline-pad-test')
    identity = ['principal', seat_principal or 'config-owner']
    expected = 'owner:' + hashlib.sha256(b'exo.shared-pad.owner.v1\0' + json.dumps(identity).encode()).hexdigest()
    async def scenario():
        try:
            assert eng.shared_pad.owner == expected
            assert (eng.home / 'shared-pad' / 'pads.sqlite').is_file()
            assert eng.registry.get('shared_pad') and eng.registry.get('work_notes')
            assert 'shared_pad' not in eng.handoff_sources
            a = eng.store.create_session(agent=eng.agent).id
            b = eng.store.create_session(agent=eng.agent).id
            empty = (await request(eng, session=a)).result['output']
            assert empty['snapshot']['revision'] == 0
            update = json.dumps({'action': 'update', 'expected_revision': 0, 'document': 'shared ☃'})
            saved = (await request(eng, update, session=a)).result['output']
            assert saved['ok'] and saved['snapshot']['revision'] == 1
            assert (await request(eng, caller='principal:terminal', session=b)).result['output'] == saved
            conflict = (await request(eng, update)).result['output']
            assert conflict == {'ok': False, 'code': 'revision_conflict', 'current': saved['snapshot']}
            assert set(saved['snapshot']) == {'pad', 'revision', 'created', 'document'}
            assert (await request(eng, '{"action":"history"}')).result['output']['history'] == [saved['snapshot']]
            for key in ('owner', 'principal', 'path', 'pad'):
                for action in ({'action': 'read'}, {'action': 'history'}, json.loads(update)):
                    assert (await request(eng, json.dumps({**action, key: 'attacker'}))).error.code == 'bad_request'
            for caller in ('local', 'channel', 'unknown'):
                for args in ('', '{"action":"history"}', update):
                    assert (await request(eng, args, caller=caller)).error.code == 'forbidden'
            private = eng.store.create_session(agent=eng.agent, kind='private').id
            assert (await request(eng, session=private)).error.code == 'forbidden'
            assert (await request(eng)).result['output'] == saved
            assert eng.shared_pad.owner == expected
            # Existing work command remains read-only and independently routed.
            assert (await request(eng, session=a, name='work')).error is None
            assert (await request(eng, 'update', session=a, name='work')).error.code == 'bad_request'
            eng.plugins.commands['custom'] = SimpleNamespace(handler=lambda args: 'plugin:' + args)
            assert (await request(eng, 'ok', name='custom')).result == {'output': 'plugin:ok'}
            assert (await request(eng, name='missing-command')).error.code == 'not_found'
        finally:
            await eng.close()
    run(scenario())


@pytest.fixture
def offline_pad_gate():
    """Allow the exact pad update and read-only work regression call; deny all else."""
    def gate(tool_name, args):
        if tool_name == 'shared_pad' and args == {
            'action': 'update', 'expected_revision': 0, 'document': 'agent notes'
        }:
            return {'action': 'allow'}
        if tool_name == 'work_notes' and args == {'action': 'read'}:
            return {'action': 'allow'}
        return {'action': 'block', 'message': 'Outside offline pad test policy'}
    return gate


def test_direct_core_attachment_ordinary_tool_and_command_share_pad(tmp_path, offline_pad_gate):
    eng = CoreEngine(tmp_path / 'direct', MODELS, cwd=str(tmp_path), provider_auth={})
    eng.plugins.hooks.register('pre_tool_call', offline_pad_gate, plugin='offline-pad-test')
    attach(eng, principal='trusted', enabled=True)
    provider = FakeProvider([tool_turn('shared_pad', {'action': 'update', 'expected_revision': 0,
                                                    'document': 'agent notes'}), 'saved'])
    eng.loop.provider = provider
    async def scenario():
        try:
            sid = (await eng.loop.create_session(agent=eng.agent, cwd=str(tmp_path))).id
            await eng.loop.send(sid, 'Write brainstorming notes')
            results = [r for m in eng.store.messages(sid) for r in m.tool_results]
            assert results and not results[-1].is_error, [r.text() for r in results]
            snapshot = (await request(eng)).result['output']['snapshot']
            assert snapshot['revision'] == 1 and snapshot['document'] == 'agent notes'
            assert len(provider.calls) == 2
            assert any(spec.name == 'shared_pad' for spec in provider.calls[0]['tools'])
        finally:
            await eng.close()
    run(scenario())
