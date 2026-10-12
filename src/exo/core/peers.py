"""Peers: live agent sessions messaging each other (D-026). Inspired by Claude Code's cross-session messages.

Three parts, none of which knows whether it runs in exod or inside a Hermes plugin:

- ``Directory``: one small presence file per daemon in a shared runtime dir. A daemon writes its file
  when it starts, removes it on exit, and anyone who scans prunes files whose pid is gone.
- envelopes: ``make_envelope`` and ``verify_envelope`` use the bus wire format (``exo.bus.wire``), signed
  with the sender seat's key, so a message is the same object on a socket or on the bus.
- ``PeerNode``: list, send, deliver and receipt for one daemon. Inbound messages become an ``Ingress``
  and go through the ingress path (policy, hold, deliver, receipt).

A local message travels over the target daemon's own socket (the ``peers`` protocol command). A bus seat
is reached through the bus client.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from exo import crypto, gate
from exo.bus import wire
from exo.core import trace
from exo.core.ingress import Ingress, IngressPath, Receipt, Target
from exo.core.sessions import new_id
from exo.protocol import schema as s

REPLAY_MINUTES = 5
ASK_TIMEOUT = 2.0  # seconds to wait on another daemon when listing
GENESIS = crypto.GENESIS


# ---------------------------------------------------------------- presence


def peers_dir(env: dict[str, str] | None = None) -> Path:
    """``$EXO_PEERS_DIR``, else ``$XDG_RUNTIME_DIR/exo/peers``, else ``~/.exo-peers``."""
    env = os.environ if env is None else env
    if env.get("EXO_PEERS_DIR"):
        return Path(env["EXO_PEERS_DIR"]).expanduser()
    if env.get("XDG_RUNTIME_DIR"):
        return Path(env["XDG_RUNTIME_DIR"]) / "exo" / "peers"
    return Path(env.get("HOME") or Path.home()) / ".exo-peers"


@dataclass
class Presence:
    seat: str
    daemon: str  # unique per running daemon: <seat>-<pid>
    pid: int
    platform: str  # exo or hermes
    socket: str
    pub: str = ""  # the seat's verify key, base64
    principal: str = ""
    cwd: str = ""
    started: str = ""
    v: int = 1


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Directory:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else peers_dir()

    def _file(self, daemon: str) -> Path:
        return self.path / f"{daemon}.json"

    def write(self, p: Presence) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass
        tmp = self.path / f".{p.daemon}.{secrets.token_hex(3)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(p), fh)
        tmp.replace(self._file(p.daemon))

    def remove(self, daemon: str) -> None:
        self._file(daemon).unlink(missing_ok=True)

    def get(self, daemon: str) -> Presence | None:
        try:
            return self._read(self._file(daemon))
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _read(path: Path) -> Presence:
        d = json.loads(path.read_text())
        return Presence(**{k: v for k, v in d.items() if k in Presence.__dataclass_fields__})

    def scan(self, prune: bool = True) -> list[Presence]:
        """Every live daemon. A file whose pid is gone, or that cannot be read, is removed (``prune``)."""
        out: list[Presence] = []
        try:
            files = sorted(self.path.glob("*.json"))
        except OSError:
            return out
        for f in files:
            try:
                p = self._read(f)
            except (OSError, ValueError, TypeError):
                if prune:
                    f.unlink(missing_ok=True)
                continue
            if not pid_alive(p.pid):
                if prune:
                    f.unlink(missing_ok=True)
                continue
            out.append(p)
        return out


# ---------------------------------------------------------------- envelopes


def short(session: str) -> str:
    return (session or "").removeprefix("s_")[:8]


def msg_id(env: dict[str, Any]) -> str:
    return "pm_" + str(env.get("id", ""))[:12]


def make_envelope(signer: crypto.Signer, *, to: str, text: str, session: str | None, daemon: str,
                  to_session: str | None = None, via: str = "agent") -> dict[str, Any]:
    """A bus-format envelope: from = seat, to = [name], body = text. What the bus format has no field for
    (sender session, target session, daemon, who wrote it, a nonce) rides in ``attach`` and is signed."""
    meta = {"peer": {"session": session, "to_session": to_session, "daemon": daemon, "via": via,
                     "nonce": secrets.token_hex(8)}}
    env = wire.make_envelope(signer.author, [to], text, GENESIS, "msg", None, None, [meta])
    return wire.finish_envelope(env, signer.key)


def verify_envelope(env: dict[str, Any], pub: str, seen: "OrderedDict[str, None] | None" = None) -> str:
    """'' when the envelope is good, else why not. Checks id, signature, age and (with ``seen``) replay."""
    try:
        core = wire.core(env)
        if not pub:
            return "no public key for the sender"
        if wire.envelope_id(core) != env.get("id"):
            return "the id does not match the content"
        if not wire.verify_doc(core, pub):
            return "the signature does not verify"
        if not wire.ts_within(str(env.get("ts")), REPLAY_MINUTES):
            return "the timestamp is outside the replay window"
    except Exception as exc:
        return f"malformed envelope ({type(exc).__name__})"
    if seen is not None:
        if env["id"] in seen:
            return "replayed message"
        seen[env["id"]] = None
        while len(seen) > 2000:
            seen.popitem(last=False)
    return ""


def peer_meta(env: dict[str, Any]) -> dict[str, Any]:
    for a in env.get("attach") or []:
        if isinstance(a, dict) and isinstance(a.get("peer"), dict):
            return a["peer"]
    return {}


# ---------------------------------------------------------------- bus seats as remote peers


class BusLink:
    """Reaches other seats through exo-bus when EXO_BUS_URL and EXO_BUS_SEAT are set."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self._seats: tuple[float, list[dict[str, Any]]] = (0.0, [])

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "BusLink | None":
        env = os.environ if env is None else env
        if not (env.get("EXO_BUS_URL") and env.get("EXO_BUS_SEAT")):
            return None
        try:
            from exo.bus import Client
            return cls(Client(env["EXO_BUS_URL"], env["EXO_BUS_SEAT"], timeout=3))
        except Exception as exc:
            trace.report("peers bus link", exc)
            return None

    @property
    def seat(self) -> str:
        return str(self.client.seat or "")

    async def seats(self) -> list[dict[str, Any]]:
        at, rows = self._seats
        if time.monotonic() - at < 60:
            return rows
        try:
            rows = list((await asyncio.to_thread(self.client.registry)).values())
        except Exception:
            rows = []  # the bus being down must not break listing local peers
        self._seats = (time.monotonic(), rows)
        return rows

    async def post(self, to: str, text: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.client.post, [to], text, "msg")


