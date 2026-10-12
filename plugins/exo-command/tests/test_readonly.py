"""The Hermes importer stays read-only. These tests cover ``exo.ingest.hermes_dashboard``."""
import sqlite3
from pathlib import Path

import pytest

from conftest import PLUGIN_DIR, digest_tree
from exo.ingest import hermes_dashboard as ro


def test_uri_is_read_only(tmp_path):
    assert ro.ro_uri(tmp_path / "x.db").endswith("?mode=ro")
    assert "mode=ro&immutable=1" in ro.ro_uri(tmp_path / "x.db", immutable=True)


def test_engine_refuses_writes(world):
    with ro.ro(world["home"] / "state.db") as c:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("UPDATE sessions SET title='x'")
        with pytest.raises(sqlite3.OperationalError):
            c.execute("CREATE TABLE evil (x)")


def test_missing_db_is_not_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        ro.open_ro(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()


def test_every_importer_reader_leaves_sources_byte_identical(world, now):
    before = digest_tree(world["home"])
    profs = ro.discover_profiles(world["home"])
    ro.live_agents(profs, now=now)
    ro.kanban_tasks(world["home"])
    ro.cost_per_session(profs, now=now)
    assert digest_tree(world["home"]) == before


def test_plugin_never_opens_sqlite():
    """The command centre reads the protocol. Only the importer opens a database, and only read-only."""
    offenders = [p.name for p in PLUGIN_DIR.rglob("*.py") if "tests" not in p.parts
                 and "sqlite3.connect" in p.read_text()]
    assert offenders == []
    importer = Path(ro.__file__).read_text()
    assert importer.count("sqlite3.connect(") == 1 and "mode=ro" in importer
