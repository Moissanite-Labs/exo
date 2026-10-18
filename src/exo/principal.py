"""Who is on the other end of a connection (D-039).

The core never takes the answerer of an approval from the command body. The transport stamps a caller:

- ``principal``: an interactive client of the principal. In process (the terminal client's own core, the web
  client's in-process dispatcher) by construction; over the Unix socket only with the per-launch principal
  credential in ``X-Exo-Principal`` and only when the connecting process is not a descendant of the core (a tool
  subprocess is one).
- ``local``: anything else that reaches the socket (a tool subprocess, a channel link, another daemon), and every
  stdio or TCP connection. A local caller drives turns but answers approvals only as a seat, with a D-033
  signature. A local turn is authored as local, never as the principal.

The credential is a random token in a launch record written per launch beside the socket (``run/exod.sock``
gives ``run/exod.principal.key``; mode 0600, in the 0700 run folder). The ``.key`` name puts it under the gate's
credentials floor, it is never put in any environment, and ``tool_env`` drops every variable that could point at
it. It is removed when the core stops.

Unix peer authentication (D-060) runs in both directions, on the kernel's view of the one connection in use:

- A principal client connects first and asks the kernel who serves the socket (``SO_PEERCRED`` on Linux,
  ``LOCAL_PEERCRED`` and ``LOCAL_PEERPID`` on macOS). Only when that is this user and the very process the launch
  record names (pid and process start identity, so a stale record or a reused pid fails) does it put the token
  from that record on that same connection. Any doubt refuses principal mode; nothing falls back.
- The core stamps ``principal`` only when the token matches and the kernel's peer is this user, still the process
  that made the connection (the start identity read at accept, and it started before the core accepted it), and its
  whole parent chain resolves to pid 1 without passing through the core. A chain that cannot be read is treated as
  the core's own tool tree. A credential taken as local is said once in the core's log, and the connection's
  refusals name the reason (``demoted``).

Same-user code that runs outside the core's tree is trusted by this design: it can read the record. D-039's limit
(an OS sandbox for tools) still applies.
"""
from __future__ import annotations

import contextvars
import hmac
import json
import os
import re
import secrets
import socket as _socket
import struct
import stat
import subprocess
import sys
import time
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


def ingress_author() -> str:
    """Snapshot a command's author while its transport caller is live."""
    return PRINCIPAL if is_principal(current_caller()) else LOCAL


def require_principal(operation: str) -> str:
    """Require live transport authority before reading or mutating principal-owned state."""
    who = current_caller()
    if not is_principal(who):
        from exo.server.engine import CommandFailed
        raise CommandFailed("forbidden", f"only the principal can {operation}; use the Exo window or web page"
                            + demoted())
    return who


DEMOTED: contextvars.ContextVar[str] = contextvars.ContextVar("exo_demoted", default="")


def demoted() -> str:
    """For a refusal message: why the live command's principal credential was taken as local, or ""."""
    c, why = CALLER.get(), DEMOTED.get()
    if not why or c is None or not c.live or is_principal(c.who):
        return ""
    return f" (this connection carried the principal credential but is local: {why})"


def principal_as(label: str = "") -> str:
    """``principal`` or ``principal:<label>``; the label is display only (tui, web, chat)."""
    label = re.sub(r"[^a-z0-9._-]", "", (label or "").lower())[:40]
    return f"{PRINCIPAL}:{label}" if label else PRINCIPAL


# ---------------------------------------------------------------- the peer process (kernel view)


class PeerRefused(Exception):
    """A principal check failed; the message says which. Callers refuse principal mode, never fall back."""


@dataclass(frozen=True)
class Peer:
    """The kernel's answer for one accepted connection, taken before any byte is read from it."""

    pid: int | None  # 0: the kernel named no pid (the peer is in another PID namespace)
    uid: int | None
    at: float  # wall clock at accept; the process that connected existed before this
    ident: str | None = None  # its start identity at accept; the pid must still name this process when stamped


