"""The engine's side of peers (D-026): the ingress Target, the peers tools, and presence for exod.

``attach(engine)`` is called from ``CoreEngine.__init__``. It builds the ``PeerNode`` lazily (the seat key
is made on first use, so an engine that never serves peers never writes one), registers the ``peers`` and
``peer_send`` tools, and gives the loop's system prompt the standing line about what a peer cannot do.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from exo import crypto, provenance
from exo.bus import wire
from exo.core.ingress import STANDING_LINE, Held, Ingress, Receipt, Target, frame
from exo.core.peers import BusLink, Directory, PeerNode, peers_dir, short
from exo.core.seats import ensure_key, key_path
from exo.core.tools.registry import ToolContext, ToolError
from exo.core.types import ContentPart, Message, ToolSpec
from exo.protocol import schema as s

if TYPE_CHECKING:
    from exo.core.engine import CoreEngine

MAX_LIVE = 8

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


class EngineTarget(Target):
    def __init__(self, engine: "CoreEngine") -> None:
        self.e = engine
        self.node: PeerNode | None = None
        self._asks: dict[str, tuple[asyncio.Task, list[str]]] = {}

    @property
    def seat(self) -> str:  # type: ignore[override]
        return self.e.seat.name if self.e.seat else "exo"

    # ---------------------------------------------------------------- sessions

    def live_sessions(self) -> list[dict[str, Any]]:
        """Sessions a peer can reach: any with a running turn, and idle ones touched within ``active_hours``."""
        store, loop = self.e.store, self.e.loop
        cutoff = time.time() - self.e.peer_cfg.active_hours * 3600
        out = []
        for info in store.list_sessions(40):
            st = loop._state.get(info.id)
            running = bool(st and st.running)
            if store.get_meta(info.id).get("locked"):
                continue
            try:
                fresh = wire.parse_ts(info.updated).timestamp() >= cutoff
            except (ValueError, TypeError):
                fresh = False
            if running or fresh:
                out.append({"id": info.id, "title": info.title, "state": "running" if running else "idle",
                            "started": info.created, "cwd": info.cwd or self.e.cwd})
        out.sort(key=lambda r: r["state"] != "running")  # stable: running first, then most recent
        return out[:MAX_LIVE]

    def pick_session(self, ing: Ingress) -> str | None:
        if ing.session and self.e.store.get_session(ing.session) is not None:
            return ing.session
        live = self.live_sessions()
        if live:
            return live[0]["id"]
        return None  # the caller creates one

    async def ensure_session(self, ing: Ingress) -> str:
        sid = self.pick_session(ing)
        if sid:
            return sid
        info = await self.e.loop.create_session(title=f"peer:{ing.sender}", seat=self.e.seat.name if self.e.seat else None,
                                                model=self.e._main_ref(), cwd=self.e.cwd)
        return info.id

    def state(self, session: str) -> str:
        st = self.e.loop._state.get(session)
        return "running" if st is not None and st.running else "idle"

    def can_wake(self, session: str) -> bool:
        wake = self.e.peer_cfg.wake
        if wake is None:  # interactive means somebody is watching: no client on the event stream means headless
            return not self.e.hub._subs
        return bool(wake)

    def open_approvals(self) -> set[str]:
        return set(self.e.approvals.open)

    # ---------------------------------------------------------------- delivery

    def _message(self, ing: Ingress) -> Message:
        msg = Message(role="user", content=[ContentPart.of_text(frame(ing))])
        msg.meta.update(self._meta(ing))
        return msg

    def _meta(self, ing: Ingress) -> dict[str, Any]:
        """The ingress tags and the author (D-040) a delivered message carries, as stored and as a woken turn's."""
        return {"peer": {"from": ing.sender, "trust": ing.trust, "msg_id": ing.id, "session": ing.meta.get("from_session")},
                "author": provenance.peer(ing.sender)}

    async def steer(self, session: str, ing: Ingress) -> bool:
        return self.e.loop.steer(session, self._message(ing))

    async def queue(self, session: str, ing: Ingress) -> None:
        msg = self.e.store.append_message(session, self._message(ing))
        await self.e.loop._emit(s.MessageCompleted(message=msg), session)

    async def wake(self, session: str, ing: Ingress) -> None:
        self.e.start_turn(session, frame(ing), self._meta(ing))

    # ---------------------------------------------------------------- holding

    async def on_held(self, held: Held) -> None:
        ing = held.ing
        sid = self.pick_session(ing)
        text = ing.body.strip().replace("\n", " ")
        label = "Bus message from seat" if ing.source == "bus" else "Peer message from"
        summary = (f"{label} {ing.sender} ({ing.trust}) is held for you. It says: \"{text[:400]}"
                   f"{'...' if len(text) > 400 else ''}\". Allow delivers it to the session; deny drops it. "
                   "It is not delivered unless you allow it, and it expires on its own.")
        opened: list[str] = []

        path = self._path()

        async def ask() -> None:
            decision = await self.e.approvals.ask(sid, summary, None, "medium", options=["allow", "deny"], opened=opened)
            if path is not None:
                path.resolve(ing.id, decision == "allow")

        task = asyncio.ensure_future(ask())
        self._asks[ing.id] = (task, opened)

    def _path(self) -> Any:
        """The ingress path whose held messages this target resolves."""
        return self.node.path if self.node is not None else None

    async def on_closed(self, held: Held, state: str) -> None:
        task, opened = self._asks.pop(held.ing.id, (None, []))
        if task is None:
            return
        rid = opened[0] if opened else ""
        if rid in self.e.approvals.open and not task.done():  # nobody answered (expired): close the prompt
            self.e.approvals.announce_resolved(rid, state, "peers", self.pick_session(held.ing))
            task.cancel()

    # ---------------------------------------------------------------- record

    async def record(self, ing: Ingress, receipt: Receipt, notify: bool) -> None:
        sid = receipt.session or ing.session
        if receipt.state in ("delivered", "released") and sid:
            self.e.hub.publish(s.PeerMessage(msg_id=ing.id, sender=ing.sender, text=ing.body, trust=ing.trust,
                                             sender_session=ing.meta.get("from_session"), mode=receipt.mode), sid)
        self.e.hub.publish(s.PeerDelivery(msg_id=ing.id, state=receipt.state, peer=ing.sender, direction="in",  # type: ignore[arg-type]
                                          detail=receipt.detail), sid)
        if notify and self.node is not None:
            await self.node.notify_sender(ing, receipt)

    async def notice(self, session: str | None, text: str) -> None:
        self.e.hub.publish(s.Notice(text=text, level="warn"), session or self.pick_session(Ingress("peer", "", "other", "")))


