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
import shlex
import sys
import subprocess
import selectors
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from exo.core.models import keys
from exo.core.private import atomic_write, private_dir


DISABLED_TOKEN = "exo-disabled-no-github-credentials"  # public invalid sentinel, never a saved credential


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
    pending = data.get('cleanup_pending', [])
    if not isinstance(pending, list):
        raise AuthError('GitHub cleanup metadata is invalid')
    for item in pending:
        if not isinstance(item, dict) or item.get('store') not in ('keychain', 'secret-service', 'file', 'gh-config'):
            raise AuthError('GitHub cleanup metadata is invalid')
        _identity(home, seat, item.get('host', ''))
    return data


def configure(home: str | Path, seat: str, mode: str, host: str = 'github.com',
              token: str | None = None, store: str | None = None) -> dict:
    from exo import providers_setup as ps
    home, seat, host = _identity(home, seat, host)
    if mode not in ('token', 'gh-login', 'host'):
        raise AuthError('Choose token, gh-login or host authentication')
    if mode == 'token':
        value = ps.check_key(token or '')
        keys.remember_secret(value)
        store = store or ('keychain' if sys.platform == 'darwin' else ps.default_store())
        if store not in ('keychain', 'secret-service', 'file'):
            raise AuthError('Choose keychain, secret-service or file storage')
        if store == 'keychain' and (sys.platform != 'darwin' or os.environ.get('EXO_NO_KEYCHAIN')):
            raise AuthError('macOS Keychain is unavailable; no fallback storage was used')
    old = binding(home, seat)
    changing = (old.get('mode') in ('token', 'gh-login') and
                (old.get('mode') != mode or old.get('host') != host or
                 (mode == 'token' and old.get('store') != store)))
    if changing:
        raise AuthError('Remove the previous GitHub binding before changing its mode, hostname or store')
    if old.get('cleanup_pending'):
        if remove(home, seat).get('cleanup_pending'):
            raise AuthError('Previous GitHub credential cleanup is incomplete; retry remove before configuring')
    meta = {'mode': mode, 'host': host}
    if mode == 'token':
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
            'available': available, 'cleanup_pending': bool(meta.get('cleanup_pending'))}


def child_env(env: dict[str, str], home: str | Path, seat: str,
              deny: Iterable[str] = (), *, cwd: str | Path | None = None) -> dict[str, str]:
    """Apply an explicit binding after the common scrub; deny always wins."""
    meta = binding(home, seat)
    if not meta:
        return dict(env)
    deny = tuple(deny)
    names = ('GH_TOKEN', 'GH_CONFIG_DIR', 'GH_HOST', 'GH_PROMPT_DISABLED')
    if any(fnmatch.fnmatchcase(name, pattern) for name in names for pattern in deny):
        raise AuthError('Seat GitHub authentication is blocked by tools.env_deny')
    if meta['mode'] == 'host':
        out = {name: value for name, value in env.items()
               if not any(fnmatch.fnmatchcase(name, pattern) for pattern in deny)}
        out['GH_HOST'] = meta['host']
        return out
    disabled = meta['mode'] == 'disabled'
    value = DISABLED_TOKEN if disabled else _token(home, seat, meta)
    if not value:
        raise AuthError('Seat GitHub authentication is unavailable; configure or log in before running tools')
    out = dict(env)
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_CONFIG_DIR'):
        out.pop(name, None)
    _, ghdir = paths(home, seat)
    if meta['mode'] in ('token', 'disabled'):
        ghdir = ghdir / ('disabled' if disabled else 'token')  # no prior gh-login accounts in token-mode config
    try:
        private_dir(ghdir)
        if meta['mode'] in ('token', 'disabled'):
            config = ghdir / 'hosts.yml'
            if ghdir.stat().st_mode & 0o077 or config.is_symlink():
                raise OSError('Unsafe private GitHub configuration')
            # gh consults the global OS keyring if both environment and config
            # lack a token. A public invalid fallback prevents identity changes
            # when a command removes the selected token variables.
            atomic_write(config, f"{meta['host']}:\n    oauth_token: {DISABLED_TOKEN}\n")
    except OSError:
        raise AuthError('Private GitHub authentication configuration is unavailable') from None
    out.update(GH_CONFIG_DIR=str(ghdir), GH_HOST=meta['host'], GH_PROMPT_DISABLED='1')
    if disabled:
        if any(fnmatch.fnmatchcase('GH_ENTERPRISE_TOKEN', pattern) for pattern in deny):
            raise AuthError('Seat GitHub authentication is blocked by tools.env_deny')
        out.update(GH_TOKEN=DISABLED_TOKEN, GH_ENTERPRISE_TOKEN=DISABLED_TOKEN)
    elif meta['host'] == 'github.com' or meta['host'].endswith('.ghe.com'):
        out['GH_TOKEN'] = value
    else:
        if any(fnmatch.fnmatchcase('GH_ENTERPRISE_TOKEN', pattern) for pattern in deny):
            raise AuthError('Seat GitHub authentication is blocked by tools.env_deny')
        out['GH_ENTERPRISE_TOKEN'] = value
    return _git_env(out, meta['host'], deny, cwd)


