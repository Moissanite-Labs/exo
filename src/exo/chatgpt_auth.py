"""OpenAI Sign in with ChatGPT: separate Exo registrations, never borrowed CLI credentials.

Only metadata crosses the principal command boundary. Tokens stay in the OS secret store.
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

import httpx

from exo.core.models import keys
from exo.core.private import atomic_write, private_dir

ISSUER = 'https://auth.openai.com'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
JWKS = ISSUER + '/.well-known/jwks.json'
REVOKE = ISSUER + '/api/accounts/oauth/revoke'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
USAGE = 'https://chatgpt.com/settings/usage'
_TERMINAL = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
             'refresh_token_invalidated', 'refresh_token_reused'}


class AuthError(Exception):
    """Fixed safe messages only; never include upstream response bodies or credential values."""


class Reauthorize(AuthError):
    """An unusable rotating grant, requiring a fresh user sign-in."""


class SecretStore:
    """OS-backed only. Base64 encodes token JSON for the existing stdin-only Keychain setter."""
    def __init__(self):
        self.backend = 'keychain' if sys.platform == 'darwin' else 'secret-service'

    def available(self):
        if self.backend == 'keychain':
            return not os.environ.get('EXO_NO_KEYCHAIN') and bool(shutil.which('security'))
        return not os.environ.get('EXO_NO_SECRET_SERVICE') and bool(shutil.which('secret-tool'))

    def _check(self):
        if not self.available():
            raise AuthError('ChatGPT connection needs an available OS secret store.')

    def get(self, name):
        self._check()
        value = keys.keychain_get(name) if self.backend == 'keychain' else keys.secret_service_get(name)
        if not value:
            raise AuthError('ChatGPT credentials are unavailable; sign in again or unlock the secret store.')
        try:
            bundle = json.loads(base64.b64decode(value, validate=True))
            for field in ("access_token", "refresh_token", "id_token"):
                if bundle.get(field):
                    keys.remember_secret(bundle[field])
            return bundle
        except Exception:
            raise AuthError('ChatGPT credentials could not be read.') from None

    def set(self, name, value):
        from exo import providers_setup as ps
        self._check()
        encoded = base64.b64encode(json.dumps(value).encode()).decode()
        try:
            (ps._keychain_set if self.backend == 'keychain' else ps._secret_tool_set)(name, encoded)
        except Exception:
            raise AuthError('ChatGPT credentials could not be saved in the OS secret store.') from None

    def delete(self, name):
        from exo import providers_setup as ps
        self._check()
        try:
            if self.backend == 'keychain':
                out = ps._run(['security', 'delete-generic-password', '-s', keys.KEYCHAIN_SERVICE, '-a', name])
                return out.returncode in (0, 44)
            return ps._secret_tool_delete(name)
        except Exception:
            return False


def _jwt():
    try:
        module = importlib.import_module('jwt')
        if not module.algorithms.has_crypto:
            raise ImportError()
        return module
    except ImportError:
        raise AuthError("Install Exo's chatgpt extra to enable ChatGPT sign-in.") from None


def _dependencies_available():
    try:
        _jwt()
        return True
    except AuthError:
        return False


def _verify(encoded, client_id, nonce=None):
    try:
        jwt = _jwt()
        signing = jwt.PyJWKClient(JWKS, timeout=10).get_signing_key_from_jwt(encoded)
        claims = jwt.decode(encoded, signing.key, algorithms=['RS256'], audience=client_id,
                            issuer=ISSUER, leeway=30, options={'require': ['exp', 'iss', 'aud', 'sub']})
        if not isinstance(claims['sub'], str) or not claims['sub']:
            raise ValueError()
        if claims.get('azp', client_id) != client_id or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and claims.get('azp') != client_id):
            raise ValueError()
        if nonce is not None and not hmac.compare_digest(str(claims.get('nonce', '')), nonce):
            raise ValueError()
        return claims
    except Exception:
        raise AuthError('ChatGPT identity verification failed. Start a new sign-in.') from None


class Service:
    def __init__(self, home, seat, *, store=None, client=None, verify=None, browser=None):
        self.home = Path(home).expanduser().resolve()
        self.seat = seat
        self.scope = hashlib.sha256((str(self.home) + '\0' + seat).encode()).hexdigest()
        self.folder = self.home / 'chatgpt'
        self.path = self.folder / (self.scope + '.json')
        self.store = store or SecretStore()
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self.verify = verify or _verify
        self.browser = browser or webbrowser.open
        self.mutex = threading.RLock()
        self.pending = None
        self.message = ''

    def _meta(self):
        if self.path.is_symlink():
            raise AuthError('ChatGPT metadata path is unsafe.')
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {'profiles': {}, 'active': None}

    def _save_meta(self, meta):
        atomic_write(self.path, json.dumps(meta) + '\n')

    def _account(self, profile):
        return 'exo-chatgpt-' + self.scope + '-' + profile

    def _locked(self):
        # Held with the in-process mutex; flock also serializes rotating refreshes across cores/processes.
        private_dir(self.folder)
        self.folder.chmod(0o700)
        path = self.folder / (self.scope + '.lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return os.fdopen(fd, 'r+')

    def status(self):
        # Atomic metadata reads never wait on a refresh or unlock/read the secret store.
        meta = self._meta()
        active = meta.get('active')
        profiles = [dict({field: v.get(field) for field in ('client_id', 'subject', 'email', 'plan_enabled')},
                         profile=k, active=(k == active)) for k, v in meta['profiles'].items()]
        return {'supported': True, 'available': bool(_dependencies_available() and active and meta['profiles'].get(active, {}).get('plan_enabled')),
                'active': active, 'profiles': profiles, 'pending': self.pending is not None, 'message': self.message,
                'store_available': bool(self.store.available()), 'dependency_available': _dependencies_available(), 'usage_url': USAGE}

    def _host(self):
        path = self.folder / 'host.json'
        # The host ID is shared by seats in this Exo home, not derived from account/email.
        fd = os.open(self.folder / 'host.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+'):
            fcntl.flock(fd, fcntl.LOCK_EX)
            if path.is_symlink():
                raise AuthError('ChatGPT host metadata path is unsafe.')
            if path.exists():
                return json.loads(path.read_text())['id']
            value = 'urn:uuid:' + str(uuid.uuid4())
            atomic_write(path, json.dumps({'id': value}))
            return value

    def start(self, profile=None):
        _jwt()
        with self.mutex, self._locked():
            if not self.store.available():
                raise AuthError('ChatGPT connection needs an available OS secret store.')
            self._cancel()
            meta = self._meta()
            previous = meta['profiles'].get(profile) if profile else meta.get('incomplete')
            if profile and previous is None:
                raise AuthError('Choose a saved ChatGPT registration or add a new one.')
            verifier = secrets.token_urlsafe(64)
            pending = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
                       'verifier': verifier, 'client_id': previous['client_id'] if previous else 'dynamic_agent_client',
                       'profile': profile, 'previous': previous, 'deadline': time.monotonic() + 300}
            service = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass  # callback queries contain one-use codes

                def do_GET(self):
                    parsed = urlsplit(self.path)
                    if parsed.path != '/auth/callback' or self.headers.get('Host') != urlsplit(pending['redirect']).netloc:
                        self.send_error(404)
                        return
                    ok = service.complete(parse_qs(parsed.query, max_num_fields=16), pending)
                    page = b'ChatGPT sign-in completed. Return to Exo.' if ok else b'ChatGPT sign-in was not completed. Return to Exo.'
                    self.send_response(200 if ok else 400)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)

            class Listener(HTTPServer):
                def get_request(self):
                    sock, address = super().get_request()
                    sock.settimeout(3)
                    return sock, address

            server = Listener(('127.0.0.1', 0), Callback)
            server.timeout = 0.25
            pending['server'] = server
            pending['redirect'] = f'http://127.0.0.1:{server.server_port}/auth/callback'
            self.pending, self.message = pending, 'Waiting for ChatGPT sign-in.'
            params = {'client_id': pending['client_id'], 'ext_agent_host_id': self._host(),
                      'response_type': 'code', 'redirect_uri': pending['redirect'], 'scope': SCOPES,
                      'resource': RESOURCE, 'state': pending['state'], 'nonce': pending['nonce'],
                      'code_challenge_method': 'S256',
                      'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
            if previous:
                # Do not place id_token_hint in a frontend reply or browser history outside OpenAI.
                params['login_hint'] = previous.get('email', '')
            else:
                params['agent_name_hint'] = 'Exo'

            def listen():
                try:
                    while self.pending is pending and time.monotonic() < pending['deadline']:
                        server.handle_request()
                finally:
                    server.server_close()
                    with self.mutex:
                        if self.pending is pending:
                            self.pending = None
                            self.message = 'ChatGPT sign-in expired. Try again.'

            threading.Thread(target=listen, daemon=True).start()
            try:
                opened = self.browser(AUTHORIZE + '?' + urlencode(params))
            except Exception:
                opened = False
            if not opened:
                self._cancel()
                raise AuthError('Could not open the system browser. Try again.')
            return self.status()

    def _cancel(self):
        self.pending = None

    def cancel(self):
        # Revocation of the pending attempt must not wait behind a slow code exchange.
        self._cancel()
        self.message = 'ChatGPT sign-in cancelled.'
        return self.status()

    def _exchange(self, data):
        try:
            response = self.client.post(TOKEN, data=data)
            payload = response.json()
            if response.status_code != 200:
                if payload.get('error') in _TERMINAL:
                    raise Reauthorize('ChatGPT sign-in is no longer valid. Sign in again.')
                raise AuthError('ChatGPT authorization is temporarily unavailable. Try again later.')
            return payload
        except AuthError:
            raise
        except Exception:
            raise AuthError('ChatGPT authorization is temporarily unavailable. Try again later.') from None

    def _bundle(self, response, client_id, nonce=None, previous=None):
        claims = self.verify(response['id_token'], client_id, nonce) if response.get('id_token') else None
        if nonce is not None and not claims:
            raise AuthError('ChatGPT identity verification failed. Start a new sign-in.')
        if previous and previous.get('subject') and claims and claims['sub'] != previous['subject']:
            raise AuthError('ChatGPT account did not match the selected registration.')
        scopes = response.get('scope')
        scopes = scopes.split() if isinstance(scopes, str) else (previous or {}).get('scopes', [])
        access = response.get('access_token')
        refresh = response.get('refresh_token') or (previous or {}).get('refresh_token')
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or response.get('token_type', '').lower() != 'bearer':
            raise AuthError('ChatGPT returned an incomplete token grant.')
        for value in (access, refresh, response.get('id_token')):
            if value:
                keys.remember_secret(value)
        return {'client_id': client_id, 'subject': claims['sub'] if claims else previous['subject'],
                'email': claims.get('email', '') if claims else previous.get('email', ''), 'scopes': scopes,
                'id_token': response.get('id_token') or (previous or {}).get('id_token'),
                'access_token': access, 'refresh_token': refresh, 'expires_at': time.time() + int(response['expires_in']),
                'earliest_refresh_at': response.get('earliest_refresh_at', 0)}

    def complete(self, query, pending):
        with self.mutex, self._locked():
            if self.pending is not pending or pending.get('consumed') or time.monotonic() >= pending['deadline']:
                return False
            states = query.get('state', [])
            if len(states) != 1 or not hmac.compare_digest(states[0], pending['state']):
                return False
            pending['consumed'] = True  # consume once, including errors
            try:
                if query.get('error'):
                    raise AuthError('ChatGPT sign-in was declined. The previous connection is unchanged.')
                codes, issued = query.get('code', []), query.get('client_id', [])
                client_id = pending['client_id']
                if client_id == 'dynamic_agent_client':
                    if (len(issued) != 1 or not 1 <= len(issued[0]) <= 256
                            or issued[0] == 'dynamic_agent_client'
                            or any(not 33 <= ord(char) <= 126 for char in issued[0])):
                        raise AuthError('ChatGPT registration was incomplete. Try again.')
                    client_id = issued[0]
                elif issued and issued != [client_id]:
                    raise AuthError('ChatGPT registration did not match. Try again.')
                if len(codes) != 1 or not codes[0]:
                    raise AuthError('ChatGPT sign-in was incomplete. Try again.')
                if pending['client_id'] == 'dynamic_agent_client':
                    meta = self._meta()
                    meta['incomplete'] = {'client_id': client_id}
                    self._save_meta(meta)
                bundle = self._bundle(self._exchange({'grant_type': 'authorization_code', 'client_id': client_id,
                    'code': codes[0], 'code_verifier': pending['verifier'], 'redirect_uri': pending['redirect'],
                    'resource': RESOURCE}), client_id, pending['nonce'], pending['previous'])
                if self.pending is not pending:
                    return False  # cancellation during exchange cannot activate a grant
                meta = self._meta()
                profile = pending['profile'] or hashlib.sha256((client_id + '\0' + bundle['subject']).encode()).hexdigest()[:24]
                plan_enabled = all(scope in bundle['scopes'] for scope in ('resource.invoke', 'chatgpt.tokens.use.direct'))
                if meta.get('active') == profile and not plan_enabled:
                    self.message = 'New sign-in did not enable ChatGPT plan usage. The previous connection is unchanged.'
                    return True
                self.store.set(self._account(profile), bundle)
                if meta.get('incomplete', {}).get('client_id') == client_id:
                    meta.pop('incomplete', None)
                meta['profiles'][profile] = {k: bundle[k] for k in ('client_id', 'subject', 'email')}
                meta['profiles'][profile]['plan_enabled'] = plan_enabled
                meta['profiles'][profile]['generation'] = uuid.uuid4().hex
                # Valid identity without plan consent remains saved, but does not replace a usable active account.
                if meta['profiles'][profile]['plan_enabled']:
                    meta['active'] = profile
                self._save_meta(meta)
                self.message = 'ChatGPT connected.' if meta['profiles'][profile]['plan_enabled'] else 'Signed in; ChatGPT plan usage is not enabled.'
                return True
            except Exception as error:
                if self.pending is pending:
                    self.message = str(error) if isinstance(error, AuthError) else 'ChatGPT sign-in could not be completed.'
                return False
            finally:
                if self.pending is pending:
                    self.pending = None

    def headers(self):
        with self.mutex, self._locked():
            meta = self._meta()
            profile = meta.get('active')
            if not profile:
                raise AuthError('Connect a ChatGPT account in Providers first.')
            account = self._account(profile)
            bundle = self.store.get(account)
            if not all(scope in bundle['scopes'] for scope in ('resource.invoke', 'chatgpt.tokens.use.direct')):
                raise AuthError('ChatGPT plan usage is not enabled for this connection.')
            if bundle['expires_at'] <= time.time() + 60:
                if float(bundle.get('earliest_refresh_at') or 0) > time.time():
                    if bundle['expires_at'] <= time.time():
                        raise AuthError('ChatGPT authorization cannot be refreshed yet. Try again later.')
                    return {'Authorization': 'Bearer ' + bundle['access_token']}
                try:
                    response = self._exchange({'grant_type': 'refresh_token', 'client_id': bundle['client_id'],
                                              'refresh_token': bundle['refresh_token'], 'resource': RESOURCE})
                except Reauthorize:
                    meta['active'] = None
                    self._save_meta(meta)
                    self.store.delete(account)
                    raise
                bundle = self._bundle(response, bundle['client_id'], previous=bundle)
                self.store.set(account, bundle)  # rotating token set persisted together before use
                if not all(scope in bundle['scopes'] for scope in ('resource.invoke', 'chatgpt.tokens.use.direct')):
                    meta['active'] = None
                    meta['profiles'][profile]['plan_enabled'] = False
                    self._save_meta(meta)
                    raise AuthError('ChatGPT plan usage is no longer enabled. Sign in again.')
            keys.remember_secret(bundle['access_token'])
            return {'Authorization': 'Bearer ' + bundle['access_token']}

    def disconnect(self, profile=None):
        with self.mutex, self._locked():
            self._cancel()
            meta = self._meta()
            profile = profile or meta.get('active')
            if not profile or profile not in meta['profiles']:
                return self.status()
            # Disable locally before remote work; a failure cannot leave requests using the grant.
            if meta.get('active') == profile:
                meta['active'] = None
            self._save_meta(meta)
            revoked = False
            try:
                bundle = self.store.get(self._account(profile))
                discovery = self.client.get(ISSUER + '/.well-known/openid-configuration').json()
                endpoint = discovery['revocation_endpoint']
                parsed = urlsplit(endpoint)
                if discovery.get('issuer') != ISSUER or parsed.scheme != 'https' or parsed.netloc != 'auth.openai.com' or parsed.query or parsed.fragment:
                    raise AuthError('ChatGPT revocation endpoint could not be verified.')
                response = self.client.post(endpoint, data={'token': bundle['refresh_token'],
                    'token_type_hint': 'refresh_token', 'client_id': bundle['client_id']})
                revoked = response.status_code == 200
            except Exception:
                pass
            deleted = self.store.delete(self._account(profile))
            self.message = ('ChatGPT disconnected.' if deleted else 'ChatGPT disabled; stored credential cleanup is not confirmed.')
            if not revoked:
                self.message += ' Remote revocation was not confirmed; manage access in ChatGPT Settings.'
            return self.status()


_services = {}
_services_lock = threading.Lock()


def service(home, seat):
    key = (str(Path(home).expanduser().resolve()), seat)
    with _services_lock:
        if key not in _services:
            _services[key] = Service(*key)
        return _services[key]


def status(home, seat):
    return service(home, seat).status()


async def request_headers(home, seat):
    try:
        return await asyncio.to_thread(service(home, seat).headers)
    except Exception:
        raise AuthError('ChatGPT authorization is unavailable. Check the connection in Providers.') from None


def continuation_scope(home, seat):
    """Nonsecret registration identity for model continuation isolation; stable across token refresh."""
    auth = service(home, seat)
    meta = auth._meta()
    active = meta.get('active')
    generation = meta['profiles'].get(active, {}).get('generation', '')
    return hashlib.sha256((auth.scope + ':' + active + ':' + generation).encode()).hexdigest() if active else ''
