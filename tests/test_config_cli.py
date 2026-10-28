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
SECRETS = ["hunter2", "nested-token-value", "literal-secret", "plain-env-value"]
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


def test_config_masks_everything_beneath_a_secret_name_env_or_headers(tmp_path, monkeypatch, capsys):
    """A container under a secret-shaped name, and a literal under `env` or `headers`, printed in full (#691)."""
    _home(tmp_path, monkeypatch, CONFIG)
    shown = _cli(capsys, "show")
    assert [x for x in SECRETS if x in shown] == []
    for x in ["dark", "http://127.0.0.1:1", "${BUS_TOKEN}", "MY_KEY", "srv", "1200"]:
        assert x in shown
    for key, want in [("token", {"a": "***"}), ("token.a", "***"), ("mcp.s.env", {"A": "***"}), ("mcp.s.env.A", "***"),
                      ("plugin_config.exo-bus.headers", {"X-Auth": "***", "X-Ref": "${BUS_TOKEN}"}),
                      ("plugin_config.exo-bus.headers.X-Auth", "***")]:
        out = _cli(capsys, "get", key)
        assert (out.strip() if isinstance(want, str) else json.loads(out)) == want, key
    for key in KEYS:
        assert [x for x in SECRETS if x in json.dumps(_api(tmp_path, key))] == [], key


def test_config_set_refuses_a_literal_beneath_a_secret_name(tmp_path, monkeypatch, capsys):
    """`set` refused a literal `token` but stored `{"a": "hunter2"}` under it, which `show` then printed (#691)."""
    _home(tmp_path, monkeypatch, {"version": 1})
    assert main(["config", "set", "token", '{"a": "hunter2"}']) == 1
    assert main(["config", "set", "plugin_config.x.credentials", '{"user": {"b": "hunter2"}}']) == 1
    assert capsys.readouterr().err.count("refusing to store a secret") == 2
    assert "hunter2" not in (tmp_path / "config.json").read_text()
    assert main(["config", "set", "token", '{"a": "${TOK}"}']) == 0
    assert _cli(capsys, "get", "token.a") == "${TOK}\n"


def test_config_masks_a_key_shaped_value_beside_a_var_reference(tmp_path, monkeypatch, capsys):
    """`set` refused a key-shaped value that also held a `${VAR}`, but `show`, `get` and `config.get` printed one
    written by hand (PR #674)."""
    leak = "${TOK}:sk-" + "A" * 24
    _home(tmp_path, monkeypatch,
          {"version": 1, "theme": leak, "token": leak, "plugin_config": {"x": {"headers": {"X-A": leak}}}})
    assert main(["config", "set", "theme", leak]) == 1
    assert "refusing to store a secret" in capsys.readouterr().err
    assert "AAAA" not in _cli(capsys, "show")
    for key in ["theme", "token", "plugin_config.x", "plugin_config.x.headers.X-A"]:
        assert _cli(capsys, "get", key).strip() in ("***", json.dumps({"headers": {"X-A": "***"}})), key
        assert "AAAA" not in json.dumps(_api(tmp_path, key)), key
