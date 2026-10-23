"""Profile commands inspect plans and write only the harness selection."""
import argparse
import json
import os
import subprocess
import sys

from exo.config import Config
from exo.harness_cmd import cmd_harness, register


def run(*args):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(), None)
    return cmd_harness(parser.parse_args(['harness', *args]))


def test_show_and_list_need_no_home_or_runtime(tmp_path, capsys):
    assert run('list') == 0
    output = capsys.readouterr().out
    assert 'lean:' in output and 'coding:' in output and 'full:' in output
    assert run('show', '--json') == 0
    assert json.loads(capsys.readouterr().out)['profile'] == 'legacy'
    assert not (tmp_path / 'exo-home').exists()


def test_use_dry_run_and_save_preserve_settings(tmp_path, capsys):
    cfg = Config({'models': {'roles': {'main': ['fake:echo']}}, 'unrelated': {'value': 42}})
    cfg.save()
    original = cfg.path.read_bytes()
    assert run('use', 'coding', '--dry-run') == 0
    assert 'Dry run' in capsys.readouterr().out
    assert cfg.path.read_bytes() == original
    assert run('use', 'lean') == 0
    saved = Config.load().data
    assert saved['harness'] == {'profile': 'lean'}
    assert saved['models'] == cfg.data['models'] and saved['unrelated'] == cfg.data['unrelated']
    assert saved.get('jobs') is None  # effective restrictions never get written over user config
    assert 'Restart Exo' in capsys.readouterr().out


def test_enable_disable_are_idempotent_and_use_resets_overrides(capsys):
    assert run('use', 'lean') == 0
    assert run('enable', 'jobs', 'skills') == 0
    assert run('enable', 'jobs') == 0
    assert Config.load().data['harness']['enable'] == ['jobs', 'skills']
    assert run('disable', 'jobs') == 0
    assert Config.load().data['harness'] == {'profile': 'lean', 'enable': ['skills'], 'disable': ['jobs']}
    assert run('enable', 'jobs') == 0
    assert 'disable' not in Config.load().data['harness']
    assert run('use', 'coding') == 0
    assert Config.load().data['harness'] == {'profile': 'coding'}


def test_invalid_graph_never_writes_config(capsys):
    assert run('use', 'coding') == 0
    cfg = Config.load()
    before = cfg.path.read_bytes()
    assert run('disable', 'memory') == 2
    assert 'required but disabled' in capsys.readouterr().err
    assert cfg.path.read_bytes() == before
    assert run('disable', 'gate') == 2
    assert cfg.path.read_bytes() == before
    assert run('enable', 'typo') == 2
    assert cfg.path.read_bytes() == before


def test_enable_from_legacy_opts_into_full_without_environment_becoming_persistent(monkeypatch, capsys):
    monkeypatch.setenv('EXO_PROFILE', 'lean')
    assert run('enable', 'skills') == 0
    assert Config.load().data['harness'] == {'profile': 'full', 'enable': ['skills']}
    assert 'EXO_PROFILE overrides' in capsys.readouterr().out
    assert run('show', '--json') == 0
    result = json.loads(capsys.readouterr().out)
    assert result['profile'] == 'lean' and result['capabilities'] == ['gate', 'skills']


def test_show_does_not_import_plugin_code_or_print_credentials(capsys):
    cfg = Config({'harness': {'profile': 'full'}, 'models': {'api_key': 'do-not-print-me'},
                  'mcp': {'service': {'env': {'PASSWORD': 'keep-private'}}}})
    cfg.save()
    result = subprocess.run([sys.executable, '-c',
        'import argparse,sys; from exo.harness_cmd import cmd_harness; '
        'cmd_harness(argparse.Namespace(harness_action="show", json=True)); '
        'assert "exo.core.plugins" not in sys.modules; '
        'assert "exo.core.engine" not in sys.modules'],
        capture_output=True, text=True, env=os.environ.copy(), timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'do-not-print-me' not in result.stdout and 'keep-private' not in result.stdout
    assert json.loads(result.stdout)['profile'] == 'full'
