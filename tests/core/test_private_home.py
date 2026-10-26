"""A fresh exo home and the files in it are owner-only."""
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from exo import cli_chat, ensure_core
from exo.core import agentlog
from exo.core.private import private_dir, private_file
from exo.scheduler import Scheduler
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

    async def serve(engine, claimed=None, **kw):  # the pid is written once the socket's start lock is held
        claimed()
        assert pid.read_text() == f"{os.getpid()}\n"
        assert mode(pid) == 0o600

    monkeypatch.setattr("exo.server.app.serve", serve)
    args = SimpleNamespace(seat=None, engine="echo", stdio=False, tcp=None, socket=None)
    assert cli_chat.cmd_serve(args) == 0
    assert not pid.exists()


def test_spawn_log_and_pid_are_private(tmp_path, monkeypatch):
    home = tmp_path / "newhome"
    proc = SimpleNamespace(pid=12345, returncode=0, communicate=lambda **kw: (b"12345\n", None))

    def popen(*args, **kwargs):
        out = kwargs["stderr"]
        assert mode(Path(out.name)) == 0o600
        out.write(b"startup log\n")
        return proc

    monkeypatch.setattr(ensure_core.subprocess, "Popen", popen)
    assert ensure_core._spawn(home).pid == 12345
    assert mode(home) == mode(home / "logs") == 0o700
    assert mode(ensure_core.log_path(home)) == 0o600
    assert ensure_core.log_path(home).read_bytes() == b"startup log\n"
    assert not ensure_core.pid_path(home).exists()  # the core writes run/exod.pid itself, under the socket's lock


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


def test_atomic_write_mode_none_does_not_race_the_umask_probe(tmp_path, monkeypatch):
    """Without /proc (macOS, which the README supports), `_umask()` probes by setting the mask to 077 and
    restoring it. Unlocked, concurrent `atomic_write(..., mode=None)` calls interleaved set and restore: files
    came out 0600 under a 022 mask, and a run could leave the whole process at 077 (#317). The probe is locked
    now, so every file takes the mask the process started with and the mask is unchanged afterwards."""
    import builtins
    import sys
    import threading

    from exo.core.private import atomic_write

    real_open = builtins.open

    def no_proc(file, *args, **kwargs):
        if str(file) == "/proc/self/status":
            raise OSError("no /proc on this platform")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_proc)
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        def write(t):
            for i in range(300):
                atomic_write(tmp_path / f"f{t}-{i}", b"x", mode=None, sync=False)

        threads = [threading.Thread(target=write, args=(t,)) for t in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
    finally:
        sys.setswitchinterval(interval)
    files = list(tmp_path.iterdir())
    assert len(files) == 8 * 300 and all(mode(p) == 0o644 for p in files)
    now = os.umask(0o022)
    os.umask(now)
    assert now == 0o022


def _no_proc(monkeypatch):
    """`_umask()` as it runs without /proc (macOS): by the set-and-restore probe."""
    from exo.core import private

    def no_proc(*a, **k):
        raise FileNotFoundError("/proc/self/status")
    monkeypatch.setattr(private, "open", no_proc, raising=False)
    return private


def test_a_umask_block_and_the_probe_share_one_lock(tmp_path, monkeypatch):
    """The socket bind in server/app.py set the mask to 0177 and restored it outside the probe's lock. Beside the
    probe, the process could be left at 0177 or 077 (#317 review). A `umask` block holds the probe's lock, so a
    thread looping one beside 8 writers leaves every file 0644 and the mask at 022."""
    import sys
    import threading

    private = _no_proc(monkeypatch)
    umask, stop = private.umask, threading.Event()

    def bind():
        while not stop.is_set():
            with umask(0o177):
                pass

    def write(t):
        for i in range(300):
            private.atomic_write(tmp_path / f"f{t}-{i}", b"x", mode=None, sync=False)

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    binder, writers = threading.Thread(target=bind), [threading.Thread(target=write, args=(t,)) for t in range(8)]
    try:
        binder.start()
        for th in writers:
            th.start()
        for th in writers:
            th.join()
    finally:
        stop.set()
        binder.join()
        sys.setswitchinterval(interval)
    assert sorted({mode(p) for p in tmp_path.iterdir()}) == [0o644] and len(list(tmp_path.iterdir())) == 8 * 300
    now = os.umask(0o022)
    assert now == 0o022


def test_a_probe_inside_a_umask_block_does_not_deadlock(monkeypatch):
    """The socket bind awaits inside its `umask` block, so another task on the same thread may probe meanwhile. The
    lock is reentrant: the probe reads the block's mask and the block restores the old one afterwards."""
    import threading

    private = _no_proc(monkeypatch)
    got = []

    def probe_inside():
        with private.umask(0o177):
            got.append(private._umask())

    t = threading.Thread(target=probe_inside, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive() and got == [0o177]
    now = os.umask(0o022)
    assert now == 0o022


def test_the_socket_bind_sets_its_umask_under_the_probes_lock(monkeypatch):
    """`HttpServer.start_unix` makes the socket under umask 0177. A probe in another thread during the bind waits
    for the lock and then reads the process's own mask: before, it ran at once and read 0177 (#317 review)."""
    import asyncio
    import shutil
    import tempfile
    import threading

    from exo.server.app import Dispatcher, HttpServer
    from exo.server.echo import EchoEngine

    private = _no_proc(monkeypatch)
    real, probes, seen, waited = asyncio.start_unix_server, [], [], []

    async def bind(*args, **kwargs):
        probes.append(threading.Thread(target=lambda: seen.append(private._umask())))
        probes[0].start()
        probes[0].join(0.5)
        waited.append(probes[0].is_alive())
        return await real(*args, **kwargs)

    async def start(sock):
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(sock)
        await srv.close()

    monkeypatch.setattr(asyncio, "start_unix_server", bind)
    d = Path(tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None))
    try:
        asyncio.run(start(d / "exod.sock"))
        probes[0].join(10)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert waited == [True] and seen == [0o022]
    now = os.umask(0o022)
    assert now == 0o022


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
