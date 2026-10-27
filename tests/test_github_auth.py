"""GitHub auth uses only disposable homes, fake credentials and fake processes."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from exo.core import github_auth as auth
from exo.core.models import keys
from exo.core.tools.builtin.shell import run_command, tool_env
from exo import github_auth_cmd

TOKEN = 'fake-seat-credential-1234567890'


@pytest.fixture(autouse=True)
def secrets(monkeypatch, tmp_path):
    monkeypatch.setenv('EXO_KEYS_FILE', str(tmp_path / 'keys.env'))
    monkeypatch.setenv('EXO_NO_KEYCHAIN', '1')
    monkeypatch.setenv('EXO_NO_SECRET_SERVICE', '1')
    monkeypatch.setattr(keys, 'keychain_get', lambda n: None)
    monkeypatch.setattr(keys, 'secret_service_get', lambda n: None)
    monkeypatch.setattr('exo.providers_setup._keychain_delete', lambda n: False)
    monkeypatch.setattr('exo.providers_setup._secret_tool_delete', lambda n: False)


def test_namespaces_precedence_redaction_and_no_parent_mutation(tmp_path, monkeypatch):
    home = tmp_path / 'one'
    auth.configure(home, 'alice', 'token', token=TOKEN, store='file')
    monkeypatch.setenv('GH_TOKEN', 'fake-host-credential-123456789')
    before = dict(os.environ)
    env = auth.child_env(tool_env(), home, 'alice')
    assert env['GH_TOKEN'] == TOKEN
    assert env['GH_CONFIG_DIR'].startswith(str(home))
    assert dict(os.environ) == before
    assert TOKEN not in json.dumps(auth.status(home, 'alice'))
    assert keys.redact_secrets(TOKEN) == '[redacted]'
    assert auth.account(home, 'alice', 'github.com') != auth.account(home, 'bob', 'github.com')
    assert auth.account(home, 'alice', 'github.com') != auth.account(tmp_path / 'two', 'alice', 'github.com')
    assert auth.account(home, 'alice', 'github.com') != auth.account(home, 'alice', 'enterprise.example')
    assert TOKEN not in auth.paths(home, 'alice')[0].read_text()


def test_missing_and_deny_fail_closed(tmp_path):
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    for pattern in ('GH_*', '*TOKEN', 'GH_CONFIG_DIR', 'GH_HOST'):
        with pytest.raises(auth.AuthError):
            auth.child_env({}, tmp_path, 'alice', deny=[pattern])
    Path(os.environ['EXO_KEYS_FILE']).unlink()
    with pytest.raises(auth.AuthError):
        auth.child_env({'HOME': '/fake/host'}, tmp_path, 'alice')


def test_rotation_remove_and_explicit_host(tmp_path, monkeypatch):
    monkeypatch.setattr('exo.providers_setup._keychain_delete', lambda n: False)
    monkeypatch.setattr('exo.providers_setup._secret_tool_delete', lambda n: False)
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN + 'rotated', store='file')
    assert auth.child_env({}, tmp_path, 'alice')['GH_TOKEN'] == TOKEN + 'rotated'
    auth.remove(tmp_path, 'alice')
    assert auth.status(tmp_path, 'alice')['mode'] == 'disabled'
    with pytest.raises(auth.AuthError):
        auth.child_env({}, tmp_path, 'alice')
    assert keys.file_get(auth.account(tmp_path, 'alice', 'github.com')) is None
    auth.configure(tmp_path, 'alice', 'host')
    assert auth.child_env({'HOME': '/host'}, tmp_path, 'alice')['HOME'] == '/host'


def test_shell_injects_and_redacts_without_argv_token(tmp_path):
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    code, output, _ = run_command('printf "%s" "$GH_TOKEN"', str(tmp_path), 3,
                                  github_auth=(str(tmp_path), 'alice'))
    assert code == 0 and TOKEN not in output and '[redacted]' in output


def test_cli_stdin_and_quiet_errors(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv('EXO_HOME', str(tmp_path))
    monkeypatch.setattr('sys.stdin', io.StringIO(TOKEN))
    assert github_auth_cmd.main(['configure', '--seat', 'alice', '--token-stdin', '--store', 'file']) == 0
    assert TOKEN not in capsys.readouterr().out
    with pytest.raises(SystemExit):
        github_auth_cmd.main(['configure', TOKEN])
    assert TOKEN not in capsys.readouterr().err


def test_enterprise_and_login_missing_do_not_fallback(tmp_path, monkeypatch):
    auth.configure(tmp_path, 'alice', 'token', host='git.example', token=TOKEN, store='file')
    env = auth.child_env({'GITHUB_TOKEN': 'host', 'GH_ENTERPRISE_TOKEN': 'host'}, tmp_path, 'alice')
    assert env['GH_ENTERPRISE_TOKEN'] == TOKEN and 'GITHUB_TOKEN' not in env and 'GH_TOKEN' not in env
    auth.configure(tmp_path, 'bob', 'gh-login')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'private'))
    with pytest.raises(auth.AuthError):
        auth.child_env({}, tmp_path, 'bob')


def test_selected_store_never_uses_shadow_keychain(tmp_path, monkeypatch):
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    monkeypatch.setattr(keys, 'keychain_get', lambda n: 'stale-secret-1234567890')
    assert auth.child_env({}, tmp_path, 'alice')['GH_TOKEN'] == TOKEN
    Path(os.environ['EXO_KEYS_FILE']).unlink()
    with pytest.raises(auth.AuthError):
        auth.child_env({}, tmp_path, 'alice')


def test_private_plaintext_login_only_never_calls_gh_or_keychain(tmp_path, monkeypatch):
    auth.configure(tmp_path, 'alice', 'gh-login')
    _, ghdir = auth.paths(tmp_path, 'alice')
    path = ghdir / 'hosts.yml'
    path.write_text('github.com:\n    oauth_token: ' + TOKEN + '\n')
    path.chmod(0o600)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must not consult gh global keychain'))
    assert auth.child_env({}, tmp_path, 'alice')['GH_TOKEN'] == TOKEN
    path.chmod(0o644)
    with pytest.raises(auth.AuthError):
        auth.child_env({}, tmp_path, 'alice')
    path.unlink()
    with pytest.raises(auth.AuthError):
        auth.child_env({}, tmp_path, 'alice')


def test_gh_login_validates_before_invocation_and_uses_https(tmp_path, monkeypatch):
    monkeypatch.setenv('EXO_HOME', str(tmp_path))
    calls = []
    def fake_run(argv, **kw):
        calls.append((argv, kw))
        ghdir = Path(kw['env']['GH_CONFIG_DIR'])
        (ghdir / 'hosts.yml').write_text('github.com:\n    oauth_token: ' + TOKEN + '\n')
        (ghdir / 'hosts.yml').chmod(0o600)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert github_auth_cmd.main(['login', '--seat', 'alice', '--host', 'invalid/host']) == 1
    assert not calls
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    assert github_auth_cmd.main(['login', '--seat', 'alice']) == 0
    assert auth.child_env({}, tmp_path, 'alice')['GH_TOKEN'] == TOKEN
    assert (auth.paths(tmp_path, 'alice')[1] / 'hosts.yml').exists()
    argv, kw = calls[0]
    assert '--insecure-storage' in argv and '--skip-ssh-key' in argv
    assert argv[argv.index('--git-protocol') + 1] == 'https'
    assert TOKEN not in repr(argv)


def test_bound_job_pump_redacts_split_tokens_before_disk_rotation(tmp_path):
    from types import SimpleNamespace
    from exo.core.jobs import JobManager, Handle, JobConfig
    class Output:
        def __init__(self):
            self.chunks = iter([b'x' * 70 + TOKEN[:10].encode(), TOKEN[10:].encode() + b'END', b''])
        def read1(self, n):
            return next(self.chunks)
    manager = object.__new__(JobManager)
    manager.cfg = JobConfig(output_cap=85)
    log = tmp_path / 'job.log'
    handle = Handle('j_fake', SimpleNamespace(stdout=Output()), None, 'fake command', 0, auth_secrets=(TOKEN,))
    manager._pump(handle, log)
    all_text = ''.join(p.read_text() for p in tmp_path.glob('job.log*'))
    assert TOKEN not in all_text and TOKEN[:10] not in all_text and '[redacted]' in all_text


def test_malformed_metadata_is_quiet_and_token_mode_excludes_old_login(tmp_path):
    auth.configure(tmp_path, 'alice', 'gh-login')
    path, ghdir = auth.paths(tmp_path, 'alice')
    (ghdir / 'hosts.yml').write_text('other.example:\n    oauth_token: fake-other-secret\n')
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    env = auth.child_env({}, tmp_path, 'alice')
    assert Path(env['GH_CONFIG_DIR']) == ghdir / 'token'
    assert not (Path(env['GH_CONFIG_DIR']) / 'hosts.yml').exists()
    for host in (None, 42, [], 'bad/host'):
        path.write_text(json.dumps({'mode': 'token', 'store': 'file', 'host': host}))
        with pytest.raises(auth.AuthError):
            auth.status(tmp_path, 'alice')
