"""Find a running core for an agent, or start one (D-036).

Clients reach one agent through a Unix socket: ``run/exod.sock`` (``exo serve``) or, while a terminal window runs
the core in process, ``run/agent-<agent>.sock`` (D-028). ``ensure_core`` returns whichever answers; with neither
it starts ``exo serve`` detached (one start at a time, guarded by a file lock), and waits until it answers.
A running core is never started twice: the agent's writer lock still has the last word.
"""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import socket as _socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from exo.core.agentlog import lock_path, socket_path
from exo.core.private import private_dir, private_file

START_TIMEOUT = 40.0


class CoreUnavailable(RuntimeError):
    """No core could be reached or started; the message says what to do."""


def home_dir() -> Path:
    return Path(os.environ.get("EXO_HOME") or Path.home() / ".exo").expanduser()


def agent_name() -> str:
    return os.environ.get("EXO_SEAT") or "default"


def exod_socket(home: Path) -> Path:
    return home / "run" / "exod.sock"


def log_path(home: Path) -> Path:
    return home / "logs" / "exod.log"


def pid_path(home: Path) -> Path:
    return home / "run" / "exod.pid"


def probe(path: Path) -> str:
    """'alive' (answers), 'stale' (a socket file nobody listens on), or 'none'."""
    if not path.exists():
        return "none"
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(str(path))
        return "alive"
    except (ConnectionRefusedError, FileNotFoundError):
        return "stale"
    except OSError as exc:
        return "stale" if exc.errno in (errno.ECONNREFUSED, errno.ENOENT, errno.ENOTSOCK) else "alive"
    finally:
        s.close()


def find_core(home: Path, agent: str) -> Path | None:
    """The socket of a core that answers now: exod first, then a terminal window's in-process core."""
    for p in (exod_socket(home), socket_path(home, agent)):
        if probe(p) == "alive":
            return p
    return None


def writer_holder(home: Path, agent: str) -> str | None:
    """None when nobody holds the agent's writer lock, else the holder's pid (or '?')."""
    path = lock_path(home, agent)
    if not path.exists():
        return None
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            return fh.read().strip() or "?"
        fcntl.flock(fh, fcntl.LOCK_UN)
    return None


def _daemonize(home: Path) -> None:
    """Fork only in a fresh helper interpreter, never in a threaded client.

    The helper's exit hands the core to init (or the nearest subreaper), so the core is never a child of the client
    that started it. This is hardening, not the cause of #145: ``principal.stamp`` walks the *peer's* ancestors for
    the core, and a client that started the core is its ancestor, never its descendant.
    """
    os.setsid()
    pid = os.fork()
    if pid:
        os.write(1, f"\n{pid}\n".encode())  # the last line: startup output (sitecustomize, .pth) may come first
        os._exit(0)
    # Close the PID channel immediately, including on an exec failure.
    out = os.open(private_file(log_path(home)), os.O_WRONLY | os.O_APPEND)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.close(out)
    # -P: the client's cwd is not put first on sys.path (a pathlib.py there would run as the core).
    os.execvpe(sys.executable, [sys.executable, "-P", "-m", "exo.cli", "serve"],
               dict(os.environ, EXO_HOME=str(home)))


_DAEMONIZE_SRC = ("import pathlib, sys; from exo.ensure_core import _daemonize;"
                  "_daemonize(pathlib.Path(sys.argv[1]))")


