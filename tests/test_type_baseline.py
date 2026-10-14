"""A baseline is a shrinking debt ledger, not an allowance for new errors."""
from collections import Counter
import json
import subprocess

import pytest

from scripts import check_types as check


def record(*, line=12, column=4, message='Incompatible types in assignment', code='assignment', **changes):
    item = {'file': 'src/exo/example.py', 'line': line, 'column': column,
            'message': message, 'code': code, 'severity': 'error'}
    return json.dumps({**item, **changes})


def test_source_movement_does_not_change_errors(tmp_path):
    before = check.parse_output(record(line=12), '', 1, tmp_path)
    after = check.parse_output(record(line=123, column=30), '', 1, tmp_path)
    assert check.differences(after, before) == (Counter(), Counter())
    before = check.parse_output(record(message='Name "value" already defined on line 7', code='no-redef'), '', 1, tmp_path)
    after = check.parse_output(record(line=100, message='Name "value" already defined on line 80', code='no-redef'), '', 1, tmp_path)
    assert check.differences(after, before) == (Counter(), Counter())


def test_counts_and_message_changes_are_not_hidden(tmp_path):
    expected = check.parse_output(record() + '\n' + record(line=15), '', 1, tmp_path)
    actual = check.parse_output(record() + '\n' + record(line=15) + '\n' + record(line=18), '', 1, tmp_path)
    new, resolved = check.differences(actual, expected)
    assert sum(new.values()) == 1 and not resolved
    changed = check.parse_output(record(message='Argument 1 has incompatible type "int"'), '', 1, tmp_path)
    new, resolved = check.differences(changed, expected)
    assert sum(new.values()) == 1 and sum(resolved.values()) == 2


@pytest.mark.parametrize('stdout,stderr,status', [
    ('mypy: compiler crashed', '', 2), ('', 'Traceback: internal error', 1),
    ('not json', '', 1), ('[]', '', 1), ('', '', 1), (record(), '', 0),
    (record(severity='warning'), '', 1), (record(code=None), '', 1),
    (record(message=''), '', 1), (record(file='../outside.py'), '', 1),
    (record(line=True), '', 1), (record(column=None), '', 1),
    (record(severity='note', file=None), '', 0),
])
def test_fatal_or_malformed_output_cannot_pass(tmp_path, stdout, stderr, status):
    with pytest.raises(check.CheckError):
        check.parse_output(stdout, stderr, status, tmp_path)


def test_notes_and_clean_output(tmp_path):
    assert not check.parse_output('', '', 0, tmp_path)
    assert not check.parse_output(record(severity='note', code=None), '', 0, tmp_path)


@pytest.fixture
def baseline_run(tmp_path, monkeypatch):
    expected = check.parse_output(record() + '\n' + record(line=15), '', 1, tmp_path)
    path = tmp_path / check.BASELINE
    check.write_baseline(path, expected)
    monkeypatch.setattr(check, 'check_environment', lambda: None)
    def run(actual, *args):
        monkeypatch.setattr(check, 'compile_errors', lambda root: actual)
        return check.main(['--root', str(tmp_path), *args])
    return run, path, expected


def test_check_requires_resolved_entries_to_be_pruned(baseline_run):
    run, path, expected = baseline_run
    old = path.read_bytes()
    assert run(expected) == 0
    assert run(Counter()) == 1
    assert path.read_bytes() == old
    assert run(Counter(), '--update') == 0
    assert json.loads(path.read_text())['diagnostics'] == []


def test_update_cannot_expand_or_replace_baseline(baseline_run):
    run, path, expected = baseline_run
    old = path.read_bytes()
    assert run(expected + expected, '--update') == 1
    assert path.read_bytes() == old
    assert run(Counter(), '--initialize') == 2
    assert path.read_bytes() == old


def test_fatal_compiler_never_rewrites_baseline(baseline_run, monkeypatch):
    _, path, _ = baseline_run
    old = path.read_bytes()
    def failed(root):
        raise check.CheckError('compiler failed')
    monkeypatch.setattr(check, 'compile_errors', failed)
    assert check.main(['--root', str(path.parents[1]), '--update']) == 2
    assert path.read_bytes() == old


@pytest.mark.parametrize('failure', ['python', 'version', 'dependency'])
def test_unreproducible_environment_is_rejected(monkeypatch, failure):
    monkeypatch.setattr(check.sys, 'version_info', (3, 13) if failure == 'python' else (3, 11))
    def version(name):
        if failure == 'dependency' and name == 'mcp':
            raise check.importlib.metadata.PackageNotFoundError(name)
        return 'old' if failure == 'version' else check.VERSION
    monkeypatch.setattr(check.importlib.metadata, 'version', version)
    with pytest.raises(check.CheckError):
        check.check_environment()


@pytest.mark.parametrize('change', ['boolean_count', 'duplicate', 'noncanonical', 'settings', 'boolean_schema'])
def test_invalid_baseline_cannot_pass(tmp_path, change):
    path = tmp_path / 'baseline.json'
    check.write_baseline(path, check.parse_output(record(), '', 1, tmp_path))
    data = json.loads(path.read_text())
    if change == 'boolean_count':
        data['diagnostics'][0]['count'] = True
    elif change == 'duplicate':
        data['diagnostics'].append(data['diagnostics'][0])
    elif change == 'noncanonical':
        data['diagnostics'][0]['path'] = './src/exo/example.py'
    elif change == 'boolean_schema':
        data['schema'] = True
    else:
        data['mypy'] = 'older'
    path.write_text(json.dumps(data))
    with pytest.raises(check.CheckError):
        check.load_baseline(path, tmp_path)


def test_compiler_runs_pinned_scope_without_writing_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv('MYPYPATH', '/untrusted/search')
    def compiler(argv, *, cwd, env, capture_output, text):
        assert cwd == tmp_path and capture_output and text
        assert '--config-file=' in argv
        assert argv[argv.index('--python-version') + 1] == '3.11'
        assert argv[argv.index('--platform') + 1] == 'linux'
        assert '--no-incremental' in argv
        assert argv[argv.index('--cache-dir') + 1] == check.os.devnull
        assert argv[-2:] == ['src/exo', 'clients/tui/exo_tui']
        assert '/untrusted/search' not in env['MYPYPATH']
        return subprocess.CompletedProcess(argv, 1, record(), '')
    monkeypatch.setattr(check.subprocess, 'run', compiler)
    assert sum(check.compile_errors(tmp_path).values()) == 1
    assert list(tmp_path.iterdir()) == []
