"""`exo` imports a command's module only when that command runs (#260), and its help reads the same.

`build_parser(cmd)` gives only `cmd` its full parser and keeps a name and help line for the rest.
`build_parser()` builds every command for real. The two must print the same usage, the same
`exo --help` and the same help for every command and nested command.
"""
import argparse
import importlib
import inspect
import json
import subprocess
import sys

import pytest

from exo import cli

# Modules `exo.cli` imported at the top before #260, and the heavy packages behind them.
COMMAND_MODULES = (
    'exo.ledger', 'exo.store', 'exo.crypto', 'exo.ingest.cli', 'exo.ask', 'exo.eval', 'exo.brief',
    'exo.consolidate', 'exo.onboard', 'exo.upstream', 'exo.cli_chat', 'exo.models_cmd', 'exo.providers_cmd',
    'exo.decide_cmd', 'exo.trace_cmd', 'exo.memory_cmd', 'exo.baseline.cli',
    'exo.improvement.offline', 'httpx', 'nacl',
)

# Every name `from exo.cli import ...` could reach before #260, other than the functions still defined here.
OLD_NAMES = {
    'crypto': 'exo.crypto', 'KINDS': 'exo.ledger', 'Ledger': 'exo.ledger', 'LedgerError': 'exo.ledger',
    'PrincipalStore': 'exo.store', 'exo_home': 'exo.store', 'principal_root': 'exo.store',
    'register_ingest': 'exo.ingest.cli', 'cmd_ingest': 'exo.ingest.cli', 'register_ask': 'exo.ask',
    'cmd_ask': 'exo.ask', 'register_eval': 'exo.eval', 'cmd_eval': 'exo.eval', 'register_brief': 'exo.brief',
    'cmd_brief': 'exo.brief', 'register_consolidate': 'exo.consolidate', 'cmd_consolidate': 'exo.consolidate',
    'cmd_proposals': 'exo.consolidate', 'register_onboard': 'exo.onboard', 'add_init_args': 'exo.onboard',
    'onboard_init': 'exo.onboard', 'cmd_interview': 'exo.onboard', 'cmd_import_store': 'exo.onboard',
    'cmd_config': 'exo.onboard', 'register_upstream': 'exo.upstream', 'cmd_upstream': 'exo.upstream',
    'register_chat': 'exo.cli_chat', 'register_models': 'exo.models_cmd', 'cmd_models': 'exo.models_cmd',
    'register_providers': 'exo.providers_cmd', 'register_decide': 'exo.decide_cmd', 'cmd_decide': 'exo.decide_cmd',
    'register_trace': 'exo.trace_cmd', 'cmd_trace': 'exo.trace_cmd', 'cmd_findings': 'exo.trace_cmd',
    'register_memory': 'exo.memory_cmd', 'cmd_memory': 'exo.memory_cmd', 'register_taste': 'exo.baseline.cli',
    'cmd_taste': 'exo.baseline.cli',
}


def loaded_after(code: str) -> list[str]:
    """The command modules a fresh interpreter has loaded after running `code`."""
    probe = (f'import json, sys\n{code}\n'
             f'print(json.dumps([m for m in {list(COMMAND_MODULES)!r} if m in sys.modules]))')
    out = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.splitlines()[-1])


def run_main(*argv: str) -> str:
    return ('import contextlib, io\nfrom exo import cli\n'
            'with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):\n'
            f'    cli.main({list(argv)!r})')


@pytest.mark.parametrize('code', ['import exo.cli', run_main('--help'), run_main('nonsense'),
                                  run_main('find', '--help'), run_main('wire', '--help')],
                         ids=['import', 'help', 'unknown', 'find-help', 'wire-help'])
def test_import_and_help_load_no_command_module(code):
    assert loaded_after(code) == []


def test_version_loads_only_what_reading_the_version_needs():
    """`exo --version` reads the install record from config.json (which loads `exo.store`), and nothing more."""
    alone = loaded_after('from exo.update_cmd import version_string; version_string()')
    assert loaded_after(run_main('--version')) == alone


