"""Updater preflight and injection tests: no actual pulls, installs, core or model calls."""
import os
import subprocess
import sys

import pytest

from exo import update_cmd as update


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    # Remove the harness's PYTHONPATH and any host source settings, only for this test process.
    for key in tuple(os.environ):
        if key.startswith(('GIT_', 'PIP_', 'UV_')) or key in {
            'PYTHONPATH', 'PYTHONHOME', 'EXO_PYTHON', 'EXO_VENV', 'BASH_ENV', 'ENV'
        }:
            monkeypatch.delenv(key)
    (tmp_path / '.git').mkdir()
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'install.sh').write_text('#!/bin/bash\n')
    monkeypatch.setattr(update, 'read_install', lambda: {})
    monkeypatch.setattr(update, 'checkout_root', lambda: tmp_path)
    return tmp_path


class Fake:
    def __init__(self, *, pip=True, uv=True, dirty='', ignored='', fail=None, exception=None):
        self.pip, self.uv, self.dirty, self.ignored = pip, uv, dirty, ignored
        self.fail, self.exception = fail, exception
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self.exception and self.exception in cmd:
            raise OSError('simulated missing executable')
        code, stdout = 0, ''
        if '--porcelain=v1' in cmd:
            stdout = self.dirty
        elif 'ls-files' in cmd:
            stdout = self.ignored
        elif 'rev-parse' in cmd:
            stdout = 'abc123\n'
        elif 'pip' in cmd and '--version' in cmd:
            code = 0 if self.pip else 1
        elif cmd[0] == 'uv' and '--help' in cmd:
            code = 0 if self.uv else 1
        elif '-c' in cmd and 'import exo; print(exo.__version__)' in cmd:
            stdout = '0.2.0\n'
        if self.fail and self.fail in cmd:
            code = 1
        return subprocess.CompletedProcess(cmd, code, stdout, 'simulated failure' if code else '')


def mutations(fake):
    return [cmd for cmd in fake.calls if 'pull' in cmd or 'install' in cmd or 'bash' in cmd and '--version' not in cmd]


def test_no_pip_uses_uv_target_before_pull(checkout, capsys):
    fake = Fake(pip=False)
    assert update.cmd_update([], run=fake) == 0
    pull, install = mutations(fake)
    assert 'pull' in pull
    assert install == ['uv', '--no-config', 'pip', 'install', '--python', sys.executable,
                       '-q', '-e', f'{checkout}[tui]']
    assert fake.calls.index(next(c for c in fake.calls if '--help' in c)) < fake.calls.index(pull)
    assert all('core.fsmonitor=false' in c and 'core.hooksPath=/dev/null' in c
               for c in fake.calls if c[0] == 'git')
    out = capsys.readouterr().out
    assert '-> 0.2.0' in out and 'Restart required' in out and 'exo stop' in out
    assert not any(cmd[:2] == ['exo', 'stop'] for cmd in fake.calls)


@pytest.mark.parametrize('exception', [None, 'uv'])
def test_no_pip_or_uv_fails_before_mutation(checkout, capsys, exception):
    fake = Fake(pip=False, uv=False, exception=exception)
    assert update.cmd_update([], run=fake) == 1
    assert not mutations(fake)
    assert 'target Python has no pip and uv pip is unavailable' in capsys.readouterr().err


@pytest.mark.parametrize('dirty', [' M src/exo/cli.py\n', '?? src/exo/new.py\n',
                                  '?? scripts/extra.sh\n', '?? notes/file.txt\n'])
def test_dirty_checkout_blocks_all_mutation(checkout, dirty, capsys):
    fake = Fake(dirty=dirty)
    assert update.cmd_update([], run=fake) == 1
    assert not mutations(fake)
    assert '--untracked-files=all' in fake.calls[0]
    assert 'local changes or untracked files' in capsys.readouterr().err


def test_injected_runner_covers_metadata_and_selfchecks(checkout):
    fake = Fake()
    assert update.cmd_update([], run=fake) == 0
    assert any('rev-parse' in cmd for cmd in fake.calls)
    assert any('import exo; print(exo.__version__)' in cmd for cmd in fake.calls)
    assert any('import exo, exo.cli' in cmd for cmd in fake.calls)
    assert any('exo.protocol.export' in cmd for cmd in fake.calls)
    assert any('models' in cmd for cmd in fake.calls)


def test_explicit_read_runner_does_not_receive_mutations(checkout):
    reader, writer = Fake(), Fake()
    assert update.cmd_update([], run=writer, read=reader) == 0
    assert len(writer.calls) == 2 and len(mutations(writer)) == 2
    assert not mutations(reader)


