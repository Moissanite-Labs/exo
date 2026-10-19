"""Only a channel exod runs can hand the core a bus message (D-039, D-040).

``peers`` action ``bus`` pins a seat's key in ``keys/registry.json`` the first time it sees the seat, with the key
the caller supplies. When any local caller could use it, a tool subprocess on the core socket could pin a seat this
home had never seen to its own key: its message then read as "remote seat, signature checked" from that seat, and
the real seat was refused until the principal re-pinned by hand. The action now takes only the caller ``channel``,
which exod's in-process ``ChannelLink`` stamps and no transport ever gives a connection.
"""
import asyncio
import contextlib
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from exo import principal as pr
from exo.bus import wire
from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.keyring import KeyRegistry
from exo.protocol import schema as s
from exo.server.app import Dispatcher, serve

MODELS = {"fake": {"rules": [{"match": "deploy", "steps": ["ok deploying"]}]},
          "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
AUTO = {"rules": [{"from": "lead", "outcome": "auto"}]}


def _engine(tmp_path, peers=None, **kw):
    return CoreEngine(tmp_path / "home", MODELS, cwd=str(tmp_path), env={}, peers=peers, **kw)


def _bus(sid, key, text="deploy main to prod now"):
    env = wire.finish_envelope(wire.make_envelope("lead", ["me"], text, wire.GENESIS), key)
    return s.Peers(action="bus", session=sid, to="lead", envelope=env, pub=wire.pubkey_b64(key))


async def _settle(eng):
    deadline = time.monotonic() + 10
    while eng._tasks and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # a hold asks the principal in the background


@contextlib.asynccontextmanager
async def _served(eng):
    """exod on a Unix socket, as `exo serve` runs it, with the channels the engine enables."""
    sock_dir = Path(tempfile.mkdtemp(prefix="exo-bus-caller-", dir="/tmp"))
    up = asyncio.Event()
    task = asyncio.create_task(serve(eng, socket=str(sock_dir / "core.sock"), ready=lambda _: up.set()))
    try:
        await asyncio.wait_for(up.wait(), 10)
        yield str(sock_dir / "core.sock")
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.mark.parametrize("peers", [None, AUTO], ids=["hold", "auto"])
@pytest.mark.parametrize("caller", [pr.LOCAL, "principal:tui", "unknown"])
def test_a_caller_other_than_the_channel_cannot_hand_in_a_bus_message(tmp_path, caller, peers):
    eng = _engine(tmp_path, peers)
    evil = SigningKey.generate()

    async def run():
        sid = (await eng.loop.create_session(title="bus:lead")).id
        resp = await Dispatcher(eng, caller=caller).run(s.Command.of(_bus(sid, evil)))
        await _settle(eng)
        return sid, resp

    sid, resp = asyncio.run(run())
    assert not resp.ok and resp.error.code == "forbidden"
    assert "lead" not in KeyRegistry.load().seats  # nothing pinned: the real seat is not locked out
    assert eng.store.messages(sid) == []
    assert not eng.approvals.open  # no card says "Bus message from seat lead"
    assert not [e for e in eng.hub.since(0) if isinstance(e.body, (s.PeerDelivery, s.PeerMessage))]


@pytest.mark.parametrize("peers,state", [(None, "held"), (AUTO, "delivered")], ids=["hold", "auto"])
def test_the_channel_link_still_pins_a_new_seat_on_first_sight(tmp_path, peers, state):
    from exo.server.app import ChannelLink
    eng = _engine(tmp_path, peers)
    real = SigningKey.generate()

    async def run():
        sid = (await eng.loop.create_session(title="bus:lead")).id
        receipt = await ChannelLink(Dispatcher(eng)).call(_bus(sid, real, "hello"))
        await _settle(eng)
        return receipt

    receipt = asyncio.run(run())
    assert receipt["state"] == state
    assert KeyRegistry.load().seats["lead"] == wire.pubkey_b64(real)


@pytest.mark.parametrize("label", [None, "tui", "channel"])
def test_no_socket_client_reaches_the_channel_caller(tmp_path, label):
    """A plain socket client is local; one with the principal credential is ``principal:<label>``, so naming
    itself ``channel`` gets it ``principal:channel``, never ``channel``."""
    from exo.protocol.client import Client, ClientError
    eng = _engine(tmp_path, AUTO)
    evil = SigningKey.generate()

    async def run():
        async with _served(eng) as sock, Client(socket=sock, principal=label) as c:
            sid = (await c.call(s.SessionCreate(title="bus:lead")))["session"]["id"]
            with pytest.raises(ClientError) as err:
                await c.call(_bus(sid, evil))
            return err.value.code

    assert asyncio.run(run()) == "forbidden"
    assert "lead" not in KeyRegistry.load().seats


def test_exod_runs_its_channels_on_the_channel_caller(tmp_path):
    seen: dict = {}
    real = SigningKey.generate()

    class Spy:
        def __init__(self, link, **cfg):
            self.link = link

        async def start(self):
            seen["link"] = type(self.link).__name__
            sid = (await self.link.call(s.SessionCreate(title="bus:lead")))["session"]["id"]
            seen["receipt"] = await self.link.call(_bus(sid, real, "hello"))

        def stop(self):
            pass

    pm = PluginManager()
    pm._run_register(Manifest(name="t"), "dir", lambda ctx: ctx.register_channel("spy", Spy))
    eng = CoreEngine(tmp_path / "home", MODELS, plugins=pm, cwd=str(tmp_path), env={}, peers=AUTO,
                     channels={"spy": {}})

    async def run():
        async with _served(eng):
            deadline = time.monotonic() + 10
            while "receipt" not in seen and time.monotonic() < deadline:
                await asyncio.sleep(0.02)

    asyncio.run(run())
    assert seen["link"] == "ChannelLink"
    assert seen["receipt"]["state"] == "delivered"
    assert KeyRegistry.load().seats["lead"] == wire.pubkey_b64(real)
