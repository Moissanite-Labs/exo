"""CoreEngine.start after a failed or cancelled start: the next command retries (exo-desktop #14, issue 2).

The engine has no main role, so start runs the provider readiness probe (``providers_setup.main_status``)
before the MCP servers. The probe is the step that raises: an MCP server that fails is recorded in
``status()`` and skipped, and ``peers.start()`` runs before the start task exists. A start cancelled inside
``mcp.start`` must not leave its server tasks running: the retry stops them, and so does close().
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from exo import providers_setup
from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher

READY = {"ready": True, "route": "fake:x", "suggested": "", "needs_model": []}


def _engine(tmp_path) -> CoreEngine:
    eng = CoreEngine(tmp_path / "home", {"fake": {}, "roles": {"aux": ["fake:x"]}}, cwd=str(tmp_path))
    assert eng._automatic_main  # no main role: start runs the readiness probe
    return eng


def _probe(monkeypatch, *, fail: int = 0, gate: threading.Event | None = None,
           entered: threading.Event | None = None) -> list[int]:
    """Patch the readiness probe: the first ``fail`` calls raise; with ``gate``, the first call waits for it."""
    calls: list[int] = []

    def main_status(*args, **kwargs):
        calls.append(1)
        if entered is not None:
            entered.set()
        if gate is not None and len(calls) == 1:
            assert gate.wait(5)
        if len(calls) <= fail:
            raise ConnectionError("provider probe down")
        return dict(READY)

    monkeypatch.setattr(providers_setup, "main_status", main_status)
    return calls


def _list() -> s.Command:
    return s.Command.of(s.SessionList())


def test_dispatcher_retries_start_after_a_failed_start(tmp_path, monkeypatch):
    calls = _probe(monkeypatch, fail=1)
    eng = _engine(tmp_path)

    async def main():
        d = Dispatcher(eng)
        first = await d.run(_list())
        assert not first.ok and first.error is not None
        assert first.error.code == "internal" and "provider probe down" in first.error.message
        second = await d.run(_list())
        assert second.ok, second.error
        assert eng._started is not None and eng._started.done() and eng._started.exception() is None
        third = await d.run(_list())  # a started engine does not probe again
        assert third.ok and len(calls) == 2
        await eng.close()

    asyncio.run(main())


def test_concurrent_awaiters_of_a_failed_start_all_fail_then_retry_once(tmp_path, monkeypatch):
    gate = threading.Event()
    calls = _probe(monkeypatch, fail=1, gate=gate)
    eng = _engine(tmp_path)

    async def main():
        d = Dispatcher(eng)
        runs = [asyncio.ensure_future(d.run(_list())) for _ in range(3)]
        await asyncio.sleep(0.05)
        gate.set()
        failed = await asyncio.gather(*runs)
        assert [r.ok for r in failed] == [False] * 3
        assert all("provider probe down" in r.error.message for r in failed)
        assert len(calls) == 1
        retried = await asyncio.gather(d.run(_list()), d.run(_list()))
        assert [r.ok for r in retried] == [True, True], [r.error for r in retried]
        assert len(calls) == 2  # the two retries share one new start
        await eng.close()

    asyncio.run(main())


def test_a_cancelled_start_task_is_cleared_and_the_next_command_retries(tmp_path, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    calls = _probe(monkeypatch, gate=gate, entered=entered)
    eng = _engine(tmp_path)

    async def main():
        d = Dispatcher(eng)
        run = asyncio.ensure_future(d.run(_list()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            eng._started.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
        finally:
            gate.set()
        try:
            again = await d.run(_list())
        except asyncio.CancelledError:
            pytest.fail("the next command re-raised the cancelled start")
        assert again.ok, again.error
        assert eng._started is not None and eng._started.done() and len(calls) == 2
        await eng.close()

    asyncio.run(main())


def test_a_cancelled_awaiter_leaves_the_running_start_alone(tmp_path, monkeypatch):
    """Guard: cancelling only the command that waits on start does not start a second runtime."""
    gate, entered = threading.Event(), threading.Event()
    calls = _probe(monkeypatch, gate=gate, entered=entered)
    eng = _engine(tmp_path)

    async def main():
        d = Dispatcher(eng)
        run = asyncio.ensure_future(d.run(_list()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task = eng._started
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
            assert eng._started is task and not task.done()
        finally:
            gate.set()
        again = await d.run(_list())
        assert again.ok, again.error
        assert eng._started is task and len(calls) == 1
        await eng.close()

    asyncio.run(main())


def test_a_start_that_failed_after_its_only_caller_left_is_retried(tmp_path, monkeypatch):
    """No command saw that error, so the next command starts again. It does not get the old error."""
    gate, entered = threading.Event(), threading.Event()
    calls = _probe(monkeypatch, fail=1, gate=gate, entered=entered)
    eng = _engine(tmp_path)

    async def main():
        d = Dispatcher(eng)
        run = asyncio.ensure_future(d.run(_list()))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task = eng._started
            run.cancel()
            with pytest.raises(asyncio.CancelledError):
                await run
        finally:
            gate.set()
        with pytest.raises(ConnectionError):
            await task  # the start fails after its only caller left
        again = await d.run(_list())
        assert again.ok, again.error
        assert len(calls) == 2
        await eng.close()

    asyncio.run(main())


def _mcp_engine(tmp_path, monkeypatch) -> tuple[CoreEngine, list]:
    """An engine with one MCP server whose first connect never finishes; later connects are ready at once."""
    eng = CoreEngine(tmp_path / "home", {"fake": {}, "roles": {"aux": ["fake:x"]}}, cwd=str(tmp_path),
                     mcp={"s": {"command": "x"}})
    servers: list = []

    async def run(srv):
        servers.append(srv)
        if len(servers) > 1:
            srv.ready.set_result(True)
        await srv.stop.wait()

    monkeypatch.setattr(eng.mcp, "_run", run)
    return eng, servers


async def _cancel_start_inside_mcp(eng: CoreEngine, d: Dispatcher, servers: list) -> None:
    run = asyncio.ensure_future(d.run(_list()))
    for _ in range(200):
        if servers:
            break
        await asyncio.sleep(0.01)
    assert servers, "mcp.start never ran"
    eng._started.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run


def test_a_retry_after_a_start_cancelled_inside_mcp_start_stops_the_first_servers(tmp_path, monkeypatch):
    _probe(monkeypatch)
    eng, servers = _mcp_engine(tmp_path, monkeypatch)

    async def main():
        d = Dispatcher(eng)
        await _cancel_start_inside_mcp(eng, d, servers)
        first = servers[0]
        assert not first.task.done()  # the server task outlives the cancelled start
        again = await d.run(_list())
        assert again.ok, again.error
        assert len(servers) == 2 and first.task.done() and first.stop.is_set()  # not orphaned
        await eng.close()
        assert servers[1].task.done()

    asyncio.run(main())


def test_close_after_a_start_cancelled_inside_mcp_start_stops_its_servers(tmp_path, monkeypatch):
    """Guard: close() stops the MCP servers even when the cancelled start was cleared."""
    _probe(monkeypatch)
    eng, servers = _mcp_engine(tmp_path, monkeypatch)

    async def main():
        await _cancel_start_inside_mcp(eng, Dispatcher(eng), servers)
        await eng.close()
        assert servers[0].task.done() and servers[0].stop.is_set()

    asyncio.run(main())
