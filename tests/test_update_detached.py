"""`exo update` on a detached checkout (D-064), against real temporary git repositories.

Git runs for real; pip, the installer and the self-checks are simulated. Nothing touches a live install."""
import os
import subprocess

import pytest

from exo import update_cmd as update

GIT = ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
       '-c', 'core.hooksPath=/dev/null', '-c', 'init.defaultBranch=main']


def git(repo, *args):
    return subprocess.run([*GIT, '-C', str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, name):
    (repo / f'{name}.txt').write_text(name)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', name)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repos(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('GIT_', 'PIP_', 'UV_')) or key in {
            'PYTHONPATH', 'PYTHONHOME', 'EXO_PYTHON', 'EXO_VENV', 'BASH_ENV', 'ENV'
        }:
            monkeypatch.delenv(key)
    origin, live = tmp_path / 'origin', tmp_path / 'live'
    subprocess.run([*GIT, 'init', '-q', '--template=', str(origin)], check=True)
    (origin / 'scripts').mkdir()
    (origin / 'scripts' / 'install.sh').write_text('#!/bin/bash\n')
    (origin / 'pyproject.toml').write_text('[project]\nname = "exo"\n')
    first = commit(origin, 'first')
    subprocess.run([*GIT, 'clone', '-q', '--template=', str(origin), str(live)], check=True)
    git(live, 'checkout', '-q', '--detach', first)
    second = commit(origin, 'second')
    monkeypatch.setattr(update, 'read_install', lambda: {})
    monkeypatch.setattr(update, 'checkout_root', lambda: live)
    return origin, live, first, second


class Runner:
    """Real git; every other command is simulated, and `fail` makes the matching one exit 1."""

    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[0] == 'git':
            return subprocess.run(cmd, capture_output=True, text=True)
        out = '0.2.1 (egg)\n' if 'import exo; print(exo.display_version())' in cmd else ''
        code = 1 if self.fail and self.fail in cmd else 0
        return subprocess.CompletedProcess(cmd, code, out, '')


def head(live):
    return git(live, 'rev-parse', 'HEAD'), git(live, 'branch', '--show-current')


def test_detached_checkout_moves_to_the_tracked_branch(repos, capsys):
    _, live, first, second = repos
    run = Runner()
    assert update.cmd_update([], run=run) == 0
    assert head(live) == (second, '')  # moved, and still detached
    assert not any('pull' in c for c in run.calls)
    assert any('fetch' in c for c in run.calls) and any('pip' in c and 'install' in c for c in run.calls)
    assert f'{first[:12]} -> {second[:12]}' in capsys.readouterr().out


def test_recorded_branch_wins_over_the_remote_default(repos, monkeypatch):
    origin, live, _, _ = repos
    git(origin, 'checkout', '-q', '-b', 'canary')
    canary = commit(origin, 'canary')
    git(origin, 'checkout', '-q', 'main')
    monkeypatch.setattr(update, 'read_install', lambda: {'branch': 'canary'})
    assert update.cmd_update([], run=Runner()) == 0
    assert head(live) == (canary, '')


def test_refuses_a_target_that_does_not_contain_the_current_commit(repos, capsys):
    _, live, _, second = repos
    local = commit(live, 'local-only')  # a commit the remote branch does not contain
    git(live, 'checkout', '-q', '--detach', local)
    run = Runner()
    assert update.cmd_update([], run=run) == 1
    assert head(live) == (local, '')
    assert not any('pip' in c and 'install' in c for c in run.calls)
    assert 'not a fast-forward' in capsys.readouterr().err
    assert update.cmd_update(['--force'], run=Runner()) == 0
    assert head(live) == (second, '')


def test_reinstall_failure_rolls_back_to_the_previous_commit(repos, capsys):
    _, live, first, _ = repos
    run = Runner(fail='install')
    assert update.cmd_update([], run=run) == 1
    assert head(live) == (first, '')
    out = capsys.readouterr()
    assert 'step failed' in out.err and f'ROLLED BACK: the checkout is back on {first[:12]}' in out.out


def test_dirty_detached_checkout_is_still_refused_before_fetch(repos, capsys):
    _, live, first, _ = repos
    (live / 'stray.py').write_text('x')
    run = Runner()
    assert update.cmd_update([], run=run) == 1
    assert not any('fetch' in c for c in run.calls) and head(live) == (first, '')
    assert 'local changes or untracked files' in capsys.readouterr().err


@pytest.mark.parametrize('branch', ['-x', 'a..b', 'main;rm'])
def test_hostile_recorded_branch_is_refused_before_fetch(repos, monkeypatch, branch, capsys):
    monkeypatch.setattr(update, 'read_install', lambda: {'branch': branch})
    run = Runner()
    assert update.cmd_update([], run=run) == 1
    assert not any('fetch' in c for c in run.calls)
    assert 'not a plain branch name' in capsys.readouterr().err
