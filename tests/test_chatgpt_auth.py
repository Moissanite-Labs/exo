"""Fake OAuth/OS-store only: no account, browser, host credentials or remote requests."""
import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from exo import chatgpt_auth as auth


class Store:
    def __init__(self):
        self.values = {}
        self.reads = 0
        self.fail = False
        self.backend = 'fake-os-store'

    def available(self):
        return True

    def get(self, name):
        self.reads += 1
        return copy.deepcopy(self.values[name])

    def set(self, name, value):
        if self.fail:
            raise RuntimeError('do not echo fake-token')
        self.values[name] = copy.deepcopy(value)

    def delete(self, name):
        self.values.pop(name, None)
        return True


def grant(**updates):
    return dict(id_token='fake-id-token', access_token='fake-access-token', refresh_token='fake-refresh-token',
                expires_in=3600, token_type='Bearer', scope=auth.SCOPES, **updates)


def setup(tmp_path, *, response=None, verify=None, handler=None):
    calls, urls, store = [], [], Store()
    def serve(request):
        calls.append(request)
        if handler:
            return handler(request)
        return httpx.Response(200, json=response if response is not None else grant())
    client = httpx.Client(transport=httpx.MockTransport(serve))
    service = auth.Service(tmp_path, 'seat', store=store, client=client,
                           verify=verify or (lambda *_: {'sub': 'account-one', 'email': 'test@example.invalid'}),
                           browser=lambda url: urls.append(url) or True)
    return service, store, calls, urls


def finish(service, *, client='oaiapp_test', state=None, **extra):
    pending = service.pending
    query = {'state': [state or pending['state']], 'code': ['one-use-code'], 'client_id': [client], **extra}
    return service.complete(query, pending)


def test_dynamic_registration_callback_and_no_tokens_in_metadata(tmp_path):
    service, store, calls, urls = setup(tmp_path)
    status = service.start()
    assert status['pending'] and not status['available']
    params = parse_qs(urlsplit(urls[0]).query)
    assert params['client_id'] == ['dynamic_agent_client'] and params['agent_name_hint'] == ['Exo']
    assert params['scope'] == [auth.SCOPES] and params['resource'] == [auth.RESOURCE]
    assert params['code_challenge_method'] == ['S256']
    assert params['redirect_uri'][0].startswith('http://127.0.0.1:')
    assert params['redirect_uri'][0].endswith('/auth/callback')
    pending = service.pending
    with httpx.Client(trust_env=False) as client:
        result = client.get(params['redirect_uri'][0], params={'state': pending['state'], 'code': 'code', 'client_id': 'oaiapp_test'})
    assert result.status_code == 200 and 'code' not in result.text
    assert service.status()['available'] and not service.status()['pending']
    assert parse_qs(calls[0].content.decode())['client_id'] == ['oaiapp_test']
    assert parse_qs(calls[0].content.decode())['redirect_uri'] == params['redirect_uri']
    snapshot = json.dumps(service.status()) + service.path.read_text()
    assert all(token not in snapshot for token in ('fake-id-token', 'fake-access-token', 'fake-refresh-token'))
    assert len(store.values) == 1
    assert not service.complete({'state': [pending['state']], 'code': ['replay']}, pending)
    assert len(calls) == 1


@pytest.mark.parametrize('kind', ['wrong-state', 'duplicate-state', 'missing-client', 'denied', 'invalid-signature', 'wrong-account', 'store-failure'])
def test_invalid_new_flow_preserves_active_registration(tmp_path, kind):
    service, store, calls, urls = setup(tmp_path)
    service.start(); assert finish(service)
    active = service.status()['active']
    service.start(active if kind == 'wrong-account' else None)
    pending = service.pending
    query = {'state': [pending['state']], 'code': ['code'], 'client_id': ['oaiapp_test']}
    if kind == 'wrong-state': query['state'] = ['wrong']
    if kind == 'duplicate-state': query['state'] *= 2
    if kind == 'missing-client': query.pop('client_id')
    if kind == 'denied': query['error'] = ['access_denied']
    if kind == 'invalid-signature': service.verify = lambda *_: (_ for _ in ()).throw(ValueError('fake-token'))
    if kind == 'wrong-account': service.verify = lambda *_: {'sub': 'other', 'email': 'other@example.invalid'}
    if kind == 'store-failure': store.fail = True
    assert not service.complete(query, pending)
    assert service.status()['active'] == active and service.status()['available']
    assert 'fake-token' not in json.dumps(service.status())
    service.cancel()


