"""The scheduler database is part of the protected Exo store."""
from exo import gate


def test_schedule_database_and_sqlite_sidecars_are_store_paths(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    monkeypatch.setenv("EXO_HOME", str(home))
    for suffix in ("", "-journal", "-wal", "-shm"):
        assert gate.is_store_path(str(home / f"schedule.sqlite{suffix}"))


def test_sqlite_shell_write_to_schedule_store_asks(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    monkeypatch.setenv("EXO_HOME", str(home))
    command = f"sqlite3 {home / 'schedule.sqlite'} 'create table jobs (id text)'"
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    assert c.category == "store_write"
    assert gate.decide(c, {"gates": {"categories": {"store_write": "allow"}}}).outcome == "ask"


def test_redirection_and_file_tool_writes_to_schedule_sidecars_ask(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    monkeypatch.setenv("EXO_HOME", str(home))
    sidecar = home / "schedule.sqlite-wal"
    redirected = gate.classify({"tool": "bash", "args": {"command": f"printf x > {sidecar}"}})
    direct = gate.classify({"tool": "write_file", "args": {"path": str(sidecar), "content": "x"}})
    assert redirected.category == direct.category == "store_write"
    assert gate.decide(direct, {"gates": {"categories": {"store_write": "allow"}}}).outcome == "ask"
