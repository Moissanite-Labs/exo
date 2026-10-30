"""Worker policy changes are validated, atomic and human-authorized, with no inference."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from exo.config import Config
from exo.core.engine import CoreEngine
from exo.principal import CALLER, Caller
from exo.server.engine import CommandFailed
from exo.worker_models import snapshot, update


@pytest.fixture
def eng(tmp_path):
    cfg = Config(home=tmp_path)
    cfg.set('models.roles', {'main': ['fake:main'], 'aux': ['fake:aux'], 'fast': ['fake:old']})
    cfg.set('plugin_config.keep', {'enabled': True})
    cfg.save()
    engine = CoreEngine(tmp_path, cfg.get('models'), env={}, cwd=str(tmp_path))
    token = CALLER.set(Caller('principal:test'))
    yield engine
    CALLER.reset(token)
    asyncio.run(engine.close())


def routes():
    return {'fast': ['fake:new', 'fake:backup'], 'standard': ['fake:standard'], 'deep': ['fake:deep']}


def test_apply_replaces_live_routes_and_survives_restart_without_changing_main(eng):
    before = snapshot(eng)
    out = update(eng, routes(), before["revision"])
    assert out['applied'] and out['saved']
    assert out['roles'] == routes() and out['revision'] != before['revision']
    assert eng.loop.model == 'fake:main' and eng.router.roles['aux'] == ['fake:aux']
    cfg = Config.load(eng.home)
    assert cfg.get('plugin_config.keep') == {'enabled': True}
    assert cfg.get('models.roles.fast') == ['fake:new', 'fake:backup']
    restarted = CoreEngine(eng.home / 'restart', cfg.get('models'), env={})
    try:
        assert {t: restarted.router.roles[t] for t in routes()} == routes()
    finally:
        asyncio.run(restarted.close())


@pytest.mark.parametrize('roles', [
    {'fast': ['fake:new']},
    {'fast': ['fake:new'], 'standard': ['unknown:model'], 'deep': []},
    {'fast': ['fake:new'], 'standard': ['fake:bad ref'], 'deep': []},
    {'fast': ['fake:new'], 'standard': ['fake:'], 'deep': []},
    {'fast': ['fake:new', 'fake:new'], 'standard': [], 'deep': []},
    {'fast': ['fake:a'] * 17, 'standard': [], 'deep': []},
    {'fast': [123], 'standard': [], 'deep': []},
    {'fast': ['fake:new'], 'standard': [], 'deep': [], 'main': ['fake:changed']},
])
def test_bad_route_in_any_tier_changes_neither_disk_nor_router(eng, roles):
    old = copy.deepcopy(eng.router.roles)
    data = (eng.home / 'config.json').read_bytes()
    with pytest.raises(CommandFailed):
        update(eng, roles, snapshot(eng)['revision'])
    assert eng.router.roles == old and (eng.home / 'config.json').read_bytes() == data


@pytest.mark.parametrize('who', ['local', 'channel', 'unknown'])
def test_agent_and_channel_cannot_read_or_change_human_settings(eng, who):
    revision = snapshot(eng)['revision']
    token = CALLER.set(Caller(who))
    try:
        for op in (lambda: snapshot(eng), lambda: update(eng, routes(), revision)):
            with pytest.raises(CommandFailed) as exc:
                op()
            assert exc.value.code == 'forbidden'
    finally:
        CALLER.reset(token)


def test_stale_client_cannot_overwrite_other_model_changes(eng):
    revision = snapshot(eng)['revision']
    eng.router.prefer('deep', 'fake:other')
    with pytest.raises(CommandFailed) as exc:
        update(eng, routes(), revision)
    assert exc.value.code == 'conflict'
    assert eng.router.roles['deep'][0] == 'fake:other'


def test_disk_change_invalidates_revision_even_with_unchanged_live_routes(eng):
    revision = snapshot(eng)['revision']
    cfg = Config.load(eng.home)
    cfg.set('models.roles.deep', ['fake:other'])
    cfg.save()
    with pytest.raises(CommandFailed) as exc:
        update(eng, routes(), revision)
    assert exc.value.code == 'conflict'


def test_save_failure_keeps_live_routes(eng, monkeypatch):
    old = copy.deepcopy(eng.router.roles)
    def fail(*args):
        raise OSError('private details')
    monkeypatch.setattr(Config, 'save', fail)
    with pytest.raises(CommandFailed) as exc:
        update(eng, routes(), snapshot(eng)['revision'])
    assert exc.value.code == 'save_failed' and 'private details' not in str(exc.value)
    assert eng.router.roles == old


def test_provider_role_restrictions_are_enforced_without_mutation(eng):
    eng.router.providers['restricted'] = {'roles': ['main']}
    bad = routes(); bad['fast'] = ['restricted:x']
    old = copy.deepcopy(eng.router.roles)
    with pytest.raises(CommandFailed):
        update(eng, bad, snapshot(eng)['revision'])
    assert eng.router.roles == old


def test_empty_tier_inherits_and_effective_routes_are_visible(eng):
    new = routes(); new['fast'] = []
    out = update(eng, new, snapshot(eng)['revision'])
    assert out['roles']['fast'] == []
    assert out['effective']['fast'] == ['fake:standard', 'fake:main']


@pytest.mark.parametrize('override', ['profile', 'delegate'])
def test_overrides_block_a_save_that_would_not_survive_restart_or_affect_workers(eng, override):
    if override == 'profile':
        eng.seat = SimpleNamespace(models={'roles': {'fast': ['fake:profile']}})
    else:
        eng.router.set_role('delegate', ['fake:forced'])
    assert not snapshot(eng)['editable']
    with pytest.raises(CommandFailed) as exc:
        update(eng, routes(), snapshot(eng)['revision'])
    assert exc.value.code == 'bad_request'
