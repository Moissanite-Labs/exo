import asyncio

import pytest

from exo.core.group_messages import GroupMessages
from exo.core.group_runtime import GroupRuntime
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)
    group = groups.create(owner="host", mode="conductor", members=["lead", "one", "two"],
                          known_profiles=["lead", "one", "two"], conductor="lead")
    yield GroupMessages(groups), group.id
    store.close()


def states(runtime):
    return {s.profile: s.state for s in runtime.status()}


async def wait_state(runtime, profile, expected):
    async def poll():
        while states(runtime)[profile] != expected:
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), 2)


def test_concurrent_mailbox_binding_and_explicit_consumption(room):
    async def scenario():
        messages, gid = room
        contexts = {}
        ready = asyncio.Event()
        release = asyncio.Event()
        async def adapter(ctx):
            contexts[ctx.member.profile] = ctx
            if len(contexts) == 3:
                ready.set()
            await release.wait()
        runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
        runtime.start()
        await asyncio.wait_for(ready.wait(), 1)
        one, two, lead = (contexts[p] for p in ("one", "two", "lead"))
        reply = one.send(audience=["lead"], text="private reply")
        with pytest.raises(ValueError):
            one.send(audience=["two"], text="forbidden")
        assert await two.checkpoint() == []
        assert two.history() == []
        assert await lead.checkpoint() == [reply]
        assert await lead.checkpoint() == [reply]
        with pytest.raises(ValueError):
            two.consume(reply.id)
        lead.consume(reply.id)
        assert await lead.checkpoint() == []
        human = runtime.send_human(audience=["two"], text="new direction")
        assert await two.checkpoint() == [human]
        assert runtime.observe() == [reply, human]
        release.set()
        assert {s.state for s in await asyncio.wait_for(runtime.wait(), 2)} == {"completed"}
        with pytest.raises(ValueError):
            runtime.start()
    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_pause_waits_for_checkpoint_and_resume_delivers(room):
    async def scenario():
        messages, gid = room
        active = asyncio.Event()
        boundary = asyncio.Event()
        received = []
        async def adapter(ctx):
            if ctx.member.profile == "one":
                active.set()
                await boundary.wait()
                received.extend(await ctx.checkpoint())
        runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
        runtime.start()
        await asyncio.wait_for(active.wait(), 2)
        runtime.pause("one")
        assert states(runtime)["one"] == "pausing"
        human = runtime.send_human(audience=["one"], text="steer")
        boundary.set()
        await asyncio.sleep(0)
        assert states(runtime)["one"] == "paused"
        assert received == []
        runtime.resume("one")
        await asyncio.wait_for(runtime.wait(), 2)
        assert received == [human]
        assert states(runtime)["one"] == "completed"
        # Delivery is not proof of consumption, even after successful completion.
        assert messages.pending(gid, owner="host", member="one") == [human]
    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_stop_while_paused_and_pause_before_start(room):
    async def scenario():
        messages, gid = room
        entered = []
        async def adapter(ctx):
            entered.append(ctx.member.profile)
        runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
        runtime.pause()
        runtime.start()
        await asyncio.sleep(0)
        assert set(states(runtime).values()) == {"paused"}
        assert entered == []
        runtime.stop("one")
        assert states(runtime)["one"] == "stopping"
        runtime.resume()
        await asyncio.wait_for(runtime.wait(), 2)
        assert states(runtime) == {"lead": "completed", "one": "stopped", "two": "completed"}
        assert set(entered) == {"lead", "two"}
    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_bad_owner_adapter_and_control_fail_before_execution(room):
    messages, gid = room
    async def adapter(ctx):
        pass
    with pytest.raises(ValueError, match="no group"):
        GroupRuntime(messages, gid, owner="other", adapters={})
    with pytest.raises(ValueError, match="adapter"):
        GroupRuntime(messages, gid, owner="host", adapters={})
    runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
    for control in (runtime.pause, runtime.resume, runtime.stop):
        with pytest.raises(ValueError, match="unknown member"):
            control("other")


