"""`exo update`: preflight a clean checkout, upgrade, then check a fresh interpreter.

A clean tree is not a signature: remote commits and their installer/build code remain trusted.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import display_version

# Both runners take argv only. Read runners return captured text stdout/stderr; mutation runners
# may stream output. Injecting `run` alone routes *all* subprocesses through it (including reads).
Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _read(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print('+ ' + ' '.join(cmd), flush=True)
    return subprocess.run(cmd, text=True)


def _git(root: str | Path, *args: str) -> list[str]:
    # Status must not execute a checkout's fsmonitor; pulls must not execute its hooks.
    return ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.untrackedCache=false', '-C', str(root), *args]


def checkout_root() -> Path | None:
    """The git checkout this code runs from, if any."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / '.git').exists() and (root / 'pyproject.toml').is_file() else None


def git_sha(root: Path | None, read: Runner = _read) -> str:
    if root is None or not (root / '.git').exists():
        return ''
    try:
        r = read(_git(root, 'rev-parse', '--short', 'HEAD'))
        return (r.stdout or '').strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def version_string(read: Runner = _read) -> str:
    root = checkout_root()
    checkout = read_install().get('checkout')
    if root is None and checkout:
        root = Path(checkout)
    sha = git_sha(root, read)
    shown = display_version()
    return f'{shown}, {sha}' if sha else shown


def read_install() -> dict:
    try:
        from .config import Config
        v = Config.load().get('install')
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def plan(install: dict | None = None, uv_tool: bool | None = None) -> dict:
    """Pure plan; pip is a placeholder until preflight chooses pip or uv pip."""
    install = read_install() if install is None else install
    checkout = install.get('checkout')
    root = Path(checkout) if checkout else checkout_root()
    if root and (root / '.git').exists():
        root = root.resolve()
        py = install.get('python') or sys.executable
        venv = install.get('venv')
        if venv:
            py = str(Path(venv) / 'bin' / 'python')
        steps = [_git(root, 'pull', '--ff-only'),
                 [py, '-I', '-m', 'pip', '--isolated', 'install', '-q',
                  *([] if checkout else ['-e']), f'{root}[tui]']]
        if checkout:
            # Install explicitly above so uv-created venvs do not need pip. The existing installer
            # refreshes plugins/config without trying pip a second time or starting an interview.
            steps.append(['env', 'GIT_CONFIG_COUNT=2', 'GIT_CONFIG_KEY_0=core.fsmonitor',
                          'GIT_CONFIG_VALUE_0=false', 'GIT_CONFIG_KEY_1=core.hooksPath',
                          'GIT_CONFIG_VALUE_1=/dev/null',
                          *([f'EXO_VENV={venv}'] if venv else []),
                          'bash', str(root / 'scripts' / 'install.sh'), '--no-pip',
                          '--no-interview', '--yes', '--python', py, '--exo-source', str(root)])
        return {'mode': 'installer' if checkout else 'checkout', 'checkout': str(root),
                'python': py, 'steps': steps}
    if checkout:
        return {'mode': 'unknown', 'steps': []}  # Never silently update a different checkout.
    if uv_tool is None:
        uv_tool = _is_uv_tool()
    if uv_tool:
        return {'mode': 'uv-tool', 'python': sys.executable, 'steps': [['uv', 'tool', 'upgrade', 'exo']]}
    return {'mode': 'unknown', 'steps': []}


def _is_uv_tool() -> bool:
    return f'{os.sep}uv{os.sep}tools{os.sep}exo{os.sep}' in sys.prefix + os.sep


