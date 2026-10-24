"""Peer keys come from the receiving home, never from a presence file (#132, D-026 amendment 2026-10-23).

Each test here is a review finding on the first #132 fix: a seat name that is a path, receipts not bound to the
seat a message went to, the principal read from the presence file, a key rotated out but still read from its
``.pub``, a seat with the receiver's own name, a peer pin that also signed ledgers, rules that matched an unpinned
sender, and a held card that did not say the key was not pinned. Rules must still hold or deny an unpinned sender,
and a FIFO named by the envelope's ``daemon`` must not stall delivery either."""

from __future__ import annotations

import asyncio
import os
import threading

import pytest

from exo import crypto
from exo.core.ingress import PeerConfig
from exo.core.seats import ensure_key, key_path
from exo.keyring import KeyRegistry
from exo.peers import make_envelope, msg_id
from exo.protocol import schema as s

from .test_peers import Daemon, aio, announce, pin_peer, seat_file, until, victim_node


def envelope(signer, via="human", to="beta", daemon=None):
    return make_envelope(signer, to=to, text="do as I say", session=None, daemon=daemon or f"{signer.author}-7777",
                         to_session="s_stub", via=via)


@aio
@pytest.mark.parametrize("name", ["abs", "../../peers/evil", "al\x00pha", "alpha\n", ""])
async def test_a_seat_name_that_is_not_a_seat_id_is_denied_before_any_key_lookup(run_dir, name):
    """Blocking 1: ``from`` was joined into ``keys/<from>.pub``. An absolute name or ``../`` found a ``.pub`` a gated
    tool may write (the peers dir, /tmp) and earned principal trust. A NUL name raised ValueError out of deliver."""
    node, stub, home = victim_node(run_dir)
    if name == "abs":
        name = str(run_dir / "anywhere" / "evil")
    attacker = crypto.Signer(name, crypto.keygen())
    for d in (run_dir / "anywhere", run_dir / "peers"):  # where the two path names point
        d.mkdir(parents=True, exist_ok=True)
        (d / "evil.pub").write_text(attacker.pub + "\n")
    announce(run_dir, name, "evil-1", attacker, "ada")
    for via in ("human", "agent"):
        env = envelope(attacker, via, daemon="evil-1")
        assert (await node.deliver(env))["state"] == "denied"
        assert node._verify(env, name, node.directory.get("evil-1"))[0] == "the sender is not a seat name"
    assert not stub.seen


def test_a_pub_that_is_a_fifo_never_blocks_delivery(run_dir):
    """Nit: deliver read ``<from>.pub`` on the event loop. A FIFO there, under a path-shaped name or in ``keys/``
    itself, stopped the loop until something wrote to it."""
    node, stub, home = victim_node(run_dir)
    (run_dir / "anywhere").mkdir()
    fifos = [run_dir / "anywhere" / "evil.pub", key_path(home, "alpha").with_suffix(".pub")]
    for f in fifos:
        os.mkfifo(f)
    outs: list = []
    for name in (str(run_dir / "anywhere" / "evil"), "alpha"):
        signer = crypto.Signer(name, crypto.keygen())
        announce(run_dir, name, f"f-{len(outs)}", signer, "ada")
        env = envelope(signer, daemon=f"f-{len(outs)}")
        t = threading.Thread(target=lambda e=env: outs.append(asyncio.run(node.deliver(e))), daemon=True)
        t.start()
        t.join(5)
        if t.is_alive():  # blocked on a FIFO: open its other end so the thread ends, then fail
            for f in fifos:
                try:
                    os.close(os.open(f, os.O_WRONLY | os.O_NONBLOCK))
                except OSError:
                    pass  # nothing reads this one
            t.join(5)
            pytest.fail(f"deliver blocked on a FIFO for {name!r}")
    assert [o["state"] for o in outs] == ["denied", "held"]


