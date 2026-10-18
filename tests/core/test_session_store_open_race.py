"""#246: two processes opening one sessions.sqlite at once must both open it.

Each open adds the UPGRADES columns a new or older file lacks. Without one write transaction around the check and the
ALTER, both openers saw a column missing and the second ALTER failed with "duplicate column name"."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from exo.core import sessions
from exo.core.sessions import UPGRADES, SessionStore

real_connect = sqlite3.connect


def test_a_store_opened_while_another_open_is_midway_both_open(tmp_path, monkeypatch):
    path = tmp_path / "sessions.sqlite"
    real, paused, other_open = sqlite3.connect, threading.Event(), threading.Event()

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
            return real(*args, factory=PausesAfterFirstColumnCheck, **kwargs)
        return real(*args, **kwargs)

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
    db = real(path)
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
    real, busy = sqlite3.connect, [2]

    class BusyAtFirst(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode=WAL" and busy[0]:
                busy[0] -= 1
                raise sqlite3.OperationalError("database is locked")  # another opener is switching the new file
            return super().execute(sql, *args)

    monkeypatch.setattr(sessions.sqlite3, "connect", lambda *a, **k: real(*a, factory=BusyAtFirst, **k))
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)
    store = SessionStore(tmp_path / "sessions.sqlite")
    assert busy == [0]
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()


def test_a_switch_to_wal_that_stays_busy_gives_up_after_two_seconds(tmp_path, monkeypatch):
    real, tries, clock = sqlite3.connect, [0], [0.0]

    class AlwaysBusy(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode=WAL":
                tries[0] += 1
                clock[0] += 0.5  # each try already waited out part of the busy timeout
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    monkeypatch.setattr(sessions.sqlite3, "connect", lambda *a, **k: real(*a, factory=AlwaysBusy, **k))
    monkeypatch.setattr(sessions.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        SessionStore(tmp_path / "sessions.sqlite")
    assert tries[0] == 4  # the try that ends at 2.0 seconds is the last


def test_a_schema_that_fails_to_apply_leaves_the_file_unlocked(tmp_path):
    path = tmp_path / "sessions.sqlite"
    db = real_connect(path)
    db.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY)")  # an events table the schema's index cannot use
    db.commit()
    db.close()
    with pytest.raises(sqlite3.OperationalError):
        SessionStore(path)
    other = real_connect(path, timeout=0.1)
    other.execute("CREATE TABLE other (x)")  # would be "database is locked" if the failed open kept its write lock
    other.commit()
    other.close()
