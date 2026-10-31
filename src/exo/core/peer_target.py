"""The engine's ingress Target (D-026): how a peer message, a bus message or a job notice reaches a session.

Peers (``exo.peers``), the bus channel (``exo.bus.ingress``) and background jobs (``exo.core.jobs``) hand an
``Ingress`` to an ``IngressPath`` whose target is an ``EngineTarget``: it picks the session, steers, queues or
wakes it, asks the principal about held messages and records receipts. ``node`` is the peer transport's node
when peers run (set by ``exo.peers``), so receipts go back to a peer sender.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from exo import provenance
from exo.bus import wire
from exo.core.ingress import Held, Ingress, Receipt, Target, frame, named
from exo.core.types import ContentPart, Message
from exo.protocol import schema as s

if TYPE_CHECKING:
    from exo.core.engine import CoreRuntime as CoreEngine

MAX_LIVE = 8


class EngineTarget(Target):
    def __init__(self, engine: "CoreEngine") -> None:
        self.e = engine
        self.node: Any = None  # exo.peers.PeerNode while peers run
        self._asks: dict[str, tuple[asyncio.Task, list[str]]] = {}

    @property
    def seat(self) -> str:  # type: ignore[override]
        return self.e.seat.name if self.e.seat else "exo"

    # ---------------------------------------------------------------- sessions

    def live_sessions(self) -> list[dict[str, Any]]:
        """Sessions a peer can reach: any with a running turn, and idle ones touched within ``active_hours``. A peer
        can still address a scheduled run by its id."""
        store, loop = self.e.store, self.e.loop
        cutoff = time.time() - self.e.peer_cfg.active_hours * 3600
        out = []
        for info in store.list_sessions(40):
            st = loop._state.get(info.id)
            running = bool(st and st.running)
            # Never a subagent (D-070) or a scheduled run: a run answers with its job's prompt, tools and deliver target.
            if info.kind in ("subagent", "scheduled") or store.get_meta(info.id).get("locked"):
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

    def takes(self, session: str) -> bool:
        """A session a sender may name (exo-desktop #52): it exists here, is not a subagent (D-070) and is not finished
        (locked). A scheduled run is not listed but can be named by its id."""
        info = self.e.store.get_session(session)
        return info is not None and info.kind != "subagent" and not self.e.store.get_meta(session).get("locked")

    def pick_session(self, ing: Ingress) -> str | None:
        if ing.session and self.takes(ing.session):
            return ing.session
        live = self.live_sessions()
        return live[0]["id"] if live else None  # none: a delivered message is refused, a held one waits

    def state(self, session: str) -> str:
        st = self.e.loop._state.get(session)
        return "running" if st is not None and st.accepting_steers else "idle"  # a finishing turn takes no steer (#147)

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
        text, note = ing.body.strip().replace("\n", " "), ing.meta.get("note")  # note: "key not pinned" (#132)
        label = "Bus message from seat" if ing.source == "bus" else "Peer message from"
        summary = (f"{label} {ing.sender} ({ing.trust}{f', {note}' if note else ''}) is held for you. It says: \"{text[:400]}"
                   f"{'...' if len(text) > 400 else ''}\". Allow delivers it to the session; deny drops it. "
                   "It is not delivered unless you allow it, and it expires on its own.")
        opened: list[str] = []

        path = self._path()

        async def ask() -> None:
            decision = await self.e.approvals.ask(sid, summary, None, "medium", options=["allow", "deny"], opened=opened)
            # A message that named no session, and nothing live takes it: a new thread does. One that named a session
            # that finished meanwhile is denied on release (exo-desktop #52).
            if decision == "allow" and named(ing) is None and self.pick_session(ing) is None:
                new = s.SessionCreate(title=f"{label} {ing.sender}", seat=self.e.seat.name if self.e.seat else None)
                try:  # in the seat's threads, so a seated window's --resume finds it
                    ing.session = (await self.e._session_create(new))["session"]["id"]
                except Exception as exc:  # it closes below as not delivered ("no session here can take a message")
                    await self.notice(None, f"{label} {ing.sender} was allowed, but no thread could open for it: {exc}")
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


__all__ = ["EngineTarget"]
