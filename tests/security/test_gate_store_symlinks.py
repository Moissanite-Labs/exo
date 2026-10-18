"""A symlink alias does not lift a store path out of the protected store (#130, D-038).

`is_store_path` resolves the candidate with `os.path.realpath`, so an alias planted anywhere on disk
still reads as the store path it points at. Every line here is classification data only; nothing runs it.
"""
import shlex

from exo import gate


def _store_write_floor(alias):
    quoted = shlex.quote(str(alias))
    calls = [
        {"tool": "bash", "args": {"command": f"printf x > {quoted}"}},
        {"tool": "write_file", "args": {"path": str(alias), "content": "x"}},
    ]
    for call in calls:
        c = gate.classify(call)
        assert c.category == "store_write", call
        assert gate.decide(c, {"gates": {"categories": {"store_write": "allow"}}}).outcome == "ask"


def test_file_alias_to_a_store_path_is_a_store_path(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    (home / "policies").mkdir(parents=True)
    (home / "policies" / "gates.yaml").write_text("{}")
    monkeypatch.setenv("EXO_HOME", str(home))
    alias = tmp_path / "alias.yaml"
    alias.symlink_to(home / "policies" / "gates.yaml")
    assert gate.is_store_path(str(alias))
    _store_write_floor(alias)


def test_directory_alias_and_leaves_inside_it(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    (home / "state").mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(home))
    alias = tmp_path / "alias-state"
    alias.symlink_to(home / "state", target_is_directory=True)
    for leaf in ("approval-grants.json", "new.json"):
        assert gate.is_store_path(str(alias / leaf))


def test_dangling_alias_into_the_store(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    (home / "state").mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(home))
    alias = tmp_path / "dangling"
    alias.symlink_to(home / "state" / "missing.json")
    assert gate.is_store_path(str(alias))


def test_outbound_symlink_keeps_lexical_protection(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    (home / "state").mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(home))
    outside = tmp_path / "ordinary.json"
    outside.write_text("{}")
    link = home / "state" / "outbound.json"
    link.symlink_to(outside)
    assert gate.is_store_path(str(link))


def test_ordinary_alias_outside_the_store_stays_ordinary(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    (home / "policies").mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(home))
    outside = tmp_path / "ordinary.yaml"
    outside.write_text("{}")
    alias = tmp_path / "alias.yaml"
    alias.symlink_to(outside)
    assert not gate.is_store_path(str(alias))
