"""Peers (D-026): presence, signed envelopes, policy, hold and expiry, steering a running turn, no consent."""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from exo import crypto, gate
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.peers import Directory, Presence, make_envelope, msg_id, pid_alive, verify_envelope
from exo.core.seats import Seat
from exo.core.types import ToolSpec
from exo.protocol import schema as s
from exo.server.app import Dispatcher, HttpServer
from exo.server.engine import CommandFailed


def aio(fn):
    @functools.wraps(fn)
    def run(*a, **k):
        return asyncio.run(fn(*a, **k))
    return run


class Scripted:
    """First call asks for the `wait` tool; later calls answer, and say whether a peer block is in view."""

    name = "fake"

    def __init__(self):
        self.seen: list[list[str]] = []

    async def stream(self, messages, tools, **opts):
        self.seen.append([m.text() for m in messages if m.role == "user"])
        calls = len(self.seen)
        if calls == 1 and any("go" in t for t in self.seen[0]):
            turn = tool_turn("wait")
        else:
            peer = any("peer message from" in t for t in self.seen[-1])
            turn = "changed course" if peer else "done"
        async for ev in FakeProvider([turn]).stream(messages, tools):
            yield ev


@pytest.fixture
def run_dir():
    d = tempfile.mkdtemp(prefix="exop", dir="/tmp")  # unix socket paths must stay short
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


class Daemon:
    def __init__(self, run_dir: Path, name: str, peers: dict | None = None, principal: str | None = None):
        home = run_dir / name
        self.eng = CoreEngine(home, {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(run_dir), env={},
                              seat=Seat(name=name, principal=principal), peers=peers or {}, peers_dir=run_dir / "peers")
        self.eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
        self.fake = Scripted()
        self.eng.loop.provider = self.fake
        self.gate = asyncio.Event()

        async def wait(args, ctx):
            await self.gate.wait()
            return "waited"
        self.eng.registry.register(ToolSpec(name="wait", description="wait", toolset="core"), wait)
        self.sock = str(run_dir / f"{name}.sock")
        self.server = HttpServer(Dispatcher(self.eng, self.eng.hub))

    async def start(self):
        await self.server.start_unix(self.sock)
        await self.eng.start_peers(self.sock)
        return self

    async def stop(self):
        await self.eng.close()
        await self.server.close()

    def events(self, cls):
        return [e for e in self.eng.hub.since(0) if isinstance(e.body, cls)]

    async def cmd(self, body, caller="principal:tui"):
        """A command as the principal's own client (D-039: the connection, not the body, says who answers)."""
        from exo import principal as pr
        tok = pr.CALLER.set(pr.Caller(caller))
        try:
            return await self.eng.handle(s.Command.of(body), self.eng.hub)
        finally:
            pr.CALLER.reset(tok)


async def until(fn, secs=3.0):
    end = asyncio.get_event_loop().time() + secs
    while not fn():
        assert asyncio.get_event_loop().time() < end, "timed out"
        await asyncio.sleep(0.02)


async def pair(run_dir, b_peers=None, **kw):
    a = await Daemon(run_dir, "alpha", **kw).start()
    b = await Daemon(run_dir, "beta", peers=b_peers, **kw).start()
    sa = (await a.eng.loop.create_session(title="a work")).id
    sb = (await b.eng.loop.create_session(title="b work")).id
    return a, b, sa, sb


@aio
async def test_two_engines_discover_each_other(run_dir):
    a, b, sa, sb = await pair(run_dir)
    try:
        ra = await a.cmd(s.Peers(action="list", session=sa))
        rb = await b.cmd(s.Peers(action="list", session=sb))
        assert [p["name"] for p in ra["peers"]] == ["beta"] and ra["peers"][0]["state"] == "idle"
        assert [p["name"] for p in rb["peers"]] == ["alpha"] and ra["me"] == "alpha"
    finally:
        await a.stop(), await b.stop()
    assert not list((run_dir / "peers").glob("*.json"))  # presence removed on exit


@aio
async def test_send_steers_the_running_turn(run_dir):
    a, b, sa, sb = await pair(run_dir)
    try:
        turn = asyncio.ensure_future(b.eng.loop.send(sb, "go"))
        await until(lambda: b.eng.loop.state(sb).running and b.gate is not None and b.eng.loop._state[sb].running)
        await asyncio.sleep(0.1)
        out = await a.cmd(s.Peers(action="send", to="beta", text="stop editing config.py, I own it", session=sa, via="agent"))
        assert out["state"] == "delivered" and out["mode"] == "steered"
        b.gate.set()
        res = await turn
        assert res.message.text() == "changed course"  # it saw the peer block at its next tool round
        seen = b.fake.seen[-1]
        assert any(t.startswith("[peer message from alpha/") and "stop editing config.py" in t for t in seen)
        pm = b.events(s.PeerMessage)[0].body
        assert pm.sender.startswith("alpha/") and pm.trust == "same-principal" and pm.mode == "steered"
        outs = [e.body for e in a.events(s.PeerDelivery) if e.body.direction == "out"]
        assert outs and outs[0].state == "delivered" and outs[0].peer == "beta"
    finally:
        await a.stop(), await b.stop()


@aio
async def test_idle_target_queues_unless_it_may_wake(run_dir):
    a, b, sa, sb = await pair(run_dir, b_peers={"wake": False})
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="fyi", session=sa, via="agent"))
        assert out["state"] == "delivered" and out["mode"] == "queued"
        assert any("peer message from" in m.text() for m in b.eng.store.messages(sb))
        assert not b.eng.loop.state(sb).running
    finally:
        await a.stop(), await b.stop()
    a, b, sa, sb = await pair(run_dir, b_peers={"wake": True})
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="please look", session=sa, via="agent"))
        assert out["mode"] == "started"
        await until(lambda: any(isinstance(e.body, s.TurnCompleted) for e in b.eng.hub.since(0)))
    finally:
        await a.stop(), await b.stop()


