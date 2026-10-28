"""A signed first reply is not a receipt (#603 review, on #523 and #132).

Since #523 ``deliver`` signs its first reply for any envelope, one that does not verify too, and the reply had the
fields a later receipt has. Any client of beta's socket could read a held message's id from beta's own list, send
beta a junk envelope with that id, and hand the signed ``denied`` that came back to alpha's ``receipt``: alpha
published ``denied`` for a message still held at beta, which breaks #132's rule that a later receipt comes only from
the seat the message went to. The two docs now sign their ``kind``: ``receipt`` takes a receipt (an older exo's
kind-less one too, told apart from a #523 reply by the ``mode`` and ``session`` that reply signs), and the send
path takes only a reply (a #523 build's kind-less one too, by the same rule). The review's nits ride along: a presence
entry verifies over the JSON it was read from, and a reply that is not a mapping fails as a refused reply."""

from __future__ import annotations

import json
import os

import pytest

from exo import crypto
from exo.core.ingress import Receipt
from exo.peers import call_socket, make_envelope, msg_id
from exo.protocol import schema as s

from .test_peers import aio, announce, pair, pin_peer, until, victim_node


def junk_envelope(held_id):
    """An envelope no seat signed, naming a held message's id: what any client of the socket can send."""
    return {"v": 1, "ts": "2026-10-26T00:00:00Z", "from": "mallory", "to": ["beta"], "thread": None, "kind": "msg",
            "body": "x", "attach": [{"peer": {"daemon": "nobody"}}], "enc": None, "prev": "",
            "id": held_id.removeprefix("pm_"), "sig": "AA"}


@aio
async def test_a_signed_reply_to_a_junk_envelope_is_no_receipt(run_dir):
    """The review's repro on two engines over their real sockets. alpha's message is held at beta; a socket client
    lists beta's held id, has beta sign a reply to a junk envelope with it, and offers that to alpha as a receipt.
    alpha refuses it and publishes nothing; the message stays held at beta, and beta's real receipt still lands."""
    a, b, sa, sb = await pair(run_dir, b_peers={"policy": {"same-principal": "hold"}})
    try:
        first = await a.cmd(s.Peers(action="send", to="beta", text="please hold", session=sa, via="agent"))
        held = [h["msg_id"] for h in (await call_socket(b.sock, s.Peers(action="list")))["held"]]
        assert first["state"] == "held" and held == [first["msg_id"]]
        reply = await call_socket(b.sock, s.Peers(action="deliver", envelope=junk_envelope(held[0])))
        assert reply["state"] == "denied" and reply["msg_id"] == held[0]  # beta still answers, and signs it
        assert await call_socket(a.sock, s.Peers(action="receipt", envelope=reply)) == {"ok": False}
        assert [e.body.state for e in a.events(s.PeerDelivery) if e.body.direction == "out"] == ["held"]
        assert held[0] in b.eng.peers.node.path.held
        rid = (await b.cmd(s.ApprovalsList()))["approvals"][0]["request_id"]
        await b.cmd(s.ApprovalRespond(request_id=rid, decision="allow", client="tui"))
        await until(lambda: any(e.body.state == "released" for e in a.events(s.PeerDelivery)))
        assert [e.body.state for e in a.events(s.PeerDelivery) if e.body.direction == "out"] == ["held", "released"]
    finally:
        await a.stop(), await b.stop()


@aio
async def test_a_signed_first_reply_still_completes_a_send(run_dir):
    """The honest path over real sockets, both seats pinned: beta's signed first reply is published as it was."""
    a, b, sa, sb = await pair(run_dir)
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="hello", session=sa, via="agent"))
        assert out["state"] == "delivered"
        assert [e.body.state for e in a.events(s.PeerDelivery) if e.body.direction == "out"] == ["delivered"]
    finally:
        await a.stop(), await b.stop()


def sent(node, run_dir, home):
    """The victim node has sent ``pm_1`` to alpha, whose key it pins and whose presence file is honest."""
    real = crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", real.pub)
    announce(run_dir, "alpha", "alpha-7777", real, "ada")
    node._sent["pm_1"] = (None, "alpha", "alpha")
    return real


def doc(signer, **extra):
    return crypto.sign_doc({"v": 1, "msg_id": "pm_1", "state": "denied", "detail": "", "by": "alpha",
                            "daemon": "alpha-7777", "ts": crypto.now_ts(), **extra}, signer.key)


@aio
@pytest.mark.parametrize("extra", [{"kind": "reply"}, {"mode": "hold", "session": None}, {"kind": "note"}],
                         ids=["reply", "reply-523", "other-kind"])
async def test_receipt_refuses_a_first_reply(run_dir, extra):
    """A doc the seat's own key signed, naming the message, the seat and its daemon, and still no receipt: one
    signed as a reply, a #523 reply (no kind, but the ``mode`` and ``session`` it signs), or another kind."""
    node, stub, home = victim_node(run_dir)
    real = sent(node, run_dir, home)
    published: list = []
    node.emit_out = lambda session, body: published.append(body)
    assert await node.receipt(doc(real, **extra)) == {"ok": False}
    assert not published