def test_declined_plan_permission_saved_separately_without_switch(tmp_path):
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    active = service.status()['active']
    service.verify = lambda *_: {'sub': 'new-account', 'email': 'test@example.invalid'}
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={**grant(), 'scope': 'openid profile email'})))
    service.start(); assert finish(service, client='oaiapp_other')
    status = service.status()
    assert status['active'] == active and len(status['profiles']) == 2
    assert sum(p['plan_enabled'] for p in status['profiles']) == 1


def test_reauthorization_reuses_registration_host_and_fresh_attempt_values(tmp_path):
    service, _, _, urls = setup(tmp_path)
    service.start(); assert finish(service)
    original = parse_qs(urlsplit(urls[-1]).query)
    profile = service.status()['active']
    service.start(profile)
    new = parse_qs(urlsplit(urls[-1]).query)
    assert new['client_id'] == ['oaiapp_test'] and 'agent_name_hint' not in new
    assert new['ext_agent_host_id'] == original['ext_agent_host_id']
    assert all(new[k] != original[k] for k in ('state', 'nonce', 'code_challenge'))
    pending = service.pending
    assert not service.complete({'state': [pending['state']], 'code': ['c'], 'client_id': ['oaiapp_other']}, pending)
    service.cancel()


def test_cancel_and_expiry_do_not_exchange_or_replace(tmp_path):
    service, _, calls, _ = setup(tmp_path)
    service.start(); old = service.pending; service.cancel()
    assert not service.complete({'state': [old['state']], 'code': ['c']}, old)
    service.start(); service.pending['deadline'] = 0
    assert not finish(service)
    service.cancel()
    assert not calls and not service.status()['available']


@pytest.mark.parametrize('bad', ['signature', 'issuer', 'audience', 'expiry', 'nonce', 'unsigned'])
def test_real_jwt_verification_rejects_invalid_claims(monkeypatch, bad):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt, 'PyJWKClient', lambda *_args, **_kw: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())))
    claims = {'sub': 'account', 'iss': auth.ISSUER, 'aud': 'oaiapp_test', 'exp': time.time() + 3600, 'nonce': 'nonce'}
    assert auth._verify(jwt.encode(claims, private, algorithm='RS256'), 'oaiapp_test', 'nonce')['sub'] == 'account'
    if bad == 'issuer': claims['iss'] = 'https://untrusted.invalid'
    if bad == 'audience': claims['aud'] = 'other'
    if bad == 'expiry': claims['exp'] = time.time() - 120
    if bad == 'nonce': claims['nonce'] = 'other'
    token = jwt.encode(claims, other if bad == 'signature' else private, algorithm='RS256')
    if bad == 'unsigned': token = jwt.encode(claims, None, algorithm='none')
    with pytest.raises(auth.AuthError, match='verification failed'):
        auth._verify(token, 'oaiapp_test', 'nonce')


def test_refresh_serialized_rotating_bundle_and_status_never_reads_tokens(tmp_path):
    service, store, calls, _ = setup(tmp_path)
    service.start(); assert finish(service)
    profile = service.status()['active']; name = service._account(profile)
    store.values[name]['expires_at'] = 0
    def refresh(request):
        assert parse_qs(request.content.decode()) == {'grant_type': ['refresh_token'], 'client_id': ['oaiapp_test'],
            'refresh_token': ['fake-refresh-token'], 'resource': [auth.RESOURCE]}
        return httpx.Response(200, json={**grant(), 'access_token': 'new-access', 'refresh_token': 'new-rotating'})
    service.client = httpx.Client(transport=httpx.MockTransport(refresh))
    other = auth.Service(tmp_path, 'seat', store=store, client=service.client, verify=service.verify)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda s: s.headers(), [service, other]))
    assert results == [{'Authorization': 'Bearer new-access'}] * 2
    assert store.values[name]['refresh_token'] == 'new-rotating'
    assert len(calls) == 1  # authorization only; refresh mock asserts old rotating token once
    reads = store.reads
    service.status(); other.status()
    assert store.reads == reads


def test_refresh_network_failure_preserves_grant_terminal_failure_disables(tmp_path):
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    profile = service.status()['active']; name = service._account(profile)
    store.values[name]['expires_at'] = 0
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503, json={})))
    with pytest.raises(auth.AuthError): service.headers()
    assert service.status()['active'] == profile and name in store.values
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={'error': 'invalid_grant'})))
    with pytest.raises(auth.AuthError): service.headers()
    assert not service.status()['available'] and name not in store.values


def test_disconnect_retains_mapping_reports_unconfirmed_revoke(tmp_path):
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    profile = service.status()['active']
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    result = service.disconnect()
    assert not result['available'] and result['active'] is None and profile in service._meta()['profiles']
    assert 'not confirmed' in result['message'] and not store.values