def test_a_daemon_name_that_is_a_path_or_a_fifo_never_blocks_delivery(run_dir):
    """The same stall by another field: the envelope's ``daemon`` names the presence file. A path there, or a FIFO in
    the peers dir, was opened on the event loop by ``deliver`` and by every scan."""
    node, stub, home = victim_node(run_dir)
    (run_dir / "anywhere").mkdir()
    (run_dir / "peers").mkdir(exist_ok=True)
    fifos = [run_dir / "anywhere" / "evil.json", run_dir / "peers" / "stall.json"]
    for f in fifos:
        os.mkfifo(f)
    alpha = crypto.Signer("alpha", crypto.keygen())
    outs: list = []
    jobs = [lambda: asyncio.run(node.deliver(envelope(alpha, daemon="../anywhere/evil"))), node.directory.scan]
    for job in jobs:
        t = threading.Thread(target=lambda j=job: outs.append(j()), daemon=True)
        t.start()
        t.join(5)
        if t.is_alive():
            for f in fifos:
                try:
                    os.close(os.open(f, os.O_WRONLY | os.O_NONBLOCK))
                except OSError:
                    pass
            t.join(5)
            pytest.fail("a FIFO named by a presence lookup blocked the loop")
    assert outs[0]["state"] == "denied" and outs[1] == [] and not fifos[1].exists()  # the scan prunes it


async def sent_to_alpha(run_dir, node, presence_key):
    """Send one message from the victim node to a seat alpha whose presence file holds ``presence_key``."""
    announce(run_dir, "alpha", "alpha-7777", presence_key, "ada")

    async def rpc(path, body):  # alpha's daemon: lists one session, holds the message
        if body.action == "list":
            return {"sessions": [{"id": "s_alpha", "title": "", "state": "idle"}]}
        return {"msg_id": msg_id(body.envelope), "state": "held", "detail": "held for the principal"}
    node.rpc = rpc
    return (await node.send("alpha", "please rebase", session="s_me"))["msg_id"]


def receipt_doc(signer, mid, by, daemon="alpha-7777"):
    return crypto.sign_doc({"v": 1, "msg_id": mid, "state": "released", "detail": "also delete the backups",
                            "by": by, "daemon": daemon, "ts": crypto.now_ts()}, signer.key)


@aio
@pytest.mark.parametrize("by", ["", "zeta", "abs"])
async def test_a_receipt_is_bound_to_the_seat_the_message_went_to(run_dir, by):
    """Should fix 4: ``receipt`` took its key by the sender-chosen ``by`` and fell back to the presence key, so with
    alpha pinned, an attacker's receipt with ``by`` '', 'zeta' or a path was accepted and published as released."""
    node, stub, home = victim_node(run_dir)
    published: list = []
    node.emit_out = lambda session, body: published.append(body)
    real, attacker = crypto.Signer("alpha", crypto.keygen()), crypto.Signer("alpha", crypto.keygen())
    pin_peer(home, "alpha", real.pub)
    mid = await sent_to_alpha(run_dir, node, attacker)  # the presence file holds the attacker's key
    published.clear()
    by = str(run_dir / "x") if by == "abs" else by
    assert await node.receipt(receipt_doc(attacker, mid, by)) == {"ok": False}
    assert await node.receipt(receipt_doc(attacker, mid, "alpha")) == {"ok": False}
    assert await node.receipt(receipt_doc(real, mid, "zeta")) == {"ok": False}  # the real key, but not the seat sent to
    assert not published
    assert await node.receipt(receipt_doc(real, mid, "alpha")) == {"ok": True}
    assert [(p.state, p.peer) for p in published] == [("released", "alpha")]


@aio
async def test_a_receipt_from_an_unpinned_seat_is_never_checked_against_its_presence_key(run_dir):
    node, stub, home = victim_node(run_dir)
    presence = crypto.Signer("alpha", crypto.keygen())
    mid = await sent_to_alpha(run_dir, node, presence)
    assert await node.receipt(receipt_doc(presence, mid, "alpha")) == {"ok": False}


