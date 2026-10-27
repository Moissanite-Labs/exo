"""Principal/write-only credential service, fake Keychain and disposable homes only."""
from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest

from exo import providers_setup as ps
from exo.core import credentials as vault
from exo.core import github_auth as github
from exo.core.engine import CoreRuntime
from exo.core.models import keys
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo.server.engine import Hub

SECRET = 'fake-write-only-credential-1234567890'
KEYCHAIN_SET = ps._keychain_set


@pytest.fixture
def store(monkeypatch, tmp_path):
    data = {}
    monkeypatch.setattr(vault, 'store_available', lambda: True)
    monkeypatch.setattr('exo.providers_setup._keychain_set', lambda name, value: data.__setitem__(name, value))
    monkeypatch.setattr('exo.providers_setup._keychain_delete', lambda name: data.pop(name, None) is not None)
    def fake_run(argv, **kwargs):
        assert argv[:2] == ['security', 'delete-generic-password']
        return subprocess.CompletedProcess(argv, 0 if data.pop(argv[argv.index('-a') + 1], None) is not None else 44, '', '')
    monkeypatch.setattr('exo.providers_setup._run', fake_run)
    monkeypatch.setattr(keys, 'keychain_get', lambda name: data.get(name))
    monkeypatch.setattr('exo.providers_setup._secret_tool_delete', lambda name: False)
    monkeypatch.setattr('exo.providers_setup.remove_from_keys_file', lambda name: False)
    monkeypatch.setenv('EXO_KEYS_FILE', str(tmp_path / 'must-not-exist.env'))
    monkeypatch.delenv('EXO_NO_KEYCHAIN', raising=False)
    monkeypatch.setattr(github.sys, 'platform', 'darwin')
    return data


def dispatch(home, body, who='principal:web', deny=()):
    engine = SimpleNamespace(home=home, agent='canary', env_deny=deny)
    async def handle(command, hub):
        return await CoreRuntime._command_run(engine, command.body)
    engine.handle = handle
    return asyncio.run(Dispatcher(engine, Hub()).run(s.Command.of(s.CommandRun(name='credentials', args=json.dumps(body))), caller=who))


def save(home, name='weather', value=SECRET, env='EXO_SECRET_WEATHER', consumers=None):
    return dispatch(home, {'action': 'set', 'name': name, 'env': env, 'consumers': consumers or ['bash'], 'value': value})


def test_scoped_write_only_metadata_no_plaintext_or_events(tmp_path, store):
    assert save(tmp_path).ok
    response = dispatch(tmp_path, {'action': 'list'})
    assert response.ok and response.result['output']['capability'] == vault.CAPABILITY
    text = response.to_json()
    assert SECRET not in text and 'EXO_SECRET_WEATHER' in text and 'canary' in text
    assert SECRET not in ''.join(p.read_text() for p in tmp_path.rglob('*') if p.is_file())
    assert not (tmp_path / 'must-not-exist.env').exists()
    assert len(store) == 1
    assert vault.account(tmp_path, 'canary', 'weather') != vault.account(tmp_path, 'other', 'weather')
    assert vault.account(tmp_path, 'canary', 'weather') != vault.account(tmp_path / 'other', 'canary', 'weather')
    assert vault.account(tmp_path, 'canary', 'weather') != vault.account(tmp_path, 'canary', 'other')


@pytest.mark.parametrize('who', ['local', 'channel', 'unknown', 'principalish'])
def test_untrusted_caller_refused_before_store_or_parse(tmp_path, store, monkeypatch, who):
    monkeypatch.setattr(vault, 'handle', lambda *args: pytest.fail('store must not run'))
    response = dispatch(tmp_path, {'action': 'set', 'value': SECRET}, who=who)
    assert not response.ok and response.error.code == 'forbidden' and not store
    assert SECRET not in response.to_json()


@pytest.mark.parametrize('failure', ['missing', 'locked', 'timeout'])
def test_secure_store_failures_no_plaintext_fallback_or_secret_error(tmp_path, store, monkeypatch, failure):
    if failure == 'missing':
        monkeypatch.setattr(vault, 'store_available', lambda: False)
    else:
        def fail(*args):
            raise subprocess.TimeoutExpired(['fake'], 1, output=SECRET) if failure == 'timeout' else RuntimeError(SECRET)
        monkeypatch.setattr('exo.providers_setup._keychain_set', fail)
    response = save(tmp_path)
    assert not response.ok and response.error.code == 'credential_error'
    assert SECRET not in response.to_json() and not store
    assert not (tmp_path / 'must-not-exist.env').exists()
    assert vault._load(tmp_path, 'canary') == {}


