"""Peer keys come from the receiving home, never from a presence file (#132, D-026 amendment 2026-10-23).

Each test here is a review finding on the first #132 fix: a seat name that is a path, receipts not bound to the
seat a message went to, the principal read from the presence file, a key rotated out but still read from its
``.pub``, a seat with the receiver's own name, a peer pin that also signed ledgers, rules that matched an unpinned
sender, and a held card that did not say the key was not pinned. Rules must still hold or deny an unpinned sender,
and a FIFO named by the envelope's ``daemon`` must not stall delivery either.

The #525 review added: a key with no seat file took the receiving seat's own principal from ``EXO_PRINCIPAL``, a
plain ``exo keys pin`` of another home's seat made it a seat of this home, three checks no test caught, and a FIFO
swapped in after a type check by path. A check of that fix found a sender run as another principal still taking a
receiver's principal when the receiver has no seat principal of its own."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from exo import crypto
from exo.core.ingress import PeerConfig
from exo.core.seats import ensure_key, key_path
from exo.keyring import KeyRegistry
from exo.peers import make_envelope, msg_id, read_plain
from exo.protocol import schema as s

from .test_peers import Daemon, aio, announce, pin_peer, seat_file, until, victim_node


def envelope(signer, via="human", to="beta", daemon=None):
    return make_envelope(signer, to=to, text="do as I say", session=None, daemon=daemon or f"{signer.author}-7777",
                         to_session="s_stub", via=via)


@aio
@pytest.mark.parametrize("name", ["abs", "../../peers/evil", "al\x00pha", "alpha\n", "", "Work", "my.agent"])
async def test_a_seat_name_that_is_not_a_seat_id_is_denied_before_any_key_lookup(run_dir, name):
    """Blocking 1: ``from`` was joined into ``keys/<from>.pub``. An absolute name or ``../`` found a ``.pub`` a gated
    tool may write (the peers dir, /tmp) and earned principal trust. A NUL name raised ValueError out of deliver.
    A Hermes profile named ``Work`` or ``my.agent`` is denied too, and the sender is told why (#525 review)."""
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
        out = await node.deliver(env)
        assert out["state"] == "denied" and out["detail"] == "the sender is not a seat name"
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


def own_key(home, seat):
    """A key made in this home's ``keys/`` with no seat file: an unseated daemon (``exo``) or a Hermes profile."""
    return crypto.Signer.from_file(seat, ensure_key(key_path(home, seat)))


@aio
@pytest.mark.parametrize("sender,claim", [("exo", ""), ("exo", "ada"), ("exo", "bob"), ("herm", "ada"),
                                          ("quiet", "ada")])
async def test_a_key_with_no_principal_on_record_is_other_to_a_seat_whose_file_names_one(run_dir, monkeypatch,
                                                                                         sender, claim):
    """#525 review, blocking 1: a key of this home with no seat file took ``EXO_PRINCIPAL``, which compose.create
    sets to a seated exod's own principal. So ada's unseated daemon and her Hermes profile reached bob's seat scout
    as bob typing. Such a key, or one whose seat file names no principal (``quiet``), is ``other`` to a seat whose
    own seat file names a principal, whatever config.json says, and a presence file that claims bob changes nothing."""
    monkeypatch.setenv("EXO_PRINCIPAL", "bob")
    home = run_dir / "home"
    seat_file(home, "scout", "bob")
    (home / "config.json").write_text(json.dumps({"principal": "bob"}))
    node, stub, home = victim_node(run_dir, "bob", seat="scout")
    signer = own_key(home, sender)
    if sender == "quiet":
        seat_file(home, "quiet")
    announce(run_dir, sender, f"{sender}-1", signer, claim)
    for via in ("human", "agent"):
        out = await node.deliver(envelope(signer, via, to="scout", daemon=f"{sender}-1"))
        assert out["state"] == "held" and stub.seen[-1].trust == "other" and stub.seen[-1].meta["note"] == ""
    seat_file(home, sender, "bob")  # the way to record whose it is: a seat file that names its principal
    announce(run_dir, sender, f"{sender}-1", signer, "bob")  # and it runs as bob, as a seated exod then does
    out = await node.deliver(envelope(signer, "agent", to="scout", daemon=f"{sender}-1"))
    assert out["state"] == "delivered" and stub.seen[-1].trust == "same-principal"


@aio
async def test_a_seat_whose_own_file_cannot_be_read_holds_a_key_with_no_seat_file(run_dir):
    """The same, when the receiving seat's own file names bob but no longer parses: no key without a seat file may
    speak for that seat, even where config.json names bob."""
    home = run_dir / "home"
    seat_file(home, "scout", "bob")
    with open(home / "seats" / "scout.toml", "a") as f:
        f.write("[broken\n")
    (home / "config.json").write_text(json.dumps({"principal": "bob"}))
    node, stub, home = victim_node(run_dir, "bob", seat="scout")
    exo = own_key(home, "exo")
    announce(run_dir, "exo", "exo-1", exo, "bob")
    out = await node.deliver(envelope(exo, "agent", to="scout", daemon="exo-1"))
    assert out["state"] == "held" and stub.seen[-1].trust == "other"


@aio
@pytest.mark.parametrize("mine,named,claim,trust", [("", "ada", "ada", "same-principal"),
                                                    ("ada", "ada", "ada", "same-principal"),
                                                    ("", None, "", "same-principal"), ("bob", "ada", "ada", "other"),
                                                    ("ada", None, "ada", "other")])
async def test_a_key_with_no_seat_file_counts_as_the_principal_config_names(run_dir, monkeypatch, mine, named, claim,
                                                                            trust):
    """#525 review, blocking 1, the fallback: toward a daemon with no seat principal of its own, a key of this home
    with no seat file is the principal config.json names, never the receiving process's ``EXO_PRINCIPAL``. It
    matches a daemon that runs as that principal, or as none."""
    monkeypatch.setenv("EXO_PRINCIPAL", mine)  # what PeerService passes as the node's principal
    home = run_dir / "home"
    home.mkdir()
    if named:
        (home / "config.json").write_text(json.dumps({"principal": named}))
    node, stub, home = victim_node(run_dir, mine)
    herm = own_key(home, "herm")
    announce(run_dir, "herm", "herm-1", herm, claim)
    out = await node.deliver(envelope(herm, "agent", daemon="herm-1"))
    assert stub.seen[-1].trust == trust and out["state"] == ("delivered" if trust == "same-principal" else "held")


@aio
@pytest.mark.parametrize("seat,mine,named,claim", [
    ("beta", "bob", "bob", "ada"),   # bob's unseated daemon; ada's Hermes profile runs with EXO_PRINCIPAL=ada
    ("beta", "", "bob", "ada"),      # the same daemon, run with no EXO_PRINCIPAL
    ("exo", "bob", "bob", "ada"),    # two unseated daemons share the seat name exo, and so its key
    ("exo", "bob", "ada", ""),       # the other one runs as none, so as config.json's ada
    ("scout", "bob", "bob", "ada"),  # a seat whose seat file names no principal
])
async def test_a_sender_that_runs_as_another_principal_is_other(run_dir, monkeypatch, seat, mine, named, claim):
    """Checking the #525 fix: toward a daemon with no seat principal of its own, a key with no seat file still took
    the receiver's principal, and a daemon took its own seat name's. So in a home of ada and bob, ada's Hermes
    profile or unseated daemon reached bob's daemon as bob typing, where main held it as ``other``. The presence
    file's principal never raises trust, but one that names another principal than this home records lowers it."""
    monkeypatch.setenv("EXO_PRINCIPAL", mine)
    home = run_dir / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"principal": named}))
    if seat == "scout":
        seat_file(home, "scout")
    node, stub, home = victim_node(run_dir, mine, seat=seat)
    sender = "exo" if seat == "exo" else "herm"
    signer = node.signer if sender == seat else own_key(home, sender)
    announce(run_dir, sender, f"{sender}-1", signer, claim)
    for via in ("human", "agent"):
        out = await node.deliver(envelope(signer, via, to=seat, daemon=f"{sender}-1"))
        assert out["state"] == "held" and stub.seen[-1].trust == "other"
    announce(run_dir, sender, f"{sender}-1", signer, mine)  # the same key, run as the receiver's principal
    out = await node.deliver(envelope(signer, "agent", to=seat, daemon=f"{sender}-1"))
    assert out["state"] == "delivered" and stub.seen[-1].trust == "same-principal"