# ---------------------------------------------------------------- the node


async def call_socket(path: str, body: Any, timeout: float = ASK_TIMEOUT) -> dict[str, Any]:
    """One protocol command to another daemon's socket. Raises on failure."""
    from exo.protocol.client import Client
    client = Client(socket=path, timeout=timeout)
    try:
        return await asyncio.wait_for(client.call(body), timeout + 1)
    finally:
        await client.aclose()


Rpc = Callable[[str, Any], Awaitable[dict[str, Any]]]


@dataclass
class PeerConfig:
    policy: dict[str, str] = field(default_factory=dict)  # trust level -> auto|hold|deny
    rules: list[dict[str, str]] = field(default_factory=list)
    hold_minutes: float = 30.0
    wake: bool | None = None  # None: off for an interactive seat, on for a headless one
    active_hours: float = 12.0  # a session idle longer than this is not "live"
    enabled: bool = True

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> "PeerConfig":
        raw = raw or {}
        return cls(policy=dict(raw.get("policy") or {}), rules=list(raw.get("rules") or []),
                   hold_minutes=float(raw.get("hold_minutes") or 30), wake=raw.get("wake"),
                   active_hours=float(raw.get("active_hours") or 12), enabled=raw.get("enabled", True) is not False)


class PeerNode:
    """list / send / deliver / receipt for one daemon. ``sessions()`` returns this daemon's live sessions as
    ``[{id, title, state, started, cwd}]``; ``target`` is the receiving side (see ingress.Target)."""

    def __init__(self, *, seat: str, signer: crypto.Signer, target: Target, sessions: Callable[[], list[dict[str, Any]]],
                 directory: Directory | None = None, socket: str = "", platform: str = "exo", principal: str = "",
                 cwd: str = "", config: PeerConfig | None = None, bus: BusLink | None = None,
                 rpc: Rpc | None = None, emit_out: Callable[..., Any] | None = None) -> None:
        self.seat, self.signer, self.target, self.sessions = seat, signer, target, sessions
        self.directory = directory or Directory()
        self.socket, self.platform, self.principal, self.cwd = socket, platform, principal or "", cwd
        self.config = config or PeerConfig()
        self.bus = bus
        self.rpc: Rpc = rpc or call_socket
        self.emit_out = emit_out  # (session, PeerDelivery body): the sender's own record of a receipt
        self.daemon = f"{seat}-{os.getpid()}"
        self.started = crypto.now_ts()
        self.path = IngressPath(target, self._policy, hold_seconds=self.config.hold_minutes * 60)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._sent: OrderedDict[str, tuple[str | None, str]] = OrderedDict()  # msg id -> (sender session, peer name)

    # ---------------------------------------------------------------- presence

    def presence(self) -> Presence:
        return Presence(seat=self.seat, daemon=self.daemon, pid=os.getpid(), platform=self.platform,
                        socket=self.socket, pub=self.signer.pub, principal=self.principal, cwd=self.cwd,
                        started=self.started)

    def start(self) -> None:
        if self.config.enabled and self.socket:
            self.directory.write(self.presence())

    def stop(self) -> None:
        self.directory.remove(self.daemon)

    # ---------------------------------------------------------------- policy

    def _policy(self, ing: Ingress) -> tuple[str, str]:
        return gate.decide_peer(ing.trust, ing.sender, {"policy": self.config.policy, "rules": self.config.rules})

    def _trust(self, meta: dict[str, Any], sender: Presence | None, ok: bool) -> str:
        if not ok or sender is None:
            return "unverified"
        same = (sender.principal or "") == (self.principal or "")
        if not same:
            return "other"
        return "principal" if meta.get("via") == "human" else "same-principal"

    # ---------------------------------------------------------------- list

    async def entries(self, exclude_session: str | None = None, local_only: bool = False) -> list[dict[str, Any]]:
        """Every peer: this daemon's other sessions, other daemons' sessions, bus seats."""
        rows = [self._entry(self.presence(), x, "here") for x in self.sessions() if x["id"] != exclude_session]
        if not local_only:
            others = [p for p in self.directory.scan() if p.daemon != self.daemon]
            got = await asyncio.gather(*(self._ask_list(p) for p in others))
            for p, sess in zip(others, got):
                rows += [self._entry(p, x, "machine") for x in sess]
            if self.bus is not None:
                known = {r["seat"] for r in rows} | {self.seat, self.bus.seat}
                for st in await self.bus.seats():
                    if st["seat"] not in known:
                        rows.append({"seat": st["seat"], "daemon": "", "session": None, "title": st.get("role", ""),
                                     "state": "remote", "started": "", "cwd": "", "platform": st.get("platform", ""),
                                     "where": "bus", "socket": ""})
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["seat"]] = counts.get(r["seat"], 0) + 1
        for r in rows:  # a seat with one live session is just its name; more than one gets the short id
            r["name"] = r["seat"] if counts[r["seat"]] == 1 or not r["session"] else f"{r['seat']}/{short(r['session'])}"
        return rows

    @staticmethod
    def _entry(p: Presence, x: dict[str, Any], where: str) -> dict[str, Any]:
        return {"seat": p.seat, "daemon": p.daemon, "session": x["id"], "title": x.get("title", ""),
                "state": x.get("state", "idle"), "started": x.get("started", ""), "cwd": x.get("cwd") or p.cwd,
                "platform": p.platform, "where": where, "socket": p.socket}

    async def _ask_list(self, p: Presence) -> list[dict[str, Any]]:
        try:
            res = await self.rpc(p.socket, s.Peers(action="list", local_only=True))
            return list(res.get("sessions") or [])
        except Exception:
            return []  # unreachable: it is going away, or it is not an exod

    def resolve(self, to: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """The entry a name points at: an exact name, ``seat/<session prefix>``, or a bare seat name."""
        to = (to or "").strip()
        for r in rows:
            if r["name"] == to:
                return r
        seat, _, sid = to.partition("/")
        hits = [r for r in rows if r["seat"] == seat and (not sid or (r["session"] or "").removeprefix("s_").startswith(sid.removeprefix("s_")))]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise ValueError(f"no peer named {to!r}. Peers now: {', '.join(r['name'] for r in rows) or 'none'}")
        busy = [h for h in hits if h["state"] == "running"]
        if len({h["daemon"] for h in hits}) == 1:  # one daemon: it picks its own most recent session
            return hits[0] if not busy else busy[0]
        raise ValueError(f"{to!r} matches {len(hits)} sessions ({', '.join(h['name'] for h in hits)}); name one")

    async def list(self, session: str | None = None, local_only: bool = False) -> dict[str, Any]:
        if local_only:
            return {"seat": self.seat, "daemon": self.daemon, "platform": self.platform, "principal": self.principal,
                    "sessions": self.sessions()}
        rows = await self.entries(session)
        held = [{"msg_id": h.ing.id, "from": h.ing.sender, "expires_in_s": max(0, int(h.deadline - time.time()))}
                for h in self.path.held.values()]
        return {"peers": [{k: v for k, v in r.items() if k not in ("socket", "daemon")} for r in rows],
                "me": self.seat, "held": held}

    # ---------------------------------------------------------------- send

    async def send(self, to: str, text: str, session: str | None = None, via: str = "agent") -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("nothing to send")
        rows = await self.entries(session)
        row = self.resolve(to, rows)
        if row["where"] == "bus":
            return await self._send_bus(row, text, session)
        env = make_envelope(self.signer, to=row["name"], text=text, session=session, daemon=self.daemon,
                            to_session=row["session"], via=via)
        mid = msg_id(env)
        self._sent[mid] = (session, row["name"])
        while len(self._sent) > 500:
            self._sent.popitem(last=False)
        try:
            if row["daemon"] == self.daemon:
                receipt = await self.deliver(env)
            else:
                receipt = await self.rpc(row["socket"], s.Peers(action="deliver", envelope=env))
        except Exception as exc:
            receipt = {"msg_id": mid, "state": "denied", "detail": f"{row['name']} could not be reached ({type(exc).__name__}: {exc})"}
        out = {**receipt, "to": row["name"]}
        await self._publish_out(session, out)
        return out

    async def _send_bus(self, row: dict[str, Any], text: str, session: str | None) -> dict[str, Any]:
        try:
            res = await self.bus.post(row["seat"], text)  # type: ignore[union-attr]
            out = {"msg_id": "bus_" + str(res.get("id", ""))[:12], "state": "delivered", "to": row["name"],
                   "detail": f"posted to the bus (seq {res.get('seq')}); {row['seat']} applies its own policy and "
                             "will not send a receipt"}
        except Exception as exc:
            out = {"msg_id": new_id("pm"), "state": "denied", "to": row["name"], "detail": f"the bus refused it ({exc})"}
        await self._publish_out(session, out)
        return out

    async def _publish_out(self, session: str | None, r: dict[str, Any]) -> None:
        if self.emit_out is not None and session:
            res = self.emit_out(session, s.PeerDelivery(msg_id=r["msg_id"], state=r["state"], peer=r.get("to", ""),
                                                        direction="out", detail=r.get("detail", "")))
            if asyncio.iscoroutine(res):
                await res

    # ---------------------------------------------------------------- inbound

    async def deliver(self, env: dict[str, Any]) -> dict[str, Any]:
        """A signed envelope from another daemon (or from this one). Returns the first receipt."""
        meta = peer_meta(env)
        sender = self.directory.get(str(meta.get("daemon") or ""))
        if sender is None and meta.get("daemon") == self.daemon:  # a message between two sessions of this daemon
            sender = self.presence()
        pub = sender.pub if sender and sender.seat == env.get("from") else ""
        why = verify_envelope(env, pub, self._seen)
        trust = self._trust(meta, sender, not why)
        sid = meta.get("session")
        name = f"{env.get('from')}/{short(sid)}" if sid else str(env.get("from"))
        ing = Ingress("peer", name, trust, str(env.get("body") or ""), session=meta.get("to_session"),
                      id=msg_id(env), meta={"kind": "msg", "from_session": sid, "from_seat": env.get("from"),
                                            "from_daemon": meta.get("daemon"), "via": meta.get("via"),
                                            "verify": why})
        receipt = await self.path.submit(ing)
        return receipt.to_dict()

    async def notify_sender(self, ing: Ingress, r: Receipt) -> None:
        """Tell the sender a held message's outcome (released, denied, expired). Signed with this seat's key."""
        doc = crypto.sign_doc({"v": 1, "msg_id": r.msg_id, "state": r.state, "detail": r.detail, "by": self.seat,
                               "daemon": self.daemon, "ts": crypto.now_ts()}, self.signer.key)
        sender = self.directory.get(str(ing.meta.get("from_daemon") or ""))
        if sender is None:
            return
        try:
            if sender.daemon == self.daemon:
                await self.receipt(doc)
            else:
                await self.rpc(sender.socket, s.Peers(action="receipt", envelope=doc))
        except Exception as exc:
            trace.report("peers receipt to sender", exc)

    async def receipt(self, doc: dict[str, Any]) -> dict[str, Any]:
        """A later receipt for a message this daemon sent. Verified against the receiver's presence key."""
        mid = str(doc.get("msg_id") or "")
        who = self.directory.get(str(doc.get("daemon") or ""))
        if mid not in self._sent or who is None or not crypto.verify_doc(doc, who.pub) or doc.get("state") not in (
                "released", "denied", "expired", "delivered"):
            return {"ok": False}
        session, peer = self._sent[mid]
        await self._publish_out(session, {"msg_id": mid, "state": doc["state"], "to": peer, "detail": doc.get("detail", "")})
        return {"ok": True}
