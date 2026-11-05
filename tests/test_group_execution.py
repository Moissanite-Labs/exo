import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from exo.core.group_execution import GroupExecutions
from exo.core.group_messages import GroupMessages
from exo.core.group_runtime import GroupRuntime
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


def setup(path):
    store = SessionStore(path)
    groups = GroupStore(store)
    group = groups.create(owner="host", mode="socratic", members=["one", "two"], known_profiles=["one", "two"])
    return store, groups, group


def test_atomic_claim_separate_connections_and_reopen(tmp_path):
    path = tmp_path / "sessions.sqlite"
    store, groups, group = setup(path)
    GroupExecutions(groups)
    def claim(_):
        other = SessionStore(path)
        try:
            return GroupExecutions(GroupStore(other)).claim(group.id, owner="host").id
        except ValueError:
            return None
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert sum(r is not None for r in results) == 1
    store.close()
    reopened = SessionStore(path)
    try:
        executions = GroupExecutions(GroupStore(reopened))
        assert executions.get(group.id, owner="host").state == "active"
        with pytest.raises(ValueError, match="already claimed"):
            executions.claim(group.id, owner="host")
    finally:
        reopened.close()


def test_owner_terminal_validation_and_no_rerun(tmp_path):
    store, groups, group = setup(tmp_path / "sessions.sqlite")
    try:
        executions = GroupExecutions(groups)
        assert executions.get(group.id, owner="host") is None
        for call in (executions.claim, executions.get):
            with pytest.raises(ValueError, match="no group"):
                call(group.id, owner="other")
        receipt = executions.claim(group.id, owner="host")
        for members in ({"one": "completed"}, {"one": "running", "two": "completed"}):
            with pytest.raises(ValueError, match="terminal"):
                executions.finish(group.id, owner="host", execution_id=receipt.id, members=members)
        terminal = {"one": "completed", "two": "failed"}
        with pytest.raises(ValueError, match="no execution"):
            executions.finish(group.id, owner="host", execution_id="wrong", members=terminal)
        with pytest.raises(ValueError, match="no group"):
            executions.finish(group.id, owner="other", execution_id=receipt.id, members=terminal)
        executions.finish(group.id, owner="host", execution_id=receipt.id, members=terminal)
        settled = executions.get(group.id, owner="host")
        executions.finish(group.id, owner="host", execution_id=receipt.id, members=terminal)
        assert executions.get(group.id, owner="host") == settled
        assert settled.state == "settled" and settled.finished
        with pytest.raises(ValueError, match="different results"):
            executions.finish(group.id, owner="host", execution_id=receipt.id,
                              members={"one": "completed", "two": "completed"})
        with pytest.raises(ValueError, match="already claimed"):
            executions.claim(group.id, owner="host")
    finally:
        store.close()


def test_runtime_claim_and_settlement_survive_observer_cancellation(tmp_path):
    async def scenario():
        store, groups, group = setup(tmp_path / "sessions.sqlite")
        try:
            messages = GroupMessages(groups)
            entered = []
            release = asyncio.Event()
            async def adapter(ctx):
                entered.append(ctx.member.profile)
                await release.wait()
            adapters = dict.fromkeys(["one", "two"], adapter)
            first = GroupRuntime(messages, group.id, owner="host", adapters=adapters)
            second = GroupRuntime(messages, group.id, owner="host", adapters=adapters)
            first.start()
            settler = first._settler
            with pytest.raises(ValueError, match="already claimed"):
                first.start()
            assert first._settler is settler
            with pytest.raises(ValueError, match="already claimed"):
                second.start()
            observer = asyncio.create_task(first.wait())
            await asyncio.sleep(0)
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observer
            assert sorted(entered) == ["one", "two"]
            assert second.receipt().state == "active"
            release.set()
            # Settlement must run even when no observer is waiting.
            await asyncio.wait_for(asyncio.shield(first._settler), 1)
            assert second.receipt().members == {"one": "completed", "two": "completed"}
            assert second.receipt().state == "settled"
            with pytest.raises(ValueError, match="already claimed"):
                second.start()
        finally:
            store.close()
    asyncio.run(scenario())


