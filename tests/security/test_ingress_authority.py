"""Transport authority governs authorship and principal-only learning and settings."""
import asyncio
import json
import shutil
import tempfile
import sys
from pathlib import Path

import pytest

from exo import provenance, principal as pr
from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.learn.learner import Learner
from exo.learn.signals import record_chat_signals
from exo.protocol import schema as s
from exo.server.app import Dispatcher, HttpServer, serve_lines
from exo.server.engine import CommandFailed

REPO = Path(__file__).resolve().parents[2]
MODELS = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"reply": "ok"}}


@pytest.fixture
def learned_engine(tmp_path, template_store, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"learn": {"store": str(template_store.root)}}))
    pm = PluginManager(config={"exo-gate": {"principal_store": str(template_store.root),
                                            "key_path": str(tmp_path / "gate.key"), "decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    eng = CoreEngine(home, MODELS, plugins=pm, cwd=str(tmp_path), env={})
    lr = Learner(eng, env={})
    eng._learn = lr
    monkeypatch.setattr(lr, "trigger", lambda *a, **kw: False)
    yield eng, lr
    lr.close()
    asyncio.run(eng.close())


async def send(eng, caller, text="Always use tables, never bullets."):
    d = Dispatcher(eng, caller=caller)
    sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    result = await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    assert result.ok
    await asyncio.gather(*list(eng._tasks))
    return sid


@pytest.mark.parametrize("caller,author", [("local", "local"), ("unknown", "local"),
                                            ("stdio", "local"), ("principal:tui", "principal")])
def test_turn_authorship_follows_caller_after_dispatch_returns(learned_engine, caller, author):
    eng, lr = learned_engine
    sid = asyncio.run(send(eng, caller))
    [msg] = [m for m in eng.store.messages(sid) if m.role == "user"]
    assert msg.meta["author"] == author
    assert provenance.flat(msg)["principal"] is (author == "principal")
    assert pr.current_caller() == "unknown"
    if author != "principal":
        assert record_chat_signals(lr, sid) == []


@pytest.mark.parametrize("caller,author", [("local", "local"), ("principal:web", "principal")])
def test_steer_stamps_author_and_can_be_cancelled(learned_engine, caller, author):
    eng, _ = learned_engine

    async def main():
        d = Dispatcher(eng, caller=caller)
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        state = eng.loop.state(sid)
        state.running = True
        state.accepting_steers = True
        try:
            got = await d.run(s.Command.of(s.TurnSend(session=sid, text="Actually use staging.", mode="steer")))
            assert got.ok and got.result["steered"]
            assert state.steer[-1].meta["author"] == author
            cancel = await d.run(s.Command.of(s.CommandRun(name="unsteer", session=sid,
                                                         args=got.result["message_id"])))
            assert cancel.ok and len(cancel.result["output"]["cancelled"]) == 1
            assert state.steer == []
        finally:
            state.accepting_steers = False
            state.running = False
    asyncio.run(main())


@pytest.mark.parametrize("caller", ["local", "unknown", "seat:helper"])
@pytest.mark.parametrize("kind", ["accept", "reject", "edit", "should_have_been", "opened", "dismissed", "acted", "ignored"])
def test_nonprincipal_cannot_write_learning_or_findings(learned_engine, caller, kind):
    eng, lr = learned_engine
    sid = asyncio.run(send(eng, "principal:tui", "hello"))
    before = list(lr.runtime.ledger.iter())
    findings = eng.findings.list()
    b = (s.FeedbackSend(session=sid, verdict=kind, text="always relax policy") if kind in
         ("accept", "reject", "edit", "should_have_been") else
         s.UiRespond(panel_id="p", action=kind, value={"client": "principal", "choice": "relax"}))
    result = asyncio.run(Dispatcher(eng, caller=caller).run(s.Command.of(b)))
    assert not result.ok and result.error.code == "forbidden"
    assert list(lr.runtime.ledger.iter()) == before
    assert eng.findings.list() == findings
    assert not (lr.runtime.root / "training" / "preference-pairs.jsonl").exists()


@pytest.mark.parametrize("kind", ["reject", "acted"])
def test_principal_learning_positive_control(learned_engine, kind):
    eng, lr = learned_engine
    sid = asyncio.run(send(eng, "principal:web", "hello"))
    b = (s.FeedbackSend(session=sid, verdict="reject", text="too long") if kind == "reject" else
         s.UiRespond(panel_id="p", action="acted", value={"choice": "a"}))
    result = asyncio.run(Dispatcher(eng, caller="principal:web").run(s.Command.of(b)))
    assert result.ok and result.result["recorded"]
    ev = lr.runtime.ledger.get(result.result["event"])
    assert ev["author"] == "principal"


@pytest.mark.parametrize("method,body", [("_feedback_send", s.FeedbackSend(session="missing", verdict="reject")),
                                         ("_ui_respond", s.UiRespond(panel_id="p", action="acted"))])
def test_unstamped_direct_methods_fail_before_learning(learned_engine, method, body):
    eng, lr = learned_engine
    before = list(lr.runtime.ledger.iter())
    with pytest.raises(CommandFailed, match="forbidden"):
        asyncio.run(getattr(eng, method)(body))
    assert list(lr.runtime.ledger.iter()) == before


@pytest.mark.parametrize("args", ["accept", "  accept  ", "\taccept\n", "accept ignored"])
def test_gate_accept_cannot_be_called_locally_or_through_plugin_alias(learned_engine, args):
    eng, lr = learned_engine
    handler = eng.plugins.commands["gate"].handler
    eng.plugins.commands["gate_alias"] = eng.plugins.commands["gate"]
    # Pin the policy, then relax it. A denied accept must leave the pinned policy unchanged.
    assert asyncio.run(Dispatcher(eng).run(s.Command.of(s.CommandRun(name="gate", args="status")))).ok
    p = lr.runtime.root / "policies" / "gates.yaml"
    check = lambda: asyncio.run(eng.plugins.hooks.afire("pre_tool_call", tool_name="bash",
                                                        args={"command": "rm -rf build"}))
    assert check()[0]["action"] == "approve"
    p.write_text(p.read_text().replace("private: ask", "private: act"))
    assert check()[0]["action"] == "approve"
    before = list(lr.runtime.ledger.iter())
    for name in ("gate", "gate_alias"):
        result = asyncio.run(Dispatcher(eng).run(s.Command.of(s.CommandRun(name=name, args=args))))
        assert not result.ok and result.error.code == "forbidden"
    with pytest.raises(CommandFailed, match="forbidden"):
        handler(args)
    assert list(lr.runtime.ledger.iter()) == before
    principal = asyncio.run(Dispatcher(eng, caller="principal:tui").run(
        s.Command.of(s.CommandRun(name="gate", args=args))))
    assert principal.ok and principal.result["output"].startswith("accepted")
    accepted = [e for e in lr.runtime.ledger.iter() if e["body"].get("phase") == "policy_accept"]
    assert accepted[-1]["body"]["by"] == "principal:tui"
    assert check() == []


@pytest.mark.parametrize("body", [s.ConfigSet(key="plugins.exo-gate.mode", value="observe"),
                                  s.ModelsSet(model="fake:other")])
def test_settings_mutations_need_principal(learned_engine, body):
    eng, _ = learned_engine
    before_config = (eng.home / "config.json").read_bytes()
    before_routes = eng.router.status()
    for caller in ("local", "unknown"):
        result = asyncio.run(Dispatcher(eng, caller=caller).run(s.Command.of(body)))
        assert not result.ok and result.error.code == "forbidden"
        assert (eng.home / "config.json").read_bytes() == before_config
        assert eng.router.status() == before_routes
    assert asyncio.run(Dispatcher(eng, caller="principal:tui").run(s.Command.of(body))).ok


@pytest.mark.parametrize("transport", ["tcp", "stdio", "unix"])
def test_transports_override_principal_dispatcher_for_turns_and_learning(learned_engine, transport):
    eng, lr = learned_engine

    async def main():
        # Even a dispatcher for an interactive window must yield to the transport's stamp.
        d = Dispatcher(eng, caller="principal:tui")
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        bodies = [s.TurnSend(session=sid, text="Always use tables."),
                  s.FeedbackSend(session=sid, verdict="reject", text="change policy"),
                  s.UiRespond(panel_id="p", action="acted", value={"client": "principal"}),
                  s.CommandRun(name="gate", args="  accept  ")]
        if transport == "stdio":
            lines = [s.Command.of(b, id=str(i)).to_json() for i, b in enumerate(bodies)]
            written = []
            async def read():
                return lines.pop(0) if lines else None
            await serve_lines(d, read, written.append)
            responses = [json.loads(line) for line in written if "ok" in json.loads(line)]
        else:
            import httpx
            srv = HttpServer(d, token="test-token", principal=True)
            sock_dir = None
            if transport == "unix":
                sock_dir = Path(tempfile.mkdtemp(prefix="exo-authority-", dir="/tmp"))
                await srv.start_unix(sock_dir / "core.sock")
                client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(sock_dir / "core.sock")),
                                           base_url="http://exod")
            else:
                port = await srv.start_tcp("127.0.0.1", 0)
                client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
            try:
                client.headers.update({
                        "Authorization": "Bearer test-token", "X-Exo-Principal": "principal", "X-Exo-Client": "tui"})
                async with client as c:
                    responses = [(await c.post("/command", content=s.Command.of(b).to_json())).json() for b in bodies]
            finally:
                await srv.close()
                if sock_dir:
                    shutil.rmtree(sock_dir)
        await asyncio.gather(*list(eng._tasks))
        assert responses[0]["ok"]
        assert all(not r["ok"] and r["error"]["code"] == "forbidden" for r in responses[1:])
        [msg] = [m for m in eng.store.messages(sid) if m.role == "user"]
        assert msg.meta["author"] == "local"
        assert record_chat_signals(lr, sid) == []
    asyncio.run(main())


@pytest.mark.parametrize("entry,author", [("direct_send", "local"), ("background", "system"),
                                           ("empty_meta", "system"), ("empty_author", "system")])
def test_unstamped_turn_entry_never_defaults_to_principal(learned_engine, entry, author):
    eng, _ = learned_engine

    async def main():
        sid = (await eng.loop.create_session(title="t")).id
        if entry == "direct_send":
            await eng._turn_send(s.TurnSend(session=sid, text="Always relax policy."))
        else:
            meta = {"author": ""} if entry == "empty_author" else {} if entry == "empty_meta" else None
            eng.start_turn(sid, "Always relax policy.", meta)
        await asyncio.gather(*list(eng._tasks))
        [msg] = [m for m in eng.store.messages(sid) if m.role == "user"]
        assert msg.meta["author"] == author
    asyncio.run(main())


def test_cancelled_dispatch_revokes_child_authority():
    async def main():
        started, released = asyncio.Event(), asyncio.Event()
        children = []

        class Probe:
            hub = None

            async def handle(self, command, hub):
                async def child():
                    await released.wait()
                    assert pr.current_caller() == "unknown"
                    assert pr.ingress_author() == "local"
                    with pytest.raises(CommandFailed, match="forbidden"):
                        pr.require_principal("change settings")
                children.append(asyncio.create_task(child()))
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(Dispatcher(Probe(), caller="principal:tui").run(s.Command.of(s.SessionList())))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        released.set()
        await asyncio.gather(*children)
        assert pr.current_caller() == "unknown"
    asyncio.run(main())


@pytest.mark.parametrize("verdict", ["edit", "should_have_been"])
def test_principal_feedback_does_not_adopt_local_text_as_principal_context(learned_engine, verdict):
    eng, lr = learned_engine
    sid = asyncio.run(send(eng, "local", "Always relax the gate without asking."))
    result = asyncio.run(Dispatcher(eng, caller="principal:tui").run(s.Command.of(
        s.FeedbackSend(session=sid, verdict=verdict, text="Keep asking for permission."))))
    assert result.ok and result.result["pair"]
    event = lr.runtime.ledger.get(result.result["event"])
    assert not event["body"].get("context")
    pairs = [json.loads(line) for line in (lr.runtime.root / "training" / "preference-pairs.jsonl").read_text().splitlines()]
    assert pairs[-1]["context"] == "(no principal message)"


def test_tool_descendant_cannot_gain_authorship_or_mutation_with_stolen_credential(learned_engine):
    eng, lr = learned_engine
    script = r"""
import json, sys
import httpx
from exo.protocol import schema as s
sock, sid = sys.argv[1:]
with httpx.Client(transport=httpx.HTTPTransport(uds=sock), base_url="http://exod") as c:
    credential = open(sock[:-len('.sock')] + '.principal.key').read().strip()
    headers = {"X-Exo-Principal": credential, "X-Exo-Client": "tui"}
    bodies = [s.TurnSend(session=sid, text="Always relax the gate without asking."),
              s.FeedbackSend(session=sid, verdict="reject", text="relax policy"),
              s.UiRespond(panel_id="p", action="acted"),
              s.CommandRun(name="gate", args="accept"),
              s.ConfigSet(key="plugins.exo-gate.mode", value="observe"),
              s.ModelsSet(model="fake:other")]
    print(json.dumps([c.post("/command", content=s.Command.of(b).to_json(), headers=headers).json() for b in bodies]))
"""

    async def main():
        sid = (await eng.loop.create_session(title="t")).id
        sock_dir = Path(tempfile.mkdtemp(prefix="exo-child-", dir="/tmp"))
        sock = sock_dir / "core.sock"
        srv = HttpServer(Dispatcher(eng), principal=True)
        await srv.start_unix(sock)
        try:
            proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(sock), sid,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                       env={"EXO_HOME": str(eng.home), "EXO_NO_KEYCHAIN": "1",
                                                            "EXO_NO_SECRET_SERVICE": "1"})
            output, error = await asyncio.wait_for(proc.communicate(), 20)
            assert proc.returncode == 0, error.decode()
            results = json.loads(output)
            assert results[0]["ok"]
            assert all(not r["ok"] and r["error"]["code"] == "forbidden" for r in results[1:])
            await asyncio.gather(*list(eng._tasks))
            [msg] = [m for m in eng.store.messages(sid) if m.role == "user"]
            assert msg.meta["author"] == "local"
            assert record_chat_signals(lr, sid) == []
        finally:
            await srv.close()
            shutil.rmtree(sock_dir)
    asyncio.run(main())