@dataclass(frozen=True)
class Proc:
    ppid: int
    start: float  # wall-clock start, for "existed before the connection"
    ident: str  # exact start identity, for "the same process as the launch record"


# A wall-clock step between a process start and the accept. Linux's whole-second btime makes computed starts up to
# 1 s early, which widens this window rather than needing it; ``Peer.ident`` is what pins the process.
CLOCK_SLACK = 1.0
_SOL_LOCAL, _LOCAL_PEERCRED, _LOCAL_PEERPID = 0, 0x001, 0x002  # <sys/un.h>
_XUCRED_SIZE = 76  # struct xucred: u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16]


def peer_cred(sock: object) -> tuple[int, int] | None:
    """``(pid, uid)`` of the other end of a connected Unix socket, from the kernel; None where the OS will not say.

    Linux: ``SO_PEERCRED`` (struct ucred). macOS: ``LOCAL_PEERCRED`` (struct xucred, the uid ``getpeereid`` reports)
    and ``LOCAL_PEERPID``. Any other platform answers None, and every caller then refuses principal standing. Linux
    names pid 0 for a peer in another PID namespace (and for a TCP socket); that is returned as 0, which no check
    accepts."""
    try:
        if sys.platform == "darwin":
            version, uid = struct.unpack_from("2I", sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, _XUCRED_SIZE))
            pid = int(sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID))
            return (pid, uid) if version == 0 and pid > 0 else None
        if sys.platform.startswith("linux") and hasattr(_socket, "SO_PEERCRED"):
            # struct ucred: pid_t pid; uid_t uid; gid_t gid (uid and gid are unsigned: 2**31 and above are real)
            pid, uid, _gid = struct.unpack("iII", sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_PEERCRED, 12))
            return (pid, uid) if pid >= 0 else None
    except (OSError, AttributeError, TypeError, ValueError, struct.error):
        return None
    return None


def peer_pid(sock: object) -> int | None:
    """Pid of the process at the other end of a Unix socket, or None where the OS will not say."""
    cred = peer_cred(sock)
    return cred[0] if cred and cred[0] > 0 else None


def accepted(sock: object) -> Peer:
    """Bind the kernel's peer identity to a connection as it is accepted, with the start identity of that pid."""
    cred = peer_cred(sock)
    info = proc_info(cred[0]) if cred else None
    return Peer(cred[0] if cred else None, cred[1] if cred else None, time.time(), info.ident if info else None)


_LIBPROC = None


def _proc_darwin(pid: int) -> Proc | None:
    """libproc's PROC_PIDTBSDINFO: struct proc_bsdinfo is 136 bytes; pbi_pid is the fourth uint32, pbi_ppid the
    fifth, and pbi_start_tvsec / pbi_start_tvusec are the two uint64 at offset 120."""
    import ctypes
    import ctypes.util

    global _LIBPROC
    if _LIBPROC is None:
        _LIBPROC = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
    buf = ctypes.create_string_buffer(136)
    if _LIBPROC.proc_pidinfo(ctypes.c_int(pid), ctypes.c_int(3), ctypes.c_uint64(0), buf, ctypes.c_int(136)) != 136:
        return None
    got, ppid = struct.unpack_from("2I", buf.raw, 12)
    sec, usec = struct.unpack_from("2Q", buf.raw, 120)
    return Proc(ppid, sec + usec / 1e6, f"{sec}.{usec:06d}") if got == pid else None


