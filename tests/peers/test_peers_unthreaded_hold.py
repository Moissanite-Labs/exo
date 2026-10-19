"""A held peer message with no live session (D-026): its approval has no session, and allow still delivers it.

Before the fix the allow found no session either and closed the message as denied ("no session here can take a
message"), although the card says allow delivers it. Now the principal's allow opens a new thread for it.
"""
from __future__ import annotations

import asyncio
import time
import types

from exo import principal as pr
from exo.core.engine import CoreEngine
from exo.core.ingress import Ingress, IngressPath
from exo.core.seats import Seat
from exo.protocol import schema as s


def _engine(tmp_path, active_hours: float, seat: Seat | None = None) -> CoreEngine:
    return CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={},
                      peers={"active_hours": active_hours}, seat=seat)


async def _until(fn, secs: float = 5.0) -> None:
    end = time.monotonic() + secs
    while not fn():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.01)


async def _hold_and_answer(eng: CoreEngine, decision: str) -> tuple[str | None, list]:
    """Submit a held peer message with no target session, answer its card as the principal, return the card's
    session and the inbound receipts."""
    t = eng._peer_target
    path = IngressPath(t, lambda ing: ("hold", "a different principal"), hold_seconds=600)

    async def notify(*_):
        return None
    t.node = types.SimpleNamespace(path=path, notify_sender=notify)
    receipt = await path.submit(Ingress("peer", "alice/abcd", "other", "can you look at the build?"))
    assert receipt.state == "held"
    await _until(lambda: eng.approvals.listing())
    (card,) = eng.approvals.listing()
    tok = pr.CALLER.set(pr.Caller("principal:web"))
    try:
        await eng.handle(s.Command.of(s.ApprovalRespond(request_id=card["request_id"], decision=decision, client="web")),
                         eng.hub)
    finally:
        pr.CALLER.reset(tok)

    def closed():
        return [e.body for e in eng.hub.since(0) if isinstance(e.body, s.PeerDelivery) and e.body.state != "held"]
    await _until(closed)
    deadline = time.monotonic() + 10
    while eng._tasks and time.monotonic() < deadline:  # a woken turn finishes before the engine closes
        await asyncio.sleep(0.01)
    return card["session"], closed()


def test_allow_with_no_live_session_delivers_into_a_new_thread(tmp_path):
    eng = _engine(tmp_path, active_hours=1e-9)  # every thread counts as idle too long: none is live

    async def run():
        old = (await eng.loop.create_session(title="yesterday")).id
        try:
            assert eng._peer_target.live_sessions() == []
            session, receipts = await _hold_and_answer(eng, "allow")
            assert session is None  # the card has no thread: clients show it in every one
            (r,) = receipts
            assert r.state == "released", r.detail
            assert not eng.store.messages(old)  # not dropped into an unrelated old thread
            new = [i for i in eng.store.list_sessions(10) if i.id != old]
            assert [i.title for i in new] == ["Peer message from alice/abcd"]
            stored = [m for m in eng.store.messages(new[0].id) if m.role == "user"]
            assert "can you look at the build?" in stored[0].text()
            assert stored[0].meta["peer"]["from"] == "alice/abcd" and stored[0].meta["author"] == "peer:alice/abcd"
        finally:
            await eng.close()

    asyncio.run(run())


def test_deny_with_no_live_session_opens_no_thread(tmp_path):
    eng = _engine(tmp_path, active_hours=1e-9)

    async def run():
        old = (await eng.loop.create_session(title="yesterday")).id
        try:
            _, receipts = await _hold_and_answer(eng, "deny")
            assert [r.state for r in receipts] == ["denied"]
            assert [i.id for i in eng.store.list_sessions(10)] == [old]
        finally:
            await eng.close()

    asyncio.run(run())


def test_allow_with_a_live_session_still_goes_there(tmp_path):
    eng = _engine(tmp_path, active_hours=12)

    async def run():
        live = (await eng.loop.create_session(title="today")).id
        try:
            session, receipts = await _hold_and_answer(eng, "allow")
            assert session == live
            assert [r.state for r in receipts] == ["released"]
            assert [i.id for i in eng.store.list_sessions(10)] == [live]
            assert any("can you look at the build?" in m.text() for m in eng.store.messages(live))
        finally:
            await eng.close()

    asyncio.run(run())


def test_the_new_thread_is_in_the_seat(tmp_path):
    """A seated window's --resume lists only its seat's threads, so the new thread carries the seat."""
    eng = _engine(tmp_path, active_hours=1e-9, seat=Seat(name="desk"))

    async def run():
        try:
            _, receipts = await _hold_and_answer(eng, "allow")
            assert [r.state for r in receipts] == ["released"]
            (new,) = eng.store.list_sessions(10)
            assert new.seat == "desk"
        finally:
            await eng.close()

    asyncio.run(run())


def test_a_thread_that_cannot_open_closes_the_message_and_says_so(tmp_path):
    """Before, the failure stayed in an unretrieved task and the allowed message waited until it expired."""
    eng = _engine(tmp_path, active_hours=1e-9)

    async def broken(_body):
        raise OSError("disk full")
    eng._session_create = broken

    async def run():
        try:
            _, receipts = await _hold_and_answer(eng, "allow")
            assert [(r.state, r.detail) for r in receipts] == [("denied", "no session here can take a message")]
            notes = [e.body.text for e in eng.hub.since(0) if isinstance(e.body, s.Notice)]
            assert any("no thread could open" in n and "disk full" in n for n in notes), notes
        finally:
            await eng.close()

    asyncio.run(run())
