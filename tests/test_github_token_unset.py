"""Fake gh models documented environment/config/keyring precedence, with no real credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from exo.core import github_auth as auth


@pytest.mark.parametrize('mode', ['token', 'disabled'])
@pytest.mark.parametrize('host', ['github.com', 'git.example'])
def test_unsetting_token_variables_never_selects_host_keyring(tmp_path, monkeypatch, mode, host):
    home = tmp_path / 'seat home'
    path, _ = auth.paths(home, 'fixture')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'mode': mode, 'host': host, 'store': 'keychain'}))
    selected = 'fake-selected-' + 'credential'
    monkeypatch.setattr(auth, '_token', lambda *a: selected)
    monkeypatch.setattr(auth, '_git_env', lambda env, *a: env)
    env = auth.child_env({'PATH': os.defpath}, home, 'fixture')
    fake = tmp_path / 'fake_gh.py'
    fake.write_text('''import os, pathlib, sys
value = os.environ.get('GH_TOKEN') or os.environ.get('GH_ENTERPRISE_TOKEN')
if not value:
    p = pathlib.Path(os.environ['GH_CONFIG_DIR']) / 'hosts.yml'
    if p.exists():
        value = p.read_text().split('oauth_token: ', 1)[1].splitlines()[0]
    else:
        print('HOST_KEYRING_SELECTED')
        sys.exit(7)
print('SELECTED' if value == 'fake-selected-' + 'credential' else 'DENIED')
''')
    normal = subprocess.run([sys.executable, str(fake)], env=env, capture_output=True, text=True)
    assert normal.stdout.strip() == ('SELECTED' if mode == 'token' else 'DENIED')
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
        env.pop(name, None)
    unset = subprocess.run([sys.executable, str(fake)], env=env, capture_output=True, text=True)
    assert unset.returncode == 0 and unset.stdout.strip() == 'DENIED'
    config = Path(env['GH_CONFIG_DIR']) / 'hosts.yml'
    assert selected not in config.read_text() and config.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('unsafe', ['symlink', 'permissions', 'write-failure'])
def test_fallback_config_failure_refuses_spawn_environment(tmp_path, monkeypatch, unsafe):
    path, ghdir = auth.paths(tmp_path / 'home', 'fixture')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'mode': 'disabled', 'host': 'github.com'}))
    ghdir = ghdir / 'disabled'
    ghdir.mkdir(parents=True, mode=0o700)
    target = tmp_path / 'untouched'
    target.write_text('unchanged')
    if unsafe == 'symlink':
        (ghdir / 'hosts.yml').symlink_to(target)
    elif unsafe == 'permissions':
        ghdir.chmod(0o755)
    else:
        def fail(*args, **kwargs):
            raise OSError('fake private failure')
        monkeypatch.setattr(auth, 'atomic_write', fail)
    with pytest.raises(auth.AuthError, match='^Private GitHub authentication configuration is unavailable$'):
        auth.child_env({}, tmp_path / 'home', 'fixture')
    assert target.read_text() == 'unchanged'
