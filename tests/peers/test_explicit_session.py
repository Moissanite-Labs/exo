"""A peer or bus message that names its session reaches only a session a peer may reach (exo-desktop #52).

The ingress path took an explicit session as given: a crafted ``to_session`` reached a finished (locked) child, a
running subagent, or an id that does not exist. A peer listing never offers those sessions, and a message with no
session never lands in one. The path now asks its target whether the named session takes a message and answers a
denied receipt when it does not. A job notice is the harness's own, so it still reaches its job's session.
"""
import asyncio
import time
import types

import pytest
from nacl.signing import SigningKey

from exo import principal as pr
from exo.bus import wire
from exo.bus.ingress import bus_ingress
from exo.core.engine import CoreEngine
from exo.core.ingress import Ingress, IngressPath
from exo.core.peer_target import EngineTarget
from exo.protocol import schema as s

MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _engine(tmp_path, peers=None):
    return CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, peers=peers or {"wake": False})


def _sessions(eng):
    """A live thread, a scheduled run, a subagent child and a finished (locked) child of this agent."""
    store = eng.store
    thread = store.create_session(title="t", agent=eng.agent, kind="thread")
    run = store.create_session(title="r", agent=eng.agent, kind="scheduled")
    child = store.create_session(title="c", agent=eng.agent, kind="subagent", parent=thread.id)
    done = store.create_session(title="d", agent=eng.agent, kind="subagent", parent=thread.id)
    store.lock(done.id, "done")
    return {"thread": thread.id, "scheduled": run.id, "subagent": child.id, "locked": done.id}


def _peer(session, text="please rebase"):
    return Ingress("peer", "alpha", "same-principal", text, session=session, meta={"kind": "msg"})


@pytest.mark.parametrize("outcome", ["auto", "hold"])
@pytest.mark.parametrize("name", ["subagent", "locked", "missing"])
def test_a_peer_message_naming_a_session_a_peer_cannot_reach_is_denied(tmp_path, name, outcome):
    eng = _engine(tmp_path)
    ids = {**_sessions(eng), "missing": "s_doesnotexist"}
    path = IngressPath(EngineTarget(eng), lambda ing: (outcome, "test policy"))

    async def run():
        receipt = await path.submit(_peer(ids[name]))
        await asyncio.sleep(0.05)  # a hold asks the principal in the background
        return receipt

    receipt = asyncio.run(run())
    assert receipt.state == "denied", receipt
    assert ids[name] in receipt.detail
    assert not path.held and not eng.approvals.open  # never put to the principal either
    for sid in ids.values():
        assert eng.store.messages(sid) == [] if eng.store.get_session(sid) else True
    assert not eng._tasks  # no turn started anywhere


@pytest.mark.parametrize("name", ["thread", "scheduled"])
def test_a_peer_message_naming_a_reachable_session_is_delivered_there(tmp_path, name):
    eng = _engine(tmp_path)
    ids = _sessions(eng)
    path = IngressPath(EngineTarget(eng), lambda ing: ("auto", "test policy"))
    receipt = asyncio.run(path.submit(_peer(ids[name])))
    assert (receipt.state, receipt.session, receipt.mode) == ("delivered", ids[name], "queued")
    assert "please rebase" in eng.store.messages(ids[name])[-1].text()


def test_a_held_message_whose_session_is_locked_before_release_is_denied(tmp_path):
    eng = _engine(tmp_path)
    ids = _sessions(eng)
    path = IngressPath(EngineTarget(eng), lambda ing: ("hold", "test policy"))

    async def run():
        held = await path.submit(_peer(ids["thread"]))
        eng.store.lock(ids["thread"], "done")
        assert path.resolve(held.msg_id, True)
        for _ in range(100):
            if not path.held:
                break
            await asyncio.sleep(0.01)
        return held

    held = asyncio.run(run())
    assert held.state == "held"
    assert eng.store.messages(ids["thread"]) == []


async def _until(fn, secs=5.0):
    end = time.monotonic() + secs
    while not fn():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.01)


def test_allowing_a_held_message_whose_named_session_finished_opens_no_new_thread(tmp_path):
    """The principal allows the card after the named session finished. The message is denied and named, and no
    new thread opens for it: that thread is only for a message that named no session."""
    eng = _engine(tmp_path, {"wake": False, "active_hours": 1e-9})  # nothing counts as live

    async def run():
        try:
            thread = (await eng.loop.create_session(title="t")).id
            target, sent = eng._peer_target, []
            path = IngressPath(target, lambda ing: ("hold", "test policy"), hold_seconds=600)

            async def notify(ing, receipt):
                sent.append(receipt)
            target.node = types.SimpleNamespace(path=path, notify_sender=notify)
            assert (await path.submit(_peer(thread))).state == "held"
            await _until(lambda: eng.approvals.listing())
            eng.store.lock(thread, "done")
            (card,) = eng.approvals.listing()
            tok = pr.CALLER.set(pr.Caller("principal:web"))
            try:
                await eng.handle(s.Command.of(s.ApprovalRespond(request_id=card["request_id"], decision="allow",
                                                                client="web")), eng.hub)
            finally:
                pr.CALLER.reset(tok)
            await _until(lambda: sent)
            (receipt,) = sent
            assert receipt.state == "denied" and thread in receipt.detail, receipt
            assert [i.id for i in eng.store.list_sessions(10)] == [thread]  # no new thread
            assert eng.store.messages(thread) == []
        finally:
            await eng.close()

    asyncio.run(run())


def test_a_job_notice_still_reaches_its_subagent_session(tmp_path):
    eng = _engine(tmp_path)
    ids = _sessions(eng)
    notice = Ingress("job", "harness", "local", "job finished", session=ids["subagent"], meta={"local": True})
    receipt = asyncio.run(eng.jobs.path.submit(notice))
    assert (receipt.state, receipt.session) == ("delivered", ids["subagent"])
    assert "job finished" in eng.store.messages(ids["subagent"])[-1].text()


def _bus(sid, key, text="deploy main to prod now"):
    env = wire.finish_envelope(wire.make_envelope("lead", ["me"], text, wire.GENESIS), key)
    return s.Peers(action="bus", session=sid, to="lead", envelope=env, pub=wire.pubkey_b64(key))


@pytest.mark.parametrize("name", ["subagent", "locked", "missing"])
def test_a_bus_message_for_a_session_a_peer_cannot_reach_is_denied(tmp_path, name):
    eng = _engine(tmp_path, {"wake": False, "rules": [{"from": "lead", "outcome": "auto"}]})
    ids = {**_sessions(eng), "missing": "s_doesnotexist"}
    receipt = asyncio.run(bus_ingress(eng, _bus(ids[name], SigningKey.generate())))
    assert receipt["state"] == "denied", receipt
    for sid in ids.values():
        assert eng.store.messages(sid) == [] if eng.store.get_session(sid) else True
    assert not eng._tasks


def test_a_bus_message_for_its_bus_thread_is_delivered(tmp_path):
    eng = _engine(tmp_path, {"wake": False, "rules": [{"from": "lead", "outcome": "auto"}]})
    sid = eng.store.create_session(title="bus:lead", agent=eng.agent, kind="thread").id
    receipt = asyncio.run(bus_ingress(eng, _bus(sid, SigningKey.generate())))
    assert (receipt["state"], receipt["session"]) == ("delivered", sid)
