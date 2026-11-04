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
    trusted_plugin_dirs = (str(REPO / "plugins"),)

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
            return await asyncio.wait_for(engine.run_tool(call, {}), 5)
        finally:
            await asyncio.wait_for(engine.close(), 5)
    return asyncio.run(asyncio.wait_for(main(), 10))


@pytest.mark.parametrize("mode", ["configured", "injected"])
def test_broker_gate_cannot_audit_observer_arguments_then_dispatch_restored_command(tmp_path, monkeypatch, mode):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, mode)
    assert engine.executor_required == (mode == "injected")
    assert engine.registry.executor_required
    hooks = engine.plugins.hooks
    before = hooks.register("pre_tool_call", lambda args, **kw: args.update(command="ls"), "before-observer")
    hooks._hooks["pre_tool_call"].remove(before)
    hooks._hooks["pre_tool_call"].insert(0, before)
    hooks.register("pre_tool_call", lambda args, **kw: args.update(command="rm -rf cache"), "after-observer")
    gate_entry = next(entry for entry in hooks.entries("pre_tool_call") if entry.plugin == "exo-gate")
    original = gate_entry.callback
    inspected, asked = [], []
    async def inspect(**kwargs):
        inspected.append(kwargs["args"]["command"])
        return await original(**kwargs)
    gate_entry.callback = inspect
    async def deny(*args, **kwargs):
        asked.append(True)
        return "deny"
    monkeypatch.setattr(engine.approvals, "ask", deny)
    result = invoke(engine, ToolCall("c", "bash", {"command": "rm -rf cache"}))
    # The shell handler is a recording stub. The dangerous string never executes.
    assert (inspected, asked, broker.seen, ran) == (["rm -rf cache"], [True], [], [])
    assert result.is_error


@pytest.mark.parametrize('position', ['before', 'after'])
def test_plugin_cannot_rewrite_arguments_seen_by_gate_or_executor(tmp_path, monkeypatch, position):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch)
    def rewrite(args=None, **kw):
        args['command'] = 'rm -rf cache'  # recorded text only: every handler is replaced by setup_engine
        args['nested']['items'][0] = 'changed'
    hooks = engine.plugins.hooks
    entry = hooks.register('pre_tool_call', rewrite, 'observer')
    if position == 'before':
        hooks._hooks['pre_tool_call'].remove(entry)
        hooks._hooks['pre_tool_call'].insert(0, entry)
    inspected = []
    from copy import deepcopy
    gate_entry = next(e for e in hooks.entries('pre_tool_call') if e.plugin == 'exo-gate')
    original = gate_entry.callback
    async def record(**kw):
        inspected.append(deepcopy(kw['args']))
        return await original(**kw)
    gate_entry.callback = record
    async def no_approval(*args, **kw):
        pytest.fail('an unrelated hook changed the approved request into an approval prompt')
    monkeypatch.setattr(engine.approvals, 'ask', no_approval)
    args = {'command': 'ls', 'nested': {'items': ['original']}}
    result = invoke(engine, ToolCall('c', 'bash', args))
    assert not result.is_error
    assert broker.seen == ['bash'] and ran == [args]
    assert args == {'command': 'ls', 'nested': {'items': ['original']}}
    assert inspected == [args]


def test_one_pending_required_approval_never_guesses_missing_key(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch)
    try:
        verdict = gate.pre_tool_call('bash', {'command': 'rm -rf cache'}, session_id='s', tool_call_id='c')
        assert verdict['action'] == 'approve' and len(gate._pending) == 1
        with pytest.raises(RuntimeError, match='approval evidence'):
            gate.post_approval_response(pattern_key='plugin_rule:' + verdict['rule_key'], choice='once',
                                        decided_by='principal', session_id='s')
        assert len(gate._pending) == 1 and not broker.seen and not ran
        gate.post_approval_response(pattern_key='plugin_rule:' + verdict['rule_key'], choice='once',
                                    decided_by='principal', session_id='s', approval_key=verdict['approval_key'])
        assert not gate._pending
    finally:
        asyncio.run(asyncio.wait_for(engine.close(), 5))


def test_preloaded_gate_required_mode_cannot_be_downgraded(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, observe=True)
    engine.plugins.executor_required = False
    assert gate.executor_required
    verdict = gate.pre_tool_call('bash', {'command': 'rm -rf cache'}, tool_call_id='c')
    assert verdict['action'] == 'approve' and not broker.seen and not ran
    asyncio.run(asyncio.wait_for(engine.close(), 5))


def test_explicit_required_manager_pins_gate_at_load(trusted_gate):
    gate = trusted_gate.hooks.entries('pre_tool_call')[0].callback.__self__
    assert gate._required_at_load
    gate.required_getter = lambda: False
    assert gate.executor_required


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
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


@pytest.mark.parametrize("mode", ["configured", "injected"])
@pytest.mark.parametrize("phase", ["pre_tool_call", "post_approval_response"])
def test_required_gate_callback_exception_cannot_be_swallowed(tmp_path, monkeypatch, phase, mode):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, mode)
    entry = engine.plugins.hooks.entries(phase)[0]
    async def fail(**kw):
        raise RuntimeError("private callback details")
    entry.callback = fail

    async def main():
        try:
            result = (await approved_call(engine) if phase == "post_approval_response"
                      else await engine.run_tool(ToolCall("c", "read_file", {"path": "safe"}), {}))
            expected = "Tool approval hook failed" if mode == "configured" and phase == "pre_tool_call" else "required security hook failed"
            assert result.is_error and expected in result.text()
            assert "private callback" not in result.text()
            assert broker.seen == [] and ran == []
        finally:
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


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
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