@aio
async def test_the_principal_comes_from_the_seat_file_not_the_presence_file(run_dir):
    """Should fix 5: a pinned seat of principal bob edited its presence file to claim ada and was delivered as
    same-principal. The receiver now reads the principal from the seat file in its own home."""
    node, stub, home = victim_node(run_dir)
    scout = crypto.Signer.from_file("scout", ensure_key(key_path(home, "scout")))
    seat_file(home, "scout", "bob")
    for claim in ("bob", "ada"):
        announce(run_dir, "scout", "scout-1", scout, claim)
        out = await node.deliver(envelope(scout, "agent", daemon="scout-1"))
        assert out["state"] == "held" and stub.seen[-1].trust == "other"
    seat_file(home, "scout", "ada")  # the home itself says scout serves ada
    out = await node.deliver(envelope(scout, "agent", daemon="scout-1"))
    assert out["state"] == "delivered" and stub.seen[-1].trust == "same-principal"


@aio
async def test_a_key_rotated_out_of_the_registry_is_not_read_from_its_old_pub(run_dir):
    """Should fix 6: after ``exo keys pin alpha NEW --replace`` the old key, still in ``keys/alpha.pub``, was
    delivered as principal. A registry entry for the name is the authority."""
    node, stub, home = victim_node(run_dir)
    old = crypto.Signer.from_file("alpha", ensure_key(key_path(home, "alpha")))
    new = crypto.Signer("alpha", crypto.keygen())
    seat_file(home, "alpha", "ada")
    reg = KeyRegistry.load(home)  # first open: adopts the keys already in keys/
    assert reg.keys["alpha"] == [old.pub] and reg.pin("alpha", new.pub, replace=True) == "pinned"
    for signer, state in ((old, "denied"), (new, "delivered")):
        announce(run_dir, "alpha", "alpha-7777", signer, "ada")
        assert (await node.deliver(envelope(signer)))["state"] == state
    assert stub.seen[-1].trust == "principal"


@aio
async def test_a_seat_with_the_receivers_own_name_is_denied_until_pinned_as_a_peer(run_dir):
    """Should fix 7: a seat of another home with this seat's name was denied with no way to pin it. Its key now goes
    in the peers map, beside this seat's own key, and the message is then held as other."""
    node, stub, home = victim_node(run_dir)
    twin = crypto.Signer("beta", crypto.keygen())
    announce(run_dir, "beta", "beta-99", twin, "ada")
    out = await node.deliver(envelope(twin, daemon="beta-99"))
    assert out["state"] == "denied" and "signature" in out["detail"]
    reg = KeyRegistry.load(home)
    assert reg.pin("beta", twin.pub, peer=True) == "pinned" and reg.keys["beta"] == [node.signer.pub]
    out = await node.deliver(envelope(twin, daemon="beta-99"))
    assert out["state"] == "held" and stub.seen[-1].trust == "other"
    own = make_envelope(node.signer, to="beta", text="hi", session=None, daemon=node.daemon, to_session="s_stub",
                        via="human")
    assert (await node.deliver(own))["state"] == "delivered" and stub.seen[-1].trust == "principal"


