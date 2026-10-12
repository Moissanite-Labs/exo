"""Who is on the other end of a connection (D-039).

The core never takes the answerer of an approval from the command body. The transport stamps a caller:

- ``principal``: an interactive client of the principal. In process (the terminal client's own core, the web
  client's in-process dispatcher) by construction; over the Unix socket only with the per-launch principal
  credential in ``X-Exo-Principal`` and only when the connecting process is not a descendant of the core (a tool
  subprocess is one).
- ``local``: anything else that reaches the socket (a tool subprocess, a channel link, another daemon), and every
  stdio or TCP connection. A local caller drives turns but answers approvals only as a seat, with a D-033
  signature.

The credential is a random token written per launch beside the socket (``run/exod.sock`` gives
``run/exod.principal.key``; mode 0600, in the 0700 run folder). The ``.key`` name puts it under the gate's
credentials floor, it is never put in any environment, and ``tool_env`` drops every variable that could point at
it. It is removed when the core stops.
"""
from __future__ import annotations

import contextvars
import hmac
import os
import re
import secrets
import socket as _socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HEADER = "X-Exo-Principal"
PRINCIPAL = "principal"
LOCAL = "local"


@dataclass
class Caller:
    who: str
    live: bool = True  # False once the command that set it has returned; tasks it spawned see "unknown"


CALLER: contextvars.ContextVar[Caller | None] = contextvars.ContextVar("exo_caller", default=None)


def current_caller() -> str:
    """The stamped caller of the command being handled now, or ``unknown`` outside one."""
    c = CALLER.get()
    return c.who if c is not None and c.live else "unknown"


def is_principal(who: str) -> bool:
    return who == PRINCIPAL or who.startswith(PRINCIPAL + ":")


def principal_as(label: str = "") -> str:
    """``principal`` or ``principal:<label>``; the label is display only (tui, web, chat)."""
    label = re.sub(r"[^a-z0-9._-]", "", (label or "").lower())[:40]
    return f"{PRINCIPAL}:{label}" if label else PRINCIPAL


# ---------------------------------------------------------------- the credential file


def token_path(sock: str | os.PathLike) -> Path:
    p = Path(sock)
    return p.with_name(p.stem + ".principal.key")


def issue(sock: str | os.PathLike) -> str:
    """A fresh credential for the core serving ``sock``; any old file is replaced."""
    path = token_path(sock)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token + "\n")
    os.replace(tmp, path)
    return token


def read(sock: str | os.PathLike) -> str | None:
    try:
        return token_path(sock).read_text().strip() or None
    except OSError:
        return None


def revoke(sock: str | os.PathLike, token: str) -> None:
    """Remove the credential file if it is still ours (a newer core may have replaced it)."""
    if read(sock) == token:
        token_path(sock).unlink(missing_ok=True)


def matches(sent: str | None, token: str | None) -> bool:
    return bool(sent and token) and hmac.compare_digest(sent.encode(), token.encode())


# ---------------------------------------------------------------- the peer process


def peer_pid(sock: object) -> int | None:
    """Pid of the process at the other end of a Unix socket, or None where the OS will not say."""
    try:
        if sys.platform == "darwin":
            return int(sock.getsockopt(0, 0x002))  # SOL_LOCAL, LOCAL_PEERPID
        if hasattr(_socket, "SO_PEERCRED"):
            pid, _uid, _gid = struct.unpack("3i", sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_PEERCRED, 12))
            return pid
    except (OSError, AttributeError, TypeError, ValueError):
        return None
    return None


def _ppid_darwin(pid: int) -> int | None:
    """libproc's PROC_PIDTBSDINFO: struct proc_bsdinfo is 136 bytes and pbi_ppid is the fifth uint32."""
    import ctypes
    import ctypes.util

    global _LIBPROC
    if _LIBPROC is None:
        _LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
    buf = ctypes.create_string_buffer(136)
    if _LIBPROC.proc_pidinfo(ctypes.c_int(pid), ctypes.c_int(3), ctypes.c_uint64(0), buf, ctypes.c_int(136)) != 136:
        return None
    return struct.unpack_from("I", buf.raw, 16)[0]


_LIBPROC = None


def _ppid(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        pass
    if sys.platform == "darwin":
        try:
            if (got := _ppid_darwin(pid)) is not None:
                return got
        except (OSError, AttributeError):
            pass
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
        return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def descends_from(pid: int, ancestor: int) -> bool:
    """True when ``pid`` is a child, grandchild, ... of ``ancestor`` (not ``ancestor`` itself)."""
    seen = set()
    cur = _ppid(pid) if pid != ancestor else None
    while cur and cur > 1 and cur not in seen:
        if cur == ancestor:
            return True
        seen.add(cur)
        cur = _ppid(cur)
    return False


def stamp(sent: str | None, token: str | None, sock: object, label: str = "") -> str:
    """The caller of one HTTP request on the Unix socket."""
    if not matches(sent, token):
        return LOCAL
    pid = peer_pid(sock)
    if pid is None or descends_from(pid, os.getpid()):  # a tool subprocess of this core holds no consent
        return LOCAL
    return principal_as(label)
