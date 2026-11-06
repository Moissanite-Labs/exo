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
    assert auth.child_env({}, tmp_path, 'alice')['GH_TOKEN'] == auth.DISABLED_TOKEN
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
    original_run = subprocess.run
    def no_gh(argv, **kwargs):
        assert Path(argv[0]).name == 'git', 'must not consult gh global keychain'
        return original_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, 'run', no_gh)
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
    original_run = subprocess.run
    def fake_run(argv, **kw):
        if Path(argv[0]).name == 'git':
            return original_run(argv, **kw)
        calls.append((argv, kw))
        ghdir = Path(kw['env']['GH_CONFIG_DIR'])
        (ghdir / 'hosts.yml').write_text('github.com:\n    oauth_token: ' + TOKEN + '\n')
        (ghdir / 'hosts.yml').chmod(0o600)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)
    assert github_auth_cmd.main(['login', '--seat', 'alice', '--host', 'invalid/host']) == 1
    assert not calls
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    assert github_auth_cmd.main(['login', '--seat', 'alice']) == 1
    assert not calls
    auth.remove(tmp_path, 'alice')
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
    with pytest.raises(auth.AuthError):
        auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    auth.remove(tmp_path, 'alice')
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    env = auth.child_env({}, tmp_path, 'alice')
    assert Path(env['GH_CONFIG_DIR']) == ghdir / 'token'
    assert auth.DISABLED_TOKEN in (Path(env['GH_CONFIG_DIR']) / 'hosts.yml').read_text()
    assert TOKEN not in (Path(env['GH_CONFIG_DIR']) / 'hosts.yml').read_text()
    for host in (None, 42, [], 'bad/host'):
        path.write_text(json.dumps({'mode': 'token', 'store': 'file', 'host': host}))
        with pytest.raises(auth.AuthError):
            auth.status(tmp_path, 'alice')


def test_keychain_errors_are_quiet_and_never_fall_back(tmp_path, monkeypatch, capsys):
    import io
    from exo import providers_setup as ps
    monkeypatch.setenv('EXO_HOME', str(tmp_path))
    monkeypatch.delenv('EXO_NO_KEYCHAIN')
    monkeypatch.setattr('sys.platform', 'darwin')
    monkeypatch.setattr('sys.stdin', io.StringIO(TOKEN))
    def refuse(*args, **kwargs):
        raise ps.SetupError('Keychain refused: ' + TOKEN)
    monkeypatch.setattr(ps, '_keychain_set', refuse)
    assert github_auth_cmd.main(['configure', '--seat', 'alice', '--token-stdin']) == 1
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert not Path(os.environ['EXO_KEYS_FILE']).exists()
    assert not auth.paths(tmp_path, 'alice')[0].exists()


def test_remove_disables_first_and_retains_failed_cleanup_for_retry(tmp_path, monkeypatch):
    from exo import providers_setup as ps
    monkeypatch.delenv('EXO_NO_KEYCHAIN')
    monkeypatch.setattr('sys.platform', 'darwin')
    monkeypatch.setattr(ps, '_keychain_set', lambda *a: None)
    monkeypatch.setattr(keys, 'keychain_get', lambda n: TOKEN)
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='keychain')
    def locked(argv, **kwargs):
        assert auth.binding(tmp_path, 'alice')['mode'] == 'disabled'
        return subprocess.CompletedProcess(argv, 36, '', TOKEN)
    monkeypatch.setattr(ps, '_run', locked)
    result = auth.remove(tmp_path, 'alice')
    assert result['cleanup_pending'] and not result['removed']
    assert TOKEN not in json.dumps(result) + auth.paths(tmp_path, 'alice')[0].read_text()
    with pytest.raises(auth.AuthError):
        auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    assert not Path(os.environ['EXO_KEYS_FILE']).exists()
    monkeypatch.setattr(ps, '_run', lambda argv, **kw: subprocess.CompletedProcess(argv, 44, '', ''))
    result = auth.remove(tmp_path, 'alice')
    assert result['removed'] and not result['cleanup_pending']


def test_backend_change_requires_confirmed_removal_before_new_binding(tmp_path, monkeypatch):
    from exo import providers_setup as ps
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    name = auth.account(tmp_path, 'alice', 'github.com')
    monkeypatch.delenv('EXO_NO_KEYCHAIN')
    monkeypatch.setattr('sys.platform', 'darwin')
    saved = {}
    def save(key, value):
        assert keys.file_get(name) is None
        saved[key] = value
    monkeypatch.setattr(ps, '_keychain_set', save)
    monkeypatch.setattr(keys, 'keychain_get', lambda n: saved.get(n))
    with pytest.raises(auth.AuthError):
        auth.configure(tmp_path, 'alice', 'token', token=TOKEN + 'rotated', store='keychain')
    assert keys.file_get(name) == TOKEN and not saved
    assert auth.remove(tmp_path, 'alice')['removed']
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN + 'rotated', store='keychain')
    assert auth.child_env({}, tmp_path, 'alice')['GH_TOKEN'] == TOKEN + 'rotated'
    assert keys.file_get(name) is None


