"""The real gate cannot bypass required execution when audit or inspection fails."""

import asyncio
import time
from pathlib import Path

import pytest

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo.server.app import Dispatcher

REPO = Path(__file__).resolve().parents[2]


class Broker:
    def __init__(self):
        self.seen = []

    async def execute(self, request, tool, run_trusted):
        self.seen.append(request.tool_name)
        return await run_trusted()


def setup_engine(tmp_path, monkeypatch, mode="injected", *, store=True, observe=False, read_body_files=True):
    if mode == "env":
        monkeypatch.setenv("EXO_TOOL_EXECUTOR_REQUIRED", "1")
    root = tmp_path / "store"
    if store:
        root.mkdir()
    pm = PluginManager(config={"exo-gate": {
        "principal_store": str(root) if store else "", "key_path": str(tmp_path / "key"),
        "decision_model": False, "mode": "observe" if observe else "enforce",
        "read_body_files": read_body_files,
    }})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    gate = pm.hooks.entries("pre_tool_call")[0].callback.__self__
    broker = Broker() if mode != "ordinary" else None
    engine = CoreEngine(tmp_path / "home", plugins=pm, executor=broker,
                        executor_required=mode == "injected", cwd=str(tmp_path / "work"))
    ran = []
    for name in ("read_file", "bash"):
        old = engine.registry.get(name)
        engine.registry.register(old.spec, lambda args, ctx: ran.append(args) or "ran", replace=True)
    return engine, gate, broker, ran


def invoke(engine, call):
    async def main():
        try:
            return await engine.run_tool(call, {})
        finally:
            await engine.close()
    return asyncio.run(main())


@pytest.mark.parametrize("mode", ["env", "injected"])
@pytest.mark.parametrize("failure", ["no_store", "bad_key", "write_failure", "missing_id"])
def test_required_decision_audit_failure_prevents_executor(tmp_path, monkeypatch, mode, failure):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, mode, store=failure != "no_store")
    if failure == "bad_key":
        gate.settings.key_path.write_text("invalid signing key")
    elif failure == "write_failure":
        ledger = gate.sink._open()
        assert ledger is not None
        def fail(*args, **kw):
            raise OSError("private host failure details")
        monkeypatch.setattr(ledger, "append", fail)
    elif failure == "missing_id":
        monkeypatch.setattr(gate.sink, "append", lambda *a, **kw: None)
    result = invoke(engine, ToolCall("c", "read_file", {"path": "safe"}))
    assert result.is_error and "Required gate" in result.text()
    assert "private host" not in result.text()
    assert broker.seen == [] and ran == []


@pytest.mark.parametrize("observe", [False, True])
def test_required_safe_read_internal_error_never_uses_fallback(tmp_path, monkeypatch, observe):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, observe=observe)
    def fail(*args):
        raise RuntimeError("private host failure details")
    monkeypatch.setattr(gate, "_rules", fail)
    result = invoke(engine, ToolCall("c", "read_file", {"path": "safe"}))
    assert result.is_error and "private host" not in result.text()
    assert broker.seen == [] and ran == []


async def approved_call(engine):
    """Answer an actual approval request through the principal protocol."""
    dispatcher = Dispatcher(engine)
    sid = (await dispatcher.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    task = asyncio.create_task(engine.run_tool(ToolCall("c", "bash", {"command": "rm -rf cache"}), {"session": sid}))
    deadline = time.monotonic() + 5
    while not engine.approvals.open and not task.done() and time.monotonic() < deadline:
        await asyncio.sleep(0.001)
    assert engine.approvals.open, "gate must record the decision before asking"
    rid = next(iter(engine.approvals.open))
    response = await dispatcher.run(s.Command.of(s.ApprovalRespond(request_id=rid, decision="allow")), "principal:tui")
    assert response.ok
    return await asyncio.wait_for(task, 5)


@pytest.mark.parametrize("mode", ["env", "injected"])
def test_required_approval_append_failure_after_explicit_allow_prevents_executor(tmp_path, monkeypatch, mode):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, mode)
    ledger = gate.sink._open()
    append = ledger.append
    recorded = []
    def fail_approval(kind, body, *args, **kw):
        recorded.append(body["phase"])
        if body["phase"] == "approval":
            raise OSError("private approval write details")
        return append(kind, body, *args, **kw)
    monkeypatch.setattr(ledger, "append", fail_approval)

    async def main():
        try:
            result = await approved_call(engine)
            assert result.is_error and "required security hook failed" in result.text()
            assert "private approval" not in result.text()
            assert recorded == ["decision", "approval"]
            assert broker.seen == [] and ran == []
        finally:
            await engine.close()
    asyncio.run(main())


