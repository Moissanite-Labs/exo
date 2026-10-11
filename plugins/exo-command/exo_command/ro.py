"""Read-only SQLite access and source discovery.

Every Hermes database is opened through ``open_ro``: a ``file:<path>?mode=ro`` URI plus
``PRAGMA query_only=ON``. The engine refuses writes twice over. When a WAL database cannot be
opened read-only (no ``-shm`` file and no permission to create one), the fallback adds
``immutable=1``: still read-only, but it skips the WAL, so the result is flagged as a snapshot.
Nothing in this package ever opens a Hermes database any other way.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple
from urllib.parse import quote

ROOT_PROFILE = "default"


def ro_uri(path: os.PathLike | str, immutable: bool = False) -> str:
    abs_path = os.path.abspath(os.path.expanduser(str(path)))
    uri = "file:{}?mode=ro".format(quote(abs_path, safe="/"))
    if immutable:
        uri += "&immutable=1"
    return uri


class RoConnection:
    """A read-only connection plus whether it had to fall back to an immutable snapshot."""

    def __init__(self, conn: sqlite3.Connection, snapshot: bool):
        self.conn = conn
        self.snapshot = snapshot

    def execute(self, sql: str, params: Sequence = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def close(self) -> None:
        self.conn.close()


def _connect(uri: str) -> sqlite3.Connection:
    if "mode=ro" not in uri:
        raise ValueError("refusing to open a Hermes database without mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    # Force a real read so a WAL file that cannot be mapped fails here, not mid-query.
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def open_ro(path: os.PathLike | str) -> RoConnection:
    if not os.path.exists(os.path.expanduser(str(path))):
        raise FileNotFoundError(str(path))
    try:
        return RoConnection(_connect(ro_uri(path)), snapshot=False)
    except sqlite3.OperationalError:
        return RoConnection(_connect(ro_uri(path, immutable=True)), snapshot=True)


@contextmanager
def ro(path: os.PathLike | str) -> Iterator[RoConnection]:
    c = open_ro(path)
    try:
        yield c
    finally:
        c.close()


def columns(conn: RoConnection, table: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute('PRAGMA table_info("{}")'.format(table.replace('"', "")))]
    except sqlite3.Error:
        return []


def select_cols(wanted: Sequence[str], have: Sequence[str]) -> List[str]:
    hs = set(have)
    return [c for c in wanted if c in hs]


# --------------------------------------------------------------------------- discovery

@dataclass
class ProfileDb:
    name: str
    path: Path


def discover_profiles(home: Path, only: Sequence[str] = (), include_root: bool = True) -> List[ProfileDb]:
    """``<home>/state.db`` as ``default`` plus ``<home>/profiles/<name>/state.db``, sorted.

    ``only`` filters by name when non-empty. Names are taken verbatim from the directory.
    """
    out: List[ProfileDb] = []
    want = set(only or [])
    root_db = home / "state.db"
    if include_root and root_db.is_file() and (not want or ROOT_PROFILE in want):
        out.append(ProfileDb(ROOT_PROFILE, root_db))
    pdir = home / "profiles"
    if pdir.is_dir():
        for entry in sorted(pdir.iterdir(), key=lambda p: p.name):
            db = entry / "state.db"
            if entry.is_dir() and db.is_file() and (not want or entry.name in want):
                out.append(ProfileDb(entry.name, db))
    return out


def discover_boards(home: Path) -> List[Tuple[str, Path, Optional[Path]]]:
    """``(slug, kanban.db, board.json or None)``: ``default`` at ``<home>/kanban.db``, others
    under ``<home>/kanban/boards/<slug>/``. Mirrors Hermes's board layout."""
    out: List[Tuple[str, Path, Optional[Path]]] = []
    root_db = home / "kanban.db"
    if root_db.is_file() and root_db.stat().st_size > 0:
        meta = home / "kanban" / "boards" / "default" / "board.json"
        out.append(("default", root_db, meta if meta.is_file() else None))
    bdir = home / "kanban" / "boards"
    if bdir.is_dir():
        for child in sorted(bdir.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name in ("default", "_archived") or child.name.startswith("."):
                continue
            db = child / "kanban.db"
            if db.is_file() and db.stat().st_size > 0:
                meta = child / "board.json"
                out.append((child.name, db, meta if meta.is_file() else None))
    return out
