"""Owner-only files and folders for the exo home (transcripts, schedules, pid and lock files live there)."""
from __future__ import annotations

import errno
import os
from pathlib import Path


def private_dir(path: str | os.PathLike) -> Path:
    """Create missing folders with mode 0700; preserve existing modes.

    Reject symlinks encountered while creating folders. This does not guard
    against every ancestor replacement race in an untrusted directory tree.
    """
    p = Path(path)
    missing = []
    for d in (p, *p.parents):
        if d.is_symlink():
            raise OSError(errno.ELOOP, "Refusing symlink directory", str(d))
        if d.exists():
            break
        missing.append(d)
    for d in reversed(missing):
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            if d.is_symlink() or not d.is_dir():
                raise
    return p


def private_file(path: str | os.PathLike) -> Path:
    """Create/tighten a file to owner-only access; reject a final symlink.

    The returned path may later be reopened by the caller, so this is not a
    race-resistant file handle API for an untrusted directory tree.
    """
    p = Path(path)
    private_dir(p.parent)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return p