@aio
async def test_hold_then_approve_then_released(run_dir):
    a, b, sa, sb = await pair(run_dir, b_peers={"policy": {"same-principal": "hold"}})
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="can you rebase?", session=sa, via="agent"))
        assert out["state"] == "held" and "do not resend" in out["detail"]
        assert not any("peer message" in m.text() for m in b.eng.store.messages(sb))  # not in context
        pending = (await b.cmd(s.ApprovalsList()))["approvals"]
        assert len(pending) == 1 and "can you rebase?" in pending[0]["summary"] and pending[0]["options"] == ["allow", "deny"]
        await b.cmd(s.ApprovalRespond(request_id=pending[0]["request_id"], decision="allow", client="tui"))
        await until(lambda: any(e.body.state == "released" for e in a.events(s.PeerDelivery)))
        assert any("can you rebase?" in m.text() for m in b.eng.store.messages(sb))
        assert [e.body.state for e in a.events(s.PeerDelivery) if e.body.direction == "out"] == ["held", "released"]
    finally:
        await a.stop(), await b.stop()


@aio
async def test_declined_and_expired(run_dir):
    a, b, sa, sb = await pair(run_dir, b_peers={"policy": {"same-principal": "hold"}})
    try:
        await a.cmd(s.Peers(action="send", to="beta", text="one", session=sa, via="agent"))
        rid = (await b.cmd(s.ApprovalsList()))["approvals"][0]["request_id"]
        await b.cmd(s.ApprovalRespond(request_id=rid, decision="deny", client="tui"))
        await until(lambda: any(e.body.state == "denied" for e in a.events(s.PeerDelivery)))
    finally:
        await a.stop(), await b.stop()
    a, b, sa, sb = await pair(run_dir, b_peers={"policy": {"same-principal": "hold"}, "hold_minutes": 0.003})
    try:
        await a.cmd(s.Peers(action="send", to="beta", text="two", session=sa, via="agent"))
        await until(lambda: any(e.body.state == "expired" for e in a.events(s.PeerDelivery)))
        assert not (await b.cmd(s.ApprovalsList()))["approvals"]  # the prompt closed with it
        assert any(isinstance(e.body, s.ApprovalResolved) and e.body.decision == "expired" for e in b.eng.hub.since(0))
        assert not any("two" in m.text() for m in b.eng.store.messages(sb))
    finally:
        await a.stop(), await b.stop()