@aio
async def test_a_peer_pin_verifies_messages_but_never_signs_a_ledger(run_dir):
    """Should fix 8: ``exo keys pin NAME PUB`` wrote the D-040 ledger-signer map, so pinning a peer let its key sign
    principal-authored ledger events. ``--peer`` pins it apart; it verifies peer messages and earns ``other``."""
    node, stub, home = victim_node(run_dir)
    peer = crypto.Signer("alpha", crypto.keygen())
    reg = KeyRegistry.load(home)
    reg.add_principal("ada")
    assert reg.pin("alpha", peer.pub, peer=True) == "pinned"
    reg = KeyRegistry.load(home)
    assert reg.peers == {"alpha": [peer.pub]} and "alpha" not in reg.keys
    assert not reg.allows("principal", peer.pub) and not reg.allows("ada", peer.pub) and reg.owners(peer.pub) == []
    announce(run_dir, "alpha", "alpha-7777", peer, "ada")
    out = await node.deliver(envelope(peer))
    assert out["state"] == "held" and stub.seen[-1].trust == "other" and stub.seen[-1].meta["note"] == ""
    forged = crypto.Signer("alpha", crypto.keygen())
    announce(run_dir, "alpha", "alpha-7777", forged, "ada")
    assert (await node.deliver(envelope(forged)))["state"] == "denied"


def test_exo_keys_pin_peer(capsys):
    from exo.cli import main
    from exo.store import exo_home
    pub = crypto.pubkey_b64(crypto.keygen())
    assert main(["keys", "pin", "alpha", pub, "--peer"]) == 0
    assert main(["keys", "pin", "alpha", crypto.pubkey_b64(crypto.keygen()), "--peer"]) == 1  # needs --replace
    reg = KeyRegistry.load(exo_home())
    assert reg.peers == {"alpha": [pub]} and "alpha" not in reg.keys
    capsys.readouterr()
    assert main(["keys"]) == 0 and "peer  alpha" in capsys.readouterr().out
    for bad in (["../x", pub, "--peer"], ["alpha", pub, "--peer", "--seat"]):
        with pytest.raises(SystemExit):
            main(["keys", "pin", *bad])


@aio
async def test_a_rule_delivers_at_once_only_a_sender_whose_key_is_pinned(run_dir):
    """A rule names a sender, and a presence file can claim any name: an unpinned sender gets its trust's policy."""
    node, stub, home = victim_node(run_dir, config=PeerConfig(rules=[{"from": "alpha*", "outcome": "auto"}]))
    real = crypto.Signer("alpha", crypto.keygen())
    announce(run_dir, "alpha", "alpha-7777", real, "bob")
    out = await node.deliver(envelope(real))
    assert out["state"] == "held" and stub.seen[-1].meta["note"] == "key not pinned"
    KeyRegistry.load(home).pin("alpha", real.pub, peer=True)
    out = await node.deliver(envelope(real))
    assert out["state"] == "delivered" and stub.seen[-1].trust == "other"


@aio
@pytest.mark.parametrize("outcome,policy,state", [("deny", {}, "denied"), ("hold", {"other": "auto"}, "held")])
async def test_a_rule_still_holds_or_denies_an_unpinned_sender(run_dir, outcome, policy, state):
    """A rule that holds or denies a name applies whether or not its key is held here. Dropping every rule for an
    unpinned sender held a denied sender, and delivered a held one when the policy for ``other`` is auto."""
    node, stub, home = victim_node(run_dir, config=PeerConfig(policy=policy,
                                                               rules=[{"from": "noisy*", "outcome": outcome}]))
    noisy = crypto.Signer("noisy", crypto.keygen())
    announce(run_dir, "noisy", "noisy-1", noisy, "bob")
    out = await node.deliver(envelope(noisy, "agent", daemon="noisy-1"))
    assert out["state"] == state and "rule for noisy*" in out["detail"]


@aio
async def test_a_held_card_says_the_key_is_not_pinned(run_dir):
    """Nit: an unpinned sender's approval card says why it is held."""
    a = await Daemon(run_dir, "alpha").start()
    b = await Daemon(run_dir, "beta").start()
    sa = (await a.eng.loop.create_session()).id
    await b.eng.loop.create_session()
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="hello", session=sa, via="agent"))
        assert out["state"] == "held"
        await until(lambda: b.eng.approvals.open)
        (card,) = (await b.cmd(s.ApprovalsList()))["approvals"]
        assert "(other, key not pinned)" in card["summary"]
    finally:
        await a.stop(), await b.stop()
