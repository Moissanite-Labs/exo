"""Bare `exo` opens the terminal client; subcommands are unaffected; --version; exo update."""
import pytest

from exo import __version__, cli, update_cmd


@pytest.fixture
def tui(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "cmd_tui", lambda rest: calls.append(list(rest)) or 0)
    return calls


@pytest.mark.parametrize("argv", [[], ["--resume"], ["--seat", "builder"], ["--no-intro"],
                                  ["--resume", "--session", "s1", "--seat", "x"]])
def test_bare_and_flags_open_the_tui_with_flags(tui, argv):
    assert cli.main(argv) == 0
    assert tui == [argv]


def test_resume_word_routes_like_the_flag(tui):
    assert cli.main(["resume"]) == 0
    assert cli.main(["resume", "--seat", "x"]) == 0
    assert tui == [["--resume"], ["--resume", "--seat", "x"]]


def test_help_lists_resume(capsys):
    with pytest.raises(SystemExit):
        cli.main(["-h"])
    assert "exo resume" in capsys.readouterr().out


def test_tui_subcommand_still_routes(tui):
    assert cli.main(["tui", "--resume"]) == 0
    assert tui == [["--resume"]]


def test_subcommands_do_not_open_the_tui(tui, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    assert cli.main(["models"]) == 0
    assert tui == []


def test_help_lists_both_and_exits(tui, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["-h"])
    assert e.value.code == 0 and tui == []
    out = capsys.readouterr().out
    assert "no command" in out and "exo tui" in out and "update" in out


def test_version(tui, capsys):
    assert cli.main(["--version"]) == 0 and tui == []
    out = capsys.readouterr().out.strip()
    assert out.startswith("exo ") and __version__ in out


def test_unknown_subcommand_is_an_error_not_a_tui(tui):
    with pytest.raises(SystemExit) as e:
        cli.main(["nonsense"])
    assert e.value.code != 0 and tui == []


# --- exo update ------------------------------------------------------------------------

def test_plan_installer_mode(tmp_path):
    (tmp_path / ".git").mkdir()
    p = update_cmd.plan({"checkout": str(tmp_path), "python": "/v/bin/python"})
    assert p["mode"] == "installer"
    assert p["steps"][0][-4:] == ["-C", str(tmp_path), "pull", "--ff-only"]
    assert p["steps"][2][6:8] == ["bash", str(tmp_path / "scripts" / "install.sh")]
    assert "--no-interview" in p["steps"][2]


def test_plan_uv_tool_and_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(update_cmd, "checkout_root", lambda: None)
    assert update_cmd.plan({}, uv_tool=True)["steps"] == [["uv", "tool", "upgrade", "exo"]]
    assert update_cmd.plan({}, uv_tool=False)["mode"] == "unknown"


def test_update_dry_run_runs_nothing(monkeypatch, capsys, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(update_cmd, "read_install", lambda: {"checkout": str(tmp_path)})
    ran = []
    assert update_cmd.cmd_update(["--dry-run"], run=lambda c: ran.append(c)) == 0
    assert ran == []
    out = capsys.readouterr().out
    assert "pull --ff-only" in out and "dry run: nothing changed" in out


def test_update_unknown_install_says_how(monkeypatch, capsys):
    monkeypatch.setattr(update_cmd, "read_install", lambda: {})
    monkeypatch.setattr(update_cmd, "checkout_root", lambda: None)
    monkeypatch.setattr(update_cmd, "_is_uv_tool", lambda: False)
    assert update_cmd.cmd_update([]) == 1
    assert "scripts/install.sh" in capsys.readouterr().err
