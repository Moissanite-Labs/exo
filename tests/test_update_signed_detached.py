"""`update.require_signed` on a detached checkout (D-064 with D-073), against real temporary git repositories.

The signed check verifies the exact commit the detached move will check out, before it is checked out, and a failed
later step still rolls back. Git and ssh-keygen run for real; pip, the installer and the self-checks are simulated."""
import os
import shutil
import subprocess

import pytest

from exo import update_cmd as update
from exo import update_verify

KEYGEN = shutil.which('ssh-keygen')
pytestmark = pytest.mark.skipif(not (shutil.which('git') and KEYGEN), reason='git or ssh-keygen missing')
GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', '-c', 'commit.gpgsign=false',
       '-c', 'core.hooksPath=/dev/null', '-c', 'init.defaultBranch=main']


def git(repo, *args):
    return subprocess.run([*GIT, '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, key=None, files=None):
    for path, text in (files or {f'{name}.txt': name}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, 'add', '-A')
    sign = ['-c', 'gpg.format=ssh', '-c', f'user.signingkey={key}'] if key else []
    r = subprocess.run([*GIT, *sign, '-C', str(repo), 'commit', '-q', *(['-S'] if key else []), '-m', name],
                       capture_output=True, text=True)
    if key and r.returncode:
        pytest.skip('this git cannot sign with ssh keys: ' + r.stderr.strip())
    assert r.returncode == 0, r.stderr
    return git(repo, 'rev-parse', 'HEAD')


def keygen(path):
    subprocess.run([KEYGEN, '-q', '-t', 'ed25519', '-N', '', '-f', str(path), '-C', 't@example.com'], check=True)
    return f"t@example.com {path.with_suffix('.pub').read_text().strip()}\n"


class Runner:
    """Real git; every other command is simulated, and `fail` makes the matching one exit 1."""

    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[0] == 'git':
            return subprocess.run(cmd, capture_output=True, text=True)
        out = '0.2.1 (egg)\n' if 'import exo; print(exo.display_version())' in cmd else ''
        return subprocess.CompletedProcess(cmd, 1 if self.fail and self.fail in cmd else 0, out, '')


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k in tuple(os.environ):
        if k.startswith(('GIT_', 'PIP_', 'UV_')) or k in {'PYTHONPATH', 'PYTHONHOME', 'EXO_PYTHON', 'EXO_VENV',
                                                          'BASH_ENV', 'ENV'}:
            monkeypatch.delenv(k)
    monkeypatch.setenv('EXO_HOME', str(tmp_path / 'exohome'))
    good = tmp_path / 'good'
    good_line = keygen(good)
    origin, live = tmp_path / 'origin', tmp_path / 'live'
    subprocess.run([*GIT, 'init', '-q', '--template=', str(origin)], check=True)
    first = commit(origin, 'first', good, {'scripts/install.sh': '#!/bin/bash\n', 'pyproject.toml': '[project]\n',
                                           update_verify.TRUST_PATH: good_line})
    subprocess.run([*GIT, 'clone', '-q', '--template=', str(origin), str(live)], check=True)
    git(live, 'checkout', '-q', '--detach', first)
    assert update_verify.pin_trust(live) == 0
    from exo.config import Config
    c = Config.load(); c.set('update.require_signed', True); c.save()
    monkeypatch.setattr(update, 'read_install', lambda: {})
    monkeypatch.setattr(update, 'checkout_root', lambda: live)
    return dict(origin=origin, live=live, first=first, good=good)


def head(live):
    return git(live, 'rev-parse', 'HEAD'), git(live, 'branch', '--show-current')


def test_signed_target_moves_the_detached_checkout(world):
    second = commit(world['origin'], 'second', world['good'])
    run = Runner()
    assert update.cmd_update([], run=run) == 0
    assert head(world['live']) == (second, '')  # moved to the verified commit, still detached
    assert sum('fetch' in c for c in run.calls) == 1  # the signed check and the move share one fetch
    assert not any('pull' in c or 'merge' in c and '--ff-only' in c for c in run.calls)


def test_unsigned_target_refused_before_checkout(world, capsys):
    commit(world['origin'], 'second')
    run = Runner()
    assert update.cmd_update([], run=run) == 1
    assert head(world['live']) == (world['first'], '')
    assert not any('checkout' in c for c in run.calls) and not any('install' in c for c in run.calls)
    assert 'signature' in capsys.readouterr().err


def test_signed_target_rolls_back_when_install_fails(world, capsys):
    commit(world['origin'], 'second', world['good'])
    run = Runner(fail='install')
    assert update.cmd_update([], run=run) == 1
    assert head(world['live']) == (world['first'], '')
    assert 'ROLLED BACK' in capsys.readouterr().out
