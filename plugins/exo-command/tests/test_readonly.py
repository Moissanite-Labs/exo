import re
import sqlite3
from pathlib import Path

import pytest

from conftest import PLUGIN_DIR, digest_tree
from exo_command import ro
from exo_command.service import CommandCentre


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


def test_full_snapshot_leaves_every_source_byte_identical(world, settings, now):
    before = digest_tree(world["home"])
    q_before = world["queue"].read_bytes()
    snap = CommandCentre(settings).snapshot(now=now)
    assert all(snap[s]["ok"] for s in ("projects", "agents", "kanban", "decisions", "costs"))
    assert digest_tree(world["home"]) == before
    assert world["queue"].read_bytes() == q_before


def test_only_ro_module_opens_sqlite():
    offenders = []
    for path in (PLUGIN_DIR / "exo_command").glob("*.py"):
        if path.name == "ro.py":
            continue
        if re.search(r"sqlite3\.connect\(", path.read_text()):
            offenders.append(path.name)
    assert offenders == []
    for path in [PLUGIN_DIR / "dashboard" / "plugin_api.py", PLUGIN_DIR / "__init__.py"]:
        assert "sqlite3.connect" not in path.read_text()


def test_no_write_routes():
    text = (PLUGIN_DIR / "dashboard" / "plugin_api.py").read_text()
    assert not re.search(r"@router\.(post|put|patch|delete)", text)
