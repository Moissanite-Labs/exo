from exo_tui.restart import has_opener, open_tab, restart_command


def test_command_from_env_and_fallback():
    assert restart_command("s1", {"EXO_RESTART_CMD": "exo-canary '/my dir'"}) == ["exo-canary", "/my dir", "--session", "s1"]
    out = restart_command("s2", {}, ["exo", "tui", "--session", "old", "--resume", "--seat", "c"])
    assert out[1:] == ["exo", "tui", "--seat", "c", "--session", "s2"]
    assert restart_command(None, {"EXO_RESTART_CMD": "x"}) == ["x", "--resume"]


def test_restart_drops_full_access_flag():
    out = restart_command("sB", {}, ["exo", "--full-access", "--session", "sA"])
    assert out[1:] == ["exo", "--session", "sB"]
    out = restart_command("sB", {}, ["exo", "--full", "--session", "sA"])
    assert out[1:] == ["exo", "--session", "sB"]
    out = restart_command("sB", {}, ["exo", "--full-acc", "--session", "sA"])
    assert out[1:] == ["exo", "--session", "sB"]
    out = restart_command("s_new", {}, ["exo", "--full-access", "--resume"])
    assert out[1:] == ["exo", "--session", "s_new"]
    out = restart_command("sB", {}, ["exo", "--full-access=true", "--session", "sA"])
    assert out[1:] == ["exo", "--session", "sB"]


def test_macos_terminal_and_iterm():
    got = []
    assert open_tab(["exo", "--session", "s1"], {}, "darwin", got.append) == "opened"
    assert got[0][:2] == ["osascript", "-e"] and "exo --session s1" in got[0][2] and "Terminal" in got[0][2]
    got.clear()
    open_tab(["exo"], {"TERM_PROGRAM": "iTerm.app"}, "darwin", got.append)
    assert "iTerm2" in got[0][2]


def test_macos_scripts_never_target_existing_tabs_or_send_keystrokes():
    for env in ({}, {"TERM_PROGRAM": "iTerm.app"}, {"TERM_PROGRAM": "Apple_Terminal"}):
        got = []
        assert open_tab(["exo", "--session", "s1"], env, "darwin", got.append) == "opened"
        assert len(got) == 1
        script = got[0][2]
        for bad in ("in front window", "current session", "current window", "keystroke", "System Events"):
            assert bad not in script, (env, bad)


def test_macos_failure_returns_command_to_run_by_hand():
    def runner(a):
        raise OSError("denied")

    out = open_tab(["exo", "--session", "s1"], {}, "darwin", runner)
    assert out != "opened" and "exo --session s1" in out


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


def test_fallback_carries_exo_env_but_not_secrets():
    out = restart_command("s3", {"EXO_HOME": "/h", "EXO_BUS_KEYS_DIR": "/k", "EXO_API_KEY": "x", "PATH": "/bin"},
                          ["exo", "tui", "--seat", "c"])
    assert out[0] == "env" and "EXO_HOME=/h" in out and "EXO_BUS_KEYS_DIR=/k" in out
    assert not any(a.startswith(("EXO_API_KEY", "PATH=")) for a in out)
    assert out[-2:] == ["--session", "s3"]


def test_native_opener_wait_is_bounded(monkeypatch):
    import subprocess
    from exo_tui import restart as rs
    seen = []

    def timed_out(args, **kwargs):
        seen.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])

    monkeypatch.setattr(rs.subprocess, 'run', timed_out)
    result = rs.open_tab(['exo'], {}, 'darwin')
    assert result != 'opened' and seen == [15]