@pytest.mark.parametrize('env', ['GH_TOKEN', 'GITHUB_TOKEN', 'PATH', 'LD_PRELOAD', 'EXO_SECRET_bad', 'EXO_SECRET_X;evil'])
def test_only_reserved_secret_namespace_is_bindable(tmp_path, store, env):
    assert not save(tmp_path, env=env).ok
    assert not store


def test_exact_consumers_deny_rotation_and_catalog(tmp_path, store):
    assert save(tmp_path).ok
    before = {'HOME': '/fake/home'}
    first = vault.child_env(before, tmp_path, 'canary', 'bash')
    assert first['EXO_SECRET_WEATHER'] == SECRET and before == {'HOME': '/fake/home'}
    assert 'EXO_SECRET_WEATHER' not in vault.child_env(before, tmp_path, 'canary', 'jobs')
    assert 'EXO_SECRET_WEATHER' not in vault.child_env(before, tmp_path, 'canary', 'provider')
    assert 'EXO_SECRET_WEATHER' not in vault.child_env(before, tmp_path, 'other', 'bash')
    with pytest.raises(vault.CredentialError):
        vault.child_env(before, tmp_path, 'canary', 'bash', deny=['EXO_SECRET_*'])
    denied = dispatch(tmp_path, {'action': 'set', 'name': 'blocked', 'env': 'EXO_SECRET_BLOCKED', 'consumers': ['bash'], 'value': SECRET}, deny=['EXO_SECRET_*'])
    assert not denied.ok and vault.account(tmp_path, 'canary', 'blocked') not in store
    assert save(tmp_path, value=SECRET + 'rotated').ok
    assert vault.child_env(before, tmp_path, 'canary', 'bash')['EXO_SECRET_WEATHER'] == SECRET + 'rotated'
    assert first['EXO_SECRET_WEATHER'] == SECRET
    prompt = vault.catalog_prompt(tmp_path, 'canary')
    assert 'EXO_SECRET_WEATHER for bash' in prompt and SECRET not in prompt
    assert 'EXO_SECRET_WEATHER' not in vault.catalog_prompt(tmp_path, 'canary', deny=['EXO_SECRET_*'])


def test_delete_failure_disables_binding_and_reports_cleanup(tmp_path, store, monkeypatch):
    assert save(tmp_path).ok
    monkeypatch.setattr(vault, '_delete', lambda name: False)
    response = dispatch(tmp_path, {'action': 'remove', 'name': 'weather'})
    assert response.ok and response.result['output'] == {'ok': False, 'disabled': True, 'deleted': False, 'cleanup_pending': True}
    assert 'EXO_SECRET_WEATHER' not in vault.child_env({}, tmp_path, 'canary', 'bash')
    assert store  # failed deletion must not be described as a removed Keychain item


def test_generic_test_checks_store_only_and_github_test_sanitizes_output(tmp_path, store, monkeypatch):
    assert save(tmp_path).ok
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('generic test must not use the network'))
    checked = dispatch(tmp_path, {'action': 'test', 'name': 'weather'}).result['output']
    assert checked == {'ok': True, 'storage_available': True, 'remote_authentication_tested': False}
    # A secret under a misleading process output must never become a response.
    monkeypatch.setattr(github, 'child_env', lambda env, *args, **kwargs: env)
    assert dispatch(tmp_path, {'action': 'github_set', 'value': SECRET, 'host': 'github.com'}).ok
    calls = []
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, SECRET, '')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    response = dispatch(tmp_path, {'action': 'github_test'})
    assert response.ok and response.result['output']['ok'] is False
    assert SECRET not in response.to_json() and SECRET not in repr(calls[0][0])
    assert calls[0][0][:3] == ['gh', 'api', 'user']


def test_metadata_list_does_not_read_keychain_and_invalid_json_is_quiet(tmp_path, store, monkeypatch):
    assert save(tmp_path).ok
    monkeypatch.setattr(keys, 'keychain_get', lambda name: pytest.fail('metadata must not read or unlock Keychain'))
    assert dispatch(tmp_path, {'action': 'list'}).ok
    engine = SimpleNamespace(home=tmp_path, agent='canary', env_deny=())
    async def handle(command, hub):
        return await CoreRuntime._command_run(engine, command.body)
    engine.handle = handle
    result = asyncio.run(Dispatcher(engine, Hub()).run(s.Command.of(s.CommandRun(name='credentials', args=SECRET)), caller='principal:web'))
    assert not result.ok and SECRET not in result.to_json()


