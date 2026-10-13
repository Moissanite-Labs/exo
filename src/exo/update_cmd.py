"""`exo update`: upgrade this install in place, print old -> new, then a quick self-check.

Which install is this?
  1. scripts/install.sh wrote `install` into config.json (venv, checkout, python): git pull the checkout,
     then re-run its install.sh, which reinstalls into the same venv and refreshes the plugins.
  2. Exo runs from a git checkout (pip install -e, or PYTHONPATH): git pull, then pip install -e again.
  3. Exo is a `uv tool` install: `uv tool upgrade exo`.
Anything else: say so and point at scripts/install.sh. Keys and principal stores are never touched.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import __version__

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def checkout_root() -> Path | None:
    """The git checkout this code runs from, if any."""
    root = Path(__file__).resolve().parents[2]
    if (root / '.git').exists() and (root / 'pyproject.toml').is_file():
        return root
    return None


def git_sha(root: Path | None) -> str:
    if root is None or not (root / '.git').exists():
        return ''
    try:
        r = subprocess.run(['git', '-C', str(root), 'rev-parse', '--short', 'HEAD'], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def version_string() -> str:
    root = checkout_root()
    if root is None and read_install().get('checkout'):
        root = Path(read_install()['checkout'])  # a venv install made from a checkout
    sha = git_sha(root)
    return f'{__version__} ({sha})' if sha else __version__


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    print('+ ' + ' '.join(cmd), flush=True)
    return subprocess.run(cmd, text=True)


def read_install() -> dict:
    try:
        from .config import Config
        v = Config.load().get('install')
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def plan(install: dict | None = None, uv_tool: bool | None = None) -> dict:
    """What `exo update` would do. {'mode': ..., 'steps': [argv, ...]} or {'mode': 'unknown', 'steps': []}."""
    install = read_install() if install is None else install
    checkout = install.get('checkout')
    py = install.get('python') or sys.executable
    if checkout and Path(checkout, '.git').exists():
        script = str(Path(checkout) / 'scripts' / 'install.sh')
        return {'mode': 'installer', 'checkout': checkout, 'python': py, 'steps': [
            ['git', '-C', checkout, 'pull', '--ff-only'],
            ['bash', script, '--no-interview', '--yes', '--exo-source', checkout]]}
    root = checkout_root()
    if root:
        return {'mode': 'checkout', 'checkout': str(root), 'python': sys.executable, 'steps': [
            ['git', '-C', str(root), 'pull', '--ff-only'],
            [sys.executable, '-m', 'pip', 'install', '-q', '-e', f'{root}[tui]']]}
    if uv_tool is None:
        uv_tool = _is_uv_tool()
    if uv_tool:
        return {'mode': 'uv-tool', 'python': sys.executable, 'steps': [['uv', 'tool', 'upgrade', 'exo']]}
    return {'mode': 'unknown', 'steps': []}


def _is_uv_tool() -> bool:
    return f'{os.sep}uv{os.sep}tools{os.sep}exo{os.sep}' in sys.prefix + os.sep


def _remote_version(py: str, root: str | None) -> str:
    """Version of the install after the upgrade, asked of a fresh interpreter (this one is stale)."""
    code = 'import exo; print(exo.__version__)'
    try:
        r = subprocess.run([py, '-c', code], capture_output=True, text=True, timeout=30)
        v = r.stdout.strip() if r.returncode == 0 else '?'
    except (OSError, subprocess.SubprocessError):
        v = '?'
    sha = git_sha(Path(root)) if root else ''
    return f'{v} ({sha})' if sha else v


def self_check(py: str, run: Runner = _run) -> list[tuple[str, bool]]:
    """Import, protocol export is current, `exo models` answers. No network, no key needed."""
    checks = [
        ('import exo', [py, '-c', 'import exo, exo.cli']),
        ('protocol export is current', [py, '-m', 'exo.protocol.export', '--check']),
        ('exo models', [py, '-m', 'exo.cli', 'models']),
    ]
    out = []
    for name, cmd in checks:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        out.append((name, ok))
    return out


def cmd_update(argv: list[str], run: Runner = _run) -> int:
    dry = '--dry-run' in argv or '-n' in argv
    if any(a in ('-h', '--help') for a in argv):
        print('usage: exo update [--dry-run]\nUpgrade Exo in place, print old -> new, then run a quick self-check.')
        return 0
    p = plan()
    if p['mode'] == 'unknown':
        print("exo update: cannot tell how Exo was installed. Re-run scripts/install.sh from a checkout, "
              "or: uv tool install --reinstall 'exo[tui] @ git+https://github.com/aska-digital/exo'",
              file=sys.stderr)
        return 1
    old = version_string()
    print(f'exo update ({p["mode"]} install, now {old})')
    if dry:
        for step in p['steps']:
            print('+ ' + ' '.join(step))
        print('dry run: nothing changed')
        return 0
    for step in p['steps']:
        if run(step).returncode != 0:
            print(f'exo update: step failed: {" ".join(step)}', file=sys.stderr)
            return 1
    install = read_install()
    py = install.get('python') or p.get('python') or sys.executable
    venv = install.get('venv')
    if venv and Path(venv, 'bin', 'python').exists():
        py = str(Path(venv, 'bin', 'python'))
    new = _remote_version(py, p.get('checkout'))
    print(f'exo: {old} -> {new}' if new != old else f'exo: already current ({new})')
    bad = 0
    for name, ok in self_check(py):
        print(f'  {"ok  " if ok else "FAIL"} {name}')
        bad += not ok
    return 1 if bad else 0