@pytest.mark.parametrize('fail', ['pull', 'install', 'exo.protocol.export'])
def test_failed_update_reports_error_and_stops(checkout, fail, capsys):
    fake = Fake(fail=fail)
    assert update.cmd_update([], run=fake) == 1
    output = capsys.readouterr()
    if fail == 'exo.protocol.export':
        assert 'FAIL protocol export' in output.out
    else:
        assert 'step failed' in output.err
        assert 'Update may be incomplete' in output.out and 'exo stop' in output.out
        assert not any('exo.protocol.export' in cmd for cmd in fake.calls)


def test_executable_failure_is_readable(checkout, capsys):
    fake = Fake(exception='pull')
    assert update.cmd_update([], run=fake) == 1
    assert 'simulated missing executable' in capsys.readouterr().err


@pytest.mark.parametrize('flag', ['--help', '--dry-run', '-n'])
def test_help_and_dry_run_do_not_spawn_even_read_probes(checkout, monkeypatch, flag):
    def forbidden(*args, **kwargs):
        pytest.fail('help/dry run must not spawn a subprocess')
    monkeypatch.setattr(update.subprocess, 'run', forbidden)
    assert update.cmd_update([flag], run=forbidden, read=forbidden) == 0


@pytest.mark.parametrize('name', ['PYTHONPATH', 'PIP_INDEX_URL', 'UV_INDEX', 'GIT_CONFIG_COUNT',
                                'BASH_ENV', 'EXO_PYTHON', 'EXO_VENV'])
def test_source_modifiers_refused_before_any_subprocess(checkout, monkeypatch, capsys, name):
    monkeypatch.setenv(name, 'untrusted')
    fake = Fake()
    assert update.cmd_update([], run=fake) == 1
    assert fake.calls == []
    assert name in capsys.readouterr().err


def test_installer_uses_selected_installer_and_target_python(checkout, monkeypatch):
    py = str(checkout / 'venv' / 'bin' / 'python')
    monkeypatch.setattr(update, 'read_install', lambda: {'checkout': str(checkout),
                                                      'venv': str(checkout / 'venv')})
    fake = Fake(pip=False)
    assert update.cmd_update([], run=fake) == 0
    pull, install, script = mutations(fake)
    assert install[install.index('--python') + 1] == py
    assert '--no-pip' in script and '--no-interview' in script and '--yes' in script
    assert script[script.index('--python') + 1] == py
    assert 'GIT_CONFIG_KEY_0=core.fsmonitor' in script
    assert f'EXO_VENV={checkout / "venv"}' in script
    assert '-e' not in install  # Installer installs stay non-editable.


@pytest.mark.parametrize('symlink', [False, True])
def test_missing_or_linked_installer_blocks_pull(checkout, monkeypatch, symlink, capsys):
    script = checkout / 'scripts' / 'install.sh'
    script.unlink()
    if symlink:
        script.symlink_to('/bin/true')
    monkeypatch.setattr(update, 'read_install', lambda: {'checkout': str(checkout)})
    fake = Fake()
    assert update.cmd_update([], run=fake) == 1
    assert not mutations(fake)
    assert 'installer is missing or a symlink' in capsys.readouterr().err


def test_invalid_configured_checkout_does_not_update_another_install(checkout, monkeypatch):
    monkeypatch.setattr(update, 'read_install', lambda: {'checkout': '/nonexistent/exo'})
    assert update.plan()['mode'] == 'unknown'


def test_selfcheck_honors_runner_and_handles_spawn_failure():
    fake = Fake(exception='exo.protocol.export')
    assert update.self_check('/target/python', run=fake) == [
        ('import exo', True), ('protocol export is current', False), ('exo models', True)]
    assert len(fake.calls) == 3


def test_ignored_executable_source_blocks_pull(checkout, capsys):
    fake = Fake(ignored='src/exo/new\nfile.py\0')
    assert update.cmd_update([], run=fake) == 1
    assert not mutations(fake)
    assert 'ignored source files' in capsys.readouterr().err


def test_ignored_python_cache_does_not_block(checkout):
    assert update.cmd_update([], run=Fake(ignored='src/exo/__pycache__/cli.cpython-313.pyc\0')) == 0


def test_git_status_cannot_execute_configured_fsmonitor(checkout):
    # Empty templates prevent even copying host hooks into this scratch checkout.
    subprocess.run(['git', 'init', '--quiet', '--template=', str(checkout)], check=True)
    marker = checkout / 'fsmonitor-ran'
    hook = checkout / 'fsmonitor.sh'
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o700)
    subprocess.run(['git', '-C', str(checkout), 'config', 'core.fsmonitor', str(hook)], check=True)
    result = update._read(update._git(checkout, 'status', '--porcelain=v1', '--untracked-files=all'))
    assert result.returncode == 0
    assert not marker.exists()


def test_benign_environment_settings_do_not_block(checkout, monkeypatch):
    for key, value in {'UV_CACHE_DIR': '/tmp/cache', 'UV_NO_PROGRESS': '1',
                       'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'GIT_AUTHOR_NAME': 'Test'}.items():
        monkeypatch.setenv(key, value)
    assert update.cmd_update([], run=Fake()) == 0
