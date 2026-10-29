"""Peers: live agent sessions messaging each other (D-026). Inspired by Claude Code's cross-session messages.

The peer transport, outside the core (docs/ENGINE-SPLIT.md, step 4). The engine holds an optional
``PeerService`` that the composition layer attaches (``attach(engine)``); the engine itself only knows that
service's ``start``, ``stop``, ``ready`` and ``command``. Delivery goes through the core's ingress path and the
engine's ingress target (``exo.core.peer_target.EngineTarget``), so policy, holding and receipts stay there.

Three parts, none of which knows whether it runs in exod or inside a Hermes plugin:

- ``Directory``: one small presence file per daemon in a shared runtime dir. A daemon writes its file, signed
  with its seat key (#523), when it starts, removes it on exit, and anyone who scans prunes files whose pid is
  gone.
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
import stat
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from exo import crypto, gate
from exo.bus import wire
from exo.config import Config
from exo.core import trace
from exo.core.hooks import maybe_await
from exo.core.ingress import Ingress, IngressPath, PeerConfig, Receipt, Target
from exo.core.private import atomic_write
from exo.core.seats import ID_RE, ensure_key, key_path, load as _load_seat, seats_dir
from exo.core.sessions import new_id
from exo.core.tools.registry import ToolContext, ToolError
from exo.core.types import ToolSpec
from exo.keyring import KeyRegistry, keys_dir
from exo.protocol import schema as s

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine

REPLAY_MINUTES = 5
ASK_TIMEOUT = 2.0  # seconds to wait on another daemon when listing
GENESIS = crypto.GENESIS
NOT_A_SEAT = "the sender is not a seat name"


# ---------------------------------------------------------------- presence


def peers_dir(env: dict[str, str] | None = None) -> Path:
    """``$EXO_PEERS_DIR``, else ``$XDG_RUNTIME_DIR/exo/peers``, else ``~/.exo-peers``."""
    e = os.environ if env is None else env
    if e.get("EXO_PEERS_DIR"):
        return Path(e["EXO_PEERS_DIR"]).expanduser()
    if e.get("XDG_RUNTIME_DIR"):
        return Path(e["XDG_RUNTIME_DIR"]) / "exo" / "peers"
    return Path(e.get("HOME") or Path.home()) / ".exo-peers"


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
    sig: str = ""  # the seat key's signature over the entry without it (#523)

    def signed(self, signer: crypto.Signer) -> "Presence":
        """The entry with ``sig`` made (#523): the daemon's own seat key signs every field but the signature. A
        sender checks it against the keys it pins for the seat, so an entry the pinned key did not make — its
        public half copied into a forged file beside a socket of its own — does not pass."""
        return replace(self, sig=crypto.sign_doc(asdict(self), signer.key)["sig"])


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


def read_plain(path: Path) -> str:
    """A regular file's text (#132). The open never waits and the type is checked on the open file, so a FIFO or a
    folder, even one renamed into place a moment before, raises ValueError instead of stalling the event loop."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path.name} is not a plain file")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
        return b"".join(chunks).decode()
    finally:
        os.close(fd)


def has_seat_file(home: Path, seat: str) -> bool:
    d = seats_dir(home)
    return any(p.exists() for p in (d / f"{seat}.toml", d / f"{seat}.json", d / seat))


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
        atomic_write(self._file(p.daemon), json.dumps(asdict(p)))

    def remove(self, daemon: str) -> None:
        self._file(daemon).unlink(missing_ok=True)

    def get(self, daemon: str) -> Presence | None:
        got = self.entry(daemon)
        return got[0] if got else None

    def entry(self, daemon: str) -> tuple[Presence, dict[str, Any]] | None:
        """The entry and the JSON it was read from, which its signature covers: a newer exo may sign a field this
        one does not know (#523)."""
        if Path(daemon).name != daemon:  # an envelope names the daemon: a path there never leaves this folder (#132)
            return None
        try:
            d = json.loads(read_plain(self._file(daemon)))
            return self._presence(d), d
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _presence(d: dict[str, Any]) -> Presence:
        if not isinstance(d, dict) or type(d.get("pid")) is not int:
            raise TypeError("presence must be an object with an integer pid")
        return Presence(**{k: v for k, v in d.items() if k in Presence.__dataclass_fields__})

    @staticmethod
    def _read(path: Path) -> Presence:
        return Directory._presence(json.loads(read_plain(path)))

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


