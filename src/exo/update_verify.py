"""Signed-release check for `exo update` (issue #42, D-073). Off unless `update.require_signed` is true.

The target commit (the upstream tip) must carry an SSH signature from a key in an allowed-signers file.
That file is the *pinned copy* in `$EXO_HOME/update/allowed_signers`, never the checkout's own
`trust/allowed_signers`: a new commit cannot vouch for itself by editing the list. The pin is created and
changed only by `exo update --pin-trust [REV]`, which copies the list from REV (default HEAD, the code you
already run) and prints it. Verification is git's own (`git verify-commit` with ssh-keygen).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .store import exo_home

TRUST_PATH = 'trust/allowed_signers'  # in the repo; only ever a source for --pin-trust


def pin_file() -> Path:
    return exo_home() / 'update' / 'allowed_signers'


def required() -> bool:
    try:
        from .config import Config
        return Config.load().get('update.require_signed') is True
    except Exception:
        return False


def _git(root, *args: str) -> list[str]:
    return ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args]


def _out(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def pin_trust(root, rev: str = 'HEAD', read=_out) -> int:
    """Copy the trust list at REV into the pin and show it. Review it first: this is the trust decision."""
    r = read(_git(root, 'show', f'{rev}:{TRUST_PATH}'))
    if r.returncode or not (r.stdout or '').strip():
        print(f'exo update: no {TRUST_PATH} at {rev}', file=sys.stderr)
        return 1
    p = pin_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(r.stdout)
    tmp.replace(p)
    print(f'pinned {TRUST_PATH} from {rev} to {p}:\n{r.stdout.rstrip()}')
    return 0


def verify_target(root, read=_out, fetch=None) -> str:
    """Fetch, then verify the upstream tip against the pin. Returns its full sha or raises ValueError."""
    pin = pin_file()
    if not pin.is_file():
        raise ValueError('update.require_signed is on but no trusted signers are pinned; review '
                         f'{TRUST_PATH} and run `exo update --pin-trust`')
    if (fetch or read)(_git(root, 'fetch', '--quiet')).returncode:
        raise ValueError('git fetch failed; nothing updated')
    sha = read(_git(root, 'rev-parse', '--verify', '@{u}^{commit}'))
    if sha.returncode or not (sha.stdout or '').strip():
        raise ValueError('no upstream branch to verify; nothing updated')
    sha = sha.stdout.strip()
    v = read(_git(root, '-c', 'gpg.format=ssh', '-c', 'gpg.ssh.program=ssh-keygen',
                  '-c', f'gpg.ssh.allowedSignersFile={pin}', 'verify-commit', sha))
    if v.returncode:
        raise ValueError(f'update target {sha[:12]} is not signed by a pinned trusted key '
                         f'(bad or missing signature); nothing updated. {(v.stderr or "").strip()[:200]}')
    return sha
