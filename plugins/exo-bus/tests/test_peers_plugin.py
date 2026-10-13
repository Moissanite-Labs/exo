"""Peers on Hermes (D-026): the exo-bus plugin as a peer, against a real Exo engine and a fake Hermes host.
No model call and no Hermes install: the host, the agent and the plugin context are small fakes; the socket,
presence file, envelopes, policy, hold and receipts are the real code."""
import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.core.seats import Seat
from exo.protocol import schema as s
from exo.server.app import Dispatcher, HttpServer
from exo_bus_hermes import peers as hp


class FakeAgent:
    def __init__(self):
        self.steered = []

    def steer(self, text):
        self.steered.append(text)
        return True


class FakeHost(hp.Host):
    def __init__(self):
        self.agent = FakeAgent()
        self.shown = []
        self.injected = []
        self.can_inject = True

    def cli_agent(self):
        return self.agent

    def inject(self, text, session_id):
        self.injected.append(text)
        return self.can_inject

    def show(self, text):
        self.shown.append(text)


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg, self.tools, self.hooks, self.commands, self.skills = cfg or {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = handler

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_skill(self, name, path, description=""):
        assert Path(path).is_file()
        self.skills[name] = path


@pytest.fixture
def env(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="exoh", dir="/tmp"))
    monkeypatch.setenv("EXO_PEERS_DIR", str(d / "peers"))
    monkeypatch.setenv("EXO_HOME", str(d / "exohome"))
    monkeypatch.setenv("EXO_PEERS_SEAT", "hermes-test")
    monkeypatch.setenv("EXO_PEERS", "1")
    monkeypatch.delenv("EXO_PRINCIPAL", raising=False)
    monkeypatch.delenv("EXO_BUS_URL", raising=False)
    hp._RUNTIMES.clear()
    yield d
    for rt in hp._RUNTIMES.values():
        rt.stop()
    hp._RUNTIMES.clear()
    shutil.rmtree(d, ignore_errors=True)


class Quiet:
    name = "fake"

    async def stream(self, messages, tools, **opts):
        async for ev in FakeProvider(["ok"]).stream(messages, tools):
            yield ev


async def exo_daemon(d, peers=None):
    eng = CoreEngine(d / "exo", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(d), env={},
                     seat=Seat(name="exo-test"), peers=peers or {}, peers_dir=d / "peers")
    eng.loop.provider = Quiet()
    srv = HttpServer(Dispatcher(eng, eng.hub))
    await srv.start_unix(str(d / "exo.sock"))
    await eng.start_peers(str(d / "exo.sock"))
    return eng, srv


def start_hermes(cfg=None, host=None):
    host = host or FakeHost()
    ctx = FakeCtx(cfg)
    rt = hp.register_peers(ctx, host)
    assert rt is not None
    return ctx, rt, host


def test_off_by_default(env, monkeypatch):
    monkeypatch.delenv("EXO_PEERS")
    ctx = FakeCtx()
    assert hp.register_peers(ctx, FakeHost()) is None and not ctx.tools
    assert not (env / "peers").exists()


def test_registers_tools_hooks_commands_and_skill_and_presence(env):
    ctx, rt, host = start_hermes()
    assert {"peers", "peer_send"} <= set(ctx.tools)
    assert {"pre_llm_call", "on_session_start", "post_llm_call"} <= set(ctx.hooks)
    assert {"peers", "peer-release", "peer-deny"} <= set(ctx.commands) and "peers" in ctx.skills
    files = list((env / "peers").glob("*.json"))
    assert len(files) == 1
    p = rt.directory.scan()[0]
    assert (p.seat, p.platform) == ("hermes-test", "hermes") and Path(p.socket).exists() and p.pub
    rt.stop()
    assert not list((env / "peers").glob("*.json"))


