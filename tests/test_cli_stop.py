"""`exo stop -h` prints help and an unknown word is an error: neither stops the core. Plain `exo stop` still does."""
import pytest

from exo import cli, ensure_core


@pytest.fixture
def stops(monkeypatch):
    calls = []
    monkeypatch.setattr(ensure_core, "stop_core", lambda *a, **k: calls.append(a) or "Exo core stopped (pid 7).")
    return calls


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_and_leaves_the_core_running_until_a_plain_stop(stops, capsys, flag):
    with pytest.raises(SystemExit) as e:
        cli.main(["stop", flag])
    assert e.value.code == 0 and stops == []
    out = capsys.readouterr().out
    assert "usage: exo stop" in out and "background core" in out and "stopped" not in out
    assert cli.main(["stop"]) == 0
    assert stops == [()] and "Exo core stopped (pid 7)." in capsys.readouterr().out


def test_an_unknown_word_is_an_error_and_leaves_the_core_running(stops, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["stop", "now"])
    assert e.value.code == 2 and stops == []
    assert "unrecognized arguments: now" in capsys.readouterr().err
