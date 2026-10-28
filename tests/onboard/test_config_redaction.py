"""#691: `config get` and `config show` mask a secret at any level of the key path, not only by the last name: a
dotted key, a container named like a secret, and a literal under env or headers. The write check judges the same way."""
import asyncio
import json
import types

import pytest

from exo import config as xc
from exo.config import Config, ConfigError, main, redact
from exo.core.engine import CoreRuntime
from exo.protocol.schema import ConfigGet


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    for var in xc.ENV.values():
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _write(home, data):
    (home / "config.json").write_text(json.dumps(data))


def test_a_dotted_key_is_masked_by_its_own_path(home, capsys):
    _write(home, {"plugin_config": {"exo-memory": {"token": "nested-token-value"}}})
    assert main(["get", "plugin_config.exo-memory.token"]) == 0
    assert capsys.readouterr().out.strip() == "***"
    assert main(["show"]) == 0
    assert "nested-token-value" not in capsys.readouterr().out


def test_a_container_under_a_secret_name_masks_every_leaf(home, capsys):
    _write(home, {"token": {"a": "hunter2"}, "credentials": {"user": "alice", "pass": "p4ss"}})
    for args in (["get", "token"], ["get", "credentials"], ["show"]):
        assert main(args) == 0
        out = capsys.readouterr().out
        assert "hunter2" not in out and "p4ss" not in out and "alice" not in out, args


def test_a_literal_under_env_or_headers_is_masked_and_a_reference_is_not(home, capsys):
    _write(home, {"plugin_config": {"exo-bus": {"headers": {"X-Auth": "literal-secret", "X-Ref": "${TOK}"}}}})
    assert main(["get", "plugin_config.exo-bus.headers.X-Auth"]) == 0
    assert capsys.readouterr().out.strip() == "***"
    assert main(["get", "plugin_config.exo-bus.headers.X-Ref"]) == 0
    assert capsys.readouterr().out.strip() == "${TOK}"


def test_a_key_env_name_is_shown_as_the_name_it_is(home, capsys):
    cfg = Config(home=home)
    cfg.set("models.providers.p.key_env", "MY_KEY")
    cfg.save()
    assert main(["get", "models.providers.p.key_env"]) == 0
    assert capsys.readouterr().out.strip() == "MY_KEY"


def test_the_json_api_masks_a_dotted_key_and_a_container_the_same_way(home):
    _write(home, {"plugin_config": {"exo-memory": {"token": "nested-token-value"}},
                  "credentials": {"user": "alice", "pass": "p4ss"}})
    api = types.SimpleNamespace(home=home)
    res = asyncio.run(CoreRuntime._config_get(api, ConfigGet(key="plugin_config.exo-memory.token")))
    assert res["value"] == "***"
    res = asyncio.run(CoreRuntime._config_get(api, ConfigGet(key="credentials")))
    assert "p4ss" not in json.dumps(res) and "alice" not in json.dumps(res)
    whole = asyncio.run(CoreRuntime._config_get(api, ConfigGet(key=None)))
    assert "nested-token-value" not in json.dumps(whole) and "p4ss" not in json.dumps(whole)


def test_a_write_refuses_a_container_under_a_secret_name(home):
    cfg = Config(home=home)
    for key, val in [("token", {"a": "hunter2"}), ("credentials", {"user": "a", "pass": "p"})]:
        with pytest.raises(ConfigError):
            cfg.set(key, val)
    cfg.set("token", {"a": "${TOK}"})  # a reference names the secret, it is not one


def test_a_plain_container_keeps_the_leaf_rule(home):
    assert redact({"n": {"token": "t"}, "visible": "x"}) == {"n": {"token": "***"}, "visible": "x"}
    assert redact({"n": {"name": "x"}}, ["plugin_config"]) == {"n": {"name": "x"}}


@pytest.mark.parametrize("key,value", [
    ("token", "${USER}:hunter2"),
    ("plugin_config.x.headers.Authorization", "Basic ${X}hunter2"),
    ("token", "b${TOKEN}earer"), ("token", "${TOKEN}Bearer"),
    ("token", 123456), ("api_key", 9876543210), ("mcp.s.env.PIN", 4321),
    ("token", {"nested": [123456, "${USER}:hunter2"]}),
    ("token", "${USER}:sk-abcdefghijklmnopqrst"),
])
def test_literal_fragments_and_numbers_are_refused_and_masked(home, capsys, key, value):
    cfg = Config(home=home)
    with pytest.raises(ConfigError):
        cfg.set(key, value)
    masked = redact(value, key.split("."))
    assert "hunter2" not in json.dumps(masked)
    assert "123456" not in json.dumps(masked)
    if not isinstance(value, (dict, list)):
        assert masked == "***"
    # A hand-edited file must not bypass the same output rule through either CLI surface.
    data = {}
    cursor = data
    for part in key.split(".")[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[key.split(".")[-1]] = value
    _write(home, data)
    assert main(["get", key]) == 0
    shown = capsys.readouterr().out.strip()
    assert shown == (masked if isinstance(masked, str) else json.dumps(masked))
    assert main(["show"]) == 0
    shown_config = json.loads(capsys.readouterr().out)
    for part in key.split("."):
        shown_config = shown_config[part]
    assert shown_config == masked
    api = types.SimpleNamespace(home=home)
    result = asyncio.run(CoreRuntime._config_get(api, ConfigGet(key=key)))
    assert result["value"] == masked


@pytest.mark.parametrize("value", ["${TOKEN}", "Bearer ${TOKEN}", "Basic ${TOKEN}", "token ${TOKEN}",
                                    "${FIRST}${SECOND}", "", None])
def test_reference_only_values_keep_working(home, value):
    cfg = Config(home=home)
    cfg.set("token", value)
    assert redact(value, ["token"]) == value