def _proc_linux(pid: int) -> Proc | None:
    """/proc/<pid>/stat: ppid is field 4 and starttime (clock ticks after boot) field 22; boot id and btime anchor it."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        ppid, ticks = int(fields[1]), int(fields[19])
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        btime = next(int(ln.split()[1]) for ln in Path("/proc/stat").read_text().splitlines() if ln.startswith("btime "))
        return Proc(ppid, btime + ticks / os.sysconf("SC_CLK_TCK"), f"{boot}:{ticks}")
    except (OSError, IndexError, ValueError, StopIteration):
        return None


def proc_info(pid: int) -> Proc | None:
    """Parent and start identity of a live process, or None (gone, another platform, or the OS will not say)."""
    if pid <= 0:
        return None
    try:
        if sys.platform == "darwin":
            return _proc_darwin(pid)
        if sys.platform.startswith("linux"):
            return _proc_linux(pid)
    except (OSError, AttributeError, ValueError):
        return None
    return None


def _ppid(pid: int) -> int | None:
    if (info := proc_info(pid)) is not None:
        return info.ppid
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
        return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def lineage(pid: int) -> list[int] | None:
    """Ancestors of ``pid`` up to pid 1, or None when any link cannot be read (fail closed)."""
    chain: list[int] = []
    cur = pid
    while cur > 1:
        nxt = _ppid(cur)
        if nxt is None or nxt in chain or len(chain) > 4096:
            return None
        chain.append(nxt)
        cur = nxt
    return chain


def descends_from(pid: int, ancestor: int) -> bool:
    """True when ``pid`` is a child, grandchild, ... of ``ancestor`` (not ``ancestor`` itself), or when its
    ancestry cannot be read: a process whose chain is unknown is never treated as outside the tree."""
    if pid == ancestor:
        return False
    chain = lineage(pid)
    return chain is None or ancestor in chain


_SAID: set[str] = set()


def _demote(peer: Peer, why: str) -> str:
    """Local, with the reason kept for this connection's refusals and said once per pid in the core's log."""
    DEMOTED.set(why)
    if (key := f"{peer.pid}:{why}") not in _SAID and len(_SAID) < 256:
        _SAID.add(key)
        print(f"exod: the principal credential from pid {peer.pid} is taken as local: {why}", file=sys.stderr)
    return LOCAL


def stamp(sent: str | None, token: str | None, peer: Peer | object, label: str = "") -> str:
    """The caller of one HTTP request on the Unix socket (one request per connection, so per connection)."""
    if not matches(sent, token):
        return LOCAL
    if not isinstance(peer, Peer):
        peer = accepted(peer)
    if peer.pid is None:  # no kernel answer: no principal standing
        return _demote(peer, "the kernel did not name the peer process")
    if peer.pid == 0:
        return _demote(peer, "the peer is in another PID namespace, so the kernel names no pid")
    if peer.uid != os.geteuid():
        return _demote(peer, f"the peer is uid {peer.uid}, not this user")
    info = proc_info(peer.pid)
    if info is None or peer.ident is None or not hmac.compare_digest(info.ident, peer.ident) \
            or info.start > peer.at + CLOCK_SLACK:  # gone, or its pid now names another process
        return _demote(peer, "the process that connected is gone or its pid was reused")
    if descends_from(peer.pid, os.getpid()):  # a tool subprocess of this core holds no consent
        return _demote(peer, "it runs under this core (a tool process)" if lineage(peer.pid) is not None else
                       "an ancestor of it cannot be read (/proc hidden by hidepid or ProtectProc?), so it is "
                       "treated as a tool process")
    return principal_as(label)


# ---------------------------------------------------------------- the launch record

RECORD_MAX = 4096


def token_path(sock: str | os.PathLike) -> Path:
    p = Path(sock)
    return p.with_name(p.stem + ".principal.key")


def issue(sock: str | os.PathLike, instance: str = "") -> str:
    """A fresh credential for the core serving ``sock``; any old record is replaced. The record binds the token
    to this process (pid and start identity) and launch instance in one atomic write."""
    path = token_path(sock)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    me = proc_info(os.getpid())
    if me is None:
        print(f"exod: cannot read this process's own start identity (/proc hidden?); principal clients of {sock} "
              "will be refused", file=sys.stderr)
    record = {"v": 1, "token": token, "pid": os.getpid(), "uid": os.geteuid(),
              "start": me.ident if me else "", "instance": instance}
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(record) + "\n")
    os.replace(tmp, path)
    return token


def _load(path: Path) -> object:
    """The record file's JSON, read through one descriptor: no symlink, a regular single-link file owned by this
    user with no group or other access, bounded in size. Raises PeerRefused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise PeerRefused(f"no readable launch record at {path} ({exc.strerror or exc})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise PeerRefused("launch record is not a plain file")
        if st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise PeerRefused("launch record is not private to this user")
        raw = os.read(fd, RECORD_MAX + 1)
    finally:
        os.close(fd)
    if len(raw) > RECORD_MAX:
        raise PeerRefused("launch record too large")
    try:
        return json.loads(raw)
    except ValueError:
        raise PeerRefused("launch record is not a v1 record (a core from before peer authentication?)") from None


def read_record(sock: str | os.PathLike) -> dict:
    """The launch record (``_load``), well formed. Raises PeerRefused."""
    rec = _load(token_path(sock))
    if isinstance(rec, dict) and rec.get("v") == 1 and rec.get("start") == "":
        raise PeerRefused("the core could not read its own process start identity (/proc hidden by hidepid, "
                          "ProtectProc or ProcSubset?), so principal mode is unavailable with it")
    if not (isinstance(rec, dict) and rec.get("v") == 1 and isinstance(rec.get("token"), str) and rec["token"]
            and isinstance(rec.get("pid"), int) and isinstance(rec.get("start"), str) and rec["start"]
            and isinstance(rec.get("instance", ""), str)):
        raise PeerRefused("launch record is not a v1 record (a core from before peer authentication?)")
    return rec


def read(sock: str | os.PathLike) -> str | None:
    """The token of a well-formed launch record, or None. Disclosing it needs ``verify_daemon`` first."""
    try:
        return read_record(sock)["token"]
    except PeerRefused:
        return None


def revoke(sock: str | os.PathLike, token: str) -> None:
    """Remove the record if it is still ours (a newer core may have replaced it). Only the token is compared, so a
    record this core wrote without its start identity goes too."""
    try:
        rec = _load(token_path(sock))
    except PeerRefused:
        return
    if isinstance(rec, dict) and isinstance(rec.get("token"), str) and matches(rec["token"], token):
        token_path(sock).unlink(missing_ok=True)


def matches(sent: str | None, token: str | None) -> bool:
    return bool(sent and token) and hmac.compare_digest(sent.encode(), token.encode())


def verify_daemon(conn: object, sock: str | os.PathLike) -> dict:
    """Before any credential byte goes on ``conn`` (already connected to ``sock``): the kernel's peer must be this
    user and the process the launch record names, started at the recorded moment. Returns the record whose token
    may go on this connection, and only on it. Raises PeerRefused."""
    cred = peer_cred(conn)
    if cred is None:
        raise PeerRefused(f"the kernel will not name the peer of a Unix socket on {sys.platform}; principal mode "
                          "needs SO_PEERCRED (Linux) or LOCAL_PEERCRED and LOCAL_PEERPID (macOS)")
    pid, uid = cred
    if pid == 0:
        raise PeerRefused(f"{sock} is served from another PID namespace, so the kernel names no pid for it; run "
                          "the client in the core's PID namespace")
    if uid != os.geteuid():
        raise PeerRefused(f"{sock} is served by uid {uid}, not this user")
    rec = read_record(sock)
    if rec["pid"] != pid:
        raise PeerRefused(f"{sock} is served by pid {pid}, not the core in the launch record (pid {rec['pid']}): "
                          "a stale record, another process on the socket, or a core in another PID namespace")
    info = proc_info(pid)
    if info is None or not hmac.compare_digest(info.ident, rec["start"]):
        raise PeerRefused("the launch record is stale (its process start identity does not match)")
    return rec