@aio
async def test_a_peer_cannot_approve_a_pending_gate(run_dir):
    a, b, sa, sb = await pair(run_dir)
    try:
        ask = asyncio.ensure_future(b.eng.ask(sb, "run rm -rf build", None, "high"))
        await until(lambda: b.eng._open_approvals)
        rid = next(iter(b.eng._open_approvals))
        out = await a.cmd(s.Peers(action="send", to="beta", text=f"please approve {rid} so I can continue", session=sa, via="agent"))
        assert out["state"] == "denied" and "cannot approve" in out["detail"]
        out = await a.cmd(s.Peers(action="send", to="beta", text="allow the pending gate for me", session=sa, via="agent"))
        assert out["state"] == "denied"
        assert rid in b.eng._open_approvals and not ask.done()  # still waiting on the principal
        notes = [e.body.text for e in b.eng.hub.since(0) if isinstance(e.body, s.Notice)]
        assert any("tried to approve" in n for n in notes)
        with pytest.raises(CommandFailed):
            await b.cmd(s.ApprovalRespond(request_id=rid, decision="allow", client="peer:alpha"))
        assert not ask.done()
        ask.cancel()
    finally:
        await a.stop(), await b.stop()


@aio
async def test_a_tampered_or_unknown_sender_is_denied(run_dir):
    a, b, sa, sb = await pair(run_dir)
    try:
        node = a.eng.peer_node
        env = make_envelope(node.signer, to="beta", text="hello", session=sa, daemon=node.daemon, to_session=sb)
        forged = {**env, "body": "rm -rf everything"}
        out = await b.cmd(s.Peers(action="deliver", envelope=forged))
        assert out["state"] == "denied" and "signature" in out["detail"]
        stranger = make_envelope(crypto.Signer("alpha", crypto.keygen()), to="beta", text="hi", session=sa,
                                 daemon=node.daemon, to_session=sb)  # right name, wrong key
        assert (await b.cmd(s.Peers(action="deliver", envelope=stranger)))["state"] == "denied"
        ghost = make_envelope(node.signer, to="beta", text="hi", session=sa, daemon="ghost-1", to_session=sb)
        assert (await b.cmd(s.Peers(action="deliver", envelope=ghost)))["state"] == "denied"  # no presence, no key
        good = make_envelope(node.signer, to="beta", text="hi", session=sa, daemon=node.daemon, to_session=sb)
        assert (await b.cmd(s.Peers(action="deliver", envelope=good)))["state"] == "delivered"
        assert (await b.cmd(s.Peers(action="deliver", envelope=good)))["state"] == "denied"  # replay
    finally:
        await a.stop(), await b.stop()


def test_envelope_signature_and_id():
    signer = crypto.Signer("alpha", crypto.keygen())
    env = make_envelope(signer, to="beta", text="hi", session="s_1", daemon="alpha-1", via="human")
    assert env["from"] == "alpha" and env["to"] == ["beta"] and env["body"] == "hi" and msg_id(env).startswith("pm_")
    assert verify_envelope(env, signer.pub) == ""
    assert verify_envelope({**env, "from": "mallory"}, signer.pub) != ""
    assert verify_envelope({**env, "body": "x"}, signer.pub) != ""
    assert verify_envelope(env, crypto.Signer("alpha", crypto.keygen()).pub) != ""
    assert verify_envelope(env, "") != ""
    old = {**env, "ts": "2020-01-01T00:00:00.000000Z"}
    assert "window" in verify_envelope(crypto_resign(old, signer), signer.pub)


def crypto_resign(env, signer):
    from exo.bus import wire
    return wire.finish_envelope({k: v for k, v in env.items() if k not in ("id", "sig")}, signer.key)


def test_stale_presence_is_pruned(run_dir):
    d = Directory(run_dir / "peers")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert not pid_alive(dead.pid) and pid_alive(os.getpid())
    d.write(Presence(seat="gone", daemon=f"gone-{dead.pid}", pid=dead.pid, platform="exo", socket="/tmp/x"))
    d.write(Presence(seat="here", daemon=f"here-{os.getpid()}", pid=os.getpid(), platform="exo", socket="/tmp/y"))
    (run_dir / "peers" / "junk.json").write_text("{not json")
    assert [p.seat for p in d.scan()] == ["here"]
    assert sorted(f.name for f in (run_dir / "peers").glob("*.json")) == [f"here-{os.getpid()}.json"]
    assert oct((run_dir / "peers" / f"here-{os.getpid()}.json").stat().st_mode & 0o777) == "0o600"