@pytest.mark.parametrize('cmd,own', [('models', 'exo.models_cmd'), ('ask', 'exo.ask'), ('log', 'exo.ledger'),
                                    ('improve', 'exo.improvement.offline')])
def test_a_command_loads_its_own_module_and_not_the_rest(cmd, own):
    alone = set(loaded_after(f'import {own}'))
    assert own in alone and set(loaded_after(run_main(cmd, '--help'))) == alone


def subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def command_paths(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    out = []
    for name, sub in subparsers(parser).items():
        out.append(path + (name,))
        out += command_paths(sub, path + (name,))
    return out


def walk(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> argparse.ArgumentParser:
    for name in path:
        parser = subparsers(parser)[name]
    return parser


FULL = cli.build_parser()
PATHS = command_paths(FULL)


def test_top_level_usage_and_help_need_no_command_module():
    lazy = cli.build_parser('--help')
    assert lazy.format_help() == FULL.format_help()
    assert lazy.format_usage() == FULL.format_usage()
    assert list(subparsers(lazy)) == list(subparsers(FULL))


@pytest.mark.parametrize('path', PATHS, ids=' '.join)
def test_each_command_help_is_the_same_as_the_full_parser(path):
    lazy = cli.build_parser(path[0])
    assert walk(lazy, path).format_help() == walk(FULL, path).format_help()
    assert lazy.format_usage() == FULL.format_usage()


def test_every_command_from_another_module_is_in_the_table():
    """A command added by a register() but missing from LAZY would vanish from the lazy parser."""
    local = {'init', 'find', 'get', 'ctx', 'links', 'lint', 'graph', 'index', 'keygen', 'keys', 'log',
             'web', 'stop', 'resume', 'tui', 'update', 'wire'}
    top = {p[0] for p in PATHS}
    assert top - local == set(cli.LAZY)
    for name, (_, handler, _) in cli.LAZY.items():
        assert callable(getattr(cli, handler)), name


def handler(name: str):
    """The function `main` runs for command `name`, or None for one it runs another way (stop, resume, update)."""
    return getattr(cli, cli.LAZY[name][1]) if name in cli.LAZY else getattr(cli, f'cmd_{name}', None)


# A handler whose second argument has a default runs with or without a store, so its signature cannot say which it
# gets. These pin the choice: `exo ingest` falls back to the store's ledger. `upstream`, `serve` and `chat` do not
# use one, and `decide`'s second argument is a model router, so it must never be handed the store.
OPTIONAL_STORE = {'ingest': True, 'upstream': False, 'serve': False, 'chat': False, 'decide': False}


@pytest.mark.parametrize('name', [n for n in subparsers(FULL) if handler(n)])
def test_a_command_gets_the_store_exactly_when_its_handler_takes_it(name):
    """`STORE_CMDS` is kept by hand (#260). A store command missing from it would be called without its store, and
    a name added by mistake would get a second argument its handler does not take."""
    params = list(inspect.signature(handler(name)).parameters.values())
    if len(params) == 2 and params[1].default is not inspect.Parameter.empty:
        assert name in OPTIONAL_STORE, f'say in OPTIONAL_STORE whether {name} gets the store'
        assert (name in cli.STORE_CMDS) is OPTIONAL_STORE[name]
    else:
        assert (name in cli.STORE_CMDS) is (len(params) == 2)


@pytest.mark.parametrize('name', sorted(OLD_NAMES))
def test_names_imported_here_before_still_resolve(name):
    home = importlib.import_module(OLD_NAMES[name])
    if name == 'crypto':
        assert cli.crypto is home
        return
    attr = 'register' if name.startswith('register_') else 'cmd_init' if name == 'onboard_init' else name
    assert getattr(cli, name) is getattr(home, attr)


def test_an_unknown_name_is_still_an_attribute_error():
    with pytest.raises(AttributeError):
        getattr(cli, 'no_such_name')
