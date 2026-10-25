"""Validated session identity reaches plugin commands without breaking legacy handlers."""
import asyncio
import hashlib
import json
from pathlib import Path

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher


def test_plugin_command_receives_existing_session_only(tmp_path):
    plugins = PluginManager(config={"exo-gate": {"decision_model": False}})
    assert plugins.load_folder(Path(__file__).resolve().parents[2] / "plugins" / "exo-gate").ok
    eng = CoreEngine(tmp_path / "home", {"fake": {"reply": "fixture"}, "roles": {"main": ["fake:x"]}},
                     plugins=plugins, cwd=str(tmp_path), env={})

    async def run():
        # Replace an existing fixture command with both legacy and session-aware handlers.
        cmd = eng.plugins.commands["permissions"]
        seen = []
        cmd.handler = lambda args: "legacy:" + args
        dispatch = Dispatcher(eng, caller="principal:tui")
        out = await dispatch.run(s.Command.of(s.CommandRun(name="permissions", args="fixture")))
        assert out.ok and out.result["output"] == "legacy:fixture"
        cmd.handler = lambda args, session_id="": seen.append(session_id) or session_id
        sid = (await eng.loop.create_session(title="fixture")).id
        out = await dispatch.run(s.Command.of(s.CommandRun(name="permissions", session=sid)))
        assert out.ok and out.result["output"] == sid and seen == [sid]
        out = await dispatch.run(s.Command.of(s.CommandRun(name="permissions", session="missing-fixture")))
        assert not out.ok and out.error.code == "not_found" and seen == [sid]
        received = []

        async def accepts_keywords(args, **kwargs):
            received.append(kwargs)
            return {"args": args, "session": kwargs["session_id"]}

        cmd.handler = accepts_keywords
        out = await dispatch.run(s.Command.of(s.CommandRun(name="permissions", args="async", session=sid)))
        assert out.ok and out.result["output"] == {"args": "async", "session": sid}
        assert received == [{"session_id": sid, "request_approval": eng.approvals.ask}]
        out = await dispatch.run(s.Command.of(s.CommandRun(name="unknown-fixture", session="missing-fixture")))
        assert not out.ok and out.error.code == "not_found" and out.error.message == "no command /unknown-fixture"
        assert len(received) == 1
        await eng.close()

    asyncio.run(run())


def test_dispatch_mode_approval_is_principal_only_and_session_bound(tmp_path, template_store):
    (template_store.root / "policies" / "gates.yaml").write_text("version: 1\ncategories: {local_write: ask}\n")
    plugins = PluginManager(config={"exo-gate": {"decision_model": False,
        "principal_store": str(template_store.root), "key_path": str(tmp_path / "gate.key")}})
    assert plugins.load_folder(Path(__file__).resolve().parents[2] / "plugins" / "exo-gate").ok
    eng = CoreEngine(tmp_path / "home", {"fake": {"reply": "fixture"}, "roles": {"main": ["fake:x"]}},
                     plugins=plugins, cwd=str(tmp_path), env={})

    async def run():
        principal = Dispatcher(eng, caller="principal:tui")
        local = Dispatcher(eng, caller="local")
        ids = [(await eng.loop.create_session(title="fixture")).id for _ in range(2)]
        async def command(args, sid=ids[0], dispatcher=principal):
            return await dispatcher.run(s.Command.of(s.CommandRun(name="permissions", args=args, session=sid)))
        async def pending():
            for _ in range(100):
                if eng.approvals.open:
                    return next(iter(eng.approvals.open.values())).request
                await asyncio.sleep(0)
            raise AssertionError("mode selection never requested approval")
        try:
            for caller in ["local", "peer:fixture", "seat:fixture"]:
                out = await command("bypass", dispatcher=Dispatcher(eng, caller=caller))
                assert not out.ok and out.error.code == "forbidden"
            for decision in ["deny", "allow"]:
                task = asyncio.create_task(command("bypass"))
                req = await pending()
                assert req.options == ["allow", "deny"] and req.approvers == ["principal"] and req.binding
                assert req.binding == hashlib.sha256(json.dumps({"mode": "bypass", "session": ids[0]},
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                seat_answer = await local.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id,
                    decision="allow", client="seat:fixture", sig="fixture-invalid")))
                assert not seat_answer.ok and seat_answer.error.code == "forbidden"
                refused = await local.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id, decision="allow")))
                assert not refused.ok and refused.error.code == "forbidden"
                assert (await command("json")).result["output"]["mode"] == "ask"
                answer = await principal.run(s.Command.of(s.ApprovalRespond(request_id=req.request_id,
                                             decision=decision, binding=req.binding)))
                assert answer.ok and (await task).ok
            assert (await command("json")).result["output"]["mode"] == "bypass"
            assert (await command("json", ids[1])).result["output"]["mode"] == "ask"
            assert await eng.plugins.hooks.afire("pre_tool_call", tool_name="write_file",
                         args={"path": "fixture.txt"}, session_id=ids[0]) == []
            assert (await eng.plugins.hooks.afire("pre_tool_call", tool_name="write_file",
                         args={"path": "fixture.txt"}, session_id=ids[1]))[0]["action"] == "approve"
            assert (await command("ask")).ok
            task = asyncio.create_task(command("bypass"))
            await pending()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert not eng.approvals.open and (await command("json")).result["output"]["mode"] == "ask"
        finally:
            await eng.close()

    asyncio.run(run())
