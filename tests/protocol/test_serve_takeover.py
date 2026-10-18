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
            with pytest.raises(ValueError, match="another server is listening.*--socket"):
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

    def run(main):
        main.close()
        assert pid.read_text() == f"{os.getpid()}\n"
        pid.write_text("4242\n")  # another core won the race for the socket and recorded itself

    monkeypatch.setattr(cli_chat.asyncio, "run", run)
    assert cli_chat.cmd_serve(_args()) == 0
    assert pid.read_text() == "4242\n"


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
