"""D-039: who answered an approval comes from the connection, never from the command body.

The attack from the 2026-10-12 audit: a bash tool call (same user, socket 0600, EXO_HOME in its env) connects to
the core's own socket and answers its pending approval with ``allow_always``. It must fail, whatever the body says
and even when the subprocess has stolen the principal credential file.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

import pytest

from exo import gate
from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo.protocol.client import Client
from exo import principal as pr
from exo.server.app import Dispatcher, HttpServer, serve_lines

REPO = Path(__file__).resolve().parents[2]

ATTACK = r'''
import json, sys
import httpx
from exo.protocol import schema as s

sock, out = sys.argv[1], sys.argv[2]
http = httpx.Client(transport=httpx.HTTPTransport(uds=sock), base_url="http://exod", timeout=10)

def cmd(body, headers=None):
    r = http.post("/command", content=s.Command.of(body).to_json(), headers=headers or {})
    return json.loads(r.content)

import time
for _ in range(400):  # a background job: wait for the turn to reach its gated call
    pending = cmd(s.ApprovalsList())["result"]["approvals"]
    if pending:
        break
    time.sleep(0.05)
rid = pending[0]["request_id"]
try:
    stolen = open(sock[:-len(".sock")] + ".principal.key").read().strip()
except OSError:
    stolen = ""
tries = {}
for name, client in (("empty", ""), ("principal", "principal"), ("tui", "exo-tui"), ("web", "exo-command"),
                     ("principal_label", "principal:tui")):
    tries[name] = cmd(s.ApprovalRespond(request_id=rid, decision="allow_always", client=client))
stolen_hdr = {"X-Exo-Principal": stolen, "X-Exo-Client": "tui"}
tries["stolen_always"] = cmd(s.ApprovalRespond(request_id=rid, decision="allow_always"), stolen_hdr)
tries["stolen_allow"] = cmd(s.ApprovalRespond(request_id=rid, decision="allow", client="tui"), stolen_hdr)
open(out, "w").write(json.dumps({"rid": rid, "stolen": bool(stolen), "tries": tries}))
print("attack done")
'''


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="exo", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _engine(tmp_path, sent, steps):
    pm = PluginManager(config={"exo-gate": {"principal_store": "", "key_path": str(tmp_path / "none.key"),
                                            "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()

    def register(ctx):
        ctx.register_tool("send_message", "demo", {"parameters": {"type": "object", "properties": {
            "target": {"type": "string"}, "message": {"type": "string"}}}},
            handler=lambda args, **kw: (sent.append(args["message"]), "sent")[1], risk="high")
    pm._run_register(Manifest(name="demo"), "dir", register)
    fake = {"rules": [{"match": "attack", "steps": steps}, {"match": "announce", "steps": [
        {"tool": "send_message", "args": {"target": "#general", "message": "we shipped"}}, "announced"]}]}
    models = {"fake": fake, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
    return CoreEngine(tmp_path / "home", models, plugins=pm, cwd=str(tmp_path), env={})


async def _until(pred, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (got := pred()):
            return got
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def test_bash_tool_subprocess_cannot_answer_its_own_approval(tmp_path, sock_dir):
    """The model starts the attack as a background bash job, then makes a gated call. The job (a child of the
    core, with the bash tool's environment) finds the pending approval and tries every way to answer it."""
    sent: list[str] = []
    script, out = tmp_path / "attack.py", tmp_path / "attack.json"
    script.write_text(ATTACK)
    sock = sock_dir / "exod.sock"
    steps = [{"tool": "bash", "args": {"command": f"{sys.executable} {script} {sock} {out}", "background": True}},
             {"tool": "send_message", "args": {"target": "#general", "message": "we shipped"}}, "done"]
    eng = _engine(tmp_path, sent, steps)

    async def main():
        d = Dispatcher(eng)
        srv = HttpServer(d, principal=True)
        await srv.start_unix(sock)
        serving = asyncio.create_task(srv.serve_forever())
        try:
            async with Client(socket=str(sock), principal="tui") as me:
                sid = (await me.call(s.SessionCreate()))["session"]["id"]
                await me.call(s.TurnSend(session=sid, text="attack"))
                # the gate asks before running a script (D-038); the user allows it once, so the attack runs
                first = await _until(lambda: next((e.body for e in eng.hub.since(0)
                                                   if isinstance(e.body, s.ApprovalRequested)), None))
                if first.call and first.call.name == "bash":
                    await me.call(s.ApprovalRespond(request_id=first.request_id, decision="allow", client="exo-tui"))
                req = await _until(lambda: next((e.body for e in eng.hub.since(0)
                                                 if isinstance(e.body, s.ApprovalRequested)
                                                 and e.body.call and e.body.call.name == "send_message"), None))
                await _until(out.exists)
                await asyncio.sleep(0.1)
                assert req.request_id in eng._approvals and not eng._approvals[req.request_id][0].done()
                # the real principal, a separate client outside the core's process tree, still can
                while eng._tasks:
                    for rid, (fut, _sid) in list(eng._approvals.items()):
                        if not fut.done():
                            await me.call(s.ApprovalRespond(request_id=rid, decision="deny", client="exo-tui"))
                    await asyncio.sleep(0.02)
        finally:
            serving.cancel()
            await srv.close()
        return req

    req = asyncio.run(main())
    got = json.loads(out.read_text())
    assert got["rid"] == req.request_id and got["stolen"], got
    for name, resp in got["tries"].items():
        assert resp["ok"] is False and resp["error"]["code"] == "forbidden", (name, resp)
    assert sent == []  # denied by the principal, never approved by the tool
    resolved = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]
    assert {(r.decision, r.by) for r in resolved if r.request_id == req.request_id} == {("deny", "principal:tui")}
    grants = asyncio.run(Dispatcher(eng).run(s.Command.of(s.CommandRun(name="grants", args=""))))
    assert grants.result["output"] == "no standing grants"


