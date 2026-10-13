"""A fresh exo home and the files in it are owner-only."""
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from exo import cli_chat, ensure_core
from exo.core import agentlog
from exo.core.private import private_dir, private_file
from exo.core.scheduler import Scheduler
from exo.core.sessions import SessionStore


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


@pytest.fixture(autouse=True)
def permissive_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


class _Eng:
    def __init__(self, home):
        self.home = home


def test_fresh_home_is_private(tmp_path):
    home = tmp_path / "newhome"  # does not exist yet
    SessionStore(home / "sessions.sqlite")
    Scheduler(_Eng(home))
    fh = agentlog.claim_writer(home, "default")
    try:
        assert mode(home) == 0o700
        assert mode(home / "run") == 0o700
        for p in (home / "sessions.sqlite", home / "schedule.sqlite", home / "run" / "agent-default.lock"):
            assert mode(p) == 0o600, p
    finally:
        agentlog.release_writer(fh)


def test_private_file_tightens_but_keeps_existing_dir(tmp_path):
    tmp_path.chmod(0o755)
    f = tmp_path / "x.sqlite"
    f.write_text("")
    f.chmod(0o644)
    private_file(f)
    assert mode(f) == 0o600
    assert mode(tmp_path) == 0o755  # an existing folder is not touched


def test_private_dir_makes_parents_private(tmp_path):
    private_dir(tmp_path / "a" / "b")
    assert mode(tmp_path / "a") == 0o700 and mode(tmp_path / "a" / "b") == 0o700


def test_serve_pid_is_private_before_server_runs(tmp_path, monkeypatch):
    home = tmp_path / "newhome"
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setattr("exo.server.app.load_engine", lambda spec: object())
    monkeypatch.setattr("exo.core.tools.builtin.shell.raise_nofile_limit", lambda: None)
    # Existing engine startup creates run/; isolate the CLI's own pid writer.
    private_dir(home / "run")
    pid = ensure_core.pid_path(home)

    def run(main):
        try:
            assert pid.read_text() == f"{os.getpid()}\n"
            assert mode(pid) == 0o600
        finally:
            main.close()

    monkeypatch.setattr(cli_chat.asyncio, "run", run)
    args = SimpleNamespace(seat=None, engine="echo", stdio=False, tcp=None, socket=None)
    assert cli_chat.cmd_serve(args) == 0
    assert not pid.exists()


def test_spawn_log_and_pid_are_private(tmp_path, monkeypatch):
    home = tmp_path / "newhome"
    proc = SimpleNamespace(pid=12345)

    def popen(*args, **kwargs):
        out = kwargs["stdout"]
        assert mode(Path(out.name)) == 0o600
        out.write(b"startup log\n")
        return proc

    monkeypatch.setattr(ensure_core.subprocess, "Popen", popen)
    assert ensure_core._spawn(home) is proc
    assert mode(home) == mode(home / "logs") == mode(home / "run") == 0o700
    assert mode(ensure_core.log_path(home)) == mode(ensure_core.pid_path(home)) == 0o600
    assert ensure_core.log_path(home).read_bytes() == b"startup log\n"
    assert ensure_core.pid_path(home).read_text() == "12345\n"


@pytest.mark.parametrize("dangling", [False, True])
def test_private_file_rejects_symlink_without_changing_target(tmp_path, dangling):
    target = tmp_path / "target"
    if not dangling:
        target.write_text("keep me")
        target.chmod(0o644)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        private_file(link)
    assert link.is_symlink()
    if dangling:
        assert not target.exists()
    else:
        assert target.read_text() == "keep me"
        assert mode(target) == 0o644


def test_private_dir_rejects_symlink_parent(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        private_dir(link / "child")
    assert not (target / "child").exists()
    assert mode(target) == 0o755


def test_private_dir_rejects_symlink_created_during_mkdir(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    link = tmp_path / "newhome"
    mkdir = Path.mkdir

    def raced_mkdir(path, *args, **kwargs):
        if path == link:
            path.symlink_to(target, target_is_directory=True)
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    with pytest.raises(OSError):
        private_dir(link / "run")
    assert not (target / "run").exists()
    assert mode(target) == 0o755
