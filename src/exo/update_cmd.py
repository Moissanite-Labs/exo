"""`exo update`: preflight a clean checkout, upgrade, then check a fresh interpreter.

A clean tree is not a signature: remote commits and their installer/build code remain trusted.
A detached checkout (a canary moved with `git checkout --detach`) cannot pull. It fetches, moves to the tracked
remote branch only if that is a fast-forward (or `--force`), and rolls back if a later step fails (D-064).
"""
from __future__ import annotations

import os
import re
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
                'python': py, 'steps': steps, 'branch': install.get('branch') or ''}
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
    head = read(_git(root, 'symbolic-ref', '-q', 'HEAD'))
    if head.returncode not in (0, 1):
        raise ValueError('cannot tell whether the checkout is on a branch; nothing updated')
    if head.returncode == 1:  # detached: fetch and move instead of pull (see _move_detached)
        prev = read(_git(root, 'rev-parse', '--verify', 'HEAD^{commit}'))
        if prev.returncode or not (prev.stdout or '').strip():
            raise ValueError('cannot read the current commit; nothing updated')
        p['detached'] = (prev.stdout or '').strip()
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


REMOTE = 'origin'
_BRANCH_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]*')


def _targets(branch: str) -> list[str]:
    """Where a detached checkout goes: the recorded branch, else origin/HEAD, else origin/main."""
    if branch and (not _BRANCH_RE.fullmatch(branch) or '..' in branch or branch.endswith(('/', '.lock'))):
        raise ValueError(f'install record branch {branch!r} is not a plain branch name; nothing updated')
    if branch:
        return [branch if branch.startswith(REMOTE + '/') else f'{REMOTE}/{branch}']
    return [f'{REMOTE}/HEAD', f'{REMOTE}/main']


def _detached_target(p: dict, run: Runner, read: Runner) -> str:
    """Fetch once and resolve where a detached checkout goes (full sha). The signed check and the move share it."""
    if p.get('target'):
        return p['target']
    root = p['checkout']
    names = _targets(p.get('branch') or '')  # validate before the first network call
    if run(_git(root, 'fetch', '--quiet', REMOTE)).returncode:
        raise ValueError(f'git fetch {REMOTE} failed; nothing changed')
    for name in names:
        r = read(_git(root, 'rev-parse', '--verify', '--quiet', f'refs/remotes/{name}^{{commit}}'))
        if r.returncode == 0 and (r.stdout or '').strip():
            p['target'], p['target_name'] = (r.stdout or '').strip(), name if len(names) == 1 else 'remote default'
            return p['target']
    raise ValueError('no tracked branch to update to (tried ' + ', '.join(names) + '); nothing changed')


def _move_detached(p: dict, run: Runner, read: Runner, force: bool) -> str:
    """Refuse a non-fast-forward unless forced. Returns the target; the caller checks it out."""
    prev, target = p['detached'], _detached_target(p, run, read)
    ff = read(_git(p['checkout'], 'merge-base', '--is-ancestor', prev, target)).returncode
    if ff not in (0, 1):
        raise ValueError('cannot compare the current commit with the target; nothing changed')
    if ff == 1 and not force:
        raise ValueError(f'{target[:12]} does not contain the current commit {prev[:12]} (not a fast-forward); '
                         'nothing changed. Use --force to move anyway.')
    print(f'detached checkout: {prev[:12]} -> {target[:12]} ({p["target_name"]})')
    return target


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


def _rollback(p: dict, prev: str, run: Runner) -> None:
    """Put a detached checkout back on the commit it started from, and say what that does not undo."""
    try:
        ok = run(_git(p['checkout'], 'checkout', '--quiet', '--detach', prev)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        print(f'ROLLED BACK: the checkout is back on {prev[:12]}. Packages a failed install step changed may '
              'still be from the new commit; re-run `exo update` or scripts/install.sh after fixing the cause.')
    else:
        print(f'ROLLBACK FAILED: the checkout may not be on {prev[:12]}; run '
              f'`git -C {p["checkout"]} checkout --detach {prev}` by hand.')


def _restart_note() -> None:
    print('Restart required: finish active turns, close clients, run `exo stop`, then reopen '
          '`exo` or `exo web` to start the shared core with the updated code.')


def _pin_trust(args: list[str]) -> int:
    root = plan().get('checkout')
    if not root:
        print('exo update: --pin-trust needs a checkout install', file=sys.stderr)
        return 1
    from .update_verify import pin_trust
    return pin_trust(root, *args)


def _require_signed(p: dict, run: Runner, read: Runner) -> None:
    """Opt-in (`update.require_signed`): verify the exact commit the update will move to, before anything moves.

    On a branch that is the upstream tip, and the pull becomes a merge of exactly that commit. On a detached
    checkout it is the tracked remote branch `_move_detached` will check out (D-064); that move then uses the sha."""
    from . import update_verify
    if not update_verify.required():
        return
    if p['mode'] not in ('checkout', 'installer'):
        raise ValueError('update.require_signed needs a checkout install; nothing updated')
    if p.get('detached'):
        p['target'] = update_verify.verify_target(p['checkout'], ref=_detached_target(p, run, read))
        return
    sha = update_verify.verify_target(p['checkout'])
    p['steps'][0] = _git(p['checkout'], 'merge', '--ff-only', sha)


def cmd_update(argv: list[str], run: Runner = _run, *, read: Runner | None = None) -> int:
    if any(a in ('-h', '--help') for a in argv):
        print('usage: exo update [--dry-run | --force | --pin-trust [REV]]\nUpgrade Exo in place after a local preflight, '
              'then self-check.\nA detached checkout moves to the tracked remote branch; --force allows a non-fast-forward '
              'move.\n--pin-trust pins the signers list for update.require_signed (docs/INSTALL.md).\n'
              'Restart the shared core and clients after updating (see docs/INSTALL.md).')
        return 0
    if argv[:1] == ['--pin-trust'] and len(argv) <= 2:
        return _pin_trust(argv[1:])
    if any(a not in ('--dry-run', '-n', '--force') for a in argv):
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
        print('dry run: nothing changed (pip step uses uv pip if pip is missing; a detached checkout '
              f'fetches {REMOTE} and checks out the tracked branch instead of pulling)')
        return 0
    reader = read if read is not None else (_read if run is _run else run)
    changed, prev = False, ''
    try:
        _preflight(p, reader)
        _require_signed(p, run, reader)
        old = version_string(reader)
        print(f'exo update ({p["mode"]} install, now {old})')
        steps = p['steps']
        if p.get('detached'):
            target = _move_detached(p, run, reader, '--force' in argv)
            prev = p['detached']
            steps = [_git(p['checkout'], 'checkout', '--quiet', '--detach', target), *steps[1:]]
        for step in steps:
            # A failed pull/install can also leave partial changes. Never hide the restart need.
            changed = True
            if run(step).returncode:
                raise ValueError('step failed: ' + ' '.join(step))
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        print(f'exo update: {e}', file=sys.stderr)
        if changed and prev:
            _rollback(p, prev, run)
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
