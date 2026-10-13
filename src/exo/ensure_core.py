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
from pathlib import Path
from typing import Callable

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


def _spawn(home: Path) -> subprocess.Popen:
    log = log_path(home)
    out = open(private_file(log), "ab")
    try:
        proc = subprocess.Popen([sys.executable, "-m", "exo.cli", "serve"], stdin=subprocess.DEVNULL, stdout=out,
                                stderr=out, start_new_session=True, env=dict(os.environ, EXO_HOME=str(home)))
    finally:
        out.close()
    private_file(pid_path(home)).write_text(f"{proc.pid}\n")
    return proc


def _tail(path: Path, n: int = 6) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").strip().splitlines()[-n:])
    except OSError:
        return ""


def ensure_core(home: Path | None = None, agent: str | None = None, timeout: float = START_TIMEOUT,
                say: Callable[[str], None] | None = None, spawn: Callable[[Path], subprocess.Popen] | None = None
                ) -> Path:
    """Socket of a core for this agent, starting ``exo serve`` in the background if none is running."""
    home = home or home_dir()
    agent = agent or agent_name()
    spawn = spawn or _spawn
    if (found := find_core(home, agent)) is not None:
        return found
    private_dir(home / "run")
    deadline = time.monotonic() + timeout
    proc = None
    holder = None
    with open(private_file(home / "run" / "exod-start.lock"), "a+") as gate:
        while True:  # one starter at a time; the others wait here, then find its socket
            try:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if (found := find_core(home, agent)) is not None:
                    return found
                if time.monotonic() > deadline:
                    raise CoreUnavailable("another process has been starting Exo's core for too long; try again")
                time.sleep(0.1)
        if (found := find_core(home, agent)) is not None:
            return found
        holder = writer_holder(home, agent)
        if holder is None:
            if probe(exod_socket(home)) == "stale":
                exod_socket(home).unlink(missing_ok=True)
            if say:
                say("Exo core is not running, starting it...")
            proc = spawn(home)
        elif say:
            say(f"Agent {agent} is held by process {holder}; waiting for it to share it...")
        while time.monotonic() < deadline:
            if (found := find_core(home, agent)) is not None:
                return found
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(0.1)
    if proc is not None:
        tail = _tail(log_path(home))
        raise CoreUnavailable(f"Exo core did not start (exit {proc.poll()}). See {log_path(home)}"
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
            os.waitpid(pid, os.WNOHANG)  # reaps it when it is our own child (tests); else ChildProcessError
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except OSError:
            pid_path(home).unlink(missing_ok=True)
            return f"Exo core stopped (pid {pid})."
        time.sleep(0.1)
    return f"Sent stop to pid {pid}; it is still shutting down."
