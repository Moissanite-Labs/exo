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
@pytest.mark.parametrize("permission_args", ["bypass", "full all"])
def test_transports_override_a_principal_dispatcher(engine, transport, permission_args):
    on = s.CommandRun(name="permissions", args=permission_args)
    async def main():
        d = Dispatcher(engine, caller="principal:tui")
        if transport == "stdio":
            lines, written = [s.Command.of(on, id="1").to_json()], []

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
                return [(await c.post("/command", content=s.Command.of(on).to_json())).json()]
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
    credential = open(sock[:-len('.sock')] + '.principal.key').read().strip()
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


@pytest.mark.parametrize('caller', ['local', 'unknown', 'peer:other', 'bus:fixture', 'seat:helper'])
def test_full_dispatch_requires_live_principal(engine, caller):
    command = s.Command.of(s.CommandRun(name='permissions', args='full s_fixture'))
    result = asyncio.run(Dispatcher(engine, caller=caller).run(command))
    assert not result.ok and result.error.code == 'forbidden'
    assert mode(engine) == 'ask'


def test_full_dispatch_scopes_all_turn_authors_to_one_session_and_local_ask_revokes(engine):
    async def main():
        sid = (await engine.loop.create_session(title='full fixture')).id
        command = s.Command.of(s.CommandRun(name='permissions', args='full ' + sid))
        on = await Dispatcher(engine, caller='principal:tui').run(command)
        assert on.ok and on.result['output'].startswith('permissions: full')
        for author in ('principal:tui', 'job', 'peer:fixture', ''):
            results = await engine.plugins.hooks.afire('pre_tool_call', tool_name='bash',
                args={'command': 'cat ~/.exo/keys/fixture.key'}, session_id=sid, author=author)
            assert results == []  # no tool is executed: only the real registered gate is consulted
        for other in ('s_other', ''):
            results = await engine.plugins.hooks.afire('pre_tool_call', tool_name='bash',
                args={'command': 'cat ~/.exo/keys/fixture.key'}, session_id=other, author='principal:tui')
            assert results[0]['action'] == 'approve'
        off = await Dispatcher(engine, caller='local').run(s.Command.of(s.CommandRun(name='permissions', args='ask')))
        assert off.ok and mode(engine) == 'ask'
        results = await engine.plugins.hooks.afire('pre_tool_call', tool_name='bash',
            args={'command': 'cat ~/.exo/keys/fixture.key'}, session_id=sid, author='principal:tui')
        assert results[0]['action'] == 'approve'
    asyncio.run(main())



def test_full_all_existing_command_protocol_covers_new_and_child_sessions(engine):
    async def main():
        # The existing command.run envelope carries the new argument; no protocol type is added.
        old_wire = json.dumps({'type': 'command.run', 'id': 'full-fixture',
                               'body': {'name': 'permissions', 'args': 'full all'}})
        result = await Dispatcher(engine, caller='principal:tui').run(s.Command.from_json(old_wire))
        decoded = s.Response.from_json(result.to_json())
        assert decoded.ok and decoded.result['output'].startswith('permissions: full for all sessions')
        parent = (await engine.loop.create_session(title='parent')).id
        child = (await engine.loop.create_session(title='child', parent=parent, kind='subagent', agent='fixture')).id
        for sid in (parent, child):
            assert await engine.plugins.hooks.afire('pre_tool_call', tool_name='bash', session_id=sid,
                args={'command': 'cat ~/.exo/keys/fixture.key'}, author='job') == []
        status = await Dispatcher(engine, caller='local').run(s.Command.of(s.CommandRun(name='permissions', args='json')))
        snap = s.Response.from_json(status.to_json()).result['output']
        assert snap['supports_full_all'] and snap['full_session'] == '*' and snap['scope'] == 'core-all-sessions'
        await Dispatcher(engine, caller='local').run(s.Command.of(s.CommandRun(name='permissions', args='ask')))
        assert mode(engine) == 'ask'
    asyncio.run(main())