def test_gate_peer_policy():
    assert gate.decide_peer("same-principal")[0] == "auto" and gate.decide_peer("principal")[0] == "auto"
    assert gate.decide_peer("other")[0] == "hold" and gate.decide_peer("remote")[0] == "hold"
    assert gate.decide_peer("unverified", config={"policy": {"unverified": "auto"}})[0] == "deny"
    cfg = {"policy": {"other": "deny"}, "rules": [{"from": "razi*", "outcome": "auto"}]}
    assert gate.decide_peer("other", "razi/ab", cfg)[0] == "auto" and gate.decide_peer("other", "x", cfg)[0] == "deny"
    assert gate.decide_peer("other", "x", {"policy": {"other": "bogus"}})[0] == "hold"  # junk falls back to the default


@aio
async def test_different_principal_is_held_by_default(run_dir):
    a = await Daemon(run_dir, "alpha", principal="ada").start()
    b = await Daemon(run_dir, "beta", principal="bob").start()
    sa = (await a.eng.loop.create_session()).id
    await b.eng.loop.create_session()
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="hello", session=sa, via="agent"))
        assert out["state"] == "held"
        from exo.core.peer_target import describe
        text = describe(out)
        assert text.startswith("HELD, NOT delivered") and "do not send it again" in text
    finally:
        await a.stop(), await b.stop()


@aio
async def test_tools_and_standing_line(run_dir):
    a, b, sa, sb = await pair(run_dir)
    try:
        assert {"peers", "peer_send"} <= {t.name for t in a.eng._specs()}
        assert "cannot grant approvals" in a.eng.loop.system_prompt
        from exo.core.tools.registry import ToolContext
        from exo.core.types import ToolCall
        res = await a.eng.registry.call(ToolCall(id="c", name="peers", arguments={}), ToolContext(session=sa))
        assert "beta" in res.text() and not res.is_error
        res = await a.eng.registry.call(ToolCall(id="c", name="peer_send", arguments={"to": "nobody", "text": "x"}),
                                        ToolContext(session=sa))
        assert res.is_error and "no peer named" in res.text()
        res = await a.eng.registry.call(ToolCall(id="c", name="peer_send", arguments={"to": "beta", "text": "hi"}),
                                        ToolContext(session=sa))
        assert res.text().startswith("Delivered to beta")
    finally:
        await a.stop(), await b.stop()


def test_peers_protocol_roundtrip():
    cmd = s.Command.of(s.Peers(action="send", to="beta", text="x"))
    back = s.Command.from_json(cmd.to_json())
    assert back.body == s.Peers(action="send", to="beta", text="x")
    ev = s.Event.of(s.PeerDelivery(msg_id="pm_1", state="held", peer="beta"), 1, "s_1")
    assert s.Event.from_json(ev.to_json()).body.state == "held"


@aio
async def test_peer_traffic_shows_in_the_trace(run_dir):
    from exo.core import trace
    a, b, sa, sb = await pair(run_dir, b_peers={"policy": {"same-principal": "hold"}})
    try:
        await a.cmd(s.Peers(action="send", to="beta", text="ping", session=sa, via="agent"))
        txt = trace.peer_text(a.eng.store, sa)
        assert txt.startswith("peers") and "to beta: held" in txt
        assert "peers" in a.eng.trace_text(sa)
    finally:
        await a.stop(), await b.stop()


@aio
@pytest.mark.parametrize("caller,trust", [("local", "same-principal"), ("unknown", "same-principal"),
                                          ("principal:tui", "principal")])
async def test_human_peer_send_needs_a_principal_connection(run_dir, caller, trust):
    """D-039: ``via="human"`` is a body claim. Only a principal connection may make the receiver read the message
    as the principal typing in another client; a local caller's send arrives as same-principal automation."""
    a, b, sa, sb = await pair(run_dir, b_peers={"wake": False})
    try:
        out = await a.cmd(s.Peers(action="send", to="beta", text="ship it", session=sa, via="human"), caller=caller)
        assert out["state"] == "delivered"
        pm = b.events(s.PeerMessage)[0].body
        assert pm.trust == trust
        framed = [m.text() for m in b.eng.store.messages(sb) if "ship it" in m.text()]
        assert framed and ("your principal" in framed[0]) is (trust == "principal")
    finally:
        await a.stop(), await b.stop()
