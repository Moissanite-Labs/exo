"""A second `exo serve` never takes a live core's socket or its run/exod.pid.

Every seat defaults to run/exod.sock, and the writer lock is per agent, so `exo serve --seat scout` beside a running
default-seat exod used to unlink the live socket, bind its own and overwrite the pid file. `exo stop` then stopped the
second core and the first was left running, holding the default agent's writer lock, with no way to reach it.
"""
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from exo import cli_chat
from exo import ensure_core as ensure
from exo.protocol import schema as s
from exo.protocol.client import Client
from exo.server.app import Dispatcher, HttpServer
from exo.server.echo import EchoEngine


@pytest.fixture
def home():
    # Unix socket paths are capped near 104 bytes; pytest's tmp_path can exceed that.
    d = Path(tempfile.mkdtemp(prefix="exo-t-", dir="/tmp" if os.path.isdir("/tmp") else None))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def listener(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(8)
    return srv


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


# ---------------------------------------------------------------- HttpServer.start_unix


def test_start_unix_refuses_a_socket_a_live_server_answers_on(home):
    path = home / "exod.sock"

    async def main():
        first = HttpServer(Dispatcher(EchoEngine()))
        await first.start_unix(path)
        inode = path.stat().st_ino
        try:
            with pytest.raises(ValueError, match="another exo core is starting or running.*--socket"):
                await HttpServer(Dispatcher(EchoEngine())).start_unix(path)
            assert path.stat().st_ino == inode
            async with Client(socket=str(path)) as c:  # the first server still answers on its own socket
                assert (await c.call(s.SessionCreate(title="t")))["session"]["id"]
        finally:
            await first.close()

    _run(main())


def test_start_unix_still_replaces_a_stale_socket_file(home):
    path = home / "exod.sock"
    listener(path).close()  # the file stays, nobody listens
    assert ensure.probe(path) == "stale"

    async def main():
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(path)
        try:
            async with Client(socket=str(path)) as c:
                assert (await c.call(s.SessionCreate(title="t")))["session"]["id"]
        finally:
            await srv.close()

    _run(main())


def test_a_start_that_fails_after_the_bind_closes_its_socket(home, monkeypatch):
    """start_unix binds its own socket under the umask and then starts the server on it (#563). A start that fails
    there closes that socket and lets go of the lock, and the next start replaces the file it left."""
    path, real, socks = home / "exod.sock", asyncio.start_unix_server, []

    async def fail_once(*args, **kwargs):
        socks.append(kwargs["sock"])
        if len(socks) == 1:
            raise OSError("listen failed")
        return await real(*args, **kwargs)

    async def main():
        with pytest.raises(OSError, match="listen failed"):
            await HttpServer(Dispatcher(EchoEngine())).start_unix(path)
        assert socks[0].fileno() == -1 and ensure.probe(path) == "stale"
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(path)
        try:
            async with Client(socket=str(path)) as c:
                assert (await c.call(s.SessionCreate(title="t")))["session"]["id"]
        finally:
            await srv.close()

    monkeypatch.setattr(asyncio, "start_unix_server", fail_once)
    _run(main())


def test_a_start_cancelled_after_listen_takes_its_socket_off_the_loop(home, monkeypatch):
    """A cancel in start_unix_server's last pass finds the socket listening and on the loop. start_unix takes it off
    the loop before it closes it. Left on, its number went to the next socket on that loop, which then never woke for
    a client (#563 review)."""
    real, fds = asyncio.start_unix_server, []

    async def cancelled(*args, **kwargs):
        fds.append(kwargs["sock"].fileno())
        await real(*args, **kwargs)  # listening and on the loop, as a cancel in its last pass finds it
        raise asyncio.CancelledError

    async def main():
        monkeypatch.setattr(asyncio, "start_unix_server", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await HttpServer(Dispatcher(EchoEngine())).start_unix(home / "a.sock")
        monkeypatch.setattr(asyncio, "start_unix_server", real)
        assert fds[0] not in asyncio.get_running_loop()._selector.get_map()
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(home / "b.sock")  # its socket takes the number the first one let go
        try:
            async with Client(socket=str(home / "b.sock")) as c:
                assert (await c.call(s.SessionCreate(title="t")))["session"]["id"]
        finally:
            await srv.close()

    _run(main())


def test_a_start_whose_chmod_fails_leaves_no_server(home, monkeypatch):
    """start_unix sets the socket file's mode before the server starts on it. A chmod that fails leaves no server
    behind, so close still works (`PeerService.stop` closes a server whose start failed), and the next start replaces
    the file (#563 review)."""
    path, real = home / "exod.sock", os.chmod

    def chmod(p, *args, **kwargs):
        if Path(p) == path:
            raise PermissionError("chmod failed")
        return real(p, *args, **kwargs)

    async def main():
        first = HttpServer(Dispatcher(EchoEngine()))
        monkeypatch.setattr(os, "chmod", chmod)
        with pytest.raises(PermissionError, match="chmod failed"):
            await first.start_unix(path)
        monkeypatch.setattr(os, "chmod", real)
        assert ensure.probe(path) == "stale"
        await first.close()
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(path)
        try:
            async with Client(socket=str(path)) as c:
                assert (await c.call(s.SessionCreate(title="t")))["session"]["id"]
        finally:
            await srv.close()

    _run(main())


# ---------------------------------------------------------------- cmd_serve


@pytest.fixture
def serve_env(home, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_SEAT", "default")  # cmd_serve sets EXO_SEAT; this restores it afterwards
    monkeypatch.setattr("exo.core.tools.builtin.shell.raise_nofile_limit", lambda: None)
    (home / "run").mkdir(mode=0o700)
    return home


def _args(**kw):
    return SimpleNamespace(**{"seat": None, "engine": "echo", "stdio": False, "tcp": None, "socket": None, **kw})


def _no_engine(spec):
    raise AssertionError("a refused serve must not load an engine (writer lock, plugins)")


def _no_serve(main):
    main.close()
    raise AssertionError("a refused serve must not run")


def test_second_serve_for_another_seat_leaves_the_live_core_alone(serve_env, monkeypatch, capsys):
    sock, pid = ensure.exod_socket(serve_env), ensure.pid_path(serve_env)
    srv = listener(sock)
    pid.write_text("4242\n")
    inode = sock.stat().st_ino
    monkeypatch.setattr("exo.server.app.load_engine", _no_engine)
    monkeypatch.setattr(cli_chat.asyncio, "run", _no_serve)
    try:
        assert cli_chat.cmd_serve(_args(seat="scout")) == 1
        assert sock.stat().st_ino == inode and ensure.probe(sock) == "alive"
    finally:
        srv.close()
    assert pid.read_text() == "4242\n"
    err = capsys.readouterr().err
    assert f"another core is listening on {sock}" in err and "--socket" in err


def test_second_serve_on_an_explicit_live_socket_is_refused(serve_env, monkeypatch, capsys):
    sock = serve_env / "run" / "scout.sock"
    srv = listener(sock)
    inode = sock.stat().st_ino
    monkeypatch.setattr("exo.server.app.load_engine", _no_engine)
    monkeypatch.setattr(cli_chat.asyncio, "run", _no_serve)
    try:
        assert cli_chat.cmd_serve(_args(seat="scout", socket=str(sock))) == 1
        assert sock.stat().st_ino == inode
    finally:
        srv.close()
    assert "--socket" in capsys.readouterr().err


def test_another_seat_on_its_own_socket_starts_beside_a_live_core(serve_env, monkeypatch):
    sock, pid = ensure.exod_socket(serve_env), ensure.pid_path(serve_env)
    srv = listener(sock)
    pid.write_text("4242\n")
    ran = []
    monkeypatch.setattr("exo.server.app.load_engine", lambda spec: object())
    monkeypatch.setattr(cli_chat.asyncio, "run", lambda main: (ran.append(1), main.close()))
    try:
        assert cli_chat.cmd_serve(_args(seat="scout", socket=str(serve_env / "run" / "scout.sock"))) == 0
    finally:
        srv.close()
    assert ran == [1] and pid.read_text() == "4242\n"  # only the core on run/exod.sock owns run/exod.pid


def test_stdio_serve_does_not_look_at_the_socket(serve_env, monkeypatch):
    sock = ensure.exod_socket(serve_env)
    srv = listener(sock)
    ran = []
    monkeypatch.setattr("exo.server.app.load_engine", lambda spec: object())
    monkeypatch.setattr(cli_chat.asyncio, "run", lambda main: (ran.append(1), main.close()))
    try:
        assert cli_chat.cmd_serve(_args(stdio=True)) == 0
    finally:
        srv.close()
    assert ran == [1] and not ensure.pid_path(serve_env).exists()


def test_serve_teardown_keeps_a_pid_file_another_core_wrote(serve_env, monkeypatch):
    pid = ensure.pid_path(serve_env)
    monkeypatch.setattr("exo.server.app.load_engine", lambda spec: object())

    async def fake_serve(engine, claimed=None, **kw):
        claimed()  # this core owns the socket and records itself, then another core overwrites the file
        assert pid.read_text() == f"{os.getpid()}\n"
        pid.write_text("4242\n")

    monkeypatch.setattr("exo.server.app.serve", fake_serve)
    assert cli_chat.cmd_serve(_args()) == 0
    assert pid.read_text() == "4242\n"


def test_a_serve_that_loses_the_start_lock_exits_1_and_leaves_the_pid_file(serve_env, monkeypatch, capsys):
    pid = ensure.pid_path(serve_env)
    pid.write_text("4242\n")
    monkeypatch.setattr("exo.server.app.load_engine", lambda spec: EchoEngine())

    from exo.server.app import _take_start_lock
    fd = _take_start_lock(ensure.exod_socket(serve_env))  # a core that holds the lock and has not bound yet
    try:
        assert cli_chat.cmd_serve(_args()) == 1
    finally:
        os.close(fd)
    assert pid.read_text() == "4242\n"  # claimed() never ran: only the lock holder writes it
    err = capsys.readouterr().err
    assert f"another exo core is starting or running on {ensure.exod_socket(serve_env)} (pid {os.getpid()})" in err
    assert "--socket" in err


# ---------------------------------------------------------------- the start lock (#242)

_CHILD = """
import asyncio, os, sys, time
from pathlib import Path
from exo.server.app import Dispatcher, HttpServer
from exo.server.echo import EchoEngine
sock, go, hold = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
async def main():
    srv = HttpServer(Dispatcher(EchoEngine()))
    while time.time() < go:
        pass
    try:
        await srv.start_unix(sock, claimed=lambda: (sock.parent / "winner").write_text(str(os.getpid())))
    except ValueError as exc:
        print("lost", exc, flush=True)
        return
    print("won", flush=True)
    await asyncio.sleep(hold)
    await srv.close()
asyncio.run(main())
"""


def _race(sock: Path, n: int, hold: float = 1.5) -> list[subprocess.Popen]:
    go = time.time() + 1.5  # every child imports first, then spins until the same instant
    return [subprocess.Popen([sys.executable, "-c", _CHILD, str(sock), str(go), str(hold)],
                             env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(n)]


def test_two_cores_started_at_once_leave_exactly_one(home):
    for trial in range(4):
        sock = home / f"t{trial}" / "exod.sock"
        procs = _race(sock, 6)
        outs = [(p, *p.communicate(timeout=60)) for p in procs]
        assert all(p.returncode == 0 for p, _, err in outs), [err for *_, err in outs]
        won = [p for p, out, _ in outs if out.startswith("won")]
        assert len(won) == 1, [out for _, out, _ in outs]
        assert (sock.parent / "winner").read_text() == str(won[0].pid)  # the pid recorded is the survivor's
        assert not sock.exists() and not sock.with_name("exod.sock.lock").exists()  # the winner cleaned up


def test_two_starts_inside_the_probe_window_leave_one(home, monkeypatch):
    """Deterministic form of the race: a slow probe holds both starters between probe and bind (before #242 both
    went on to unlink and bind, and the second took the first's socket)."""
    import threading
    real = ensure.probe

    def slow_probe(path):
        out = real(path)
        time.sleep(0.4)
        return out

    monkeypatch.setattr(ensure, "probe", slow_probe)
    sock = home / "exod.sock"
    results: list[str] = []
    gate = threading.Barrier(2)

    def start():
        async def main():
            srv = HttpServer(Dispatcher(EchoEngine()))
            gate.wait()
            try:
                await srv.start_unix(sock)
            except ValueError:
                results.append("lost")
                return
            results.append("won")
            await asyncio.sleep(1.0)  # both attempts are over before the winner lets go
            await srv.close()

        asyncio.run(main())

    threads = [threading.Thread(target=start) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert sorted(results) == ["lost", "won"]


def test_the_loser_names_the_running_core(home):
    sock = home / "exod.sock"

    async def main():
        first = HttpServer(Dispatcher(EchoEngine()))
        await first.start_unix(sock)
        try:
            with pytest.raises(ValueError) as exc:
                await HttpServer(Dispatcher(EchoEngine())).start_unix(sock)
        finally:
            await first.close()
        return str(exc.value)

    msg = _run(main())
    assert f"another exo core is starting or running on {sock} (pid {os.getpid()})" in msg and "--socket" in msg


def test_a_lock_the_loser_holds_before_bind_also_refuses(home):
    """The window the probe misses: a core holds the lock but has not bound yet."""
    sock = home / "exod.sock"
    from exo.server.app import _take_start_lock
    fd = _take_start_lock(sock)
    try:
        with pytest.raises(ValueError, match="starting or running"):
            _run(HttpServer(Dispatcher(EchoEngine())).start_unix(sock))
    finally:
        os.close(fd)


def test_a_start_after_a_crash_still_works(home):
    sock = home / "exod.sock"
    code = ("import sys, asyncio; from pathlib import Path\n"
            "from exo.server.app import Dispatcher, HttpServer\nfrom exo.server.echo import EchoEngine\n"
            "async def m():\n s = HttpServer(Dispatcher(EchoEngine())); await s.start_unix(Path(sys.argv[1]))\n"
            " print('up', flush=True); import os; os.kill(os.getpid(), 9)\nasyncio.run(m())\n")
    p = subprocess.run([sys.executable, "-c", code, str(sock)], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert "up" in p.stdout and p.returncode == -9
    assert sock.exists() and sock.with_name("exod.sock.lock").exists()  # a killed core leaves both behind
    assert ensure.probe(sock) == "stale"

    async def main():
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(sock)
        try:
            async with Client(socket=str(sock)) as c:
                assert (await c.call(s.SessionCreate(title="t")))["session"]["id"]
        finally:
            await srv.close()

    _run(main())


def test_the_lock_file_is_owner_only_like_the_socket_folder(home):
    sock = home / "sub" / "exod.sock"
    lock = sock.with_name("exod.sock.lock")

    async def main():
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(sock)
        try:
            assert lock.stat().st_mode & 0o777 == 0o600 and lock.read_text().strip() == str(os.getpid())
            assert sock.parent.stat().st_mode & 0o777 == 0o700 and sock.stat().st_mode & 0o777 == 0o600
        finally:
            await srv.close()

    _run(main())
    lock.touch(mode=0o666)
    lock.chmod(0o666)  # a loose lock file left by something else is tightened, not trusted
    from exo.server.app import _take_start_lock
    os.close(_take_start_lock(sock))
    assert lock.stat().st_mode & 0o777 == 0o600


def test_close_does_not_unlink_a_socket_a_new_core_bound_after_it(home):
    sock = home / "exod.sock"

    async def main():
        first = HttpServer(Dispatcher(EchoEngine()))
        await first.start_unix(sock)
        await first.close()  # socket and lock are gone before the lock is let go
        second = HttpServer(Dispatcher(EchoEngine()))
        await second.start_unix(sock)
        try:
            assert ensure.probe(sock) == "alive"
            await first.close()  # a second close is a no-op and leaves the new core's files
            assert ensure.probe(sock) == "alive" and sock.with_name("exod.sock.lock").exists()
        finally:
            await second.close()

    _run(main())


def test_a_socket_in_a_symlinked_folder_still_binds(home):
    """macOS /tmp is a symlink; main binds there, so the lock file must not go through private_dir."""
    (home / "real").mkdir()
    (home / "link").symlink_to(home / "real")
    sock = home / "link" / "x.sock"

    async def main():
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(sock)
        try:
            assert ensure.probe(sock) == "alive"
            assert (home / "real" / "x.sock.lock").stat().st_mode & 0o777 == 0o600
        finally:
            await srv.close()

    _run(main())


def test_serve_teardown_does_not_unlink_a_socket_a_new_core_bound_after_close(home, monkeypatch):
    """serve() once unlinked the socket after closing its server, which could delete the next core's."""
    import exo.server.app as app
    sock = home / "exod.sock"
    successors: list[HttpServer] = []
    real_close = app.HttpServer.close

    async def close(self):
        await real_close(self)
        if not successors:  # the lock is free and the socket gone: a new core binds at once
            nxt = HttpServer(Dispatcher(EchoEngine()))
            await nxt.start_unix(sock)
            successors.append(nxt)

    monkeypatch.setattr(app.HttpServer, "close", close)

    async def main():
        task = asyncio.create_task(app.serve(EchoEngine(), socket=sock))
        while ensure.probe(sock) != "alive":
            await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            assert ensure.probe(sock) == "alive"
        finally:
            monkeypatch.undo()
            for nxt in successors:
                await nxt.close()

    _run(main())


def test_a_waiter_that_locked_an_unlinked_file_does_not_hold_the_lock(home, monkeypatch):
    """The holder lets go between a waiter's open and its flock, and a third start takes the new file."""
    import fcntl
    from exo.server import app
    sock = home / "exod.sock"
    first = app._take_start_lock(sock)
    real = fcntl.flock
    state: dict = {}

    def flock(fd, op):
        if "third" not in state:
            state["third"] = None
            sock.with_name("exod.sock.lock").unlink()  # the holder's release
            os.close(first)
            state["third"] = app._take_start_lock(sock)  # a third start, on a new file
        return real(fd, op)

    monkeypatch.setattr(fcntl, "flock", flock)
    try:
        with pytest.raises(app.SocketBusy):  # the waiter's lock was on the old file, so it must retry and lose
            app._take_start_lock(sock)
    finally:
        os.close(state["third"])


def test_close_removes_the_socket_before_the_lock_file(home, monkeypatch):
    """Once the lock file is gone a waiter takes a new one, so the socket must already be gone by then."""
    import exo.server.app as app
    sock = home / "exod.sock"
    order: list[str] = []
    real_lock_path = app._lock_path

    def recording(name):
        class P(type(Path())):
            def unlink(self, missing_ok=False):
                order.append(name)
                return super().unlink(missing_ok=missing_ok)
        return P

    monkeypatch.setattr(app, "_lock_path", lambda p: recording("lock")(real_lock_path(p)))

    async def main():
        srv = HttpServer(Dispatcher(EchoEngine()))
        await srv.start_unix(sock)
        srv._unix_path = recording("socket")(srv._unix_path)
        await srv.close()

    _run(main())
    assert order == ["socket", "lock"]


def test_only_a_socket_refusal_is_a_plain_exit_line(serve_env, monkeypatch):
    import json
    monkeypatch.setattr("exo.server.app.load_engine", lambda spec: object())

    async def boom(engine, **kw):
        json.loads("{not json")  # a ValueError that is a real bug keeps its traceback

    monkeypatch.setattr("exo.server.app.serve", boom)
    with pytest.raises(json.JSONDecodeError):
        cli_chat.cmd_serve(_args())


def test_stop_core_says_a_core_is_starting_and_removes_nothing(home):
    from exo.server.app import _take_start_lock
    sock, pid = ensure.exod_socket(home), ensure.pid_path(home)
    (home / "run").mkdir(mode=0o700)
    pid.write_text("4242\n")
    fd = _take_start_lock(sock)  # a core between claimed() and bind
    try:
        msg = ensure.stop_core(home)
    finally:
        os.close(fd)
    assert f"is starting (pid {os.getpid()})" in msg and "nothing was removed" in msg
    assert pid.read_text() == "4242\n"


def test_stop_core_with_no_core_clears_the_stale_files(home):
    sock, pid = ensure.exod_socket(home), ensure.pid_path(home)
    (home / "run").mkdir(mode=0o700)
    listener(sock).close()
    pid.write_text("4242\n")
    assert ensure.stop_core(home) == "Exo core (exod) is not running."
    assert not sock.exists() and not pid.exists()


def test_ensure_core_leaves_a_stale_socket_for_the_core_to_replace(home):
    sock = ensure.exod_socket(home)
    listener(sock).close()
    dead = SimpleNamespace(poll=lambda: 1, gone=True)
    with pytest.raises(ensure.CoreUnavailable):
        ensure.ensure_core(home, agent="nobody", timeout=2, spawn=lambda h: dead)
    assert sock.exists()


# ---------------------------------------------------------------- real processes


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("EXO_")}
    return {**env, "EXO_HOME": str(home), "EXO_ENGINE": "echo", "PYTHONUNBUFFERED": "1"}


def _exo(*args: str) -> list[str]:
    return [sys.executable, "-m", "exo.cli", *args]


def _wait_alive(path: Path, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if ensure.probe(path) == "alive":
            return
        assert proc.poll() is None, proc.stderr.read() if proc.stderr else ""
        time.sleep(0.1)
    raise AssertionError(f"nothing answered on {path}")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)


def test_two_seats_in_one_home_then_exo_stop_stops_the_first(home):
    """The finding's repro: a default-seat exod runs, then `exo serve --seat scout`, then `exo stop`."""
    sock, pid, own = ensure.exod_socket(home), ensure.pid_path(home), home / "run" / "scout.sock"
    procs = [subprocess.Popen(_exo("serve"), env=_env(home), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True)]
    try:
        _wait_alive(sock, procs[0])
        inode = sock.stat().st_ino
        assert pid.read_text().strip() == str(procs[0].pid)

        second = subprocess.run(_exo("serve", "--seat", "scout"), env=_env(home), capture_output=True, text=True,
                                timeout=30)
        assert second.returncode == 1, second.stderr
        assert "another core is listening" in second.stderr and "--socket" in second.stderr
        assert sock.stat().st_ino == inode and pid.read_text().strip() == str(procs[0].pid)

        procs.append(subprocess.Popen(_exo("serve", "--seat", "scout", "--socket", str(own)), env=_env(home),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True))
        _wait_alive(own, procs[1])
        assert pid.read_text().strip() == str(procs[0].pid)

        assert ensure.stop_core(home) == f"Exo core stopped (pid {procs[0].pid})."
        assert procs[0].poll() is not None and procs[1].poll() is None and ensure.probe(own) == "alive"
        assert not pid.exists()
    finally:
        for proc in procs:
            _stop(proc)