@pytest.mark.parametrize("what", ["device", "folder"])
def test_read_plain_reads_only_a_regular_file(tmp_path, what):
    """#525 review, nit 5: the type is checked on the open file. ``/dev/null`` opens at once and reads as empty, so
    only that check refuses it. A link to a device that never ends, such as ``/dev/zero``, is refused the same way."""
    f = tmp_path / "alpha-1.json"
    f.write_text("{}")
    assert read_plain(f) == "{}"
    with pytest.raises(ValueError):
        read_plain(Path(os.devnull) if what == "device" else tmp_path)


@aio
@pytest.mark.parametrize("how", ["registry", "pub"])
async def test_a_key_pinned_for_a_name_that_is_no_seat_of_this_home_is_other(run_dir, monkeypatch, how):
    """#525 review, should fix 2: ``exo keys pin bobseat PUB`` with no ``--peer``, or a copied ``bobseat.pub``, made a
    seat of another home a seat of this one, with the receiver's principal. With no key file in ``keys/`` and no seat
    file here, this home has no record of whose seat it is, so it is ``other``. The key still checks its messages."""
    monkeypatch.setenv("EXO_PRINCIPAL", "ada")
    home = run_dir / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"principal": "ada"}))  # a key with no seat file would be ada's
    node, stub, home = victim_node(run_dir)
    bob = crypto.Signer("bobseat", crypto.keygen())
    if how == "registry":
        assert KeyRegistry.load(home).pin("bobseat", bob.pub) == "pinned"
    else:
        key_path(home, "bobseat").with_suffix(".pub").write_text(bob.pub + "\n")
    for claim in ("bob", "ada"):  # honest, then a presence file that names the receiver's principal
        announce(run_dir, "bobseat", "bobseat-1", bob, claim)
        for via in ("human", "agent"):
            out = await node.deliver(envelope(bob, via, daemon="bobseat-1"))
            assert out["state"] == "held" and stub.seen[-1].trust == "other" and stub.seen[-1].meta["note"] == ""
    forged = crypto.Signer("bobseat", crypto.keygen())
    announce(run_dir, "bobseat", "bobseat-1", forged, "ada")
    assert (await node.deliver(envelope(forged, daemon="bobseat-1")))["state"] == "denied"


