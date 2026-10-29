"""A pathological search cannot hold the core's interpreter lock or outlive its call."""
import asyncio
import os
import time

import pytest

from exo import search_worker
from exo.core.tools import Block, PRE_TOOL_CALL, ToolContext, default_registry
from exo.core.types import ToolCall, ToolResult


@pytest.fixture
def children(monkeypatch):
    processes = []
    create = asyncio.create_subprocess_exec

    async def record(*args, **kwargs):
        proc = await create(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record)
    yield processes
    assert all(p.returncode is not None for p in processes), "search left a child running"


def _call(name, cwd, **args):
    return default_registry().call(ToolCall("c", name, args), ToolContext(cwd=str(cwd)))


@pytest.mark.parametrize("name", ["grep", "glob", "grep-include"])
def test_pathological_pattern_times_out_without_stopping_loop(tmp_path, monkeypatch, children, name):
    monkeypatch.setattr(search_worker, "SEARCH_TIMEOUT", 1.0)
    if name == "grep":
        (tmp_path / "a.txt").write_text("a" * 40 + "b")
        args = {"pattern": "(a+)+$"}
    else:
        deepest = tmp_path.joinpath(*(["d"] * 30))
        deepest.mkdir(parents=True)
        (deepest / "a.txt").write_text("ordinary")
        pattern = "**/" * 30 + "absent"
        args = {"pattern": pattern} if name == "glob" else {"pattern": ".", "glob": pattern}
    # Built before the clock starts: the first build imports the built-in tools, which is not the search and on a
    # loaded runner took the call past its 3 s bound.
    registry = default_registry()
    async def run():
        ticks = []

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(.01)

        tick = asyncio.create_task(ticker())
        try:
            start = time.monotonic()
            call = ToolCall("c", "grep" if name == "grep-include" else name, args)
            result = await registry.call(call, ToolContext(cwd=str(tmp_path)))
            assert result.is_error and result.text() == "Search stopped after 1 s; narrow the pattern"
            assert time.monotonic() - start < 3
            assert len(ticks) > 5
            assert max(b - a for a, b in zip(ticks, ticks[1:])) < .1
        finally:
            tick.cancel()
            await asyncio.gather(tick, return_exceptions=True)

    asyncio.run(run())
    assert len(children) == 1 and children[0].returncode != 0


@pytest.mark.parametrize("cancel", ["task", "context"])
def test_cancel_stops_and_reaps_worker(tmp_path, children, cancel):
    (tmp_path / "a.txt").write_text("a" * 40 + "b")

    async def run():
        ctx = ToolContext(cwd=str(tmp_path))
        task = asyncio.create_task(default_registry().call(ToolCall("c", "grep", {"pattern": "(a+)+$"}), ctx))
        while not children:
            await asyncio.sleep(.01)
        await asyncio.sleep(.15)
        if cancel == "task":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert ctx.cancel.is_set()
        else:
            ctx.cancel.set()
            result = await task
            assert result.is_error and result.text() == "Search interrupted"
        assert children[0].returncode is not None

    asyncio.run(run())


def test_cancel_during_spawn_still_owns_child(tmp_path, children, monkeypatch):
    create = asyncio.create_subprocess_exec

    async def delayed(*args, **kwargs):
        proc = await create(*args, **kwargs)
        await asyncio.sleep(.1)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)

    async def run():
        task = asyncio.create_task(_call("grep", tmp_path, pattern="."))
        while not children:
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert children[0].returncode is not None

    asyncio.run(run())


def test_already_cancelled_call_starts_no_worker(tmp_path, children):
    ctx = ToolContext(cwd=str(tmp_path))
    ctx.cancel.set()
    result = asyncio.run(default_registry().call(ToolCall("c", "glob", {"pattern": "*"}), ctx))
    assert result.is_error and result.text() == "Search interrupted"
    assert children == []


@pytest.mark.parametrize("mode", ["gate", "missing", "remote", "trusted"])
def test_gate_and_required_executor_precede_local_worker(tmp_path, children, mode):
    (tmp_path / "a.txt").write_text("visible")
    seen = []

    class Executor:
        async def execute(self, request, spec, run_trusted):
            seen.append(request.tool_name)
            if mode == "remote":
                return ToolResult.of_text(request.call_id, "remote result")
            return await run_trusted()

    reg = default_registry(executor=None if mode == "missing" else Executor(), executor_required=True)
    if mode == "gate":
        reg.add_hook(PRE_TOOL_CALL, lambda *args: Block("refused"))
    result = asyncio.run(reg.call(ToolCall("c", "grep", {"pattern": "."}), ToolContext(cwd=str(tmp_path))))
    if mode in ("gate", "missing"):
        assert result.is_error and seen == [] and children == []
    elif mode == "remote":
        assert result.text() == "remote result" and seen == ["grep"] and children == []
    else:
        assert result.text() == "a.txt:1:visible" and seen == ["grep"] and len(children) == 1


def test_worker_failure_is_an_error_and_reaped(tmp_path, monkeypatch, children):
    monkeypatch.setattr(search_worker, "_BOOT", "raise SystemExit(7)")
    result = asyncio.run(_call("grep", tmp_path, pattern="."))
    assert result.is_error and result.text() == "Search worker exited with status 7"


def test_worker_keeps_store_paths_without_credentials_or_python_hooks(tmp_path, monkeypatch, children):
    create = asyncio.create_subprocess_exec
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-search-worker-secret")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setenv("LD_PRELOAD", "/fake/loader")
    monkeypatch.setenv("EXO_KEYS_FILE", str(tmp_path / "plain" / "credentials.cfg"))
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "credentials.cfg").write_text("must stay out")
    (tmp_path / "a.txt").write_text("visible")
    (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('untrusted startup')")

    async def inspect(*args, **kwargs):
        assert args[1] == "-I"
        assert kwargs["env"]["EXO_KEYS_FILE"] == os.environ["EXO_KEYS_FILE"]
        assert not {"ANTHROPIC_API_KEY", "PYTHONPATH", "LD_PRELOAD"} & kwargs["env"].keys()
        return await create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", inspect)
    result = asyncio.run(_call("grep", tmp_path, pattern="visible|must stay out"))
    assert not result.is_error
    assert result.text() == "a.txt:1:visible\n[skipped 1 secret-store paths; read one by name to be asked]"


def test_large_output_preserves_registry_truncation(tmp_path):
    from exo.core.tools.builtin.search import grep
    from exo.core.tools.registry import _cap_result

    (tmp_path / "a.txt").write_text(("x" * 300 + "\n") * 500)
    args = {"pattern": "x", "limit": 500}
    expected = _cap_result(ToolResult.of_text("c", grep(args, ToolContext(cwd=str(tmp_path)))))
    assert asyncio.run(_call("grep", tmp_path, **args)).text() == expected.text()
