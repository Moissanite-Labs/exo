"""Permissions bypass (D-065) through the real core: only a principal connection turns it on, whatever the path."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from exo import principal as pr
from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher, HttpServer, serve_lines
from exo.server.engine import CommandFailed

REPO = Path(__file__).resolve().parents[2]
MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"reply": "ok"}}
# The review rig on #366: a rule fake that answers PROBE with a call the gate asks about, then a plain reply.
PROBE_MODELS = {"fake": {"rules": [{"match": "PROBE", "steps": [
    {"tool": "bash", "args": {"command": "rm -rf build"}}, "done"]}]},
    "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
ON = s.CommandRun(name="permissions", args="bypass")


def build(tmp_path, store):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    pm = PluginManager(config={"exo-gate": {"principal_store": str(store.root), "key_path": str(tmp_path / "gate.key"),
                                            "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    return CoreEngine(home, MODELS, plugins=pm, cwd=str(tmp_path), env={})


@pytest.fixture
def engine(tmp_path, template_store):
    eng = build(tmp_path, template_store)
    yield eng
    asyncio.run(eng.close())


def mode(eng):
    return eng.plugins.commands["permissions"].handler("json")["mode"]


def risky(eng, author="principal:tui"):
    return asyncio.run(eng.plugins.hooks.afire("pre_tool_call", tool_name="bash", args={"command": "rm -rf build"},
                                               author=author))  # the core sends who started the turn (#598)


@pytest.mark.parametrize("caller", ["local", "unknown", "seat:helper", "peer:other", "bus:scout"])
def test_nonprincipal_dispatch_cannot_turn_bypass_on(engine, caller):
    result = asyncio.run(Dispatcher(engine, caller=caller).run(s.Command.of(ON)))
    assert not result.ok and result.error.code == "forbidden"
    assert mode(engine) == "ask" and risky(engine)[0]["action"] == "approve"


def test_principal_turns_it_on_and_the_gate_stops_asking(engine):
    result = asyncio.run(Dispatcher(engine, caller="principal:tui").run(s.Command.of(ON)))
    assert result.ok and result.result["output"].startswith("permissions: bypass")
    assert mode(engine) == "bypass" and risky(engine) == []


@pytest.mark.parametrize("transport", ["tcp", "stdio", "unix"])
def test_transports_override_a_principal_dispatcher(engine, transport):
    async def main():
        d = Dispatcher(engine, caller="principal:tui")
        if transport == "stdio":
            lines, written = [s.Command.of(ON, id="1").to_json()], []

            async def read():
                return lines.pop(0) if lines else None
            await serve_lines(d, read, written.append)
            return [json.loads(line) for line in written if "ok" in json.loads(line)]
        import httpx
        srv = HttpServer(d, token="test-token", principal=True)
        sock_dir = None
        if transport == "unix":
            sock_dir = Path(tempfile.mkdtemp(prefix="exo-bypass-", dir="/tmp"))
            await srv.start_unix(sock_dir / "core.sock")
            client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(sock_dir / "core.sock")),
                                       base_url="http://exod")
        else:
            port = await srv.start_tcp("127.0.0.1", 0)
            client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
        try:
            client.headers.update({"Authorization": "Bearer test-token", "X-Exo-Principal": "principal",
                                   "X-Exo-Client": "tui"})
            async with client as c:
                return [(await c.post("/command", content=s.Command.of(ON).to_json())).json()]
        finally:
            await srv.close()
            if sock_dir:
                shutil.rmtree(sock_dir)
    [r] = asyncio.run(main())
    assert not r["ok"] and r["error"]["code"] == "forbidden"
    assert mode(engine) == "ask"


def test_a_tool_process_with_the_stolen_credential_cannot_turn_it_on(engine):
    script = r"""
