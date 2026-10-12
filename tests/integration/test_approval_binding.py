"""D-033 on the real engine: who may answer an approval, and answers bound to the exact text."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher

REPO = Path(__file__).resolve().parents[2]
FAKE = {"rules": [{"match": "announce", "steps": [
    {"tool": "send_message", "args": {"target": "#general", "message": "we shipped"}}, "announced"]}]}
MODELS = {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
APPROVALS = 'approvers:\n  message_send: [principal, "seat:saga-divan"]\n'


def _engine(tmp_path, sent, store=""):
    pm = PluginManager(config={"exo-gate": {"principal_store": str(store), "key_path": str(tmp_path / "none.key"),
                                            "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()

    def register(ctx):
        ctx.register_tool("send_message", "demo", {"parameters": {"type": "object", "properties": {
            "target": {"type": "string"}, "message": {"type": "string"}}}},
            handler=lambda args, **kw: (sent.append(args["message"]), "sent")[1], risk="high")
    pm._run_register(Manifest(name="demo"), "dir", register)
    return CoreEngine(tmp_path / "home", MODELS, plugins=pm, cwd=str(tmp_path), env={})


async def _turn(eng, answers):
    """Run one gated turn. ``answers`` is a list of ApprovalRespond kwargs tried in order on the first
    request; returns (request, responses)."""
    d = Dispatcher(eng)
    sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    await d.run(s.Command.of(s.TurnSend(session=sid, text="announce the release")))
    deadline, req, out = time.monotonic() + 10, None, []
    while eng._tasks and time.monotonic() < deadline:
        for ev in eng.hub.since(0):
            if isinstance(ev.body, s.ApprovalRequested) and req is None:
                req = ev.body
                for kw in answers:
                    out.append(await d.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, **kw))))
        await asyncio.sleep(0.01)
    assert req is not None and not eng._tasks
    return req, out


def test_default_table_lets_only_the_principal_answer(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent)
    req, out = asyncio.run(_turn(eng, [{"decision": "allow", "client": "seat:saga-divan"},
                                       {"decision": "allow", "client": "peer:x"},
                                       {"decision": "allow", "client": "tui"}]))
    assert req.approvers == ["principal"] and len(req.binding) == 64
    assert [r.ok for r in out] == [False, False, True]
    assert out[0].error.code == "forbidden" and "seat:saga-divan may not answer" in out[0].error.message
    assert sent == ["we shipped"]


def test_a_named_seat_may_answer_and_others_may_not(tmp_path):
    store = tmp_path / "store"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "approvals.yaml").write_text(APPROVALS)
    sent: list[str] = []
    eng = _engine(tmp_path, sent, store)
    req, out = asyncio.run(_turn(eng, [{"decision": "allow", "client": "seat:builder"},
                                       {"decision": "allow", "client": "seat:saga-divan"}]))
    assert req.approvers == ["principal", "seat:saga-divan"]
    assert [r.ok for r in out] == [False, True] and sent == ["we shipped"]
    resolved = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]
    assert resolved[0].by == "seat:saga-divan"


def test_an_answer_for_other_text_is_refused(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent)
    req, out = asyncio.run(_turn(eng, [{"decision": "allow", "binding": "0" * 64},
                                       {"decision": "deny", "binding": "x"},
                                       {"decision": "allow", "binding": ""}]))
    assert out[0].error.code == "stale" and out[1].error.code == "stale"
    assert out[2].ok and sent == ["we shipped"]  # an answer that names no binding keeps the old rule


def test_matching_binding_is_accepted_and_always_is_kept_by_the_gate(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent)
    holder: dict = {}

    async def run():
        d = Dispatcher(eng)
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        await d.run(s.Command.of(s.TurnSend(session=sid, text="announce the release")))
        deadline = time.monotonic() + 10
        while eng._tasks and time.monotonic() < deadline:
            for ev in eng.hub.since(0):
                if isinstance(ev.body, s.ApprovalRequested) and "req" not in holder:
                    holder["req"] = ev.body
                    holder["resp"] = await d.run(s.Command.of(s.ApprovalRespond(
                        request_id=ev.body.request_id, decision="allow_always", binding=ev.body.binding)))
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert holder["resp"].ok and sent == ["we shipped"]
    assert not eng._always  # no unscoped in-memory allow: the gate keeps a text-bound grant instead
    grants = json.loads((Path(os.environ["EXO_HOME"]) / "state" / "gate-grants.json").read_text())
    assert grants[0]["rule"] == "message_send" and grants[0]["binding"] == holder["req"].binding