@pytest.mark.parametrize("prestart", [False, True])
def test_same_tick_resume_then_pause_still_blocks_work(room, prestart):
    async def scenario():
        messages, gid = room
        active, boundary = asyncio.Event(), asyncio.Event()
        entered, received = [], []
        async def adapter(ctx):
            if ctx.member.profile != "one":
                return
            if not prestart:
                active.set()
                await boundary.wait()
                received.extend(await ctx.checkpoint())
            entered.append("one")
        runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
        human = runtime.send_human(audience=["one"], text="steer")
        if prestart:
            runtime.pause("one")
        runtime.start()
        try:
            if not prestart:
                await asyncio.wait_for(active.wait(), 2)
                runtime.pause("one")
                boundary.set()
            await wait_state(runtime, "one", "paused")
            runtime.resume("one")
            runtime.pause("one")  # Clear the gate before the resolved waiter runs.
            for _ in range(3):
                await asyncio.sleep(0)
                assert states(runtime)["one"] == "paused"
                assert entered == [] and received == []
            assert messages.store._q("SELECT delivered FROM swarm_delivery")[0]["delivered"] == 0
            runtime.resume("one")
            await asyncio.wait_for(runtime.wait(), 2)
            assert entered == ["one"]
            assert received == ([] if prestart else [human])
            assert states(runtime)["one"] == "completed"
        finally:
            runtime.stop()
            await asyncio.wait_for(runtime.wait(), 2)
    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("finish", ["resume", "stop"])
def test_cancel_one_checkpoint_keeps_other_waiter_paused(room, finish):
    async def scenario():
        messages, gid = room
        active, boundary, spawned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cleaning, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        tasks, received = {}, []
        async def adapter(ctx):
            if ctx.member.profile != "one":
                return
            active.set()
            await boundary.wait()
            tasks["first"] = asyncio.create_task(ctx.checkpoint())
            tasks["second"] = asyncio.create_task(ctx.checkpoint())
            await asyncio.sleep(0)  # Both children enter their gate waits before this resumes.
            spawned.set()
            try:
                received.extend(await tasks["second"])
            finally:
                for task in tasks.values():
                    task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
                if finish == "stop":
                    cleaning.set()
                    await release.wait()
                cleaned.set()
        runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
        human = runtime.send_human(audience=["one"], text="steer")
        runtime.start()
        try:
            await asyncio.wait_for(active.wait(), 2)
            runtime.pause("one")
            boundary.set()
            await asyncio.wait_for(spawned.wait(), 2)
            assert states(runtime)["one"] == "paused"
            tasks["first"].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks["first"]
            assert not tasks["second"].done()
            assert states(runtime)["one"] == "paused"
            assert received == []
            if finish == "resume":
                runtime.resume("one")
            else:
                runtime.stop("one")
                await asyncio.wait_for(cleaning.wait(), 2)
                runtime.stop("one")
                for _ in range(3):
                    await asyncio.sleep(0)
                    assert states(runtime)["one"] == "stopping"
                    assert not cleaned.is_set()
                release.set()
            await asyncio.wait_for(runtime.wait(), 2)
            assert cleaned.is_set()
            assert states(runtime)["one"] == ("completed" if finish == "resume" else "stopped")
            assert received == ([human] if finish == "resume" else [])
            assert not runtime._paused
        finally:
            release.set()
            runtime.stop()
            await asyncio.wait_for(runtime.wait(), 2)
    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_checkpoint_repeats_oldest_100_until_explicit_consumption(room):
    async def scenario():
        messages, gid = room
        batches = []
        async def adapter(ctx):
            if ctx.member.profile == "one":
                batches.append(await ctx.checkpoint())
                batches.append(await ctx.checkpoint())
                for message in batches[0]:
                    ctx.consume(message.id)
                batches.append(await ctx.checkpoint())
        runtime = GroupRuntime(messages, gid, owner="host", adapters=dict.fromkeys(["lead", "one", "two"], adapter))
        sent = [runtime.send_human(audience=["one"], text=str(i)) for i in range(150)]
        runtime.start()
        await asyncio.wait_for(runtime.wait(), 2)
        assert batches == [sent[:100], sent[:100], sent[100:]]
        assert messages.pending(gid, owner="host", member="one") == sent[100:]
    asyncio.run(asyncio.wait_for(scenario(), 5))