def doc_kind(doc: dict[str, Any]) -> str:
    """``reply`` for a deliver's first answer, ``receipt`` for a later outcome (#603 review). Each signs its kind; an
    older exo signs none, and its first reply (#523) is the one that signs ``mode`` and ``session``, which no
    receipt ever has."""
    return str(doc.get("kind", "reply" if "mode" in doc or "session" in doc else "receipt"))


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
        e = os.environ if env is None else env
        if not (e.get("EXO_BUS_URL") and e.get("EXO_BUS_SEAT")):
            return None
        try:
            from exo.bus import Client
            return cls(Client(e["EXO_BUS_URL"], e["EXO_BUS_SEAT"], timeout=3))
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


class PeerNode:
    """list / send / deliver / receipt for one daemon. ``sessions()`` returns this daemon's live sessions as
    ``[{id, title, state, started, cwd}]``; ``target`` is the receiving side (see ingress.Target)."""

    def __init__(self, *, seat: str, signer: crypto.Signer, target: Target, sessions: Callable[[], list[dict[str, Any]]],
                 directory: Directory | None = None, socket: str = "", platform: str = "exo", principal: str = "",
                 cwd: str = "", home: str | os.PathLike | None = None, config: PeerConfig | None = None,
                 bus: BusLink | None = None,
                 rpc: Rpc | None = None, emit_out: Callable[..., Any] | None = None) -> None:
        self.seat, self.signer, self.target, self.sessions = seat, signer, target, sessions
        self.directory = directory or Directory()
        self.socket, self.platform, self.principal, self.cwd = socket, platform, principal or "", cwd
        self.home = Path(home).expanduser() if home else None  # the principal-owned home: where seat keys are pinned
        self._home_principal, self._seated = "", False  # config.json's principal; this seat's file names one (#132)
        if self.home is not None:
            try:
                self._home_principal = str(Config.load(self.home).data.get("principal") or "")
            except Exception:
                pass  # unreadable: it names no principal
            try:
                self._seated = has_seat_file(self.home, seat) and bool(_load_seat(self.home, seat).principal)
            except Exception:
                self._seated = True  # a seat file that cannot be read: no key without a seat file speaks for it
        self.config = config or PeerConfig()
        self.bus = bus
        self.rpc: Rpc = rpc or call_socket
        self.emit_out = emit_out  # (session, PeerDelivery body): the sender's own record of a receipt
        self.daemon = f"{seat}-{os.getpid()}"
        self.started = crypto.now_ts()
        self.path = IngressPath(target, self._policy, hold_seconds=self.config.hold_minutes * 60)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._sent: OrderedDict[str, tuple[str | None, str, str]] = OrderedDict()  # msg id -> (session, peer, its seat)

    # ---------------------------------------------------------------- presence

    def presence(self) -> Presence:
        return Presence(seat=self.seat, daemon=self.daemon, pid=os.getpid(), platform=self.platform,
                        socket=self.socket, pub=self.signer.pub, principal=self.principal, cwd=self.cwd,
                        started=self.started)

    def start(self) -> None:
        if self.config.enabled and self.socket:
            self.directory.write(self.presence().signed(self.signer))

    def stop(self) -> None:
        self.directory.remove(self.daemon)

    # ---------------------------------------------------------------- policy

    def _policy(self, ing: Ingress) -> tuple[str, str]:
        if ing.meta.get("verify") == NOT_A_SEAT:  # no signature was checked: the sender is told why (#132)
            return "deny", NOT_A_SEAT
        got = gate.decide_peer(ing.trust, ing.sender, {"policy": self.config.policy, "rules": self.config.rules})
        if ing.meta.get("pinned"):
            return got
        # A rule names a sender, and a presence file can claim any name (#132). For a sender whose key this home
        # does not hold, a rule may hold or deny, but never deliver sooner than the policy for its trust.
        base = gate.decide_peer(ing.trust, ing.sender, {"policy": self.config.policy})
        return max(got, base, key=lambda o: gate.PEER_OUTCOMES.index(o[0]))

    def _pins(self, seat: str) -> tuple[list[str], list[str]]:
        """The keys this home holds for ``seat`` (#132), never a presence file's: ``(here, peer)``. ``here`` is this
        daemon's own key when ``seat`` is its seat, plus this home's key for a seat of this home: its key registry
        entry, else the ``.pub`` beside the seat's key file in ``keys/`` (``core/seats.py``). A registry entry is the
        authority for its name, so a key rotated out with ``exo keys pin --replace`` is not read back from a leftover
        ``.pub``. A seat of this home has a seat file here or its key file in ``keys/``. For any other name, such a
        key (a plain ``exo keys pin`` or a copied ``.pub`` of another home's seat) is in ``peer``, with what
        ``exo keys pin --peer`` pinned. Call it with a seat id only."""
        own = [self.signer.pub] if seat == self.seat else []
        if self.home is None:
            return own, []
        try:
            reg = KeyRegistry.load(self.home, create=False)
        except Exception:  # unreadable: it cannot say which keys are pinned, or which a .pub still holds
            return own, []
        paths = [key_path(self.home, seat)]
        try:
            paths.insert(0, _load_seat(self.home, seat).key_path(self.home))
        except Exception:
            pass
        here, kdir = list(own), keys_dir(self.home).resolve()
        for p in paths:
            if p.parent.resolve() != kdir:  # a seat file may name a key elsewhere: only keys/ pins (#132)
                continue
            try:
                found = reg.keys.get(p.stem) or [read_plain(p.with_suffix(".pub")).strip()]
            except (OSError, ValueError):
                found = []
            here += [k for k in found if k and k not in here]
        peer = list(reg.peers.get(seat, []))
        if own or has_seat_file(self.home, seat) or paths[-1].is_file():
            return here, peer
        return own, here + peer  # no record here of whose seat it is: its key checks messages, never trust

    def _verify(self, env: dict[str, Any], seat: str, sender: Presence | None) -> tuple[str, str]:
        """``(why, pin)``: why the envelope fails ('' when good), and the pin it verified against: ``here``, ``peer``
        or '' when this home holds no key for the seat. Then the presence key only shows the signature is
        consistent. A name that holds a key here is checked against those keys alone (#132)."""
        if not ID_RE.fullmatch(seat):  # a path, an empty or a NUL name: never looked up as a key file (#132)
            return NOT_A_SEAT, ""
        if sender is None or sender.seat != seat:
            return verify_envelope(env, "", self._seen), ""
        here, peer = self._pins(seat)
        if not here and not peer:
            return verify_envelope(env, sender.pub, self._seen), ""
        why = ""
        for pub in here + peer:
            why = verify_envelope(env, pub, self._seen)
            if not why:
                return "", "here" if pub in here else "peer"
        return why, ""

    def _principal(self, seat: str) -> str | None:
        """The seat's principal as this home records it, never as a presence file or an environment claims (#132):
        this seat's own, else the one its seat file names. None makes the message ``other``. A key of this home with
        no seat file, or a seat file that names no principal (an unseated daemon, a Hermes profile on this home),
        counts as the principal config.json names. It is this seat's only when this seat runs as that principal or
        as none, and has no seat file that names one: config.json is not a store file, so it never vouches for a
        principal a seat file records. None for a seat file that cannot be read."""
        if seat == self.seat or self.home is None:
            return self.principal if seat == self.seat else None
        try:
            named = _load_seat(self.home, seat).principal if has_seat_file(self.home, seat) else None
        except Exception:
            return None
        if named:
            return named
        return self.principal if not self._seated and self.principal in ("", self._home_principal) else None

    def _trust(self, meta: dict[str, Any], seat: str, ok: bool, pin: str, claim: Any = "") -> str:
        """``claim`` is the principal the sender's presence file names. It never raises trust, but a sender that
        says it runs as another principal than this home records is ``other``, as before #132. An empty claim, like
        an empty principal here, stands for the principal config.json names."""
        if not ok:
            return "unverified"
        if pin != "here":
            # Only a key of this home says whose seat this is. An unpinned key came from the presence file, which
            # any same-user process can write, and a peer pin names another home's seat (#132).
            return "other"
        principal = self._principal(seat)
        if principal is None or principal != (self.principal or ""):
            return "other"
        if (claim or self._home_principal) != (principal or self._home_principal):
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
        socket = str(row["socket"])
        if row["where"] == "machine":
            # the text never ships to a socket a forged presence file names, and it ships to the socket of the
            # entry that verified, not the one the scan saw (#523)
            socket = self._check_presence_key(row)
        env = make_envelope(self.signer, to=row["name"], text=text, session=session, daemon=self.daemon,
                            to_session=row["session"], via=via)
        mid = msg_id(env)
        self._sent[mid] = (session, row["name"], row["seat"])
        while len(self._sent) > 500:
            self._sent.popitem(last=False)
        try:
            if row["daemon"] == self.daemon:
                receipt = await self.deliver(env)
            else:
                receipt = await self.rpc(socket, s.Peers(action="deliver", envelope=env))
        except Exception as exc:
            receipt = {"msg_id": mid, "state": "denied", "detail": f"{row['name']} could not be reached ({type(exc).__name__}: {exc})"}
        else:
            self._check_reply(receipt, mid, row)  # raises: a reply that fails is not published (#523)
        out = {**receipt, "to": row["name"]}
        await self._publish_out(session, out)
        return out

    def _check_presence_key(self, row: dict[str, Any]) -> str:
        """The sender's half of #132's key rule (#523): the text ships only when the presence file the row came
        from holds a key this home pins for that seat and carries that key's signature over the entry, and the
        socket returned is the one the verified entry names. Any same-user process can write a presence file for
        a pinned name: with a socket of its own, or with the pinned key's public half copied into it beside that
        socket. Neither carries a signature the pinned key made, and neither is sent to. The file is re-read here,
        so the entry checked and the socket shipped to are one, not the entry the scan happened to see. For a name
        this home holds no key for, the receipt-side trust ladder decides what the receiver makes of the message,
        as it did before, and the scanned socket stands."""
        here, peer = self._pins(str(row["seat"]))
        if not here and not peer:
            return str(row["socket"])
        p, raw = self.directory.entry(str(row["daemon"])) or (None, {})
        if p is None or p.daemon != row["daemon"] or p.seat != row["seat"] or p.pub not in here + peer \
                or not any(crypto.verify_doc(raw, k) for k in here + peer):
            raise ValueError(f"{row['name']}'s presence file does not verify against a key pinned for "
                             f"{row['seat']} here; not sent")
        return p.socket

    def _check_reply(self, receipt: dict[str, Any], mid: str, row: dict[str, Any]) -> None:
        """The first reply to a deliver is signed like a later receipt (#523, with #132's primitives): the
        receiving seat signs it (``deliver``), and here it must name the message, state, seat and daemon the
        text went to and verify against a key this home pins for that seat, as ``receipt`` has since #132. A
        reply that fails raises: nothing is published and the send fails. For a name no key here for, the reply
        is not checked and the receiving side's own ladder decides what arrived, as before. The reply is signed as
        ``kind: "reply"`` and a later receipt as ``kind: "receipt"``, so neither passes for the other (``doc_kind``:
        a #523 build's reply, which names no kind, is still a reply)."""
        if not isinstance(receipt, dict):
            raise ValueError(f"{row['name']} answered with something that is not a reply; not published")
        here, peer = self._pins(str(row["seat"]))
        if not here and not peer:
            return
        by = str(receipt.get("by") or "")
        if doc_kind(receipt) != "reply":
            why = "the doc is not a first reply"
        elif receipt.get("msg_id") != mid:
            why = "the reply does not name the message"
        elif not ID_RE.fullmatch(by) or by != row["seat"] or receipt.get("daemon") != row["daemon"] \
                or receipt.get("state") not in ("delivered", "held", "denied"):
            why = "the reply does not name the seat the message went to"
        elif not any(crypto.verify_doc(receipt, p) for p in here + peer):
            why = "the reply does not verify against a key pinned for the seat here"
        else:
            return
        raise ValueError(f"{row['name']} answered with a first reply that does not verify ({why}); not published")

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
            await maybe_await(self.emit_out(session, s.PeerDelivery(msg_id=r["msg_id"], state=r["state"],
                                                                    peer=r.get("to", ""), direction="out",
                                                                    detail=r.get("detail", ""))))

    # ---------------------------------------------------------------- inbound

    async def deliver(self, env: dict[str, Any]) -> dict[str, Any]:
        """A signed envelope from another daemon (or from this one). Returns the first reply, signed with this
        seat's key so the sending side can verify it (#523), the way a later receipt is signed (#132)."""
        meta = peer_meta(env)
        sender = self.directory.get(str(meta.get("daemon") or ""))
        if sender is None and meta.get("daemon") == self.daemon:  # a message between two sessions of this daemon
            sender = self.presence()
        seat = str(env.get("from") or "")
        why, pin = self._verify(env, seat, sender)
        trust = self._trust(meta, seat, not why, pin, sender.principal if sender else "")
        sid = meta.get("session")
        name = f"{env.get('from')}/{short(sid)}" if sid else str(env.get("from"))
        ing = Ingress("peer", name, trust, str(env.get("body") or ""), session=meta.get("to_session"),
                      id=msg_id(env), meta={"kind": "msg", "from_session": sid, "from_seat": env.get("from"),
                                            "from_daemon": meta.get("daemon"), "via": meta.get("via"),
                                            "verify": why, "pinned": bool(pin),
                                            "note": "" if pin or why else "key not pinned"})
        receipt = await self.path.submit(ing)
        # The first reply is signed like the later receipts are (#523, #132): the receiving seat answers only
        # through a doc its own key verifies a send against, so a forged presence cannot publish an outcome. It is
        # signed for any envelope, one that does not verify too, so it says it is a reply: a socket client that
        # names a held message's id in a junk envelope gets a signed `denied` no sender takes as a receipt.
        return crypto.sign_doc({"v": 1, "kind": "reply", "msg_id": receipt.msg_id, "state": receipt.state,
                                "detail": receipt.detail, "mode": receipt.mode, "session": receipt.session,
                                "by": self.seat, "daemon": self.daemon, "ts": crypto.now_ts()}, self.signer.key)

    async def notify_sender(self, ing: Ingress, r: Receipt) -> None:
        """Tell the sender a held message's outcome (released, denied, expired). Signed with this seat's key."""
        doc = crypto.sign_doc({"v": 1, "kind": "receipt", "msg_id": r.msg_id, "state": r.state, "detail": r.detail,
                               "by": self.seat, "daemon": self.daemon, "ts": crypto.now_ts()}, self.signer.key)
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
        """A later receipt for a message this daemon sent (#132). ``by`` must be the seat the message went to and the
        seat of the daemon sending the receipt, and the signature must verify against a key this home pins for that
        seat. A presence key never verifies a receipt, and a first reply is no receipt: an older exo's receipt says
        no ``kind``, and its first reply (#523) is told apart by the ``mode`` and ``session`` it signs."""
        mid, by = str(doc.get("msg_id") or ""), str(doc.get("by") or "")
        sent = self._sent.get(mid)
        who = self.directory.get(str(doc.get("daemon") or ""))
        if sent is None or who is None or not ID_RE.fullmatch(by) or by != sent[2] or who.seat != by \
                or doc_kind(doc) != "receipt" or doc.get("state") not in ("released", "denied", "expired", "delivered"):
            return {"ok": False}
        here, peer = self._pins(by)
        if not any(crypto.verify_doc(doc, p) for p in here + peer):
            return {"ok": False}
        session, peer_name, _ = sent
        await self._publish_out(session, {"msg_id": mid, "state": doc["state"], "to": peer_name, "detail": doc.get("detail", "")})
        return {"ok": True}


