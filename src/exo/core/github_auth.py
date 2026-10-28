"""Per-seat GitHub authentication for trusted shell tools; never changes host gh login.

Secrets stay out of metadata, arguments and returned status. Shell access is trusted:
a command can use or transform its credential; output redaction is not a sandbox.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable

from exo.core.models import keys
from exo.core.private import atomic_write, private_dir


class AuthError(ValueError):
    pass


def _identity(home: str | Path, seat: str, host: str = 'github.com') -> tuple[Path, str, str]:
    if not isinstance(seat, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', seat):
        raise AuthError('Invalid seat name')
    if not isinstance(host, str):
        raise AuthError('Invalid GitHub hostname')
    host = host.lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or '..' in host:
        raise AuthError('Invalid GitHub hostname')
    home = Path(home).expanduser().resolve()
    return home, seat, host


def paths(home: str | Path, seat: str) -> tuple[Path, Path]:
    home, seat, _ = _identity(home, seat)
    base = home / 'state' / 'github-auth' / seat
    return base / 'binding.json', base / 'gh'


def account(home: str | Path, seat: str, host: str) -> str:
    home, seat, host = _identity(home, seat, host)
    digest = hashlib.sha256(f'{home}\0{seat}\0{host}'.encode()).hexdigest()
    return 'EXO_GITHUB_' + digest.upper() + '_TOKEN'


def binding(home: str | Path, seat: str) -> dict:
    path, _ = paths(home, seat)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise AuthError('GitHub authentication metadata is unreadable') from None
    if not isinstance(data, dict) or data.get('mode') not in ('token', 'gh-login', 'host', 'disabled'):
        raise AuthError('GitHub authentication metadata is invalid')
    _identity(home, seat, data.get('host', ''))
    if data['mode'] == 'token' and data.get('store') not in ('keychain', 'secret-service', 'file'):
        raise AuthError('GitHub authentication store is invalid')
    return data


def configure(home: str | Path, seat: str, mode: str, host: str = 'github.com',
              token: str | None = None, store: str | None = None) -> dict:
    from exo import providers_setup as ps
    home, seat, host = _identity(home, seat, host)
    if mode not in ('token', 'gh-login', 'host'):
        raise AuthError('Choose token, gh-login or host authentication')
    old = binding(home, seat)
    meta = {'mode': mode, 'host': host}
    if mode == 'token':
        value = ps.check_key(token or '')
        keys.remember_secret(value)
        store = store or ps.default_store()
        name = account(home, seat, host)
        if store == 'keychain':
            ps._keychain_set(name, value)
        elif store == 'secret-service':
            ps._secret_tool_set(name, value)
        elif store == 'file':
            ps.write_keys_file(name, value)
        else:
            raise AuthError('Choose keychain, secret-service or file storage')
        meta['store'] = store
    if old.get('mode') == 'token' and (mode != 'token' or old.get('host') != host):
        _remove_saved_token(home, seat, old)
    path, ghdir = paths(home, seat)
    private_dir(path.parent)
    if mode == 'gh-login':
        private_dir(ghdir)
    atomic_write(path, json.dumps(meta) + '\n')
    return status(home, seat)


def _token(home: str | Path, seat: str, meta: dict) -> str | None:
    if meta['mode'] == 'token':
        name = account(home, seat, meta['host'])
        readers = {'keychain': keys.keychain_get, 'secret-service': keys.secret_service_get,
                   'file': keys.file_get}
        reader = readers.get(meta.get('store'))
        value = reader(name) if reader else None
        if value:
            keys.remember_secret(value)
        return value
    if meta['mode'] == 'gh-login':
        _, ghdir = paths(home, seat)
        path = ghdir / 'hosts.yml'
        try:
            if path.is_symlink() or ghdir.is_symlink() or (path.stat().st_mode & 0o077) or (ghdir.stat().st_mode & 0o077):
                return None
            # gh --insecure-storage writes plain YAML scalar tokens. Read only
            # the selected host's private file, never gh's global OS keychain.
            text = path.read_text()
        except OSError:
            return None
        active, values = False, set()
        for line in text.splitlines():
            if line and not line[0].isspace():
                active = line.strip().rstrip(':').strip("\"'") == meta['host']
            elif active:
                match = re.fullmatch(r'\s+oauth_token:\s*([A-Za-z0-9_\-]+)\s*', line)
                if match:
                    values.add(match.group(1))
        if len(values) != 1:
            return None  # ambiguous/missing credentials never fall back
        value = values.pop()
        keys.remember_secret(value)
        return value

    return None


def status(home: str | Path, seat: str) -> dict:
    meta = binding(home, seat)
    if not meta:
        return {'seat': seat, 'configured': False, 'mode': 'legacy-host'}
    available = True if meta['mode'] == 'host' else bool(_token(home, seat, meta))
    return {'seat': seat, 'configured': True, 'mode': meta['mode'], 'host': meta['host'],
            'store': meta.get('store', 'gh-config' if meta['mode'] == 'gh-login' else 'host'),
            'available': available}


def child_env(env: dict[str, str], home: str | Path, seat: str,
              deny: Iterable[str] = ()) -> dict[str, str]:
    """Apply an explicit binding after the common scrub; deny always wins."""
    meta = binding(home, seat)
    if not meta:
        return dict(env)
    names = ('GH_TOKEN', 'GH_CONFIG_DIR', 'GH_HOST')
    if any(fnmatch.fnmatchcase(name, pattern) for name in names for pattern in deny):
        raise AuthError('Seat GitHub authentication is blocked by tools.env_deny')
    if meta['mode'] == 'host':
        out = dict(env)
        for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_CONFIG_DIR'):
            if name in os.environ and not any(fnmatch.fnmatchcase(name, pattern) for pattern in deny):
                out[name] = os.environ[name]
        out['GH_HOST'] = meta['host']
        return out
    if meta['mode'] == 'disabled':
        raise AuthError('Seat GitHub authentication is disabled; configure authentication explicitly')
    value = _token(home, seat, meta)
    if not value:
        raise AuthError('Seat GitHub authentication is unavailable; configure or log in before running tools')
    out = dict(env)
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_CONFIG_DIR'):
        out.pop(name, None)
    _, ghdir = paths(home, seat)
    if meta['mode'] == 'token':
        ghdir = ghdir / 'token'  # no prior gh-login accounts in token-mode config
    private_dir(ghdir)
    out.update(GH_CONFIG_DIR=str(ghdir), GH_HOST=meta['host'], GH_PROMPT_DISABLED='1')
    if meta['host'] == 'github.com' or meta['host'].endswith('.ghe.com'):
        out['GH_TOKEN'] = value
    else:
        if any(fnmatch.fnmatchcase('GH_ENTERPRISE_TOKEN', pattern) for pattern in deny):
            raise AuthError('Seat GitHub authentication is blocked by tools.env_deny')
        out['GH_ENTERPRISE_TOKEN'] = value
    return out


def _remove_saved_token(home: str | Path, seat: str, meta: dict) -> None:
    from exo import providers_setup as ps
    name = account(home, seat, meta['host'])
    ps._keychain_delete(name)
    ps._secret_tool_delete(name)
    ps.remove_from_keys_file(name)


def remove(home: str | Path, seat: str) -> dict:
    meta = binding(home, seat)
    if meta and meta['mode'] == 'token':
        _remove_saved_token(home, seat, meta)
    path, ghdir = paths(home, seat)
    shutil.rmtree(ghdir, ignore_errors=True)
    private_dir(path.parent)
    atomic_write(path, json.dumps({'mode': 'disabled', 'host': meta.get('host', 'github.com')}) + '\n')
    return {'seat': seat, 'configured': True, 'mode': 'disabled', 'available': False}
