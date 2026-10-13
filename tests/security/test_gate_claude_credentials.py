"""Claude's local credential stores always require principal approval."""
import os

import pytest

from exo import gate


@pytest.mark.parametrize("path", [
    "~/.claude/.credentials.json",
    "~/.claude/.credentials",
    "~/.claude/projects/session.json",
    "~/work/.credentials.json",
])
def test_known_claude_secret_paths_are_credentials(path, monkeypatch):
    home = os.path.abspath("/tmp/exo-gate-home")
    monkeypatch.setenv("HOME", home)
    full = os.path.expanduser(path)
    c = gate.classify({"tool": "bash", "args": {"command": f"cat {full}"}})
    assert c.category == "credentials"
    assert gate.decide(c, {"gates": {"categories": {"credentials": "allow"}}}).outcome == "ask"


def test_claude_config_dir_expands_for_shell_and_file_tools(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = tmp_path / "claude-custom"
    home.mkdir()
    config.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))

    shell = gate.classify({"tool": "bash", "args": {"command": 'cat "$CLAUDE_CONFIG_DIR"/.credentials.json'}})
    direct = gate.classify({"tool": "write_file", "args": {"path": str(config / "settings.json"), "content": "x"}})
    assert shell.category == direct.category == "credentials"
    assert gate.decide(direct, {"gates": {"categories": {"credentials": "allow"}}}).outcome == "ask"


def test_default_claude_dir_is_sensitive_without_config_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert gate.is_sensitive_path(str(home / ".claude" / "settings.json"))
