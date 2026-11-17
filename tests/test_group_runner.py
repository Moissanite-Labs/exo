import asyncio

import pytest

from exo.core.group_runner import GroupRunner
from exo.core.groups import GroupMember, SavedGroup


def group():
    return SavedGroup("g-test", "host", "socratic", None,
                      (GroupMember("one", "s-one"), GroupMember("two", "s-two")), "now")


def states(runtime):
    return {s.profile: s.state for s in runtime.status()}


def test_concurrent_and_no_implicit_restart():
    async def scenario():
        entered = set()
        both = asyncio.Event()
        async def run(member):
            entered.add(member.profile)
            if len(entered) == 2:
                both.set()
            await both.wait()
        runtime = GroupRunner(group(), {"one": run, "two": run})
        runtime.start()
        await asyncio.wait_for(runtime.wait(), 1)
        assert states(runtime) == {"one": "completed", "two": "completed"}
        with pytest.raises(ValueError):
            runtime.start()
    asyncio.run(scenario())


@pytest.mark.parametrize("reraise", [True, False])
def test_stop_receipt_waits_for_cleanup_and_is_not_repeated(reraise):
    async def scenario():
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()
        async def one(member):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                cleaned.set()
                if reraise:
                    raise
        async def two(member):
            return
        runtime = GroupRunner(group(), {"one": one, "two": two})
        runtime.start()
        await entered.wait()
        runtime.stop("one")
        await cleaning.wait()
        assert states(runtime)["one"] == "stopping"
        runtime.stop("one")
        runtime.stop("one")
        for _ in range(3):
            await asyncio.sleep(0)
            assert states(runtime)["one"] == "stopping"
            assert not cleaned.is_set()
        release.set()
        await runtime.wait()
        assert cleaned.is_set()
        assert states(runtime) == {"one": "stopped", "two": "completed"}
        assert await runtime.wait() == runtime.status()
    asyncio.run(scenario())


def test_failure_does_not_cancel_sibling_or_expose_error():
    async def scenario():
        release = asyncio.Event()
        async def fail(member):
            raise RuntimeError("sensitive adapter error")
        async def slow(member):
            await release.wait()
        runtime = GroupRunner(group(), {"one": fail, "two": slow})
        runtime.start()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert states(runtime) == {"one": "failed", "two": "running"}
        assert "sensitive" not in repr(runtime.status())
        release.set()
        await runtime.wait()
        assert states(runtime)["two"] == "completed"
    asyncio.run(scenario())


def test_cancel_observer_does_not_cancel_members_and_early_stop():
    async def scenario():
        async def run(member):
            await asyncio.Event().wait()
        runtime = GroupRunner(group(), {"one": run, "two": run})
        runtime.start()
        observer = asyncio.create_task(runtime.wait())
        await asyncio.sleep(0)
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
        assert states(runtime) == {"one": "running", "two": "running"}
        runtime.stop()
        await runtime.wait()
        assert set(states(runtime).values()) == {"stopped"}
        early = GroupRunner(group(), {"one": run, "two": run})
        early.start()
        early.stop()
        await early.wait()
        assert set(states(early).values()) == {"stopped"}
    asyncio.run(scenario())


def test_missing_runner_rejected_before_start():
    with pytest.raises(ValueError):
        GroupRunner(group(), {})


@pytest.mark.parametrize("cancel_child", [True, False])
def test_unrequested_cancellation_is_failure(cancel_child):
    async def scenario():
        async def cancelled(member):
            if cancel_child:
                task = asyncio.create_task(asyncio.sleep(1))
                task.cancel()
                await task
            raise asyncio.CancelledError("private adapter detail")
        async def done(member):
            return
        runtime = GroupRunner(group(), {"one": cancelled, "two": done})
        runtime.start()
        await runtime.wait()
        assert states(runtime) == {"one": "failed", "two": "completed"}
        assert "private" not in repr(runtime.status())
        assert await runtime.wait() == runtime.status()
    asyncio.run(scenario())


def test_duplicate_members_rejected_before_tasks_can_be_created():
    duplicate = SavedGroup("g-duplicate", "host", "socratic", None,
                           (GroupMember("one", "s-one0"), GroupMember("one", "s-one1")), "now")
    async def run(member):
        raise AssertionError("must not run")
    with pytest.raises(ValueError, match="unique"):
        GroupRunner(duplicate, {"one": run})


def test_pre_start_stop_is_a_noop_and_does_not_prevent_start():
    async def scenario():
        entered = set()
        async def run(member):
            entered.add(member.profile)
        runtime = GroupRunner(group(), {"one": run, "two": run})
        runtime.stop()
        assert set(states(runtime).values()) == {"idle"}
        runtime.start()
        await runtime.wait()
        assert entered == {"one", "two"}
        assert set(states(runtime).values()) == {"completed"}
    asyncio.run(scenario())
