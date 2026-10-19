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


def atomic_write(path: str | os.PathLike, data: bytes | str, mode: int | None = 0o600, *, sync: bool = True) -> Path:
    """Replace ``path`` with ``data`` through a temporary file mkstemp makes in the same folder (which must exist).
    Its random name is opened with O_EXCL, so a symlink planted in the folder cannot redirect the write (#273). It
    gets ``mode`` (None: 0666 less the umask, as a plain open gives), is renamed over ``path`` (a symlink there is
    replaced, not followed) and is removed on any failure. ``sync`` fsyncs the file and the folder (ms on a disk)."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name[:60]}.", suffix=".tmp", dir=p.parent)  # fits any 255-byte name
    try:
        with os.fdopen(fd, "wb") as fh:
            try:
                os.fchmod(fh.fileno(), 0o666 & ~_umask() if mode is None else mode)
            except PermissionError:  # no unix modes here (vfat): keep the filesystem's own, as a plain open did
                if mode is not None:
                    raise
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
            if sync:
                fh.flush()
                os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if sync:
        with contextlib.suppress(OSError):
            dfd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    return p


def _umask() -> int:
    """From /proc on Linux. Elsewhere a probe sets the strictest mask for a moment: a file made meanwhile comes out
    tighter, never looser."""
    with contextlib.suppress(OSError, StopIteration, ValueError, IndexError), open("/proc/self/status") as fh:
        return next(int(ln.split()[1], 8) for ln in fh if ln.startswith("Umask:"))
    old = os.umask(0o077)
    os.umask(old)
    return old
