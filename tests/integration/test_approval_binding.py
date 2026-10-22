"""D-033 on the real engine: who may answer an approval, and answers bound to the exact text."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from exo import crypto
from exo.core.approvals import sign_approval
from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher

REPO = Path(__file__).resolve().parents[2]
FAKE = {"rules": [{"match": "announce", "steps": [
    {"tool": "send_message", "args": {"target": "#general", "message": "we shipped"}}, "announced"]}]}
MODELS = {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
APPROVALS = 'approvers:\n  message_send: [principal, "seat:saga-divan"]\n'


def _engine(tmp_path, sent, store="", models=MODELS):
    pm = PluginManager(config={"exo-gate": {"principal_store": str(store), "key_path": str(tmp_path / "none.key"),
                                            "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()

    def register(ctx):
        ctx.register_tool("send_message", "demo", {"parameters": {"type": "object", "properties": {
            "target": {"type": "string"}, "message": {"type": "string"}}}},
            handler=lambda args, **kw: (sent.append(args["message"]), "sent")[1], risk="high")
        # A shell tool by name, so the gate reads its command; it only records the line, never runs it.
        ctx.register_tool("run_command", "demo", {"parameters": {"type": "object", "properties": {
            "command": {"type": "string"}}}},
            handler=lambda args, **kw: (sent.append(args["command"]), "ran")[1], risk="high")
    pm._run_register(Manifest(name="demo"), "dir", register)
    return CoreEngine(tmp_path / "home", models, plugins=pm, cwd=str(tmp_path), env={})


async def _turn(eng, answers, text="announce the release"):
    """Run one gated turn. ``answers`` is a list of ApprovalRespond kwargs tried in order on the first
    request; returns (request, responses). The dispatcher is a local connection (D-039); an answer with
    ``caller="principal:tui"`` comes from a principal connection."""
    d = Dispatcher(eng)
    sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    deadline, req, out = time.monotonic() + 10, None, []
    while eng._tasks and time.monotonic() < deadline:
        for ev in eng.hub.since(0):
            if isinstance(ev.body, s.ApprovalRequested) and req is None:
                req = ev.body
                for kw in answers:
                    kw = dict(kw(req) if callable(kw) else kw)
                    caller = kw.pop("caller", None)
                    out.append(await d.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, **kw)), caller))
        await asyncio.sleep(0.01)
    assert req is not None and not eng._tasks
    return req, out


def test_default_table_lets_only_the_principal_answer(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent)
    req, out = asyncio.run(_turn(eng, [{"decision": "allow", "client": "seat:saga-divan"},
                                       {"decision": "allow", "client": "peer:x"},
                                       {"decision": "allow", "client": "tui"},
                                       {"decision": "allow", "client": "tui", "caller": "principal:tui"}]))
    assert req.approvers == ["principal"] and len(req.binding) == 64
    assert [r.ok for r in out] == [False, False, False, True]
    assert "cannot answer approvals" in out[2].error.message  # D-039: a body that says tui is not the principal
    assert out[0].error.code == "forbidden" and "seat:saga-divan may not answer" in out[0].error.message
    assert sent == ["we shipped"]


def _seat_key(name):
    key = crypto.keygen()
    crypto.save_key(key, Path(os.environ["EXO_HOME"]) / "keys" / f"{name}.key")
    return key


def _signed(key, seat, decision, **over):
    def make(req):
        sig = sign_approval(key, over.get("request_id", req.request_id), over.get("sign_decision", decision),
                            req.binding, seat)
        return {"decision": decision, "client": f"seat:{seat}", "sig": sig}
    return make


def test_a_named_seat_may_answer_signed_and_others_may_not(tmp_path):
    store = tmp_path / "store"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "approvals.yaml").write_text(APPROVALS)
    sent: list[str] = []
    eng = _engine(tmp_path, sent, store)
    divan, builder = _seat_key("saga-divan"), _seat_key("builder")
    req, out = asyncio.run(_turn(eng, [
        _signed(builder, "builder", "allow"),                           # signed, but not on the list
        {"decision": "allow", "client": "seat:saga-divan"},              # on the list, unsigned
        _signed(builder, "saga-divan", "allow"),                         # signed with the wrong key
        _signed(divan, "saga-divan", "allow", sign_decision="deny"),     # signature over another decision
        _signed(divan, "saga-divan", "allow", request_id="a_other"),     # signature for another request
        _signed(divan, "saga-divan", "allow_always"),                    # a seat cannot give a standing grant
        _signed(divan, "saga-divan", "allow")]))
    assert req.approvers == ["principal", "seat:saga-divan"]
    assert [r.ok for r in out] == [False, False, False, False, False, False, True], [r.error for r in out]
    assert "may not answer" in out[0].error.message and "must sign" in out[1].error.message
    assert "standing grant" in out[5].error.message
    assert sent == ["we shipped"]
    resolved = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]
    assert resolved[0].by == "seat:saga-divan"


def test_grants_command_lists_and_revokes(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent)
    asyncio.run(_turn(eng, [{"decision": "allow_always", "caller": "principal:tui"}]))

    async def cmd(args):
        r = await Dispatcher(eng).run(s.Command.of(s.CommandRun(name="grants", args=args)))
        assert r.ok, r.error
        return r.result["output"]

    listing = asyncio.run(cmd(""))
    gid = listing.split()[0]
    assert gid.startswith("g_") and "message_send on #general" in listing and "by principal:tui" in listing
    assert asyncio.run(cmd(f"revoke {gid}")) == f"revoked {gid}"
    assert asyncio.run(cmd("list")) == "no standing grants"
    assert asyncio.run(cmd("revoke g_nope")) == "no grant g_nope"


def test_an_answer_for_other_text_is_refused(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent)
    me = "principal:tui"
    req, out = asyncio.run(_turn(eng, [{"decision": "allow", "binding": "0" * 64, "caller": me},
                                       {"decision": "deny", "binding": "x", "caller": me},
                                       {"decision": "allow", "binding": "", "caller": me}]))
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
                        request_id=ev.body.request_id, decision="allow_always", binding=ev.body.binding)),
                        "principal:tui")
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert holder["resp"].ok and sent == ["we shipped"]
    assert not eng.approvals.always  # no unscoped in-memory allow: the gate keeps a text-bound grant instead
    grants = json.loads((Path(os.environ["EXO_HOME"]) / "state" / "gate-grants.json").read_text())
    assert grants[0]["rule"] == "message_send" and grants[0]["binding"] == holder["req"].binding


@pytest.mark.parametrize("line, seat_answers", [
    ("env; echo x > ~/.exo/policies/gates.yaml", False),                      # credentials, with a store write
    ("cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml", False),
    ("sudo sh -c 'env; echo x > ~/.exo/policies/gates.yaml'", False),        # behind wrappers
    ("timeout 5 bash -c 'cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml'", False),
    ("env FOO=1 sh -c 'env; echo x > ~/.exo/policies/gates.yaml'", False),
    ("(cat ~/.kube/config && cp evil.yaml ~/.exo/policies/default.yaml)", False),
    ('echo "$(env)" > ~/.exo/policies/gates.yaml', False),
    ("bash <<'EOF'\nenv\necho x > ~/.exo/policies/gates.yaml\nEOF", False),
    ("env", True),                                                            # credentials alone
    ("sudo cat ~/.kube/config", True),
])
def test_a_seat_delegated_credentials_may_not_answer_a_store_write_on_the_line(tmp_path, line, seat_answers):
    """#447, D-038: a secret read leads the line, but the store write beside it keeps the answer with the principal,
    also behind a wrapper, in a subshell, a $( ) or a heredoc. A secret read alone keeps the delegation."""
    store = tmp_path / "store"
    (store / "policies").mkdir(parents=True)
    (store / "policies" / "approvals.yaml").write_text('approvers:\n  credentials: [principal, "seat:builder"]\n')
    models = {"fake": {"rules": [{"match": "run it", "steps": [{"tool": "run_command", "args": {"command": line}},
                                                               "done"]}]},
              "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
    ran: list[str] = []
    eng = _engine(tmp_path, ran, store, models)
    builder = _seat_key("builder")
    req, out = asyncio.run(_turn(eng, [_signed(builder, "builder", "allow"),
                                       {"decision": "allow", "caller": "principal:tui"}], text="run it"))
    assert req.options == ["allow", "deny"]                                   # no standing grant on offer
    if seat_answers:
        assert req.approvers == ["principal", "seat:builder"]
        assert out[0].ok and not out[1].ok, [r.error for r in out]
    else:
        assert req.approvers == ["principal"]
        assert not out[0].ok and "seat:builder may not answer" in out[0].error.message
        assert out[1].ok, out[1].error
    assert ran == [line]