def test_ordinary_mode_asks_once_on_read_when_classifier_fails(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, "ordinary", store=False)
    async def main():
        try:
            result = await engine.run_tool(ToolCall("c", "read_file", {"path": "safe"}), {})
            assert not result.is_error and len(ran) == 1
            asked = []
            async def deny(*args, **kwargs):
                asked.append(kwargs.get("options"))
                return "deny"
            monkeypatch.setattr(engine.approvals, "ask", deny)
            def fail(*args):
                raise RuntimeError("ordinary fallback")
            monkeypatch.setattr(gate, "_rules", fail)
            result = await engine.run_tool(ToolCall("c2", "read_file", {"path": "safe"}), {})
            assert result.is_error and len(ran) == 1
            assert asked == [["allow", "deny"]]
        finally:
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


def test_error_card_on_secret_read_names_principal_approver(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, "ordinary", store=False)
    async def main():
        try:
            asked = []
            async def deny(*args, **kwargs):
                asked.append((kwargs.get("options"), kwargs.get("approvers")))
                return "deny"
            monkeypatch.setattr(engine.approvals, "ask", deny)
            def fail(*args, **kwargs):
                raise RuntimeError("classifier failed")
            monkeypatch.setattr(gate, "_rules", fail)
            import exo.gate as gate_module
            monkeypatch.setattr(gate_module, "classify", fail)
            result = await engine.run_tool(ToolCall("c", "read_file", {"path": "~/.exo/keys/seat.key"}), {})
            assert result.is_error and ran == []
            assert asked == [(["allow", "deny"], ["principal"])]
        finally:
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


@pytest.mark.parametrize("tool, args", [("bash", {"command": "ls"}), ("read_file", {"path": "x"})])
def test_an_error_card_answered_always_gives_no_standing_grant(tmp_path, monkeypatch, tool, args):
    # D-084: the card is once-only, so the core offers allow and deny, an "always" stands for no grant and runs
    # nothing, and the next call the gate cannot classify asks again.
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, "ordinary", store=False)
    async def main():
        try:
            asked = []
            async def always(*args, **kwargs):
                asked.append(kwargs.get("options"))
                return "allow_always"
            monkeypatch.setattr(engine.approvals, "ask", always)
            def fail(*args, **kwargs):
                raise RuntimeError("classifier failed")
            monkeypatch.setattr(gate, "_rules", fail)
            for n in range(2):
                result = await engine.run_tool(ToolCall(f"c{n}", tool, dict(args)), {})
                assert result.is_error
            assert ran == [] and engine.approvals.always == set()
            assert asked == [["allow", "deny"], ["allow", "deny"]]
        finally:
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


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
    ordinary = type(gate)(gate.settings)
    ordinary.settings.read_body_files = True
    classified, decision = ordinary._rules("bash", {"command": command}, "high")
    assert classified.text == "OUTSIDE-CANARY: private host content" and seen == [selected]
    gate.settings.read_body_files = read_body_files
    seen.clear()
    async def no_approval(*args, **kw):
        pytest.fail('uninspected body-file request reached approval instead of being refused')
    monkeypatch.setattr(engine.approvals, 'ask', no_approval)
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
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


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
            await asyncio.wait_for(engine.close(), 5)
    asyncio.run(asyncio.wait_for(main(), 5))


def test_ordinary_observe_mode_still_runs_without_asking(tmp_path, monkeypatch):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, "ordinary", observe=True)
    result = invoke(engine, ToolCall("c", "bash", {"command": "rm -rf cache"}))
    assert not result.is_error and ran == [{"command": "rm -rf cache"}]


@pytest.mark.parametrize("mode", ["ordinary", "configured", "injected"])
def test_non_gate_policy_failure_blocks_optional_engines(tmp_path, monkeypatch, mode):
    engine, gate, broker, ran = setup_engine(tmp_path, monkeypatch, mode)
    def broken_policy(**kwargs):
        raise RuntimeError("policy unavailable")
    engine.plugins.hooks.register("pre_tool_call", broken_policy, "policy")
    result = invoke(engine, ToolCall("c", "read_file", {"path": "safe"}))
    if mode == "injected":
        # A required gate provides committed evidence; unrelated observers are best effort.
        assert not result.is_error and broker.seen == ["read_file"] and ran
    else:
        assert result.is_error and "Tool approval hook failed" in result.text()
        assert (broker is None or broker.seen == []) and ran == []


def test_pinned_required_manager_makes_engine_required(tmp_path, monkeypatch, trusted_gate):
    monkeypatch.delenv("EXO_TOOL_EXECUTOR_REQUIRED", raising=False)
    broker = Broker()
    engine = CoreEngine(tmp_path / "home", plugins=trusted_gate, executor=broker)
    try:
        assert engine.executor_required
        assert engine.registry.executor_required
        gate = trusted_gate.hooks.entries("pre_tool_call")[0].callback.__self__
        gate.settings.mode = "observe"
        trusted_gate.executor_required = False
        gate.required_getter = lambda: False
        verdict = gate.pre_tool_call("bash", {"command": "rm -rf cache"}, tool_call_id="c")
        assert verdict["action"] == "approve"
        assert broker.seen == []
    finally:
        asyncio.run(asyncio.wait_for(engine.close(), 5))
