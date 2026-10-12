"""A fresh exo home and the files in it are owner-only."""
import stat
import sqlite3
import subprocess
import sys

from exo.core import agentlog
from exo.core.private import private_dir, private_file
from exo.core.scheduler import Scheduler
from exo.core.sessions import SessionStore


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


class _Eng:
    def __init__(self, home):
        self.home = home


def test_fresh_home_is_private(tmp_path):
    home = tmp_path / "newhome"  # does not exist yet
    SessionStore(home / "sessions.sqlite")
    Scheduler(_Eng(home))
    fh = agentlog.claim_writer(home, "default")
    try:
        assert mode(home) == 0o700
        assert mode(home / "run") == 0o700
        for p in (home / "sessions.sqlite", home / "schedule.sqlite", home / "run" / "agent-default.lock"):
            assert mode(p) == 0o600, p
    finally:
        agentlog.release_writer(fh)


def test_private_file_tightens_but_keeps_existing_dir(tmp_path):
    tmp_path.chmod(0o755)
    f = tmp_path / "x.sqlite"
    f.write_text("")
    f.chmod(0o644)
    private_file(f)
    assert mode(f) == 0o600
    assert mode(tmp_path) == 0o755  # an existing folder is not touched


def test_private_dir_makes_parents_private(tmp_path):
    private_dir(tmp_path / "a" / "b")
    assert mode(tmp_path / "a") == 0o700 and mode(tmp_path / "a" / "b") == 0o700