@pytest.mark.parametrize("phase", ["pre_tool_call", "post_approval_response"])
def test_required_gate_callback_exception_cannot_be_swallowed(tmp_path, monkeypatch, phase):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch)
    entry = engine.plugins.hooks.entries(phase)[0]
    async def fail(**kw):
        raise RuntimeError("private callback details")
    entry.callback = fail

    async def main():
        try:
            result = (await approved_call(engine) if phase == "post_approval_response"
                      else await engine.run_tool(ToolCall("c", "read_file", {"path": "safe"}), {}))
            assert result.is_error and "required security hook failed" in result.text()
            assert "private callback" not in result.text()
            assert broker.seen == [] and ran == []
        finally:
            await engine.close()
    asyncio.run(main())


def test_optional_hook_failures_remain_optional_in_required_engine(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch)
    async def broken_observer(**kw):
        raise RuntimeError("optional observer")
    for phase in ("pre_tool_call", "post_approval_response"):
        engine.plugins.hooks.register(phase, broken_observer, "optional-observer")
    async def main():
        try:
            result = await approved_call(engine)
            assert not result.is_error
            assert broker.seen == ["bash"] and len(ran) == 1
        finally:
            await engine.close()
    asyncio.run(main())


def test_ordinary_mode_still_allows_read_when_audit_or_classifier_fails(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, "ordinary", store=False)
    async def main():
        try:
            result = await engine.run_tool(ToolCall("c", "read_file", {"path": "safe"}), {})
            assert not result.is_error
            def fail(*args):
                raise RuntimeError("ordinary fallback")
            monkeypatch.setattr(gate, "_rules", fail)
            result = await engine.run_tool(ToolCall("c2", "read_file", {"path": "safe"}), {})
            assert not result.is_error and len(ran) == 2
        finally:
            await engine.close()
    asyncio.run(main())


@pytest.mark.parametrize("symlink", [False, True])
@pytest.mark.parametrize("read_body_files", [False, True])
def test_required_body_file_blocks_without_host_read_even_when_inspection_disabled(tmp_path, monkeypatch,
                                                                                 symlink, read_body_files):
    outside = tmp_path / "outside-canary"
    outside.write_text("OUTSIDE-CANARY: private host content")
    work = tmp_path / "work"
    work.mkdir()
    selected = work / "body"
    if symlink:
        selected.symlink_to(outside)
    else:
        selected = outside
    command = f"gh pr comment 3 -R other/repo --body-file {selected}"
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, read_body_files=read_body_files)
    original = Path.read_text
    seen = []
    def tracked(path, *args, **kw):
        if path in (outside, selected):
            seen.append(path)
        return original(path, *args, **kw)
    monkeypatch.setattr(Path, "read_text", tracked)
    # Positive control: ordinary mode actually dereferences the selected outside file or symlink.
    manager = engine.plugins
    manager.executor_required = False
    gate.settings.read_body_files = True
    classified, decision = gate._rules("bash", {"command": command}, "high")
    assert classified.text == "OUTSIDE-CANARY: private host content" and seen == [selected]
    manager.executor_required = True
    gate.settings.read_body_files = read_body_files
    seen.clear()
    result = invoke(engine, ToolCall("c", "bash", {"command": command}))
    assert result.is_error and "OUTSIDE-CANARY" not in result.text()
    assert seen == [] and broker.seen == [] and ran == []


