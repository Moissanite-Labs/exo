import asyncio
import json

from exo.cli import main
from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher

# The secret shapes of #691, beside values that must stay visible.
CONFIG = {
    "version": 1,
    "theme": "dark",
    "token": {"a": "hunter2"},
    "plugin_config": {
        "exo-memory": {"token": "nested-token-value", "pack_budget": 1200},
        "exo-bus": {"url": "http://127.0.0.1:1", "headers": {"X-Auth": "literal-secret", "X-Ref": "${BUS_TOKEN}"}},
    },
    "mcp": {"s": {"command": "srv", "env": {"A": "plain-env-value"}}},
    "models": {"providers": {"p": {"key_env": "MY_KEY"}}},
}
KEYS = ["theme", "token", "token.a", "plugin_config", "plugin_config.exo-memory", "plugin_config.exo-memory.token",
        "plugin_config.exo-bus.headers", "plugin_config.exo-bus.headers.X-Auth", "plugin_config.exo-bus.headers.X-Ref",
        "mcp", "mcp.s.env", "mcp.s.env.A", "models.providers.p.key_env"]


def _home(tmp_path, monkeypatch, data):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(data))


def _cli(capsys, *argv):
    assert main(["config", *argv]) == 0
    return capsys.readouterr().out


def _api(tmp_path, key=None):
    d = Dispatcher(CoreEngine(tmp_path, {}, cwd=str(tmp_path), env={}))
    res = asyncio.run(d.run(s.Command.of(s.ConfigGet(key=key)))).result
    return res["config"] if key is None else res["value"]


def test_config_show_redacts_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({
        "version": 1,
        "api_key": "sk-SUPERSECRETEXAMPLE1234567890",
        "theme": "dark",
    }))
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    # The secret value must never reach the terminal/logs; a non-secret must.
    assert "sk-SUPERSECRETEXAMPLE1234567890" not in out
    assert "***" in out
    assert "dark" in out


def test_config_get_redacts_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({
        "version": 1,
        "api_key": "sk-SUPERSECRETEXAMPLE1234567890",
    }))
    assert main(["config", "get", "api_key"]) == 0
    assert "sk-SUPERSECRETEXAMPLE1234567890" not in capsys.readouterr().out


def test_config_get_masks_a_secret_reached_by_a_dotted_key(tmp_path, monkeypatch, capsys):
    """`get` handed the whole dotted key to the name test, and a `.` before `token` defeats it (#691)."""
    _home(tmp_path, monkeypatch, CONFIG)
    assert _cli(capsys, "get", "plugin_config.exo-memory.token") == "***\n"
    assert json.loads(_cli(capsys, "show"))["plugin_config"]["exo-memory"]["token"] == "***"


def test_config_cli_prints_what_the_api_returns(tmp_path, monkeypatch, capsys):
    """`exo config show` and `get` print what `config.get` returns, whatever the key (#691)."""
    _home(tmp_path, monkeypatch, CONFIG)
    for key in KEYS:
        api = _api(tmp_path, key)
        assert _cli(capsys, "get", key) == (api if isinstance(api, str) else json.dumps(api)) + "\n", key
    assert json.loads(_cli(capsys, "show")) == _api(tmp_path)
