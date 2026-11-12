"""An event-driven wait must not poll, change job lifetime, or become a watching client."""
import asyncio
import time

import pytest

from exo import gate
from exo.core.engine import CoreEngine
from exo.core.seats import Seat
from exo.core.tools.registry import ToolError
from exo.core.types import ToolCall


def engine(tmp_path):
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}}, cwd=str(tmp_path),
                     env={}, seat=Seat(name="wait-test"), jobs={"wake": False})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "fixture-allow")
    return eng


def seed(eng, job="j_fixture", state="running", agent="wait-test"):
    eng.store._q("INSERT INTO jobs (id,agent,command,cwd,state,pid,started,log) VALUES (?,?,?,?,?,?,?,?)",
                 (job, agent, "fixture", str(eng.home), state, 999999, time.time(), str(eng.home / "unused.log")))
    return job


@pytest.mark.parametrize("command,expected", [("sleep 0.08; exit 0", "done"), ("sleep 0.08; exit 3", "failed")])
def test_real_job_completion_wakes_all_waiters_without_polling(tmp_path, monkeypatch, command, expected):
    async def run():
        eng = engine(tmp_path)
        try:
            sid = (await eng.loop.create_session(agent=eng.agent)).id
            job = eng.jobs.start(command, str(tmp_path), sid)
            queries = []
            get = eng.jobs.get
            monkeypatch.setattr(eng.jobs, "get", lambda jid: (queries.append(jid), get(jid))[1])
            before = set(eng.hub._subs)
            a, b = await asyncio.gather(eng.jobs.wait(job["id"], 2), eng.jobs.wait(job["id"], 2))
            assert all(row["state"] == expected and not expired for row, expired in (a, b))
            assert len(queries) == 4  # one snapshot and one completion read per waiter, no polling
            assert eng.hub._subs == before and eng.jobs._waiters == {}
        finally:
            await eng.close()
    asyncio.run(run())


@pytest.mark.parametrize("state", ["done", "failed", "killed", "timeout", "lost"])
def test_already_terminal_returns_without_waiter(tmp_path, state):
    async def run():
        eng = engine(tmp_path)
        try:
            job = seed(eng, state=state)
            row, expired = await asyncio.wait_for(eng.jobs.wait(job), 0.2)
            assert row["state"] == state and not expired and not eng.jobs._waiters
        finally:
            await eng.close()
    asyncio.run(run())


def test_timeout_and_cancellation_do_not_kill_or_retain_waiters(tmp_path):
    async def run():
        eng = engine(tmp_path)
        try:
            job = seed(eng)
            row, expired = await eng.jobs.wait(job, 0.01)
            assert expired and row["state"] == "running" and not eng.jobs._waiters
            waiter = asyncio.create_task(eng.jobs.wait(job))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert eng.jobs.get(job)["state"] == "running" and not eng.jobs._waiters
        finally:
            await eng.close()
    asyncio.run(run())


def test_recovery_wakes_waiter_as_lost(tmp_path):
    async def run():
        eng = engine(tmp_path)
        try:
            job = seed(eng)
            waiter = asyncio.create_task(eng.jobs.wait(job))
            await asyncio.sleep(0)
            assert await eng.jobs.recover() == [job]
            row, expired = await asyncio.wait_for(waiter, 0.2)
            assert row["state"] == "lost" and not expired and not eng.jobs._waiters
        finally:
            await eng.close()
    asyncio.run(run())


def test_shutdown_wakes_waiter_as_killed(tmp_path):
    async def run():
        eng = engine(tmp_path)
        try:
            job = eng.jobs.start("sleep 30", str(tmp_path), None)
            waiter = asyncio.create_task(eng.jobs.wait(job["id"]))
            await asyncio.sleep(0)
            await eng.jobs.close()
            row, expired = await asyncio.wait_for(waiter, 0.2)
            assert row["state"] == "killed" and not expired and not eng.jobs._waiters
        finally:
            await eng.close()
    asyncio.run(run())


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf"), True, "30", None])
def test_bad_timeout_refused(tmp_path, timeout):
    async def run():
        eng = engine(tmp_path)
        try:
            with pytest.raises(ToolError, match="timeout"):
                await eng.jobs.wait(seed(eng), timeout)
            assert not eng.jobs._waiters
        finally:
            await eng.close()
    asyncio.run(run())


def test_registered_tool_refuses_other_agent_and_returns_terminal_summary(tmp_path):
    async def run():
        eng = engine(tmp_path)
        try:
            other = seed(eng, "j_other", agent="other")
            mine = seed(eng, "j_mine", state="done")
            denied = await eng.registry.call(ToolCall(id="a", name="job_wait", arguments={"id": other}))
            assert denied.is_error and not eng.jobs._waiters
            result = await eng.registry.call(ToolCall(id="b", name="job_wait", arguments={"id": mine}))
            assert not result.is_error and "j_mine: done" in result.text()
            assert gate.classify(("job_wait", {"id": mine})).category == "read"
        finally:
            await eng.close()
    asyncio.run(run())
