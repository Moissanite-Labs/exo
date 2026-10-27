"""Selected plugin plans never import invalid dependents; resources have a final owner."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from exo.core.plugins import Manifest, PluginManager


def plugin(root, name, requires=None, body="def register(ctx):\n    pass\n"):
    folder = root / name
    folder.mkdir()
    manifest = f"name: {name}\n"
    if requires is not None:
        manifest += f"requires: {json.dumps(requires)}\n"
    (folder / "plugin.yaml").write_text(manifest)
    marker = root / f"{name}.imported"
    (folder / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + body)
    return folder, marker


def test_dependencies_load_before_dependents_and_shutdown_in_reverse(tmp_path):
    output = tmp_path / "order"
    body = lambda name: (
        "def register(ctx):\n"
        f"    with open({str(output)!r}, 'a') as f: f.write('start {name}\\n')\n"
        "    def close():\n"
        f"        with open({str(output)!r}, 'a') as f: f.write('close {name}\\n')\n"
        "    ctx.register_cleanup(close)\n")
    plugin(tmp_path, "a-child", ["z-base"], body("child"))
    plugin(tmp_path, "z-base", body=body("base"))
    manager = PluginManager()
    records = manager.load_all([tmp_path], entry_points=False)
    assert [r.manifest.name for r in records] == ["z-base", "a-child"]
    assert all(r.ok for r in records)
    manager.close()
    manager.close()
    assert output.read_text().splitlines() == ["start base", "start child", "close child", "close base"]


@pytest.mark.parametrize("selection", [{"enabled": ["child"]}, {"disabled": ["base"]}])
def test_excluded_dependencies_stay_unimported(tmp_path, selection):
    _, child = plugin(tmp_path, "child", ["base"])
    _, base = plugin(tmp_path, "base")
    manager = PluginManager()
    [rec] = manager.load_all([tmp_path], entry_points=False, **selection)
    assert not rec.ok and "disabled or excluded" in rec.error
    assert not child.exists() and not base.exists()


def test_empty_allowlist_imports_nothing(tmp_path):
    _, marker = plugin(tmp_path, "p")
    assert PluginManager().load_all([tmp_path], enabled=[], entry_points=False) == []
    assert not marker.exists()


def test_missing_dependency_is_reported_before_import(tmp_path):
    _, marker = plugin(tmp_path, "child", ["missing"])
    manager = PluginManager()
    [rec] = manager.load_all([tmp_path], entry_points=False)
    assert not rec.ok and "missing" in rec.error and not marker.exists()
    assert manager.errors() == {"child": rec.error}


@pytest.mark.parametrize("requires", ["base", 17, [""], [" base"], [False], {"base": True}])
def test_bad_requires_is_reported_before_import(tmp_path, requires):
    _, marker = plugin(tmp_path, "bad", requires)
    [rec] = PluginManager().load_all([tmp_path], entry_points=False)
    assert not rec.ok and "requires must be a list" in rec.error
    assert not marker.exists()


def test_cycle_and_dependents_fail_but_independent_plugin_loads(tmp_path):
    marks = [plugin(tmp_path, name, requires)[1] for name, requires in
             [("a", ["b"]), ("b", ["a"]), ("c", ["a"]), ("d", [])]]
    manager = PluginManager()
    records = {r.manifest.name: r for r in manager.load_all([tmp_path], entry_points=False)}
    assert set(records) == {"a", "b", "c", "d"}
    assert "dependency cycle: a -> b -> a" in records["a"].error
    assert "dependency cycle" in records["b"].error and not records["c"].ok
    assert records["d"].ok
    assert [m.exists() for m in marks] == [False, False, False, True]


def test_failed_dependency_does_not_import_dependent(tmp_path):
    _, child = plugin(tmp_path, "child", ["base"])
    _, base = plugin(tmp_path, "base", body="raise RuntimeError('broken base')\n")
    records = {r.manifest.name: r for r in PluginManager().load_all([tmp_path], entry_points=False)}
    assert "broken base" in records["base"].error and "failed" in records["child"].error
    assert base.exists() and not child.exists()


def test_direct_load_requires_previously_loaded_dependencies(tmp_path):
    base, _ = plugin(tmp_path, "base")
    child, marker = plugin(tmp_path, "child", ["base"])
    rejected = PluginManager().load_folder(child)
    assert not rejected.ok and not marker.exists()
    manager = PluginManager()
    assert manager.load_folder(base).ok
    assert manager.load_folder(child).ok


def test_failed_register_releases_resources_and_rolls_back(tmp_path, caplog):
    events = []
    manager = PluginManager()

    def register(ctx):
        ctx.register_command("temporary", lambda: "ok")
        ctx.register_cleanup(lambda: events.append("first"))
        ctx.register_cleanup(lambda: 1 / 0)
        ctx.register_cleanup(lambda: events.append("last"))
        raise ValueError("failed setup")

    rec = manager._run_register(Manifest("bad"), "dir", register)
    assert not rec.ok and rec.registered == [] and manager.commands == {}
    assert events == ["last", "first"] and "cleanup failed" in caplog.text
    manager.close()
    assert events == ["last", "first"]


def test_cleanup_failure_cannot_skip_other_plugins_and_close_keeps_gate_hooks(caplog):
    events = []
    manager = PluginManager(executor_required=True)

    def register(ctx):
        ctx.register_hook("pre_tool_call", lambda **kw: "guard")
        ctx.register_cleanup(lambda: events.append("guard"))

    manager._run_register(Manifest("exo-gate"), "dir", register)
    manager._run_register(Manifest("broken"), "dir", lambda ctx: ctx.register_cleanup(lambda: 1 / 0))
    manager.close()
    manager.close()
    assert events == ["guard"] and "cleanup failed" in caplog.text
    assert manager.hooks.has("pre_tool_call")  # final resource release cannot remove the gate


def test_closed_manager_never_imports_new_plugins(tmp_path):
    folder, marker = plugin(tmp_path, "new")
    manager = PluginManager()
    manager.close()
    assert "closed" in manager.load_folder(folder).error
    assert not marker.exists()
    assert "closed" in _closed_manager().load_all([tmp_path], entry_points=False)[0].error
    assert not marker.exists()


def _closed_manager():
    manager = PluginManager()
    manager.close()
    return manager


def test_async_cleanup_is_rejected():
    manager = PluginManager()

    async def cleanup():
        pass

    rec = manager._run_register(Manifest("async"), "dir", lambda ctx: ctx.register_cleanup(cleanup))
    assert not rec.ok and "synchronous" in rec.error


def test_folder_can_depend_on_selected_entry_point(monkeypatch, tmp_path):
    _, marker = plugin(tmp_path, "child", ["base"])
    imported = []

    def load():
        imported.append("base")
        return lambda ctx: ctx.register_command("base", lambda: "ok")

    ep = SimpleNamespace(name="base", load=load)
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [ep])
    manager = PluginManager()
    records = manager.load_all([tmp_path])
    assert [r.manifest.name for r in records] == ["base", "child"]
    assert all(r.ok for r in records) and imported == ["base"] and marker.exists()


def test_entry_point_requirements_prevent_registration_not_import(monkeypatch):
    imported, registered = [], []

    def load():
        imported.append("child")
        return SimpleNamespace(PLUGIN_MANIFEST={"requires": ["missing"]},
                               register=lambda ctx: registered.append("child"))

    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [SimpleNamespace(name="child", load=load)])
    [rec] = PluginManager().load_entry_points()
    assert not rec.ok and imported == ["child"] and not registered


def test_folder_precedence_does_not_import_duplicate_entry_point(monkeypatch, tmp_path):
    plugin(tmp_path, "same")

    def load():
        raise AssertionError("entry point shadowed by folder must not be imported")

    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [SimpleNamespace(name="same", load=load)])
    [rec] = PluginManager().load_all([tmp_path])
    assert rec.ok and rec.source == "dir"


@pytest.mark.parametrize("via", ["load_all", "load_entry_points"])
def test_entry_points_register_in_discovery_order_as_before(monkeypatch, tmp_path, via):
    """Hooks run in registration order, so entry points keep the order importlib lists them in (#500 review)."""
    def ep(name):
        return SimpleNamespace(name=name, load=lambda: lambda ctx: ctx.register_hook("pre_tool_call", lambda **kw: None))

    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [ep("zeta"), ep("alpha")])
    manager = PluginManager()
    if via == "load_all":
        manager.load_all([tmp_path / "none"])
    else:
        manager.load_entry_points()
    assert list(manager.plugins) == ["zeta", "alpha"]
    assert [entry.plugin for entry in manager.hooks.entries("pre_tool_call")] == ["zeta", "alpha"]