def _git_env(env: dict[str, str], host: str, deny: Iterable[str],
             cwd: str | Path | None = None) -> dict[str, str]:
    """Child-only equivalent of gh auth setup-git, without editing host Git configuration.

    Trusted shell commands may override this with git -c, embedded URL credentials or SSH.
    """
    out = {k: v for k, v in env.items() if not k.startswith(('GIT_CONFIG', 'GIT_TRACE')) and
           k not in ('GIT_ASKPASS', 'SSH_ASKPASS', 'GIT_CURL_VERBOSE', 'GH_DEBUG', 'DEBUG', 'GH_BROWSER')}
    gh = shutil.which('gh', path=out.get('PATH')) or 'gh'
    entries = [('http.extraHeader', ''), (f'http.https://{host}/.extraHeader', ''),
               (f'credential.https://{host}.helper', ''),
               (f'credential.https://{host}.helper', '!' + shlex.quote(gh) + ' auth git-credential'),
               (f'credential.https://{host}.username', 'x-access-token'), ('core.askPass', '')]
    # More-specific URL headers beat a host-only reset even at command scope.
    # Read names only, then override matching host keys without reading their values.
    git = shutil.which('git', path=out.get('PATH'))
    if not git:
        raise AuthError('Git is unavailable; HTTPS credential configuration cannot be checked')
    for key in _git_header_names(git, out, cwd):
        if not key.startswith('http.') or not key.lower().endswith('.extraheader'):
            continue
        url = key[5:-12]
        try:
            selected = urlsplit(url)
            matches = selected.scheme == 'https' and selected.hostname == host
        except ValueError:
            matches = False
        if matches:
            if selected.username is not None or selected.password is not None or selected.query or selected.fragment:
                raise AuthError('GitHub HTTPS configuration contains a credential-bearing URL; remove it before using the seat binding')
            entries.append((key, ''))
    additions = {'GIT_CONFIG_COUNT': str(len(entries)), 'GIT_TERMINAL_PROMPT': '0'}
    for i, (key, value) in enumerate(entries):
        additions[f'GIT_CONFIG_KEY_{i}'] = key
        additions[f'GIT_CONFIG_VALUE_{i}'] = value
    if any(fnmatch.fnmatchcase(name, pattern) for name in additions for pattern in deny):
        raise AuthError('Seat GitHub authentication is blocked by tools.env_deny')
    out.update(additions)
    return out


def _git_header_names(git: str, env: dict[str, str], cwd) -> list[str]:
    """Read bounded names only, never config values or unbounded subprocess diagnostics."""
    proc = None
    try:
        proc = subprocess.Popen([git, 'config', '--name-only', '--get-regexp', r'^http\..*extraheader$'],
                                cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        deadline, data = time.monotonic() + 3, bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise AuthError('GitHub HTTPS configuration inspection timed out')
                chunk = os.read(proc.stdout.fileno(), min(8192, 65537 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 65536:
                    raise AuthError('GitHub HTTPS configuration exceeds the inspection limit')
        code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        names = bytes(data).decode('utf-8').splitlines()
        if code not in (0, 1) or len(names) > 256:
            raise AuthError('GitHub HTTPS configuration could not be checked')
        return list(dict.fromkeys(names))
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise AuthError('GitHub HTTPS credential configuration could not be checked') from None
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()


def _remove_saved_token(home: str | Path, seat: str, meta: dict) -> bool:
    """Delete only the selected store. False means cleanup was not confirmed."""
    from exo import providers_setup as ps
    name = account(home, seat, meta['host'])
    try:
        if meta['store'] == 'keychain':
            if sys.platform != 'darwin' or os.environ.get('EXO_NO_KEYCHAIN'):
                return False
            result = ps._run(['security', 'delete-generic-password', '-s', keys.KEYCHAIN_SERVICE, '-a', name])
            return result.returncode in (0, 44)  # errSecItemNotFound (-25300 modulo 256) is already absent.
        if meta['store'] == 'secret-service':
            return ps._secret_tool_delete(name)
        ps.remove_from_keys_file(name)  # False means the file already has no matching key.
        return True
    except (OSError, ps.SetupError, subprocess.SubprocessError):
        return False


def remove(home: str | Path, seat: str) -> dict:
    meta = binding(home, seat)
    path, ghdir = paths(home, seat)
    private_dir(path.parent)
    cleanup = meta.get('cleanup_pending') or ([{'host': meta['host'], 'store': meta['store']}]
                                               if meta.get('mode') == 'token' else [])
    disabled = {'mode': 'disabled', 'host': meta.get('host', 'github.com'), 'cleanup_pending': cleanup}
    # Stop future authenticated spawns before attempting fallible store cleanup.
    atomic_write(path, json.dumps(disabled) + '\n')
    remaining = [item for item in cleanup if item.get('store') != 'gh-config' and
                 not _remove_saved_token(home, seat, item)]
    try:
        if ghdir.exists():
            shutil.rmtree(ghdir)
    except OSError:
        remaining.append({'host': disabled['host'], 'store': 'gh-config'})
    disabled['cleanup_pending'] = remaining
    atomic_write(path, json.dumps(disabled) + '\n')
    return {'seat': seat, 'configured': True, 'mode': 'disabled', 'available': False,
            'cleanup_pending': bool(remaining), 'removed': not bool(remaining)}
