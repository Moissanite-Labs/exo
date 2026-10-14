"""Absolute symlink aliases retain the protected store approval floor."""
import shlex

import pytest

from exo import gate


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / "store"
    (home / "state").mkdir(parents=True)
    (home / "state" / "custom.json").write_text("{}")
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    return home


def assert_floor(path, deletion=False):
    quoted = shlex.quote(str(path))
    commands = [f"printf x > {quoted}", f"touch {quoted}"]
    if deletion:
        commands = [f"rm -rf {quoted}"]
    calls = [{"tool": "bash", "args": {"command": c}} for c in commands]
    if not deletion:
        calls.append({"tool": "write_file", "args": {"path": str(path), "content": "x"}})
    for call in calls:
        classified = gate.classify(call)
        assert classified.category == "store_write", call
        assert gate.decide(classified, {"gates": {"categories": {"store_write": "allow"}}}).outcome == "ask"


def test_file_parent_and_dangling_aliases(store, tmp_path):
    alias = tmp_path / "innocent"
    alias.symlink_to(store / "state" / "custom.json")
    assert_floor(alias)
    parent = tmp_path / "folder"
    parent.symlink_to(store / "state", target_is_directory=True)
    for leaf in ("custom.json", "new.json"):
        assert_floor(parent / leaf)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(store / "state" / "missing.json")
    assert_floor(dangling)


def test_lexical_protection_survives_outbound_symlink(store, tmp_path):
    external = tmp_path / "ordinary.json"
    external.write_text("{}")
    alias = store / "state" / "outbound.json"
    alias.symlink_to(external)
    assert_floor(alias)


def test_symlink_dotdot_uses_filesystem_order(store, tmp_path):
    (store / "ordinary").mkdir()
    alias = tmp_path / "hop"
    alias.symlink_to(store / "ordinary", target_is_directory=True)
    assert_floor(f"{alias}/../state/new.json")


def test_root_and_ancestor_deletion_aliases(store, tmp_path):
    root = tmp_path / "root-alias"
    root.symlink_to(store, target_is_directory=True)
    assert_floor(root, deletion=True)
    ancestor = tmp_path / "ancestor-alias"
    ancestor.symlink_to(tmp_path, target_is_directory=True)
    assert_floor(ancestor, deletion=True)
    assert_floor(root / "state" / "custom.json", deletion=True)


def test_custom_principal_and_nested_principal_aliases(store, tmp_path, monkeypatch):
    custom = tmp_path / "custom"
    (custom / "policies").mkdir(parents=True)
    monkeypatch.setenv("EXO_PRINCIPAL_STORE", str(custom))
    nested = store / "principals" / "alice"
    (nested / "state").mkdir(parents=True)
    for i, target in enumerate((custom / "policies", nested / "state")):
        alias = tmp_path / f"principal-alias-{i}"
        alias.symlink_to(target, target_is_directory=True)
        assert_floor(alias / "ordinary.json")
    alias = tmp_path / "principal-root"
    alias.symlink_to(nested, target_is_directory=True)
    assert_floor(alias, deletion=True)


def test_resolved_distinctive_names_and_home_alias(store, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "seat.toml").write_text("")
    alias = tmp_path / "plain"
    alias.symlink_to(outside / "seat.toml")
    assert_floor(alias)
    monkeypatch.setenv("HOME", str(tmp_path))
    home_alias = tmp_path / "home-alias"
    home_alias.symlink_to(tmp_path, target_is_directory=True)
    assert_floor(home_alias / "store" / "state" / "custom.json")


def test_normal_outside_paths_and_relative_alias_remain_unprotected(store, tmp_path):
    outside = tmp_path / "ordinary"
    outside.mkdir()
    alias = tmp_path / "outside-alias"
    alias.symlink_to(outside, target_is_directory=True)
    assert not gate.is_store_path(str(alias / "new.json"))
    assert not gate.is_store_path("outside-alias/new.json")
    classified = gate.classify({"tool": "write_file", "args": {"path": str(alias / "new.json"), "content": "x"}})
    assert classified.category == "local_write"


def test_configured_root_alias_and_dotdot(store, tmp_path, monkeypatch):
    (store / "ordinary").mkdir()
    alias = tmp_path / "configured"
    alias.symlink_to(store / "ordinary", target_is_directory=True)
    monkeypatch.setenv("EXO_HOME", f"{alias}/..")
    assert_floor(store / "state" / "new.json")
    assert_floor(f"{alias}/../state/new.json")
