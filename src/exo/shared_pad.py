"""Isolated, owner-scoped freeform pad storage; not a runtime feature.

Callers must derive ``owner`` from a trusted identity, never model/tool input.
The owner is a namespace, not authentication. No session, transcript anchor,
automatic context import, execution, or engine/tool registration is involved.
An absent pad has revision 0; updates append complete plain-text snapshots.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

MAX_BYTES = 1024 * 1024
MAX_HISTORY = 100
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,159}")
_TABLE = """CREATE TABLE IF NOT EXISTS shared_pad_revisions (
    owner TEXT NOT NULL,
    pad TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    created TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (owner, pad, revision)
)"""


class PadValidationError(ValueError):
    """Invalid input; no revision has been written."""


class RevisionNotFound(LookupError):
    """The requested revision does not exist in this owner's namespace."""


class RevisionConflict(Exception):
    """CAS failure with the complete current snapshot from the transaction.

    ``current`` contains the document as well as its revision, including the
    revision-0/None snapshot when a caller expected a nonexistent revision.
    """

    def __init__(self, current: dict[str, Any]) -> None:
        self.current = current
        super().__init__(f"Pad revision conflict: current revision is {current['revision']}")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise PadValidationError(f"{label} must be 1-160 ASCII identifier characters")
    return value


def _integer(value: Any, label: str, minimum: int) -> int:
    if type(value) is not int or value < minimum or value > 2**63 - 1:
        raise PadValidationError(f"{label} must be an integer from {minimum} to {2**63 - 1}")
    return value


def _document(value: Any, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise PadValidationError("document must be plain text")
    # As in work_notebook, reject lone surrogates and non-text C0 controls.
    if any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 32 and c not in "\n\t" for c in value):
        raise PadValidationError("document contains invalid text characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise PadValidationError(f"document exceeds {max_bytes} UTF-8 bytes")
    return value


class SharedPadStorage:
    """Durable SQLite snapshots bound to one explicit owner.

    Each call owns and closes its connection. Independent instances/processes
    serialize compare-and-append with BEGIN IMMEDIATE, not an in-memory lock.
    History is newest-first and includes complete documents; empty text clears
    the pad without deleting its history. Database parent directories must exist.
    """

    def __init__(self, path: str | Path, *, owner: str, max_bytes: int = 32768) -> None:
        self._owner = _identifier(owner, "owner")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES:
            raise PadValidationError(f"max_bytes must be an integer from 1 to {MAX_BYTES}")
        if not isinstance(path, (str, Path)) or not str(path) or str(path) == ":memory:":
            raise PadValidationError("path must name a durable SQLite database")
        # Bind relative paths at construction, even if a caller later changes cwd.
        self._path = Path(path).absolute()
        self._max_bytes = max_bytes
        with self._connection() as db:
            db.execute(_TABLE)

    @property
    def owner(self) -> str:
        return self._owner

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()  # also rolls back an unfinished write on exceptions

    def _snapshot(self, pad: str, row: sqlite3.Row | None) -> dict[str, Any]:
        return {"owner": self.owner, "pad": pad,
                "revision": row["revision"] if row else 0,
                "created": row["created"] if row else None,
                "document": row["document"] if row else None}

    def _current(self, db: sqlite3.Connection, pad: str) -> dict[str, Any]:
        row = db.execute("SELECT revision, created, document FROM shared_pad_revisions "
                         "WHERE owner=? AND pad=? ORDER BY revision DESC LIMIT 1",
                         (self.owner, pad)).fetchone()
        return self._snapshot(pad, row)

    def read(self, pad: str, revision: int | None = None) -> dict[str, Any]:
        pad = _identifier(pad, "pad")
        if revision is not None:
            _integer(revision, "revision", 1)
        with self._connection() as db:
            if revision is None:
                return self._current(db, pad)
            row = db.execute("SELECT revision, created, document FROM shared_pad_revisions "
                             "WHERE owner=? AND pad=? AND revision=?",
                             (self.owner, pad, revision)).fetchone()
            if row is None:
                raise RevisionNotFound("pad revision does not exist")
            return self._snapshot(pad, row)

    def history(self, pad: str, limit: int = 10) -> list[dict[str, Any]]:
        pad = _identifier(pad, "pad")
        if type(limit) is not int or not 1 <= limit <= MAX_HISTORY:
            raise PadValidationError(f"history limit must be an integer from 1 to {MAX_HISTORY}")
        with self._connection() as db:
            rows = db.execute("SELECT revision, created, document FROM shared_pad_revisions "
                              "WHERE owner=? AND pad=? ORDER BY revision DESC LIMIT ?",
                              (self.owner, pad, limit)).fetchall()
            return [self._snapshot(pad, row) for row in rows]

    def update(self, pad: str, expected_revision: int, document: str) -> dict[str, Any]:
        pad = _identifier(pad, "pad")
        _integer(expected_revision, "expected_revision", 0)
        body = _document(document, self._max_bytes)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._current(db, pad)
            if current["revision"] != expected_revision:
                raise RevisionConflict(current)
            revision = _integer(expected_revision + 1, "new revision", 1)
            created = datetime.now(timezone.utc).isoformat()
            db.execute("INSERT INTO shared_pad_revisions "
                       "(owner, pad, revision, created, document) VALUES (?, ?, ?, ?, ?)",
                       (self.owner, pad, revision, created, body))
            db.commit()
            return {"owner": self.owner, "pad": pad, "revision": revision,
                    "created": created, "document": body}