@pytest.mark.parametrize("responses", [[("second", "allow"), ("first", "deny")],
                                       [("second", "deny"), ("first", "allow")]])
def test_identical_concurrent_approvals_keep_session_call_and_audit_refs(tmp_path, monkeypatch, responses):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch)
    tool = engine.registry.get("bash")
    engine.registry.register(tool.spec, lambda args, ctx: ran.append(ctx.session) or "ran", replace=True)

    async def main():
        try:
            dispatcher = Dispatcher(engine)
            sessions = {}
            tasks = {}
            for call_id in ("first", "second"):
                sessions[call_id] = (await dispatcher.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
                tasks[call_id] = asyncio.create_task(engine.run_tool(
                    ToolCall(call_id, "bash", {"command": "rm -rf cache"}), {"session": sessions[call_id]}))
            deadline = time.monotonic() + 5
            while len(engine.approvals.open) < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.001)
            assert len(engine.approvals.open) == 2
            requests = {p.request.call.id: rid for rid, p in engine.approvals.open.items()}
            for call_id, response in responses:
                result = await dispatcher.run(s.Command.of(s.ApprovalRespond(
                    request_id=requests[call_id], decision=response)), "principal:tui")
                assert result.ok
                outcome = await asyncio.wait_for(tasks[call_id], 5)
                assert outcome.is_error == (response == "deny")
            events = list(gate.sink._open().iter(kind="action"))
            decisions = {event["body"]["tool_call_id"]: event for event in events
                         if event["body"]["phase"] == "decision"}
            approvals = [event for event in events if event["body"]["phase"] == "approval"]
            assert decisions["first"]["body"]["rule_key"] == decisions["second"]["body"]["rule_key"]
            assert decisions["first"]["body"]["approval_key"] != decisions["second"]["body"]["approval_key"]
            for event in approvals:
                call_id = event["body"]["tool_call_id"]
                assert event["session"] == sessions[call_id]
                assert event["refs"] == [decisions[call_id]["id"]]
                assert event["body"]["approval_key"] == decisions[call_id]["body"]["approval_key"]
            allowed = next(call_id for call_id, response in responses if response == "allow")
            assert ran == [sessions[allowed]] and broker.seen == ["bash"]
            assert not gate._pending
        finally:
            await engine.close()
    asyncio.run(main())


def test_required_observe_mode_still_asks_before_executor(tmp_path, monkeypatch):
    """Observe mode logs instead of asking; a required executor still needs the principal's answer."""
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, observe=True)

    async def main():
        try:
            dispatcher = Dispatcher(engine)
            sid = (await dispatcher.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
            task = asyncio.create_task(engine.run_tool(ToolCall("c", "bash", {"command": "rm -rf cache"}),
                                                       {"session": sid}))
            deadline = time.monotonic() + 5
            while not engine.approvals.open and not task.done() and time.monotonic() < deadline:
                await asyncio.sleep(0.001)
            assert engine.approvals.open and not task.done(), "required mode must ask even in observe mode"
            assert broker.seen == [] and ran == []
            rid = next(iter(engine.approvals.open))
            await dispatcher.run(s.Command.of(s.ApprovalRespond(request_id=rid, decision="deny")), "principal:tui")
            result = await asyncio.wait_for(task, 5)
            assert result.is_error and broker.seen == [] and ran == []
        finally:
            await engine.close()
    asyncio.run(main())


def test_ordinary_observe_mode_still_runs_without_asking(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, "ordinary", observe=True)
    result = invoke(engine, ToolCall("c", "bash", {"command": "rm -rf cache"}))
    assert not result.is_error and ran == [{"command": "rm -rf cache"}]