def test_inherited_secret_namespace_never_bypasses_explicit_binding(tmp_path, store):
    inherited = {'EXO_SECRET_WEATHER': 'host-value', 'EXO_SECRET_OTHER': 'other-host-value', 'PATH': '/fake/bin'}
    assert vault.child_env(inherited, tmp_path, 'canary', 'bash') == {'PATH': '/fake/bin'}
    assert save(tmp_path).ok
    assert vault.child_env(inherited, tmp_path, 'canary', 'bash') == {'PATH': '/fake/bin', 'EXO_SECRET_WEATHER': SECRET}
    assert vault.child_env(inherited, tmp_path, 'canary', 'jobs') == {'PATH': '/fake/bin'}
    assert vault.child_env(inherited, tmp_path, 'canary', 'provider') == {'PATH': '/fake/bin'}
    assert dispatch(tmp_path, {'action': 'remove', 'name': 'weather'}).ok
    assert vault.child_env(inherited, tmp_path, 'canary', 'bash') == {'PATH': '/fake/bin'}


def test_removing_already_absent_item_confirms_deletion(tmp_path, store):
    assert save(tmp_path).ok
    store.clear()  # an item removed outside Exo is already absent
    result = dispatch(tmp_path, {'action': 'remove', 'name': 'weather'}).result['output']
    assert result == {'ok': True, 'disabled': True, 'deleted': True, 'cleanup_pending': False}
    result = dispatch(tmp_path, {'action': 'remove', 'name': 'weather'}).result['output']
    assert result['deleted'] is True and result['cleanup_pending'] is False


def test_ui_scope_cannot_select_another_home_or_seat(tmp_path, store):
    response = dispatch(tmp_path, {'action': 'set', 'name': 'weather', 'env': 'EXO_SECRET_WEATHER',
                                   'consumers': ['bash'], 'value': SECRET, 'seat': 'other', 'home': '/not-the-core'})
    assert response.ok and set(store) == {vault.account(tmp_path, 'canary', 'weather')}


def test_keychain_writer_uses_private_stdin_not_secret_arguments(tmp_path, store, monkeypatch, capsys):
    # Exercise the actual existing writer with a fake process boundary.
    calls = []
    def fake_run(argv, stdin=None):
        calls.append((argv, stdin))
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(ps, '_run', fake_run)
    monkeypatch.setattr(ps, '_keychain_set', KEYCHAIN_SET)
    response = save(tmp_path)
    assert response.ok and calls[0][0] == ['security', '-i']
    assert SECRET not in repr(calls[0][0]) and SECRET in calls[0][1]
    assert SECRET not in capsys.readouterr().out


def test_remove_metadata_failure_after_delete_can_be_retried_as_already_absent(tmp_path, store, monkeypatch):
    assert save(tmp_path).ok
    original = vault._save
    writes = 0
    def interrupted(home, seat, data):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError('fixture metadata failure')
        return original(home, seat, data)
    monkeypatch.setattr(vault, '_save', interrupted)
    failed = dispatch(tmp_path, {'action': 'remove', 'name': 'weather'})
    assert not failed.ok and not store
    assert vault._load(tmp_path, 'canary')['weather']['enabled'] is False
    assert 'EXO_SECRET_WEATHER' not in vault.child_env({'EXO_SECRET_WEATHER': 'host-value'}, tmp_path, 'canary', 'bash')
    monkeypatch.setattr(vault, '_save', original)
    retried = dispatch(tmp_path, {'action': 'remove', 'name': 'weather'}).result['output']
    assert retried['deleted'] is True and retried['cleanup_pending'] is False


def test_remove_with_backend_disabled_revokes_binding_without_os_access(tmp_path, store, monkeypatch):
    assert save(tmp_path).ok
    monkeypatch.setattr(vault, 'store_available', lambda: False)
    monkeypatch.setattr('exo.providers_setup._run', lambda *args, **kwargs: pytest.fail('disabled Keychain must not be contacted'))
    result = dispatch(tmp_path, {'action': 'remove', 'name': 'weather'}).result['output']
    assert result['disabled'] is True and result['cleanup_pending'] is True and result['deleted'] is False
    assert 'EXO_SECRET_WEATHER' not in vault.child_env({'EXO_SECRET_WEATHER': 'host'}, tmp_path, 'canary', 'bash')


def test_actual_core_dispatch_checks_credentials_without_starting_runtime(tmp_path, store):
    async def go():
        engine = SimpleNamespace(home=tmp_path, agent='canary', env_deny=())
        async def start():
            pytest.fail('credential setup must not start providers or MCP')
        engine.start = start
        async def command(body):
            return await CoreRuntime._command_run(engine, body)
        engine._command_run = command
        async def handle(body, hub):
            return await CoreRuntime.handle(engine, body, hub)
        engine.handle = handle
        hub = Hub()
        dispatcher = Dispatcher(engine, hub)
        command = s.Command.of(s.CommandRun(name='credentials', args=json.dumps({'action': 'list'})))
        refused = await dispatcher.run(command, caller='local')
        accepted = await dispatcher.run(command, caller='principal:web')
        assert refused.error.code == 'forbidden' and accepted.ok
        assert hub.head() == 0  # no command payload or secret-bearing event is persisted
    asyncio.run(go())