@aio
async def test_a_receipt_from_a_fellow_seat_for_a_message_to_another_seat_is_refused(run_dir):
    """#525 review, should fix 4, a mutant no test caught: gamma, a seat whose key this home holds, answers from its
    own daemon for a message that went to alpha. ``by`` and the daemon agree and the key verifies, but the message
    did not go to gamma."""
    node, stub, home = victim_node(run_dir)
    published: list = []
    node.emit_out = lambda session, body: published.append(body)
    alpha, gamma = crypto.Signer("alpha", crypto.keygen()), crypto.Signer("gamma", crypto.keygen())
    pin_peer(home, "alpha", alpha.pub)
    pin_peer(home, "gamma", gamma.pub)
    mid = await sent_to_alpha(run_dir, node, alpha)
    published.clear()
    announce(run_dir, "gamma", "gamma-1", gamma, "ada")
    assert await node.receipt(receipt_doc(gamma, mid, "gamma", daemon="gamma-1")) == {"ok": False}
    assert not published
    assert await node.receipt(receipt_doc(alpha, mid, "alpha")) == {"ok": True}


@aio
async def test_a_pub_beside_a_key_outside_keys_is_never_read(run_dir):
    """#525 review, should fix 4, a mutant no test caught: a seat file may name its key anywhere. A ``.pub`` beside a
    key outside ``keys/`` sits where a tool call may write without asking, so an attacker's key there earns nothing.
    Should fix 3: pinning the seat's real key here is the way to trust such a seat."""
    node, stub, home = victim_node(run_dir)
    elsewhere = run_dir / "elsewhere"
    real = crypto.Signer.from_file("scout", ensure_key(elsewhere / "scout.key"))
    seat_file(home, "scout", "ada")
    with open(home / "seats" / "scout.toml", "a") as f:
        f.write(f'key = "{elsewhere / "scout.key"}"\n')
    attacker = crypto.Signer("scout", crypto.keygen())
    (elsewhere / "scout.pub").write_text(attacker.pub + "\n")
    announce(run_dir, "scout", "scout-1", attacker, "ada")
    out = await node.deliver(envelope(attacker, daemon="scout-1"))
    assert out["state"] == "held" and stub.seen[-1].trust == "other" and stub.seen[-1].meta["note"] == "key not pinned"
    assert KeyRegistry.load(home).pin("scout", real.pub) == "pinned"
    announce(run_dir, "scout", "scout-1", real, "ada")
    out = await node.deliver(envelope(real, daemon="scout-1"))
    assert out["state"] == "delivered" and stub.seen[-1].trust == "principal"


