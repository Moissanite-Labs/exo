"""The core's side of the bus channel (D-040): an inbox envelope goes through the same ingress path as a peer message.

The channel (plugins/exo-bus) polls the seat's inbox and calls the ``peers`` command with action ``bus`` for each
envelope. Here the envelope is checked against the sender seat's pinned key (exo.keyring; never the relay's key),
framed as a bus message, stamped with the author ``bus:<seat>`` (exo.provenance), and put through the receiving
seat's peer policy: auto, hold for the principal, or deny. A remote seat is held by default.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from exo import gate, provenance
from exo.bus import wire
from exo.bus.client import render_body
from exo.core.ingress import Ingress, IngressPath
from exo.core.peer_target import EngineTarget
from exo.protocol import schema as s

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine


class BusTarget(EngineTarget):
    """Bus messages through the same ingress path as peers: policy, hold for the principal, deliver, receipt.

    Each bus chat has its own session (the channel makes it), so a delivered message may start a turn there
    whether or not a client is watching; the reply only goes back to the bus when the policy delivered the
    message on its own (the channel reads the receipt)."""

    def __init__(self, engine: "CoreEngine") -> None:
        super().__init__(engine)
        self.seen: OrderedDict[str, None] = OrderedDict()  # envelope ids already taken: a replay is refused
        self.path = IngressPath(self, lambda ing: gate.decide_peer(ing.trust, ing.sender, {
            "policy": self.e.peer_cfg.policy, "rules": self.e.peer_cfg.rules}),
            hold_seconds=self.e.peer_cfg.hold_minutes * 60)

    def _path(self) -> Any:
        return self.path

    def pick_session(self, ing: Ingress) -> str | None:
        return ing.session if ing.session and self.takes(ing.session) else None

    def can_wake(self, session: str) -> bool:
        return True

    def _meta(self, ing: Ingress) -> dict[str, Any]:
        return {"bus": {"from": ing.sender, "trust": ing.trust, "msg_id": ing.id, "envelope": ing.meta.get("envelope"),
                        "thread": ing.meta.get("thread")},
                "author": provenance.bus(ing.sender)}


def verify_bus_envelope(env: dict[str, Any], registry_pub: str) -> tuple[str, str]:
    """(trust, why) for an inbox envelope: ``remote`` when it is signed by the sender seat's pinned key, else
    ``unverified``. The seat's key is pinned the first time it is seen (exo.keyring); a later envelope signed by
    any other key, the relay's included, is refused."""
    from exo.keyring import KeyRegistry

    seat = str(env.get("from") or "")
    try:
        core = wire.core(env)
        if wire.envelope_id(core) != env.get("id"):
            return "unverified", "the id does not match the envelope content"
    except Exception as exc:
        return "unverified", f"malformed envelope ({type(exc).__name__})"
    reg = KeyRegistry.load()
    pinned = reg.seats.get(seat)
    if pinned is None:
        if not registry_pub:
            return "unverified", f"no public key for seat {seat!r}"
        if not wire.verify_doc(core, registry_pub):
            return "unverified", "the signature does not verify against the seat's key"
        reg.pin_seat(seat, registry_pub)
        return "remote", ""
    if registry_pub and registry_pub != pinned:
        why = f"seat {seat!r} now has a different key than the one pinned; pin it with exo keys pin --seat --replace"
        if not wire.verify_doc(core, pinned):
            return "unverified", why
    if not wire.verify_doc(core, pinned):
        return "unverified", "the signature does not verify against the seat's pinned key"
    return "remote", ""


async def bus_ingress(engine: "CoreEngine", b: s.Peers) -> dict[str, Any]:
    """``peers`` action ``bus``: one inbox envelope from the bus channel, with its decrypted text, the bus session it
    belongs to and the registry's key for the sender. Returns the first receipt."""
    env = dict(b.envelope or {})
    seat = str(env.get("from") or "")
    if not seat or not env.get("id"):
        raise ValueError("bus: the envelope needs from and id")
    trust, why = verify_bus_envelope(env, b.pub)
    tgt = getattr(engine, "_bus_target", None)
    if tgt is None:
        tgt = engine._bus_target = BusTarget(engine)
    if trust != "unverified" and env["id"] in tgt.seen:
        trust, why = "unverified", "this envelope was already delivered (replay)"
    elif trust != "unverified":
        tgt.seen[env["id"]] = None
        while len(tgt.seen) > 5000:
            tgt.seen.popitem(last=False)
    # The signature covers the body, not the channel's text: read an open body here, and take the channel's text only
    # for a body sealed to this seat, which the core cannot open.
    text = (b.text or "") if env.get("enc") else render_body(str(env.get("body") or ""))
    ing = Ingress("bus", seat, trust, text, session=b.session, id="bm_" + str(env["id"])[:12],
                  meta={"kind": "msg", "from_seat": seat, "thread": env.get("thread") or env.get("id"),
                        "envelope": env.get("id"), "verify": why, "chat": b.to})
    receipt = await tgt.path.submit(ing)
    if why and receipt.state == "denied":
        receipt.detail = f"{receipt.detail} ({why})"
    return receipt.to_dict()
