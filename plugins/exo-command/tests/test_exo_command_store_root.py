"""The principal store comes from the core's rule, so a store made with `exo init --root` (recorded in config
`principals.<id>`) holds the decisions queue the panel reads, not an empty guess under $EXO_HOME/principals."""
import json

from exo_command import decisions
from exo_command.config import load_settings


def _home_with_root(tmp_path, monkeypatch, pid="p"):
    home, root = tmp_path / "home", tmp_path / "elsewhere" / pid
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"principal": pid, "principals": {pid: str(root)}}))
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_PRINCIPAL", pid)
    return home, root


def test_recorded_root_holds_the_decisions_queue(tmp_path, monkeypatch):
    home, root = _home_with_root(tmp_path, monkeypatch)
    decisions.append_event(root / "queues" / "decisions.jsonl", {"op": "open", "id": "dec-1", "title": "Ship it?"})
    s = load_settings()
    assert s.principal_store == root
    assert s.decisions_queue == root / "queues" / "decisions.jsonl"
    d = decisions.read_queue(s.decisions_queue)
    assert d["exists"] and [x["id"] for x in d["waiting"]] == ["dec-1"]


def test_explicit_settings_still_win(tmp_path, monkeypatch):
    _home_with_root(tmp_path, monkeypatch)
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(tmp_path / "env-store"))
    assert load_settings().principal_store == tmp_path / "env-store"
    s = load_settings({"principal_store": str(tmp_path / "given")})
    assert s.decisions_queue == tmp_path / "given" / "queues" / "decisions.jsonl"


def test_no_recorded_root_keeps_the_home_default(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    monkeypatch.setenv("EXO_PRINCIPAL", "p")
    assert load_settings().principal_store == tmp_path / "principals" / "p"


def test_a_bad_principal_id_names_no_store(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    monkeypatch.setenv("EXO_PRINCIPAL", "../outside")
    s = load_settings()
    assert s.principal_store is None and s.decisions_queue is None
