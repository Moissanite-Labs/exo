"""CoreRuntime.close() releases the databases it opened, and the spend journal closes its schema connection.

Without this, every engine a process opens and closes leaves sessions.sqlite (with its -wal and -shm files)
and schedule.sqlite open. The root suite then held about 900 handles by its end and failed at the 256-file
default of a macOS Terminal.
"""
import asyncio
import os
import sqlite3

import pytest

from exo.core.engine import CoreEngine
from exo.model_spend.journal import SpendJournal
from exo.scheduler import Scheduler

MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
needs_fd_dir = pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="counts handles through /dev/fd")


def _open_handles() -> int:
    return len(os.listdir("/dev/fd"))


def _engine(home, cwd):
    return CoreEngine(home, MODELS, cwd=str(cwd), env={}, attach_scheduler=Scheduler)


def test_close_releases_the_session_store_and_schedule_database(tmp_path):
    eng = _engine(tmp_path / "home", tmp_path)
    asyncio.run(eng.close())
    with pytest.raises(sqlite3.ProgrammingError):
        eng.store._db.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        eng.scheduler.db.execute("SELECT 1")


@needs_fd_dir
def test_engines_opened_and_closed_do_not_accumulate_handles(tmp_path):
    kept = []  # held like a long-lived owner would, so garbage collection cannot hide a missing close

    def cycle(i):
        eng = _engine(tmp_path / f"home{i}", tmp_path)
        asyncio.run(eng.close())
        kept.append(eng)

    cycle(0)
    before = _open_handles()
    for i in range(1, 11):
        cycle(i)
    assert _open_handles() - before < 5


@needs_fd_dir
def test_provisioning_a_spend_journal_leaves_no_connection_open(tmp_path):
    SpendJournal.provision(tmp_path / "warm.sqlite", "alice")
    before = _open_handles()
    for i in range(10):
        SpendJournal.provision(tmp_path / f"journal{i}.sqlite", "alice")
    assert _open_handles() - before < 3