class _DetachedCore:
    """The detached core: not our child, unless this process reaps orphans (PID 1 or a subreaper).

    ``poll`` is ``Popen.poll``: the exit code once known, else None. ``gone`` is set when the core exited (or is a zombie)
    and is not this process's child, so its exit code cannot be known here.
    """

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode: int | None = None
        self.gone = False

    def poll(self) -> int | None:
        if self.returncode is not None or self.gone:
            return self.returncode
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
                self.gone = True
            return self.returncode
        try:
            self.gone = Path(f"/proc/{self.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
        except (OSError, IndexError):
            pass
        if not self.gone:
            try:
                os.kill(self.pid, 0)
            except (ProcessLookupError, PermissionError):  # EPERM: the pid now belongs to someone else
                self.gone = True
        return None


def _exit_text(proc: subprocess.Popen | _DetachedCore) -> str:
    code = proc.poll()
    if code is None and getattr(proc, "gone", False):
        return "exited; it is not this process's child, so the exit code is not known"
    return f"exit {code}"


def _spawn(home: Path, timeout: float = START_TIMEOUT) -> _DetachedCore:
    with open(private_file(log_path(home)), "ab") as out:
        proc = subprocess.Popen([sys.executable, "-P", "-c", _DAEMONIZE_SRC, str(home)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=out,
                                env=dict(os.environ, EXO_HOME=str(home)))
        try:
            result, _ = proc.communicate(timeout=max(0.001, timeout))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            # Do not read until EOF: a broken helper may have leaked the pipe
            # into a descendant which remains alive after the helper is killed.
            if proc.stdout is not None:
                proc.stdout.close()
            raise CoreUnavailable(f"Exo core startup helper timed out. See {log_path(home)}") from None
    pid = (result.strip().splitlines() or [b""])[-1].strip()
    if proc.returncode or not pid.isdigit() or int(pid) <= 1:
        raise CoreUnavailable(f"Exo core startup helper failed (exit {proc.returncode}). "
                              f"See {log_path(home)}:\n{_tail(log_path(home))}")
    private_file(pid_path(home)).write_text(f"{int(pid)}\n")
    return _DetachedCore(int(pid))


def _tail(path: Path, n: int = 6) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").strip().splitlines()[-n:])
    except OSError:
        return ""


@contextmanager
def startup_lock(home: Path, timeout: float = START_TIMEOUT) -> Iterator[None]:
    """Shared TUI/exod starter gate; the writer lock remains the ownership authority."""
    private_dir(home / "run")
    deadline = time.monotonic() + timeout
    with open(private_file(home / "run" / "exod-start.lock"), "a+") as gate:
        while True:
            try:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoreUnavailable("another process has been starting Exo's core for too long; try again")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(gate, fcntl.LOCK_UN)


def ensure_core(home: Path | None = None, agent: str | None = None, timeout: float = START_TIMEOUT,
                say: Callable[[str], None] | None = None, spawn: Callable[[Path], subprocess.Popen | _DetachedCore] | None = None
                ) -> Path:
    """Socket of a core for this agent, starting ``exo serve`` in the background if none is running."""
    home = home or home_dir()
    agent = agent or agent_name()
    if (found := find_core(home, agent)) is not None:
        return found
    deadline = time.monotonic() + timeout
    proc = None
    holder = None
    with startup_lock(home, timeout):
        if (found := find_core(home, agent)) is not None:
            return found
        holder = writer_holder(home, agent)
        if holder is None:
            if probe(exod_socket(home)) == "stale":
                exod_socket(home).unlink(missing_ok=True)
            if say:
                say("Exo core is not running, starting it...")
            proc = spawn(home) if spawn else _spawn(home, deadline - time.monotonic())
        elif say:
            say(f"Agent {agent} is held by process {holder}; waiting for it to share it...")
        while time.monotonic() < deadline:
            if (found := find_core(home, agent)) is not None:
                return found
            if proc is not None and (proc.poll() is not None or getattr(proc, "gone", False)):
                break
            time.sleep(0.1)
    if proc is not None:
        tail = _tail(log_path(home))
        raise CoreUnavailable(f"Exo core did not start ({_exit_text(proc)}). See {log_path(home)}"
                              + (f":\n{tail}" if tail else ""))
    raise CoreUnavailable(
        f"Agent {agent} is held by process {holder}, which is not sharing it. Close that Exo window "
        f"(or run `exo stop`), then try again.")


def stop_core(home: Path | None = None, timeout: float = 10.0) -> str:
    """Stop the background exod this machine started. Returns a plain sentence."""
    home = home or home_dir()
    if probe(exod_socket(home)) != "alive":
        exod_socket(home).unlink(missing_ok=True)
        pid_path(home).unlink(missing_ok=True)
        return "Exo core (exod) is not running."
    try:
        pid = int(pid_path(home).read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return (f"exod is running but was not started by exo (no usable {pid_path(home)}); "
                "stop it where you started it.")
    os.kill(pid, signal.SIGTERM)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.waitpid(pid, os.WNOHANG)  # reaps it when this process adopted it (PID 1 or a subreaper)
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except OSError:
            pid_path(home).unlink(missing_ok=True)
            return f"Exo core stopped (pid {pid})."
        time.sleep(0.1)
    return f"Sent stop to pid {pid}; it is still shutting down."
