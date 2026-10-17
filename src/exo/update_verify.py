"""Signed-release check for `exo update` (issue #42, D-073). Off unless `update.require_signed` is true.

The target commit (the upstream tip) must carry an SSH signature from a key in an allowed-signers file.
That file is the *pinned copy* in `$EXO_HOME/update/allowed_signers`, never the checkout's own
`trust/allowed_signers`: a new commit cannot vouch for itself by editing the list. The pin is created and
changed only by `exo update --pin-trust [REV]`, which copies the list from REV (default HEAD, the code you
already run) and prints it. Once a pin exists, a different list is taken only if the commit that set it in REV's
first-parent history verifies against the current pin; otherwise the pin is edited by hand.

Verification is git's own (`git verify-commit` with ssh-keygen), SSH signatures only. git picks the verify
backend from the signature's armor, not from `gpg.format`, so the OpenPGP and X.509 backends are switched off
and a commit without an SSH signature header is refused, whatever the local gpg keyring holds. A config.json
that exists but cannot be read refuses the update; it never reads as "off".
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .store import exo_home

TRUST_PATH = 'trust/allowed_signers'  # in the repo; only ever a source for --pin-trust
_SSH_SIG = re.compile(r'^gpgsig(-sha256)? -----BEGIN SSH SIGNATURE-----$', re.M)


def pin_file() -> Path:
    return exo_home() / 'update' / 'allowed_signers'


def required() -> bool:
    """`update.require_signed`. A missing config.json means off; one that cannot be read refuses (fails closed)."""
    from .config import Config
    try:
        on = Config.load().get('update.require_signed', False)
    except Exception as e:  # bad JSON, a newer version, unreadable
        raise ValueError(f'cannot read config.json, so update.require_signed is unknown ({e}); '
                         'nothing updated') from e
    if not isinstance(on, bool):
        raise ValueError(f'update.require_signed must be true or false, not {on!r}; nothing updated')
    return on


def _git(root, *args: str) -> list[str]:
    return ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args]


def _out(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def _verify(root, sha: str, pin: Path, read=_out) -> subprocess.CompletedProcess:
    """`git verify-commit SHA` against PIN only: an SSH signature header, and no gpg or gpgsm fallback."""
    head = read(_git(root, 'cat-file', 'commit', sha))
    if head.returncode or not _SSH_SIG.search((head.stdout or '').split('\n\n', 1)[0]):
        return subprocess.CompletedProcess([], 1, '', 'no SSH signature (OpenPGP and X.509 are not accepted)')
    return read(_git(root, '-c', 'gpg.format=ssh', '-c', 'gpg.ssh.program=ssh-keygen',
                     '-c', 'gpg.openpgp.program=false', '-c', 'gpg.x509.program=false',
                     '-c', f'gpg.ssh.allowedSignersFile={pin}', 'verify-commit', sha))


def pin_trust(root, rev: str = 'HEAD', read=_out) -> int:
    """Copy the trust list at REV into the pin and show it. Review it first: this is the trust decision."""
    if rev.startswith('-'):
        print(f'exo update: {rev!r} is not a revision', file=sys.stderr)
        return 1
    r = read(_git(root, 'show', f'{rev}:{TRUST_PATH}'))
    if r.returncode or not (r.stdout or '').strip():
        print(f'exo update: no {TRUST_PATH} at {rev}', file=sys.stderr)
        return 1
    p = pin_file()
    if p.is_file() and p.read_text() != r.stdout:  # a rotation: the commit that set the list must verify
        # --no-show-signature: a user's log.showSignature would print the signature check before the sha
        c = read(_git(root, 'log', '-1', '--first-parent', '--no-show-signature', '--format=%H', rev,
                      '--', TRUST_PATH))
        c = '' if c.returncode else (c.stdout or '').strip()
        if not (c and read(_git(root, 'show', f'{c}:{TRUST_PATH}')).stdout == r.stdout
                and _verify(root, c, p, read).returncode == 0):
            print(f'exo update: the {TRUST_PATH} at {rev} was not committed under a pinned key (commit '
                  f'{c[:12] or "?"} has no good signature from one); nothing pinned. If a pinned key is lost or '
                  f'compromised, edit {p} by hand (D-073).', file=sys.stderr)
            return 1
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
    v = _verify(root, sha, pin, read)
    if v.returncode:
        raise ValueError(f'update target {sha[:12]} is not signed by a pinned trusted key '
                         f'(bad or missing signature); nothing updated. {(v.stderr or "").strip()[:200]}')
    return sha
