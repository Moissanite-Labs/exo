# Vendored from aska-digital/optchat-memory v1.0.0 (commit 8682354), the episodic layer of exo-memory.
# MIT License. Copyright (c) 2026 aska-digital. Full text: LICENSE-optchat in this directory.
# Changes for Exo are marked "exo:".
"""Append-only message log (OptChat spec section 2).

Layout: ``<root>/main/YYYY-MM-DD.jsonl`` — one JSON object per line::

    {"i": 0, "kind": "user", "text": "...", "size": 123, "date": "2026-10-04T...","session": "..."}

``i`` is the message's permanent global id. ``kind`` is one of ``user``
(the user's words), ``talk`` (the agent's replies), ``tool`` (tool calls:
name + JSON input), ``echo`` (tool results), ``work`` (a subagent's report),
``note`` (imported history).

Durability: each line is written with one ``write()`` + ``fsync()`` before
the call returns. Single writer per root: a non-blocking file lock on
``main/.lock`` — ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows —
is held for the process lifetime; a second process gets a loud ``LogLocked``
instead of a corrupted log (the spec uses a Unix socket for the same
purpose). Torn lines (a crash mid-write) are reported and skipped at load;
a file not ending in ``\\n`` gets one appended. Every log and lock file is
created owner-only (0600) so a shared home never exposes the transcript.

Never edit or delete: the log is history.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

KINDS = ("user", "talk", "tool", "echo", "work", "note")


if os.name == "nt":  # Windows has no fcntl; lock one byte with msvcrt instead.
    import msvcrt

    def _lock_exclusive_nb(handle) -> None:
        """Non-blocking exclusive lock. Raises OSError when held (Windows)."""
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:  # POSIX
    import fcntl

    def _lock_exclusive_nb(handle) -> None:
        """Non-blocking exclusive lock. Raises OSError when held (POSIX)."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _open_append(path, *, mode: str = "ab"):
    """Open (creating with 0600) and append; POSIX owner-only from the first byte."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.chmod(path, 0o600)  # force even when the file pre-existed under a looser mode
    except OSError:
        pass
    return os.fdopen(fd, mode)


class LogLocked(RuntimeError):
    """Another process already holds this log's write lock."""


class ChatLog:
    """The append-only log. Not thread-safe on its own — the provider serializes access."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.main_dir = self.root / "main"
        self.main_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.main_dir / ".lock"
        # "a+" never truncates an existing file, so a Windows byte-lock target
        # survives re-open; the lock file is also forced owner-only.
        self._lock_file = open(lock_path, "a+")
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        try:
            _lock_exclusive_nb(self._lock_file)
        except OSError as exc:
            self._lock_file.close()
            raise LogLocked(
                f"optchat log at {self.root} is already held by another process; "
                "one writer per memory root (see store.py)."
            ) from exc
        self._lock = RLock()
        # id -> (path, byte offset). Rebuilt by _scan().
        self._index: list[tuple[Path, int]] = []
        self._scan()

    # -- loading ---------------------------------------------------------

    def _day_files(self) -> list[Path]:
        return sorted(self.main_dir.glob("*.jsonl"))

    def _scan(self) -> None:
        """Rebuild the id index. Torn lines are reported and skipped."""
        self._index = []
        for path in self._day_files():
            with open(path, "rb") as fh:
                offset = 0
                for raw in fh:
                    line = raw.decode("utf-8", errors="replace")
                    if line.strip():
                        try:
                            obj = json.loads(line)
                            i = int(obj["i"])
                        except (ValueError, KeyError, TypeError) as exc:
                            logger.warning("optchat: torn log line skipped in %s: %s", path, exc)
                        else:
                            while len(self._index) <= i:
                                self._index.append(None)  # type: ignore[arg-type]
                            self._index[i] = (path, offset)
                    offset += len(raw)
            # A file not ending in newline gets one, so the next write starts clean.
            with open(path, "r+b") as fh:
                fh.seek(0, 2)
                if fh.tell() > 0:
                    fh.seek(-1, 2)
                    if fh.read(1) != b"\n":
                        fh.write(b"\n")

    # -- writing ---------------------------------------------------------

    def _today_path(self) -> Path:
        return self.main_dir / (datetime.now().astimezone().strftime("%Y-%m-%d") + ".jsonl")

    def append(self, kind: str, text: str, session: str = "", date: Optional[str] = None) -> int:
        """Append one message; returns its permanent id."""
        if kind not in KINDS:
            raise ValueError(f"unknown log kind: {kind!r}")
        with self._lock:
            i = len(self._index)
            obj = {
                "i": i,
                "kind": kind,
                "text": text,
                "size": len(f"{kind}: {text}".encode("utf-8")),
                "date": date or datetime.now().astimezone().isoformat(),
                "session": session,
            }
            path = self._today_path()
            with _open_append(path) as fh:
                offset = os.lseek(fh.fileno(), 0, os.SEEK_END)
                fh.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            self._index.append((path, offset))
            return i

    # -- reading ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._index)

    def read(self, i: int) -> dict:
        """Read message ``i`` whole.

        Raises IndexError when absent — including ids lost to a torn write
        (the index keeps a gap so later ids stay aligned; callers must not
        see a TypeError from unpacking the gap).
        """
        with self._lock:
            entry = self._index[i] if 0 <= i < len(self._index) else None
        if entry is None:
            raise IndexError(f"optchat: message {i} not in log")
        path, offset = entry
        with open(path, "rb") as fh:
            fh.seek(offset)
            return json.loads(fh.readline().decode("utf-8"))

    def iter_all(self) -> Iterator[dict]:
        for i in range(len(self)):
            try:
                yield self.read(i)
            except IndexError:
                continue  # id lost to a torn write; ids after it stay aligned

    def close(self) -> None:
        with self._lock:
            try:
                _unlock(self._lock_file)
            finally:
                self._lock_file.close()