# ---------------------------------------------------------------- engine wiring


def build_node(engine: "CoreEngine", socket: str) -> PeerNode:
    tgt: EngineTarget = engine._peer_target
    seat = tgt.seat
    kp = engine.seat.key_path(engine.home) if engine.seat else key_path(engine.home, seat)
    signer = crypto.Signer.from_file(seat, ensure_key(kp))
    node = PeerNode(seat=seat, signer=signer, target=tgt, sessions=tgt.live_sessions, socket=socket, platform="exo",
                    principal=(engine.seat.principal if engine.seat else None) or os.environ.get("EXO_PRINCIPAL", ""),
                    cwd=engine.cwd, config=engine.peer_cfg, bus=BusLink.from_env(),
                    directory=Directory(engine.peer_cfg_dir or peers_dir()),
                    emit_out=lambda session, body: engine.hub.publish(body, session))
    tgt.node = node
    return node


def attach(engine: "CoreEngine") -> None:
    """Make the engine peer-aware: target, tools and the standing prompt line. Idempotent per engine."""
    engine._peer_target = EngineTarget(engine)
    if not engine.peer_cfg.enabled:
        return

    def need_node() -> PeerNode:
        if engine.peer_node is None:
            raise ToolError("peers are not running in this process (the engine has no peer socket yet)")
        return engine.peer_node

    async def peers(args: dict[str, Any], ctx: ToolContext) -> str:
        res = await need_node().list(ctx.session)
        rows = res["peers"]
        lines = [f"You are {res['me']}. {len(rows)} peer(s):"] if rows else [f"You are {res['me']}. No other live sessions."]
        for r in rows:
            lines.append(f"- {r['name']}  [{r['platform']}, {r['state']}]  {r['cwd'] or ''}  {r['title'] or ''}".rstrip())
        for h in res["held"]:
            lines.append(f"held for your principal (not delivered to you): message {h['msg_id']} from {h['from']}")
        return "\n".join(lines)

    async def peer_send(args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            r = await need_node().send(str(args.get("to") or ""), str(args.get("text") or ""), ctx.session, "agent")
        except ValueError as exc:
            raise ToolError(str(exc)) from None
        return describe(r)

    engine.registry.register(PEERS, peers, replace=True)
    engine.registry.register(SEND, peer_send, replace=True)


def describe(r: dict[str, Any]) -> str:
    """What the sending model is told. Plain about held and refused: it must not claim delivery."""
    to, state, detail = r.get("to", "the peer"), r.get("state"), r.get("detail", "")
    if state == "delivered":
        return f"Delivered to {to}: {detail}. (message {r['msg_id']})"
    if state == "held":
        return (f"HELD, NOT delivered to {to}: {detail} Do not tell the user it was delivered and do not send it again. "
                f"A receipt will follow when it is released, denied or expires. (message {r['msg_id']})")
    return f"NOT delivered to {to} ({state}): {detail}. (message {r['msg_id']})"


__all__ = ["EngineTarget", "STANDING_LINE", "attach", "build_node", "describe", "short"]