def test_tcp_cannot_forge_principal_credential_setup(tmp_path, store):
    import httpx
    from exo.server.app import HttpServer
    async def go():
        engine = SimpleNamespace(home=tmp_path, agent='canary', env_deny=())
        async def handle(command, hub):
            return await CoreRuntime._command_run(engine, command.body)
        engine.handle = handle
        server = HttpServer(Dispatcher(engine, Hub()), token='fake-core-bearer', principal=True)
        port = await server.start_tcp('127.0.0.1', 0)
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
                payload = s.Command.of(s.CommandRun(name='credentials', args=json.dumps({'action': 'set', 'value': SECRET})))
                result = await client.post('/command', content=payload.to_json(),
                                          headers={'Authorization': 'Bearer fake-core-bearer', 'X-Exo-Principal': 'forged', 'X-Exo-Client': 'web'})
                assert result.json()['error']['code'] == 'forbidden'
                assert SECRET not in result.text and not store
        finally:
            await server.close()
    asyncio.run(go())


def test_real_core_secret_command_success_and_failure_never_persist_payload(tmp_path, store, monkeypatch):
    from exo.core.engine import CoreEngine
    from exo.core.seats import Seat
    async def go():
        engine = CoreEngine(tmp_path / 'home', {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}},
                            cwd=str(tmp_path), env={}, seat=Seat(name='canary'))
        dispatcher = Dispatcher(engine)
        counts = lambda: tuple(engine.store._q(f'SELECT COUNT(*) FROM {table}')[0][0] for table in ('events', 'messages'))
        before = counts()
        body = {'action': 'set', 'name': 'weather', 'env': 'EXO_SECRET_WEATHER', 'consumers': ['bash'], 'value': SECRET}
        try:
            accepted = await dispatcher.run(s.Command.of(s.CommandRun(name='credentials', args=json.dumps(body))), caller='principal:web')
            assert accepted.ok
            def fail(*args):
                raise RuntimeError(SECRET)
            monkeypatch.setattr('exo.providers_setup._keychain_set', fail)
            rejected = await dispatcher.run(s.Command.of(s.CommandRun(name='credentials', args=json.dumps({**body, 'value': SECRET + 'rotated'}))), caller='principal:web')
            assert not rejected.ok and SECRET not in rejected.to_json()
            assert counts() == before
            assert SECRET not in json.dumps([dict(row) for row in engine.store._q('SELECT * FROM events')])
            assert SECRET not in json.dumps([dict(row) for row in engine.store._q('SELECT * FROM messages')])
        finally:
            await engine.close()
        assert not any(SECRET.encode() in path.read_bytes() for path in tmp_path.rglob('*') if path.is_file())
    asyncio.run(go())


def test_generic_bindings_reach_only_allowed_shell_and_job_consumers_with_output_redaction(tmp_path, store):
    from exo.core.engine import CoreEngine
    from exo.core.seats import Seat
    from exo.core.tools.builtin.shell import run_command
    home = tmp_path / 'home'
    assert save(home, consumers=['bash', 'jobs']).ok
    streamed = []
    code, output, status = run_command('printf "%s" "$EXO_SECRET_WEATHER"', str(tmp_path), 3,
                                       github_auth=(str(home), 'canary'), emit=streamed.append)
    assert code == 0 and status == 'ok' and '[redacted]' in output
    assert SECRET not in output + ''.join(streamed)
    async def go():
        engine = CoreEngine(home, {'roles': {'main': ['fake:x'], 'aux': ['fake:x']}},
                            cwd=str(tmp_path), env={}, seat=Seat(name='canary'), jobs={'wake': False})
        try:
            sid = (await engine.loop.create_session(agent='canary')).id
            result = engine.jobs.start('printf "%s" "${EXO_SECRET_WEATHER:0:10}"; sleep 0.03; printf "%s" "${EXO_SECRET_WEATHER:10}"', str(tmp_path), sid)
            for _ in range(300):
                if result['id'] not in engine.jobs.live:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail('fake job did not finish')
            text = engine.jobs.log_path(result['id']).read_text()
            assert '[redacted]' in text and SECRET not in text and SECRET[:10] not in text
        finally:
            await engine.close()
    asyncio.run(go())