def test_disabled_allows_unrelated_shell_without_host_auth(tmp_path, monkeypatch):
    auth.remove(tmp_path, 'alice')
    before = dict(os.environ)
    env = auth.child_env({'GH_TOKEN': 'host', 'GITHUB_TOKEN': 'host', 'GH_ENTERPRISE_TOKEN': 'host'}, tmp_path, 'alice')
    assert env['GH_TOKEN'] == env['GH_ENTERPRISE_TOKEN'] == auth.DISABLED_TOKEN
    assert 'GITHUB_TOKEN' not in env and env['GH_CONFIG_DIR'].endswith('/disabled')
    code, output, _ = run_command('printf unrelated', str(tmp_path), 3, github_auth=(str(tmp_path), 'alice'))
    assert code == 0 and 'unrelated' in output
    assert dict(os.environ) == before
    for pattern in ('GH_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GH_PROMPT_DISABLED', 'GIT_CONFIG*', 'GIT_CONFIG_VALUE_2', 'GIT_TERMINAL_PROMPT'):
        with pytest.raises(auth.AuthError):
            auth.child_env({}, tmp_path, 'alice', deny=[pattern])


def test_https_git_uses_seat_helper_without_host_config_mutation(tmp_path, monkeypatch):
    import shutil
    git = shutil.which('git')
    if not git:
        pytest.skip('git is not installed')
    bins = tmp_path / 'bin'; bins.mkdir()
    gh = bins / 'gh'
    gh.write_text('#!/bin/sh\nprintf "username=seat-user\\npassword=%s\\n" "$GH_TOKEN"\n')
    gh.chmod(0o700)
    host = tmp_path / 'host'; host.mkdir()
    marker = tmp_path / 'host-helper-used'
    config = host / '.gitconfig'
    config.write_text('[credential]\n helper = !touch "' + str(marker) + '"; echo username=host; echo password=host-password\n'
                      '[http]\n extraHeader = Authorization: host-header\n'
                      '[http "https://github.com/org"]\n extraHeader = Authorization: path-host-header\n')
    original = config.read_bytes()
    repo = tmp_path / 'repo'
    subprocess.run([git, 'init', '-q', str(repo)], check=True, capture_output=True)
    subprocess.run([git, 'config', '--file', str(repo / '.git/config'),
                    'http.https://github.com/org/team.extraHeader', 'Authorization: repo-header'], check=True)
    repo_original = (repo / '.git/config').read_bytes()
    auth.configure(tmp_path, 'alice', 'token', token=TOKEN, store='file')
    base = {'HOME': str(host), 'PATH': str(bins) + os.pathsep + os.environ.get('PATH', ''),
            'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.extraHeader',
            'GIT_CONFIG_VALUE_0': 'Authorization: inherited-header', 'GIT_CONFIG_PARAMETERS': "'credential.helper=host'",
            'GIT_ASKPASS': '/host/askpass', 'SSH_ASKPASS': '/host/askpass', 'GH_DEBUG': 'api',
            'GIT_TRACE': '/host/trace', 'GIT_TRACE_CURL': '/host/curltrace', 'GIT_TRACE_REDACT': '0',
            'GIT_CURL_VERBOSE': '1'}
    env = auth.child_env(base, tmp_path, 'alice', cwd=repo)
    assert all(k not in env for k in ('GIT_CONFIG_PARAMETERS', 'GIT_ASKPASS', 'SSH_ASKPASS', 'GH_DEBUG',
                                        'GIT_TRACE', 'GIT_TRACE_CURL', 'GIT_TRACE_REDACT', 'GIT_CURL_VERBOSE'))
    def fill():
        return subprocess.run([git, 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                              env=env, cwd=repo, text=True, capture_output=True, timeout=5)
    result = fill()
    assert result.returncode == 0 and 'password=' + TOKEN in result.stdout
    assert not marker.exists() and config.read_bytes() == original
    header = subprocess.run([git, 'config', '--get-urlmatch', 'http.extraHeader', 'https://github.com/org/team/repo'],
                            env=env, cwd=repo, text=True, capture_output=True, timeout=5)
    assert header.stdout.strip() == ''
    assert (repo / '.git/config').read_bytes() == repo_original
    auth.remove(tmp_path, 'alice')
    env = auth.child_env(base, tmp_path, 'alice', cwd=repo)
    assert 'password=' + auth.DISABLED_TOKEN in fill().stdout
    assert not marker.exists() and config.read_bytes() == original


@pytest.mark.parametrize('url', ['https://user:fake-config-password' '@github.com/org/',
                                 'https://github.com/org/?token=fake-config-password',
                                 'https://github.com/org/#fake-config-password'])
def test_git_header_url_credentials_never_enter_child_environment(tmp_path, url):
    import shutil
    if not shutil.which('git'):
        pytest.skip('git is not installed')
    host = tmp_path / 'host'; host.mkdir()
    (host / '.gitconfig').write_text('[http "' + url + '"]\n extraHeader = Authorization: fake-header\n')
    env = {'HOME': str(host), 'PATH': os.environ.get('PATH', '')}
    original = dict(env)
    with pytest.raises(auth.AuthError) as caught:
        auth._git_env(env, 'github.com', (), tmp_path)
    assert 'fake-config-password' not in str(caught.value)
    assert env == original


@pytest.mark.parametrize('output', ['x' * 65537, 'http.https://github.com/.extraheader\n' * 257])
def test_git_header_inspection_enforces_stream_and_name_limits(tmp_path, output):
    import sys
    git = tmp_path / 'fake-git'
    git.write_text('#!' + sys.executable + '\nimport sys\nsys.stdout.write(' + repr(output) + ')\n')
    git.chmod(0o700)
    with pytest.raises(auth.AuthError):
        auth._git_header_names(str(git), {}, tmp_path)


def test_git_header_inspection_timeout_reaps_child(tmp_path):
    import sys
    git = tmp_path / 'fake-git'
    git.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(30)\n')
    git.chmod(0o700)
    with pytest.raises(auth.AuthError, match='timed out'):
        auth._git_header_names(str(git), {}, tmp_path)


HOST_AUTH_NAMES = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_CONFIG_DIR')


def test_host_mode_never_restores_globals_removed_by_common_scrub(tmp_path, monkeypatch):
    auth.configure(tmp_path, 'alice', 'host')
    fake_global = {name: 'fake-global-' + name for name in HOST_AUTH_NAMES}
    monkeypatch.setattr(os, 'environ', fake_global)
    provided = tool_env(fake_global)
    assert all(name not in provided for name in HOST_AUTH_NAMES)
    result = auth.child_env(provided, tmp_path, 'alice')
    assert result == {**provided, 'GH_HOST': 'github.com'}
    assert fake_global == {name: 'fake-global-' + name for name in HOST_AUTH_NAMES}


def test_host_mode_preserves_provided_auth_and_stored_config_selection(tmp_path, monkeypatch):
    auth.configure(tmp_path, 'alice', 'host')
    fake_global = {name: 'fake-global-' + name for name in HOST_AUTH_NAMES}
    monkeypatch.setattr(os, 'environ', fake_global)
    provided = {name: 'fake-approved-' + name for name in HOST_AUTH_NAMES}
    provided.update(HOME=str(tmp_path / 'fake-host-home'), XDG_CONFIG_HOME=str(tmp_path / 'fake-xdg'))
    expected = {**provided, 'GH_HOST': 'github.com'}
    assert auth.child_env(provided, tmp_path, 'alice') == expected
    # No supplied token/config override: gh can still use its ordinary stored host configuration.
    base = {'HOME': provided['HOME'], 'XDG_CONFIG_HOME': provided['XDG_CONFIG_HOME']}
    assert auth.child_env(base, tmp_path, 'alice') == {**base, 'GH_HOST': 'github.com'}
    assert provided == {key: value for key, value in expected.items() if key != 'GH_HOST'}


@pytest.mark.parametrize('pattern,denied', [
    ('GITHUB_*', {'GITHUB_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'}),
    ('GH_ENTERPRISE_TOKEN', {'GH_ENTERPRISE_TOKEN'}),
    ('*ENTERPRISE_TOKEN', {'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'}),
])
@pytest.mark.parametrize('as_iterator', [False, True])
def test_host_mode_direct_deny_removes_optional_provided_auth(tmp_path, monkeypatch, pattern, denied, as_iterator):
    auth.configure(tmp_path, 'alice', 'host')
    monkeypatch.setattr(os, 'environ', {name: 'fake-global-' + name for name in HOST_AUTH_NAMES})
    provided = {name: 'fake-approved-' + name for name in HOST_AUTH_NAMES}
    result = auth.child_env(provided, tmp_path, 'alice', deny=iter([pattern]) if as_iterator else [pattern])
    assert all(name not in result for name in denied)
    assert result == {**{name: value for name, value in provided.items() if name not in denied}, 'GH_HOST': 'github.com'}
    assert all(name in provided for name in denied)


@pytest.mark.parametrize('pattern', ['GH_TOKEN', 'GH_*', 'GH_CONFIG_DIR', 'GH_HOST', 'GH_PROMPT_DISABLED'])
@pytest.mark.parametrize('as_iterator', [False, True])
def test_host_mode_required_environment_deny_still_fails_closed(tmp_path, pattern, as_iterator):
    auth.configure(tmp_path, 'alice', 'host')
    with pytest.raises(auth.AuthError, match='blocked by tools.env_deny'):
        auth.child_env({'HOME': '/fake/host'}, tmp_path, 'alice', deny=iter([pattern]) if as_iterator else [pattern])