import json, sys
import httpx
from exo.protocol import schema as s
sock = sys.argv[1]
with httpx.Client(transport=httpx.HTTPTransport(uds=sock), base_url="http://exod") as c:
    credential = json.load(open(sock[:-len('.sock')] + '.principal.key'))['token']
    headers = {"X-Exo-Principal": credential, "X-Exo-Client": "tui"}
    body = s.CommandRun(name="permissions", args="bypass")
    print(json.dumps(c.post("/command", content=s.Command.of(body).to_json(), headers=headers).json()))
"""

    async def main():
        sock_dir = Path(tempfile.mkdtemp(prefix="exo-child-", dir="/tmp"))
        sock = sock_dir / "core.sock"
        srv = HttpServer(Dispatcher(engine), principal=True)
        await srv.start_unix(sock)
        try:
            proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(sock),
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                       env={"EXO_HOME": str(engine.home), "EXO_NO_KEYCHAIN": "1",
                                                            "EXO_NO_SECRET_SERVICE": "1"})
            out, err = await asyncio.wait_for(proc.communicate(), 20)
            assert proc.returncode == 0, err.decode()
            return json.loads(out)
        finally:
            await srv.close()
            shutil.rmtree(sock_dir)
    r = asyncio.run(main())
    assert not r["ok"] and r["error"]["code"] == "forbidden"
    assert mode(engine) == "ask"


class DetachedToolEscaped(AssertionError):
    """Only this observed authorization failure is an expected sandbox gap."""


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="Unix fork and peer credentials")
@pytest.mark.xfail(strict=True, raises=DetachedToolEscaped,
                   reason="#31: same-user detached tools are not yet sandboxed")
def test_detached_tool_cannot_use_fixture_principal_credential(engine):
    """The credential, socket, and policy state all belong to this disposable test engine."""
    script = r"""
import json, os, sys, time
sock = sys.argv[1]
if os.fork():
    os._exit(0)
os.setsid()
if os.fork():
    os._exit(0)
try:
    deadline = time.monotonic() + 3
    while os.getppid() != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    if os.getppid() != 1:
        raise RuntimeError("detach did not reach pid 1; no authorization conclusion")
    import httpx
    from exo.protocol import schema as s
    credential = json.load(open(sock[:-len('.sock')] + '.principal.key'))['token']
    with httpx.Client(transport=httpx.HTTPTransport(uds=sock), base_url="http://exod", timeout=3) as c:
        body = s.CommandRun(name="permissions", args="bypass")
        response = c.post("/command", content=s.Command.of(body).to_json(),
                          headers={"X-Exo-Principal": credential, "X-Exo-Client": "test-detached"})
        print(json.dumps({"detached": True, "response": response.json()}), flush=True)
except BaseException as exc:
    print(json.dumps({"probe_error": type(exc).__name__}), flush=True)
finally:
    os._exit(0)
"""

    async def probe():
        # A short path is needed for macOS sockaddr_un. Never use an installed core socket.
        with tempfile.TemporaryDirectory(prefix="exo-detach-", dir="/tmp") as directory:
            sock = Path(directory) / "core.sock"
            srv = HttpServer(Dispatcher(engine), principal=True)
            await srv.start_unix(sock)
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", script, str(sock), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={"EXO_HOME": str(engine.home), "EXO_NO_KEYCHAIN": "1", "EXO_NO_SECRET_SERVICE": "1",
                         "PYTHONPATH": str(REPO / "src")})
                out, err = await asyncio.wait_for(proc.communicate(), 15)
                assert proc.returncode == 0, err.decode()
                result = json.loads(out)
                assert result.get("detached") is True, result
                return result["response"]
            finally:
                await srv.close()

    response = asyncio.run(probe())
    if response.get("ok") and mode(engine) == "bypass":
        raise DetachedToolEscaped("detached fixture tool enabled permissions bypass")
    assert not response["ok"] and response["error"]["code"] == "forbidden", response
    assert mode(engine) == "ask"


@pytest.mark.parametrize("author", ["bus:scout", "peer:other", "local", "agent"])
def test_a_message_saying_permissions_bypass_is_only_text(engine, author):
    async def main():
        sid = (await engine.loop.create_session(title="t")).id
        engine.start_turn(sid, "/permissions bypass", {"author": author})
        await asyncio.gather(*list(engine._tasks))
    asyncio.run(main())
    assert mode(engine) == "ask"


def test_work_started_by_a_principal_command_does_not_keep_its_authority(engine):
    """The agent's turn runs in a task the principal's command started; once that command returns, the task is
    nobody. A tool the agent runs inside the turn cannot reach the switch with the principal's standing."""
    async def main():
        results = []

        class Probe:
            hub = None

            async def handle(self, command, hub):
                async def later():
                    await asyncio.sleep(0)
                    try:
                        engine.plugins.commands["permissions"].handler("bypass")
                        results.append("on")
                    except CommandFailed as exc:
                        results.append(exc.code)
                task = asyncio.create_task(later())
                return {"task": task}

        out = await Dispatcher(Probe(), caller="principal:tui").run(s.Command.of(s.SessionList()))
        await out.result["task"]
        return results
    assert asyncio.run(main()) == ["forbidden"]
    assert pr.current_caller() == "unknown" and mode(engine) == "ask"


