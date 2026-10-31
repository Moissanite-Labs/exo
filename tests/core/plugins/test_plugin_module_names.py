"""Two plugins whose names differ only in punctuation must not share one module (exo-desktop #52).

A folder plugin imports as ``exo_plugins.<name>`` with each character that is not a letter, digit or ``_`` made
``_``. So ``a-b`` and ``a_b`` both import as ``exo_plugins.a_b``. Loading the second replaced the first's package in
``sys.modules``: a later relative import in the first plugin's code then ran the second plugin's file. The second
plugin is now refused with an error that names both, and the first keeps its own code.
"""
import sys
import textwrap

import pytest

from exo.core.plugins import PluginManager

INIT = '''
def register(ctx):
    ctx.register_tool(name=TOOL, toolset="t", handler=handler, check_fn=lambda: True,
                      schema={"name": TOOL, "description": "Who runs.", "parameters": {"type": "object", "properties": {}}})


def handler(args, **kw):
    from .impl import WHO  # imported on use, as a plugin may do
    return WHO
'''


def _plugin(root, name, tool):
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(f"name: {name}\nversion: '1.0'\n")
    (d / "__init__.py").write_text(textwrap.dedent(INIT).replace("TOOL", repr(tool)))
    (d / "impl.py").write_text(f"WHO = {name!r}\n")


@pytest.fixture
def clean_modules():
    yield
    for k in [k for k in sys.modules if k == "exo_plugins.a_b" or k.startswith("exo_plugins.a_b.")]:
        del sys.modules[k]


def test_a_second_plugin_with_the_same_module_name_is_refused(tmp_path, clean_modules):
    _plugin(tmp_path, "a-b", "dash_tool")
    _plugin(tmp_path, "a_b", "under_tool")
    pm = PluginManager()
    recs = {r.manifest.name: r for r in pm.load_all(dirs=[tmp_path], entry_points=False)}
    assert recs["a-b"].ok
    assert pm.tools["dash_tool"].handler({}) == "a-b"  # the first plugin runs its own impl.py
    err = recs["a_b"].error or ""
    assert "a-b" in err and "exo_plugins.a_b" in err, err
    assert "under_tool" not in pm.tools


def test_a_second_manager_cannot_take_the_module_of_a_loaded_plugin(tmp_path, clean_modules):
    _plugin(tmp_path / "one", "a-b", "dash_tool")
    _plugin(tmp_path / "two", "a_b", "under_tool")
    first = PluginManager()
    assert first.load_folder(tmp_path / "one" / "a-b").ok
    second = PluginManager()
    assert not second.load_folder(tmp_path / "two" / "a_b").ok
    assert first.tools["dash_tool"].handler({}) == "a-b"


def test_the_same_plugin_loads_again_from_another_folder(tmp_path, clean_modules):
    _plugin(tmp_path / "one", "a-b", "dash_tool")
    _plugin(tmp_path / "two", "a-b", "dash_tool")
    assert PluginManager().load_folder(tmp_path / "one" / "a-b").ok
    pm = PluginManager()
    assert pm.load_folder(tmp_path / "two" / "a-b").ok  # a fresh load of one plugin, as tests and restarts do
    assert pm.tools["dash_tool"].handler({}) == "a-b"