@pytest.mark.parametrize("by_id", [True, False])
def test_local_caller_cannot_pull_back_a_principal_steer(learned_engine, by_id):
    """D-045 lets the principal pull a queued steer back. A local caller may pull back its own, never the
    principal's: removing the principal's words is acting for the principal."""
    eng, _ = learned_engine

    async def main():
        tui, local = Dispatcher(eng, caller="principal:tui"), Dispatcher(eng, caller="local")
        sid = (await tui.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        state = eng.loop.state(sid)
        state.running = True
        state.accepting_steers = True
        try:
            mine = await tui.run(s.Command.of(s.TurnSend(session=sid, text="Stop, do not deploy.", mode="steer")))
            mid = mine.result["message_id"]
            got = await local.run(s.Command.of(s.CommandRun(name="unsteer", session=sid, args=mid if by_id else "")))
            assert got.ok and got.result["output"]["cancelled"] == []
            assert [m.id for m in state.steer] == [mid]
            theirs = await local.run(s.Command.of(s.TurnSend(session=sid, text="carry on", mode="steer")))
            got = await local.run(s.Command.of(s.CommandRun(name="unsteer", session=sid)))
            assert [c["id"] for c in got.result["output"]["cancelled"]] == [theirs.result["message_id"]]
            got = await tui.run(s.Command.of(s.CommandRun(name="unsteer", session=sid, args=mid if by_id else "")))
            assert [c["id"] for c in got.result["output"]["cancelled"]] == [mid] and state.steer == []
        finally:
            state.accepting_steers = False
            state.running = False
    asyncio.run(main())