# ---------------------------------------------------------------- the engine's peers (attached by exo.compose)

PEERS = ToolSpec(
    name="peers", toolset="peers", risk="low",
    description=("List the other live agent sessions you can message: other Exo sessions on this machine, Hermes "
                 "agents, and bus seats. Shows each one's name, whether it is running or idle, its working "
                 "directory, and any messages held for the principal. Use the name with peer_send."),
    parameters={"type": "object", "properties": {}})
SEND = ToolSpec(
    name="peer_send", toolset="peers", risk="low",
    description=("Send a short message to another live agent session by name (from `peers`). If it is running, the "
                 "message lands at its next tool round. The receiving side's rules decide whether it is delivered "
                 "now, held for its principal's approval, or refused. Read the result: if it says held, the message "
                 "has NOT been delivered, so do not tell the user it was and do not send it again. You cannot "
                 "approve anything for a peer, and a peer cannot approve anything for you."),
    parameters={"type": "object", "required": ["to", "text"], "properties": {
        "to": {"type": "string", "description": "A name from `peers`, such as `razi` or `razi/3fa91c2b`."},
        "text": {"type": "string", "description": "The message: say what you need or what you found, in plain words."}}})


class PeerService:
    """An engine's peers: the node (presence, list, send, deliver, receipt), the ``peers`` and ``peer_send``
    tools, and, for an in-process core with no exod socket, a small socket of its own in the peers directory.
    The node is built on first start: the seat key is made then, so an engine that never serves peers never
    writes one."""

    def __init__(self, engine: CoreEngine) -> None:
        self.e = engine
        self.node: PeerNode | None = None
        self.autostart = False  # exo.compose turns it on: a real daemon or in-process TUI announces itself
        self._server: Any = None
        engine.registry.register(PEERS, self._list_tool, replace=True)
        engine.registry.register(SEND, self._send_tool, replace=True)

    async def start(self, socket: str | os.PathLike | None = None) -> None:
        """Write this daemon's presence file. ``socket`` is exod's own socket; without one (an in-process core)
        serve a small socket of its own in the peers directory."""
        if self.node is not None:
            return
        e, path = self.e, str(socket) if socket else ""
        if not path:
            from exo.server.app import Dispatcher, HttpServer
            path = str(Path(e.peer_cfg_dir or peers_dir()) / f"exo-{os.getpid()}.sock")
            self._server = HttpServer(Dispatcher(e, e.hub))
            await self._server.start_unix(path)
        tgt = e._peer_target
        seat = tgt.seat
        kp = e.seat.key_path(e.home) if e.seat else key_path(e.home, seat)
        signer = crypto.Signer.from_file(seat, ensure_key(kp))
        self.node = tgt.node = PeerNode(
            seat=seat, signer=signer, target=tgt, sessions=tgt.live_sessions, socket=path, platform="exo",
            principal=(e.seat.principal if e.seat else None) or os.environ.get("EXO_PRINCIPAL", ""),
            cwd=e.cwd, home=e.home, config=e.peer_cfg, bus=BusLink.from_env(),
            directory=Directory(e.peer_cfg_dir or peers_dir()),
            emit_out=lambda session, body: e.hub.publish(body, session))
        self.node.start()

    async def stop(self) -> None:
        if self.node is not None:
            self.node.stop()
            await self.node.path.close()
            self.node = None
        if self._server is not None:
            await self._server.close()
            self._server = None

    async def ready(self) -> bool:
        """True when the node runs (started now if ``autostart`` is on and it was not yet)."""
        if self.node is None and self.autostart:
            await self.start()
        return self.node is not None

    async def command(self, b: s.Peers, via: str) -> dict[str, Any]:
        """The ``peers`` protocol command (not ``bus``, which the engine routes to bus ingress). Call after
        ``ready()``. ``via`` is what the engine allows the caller to claim (D-039)."""
        node = self._need()
        if b.action == "list":
            return await node.list(b.session, b.local_only)
        if b.action == "send":
            return await node.send(b.to, b.text, b.session, via)
        if b.action == "deliver":
            return await node.deliver(b.envelope)
        return await node.receipt(b.envelope)

    def _need(self) -> PeerNode:
        if self.node is None:
            raise ToolError("peers are not running in this process (the engine has no peer socket yet)")
        return self.node

    async def _list_tool(self, args: dict[str, Any], ctx: ToolContext) -> str:
        res = await self._need().list(ctx.session)
        rows = res["peers"]
        lines = [f"You are {res['me']}. {len(rows)} peer(s):"] if rows else [f"You are {res['me']}. No other live sessions."]
        for r in rows:
            lines.append(f"- {r['name']}  [{r['platform']}, {r['state']}]  {r['cwd'] or ''}  {r['title'] or ''}".rstrip())
        for h in res["held"]:
            lines.append(f"held for your principal (not delivered to you): message {h['msg_id']} from {h['from']}")
        return "\n".join(lines)

    async def _send_tool(self, args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            r = await self._need().send(str(args.get("to") or ""), str(args.get("text") or ""), ctx.session, "agent")
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return describe(r)


def attach(engine: CoreEngine) -> PeerService | None:
    """The engine's peers when its config enables them (``CoreEngine(attach_peers=attach)``, as exo.compose does)."""
    return PeerService(engine) if engine.peer_cfg.enabled else None


def describe(r: dict[str, Any]) -> str:
    """What the sending model is told. Plain about held and refused: it must not claim delivery."""
    to, state, detail = r.get("to", "the peer"), r.get("state"), r.get("detail", "")
    if state == "delivered":
        return f"Delivered to {to}: {detail}. (message {r['msg_id']})"
    if state == "held":
        return (f"HELD, NOT delivered to {to}: {detail} Do not tell the user it was delivered and do not send it again. "
                f"A receipt will follow when it is released, denied or expires. (message {r['msg_id']})")
    return f"NOT delivered to {to} ({state}): {detail}. (message {r['msg_id']})"