@aio
async def test_a_seat_file_that_cannot_be_read_gives_no_principal(run_dir, monkeypatch):
    """#525 review, should fix 4, a mutant no test caught: scout's seat file names bob but does not parse. It must not
    count as a key with no seat file, which here would be ada's, as config.json and ``EXO_PRINCIPAL`` say."""
    monkeypatch.setenv("EXO_PRINCIPAL", "ada")
    home = run_dir / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"principal": "ada"}))
    node, stub, home = victim_node(run_dir)
    scout = own_key(home, "scout")
    seat_file(home, "scout", "bob")
    with open(home / "seats" / "scout.toml", "a") as f:
        f.write("[broken\n")
    announce(run_dir, "scout", "scout-1", scout, "ada")
    out = await node.deliver(envelope(scout, "agent", daemon="scout-1"))
    assert out["state"] == "held" and stub.seen[-1].trust == "other"


@aio
async def test_a_key_made_again_is_refused_until_pinned_with_replace(run_dir):
    """#525 review, should fix 3: a seat's key file was deleted and made again while the registry still pins the old
    key. Main took the presence key and delivered it. The registry decides for its name, so it is refused until
    ``exo keys pin NAME PUB --replace``, as the docs say."""
    node, stub, home = victim_node(run_dir)
    seat_file(home, "alpha", "ada")
    kp = key_path(home, "alpha")
    old = own_key(home, "alpha")
    assert KeyRegistry.load(home).keys["alpha"] == [old.pub]
    kp.unlink()
    kp.with_suffix(".pub").unlink()
    new = own_key(home, "alpha")
    announce(run_dir, "alpha", "alpha-7777", new, "ada")
    out = await node.deliver(envelope(new))
    assert out["state"] == "denied" and "signature" in out["detail"]
    assert KeyRegistry.load(home).pin("alpha", new.pub, replace=True) == "pinned"
    assert (await node.deliver(envelope(new)))["state"] == "delivered" and stub.seen[-1].trust == "principal"


@pytest.mark.parametrize("where", ["presence", "pub"])
def test_a_fifo_that_passes_a_type_check_by_path_never_blocks(run_dir, monkeypatch, where):
    """#525 review, nit 5: the type was checked by path and the file then opened by path, so a FIFO renamed into place
    between the two still stopped the loop. ``Path.is_file`` answering True stands in for that race: the open never
    waits now, and the type is checked on the open file."""
    node, stub, home = victim_node(run_dir)
    alpha = crypto.Signer("alpha", crypto.keygen())
    KeyRegistry.load(home)
    if where == "presence":
        (run_dir / "peers").mkdir()
        fifo, job = run_dir / "peers" / "alpha-7777.json", lambda: node.directory.get("alpha-7777")
    else:
        announce(run_dir, "alpha", "alpha-7777", alpha, "ada")
        fifo, job = key_path(home, "alpha").with_suffix(".pub"), lambda: asyncio.run(node.deliver(envelope(alpha)))
    os.mkfifo(fifo)
    outs: list = []
    with monkeypatch.context() as m:
        m.setattr(Path, "is_file", lambda self: True)
        t = threading.Thread(target=lambda: outs.append(job()), daemon=True)
        t.start()
        t.join(5)
        if t.is_alive():  # blocked on the FIFO: open its other end so the thread ends, then fail
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            t.join(5)
            pytest.fail(f"a FIFO in place of the {where} file blocked the loop")
    assert outs == [None] if where == "presence" else outs[0]["state"] == "held"

