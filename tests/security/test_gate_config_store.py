"""`config.json` and the pinned trust list are part of the protected Exo store (D-074).

`config.json` names the plugins the core loads (the approval gate among them), the plugin config, the models
and the interpreter `exo update` runs. `update/allowed_signers` is the trust root for signed updates (D-073).
A write to either steers the gate or the code that later runs as the principal, so it asks like `policies/`.
"""
import pytest

from exo import gate

ALLOW_ALL = {"gates": {"categories": {"store_write": "allow", "local_write": "allow", "shell": "allow"}}}


def _home(tmp_path, monkeypatch):
    home = tmp_path / "exo-home"
    monkeypatch.setenv("EXO_HOME", str(home))
    return home


def test_config_and_trust_pin_are_store_paths(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert gate.is_store_path(str(home / "config.json"))
    assert gate.is_store_path(str(home / "update"))
    assert gate.is_store_path(str(home / "update" / "allowed_signers"))
    assert gate.is_store_path("~/.exo/config.json")
    assert gate.is_store_path("~/.exo/update/allowed_signers")
    # Only the home's own config.json: a project's config.json and the home's other files stay ordinary.
    assert not gate.is_store_path(str(tmp_path / "project" / "config.json"))
    assert not gate.is_store_path(str(home / "plugins" / "x" / "config.json"))
    assert not gate.is_store_path(str(home / "config.json.bak"))
    assert not gate.is_store_path(str(home / "README.md"))


@pytest.mark.parametrize("name", ["config.json", "update/allowed_signers"])
def test_file_tool_writes_ask_and_no_policy_lowers_them(tmp_path, monkeypatch, name):
    home = _home(tmp_path, monkeypatch)
    target = home / name
    for call in ({"tool": "write_file", "args": {"path": str(target), "content": "{}"}},
                 {"tool": "edit_file", "args": {"path": str(target), "old": "a", "new": "b"}}):
        c = gate.classify(call)
        assert c.category == "store_write", call
        assert gate.decide(c, ALLOW_ALL).outcome == "ask", call


@pytest.mark.parametrize("command", [
    "cp /tmp/evil {home}/config.json",
    "mv /tmp/evil {home}/config.json",
    "tee {home}/config.json < /tmp/evil",
    "cat /tmp/evil > {home}/config.json",
    "sed -i s/true/false/ {home}/config.json",
    "cp /tmp/evil {home}/update/allowed_signers",
    "printf x > {home}/update/allowed_signers",
    "rm -rf {home}/update",
])
def test_shell_writes_ask(tmp_path, monkeypatch, command):
    home = _home(tmp_path, monkeypatch)
    c = gate.classify({"tool": "bash", "args": {"command": command.format(home=home)}})
    assert c.category == "store_write", command
    assert gate.decide(c, ALLOW_ALL).outcome == "ask", command


def test_reads_stay_reads(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    for command in (f"cat {home}/config.json", f"cat {home}/update/allowed_signers"):
        c = gate.classify({"tool": "bash", "args": {"command": command}})
        assert c.category == "read", command
        assert gate.decide(c, ALLOW_ALL).outcome == "allow", command
