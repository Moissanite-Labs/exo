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


def test_stop_receipt_waits_for_cleanup_and_is_not_repeated():
    async def scenario():
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        async def one(member):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
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
        await asyncio.sleep(0)
        assert states(runtime)["one"] == "stopping"
        release.set()
        await runtime.wait()
        assert states(runtime) == {"one": "stopped", "two": "completed"}
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
