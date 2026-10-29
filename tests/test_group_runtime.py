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
        assert {s.state for s in await runtime.wait()} == {"completed"}
        with pytest.raises(ValueError):
            runtime.start()
    asyncio.run(scenario())


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
        await active.wait()
        runtime.pause("one")
        assert states(runtime)["one"] == "pausing"
        human = runtime.send_human(audience=["one"], text="steer")
        boundary.set()
        await asyncio.sleep(0)
        assert states(runtime)["one"] == "paused"
        assert received == []
        runtime.resume("one")
        await runtime.wait()
        assert received == [human]
        assert states(runtime)["one"] == "completed"
        # Delivery is not proof of consumption, even after successful completion.
        assert messages.pending(gid, owner="host", member="one") == [human]
    asyncio.run(scenario())


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
        await runtime.wait()
        assert states(runtime) == {"lead": "completed", "one": "stopped", "two": "completed"}
        assert set(entered) == {"lead", "two"}
    asyncio.run(scenario())


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