def test_unavailable_os_store_fails_without_plaintext_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv('EXO_NO_KEYCHAIN', '1')
    monkeypatch.setenv('EXO_NO_SECRET_SERVICE', '1')
    service = auth.Service(tmp_path, 'seat', browser=lambda _: pytest.fail('no browser'))
    with pytest.raises(auth.AuthError, match='OS secret store'):
        service.start()
    assert not list(tmp_path.rglob('*.json'))


def test_same_active_reauthorization_without_plan_preserves_old_bundle(tmp_path):
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    profile = service.status()['active']
    before = copy.deepcopy(store.values)
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={**grant(), 'scope': 'openid profile email', 'access_token': 'not-enabled-new-token'})))
    service.start(profile); assert finish(service)
    assert store.values == before and service.status()['available']
    assert 'unchanged' in service.status()['message']


def test_issued_registration_survives_failed_exchange_without_identity_claim(tmp_path):
    service, _, _, urls = setup(tmp_path)
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(400, json={'error': 'invalid_grant'})))
    service.start(); assert not finish(service)
    assert not service.status()['available'] and not service.status()['profiles']
    service.start()
    params = parse_qs(urlsplit(urls[-1]).query)
    assert params['client_id'] == ['oaiapp_test'] and 'agent_name_hint' not in params
    service.cancel()


def test_service_lookup_does_not_create_extra_clients(monkeypatch, tmp_path):
    created = []
    monkeypatch.setattr(auth, '_services', {})
    monkeypatch.setattr(auth, 'Service', lambda *args: created.append(args) or object())
    assert auth.service(tmp_path, 'seat') is auth.service(tmp_path, 'seat')
    assert len(created) == 1


def test_revocation_uses_fixed_discovery_and_rejects_off_origin_endpoint(tmp_path):
    service, _, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    seen = []
    def remote(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={'issuer': auth.ISSUER, 'revocation_endpoint': 'https://untrusted.invalid/steal'})
    service.client = httpx.Client(transport=httpx.MockTransport(remote))
    assert 'not confirmed' in service.disconnect()['message']
    assert seen == [auth.ISSUER + '/.well-known/openid-configuration']


def test_status_does_not_wait_for_network_mutex(tmp_path):
    service, _, _, _ = setup(tmp_path)
    held, release = threading.Event(), threading.Event()
    def hold():
        with service.mutex:
            held.set(); release.wait(5)
    worker = threading.Thread(target=hold)
    worker.start(); assert held.wait(1)
    try:
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(service.status).result(timeout=1)['available'] is False
    finally:
        release.set(); worker.join(1)


def test_principal_boundary_refuses_before_secret_service_access(monkeypatch, tmp_path):
    import asyncio
    from exo import diagnostics, principal
    from exo.protocol import schema as s
    from exo.server.engine import CommandFailed
    monkeypatch.setattr(auth, 'service', lambda *_: pytest.fail('no secret service access'))
    for caller in ('local', 'channel', 'unknown'):
        token = principal.CALLER.set(principal.Caller(caller))
        try:
            with pytest.raises(CommandFailed) as error:
                asyncio.run(diagnostics.builtin_command(SimpleNamespace(home=tmp_path, agent='seat'),
                    s.CommandRun(name='chatgpt', args='{"action":"start"}')))
            assert error.value.code == 'forbidden'
        finally:
            principal.CALLER.reset(token)


