"""Wave 2 acceptance for exo-gate and exo-bus on the Exo core (docs/STANDALONE.md section 6).

1. exo-gate blocks a public, high-risk tool call until a client answers ``approval.requested``:
   deny in one run (the tool never runs), allow in another (it runs once).
2. A signed bus message becomes a turn on the fake model, and the reply goes back as a signed
   envelope that verifies against the seat's registry key.

Engines are built with ``env={}`` and the fake model, so nothing touches the network.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

FAKE = {"rules": [
    {"match": "announce", "steps": [{"tool": "send_message", "args": {"target": "#general",
                                                                      "message": "we shipped"}}, "announced"]},
    {"match": "hello", "steps": ["hi back from exo"]},
]}
MODELS = {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _engine(tmp_path, plugins=("exo-gate",), sent=None) -> CoreEngine:
    pm = PluginManager(config={"exo-gate": {"principal_store": "", "key_path": str(tmp_path / "none.key")}})
    for name in plugins:
        loaded = pm.load_folder(REPO / "plugins" / name)
        assert loaded.ok, pm.errors()

    def register(ctx):  # a public, high-risk tool that records every run
        ctx.register_tool("send_message", "demo", {"parameters": {"type": "object", "properties": {
            "target": {"type": "string"}, "message": {"type": "string"}}}},
            handler=lambda args, **kw: (sent.append(args["message"]), "sent")[1], risk="high")
    pm._run_register(__import__("exo.core.plugins", fromlist=["Manifest"]).Manifest(name="demo"), "dir", register)
    assert not pm.errors(), pm.errors()
    return CoreEngine(tmp_path / "home", MODELS, plugins=pm, cwd=str(tmp_path), env={})


async def _run_gated_turn(eng: CoreEngine, answer: str) -> list[s.Event]:
    d = Dispatcher(eng)
    sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    await d.run(s.Command.of(s.TurnSend(session=sid, text="announce the release")))
    deadline = time.monotonic() + 10
    answered = False
    while eng._tasks and time.monotonic() < deadline:
        for ev in eng.hub.since(0):
            if isinstance(ev.body, s.ApprovalRequested) and ev.body.request_id in eng._approvals and not answered:
                # the turn is paused here: the tool has not run
                assert ev.body.risk == "high" and ev.body.call.name == "send_message"
                assert eng.hub.since(0) and not [e for e in eng.hub.since(0) if isinstance(e.body, s.ToolCompleted)]
                resp = await d.run(s.Command.of(s.ApprovalRespond(request_id=ev.body.request_id, decision=answer)))
                assert resp.ok
                answered = True
        await asyncio.sleep(0.01)
    assert answered and not eng._tasks
    return eng.hub.since(0)


@pytest.mark.parametrize("answer,ran", [("deny", False), ("allow", True)])
def test_gate_blocks_public_action_until_a_client_answers(tmp_path, answer, ran):
    sent: list[str] = []
    eng = _engine(tmp_path, sent=sent)
    events = asyncio.run(_run_gated_turn(eng, answer))
    types = [e.type for e in events]
    assert types.index("approval.requested") < types.index("approval.resolved") < types.index("tool.completed")
    assert sent == (["we shipped"] if ran else [])
    out = [e.body.result for e in events if isinstance(e.body, s.ToolCompleted)][0]
    assert out.is_error is (not ran)


def test_gate_does_not_ask_for_a_private_read(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    eng = _engine(tmp_path, sent=[])
    hook = eng.plugins.hooks.entries("pre_tool_call")[0].callback  # async: the core awaits it
    gate = lambda **kw: asyncio.run(hook(**kw))  # noqa: E731
    assert gate(tool_name="read_file", args={"path": "notes.txt"}, session_id="s", risk="low") is None
    # an unknown tool the registry calls high risk is an ask even though the classifier cannot place it
    verdict = gate(tool_name="frobnicate", args={}, session_id="s", risk="high")
    assert verdict and verdict["action"] == "approve"
    assert gate(tool_name="frobnicate", args={}, session_id="s", risk="low") is None


def test_bus_round_trip_on_the_real_engine(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from bus.conftest import Bus, write_keys
    from exo.bus import wire
    for k in ("EXO_BUS_URL", "EXO_BUS_SEAT", "DIVAN_URL", "DIVAN_SEAT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    bus = Bus(tmp_path)
    try:
        keys = tmp_path / "seat-keys"
        write_keys(keys, {"beta": bus.keys["beta"]})
        extra = {"url": bus.url, "seat": "beta", "keys_dir": str(keys), "state_dir": str(tmp_path / "state"),
                 "poll_seconds": "0.1"}
        eng = _engine(tmp_path, plugins=("exo-bus",), sent=[])
        entry = eng.plugins.channels["exo_bus"]

        # the channel factory is what a host calls: factory(link, **extra)
        from importlib import import_module
        adapter = import_module(entry.factory.__module__)
        link = adapter.InProcessLink(eng)

        async def run() -> None:
            ch = entry.factory(link, **extra)
            assert await ch.start()
            try:
                bus.post("gamma", "beta", "hello there", thread="t1")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not bus.client("gamma").inbox():
                    await asyncio.sleep(0.05)
            finally:
                await ch.stop()

        asyncio.run(run())
        cli = bus.client("gamma")
        [reply] = cli.inbox()
        assert reply["from"] == "beta" and reply["to"] == ["gamma"] and reply["thread"] == "t1"
        assert reply["body"] == "hi back from exo"
        # signed by beta's registry key, and the whole chain verifies
        reg, head = cli.registry(), cli.head()
        assert cli.signer_of(reply, reg, head.get("relay_pubkey")) == reg["beta"]["pubkey"]
        assert wire.verify_doc(wire.core(reply), reg["beta"]["pubkey"])
        assert cli.verify()["ok"] is True
        # the inbound message became a user turn on a bus session
        sessions = eng.store.list_sessions(10, None)
        assert [i.title for i in sessions] == ["bus:gamma"]
        assert "hello there" in eng.store.messages(sessions[0].id)[0].text()
    finally:
        bus.stop()
