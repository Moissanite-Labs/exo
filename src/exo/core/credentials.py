"""Principal-owned, write-only credential setup and explicitly bound shell consumers.

The model sees only the catalog of allowed variable names. Secret values go from
Keychain to a selected child environment, never through a turn or event payload.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Iterable

from exo import providers_setup as ps
from exo.core import github_auth as github
from exo.core.models import keys
from exo.core.private import atomic_write, private_dir
from exo.envscrub import scrub_env
from exo.principal import require_principal
from exo.server.engine import CommandFailed

CAPABILITY = 'secure-seat-credentials-v1'
CONSUMERS = ('bash', 'jobs')
ENV = re.compile(r'EXO_SECRET_[A-Z][A-Z0-9_]{0,47}')
NAME = re.compile(r'[a-z][a-z0-9_-]{0,47}')


class CredentialError(ValueError):
    pass


def _scope(home, seat):
    return github._identity(home, seat)[:2]


def _path(home, seat):
    home, seat = _scope(home, seat)
    return home / 'state' / 'credentials' / (seat + '.json')


def account(home, seat, name):
    home, seat = _scope(home, seat)
    digest = hashlib.sha256(f'{home}\0{seat}\0{name}'.encode()).hexdigest().upper()
    return 'EXO_CREDENTIAL_' + digest + '_TOKEN'


def store_available():
    return sys.platform == 'darwin' and bool(shutil.which('security')) and not os.environ.get('EXO_NO_KEYCHAIN')


def _secure_store():
    if not store_available():
        raise CredentialError('macOS Keychain is unavailable; no plaintext fallback is permitted')


def _record(name, env, consumers):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise CredentialError('Use a short lowercase service name')
    if not isinstance(env, str) or not ENV.fullmatch(env):
        raise CredentialError('Use an EXO_SECRET_ variable name')
    if not isinstance(consumers, list) or not consumers or any(c not in CONSUMERS for c in consumers):
        raise CredentialError('Select bash, jobs, or both as the allowed consumer')
    return {'name': name, 'env': env, 'consumers': sorted(set(consumers)), 'enabled': True, 'store': 'keychain'}


def _load(home, seat):
    try:
        data = json.loads(_path(home, seat).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise CredentialError('Credential metadata is unavailable') from None
    if not isinstance(data, dict):
        raise CredentialError('Credential metadata is invalid')
    out = {}
    for name, row in data.items():
        if not isinstance(row, dict) or row.get('store') != 'keychain' or not isinstance(row.get('enabled'), bool):
            raise CredentialError('Credential metadata is invalid')
        checked = _record(name, row.get('env'), row.get('consumers'))
        checked.update(enabled=row['enabled'], cleanup_pending=bool(row.get('cleanup_pending')))
        out[name] = checked
    return out


def _save(home, seat, data):
    path = _path(home, seat)
    private_dir(path.parent)
    atomic_write(path, json.dumps(data, sort_keys=True) + '\n')


def _denied(name, deny):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in deny)


def metadata(home, seat, deny=()):
    home, seat = _scope(home, seat)
    rows = []
    for row in _load(home, seat).values():
        rows.append({**row, 'configured': row['enabled'], 'status': 'not checked' if row['enabled'] else 'disabled',
                     'policy_blocked': _denied(row['env'], deny)})
    meta = github.binding(home, seat)
    gh = {'mode': meta.get('mode', 'legacy-host'), 'host': meta.get('host', 'github.com'),
          'store': meta.get('store'), 'configured': meta.get('mode') == 'token',
          'secure': meta.get('mode') == 'token' and meta.get('store') == 'keychain', 'status': 'not checked'}
    return {'capability': CAPABILITY, 'seat': seat,
            'home_id': hashlib.sha256(str(home).encode()).hexdigest()[:12],
            'store': 'keychain', 'store_available': bool(store_available()),
            'github': gh, 'credentials': sorted(rows, key=lambda row: row['name']),
            'consumers': list(CONSUMERS), 'variable_prefix': 'EXO_SECRET_'}


def _github_test(home, seat, deny):
    meta = github.binding(home, seat)
    if meta.get('mode') != 'token' or meta.get('store') != 'keychain':
        raise CredentialError('Configure a Keychain GitHub token before testing')
    env = github.child_env(scrub_env(deny=deny), home, seat, deny=deny)
    proc = subprocess.run(['gh', 'api', 'user', '--hostname', meta['host'], '--jq', '.login'],
                          env=env, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    login = proc.stdout.strip()
    # Never return arbitrary process output, including errors or a token echoed by a fake binary.
    if proc.returncode or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', login) or keys.redact_secrets(login) != login:
        return {'ok': False, 'error': 'GitHub authentication failed; check the token and its permissions'}
    return {'ok': True, 'login': login, 'host': meta['host']}


def _delete(name):
    if not store_available():
        return False
    try:
        result = ps._run(['security', 'delete-generic-password', '-s', keys.KEYCHAIN_SERVICE, '-a', name])
        return result.returncode in (0, 44)  # item-not-found is already removed
    except (OSError, ps.SetupError, subprocess.SubprocessError):
        return False


def handle(home, seat, body, deny=()):
    """Synchronous store work. The caller has already proved principal authority."""
    if not isinstance(body, dict):
        raise CredentialError('Invalid credential request')
    action = body.get('action')
    if action == 'list':
        return metadata(home, seat, deny)
    value = body.pop('value', None)
    if isinstance(value, str):
        keys.remember_secret(value)
    if action == 'github_remove':
        return github.remove(home, seat)
    if action not in ('remove',):
        _secure_store()
    if action == 'github_set':
        host = body.get('host', 'github.com')
        github.configure(home, seat, 'token', host, ps.check_key(value or ''), 'keychain')
        return {'ok': True, 'store': 'keychain', 'seat': seat}
    if action == 'github_test':
        return _github_test(home, seat, deny)
    name = body.get('name')
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise CredentialError('Use a short lowercase service name')
    path = _path(home, seat)
    with ps._keys_file_lock(path):
        rows = _load(home, seat)
        if action == 'set':
            row = _record(name, body.get('env'), body.get('consumers'))
            if _denied(row['env'], deny):
                raise CredentialError('This variable is denied by the seat environment policy')
            if any(other != name and r['enabled'] and r['env'] == row['env']
                   and set(r['consumers']) & set(row['consumers']) for other, r in rows.items()):
                raise CredentialError('That variable is already bound to another service for this consumer')
            ps._keychain_set(account(home, seat, name), ps.check_key(value or ''))
            rows[name] = row
            _save(home, seat, rows)
            return {'ok': True, 'store': 'keychain', 'name': name}
        row = rows.get(name)
        if row is None:
            raise CredentialError('The credential binding was not found')
        if action == 'test':
            # This checks local storage only, never an arbitrary network target.
            available = bool(keys.keychain_get(account(home, seat, name))) if row['enabled'] else False
            return {'ok': available, 'storage_available': available, 'remote_authentication_tested': False}
        if action == 'remove':
            row['enabled'] = False
            row['cleanup_pending'] = True
            _save(home, seat, rows)  # revocation applies even if Keychain deletion fails
            removed = _delete(account(home, seat, name))
            row['cleanup_pending'] = not removed
            _save(home, seat, rows)
            return {'ok': removed, 'disabled': True, 'deleted': removed, 'cleanup_pending': not removed}
    raise CredentialError('Unknown credential action')


async def command(home, seat, raw, deny=()):
    require_principal('manage credentials')  # before parsing or touching any secret store
    body = None
    try:
        if not isinstance(raw, str) or len(raw) > 8192:
            raise CredentialError('Invalid credential request')
        body = json.loads(raw)
        return await asyncio.to_thread(handle, home, seat, body, deny)
    except Exception:
        # Dispatcher otherwise includes arbitrary exception text. Never let a store,
        # parser or subprocess echo a secret into a response or harness error.
        raise CommandFailed('credential_error', 'Credential operation failed; check settings, policy and Keychain availability') from None
    finally:
        if isinstance(body, dict):
            body.clear()


def child_env(env, home, seat, consumer, deny: Iterable[str] = ()):
    # This namespace is owned by explicit bindings. Never inherit a host value,
    # including when a binding was removed or excludes this consumer.
    out = {name: value for name, value in env.items() if not name.startswith('EXO_SECRET_')}
    if consumer not in CONSUMERS:
        return out
    for row in _load(home, seat).values():
        if not row['enabled'] or consumer not in row['consumers']:
            continue
        if _denied(row['env'], deny):
            raise CredentialError('Credential injection is denied by the seat environment policy')
        _secure_store()
        value = keys.keychain_get(account(home, seat, row['name']))
        if not value:
            raise CredentialError('An explicitly bound credential is unavailable')
        keys.remember_secret(value)
        out[row['env']] = value
    return out


def catalog_prompt(home, seat, deny=()):
    try:
        rows = [r for r in _load(home, seat).values() if r['enabled'] and not _denied(r['env'], deny)]
        if not rows:
            return ''
        return ('Configured secret variables (values are never in this prompt): ' + '; '.join(
            r['env'] + ' for ' + ', '.join(r['consumers']) for r in rows)
            + '. Use the named environment variable only in those consumers. Never print or request its value in chat. '
              'Existing tool approval rules still apply. Configure or rotate credentials in the Credentials view.')
    except Exception:
        return 'The credential catalog is unavailable. Ask the user to check Credentials; do not request secrets in chat.'
