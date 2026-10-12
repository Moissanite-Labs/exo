"""Owner-only files and folders for the exo home (transcripts, schedules, pid and lock files live there)."""
from __future__ import annotations

import os
from pathlib import Path


def private_dir(path: str | os.PathLike) -> Path:
    """Create ``path`` (and any missing parents) with mode 0700. A folder that already exists is left as it is."""
    p = Path(path)
    missing = []
    for d in (p, *p.parents):
        if d.exists():
            break
        missing.append(d)
    for d in reversed(missing):
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            pass
    return p


def private_file(path: str | os.PathLike) -> Path:
    """Make sure ``path`` exists with mode 0600 (an existing file is only tightened, never widened)."""
    p = Path(path)
    private_dir(p.parent)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_mode & 0o077:
            os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return p