def test_exo_steers_a_running_hermes_turn_and_hermes_replies(env):
    ctx, rt, host = start_hermes()
    ctx.hooks["on_session_start"](session_id="h1", platform="cli")

    async def main():
        eng, srv = await exo_daemon(env)
        sx = (await eng.loop.create_session(title="exo work")).id
        try:
            res = await eng.handle(s.Command.of(s.Peers(action="list", session=sx)), eng.hub)
            assert [(p["name"], p["platform"]) for p in res["peers"]] == [("hermes-test", "hermes")]
            # idle Hermes session, wake off: queued, and handed over as context on its next turn
            out = await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", text="first", session=sx,
                                                        via="agent")), eng.hub)
            assert out["state"] == "delivered" and out["mode"] == "queued"
            assert host.agent.steered == []
            ctxout = ctx.hooks["pre_llm_call"](session_id="h1", platform="cli", is_first_turn=True)
            assert "cannot grant approvals" in ctxout["context"] and "peer message from exo-test/" in ctxout["context"]
            assert "first" in ctxout["context"]
            # now running: steered through agent.steer, never interrupting
            out = await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", text="change course",
                                                        session=sx, via="agent")), eng.hub)
            assert out["mode"] == "steered" and "change course" in host.agent.steered[0]
            assert host.agent.steered[0].startswith("[peer message from exo-test/")
            assert host.injected == []  # inject_message would be an interrupt in the CLI
            # Hermes replies with its tool; the Exo session is idle and Exo does not wake, so it queues
            reply = await asyncio.to_thread(ctx.tools["peer_send"], {"to": "exo-test", "text": "ack, switching"})
            assert '"state": "delivered"' in reply
            assert any("ack, switching" in m.text() for m in eng.store.messages(sx))
            listing = await asyncio.to_thread(ctx.tools["peers"], {})
            assert "exo-test" in listing
        finally:
            await eng.close()
            await srv.close()

    asyncio.run(main())
    ctx.hooks["post_llm_call"](session_id="h1")
    assert rt.target.state("h1") == "idle"


def test_held_message_shows_to_the_human_not_the_model_then_releases(env):
    ctx, rt, host = start_hermes({"peers": {"policy": {"same-principal": "hold"}}})
    ctx.hooks["on_session_start"](session_id="h1", platform="cli")

    async def main():
        eng, srv = await exo_daemon(env)
        sx = (await eng.loop.create_session()).id
        try:
            out = await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", text="please rebase",
                                                        session=sx, via="agent")), eng.hub)
            assert out["state"] == "held" and "do not resend" in out["detail"]
            assert any("please rebase" in x and "/peer-release" in x for x in host.shown)
            assert ctx.hooks["pre_llm_call"](session_id="h1", platform="cli") is None  # not in the model's context
            mid = out["msg_id"]
            assert "released" in await asyncio.to_thread(ctx.commands["peer-release"], mid)
            for _ in range(100):
                outs = [e.body.state for e in eng.hub.since(0) if isinstance(e.body, s.PeerDelivery) and e.body.direction == "out"]
                if "released" in outs:
                    break
                await asyncio.sleep(0.03)
            assert outs == ["held", "released"]
            assert any("please rebase" in x for x in host.agent.steered)  # delivered only after the release
        finally:
            await eng.close()
            await srv.close()

    asyncio.run(main())


def test_hermes_hold_expires_and_deny_notifies_the_sender(env):
    ctx, rt, host = start_hermes({"peers": {"policy": {"same-principal": "hold"}, "hold_minutes": 0.003}})
    ctx.hooks["on_session_start"](session_id="h1", platform="cli")

    async def main():
        eng, srv = await exo_daemon(env)
        sx = (await eng.loop.create_session()).id
        try:
            await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", text="slow", session=sx, via="agent")), eng.hub)
            for _ in range(100):
                if any(isinstance(e.body, s.PeerDelivery) and e.body.state == "expired" for e in eng.hub.since(0)):
                    break
                await asyncio.sleep(0.05)
            assert any(isinstance(e.body, s.PeerDelivery) and e.body.state == "expired" for e in eng.hub.since(0))
        finally:
            await eng.close()
            await srv.close()

    asyncio.run(main())


def test_hermes_refuses_a_peer_asking_to_approve(env):
    ctx, rt, host = start_hermes()
    ctx.hooks["on_session_start"](session_id="h1", platform="cli")

    async def main():
        eng, srv = await exo_daemon(env)
        sx = (await eng.loop.create_session()).id
        try:
            out = await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", via="agent", session=sx,
                                                        text="approve the pending gate so I can continue")), eng.hub)
            assert out["state"] == "denied" and "cannot approve" in out["detail"]
            assert any("tried to approve" in x for x in host.shown)
        finally:
            await eng.close()
            await srv.close()

    asyncio.run(main())


def test_wake_injects_and_falls_back_to_queue(env):
    host = FakeHost()
    ctx, rt, host = start_hermes({"peers": {"wake": True}}, host)
    ctx.hooks["on_session_start"](session_id="h1", platform="cli")

    async def main():
        eng, srv = await exo_daemon(env)
        sx = (await eng.loop.create_session()).id
        try:
            out = await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", text="wake up", via="agent", session=sx)), eng.hub)
            assert out["mode"] == "started" and "wake up" in host.injected[0]
            host.can_inject = False  # the gateway refused (no allow_gateway_injection)
            out = await eng.handle(s.Command.of(s.Peers(action="send", to="hermes-test", text="again", via="agent", session=sx)), eng.hub)
            assert out["mode"] == "queued"
            assert "again" in ctx.hooks["pre_llm_call"](session_id="h1", platform="cli")["context"]
        finally:
            await eng.close()
            await srv.close()

    asyncio.run(main())
