"""Owner-only files and folders for the exo home (transcripts, schedules, pid and lock files live there)."""
from __future__ import annotations

import contextlib
import errno
import os
import tempfile
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


def atomic_write(path: str | os.PathLike, data: bytes | str, mode: int | None = 0o600) -> Path:
    """Replace ``path`` with ``data`` through a temporary file from mkstemp in the same folder (which must exist). Its
    random name is opened with O_EXCL, so a symlink planted in the folder cannot redirect the write (#273). It gets
    ``mode`` (None: 0666 less the umask, as a plain open gives), is fsynced, renamed over ``path`` (a symlink there is
    replaced, not followed) and removed on any failure. The folder is fsynced after the rename."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            os.fchmod(fh.fileno(), 0o666 & ~_umask() if mode is None else mode)
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    with contextlib.suppress(OSError):
        dfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    return p


def _umask() -> int:
    """Read from /proc on Linux. Elsewhere the probe sets the strictest mask, so a file another thread creates
    meanwhile comes out tighter, never looser."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            return next(int(line.split()[1], 8) for line in fh if line.startswith("Umask:"))
    except (OSError, StopIteration, ValueError, IndexError):
        old = os.umask(0o077)
        os.umask(old)
        return old
