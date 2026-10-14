"""ApprovalDesk under pressure: concurrent asks, a cancel during an ask, and seat answers (D-033, D-039, D-045)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from exo import crypto
from exo.core.approvals import sign_approval
from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _setup(tmp_path):
    eng = CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={})
    return eng, Dispatcher(eng, caller="principal:tui")


def _resolved(eng):
    return [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]


def _requested(eng):
    return {e.body.summary: e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalRequested)}


async def _respond(d, rid, decision, caller=None, **kw):
    return await d.run(s.Command.of(s.ApprovalRespond(request_id=rid, decision=decision, **kw)), caller)


def test_concurrent_asks_resolve_independently(tmp_path):
    async def go():
        eng, d = _setup(tmp_path)
        a = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        b = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        who_a: list[str] = []
        who_b: list[str] = []
        ta = asyncio.ensure_future(eng.approvals.ask(a, "first", None, decided_by=who_a))
        tb = asyncio.ensure_future(eng.approvals.ask(b, "second", None, decided_by=who_b, binding="h2"))
        await asyncio.sleep(0.02)
        req = _requested(eng)
        listed = (await d.run(s.Command.of(s.ApprovalsList()))).result["approvals"]
        assert {r["request_id"]: r["session"] for r in listed} == {req["first"].request_id: a,
                                                                   req["second"].request_id: b}
        only_b = (await d.run(s.Command.of(s.ApprovalsList(session=b)))).result["approvals"]
        assert [r["request_id"] for r in only_b] == [req["second"].request_id]

        # an answer bound to other text does not touch either request
        stale = await _respond(d, req["second"].request_id, "allow", binding="other")
        assert not stale.ok and stale.error.code == "stale"
        assert (await _respond(d, req["second"].request_id, "deny", binding="h2")).ok
        assert await tb == "deny" and not ta.done()
        assert who_b == ["principal:tui"]
        assert (await _respond(d, req["first"].request_id, "allow_always", caller="principal:web")).ok
        assert await ta == "allow_always" and who_a == ["principal:web"]

        resolved = _resolved(eng)
        assert sorted((r.request_id, r.decision, r.by) for r in resolved) == sorted([
            (req["second"].request_id, "deny", "principal:tui"),
            (req["first"].request_id, "allow_always", "principal:web")])
        assert not eng.approvals.open and not eng.approvals._announced
        again = await _respond(d, req["first"].request_id, "allow")
        assert not again.ok and again.error.code == "not_found"
        assert eng.loop.approval_ms.get(a, -1) >= 0 and eng.loop.approval_ms.get(b, -1) >= 0

    asyncio.run(go())


def test_cancel_during_ask_closes_the_card_once_and_refuses_late_answers(tmp_path):
    async def go():
        eng, d = _setup(tmp_path)
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        opened: list[str] = []
        task = asyncio.ensure_future(eng.approvals.ask(sid, "run it?", None, opened=opened))
        await asyncio.sleep(0.02)
        (rid,) = opened
        assert rid in eng.approvals.open
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert [(r.request_id, r.decision, r.by) for r in _resolved(eng)] == [(rid, "cancelled", "core")]
        late = await _respond(d, rid, "allow")
        assert not late.ok and late.error.code == "not_found"
        assert (await d.run(s.Command.of(s.ApprovalsList()))).result["approvals"] == []
        assert not eng.approvals.open and not eng.approvals._announced

        # an answer and a cancel in the same tick: the asker sees the answer, one card closes
        task = asyncio.ensure_future(eng.approvals.ask(sid, "and this?", None, opened=opened))
        await asyncio.sleep(0.02)
        rid2 = opened[-1]
        assert (await _respond(d, rid2, "allow")).ok
        task.cancel()
        res = (await asyncio.gather(task, return_exceptions=True))[0]
        assert res == "allow" or isinstance(res, asyncio.CancelledError)
        assert [r.decision for r in _resolved(eng) if r.request_id == rid2] == ["allow"]
        assert not eng.approvals.open and not eng.approvals._announced

    asyncio.run(go())


def test_non_principal_callers_answer_only_as_signed_seats_and_never_always(tmp_path):
    async def go():
        eng, d = _setup(tmp_path)
        key = crypto.keygen()
        crypto.save_key(key, Path(os.environ["EXO_HOME"]) / "keys" / "reviewer.key")
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]

        def sig(rid, decision):
            return sign_approval(key, rid, decision, "", "reviewer")

        task = asyncio.ensure_future(eng.approvals.ask(sid, "post it", None))
        await asyncio.sleep(0.02)
        rid = _requested(eng)["post it"].request_id
        tries = [
            # a local connection cannot claim the principal through the body
            await _respond(d, rid, "deny", caller="local", client="principal"),
            await _respond(d, rid, "deny", caller="local", client=""),
            # a peer label is refused even on a principal connection
            await _respond(d, rid, "deny", caller="principal:tui", client="peer:other"),
            # a seat must sign, and a signed seat still cannot give a standing grant
            await _respond(d, rid, "deny", caller="local", client="seat:reviewer"),
            await _respond(d, rid, "allow_always", caller="local", client="seat:reviewer",
                           sig=sig(rid, "allow_always")),
            # a signature over one decision does not cover another
            await _respond(d, rid, "allow", caller="local", client="seat:reviewer", sig=sig(rid, "deny")),
        ]
        assert [t.error.code for t in tries] == ["forbidden"] * len(tries), [t.error for t in tries]
        assert not task.done() and not _resolved(eng)
        assert "standing grant" in tries[4].error.message
        ok = await _respond(d, rid, "deny", caller="local", client="seat:reviewer", sig=sig(rid, "deny"))
        assert ok.ok, ok.error
        assert await task == "deny"
        assert [(r.decision, r.by) for r in _resolved(eng)] == [("deny", "seat:reviewer")]
        assert not eng.approvals.always

    asyncio.run(go())