def test_bypass_does_not_survive_a_core_restart(tmp_path, template_store):
    eng = build(tmp_path, template_store)
    assert asyncio.run(Dispatcher(eng, caller="principal:tui").run(s.Command.of(ON))).ok
    assert mode(eng) == "bypass"
    asyncio.run(eng.close())
    eng2 = build(tmp_path, template_store)
    try:
        assert mode(eng2) == "ask" and risky(eng2)[0]["action"] == "approve"
    finally:
        asyncio.run(eng2.close())


def probe_engine(tmp_path, store):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    pm = PluginManager(config={"exo-gate": {"principal_store": str(store.root), "key_path": str(tmp_path / "gate.key"),
                                            "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    return CoreEngine(home, PROBE_MODELS, plugins=pm, cwd=str(tmp_path), env={})


def cards(eng, since):
    return [e.body for e in eng.hub.since(since) if isinstance(e.body, s.ApprovalRequested)]


async def probe_turn(eng, author):
    """A PROBE turn ``author`` starts; cards it opens are denied by the principal, so the turn ends. Returns
    (cards opened, calls the bypass auto-approved) (#598)."""
    sid = (await eng.loop.create_session(title="t")).id
    mark = eng.hub.since(0)[-1].seq if eng.hub.since(0) else 0
    eng.start_turn(sid, "PROBE", {"author": author})
    async with asyncio.timeout(30):
        while eng._tasks:  # the turn ends when its card is answered; without bypass there is none
            for p in list(eng.approvals.open.values()):
                tok = pr.CALLER.set(pr.Caller("principal:tui"))
                try:
                    eng.approvals.respond(s.ApprovalRespond(request_id=p.request.request_id, decision="deny"))
                except CommandFailed:
                    pass
                finally:
                    pr.CALLER.reset(tok)
            await asyncio.sleep(0.01)
    auto = eng.plugins.commands["permissions"].handler("json")["auto_approved"]
    return cards(eng, mark), auto


@pytest.mark.parametrize("author", ["principal:tui", "local", "peer:mallory", "bus:scout", "job"])
def test_bypass_lowers_only_turns_the_principal_started(tmp_path, template_store, author):
    """#598: bypass consents to skip asking in the principal's turns. A turn a local caller, a peer, a bus wake
    or a job notice started in the same core asks as if bypass were off."""
    eng = probe_engine(tmp_path, template_store)
    try:
        assert asyncio.run(Dispatcher(eng, caller="principal:tui").run(s.Command.of(ON))).ok
        opened, auto = asyncio.run(probe_turn(eng, author))
        if pr.is_principal(author):
            assert not opened and auto == 1
        else:
            assert len(opened) == 1 and auto == 0
    finally:
        asyncio.run(eng.close())
