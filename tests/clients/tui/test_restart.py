from exo_tui.restart import has_opener, open_tab, restart_command


def test_command_from_env_and_fallback():
    assert restart_command("s1", {"EXO_RESTART_CMD": "exo-canary '/my dir'"}) == ["exo-canary", "/my dir", "--session", "s1"]
    out = restart_command("s2", {}, ["exo", "tui", "--session", "old", "--resume", "--seat", "c"])
    assert out[1:] == ["exo", "tui", "--seat", "c", "--session", "s2"]
    assert restart_command(None, {"EXO_RESTART_CMD": "x"}) == ["x", "--resume"]


def test_macos_terminal_and_iterm():
    got = []
    assert open_tab(["exo", "--session", "s1"], {}, "darwin", got.append) == "opened"
    assert got[0][:2] == ["osascript", "-e"] and "exo --session s1" in got[0][2] and "Terminal" in got[0][2]
    got.clear()
    open_tab(["exo"], {"TERM_PROGRAM": "iTerm.app"}, "darwin", got.append)
    assert "iTerm2" in got[0][2]


def test_macos_falls_back_to_window_without_accessibility():
    got = []

    def runner(a):
        got.append(a)
        if len(got) == 1:
            raise OSError("not allowed")

    assert open_tab(["exo"], {}, "darwin", runner) == "opened"
    assert len(got) == 2 and "keystroke" not in got[1][2]


def test_linux_openers_and_manual_fallback():
    got = []
    have = {"kitty", "gnome-terminal"}
    which = lambda n: f"/bin/{n}" if n in have else None  # noqa: E731
    assert open_tab(["exo", "--resume"], {"KITTY_WINDOW_ID": "1"}, "linux", got.append, which) == "opened"
    assert got[0][:3] == ["kitty", "@", "launch"]
    got.clear()
    assert open_tab(["exo"], {}, "linux", got.append, which) == "opened" and got[0][0] == "gnome-terminal"
    line = open_tab(["exo", "--session", "s 1"], {}, "linux", got.append, lambda n: None)
    assert line.endswith("exo --session 's 1'") and "cd " in line
    assert not has_opener({}, "linux", lambda n: None) and has_opener({}, "darwin")
