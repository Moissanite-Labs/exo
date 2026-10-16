import importlib.util
import sys
from pathlib import Path


from conftest import PLUGIN_DIR
from exo import privacy
from exo_command.config import load_settings

DENY = privacy.deny_regex()  # untracked deny-list, None when absent


def test_defaults_are_generic(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    s = load_settings()
    assert s.principal_store is None and s.decisions_queue is None
    assert s.resolved_socket() == str(tmp_path / "run" / "exod.sock")
    assert s.host == "127.0.0.1" and s.resolved_token() is None


def test_env_relative_queue_and_token(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    monkeypatch.setenv("EXO_PRINCIPAL", "p")
    monkeypatch.setenv("EXO_COMMAND_LIVE_WINDOW_SECONDS", "60")
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "exod.token").write_text("sekret\n")
    s = load_settings()
    assert s.decisions_queue == tmp_path / "principals" / "p" / "queues" / "decisions.jsonl"
    assert s.live_window_seconds == 60 and s.resolved_token() == "sekret"
    assert "token" not in s.public() and "token_file" not in s.public()
    s2 = load_settings({"decisions_queue": "/abs/q.jsonl", "port": 9000})
    assert s2.decisions_queue == Path("/abs/q.jsonl") and s2.port == 9000


def test_no_hardcoded_identity():
    deny = DENY
    hits = []
    for p in PLUGIN_DIR.rglob("*"):
        if p.is_file() and p.suffix in (".py", ".js", ".json", ".yaml", ".css", ".html") and "tests" not in p.parts:
            text = p.read_text(errors="ignore")
            if (deny and deny.search(text)) or "/Users/" in text:
                hits.append(str(p.relative_to(PLUGIN_DIR)))
    assert hits == []


def _load_root_plugin():
    spec = importlib.util.spec_from_file_location(
        "exo_command_plugin", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exo_command_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_register_adds_slash_command_and_explains_a_missing_core(monkeypatch, tmp_path):
    import asyncio
    mod = _load_root_plugin()
    commands = {}

    class Ctx:
        def register_command(self, name, handler, description="", **kw):
            commands[name] = handler

    mod.register(Ctx())
    assert list(commands) == ["exo-command"]
    monkeypatch.setenv("EXO_HOME", str(tmp_path))  # no daemon here
    out = asyncio.run(commands["exo-command"](""))
    assert out.startswith("Cannot reach the Exo core") and "exo serve" in out


def test_plugin_yaml_declares_settings():
    yaml_text = (PLUGIN_DIR / "plugin.yaml").read_text()
    assert yaml_text.startswith("name: exo-command")
    for key in ("socket", "url", "token_file", "host", "port", "principal_store", "decisions_queue"):
        assert "\n  {}:".format(key) in yaml_text
    assert "hermes" not in yaml_text.lower()


def test_static_page_has_no_bundler():
    """No build step: native ES modules served as they are, local imports only, no require, no node_modules."""
    import re
    static = PLUGIN_DIR / "exo_command" / "static"
    for f in static.glob("*.js"):
        js = f.read_text()
        assert "require(" not in js and "__HERMES" not in js and "node_modules" not in js, f.name
        for m in re.finditer(r"""^\s*import\s.*?from\s+["']([^"']+)["']""", js, re.M):
            assert m.group(1).startswith("./"), (f.name, m.group(1))
    command = (static / "command.js").read_text()
    assert "/api/snapshot" in command and "/api/stream" in command
    assert not list(PLUGIN_DIR.glob("package.json")) and not list(PLUGIN_DIR.glob("**/package.json"))