def _preflight(p: dict, read: Runner) -> None:
    """No pulls or installs until every local prerequisite is checked."""
    source_env = {
        'PYTHONPATH', 'PYTHONHOME', 'EXO_PYTHON', 'EXO_VENV', 'BASH_ENV', 'ENV',
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_EXEC_PATH',
        'GIT_SSH', 'GIT_SSH_COMMAND', 'GIT_PROXY_COMMAND',
        'PIP_CONFIG_FILE', 'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_FIND_LINKS',
        'PIP_NO_INDEX', 'PIP_TRUSTED_HOST', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_ROOT',
        'PIP_REQUIREMENT', 'PIP_CONSTRAINT', 'PIP_EDITABLE',
        'UV_INDEX', 'UV_DEFAULT_INDEX', 'UV_INDEX_URL', 'UV_EXTRA_INDEX_URL',
        'UV_FIND_LINKS', 'UV_NO_INDEX', 'UV_CONFIG_FILE', 'UV_PROJECT',
        'UV_WORKING_DIR', 'UV_PYTHON', 'UV_PROJECT_ENVIRONMENT',
        'UV_CONSTRAINT', 'UV_BUILD_CONSTRAINT', 'UV_OVERRIDE', 'UV_INSECURE_HOST',
    }
    modifiers = sorted(k for k, v in os.environ.items() if v and
                       (k in source_env or k.startswith('GIT_CONFIG')))
    if modifiers:
        raise ValueError('unset update source/environment modifiers: ' + ', '.join(modifiers))
    if p['mode'] == 'uv-tool':
        if read(['uv', '--version']).returncode:
            raise ValueError('uv is unavailable; nothing updated')
        return
    root = p['checkout']
    status = read(_git(root, 'status', '--porcelain=v1', '--untracked-files=all'))
    if status.returncode or status.stdout is None:
        raise ValueError('cannot inspect checkout status; nothing updated')
    if status.stdout.strip():
        raise ValueError('checkout has local changes or untracked files; commit, stash or remove them first')
    ignored = read(_git(root, 'ls-files', '-z', '--others', '--ignored', '--exclude-standard',
                        '--', 'src', 'scripts', 'clients', 'plugins'))
    if ignored.returncode or ignored.stdout is None:
        raise ValueError('cannot inspect ignored source files; nothing updated')
    source_suffixes = {'.py', '.pyc', '.sh', '.toml', '.yaml', '.pth', '.so', '.dylib'}
    if any(Path(f).suffix in source_suffixes and '__pycache__' not in Path(f).parts
           for f in ignored.stdout.split('\0')):
        raise ValueError('checkout has ignored source files; remove or track them before updating')
    if p['mode'] == 'installer':
        script = Path(root) / 'scripts' / 'install.sh'
        if not script.is_file() or script.is_symlink():
            raise ValueError('checkout installer is missing or a symlink; nothing updated')
        if read(['bash', '--version']).returncode:
            raise ValueError('bash is unavailable; nothing updated')
    py = p['python']
    if read([py, '-I', '-m', 'pip', '--isolated', '--version']).returncode == 0:
        return
    try:
        uv_ok = read(['uv', '--no-config', 'pip', '--help']).returncode == 0
    except OSError:
        uv_ok = False
    if not uv_ok:
        raise ValueError('target Python has no pip and uv pip is unavailable; install uv before updating')
    p['steps'][1] = ['uv', '--no-config', 'pip', 'install', '--python', py,
                     *p['steps'][1][6:]]


def _remote_version(py: str, root: str | None, read: Runner = _read) -> str:
    """Ask a fresh interpreter: this process still has the old modules loaded."""
    try:
        r = read([py, '-I', '-c', 'import exo; print(exo.display_version())'])
        v = ((r.stdout or '').strip() or '?') if r.returncode == 0 else '?'
    except (OSError, subprocess.SubprocessError):
        v = '?'
    sha = git_sha(Path(root), read) if root else ''
    return f'{v}, {sha}' if sha else v


def self_check(py: str, run: Runner = _read) -> list[tuple[str, bool]]:
    """Import, protocol export is current, `exo models` answers. No network, no key needed."""
    checks = [
        ('import exo', [py, '-I', '-c', 'import exo, exo.cli']),
        ('protocol export is current', [py, '-I', '-m', 'exo.protocol.export', '--check']),
        ('exo models', [py, '-I', '-m', 'exo.cli', 'models']),
    ]
    out = []
    for name, cmd in checks:
        try:
            ok = run(cmd).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        out.append((name, ok))
    return out


def _restart_note() -> None:
    print('Restart required: finish active turns, close clients, run `exo stop`, then reopen '
          '`exo` or `exo web` to start the shared core with the updated code.')


def cmd_update(argv: list[str], run: Runner = _run, *, read: Runner | None = None) -> int:
    if any(a in ('-h', '--help') for a in argv):
        print('usage: exo update [--dry-run]\nUpgrade Exo in place after a local preflight, then self-check.\n'
              'Restart the shared core and clients after updating (see docs/INSTALL.md).')
        return 0
    if any(a not in ('--dry-run', '-n') for a in argv):
        print('exo update: unknown option (see --help)', file=sys.stderr)
        return 1
    p = plan()
    if p['mode'] == 'unknown':
        print('exo update: cannot tell how Exo was installed, or configured checkout is missing. '
              'Re-run scripts/install.sh from a checkout.', file=sys.stderr)
        return 1
    if '--dry-run' in argv or '-n' in argv:
        print(f'exo update ({p["mode"]} install, now {display_version()})')
        print('preflight: clean checkout, installer, target pip or uv pip; no probes in dry run')
        for step in p['steps']:
            print('+ ' + ' '.join(step))
        print('dry run: nothing changed (pip step uses uv pip if pip is missing)')
        return 0
    reader = read if read is not None else (_read if run is _run else run)
    changed = False
    try:
        _preflight(p, reader)
        old = version_string(reader)
        print(f'exo update ({p["mode"]} install, now {old})')
        for step in p['steps']:
            # A failed pull/install can also leave partial changes. Never hide the restart need.
            changed = True
            if run(step).returncode:
                raise ValueError('step failed: ' + ' '.join(step))
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        print(f'exo update: {e}', file=sys.stderr)
        if changed:
            print('Update may be incomplete; repair the failed step before resuming work.')
            _restart_note()
        return 1
    # Do not stop an active user's core. Loaded modules cannot be refreshed by a package install.
    _restart_note()
    py = p['python']
    new = _remote_version(py, p.get('checkout'), reader)
    print(f'exo: {old} -> {new}' if new != old else f'exo: already current ({new})')
    checks = self_check(py, run=reader)
    for name, ok in checks:
        print(f'  {"ok  " if ok else "FAIL"} {name}')
    return 1 if new.startswith('?') or not all(ok for _, ok in checks) else 0