@aio
@pytest.mark.parametrize("extra", [{"kind": "receipt"}, {}], ids=["receipt", "older-exo"])
async def test_receipt_takes_a_receipt_and_an_older_exos_kindless_one(run_dir, extra):
    node, stub, home = victim_node(run_dir)
    real = sent(node, run_dir, home)
    assert await node.receipt(doc(real, **extra)) == {"ok": True}


@aio
async def test_the_receiver_signs_each_doc_with_its_kind(run_dir):
    """``deliver`` signs its first reply as a reply and ``notify_sender`` a later outcome as a receipt."""
    node, stub, home = victim_node(run_dir)
    alpha = crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", alpha.pub)
    announce(run_dir, "alpha", "alpha-7777", alpha, "ada")
    env = make_envelope(alpha, to="beta", text="hi", session=None, daemon="alpha-7777", to_session="s_stub",
                        via="agent")
    reply = await node.deliver(env)
    assert reply["kind"] == "reply" and crypto.verify_doc(reply, node.signer.pub)
    got: list = []

    async def alpha_socket(path, body):
        got.append(body.envelope)
        return {"ok": True}
    node.rpc = alpha_socket
    await node.notify_sender(stub.seen[-1], Receipt(msg_id=reply["msg_id"], state="released"))
    assert [(d["kind"], d["state"]) for d in got] == [("receipt", "released")]
    assert crypto.verify_doc(got[0], node.signer.pub)


def first_reply_from(real, **extra):
    async def rpc(path, body):
        if body.action == "list":
            return {"sessions": [{"id": "s_alpha", "title": "", "state": "idle"}]}
        return crypto.sign_doc({"v": 1, "msg_id": msg_id(body.envelope), "state": "delivered", "detail": "on it",
                                "by": "alpha", "daemon": "alpha-7777", "ts": crypto.now_ts(), **extra}, real.key)
    return rpc


@aio
@pytest.mark.parametrize("extra", [{"kind": "receipt"}, {}], ids=["receipt", "older-exo"])
async def test_a_receipt_offered_as_a_first_reply_is_refused(run_dir, extra):
    """The other direction: a doc the pinned key signed as a later receipt, or an older exo's kind-less receipt, is
    no first reply. Nothing is published and the send fails."""
    node, stub, home = victim_node(run_dir)
    real = crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", real.pub)
    announce(run_dir, "alpha", "alpha-7777", real, "ada")
    published: list = []
    node.emit_out = lambda session, body: published.append(body)
    node.rpc = first_reply_from(real, **extra)
    with pytest.raises(ValueError, match="not a first reply"):
        await node.send("alpha", "did you ship?", session="s_me")
    assert not published


@aio
async def test_a_523_builds_first_reply_still_completes_a_send(run_dir):
    """A #523 build signs its first reply with no kind but with ``mode`` and ``session``: still a reply, so a send
    to it is published as before (its text has already arrived, so refusing the outcome would only invite a
    second send)."""
    node, stub, home = victim_node(run_dir)
    real = crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", real.pub)
    announce(run_dir, "alpha", "alpha-7777", real, "ada")
    published: list = []
    node.emit_out = lambda session, body: published.append(body)
    node.rpc = first_reply_from(real, mode="started", session="s_alpha")
    out = await node.send("alpha", "did you ship?", session="s_me")
    assert out["state"] == "delivered" and [p.state for p in published] == ["delivered"]


@aio
async def test_a_reply_that_is_not_a_mapping_is_a_refused_reply(run_dir):
    """A peer that answers a deliver with a list: the send fails with a clean ValueError, not AttributeError."""
    node, stub, home = victim_node(run_dir)
    real = crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", real.pub)
    announce(run_dir, "alpha", "alpha-7777", real, "ada")

    async def odd(path, body):
        if body.action == "list":
            return {"sessions": [{"id": "s_alpha", "title": "", "state": "idle"}]}
        return ["delivered"]
    node.rpc = odd
    with pytest.raises(ValueError, match="not a reply"):
        await node.send("alpha", "did you ship?", session="s_me")


@aio
async def test_a_presence_entry_verifies_over_the_fields_it_signed(run_dir):
    """A newer exo signs a field this one does not know (``caps``). The signature covers the JSON as written, so
    the entry verifies and the send goes through."""
    node, stub, home = victim_node(run_dir)
    real = crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", real.pub)
    entry = {"seat": "alpha", "daemon": "alpha-7777", "pid": os.getpid(), "platform": "exo",
             "socket": "/tmp/x.sock", "pub": real.pub, "principal": "ada", "cwd": "", "started": "", "v": 1,
             "caps": ["x"]}
    (run_dir / "peers").mkdir(parents=True, exist_ok=True)
    (run_dir / "peers" / "alpha-7777.json").write_text(json.dumps(crypto.sign_doc(entry, real.key)))
    node.rpc = first_reply_from(real, kind="reply")
    out = await node.send("alpha", "did you ship?", session="s_me")
    assert out["state"] == "delivered"