def test_process_exit_does_not_release_claim(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "sessions.sqlite"
    store, groups, group = setup(path)
    try:
        script = '''
import os, sys
from exo.core.sessions import SessionStore
from exo.core.groups import GroupStore
from exo.core.group_execution import GroupExecutions
store = SessionStore(sys.argv[1])
GroupExecutions(GroupStore(store)).claim(sys.argv[2], owner="host")
os._exit(0)
'''
        subprocess.run([sys.executable, "-c", script, str(path), group.id], check=True, timeout=15)
        executions = GroupExecutions(groups)
        assert executions.get(group.id, owner="host").state == "active"
        with pytest.raises(ValueError, match="already claimed"):
            executions.claim(group.id, owner="host")
    finally:
        store.close()


def test_stop_receipt_waits_for_cleanup(tmp_path):
    async def scenario():
        store, groups, group = setup(tmp_path / "sessions.sqlite")
        try:
            started = asyncio.Event()
            cleaning = asyncio.Event()
            release = asyncio.Event()
            async def adapter(ctx):
                if ctx.member.profile == "two":
                    raise RuntimeError("adapter detail must not be persisted")
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
            runtime = GroupRuntime(GroupMessages(groups), group.id, owner="host",
                                   adapters=dict.fromkeys(["one", "two"], adapter))
            runtime.start()
            await asyncio.wait_for(started.wait(), 2)
            runtime.stop("one")
            await asyncio.wait_for(cleaning.wait(), 2)
            assert runtime.receipt().state == "active"
            release.set()
            await asyncio.wait_for(runtime.wait(), 2)
            receipt = runtime.receipt()
            assert receipt.state == "settled"
            assert receipt.members == {"one": "stopped", "two": "failed"}
        finally:
            store.close()
    asyncio.run(scenario())


def test_start_outside_running_loop_leaves_no_claim(tmp_path):
    store, groups, group = setup(tmp_path / "sessions.sqlite")
    entered = []
    async def adapter(ctx):
        entered.append(ctx.member.profile)
    runtime = GroupRuntime(GroupMessages(groups), group.id, owner="host",
                           adapters=dict.fromkeys(["one", "two"], adapter))
    try:
        with pytest.raises(RuntimeError, match="no running event loop"):
            runtime.start()
        assert runtime.receipt() is None
        assert store._q("SELECT * FROM swarm_executions") == []
        assert entered == []
        assert {status.state for status in runtime.status()} == {"idle"}
        async def scenario():
            runtime.start()
            await asyncio.wait_for(runtime.wait(), 2)
            assert sorted(entered) == ["one", "two"]
            assert runtime.receipt().members == {"one": "completed", "two": "completed"}
        asyncio.run(asyncio.wait_for(scenario(), 5))
    finally:
        store.close()


def test_wait_propagates_receipt_save_failure_and_keeps_active_claim(tmp_path):
    async def scenario():
        store, groups, group = setup(tmp_path / "sessions.sqlite")
        entered = []
        async def adapter(ctx):
            entered.append(ctx.member.profile)
        runtime = GroupRuntime(GroupMessages(groups), group.id, owner="host",
                               adapters=dict.fromkeys(["one", "two"], adapter))
        try:
            store._q("CREATE TRIGGER abort_settlement BEFORE UPDATE ON swarm_executions "
                     "BEGIN SELECT RAISE(ABORT, 'receipt save blocked'); END")
            runtime.start()
            with pytest.raises(sqlite3.IntegrityError, match="receipt save blocked"):
                await asyncio.wait_for(runtime.wait(), 2)
            assert sorted(entered) == ["one", "two"]
            assert {status.state for status in runtime.status()} == {"completed"}
            receipt = runtime.receipt()
            assert receipt.state == "active" and receipt.finished is None
            # These are claim-time placeholders, despite both adapters having exited.
            assert receipt.members == {"one": "queued", "two": "queued"}
            with pytest.raises(sqlite3.IntegrityError, match="receipt save blocked"):
                await asyncio.wait_for(runtime.wait(), 2)
            assert runtime.receipt() == receipt
            with pytest.raises(ValueError, match="already claimed"):
                runtime.start()
        finally:
            store.close()
    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_failed_runner_start_retains_claim_and_wait_reports_not_started(tmp_path, monkeypatch):
    async def scenario():
        store, groups, group = setup(tmp_path / "sessions.sqlite")
        entered = []
        async def adapter(ctx):
            entered.append(ctx.member.profile)
        runtime = GroupRuntime(GroupMessages(groups), group.id, owner="host",
                               adapters=dict.fromkeys(["one", "two"], adapter))
        def fail_start():
            raise RuntimeError("runner start failed")
        monkeypatch.setattr(runtime._runner, "start", fail_start)
        try:
            with pytest.raises(RuntimeError, match="runner start failed"):
                runtime.start()
            receipt = runtime.receipt()
            assert receipt.state == "active" and receipt.finished is None
            assert entered == []
            with pytest.raises(ValueError, match="execution has not started"):
                await asyncio.wait_for(runtime.wait(), 2)
            with pytest.raises(ValueError, match="already claimed"):
                runtime.start()
            assert runtime.receipt() == receipt
        finally:
            store.close()
    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_unrequested_cancellation_settles_as_failed(tmp_path):
    async def scenario():
        store, groups, group = setup(tmp_path / "sessions.sqlite")
        async def adapter(ctx):
            if ctx.member.profile == "one":
                raise asyncio.CancelledError("adapter detail must not be persisted")
        runtime = GroupRuntime(GroupMessages(groups), group.id, owner="host",
                               adapters=dict.fromkeys(["one", "two"], adapter))
        try:
            runtime.start()
            await asyncio.wait_for(runtime.wait(), 2)
            receipt = runtime.receipt()
            assert receipt.state == "settled"
            assert receipt.members == {"one": "failed", "two": "completed"}
            assert "adapter detail" not in repr(receipt)
        finally:
            store.close()
    asyncio.run(asyncio.wait_for(scenario(), 5))