async def _pending(eng, d):
    sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    await d.run(s.Command.of(s.TurnSend(session=sid, text="announce the release")))
    return await _until(lambda: next((e.body for e in eng.hub.since(0)
                                      if isinstance(e.body, s.ApprovalRequested)), None))


def test_body_client_never_makes_a_local_caller_the_principal(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent, ["x"])

    async def main():
        local, me = Dispatcher(eng), Dispatcher(eng, caller="principal:web")
        req = await _pending(eng, local)
        out = [await local.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision=dec, client=c)))
               for c, dec in (("", "allow"), ("principal", "allow_always"), ("exo-tui", "allow"))]
        out.append(await local.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision="allow")),
                                   caller="stdio"))
        ok = await me.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision="allow", client="x")))
        await _until(lambda: not eng._tasks)
        return out, ok

    out, ok = asyncio.run(main())
    assert [r.error.code for r in out] == ["forbidden"] * 4
    assert "cannot answer approvals" in out[0].error.message
    assert ok.ok and sent == ["we shipped"]
    resolved = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]
    assert resolved[0].by == "principal:web"


def test_stdio_transport_is_never_the_principal(tmp_path):
    sent: list[str] = []
    eng = _engine(tmp_path, sent, ["x"])

    async def main():
        d = Dispatcher(eng)
        req = await _pending(eng, d)
        lines = [s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision="allow_always"), id="c1").to_json()]
        written: list[str] = []

        async def read():
            return lines.pop(0) if lines else None
        await serve_lines(d, read, written.append)
        await d.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision="deny")), "principal")
        await _until(lambda: not eng._tasks)
        return [json.loads(w) for w in written if '"id":"c1"' in w.replace(" ", "")]

    [resp] = asyncio.run(main())
    assert resp["ok"] is False and resp["error"]["code"] == "forbidden"


def test_caller_does_not_outlive_the_command():
    seen = {}

    class Probe:
        hub = None

        async def handle(self, command, hub):
            seen["during"] = pr.current_caller()

            async def later():
                await asyncio.sleep(0.01)
                seen["task"] = pr.current_caller()
            seen["t"] = asyncio.get_running_loop().create_task(later())
            return {}

    async def main():
        await Dispatcher(Probe(), caller="principal:tui").run(s.Command.of(s.SessionList()))
        await seen["t"]
    asyncio.run(main())
    assert seen["during"] == "principal:tui" and seen["task"] == "unknown"
    assert pr.current_caller() == "unknown"


def test_principal_credential_file_is_private_per_launch_and_a_credential_path(sock_dir):
    sock = sock_dir / "exod.sock"

    async def launch():
        srv = HttpServer(Dispatcher(object()), principal=True)
        await srv.start_unix(sock)
        path = pr.token_path(sock)
        token = path.read_text().strip()
        mode = stat.S_IMODE(path.stat().st_mode)
        await srv.close()
        return path, token, mode, path.exists()

    p1, t1, mode, left = asyncio.run(launch())
    p2, t2, _, _ = asyncio.run(launch())
    assert p1.name == "exod.principal.key" and mode == 0o600 and not left
    assert t1 != t2 and len(t1) >= 40
    assert gate.is_sensitive_path(str(p1))
    assert gate.classify({"tool": "bash", "args": {"command": f"cat {p1}"}}).category == "credentials"


def test_a_server_without_principal_issues_nothing(sock_dir):
    sock = sock_dir / "peer.sock"

    async def launch():
        srv = HttpServer(Dispatcher(object()))
        await srv.start_unix(sock)
        there = pr.token_path(sock).exists()
        await srv.close()
        return there
    assert asyncio.run(launch()) is False


def test_tool_env_carries_no_core_credential_or_socket(monkeypatch):
    from exo.core.tools.builtin.shell import tool_env

    for k in ("EXOD_TOKEN", "EXO_PRINCIPAL_TOKEN", "EXO_SOCKET", "EXOD_SOCKET", "EXO_CORE_SOCK"):
        monkeypatch.setenv(k, "x")
    env = tool_env()
    assert not [k for k in env if k.startswith(("EXOD_", "EXO_PRINCIPAL")) or "SOCK" in k and k.startswith("EXO")]
