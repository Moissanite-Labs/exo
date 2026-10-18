"""#246: two processes opening one sessions.sqlite at once must both open it.

Each open adds the UPGRADES columns a new or older file lacks. Without one write transaction around the check and the
ALTER, both openers saw a column missing and the second ALTER failed with "duplicate column name". A new file's switch
to WAL can also be busy without waiting, so it is tried again until a deadline, and a schema that fails to apply rolls
back so the file is not left locked."""

from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

import pytest

from exo.core import sessions
from exo.core.sessions import UPGRADES, SessionStore

real_connect = sqlite3.connect


def _clock(monkeypatch, now: list[float]) -> None:
    """Freeze the open's clock and skip its sleeps, for sessions.py only."""
    monkeypatch.setattr(sessions, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=lambda s: None))


def test_a_store_opened_while_another_open_is_midway_both_open(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite"
    paused, other_open = threading.Event(), threading.Event()

    class PausesAfterFirstColumnCheck(sqlite3.Connection):
        def execute(self, sql, *args):
            if not sql.startswith("PRAGMA table_info") or paused.is_set():
                return super().execute(sql, *args)
            rows = super().execute(sql, *args).fetchall()
            paused.set()
            other_open.wait(1)  # the other opener runs here; with the fix it waits for this one's transaction
            return rows

    first_call = [True]

    def connect(*args, **kwargs):
        if first_call[0]:
            first_call[0] = False
            return real_connect(*args, factory=PausesAfterFirstColumnCheck, **kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sessions.sqlite3, "connect", connect)
    errors: list[BaseException] = []

    def open_first() -> None:
        try:
            SessionStore(path).close()
        except BaseException as exc:  # noqa: BLE001 - the assertion below names it
            errors.append(exc)

    first = threading.Thread(target=open_first)
    first.start()
    assert paused.wait(5)
    SessionStore(path).close()
    other_open.set()
    first.join(10)
    assert not first.is_alive()
    assert errors == []
    db = real_connect(path)
    for table, col, _ in UPGRADES:
        assert col in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    db.close()


def test_reopening_a_store_keeps_its_rows(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store = SessionStore(path)
    sid = store.create_session(title="kept").id
    store.close()
    assert SessionStore(path).get_session(sid).title == "kept"


def test_a_busy_switch_to_wal_is_tried_again(tmp_path, monkeypatch):
    busy = [2]

    class BusyAtFirst(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode=WAL" and busy[0]:
                busy[0] -= 1
                raise sqlite3.OperationalError("database is locked")  # another opener is switching the new file
            return super().execute(sql, *args)

    monkeypatch.setattr(sessions.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=BusyAtFirst, **k))
    _clock(monkeypatch, [0.0])
    store = SessionStore(tmp_path / "sessions.sqlite")
    assert busy == [0]
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()


def test_a_switch_to_wal_that_stays_busy_gives_up_after_two_seconds(tmp_path, monkeypatch):
    tries, now = [0], [0.0]

    class AlwaysBusy(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode=WAL":
                tries[0] += 1
                now[0] += 0.5  # each try already waited out part of the busy timeout
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    monkeypatch.setattr(sessions.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=AlwaysBusy, **k))
    _clock(monkeypatch, now)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        SessionStore(tmp_path / "sessions.sqlite")
    assert tries[0] == 4  # the try that ends at 2.0 seconds is the last


def test_any_other_error_on_the_switch_to_wal_is_raised_at_once(tmp_path, monkeypatch):
    tries, now = [0], [0.0]

    class DiskError(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode=WAL":
                tries[0] += 1
                now[0] += 0.5  # a retry would still end at the deadline
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, *args)

    monkeypatch.setattr(sessions.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=DiskError, **k))
    _clock(monkeypatch, now)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        SessionStore(tmp_path / "sessions.sqlite")
    assert tries[0] == 1


def test_a_schema_that_fails_to_apply_leaves_the_file_unlocked(tmp_path):
    path = tmp_path / "sessions.sqlite"
    db = real_connect(path)
    db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY)")  # an events table the schema's index cannot use
    db.commit()
    db.close()
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        SessionStore(path)
    other = real_connect(path, timeout=0.1)
    other.execute("CREATE TABLE other (x)")  # would be "database is locked" if the failed open kept its write lock
    other.commit()
    other.close()


def test_an_open_that_cannot_get_the_write_lock_says_locked(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite"
    SessionStore(path).close()
    holder = real_connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # a live writer holds the file
    monkeypatch.setattr(sessions.sqlite3, "connect", lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.1}))
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):  # not "cannot rollback"
        SessionStore(path)
    holder.execute("ROLLBACK")
    holder.close()
