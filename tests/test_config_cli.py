import json

from exo.cli import main


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