def test_refresh_is_serialized_between_processes(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    name = service._account(service.status()['active'])
    bundle = store.values[name]; bundle['expires_at'] = 0
    (tmp_path / 'fake-store.json').write_text(json.dumps(bundle))
    script = r'''
import json, pathlib, sys, httpx
from exo.chatgpt_auth import Service
home=pathlib.Path(sys.argv[1])
class Store:
    backend='fake-os-store'
    def get(self, _): return json.loads((home/'fake-store.json').read_text())
    def set(self, _, bundle): (home/'fake-store.json').write_text(json.dumps(bundle))
def response(request):
    with (home/'refresh-count').open('a') as f: f.write('refresh\n')
    return httpx.Response(200,json={'access_token':'next-access','refresh_token':'next-refresh',
      'expires_in':3600,'token_type':'Bearer'})
service=Service(home,'seat',store=Store(),client=httpx.Client(transport=httpx.MockTransport(response)))
assert service.headers()=={'Authorization':'Bearer next-access'}
print('ok')
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(auth.__file__).parents[1])}
    processes = [subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and stdout.strip() == 'ok', stderr
    assert (tmp_path / 'refresh-count').read_text().splitlines() == ['refresh']


def test_encoded_os_store_uses_native_keychain_and_remembers_decoded_tokens(monkeypatch):
    import base64
    from exo import macos_keychain
    from exo.core.models import keys
    calls = []
    store = auth.SecretStore(); store.backend = 'keychain'
    monkeypatch.setattr(store, 'available', lambda: True)
    monkeypatch.setattr(macos_keychain, 'set_generic_password', lambda service, name, value: calls.append((service, name, value)))
    bundle = {'access_token': 'fake-access', 'refresh_token': 'fake-refresh', 'id_token': 'fake-id'}
    store.set('exo-chatgpt-fake', bundle)
    service, name, encoded = calls[0]
    assert service == keys.KEYCHAIN_SERVICE and name == 'exo-chatgpt-fake'
    assert json.loads(base64.b64decode(encoded)) == bundle
    monkeypatch.setattr(macos_keychain, 'get_generic_password', lambda *_: encoded)
    remembered = []
    monkeypatch.setattr(keys, 'remember_secret', remembered.append)
    assert store.get('exo-chatgpt-fake') == bundle and set(remembered) == set(bundle.values())


def test_unsafe_metadata_and_lock_symlinks_refused(tmp_path):
    service, _, _, _ = setup(tmp_path)
    service.folder.mkdir()
    outside = tmp_path / 'outside'; outside.write_text('unchanged')
    service.path.symlink_to(outside)
    with pytest.raises(auth.AuthError): service.status()
    service.path.unlink()
    (service.folder / (service.scope + '.lock')).symlink_to(outside)
    with pytest.raises(OSError): service.start()
    assert outside.read_text() == 'unchanged'


def test_refresh_scope_loss_disables_inference_and_metadata(tmp_path):
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    profile = service.status()['active']; name = service._account(profile)
    store.values[name]['expires_at'] = 0
    service.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={**grant(), 'scope': 'openid chatgpt.tokens.use.direct'})))
    with pytest.raises(auth.AuthError, match='no longer enabled'):
        service.headers()
    assert not service.status()['available'] and service.status()['active'] is None
    assert not service._meta()['profiles'][profile]['plan_enabled']


def test_cancel_during_exchange_cannot_activate_new_grant(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def held(_):
        entered.set(); release.wait(5)
        return httpx.Response(200, json=grant())
    service, store, _, _ = setup(tmp_path, handler=held)
    service.start()
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(finish, service)
        assert entered.wait(1)
        assert not service.cancel()['pending']
        release.set()
        assert not pending.result(2)
    assert not service.status()['available'] and not store.values


def test_missing_optional_extra_refuses_before_browser_or_metadata(monkeypatch, tmp_path):
    service, _, calls, urls = setup(tmp_path)
    def missing():
        raise auth.AuthError("Install Exo's chatgpt extra to enable ChatGPT sign-in.")
    monkeypatch.setattr(auth, '_jwt', missing)
    with pytest.raises(auth.AuthError, match='chatgpt extra'):
        service.start()
    assert not urls and not calls and not service.path.exists()
    assert not service.status()['dependency_available'] and not service.status()['available']


def test_successful_reauthorization_bumps_scope_but_refresh_does_not(tmp_path, monkeypatch):
    service, store, _, _ = setup(tmp_path)
    monkeypatch.setattr(auth, 'service', lambda *_: service)
    service.start(); assert finish(service)
    scope = auth.continuation_scope(tmp_path, 'seat')
    profile = service.status()['active']
    service.start(profile); assert finish(service)
    assert scope != auth.continuation_scope(tmp_path, 'seat')
    scope = auth.continuation_scope(tmp_path, 'seat')
    store.values[service._account(profile)]['expires_at'] = 0
    service.headers()
    assert scope == auth.continuation_scope(tmp_path, 'seat')


def test_registration_accepts_issued_client_without_example_prefix(tmp_path):
    validated = []
    def verify(token, audience, nonce):
        validated.append((audience, nonce))
        return {'sub': 'account-one', 'email': 'test@example.invalid'}
    service, _, calls, _ = setup(tmp_path, verify=verify)
    service.start()
    nonce = service.pending['nonce']
    assert finish(service, client='issued-exo-client:public.123')
    assert validated == [('issued-exo-client:public.123', nonce)]
    assert parse_qs(calls[0].content.decode())['client_id'] == ['issued-exo-client:public.123']
    assert service.status()['profiles'][0]['client_id'] == 'issued-exo-client:public.123'


@pytest.mark.parametrize('client', ['dynamic_agent_client', '', 'x' * 257, 'client with space', 'client\tvalue', 'client\nvalue', 'client\x7fvalue'])
def test_invalid_issued_identifier_refused_before_code_exchange(tmp_path, client):
    service, store, calls, _ = setup(tmp_path)
    service.start()
    assert not finish(service, client=client)
    assert not calls and not store.values and not service.status()['available']


def test_large_oauth_bundle_bypasses_interactive_security_line_limit(monkeypatch):
    from exo import macos_keychain, providers_setup as ps
    store = auth.SecretStore(); store.backend = 'keychain'
    monkeypatch.setattr(store, 'available', lambda: True)
    saved = []
    def limited_cli(argv, stdin=None):
        # Apple's interactive parser reads at most 4095 bytes per line, irrespective of item capacity.
        return SimpleNamespace(returncode=1 if len((stdin or '').encode()) >= 4096 else 0,
                               stdout='', stderr='synthetic interactive line too long')
    monkeypatch.setattr(ps, '_run', limited_cli)
    monkeypatch.setattr(macos_keychain, 'set_generic_password', lambda service, name, encoded: saved.append(encoded))
    bundle = {'access_token': 'synthetic-access-' + 'a' * 6000, 'refresh_token': 'synthetic-refresh-' + 'r' * 1000,
              'id_token': 'synthetic-identity-' + 'i' * 6000}
    store.set('exo-chatgpt-large-fixture', bundle)
    assert len(saved) == 1 and len(saved[0]) > 4096
    import base64
    assert json.loads(base64.b64decode(saved[0])) == bundle


def test_callback_store_failure_page_is_actionable_and_does_not_echo_exception(tmp_path, monkeypatch):
    from exo import macos_keychain
    service, _, _, _ = setup(tmp_path)
    service.store = auth.SecretStore(); service.store.backend = 'keychain'
    monkeypatch.setattr(service.store, 'available', lambda: True)
    def fail(*_):
        raise RuntimeError('fake-token')
    monkeypatch.setattr(macos_keychain, 'set_generic_password', fail)
    service.start()
    pending = service.pending
    with httpx.Client(trust_env=False) as client:
        result = client.get(pending['redirect'], params={'state': pending['state'], 'code': 'synthetic-code',
                                                        'client_id': 'issued-fixture-client'})
    assert result.status_code == 400
    # A store implementation's raw errors must not become callback text.
    assert 'fake-token' not in result.text and 'synthetic-code' not in result.text
    assert 'Return to Exo Providers' in result.text and 'Unlock the OS secret store' in result.text
    assert 'previous connection is unchanged' in result.text and not service.status()['available']


def test_legacy_keychain_registration_requires_reauth_and_new_namespace_preserves_old(tmp_path):
    service, store, _, _ = setup(tmp_path)
    service.start(); assert finish(service)
    profile = service.status()['active']
    legacy_account = service._account(profile)
    old_bundle = copy.deepcopy(store.values[legacy_account])
    meta = service._meta()
    meta['profiles'][profile].pop('storage')
    meta['profiles'][profile]['store_backend'] = 'keychain'
    service._save_meta(meta)
    store.backend = 'keychain'
    assert not service.status()['available'] and 'Reconnect' in service.status()['message']
    with pytest.raises(auth.AuthError, match='Reconnect'):
        service.headers()
    store.fail = True
    service.start(profile); assert not finish(service)
    assert store.values[legacy_account] == old_bundle and service._meta() == meta
    store.fail = False
    service.start(profile); assert finish(service)
    new_account = service._account(profile)
    assert new_account != legacy_account and new_account.startswith('exo-chatgpt-native-v1-')
    assert store.values[legacy_account] == old_bundle
    assert service._meta()['profiles'][profile]['store_backend'] == 'keychain'
    assert service.headers()['Authorization'].startswith('Bearer ')


def test_linux_namespace_and_backend_mismatch_are_explicit(tmp_path):
    service, store, _, _ = setup(tmp_path)
    store.backend = 'secret-service'
    service.start(); assert finish(service)
    profile = service.status()['active']
    assert service._account(profile).startswith('exo-chatgpt-')
    assert not service._account(profile).startswith('exo-chatgpt-native-v1-')
    meta = service._meta(); meta['profiles'][profile]['store_backend'] = 'keychain'
    meta['profiles'][profile]['storage'] = 'native-v1'
    service._save_meta(meta)
    assert not service.status()['available']
    with pytest.raises(auth.AuthError, match='another OS secret store'):
        service.headers()
    # Explicitly choosing the new Linux storage ignores the previous native namespace marker.
    assert service._account(profile, native=False).startswith('exo-chatgpt-' + service.scope)
