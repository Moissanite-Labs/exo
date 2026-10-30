"""GroupHost injection-layer tests. Fakes only; no CoreEngine, no live install.

Each full start/control/wait lifecycle runs inside ONE asyncio.run loop: no
throwaway start loop, no sleep-based timing; blocking members are released
with asyncio.Event instead.
"""
import asyncio
from types import SimpleNamespace

import pytest

from exo.core.group_engine_adapter import AdapterError
from exo.core.group_execution import GroupExecutions
from exo.core.group_host import GroupHost, HostError
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore

PROFILES = ["lead", "one", "two"]
AUDIENCES = {"lead": ["one"], "one": ["lead"], "two": ["lead"]}


async def ack_turn(command):
    return SimpleNamespace(stop_reason="end", error=None,
                           message=SimpleNamespace(text=lambda: "ack"))


@pytest.fixture
def room():
    store = SessionStore()
    groups = GroupStore(store)

    def factory(group):
        # Fake engines complete turns by default; tests may override run_turn.
        return {m.profile: SimpleNamespace(agent=m.profile, store=store, run_turn=ack_turn)
                for m in group.members}

    host = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=factory)
    yield store, groups, host, factory
    store.close()


def create(host, **kw):
    args = dict(mode="conductor", members=PROFILES, conductor="lead")
    args.update(kw)
    return host.create(**args)


def states(host, group_id):
    """Member state map from the structured status snapshot."""
    return {s.profile: s.state for s in host.status(group_id)["members"]}


def started(room):
    """A group whose members have finished; lifecycle kept inside one loop."""
    _, _, host, _ = room
    group = create(host)

    async def scenario():
        host.start(group.id, audiences=AUDIENCES)
        await host.wait(group.id)

    asyncio.run(scenario())
    return host, group


def test_create_restricts_members_to_injected_profiles(room):
    _, _, host, _ = room
    with pytest.raises(HostError, match="unknown group profile"):
        create(host, members=["lead", "one", "intruder"])
    with pytest.raises(HostError):
        create(host, mode="socratic", conductor="lead")
    group = create(host)
    assert group.owner == "host"
    assert {m.profile for m in group.members} == set(PROFILES)


def test_construction_rejects_missing_authority(room):
    _, groups, _, factory = room
    with pytest.raises(HostError):
        GroupHost(groups, owner=" ", known_profiles=PROFILES, engine_factory=factory)
    with pytest.raises(HostError):
        GroupHost(groups, owner="host", known_profiles=[], engine_factory=factory)
    with pytest.raises(HostError):
        GroupHost(groups, owner="host", known_profiles="lead", engine_factory=factory)
    with pytest.raises(HostError):
        GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=None)


def test_owner_mismatch_denies_every_operation(room):
    _, groups, host, factory = room
    group = create(host)
    other = GroupHost(groups, owner="other", known_profiles=PROFILES, engine_factory=factory)
    with pytest.raises(HostError, match="no such group"):
        other.start(group.id, audiences=AUDIENCES)
    for call in (lambda: other.status(group.id),
                 lambda: other.pause(group.id),
                 lambda: other.resume(group.id),
                 lambda: other.stop(group.id),
                 lambda: other.send(group.id, audience=["one"], text="hi")):
        with pytest.raises(HostError, match="no such group"):
            call()
    # A started runtime of the real owner stays invisible to another owner.
    asyncio.run(run_start(host, group.id))
    with pytest.raises(HostError, match="no such group"):
        other.stop(group.id)


async def run_start(host, group_id):
    host.start(group_id, audiences=AUDIENCES)
    await host.wait(group_id)


def test_unknown_group_and_unknown_profile_controls(room):
    _, _, host, _ = room
    group = create(host)
    with pytest.raises(HostError, match="no such group"):
        host.status("g-nope")
    with pytest.raises(HostError, match="non-empty string"):
        host.pause(" ")
    with pytest.raises(HostError, match="not started"):
        host.pause(group.id)
    asyncio.run(run_start(host, group.id))
    with pytest.raises(HostError, match="unknown member"):
        host.pause(group.id, profile="ghost")
    with pytest.raises(HostError, match="unknown member"):
        host.stop(group.id, profile="ghost")


def test_duplicate_start_rejected_and_claim_retained(room):
    host, group = started(room)
    with pytest.raises(HostError, match="already started"):
        asyncio.run(run_start(host, group.id))
    # A fresh host object cannot retry either: the durable claim survives.
    _, groups, _, factory = room
    second = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=factory)
    with pytest.raises(HostError, match="already claimed"):
        asyncio.run(run_start(second, group.id))


def test_start_rejects_members_outside_injected_profiles(room):
    """Allowlist reduced after create: start must fail before factory or claim."""
    _, groups, host, _ = room
    group = create(host)
    called = []

    def spying_factory(g):
        called.append(True)
        return {}

    narrowed = GroupHost(groups, owner="host", known_profiles=["lead", "one"],
                         engine_factory=spying_factory)
    with pytest.raises(HostError, match="outside the injected profile set"):
        asyncio.run(run_start(narrowed, group.id))
    assert not called  # factory never invoked
    assert GroupExecutions(groups).get(group.id, owner="host") is None  # no claim
    snap = host.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}


def test_status_reports_durable_receipt_and_reconciliation(room):
    """No local runtime means no claimed liveness; the receipt is surfaced."""
    _, groups, host, factory = room
    group = create(host)
    asyncio.run(run_start(host, group.id))
    receipt = GroupExecutions(groups).get(group.id, owner="host")
    assert receipt is not None and receipt.state == "settled"

    fresh = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=factory)
    snap = fresh.status(group.id)
    assert snap["local_runtime"] is False
    assert snap["receipt"] is not None and snap["receipt"].id == receipt.id
    assert snap["reconciliation_required"] is False  # settled claim
    assert {s.profile: s.state for s in snap["members"]} == {
        p: "completed" for p in PROFILES}

    # Simulate a crashed process leaving an active durable claim.
    def force_active(db):
        db.execute("UPDATE swarm_executions SET state='active', finished=NULL"
                   " WHERE group_id=?", (group.id,))

    groups.sessions._tx(force_active)
    snap = fresh.status(group.id)
    assert snap["reconciliation_required"] is True
    assert {s.state for s in snap["members"]} == {"unknown"}  # never "idle"
    with pytest.raises(HostError, match="already claimed"):
        asyncio.run(run_start(fresh, group.id))


def test_failed_factory_leaves_no_running_work(room):
    store, groups, host, _ = room
    group = create(host)

    def broken(g):
        raise RuntimeError("factory refused")

    refusing = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=broken)
    with pytest.raises(RuntimeError):
        asyncio.run(run_start(refusing, group.id))
    # No runtime, no claim: fixing the inputs allows the single real start.
    snap = host.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}

    async def scenario():
        host.start(group.id, audiences=AUDIENCES)
        assert states(host, group.id) == {p: "queued" for p in PROFILES}
        host.stop(group.id)
        result = {s.profile: s.state for s in await host.wait(group.id)}
        assert set(result.values()) <= {"completed", "stopped", "failed"}

    asyncio.run(scenario())

    store2 = SessionStore()
    groups2 = GroupStore(store2)
    try:
        group2 = groups2.create(owner="host", mode="conductor", members=PROFILES,
                                known_profiles=PROFILES, conductor="lead")

        def incomplete(g):
            return {m.profile: SimpleNamespace(agent=m.profile, store=store2)
                    for m in g.members if m.profile != "two"}

        partial = GroupHost(groups2, owner="host", known_profiles=PROFILES,
                            engine_factory=incomplete)
        with pytest.raises(HostError):
            asyncio.run(partial.start(group2.id, audiences=AUDIENCES))
        assert groups2.get(group2.id, owner="host") is not None
        assert GroupExecutions(groups2).get(group2.id, owner="host") is None
    finally:
        store2.close()


def test_controls_pause_resume_stop_and_send(room):
    """One lifecycle, one loop: pausing verified via runtime gates, not sleeps."""
    _, _, host, _ = room
    group = create(host)

    async def scenario():
        host.send(group.id, audience=["two"], text="question")
        host.start(group.id, audiences=AUDIENCES)
        host.pause(group.id)  # pause before yielding: adapters hold at the gate
        gate_wait = asyncio.create_task(host._runtimes[group.id]._gates["lead"].wait())
        await asyncio.sleep(0)  # let tasks reach the gate; no time-based timing
        snapshot = {s.profile: s.state for s in host.status(group.id)["members"]}
        assert set(snapshot) == set(PROFILES)
        assert set(snapshot.values()) <= {"queued", "running", "pausing", "paused"}
        assert gate_wait.done() is False  # still paused, not a sleep heuristic
        host.pause(group.id, profile="one")  # targeted pause accepted
        host.resume(group.id)
        await asyncio.wait_for(gate_wait, timeout=5)  # gate released by resume
        done = asyncio.create_task(host.wait(group.id))
        while not any(t.done() for t in host._runtimes[group.id]._runner._tasks.values()):
            await asyncio.sleep(0)  # let at least one member finish naturally
        host.stop(group.id, profile="one")  # targeted stop accepted
        host.stop(group.id)
        result = {s.profile: s.state for s in await done}
        assert set(result) == set(PROFILES)
        assert set(result.values()) <= {"completed", "stopped"}

    asyncio.run(scenario())


def test_fake_adapter_full_lifecycle(room):
    _, _, host, factory = room
    group = create(host)

    host2 = GroupHost(host._groups, owner="host", known_profiles=PROFILES,
                      engine_factory=factory)

    async def scenario():
        host2.send(group.id, audience=["two"], text="question")
        host2.start(group.id, audiences=AUDIENCES)
        assert states(host2, group.id) == {p: "queued" for p in PROFILES}
        result = {s.profile: s.state for s in await host2.wait(group.id)}
        assert set(result.values()) == {"completed"}
        # Successful turns acknowledged input and routed replies to the audience.
        history = host2._messages.observe(group.id, owner="host")
        assert any(m.sender_kind == "human" for m in history)
        assert any(m.sender == "two" and m.audience == ("lead",) for m in history)

    asyncio.run(scenario())


def test_failed_turn_leaves_inputs_pending_without_retry(room):
    _, _, host, factory = room
    group = create(host)

    async def failing(command):
        return SimpleNamespace(stop_reason="error", error="provider failed", message=None)

    def bad_engines(g):
        engines = factory(g)
        for engine in engines.values():
            engine.run_turn = failing
        return engines

    host2 = GroupHost(host._groups, owner="host", known_profiles=PROFILES,
                      engine_factory=bad_engines)

    async def scenario():
        note = host2.send(group.id, audience=["two"], text="question")
        host2.start(group.id, audiences=AUDIENCES)
        result = {s.profile: s.state for s in await host2.wait(group.id)}
        assert set(result.values()) == {"failed"}
        pending = host2._messages.pending(group.id, owner="host", member="two")
        assert note in pending  # no acknowledgement, no retry

    asyncio.run(scenario())


def test_invalid_audience_rejected_before_claim(room):
    _, groups, host, _ = room
    group = create(host)
    with pytest.raises((HostError, AdapterError)):
        asyncio.run(host.start(group.id,
                               audiences={"lead": ["one"], "one": ["lead"], "two": ["one"]}))
    snap = host.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}
    assert GroupExecutions(groups).get(group.id, owner="host") is None


def test_aclose_plain_callable_factory_backward_compatible(room):
    """A plain callable factory has no aclose; host close still settles runtimes."""
    _, _, host, _ = room
    group = create(host)

    async def scenario():
        host.start(group.id, audiences=AUDIENCES)
        await host.aclose()
        assert host._closed is True
        assert host._runtimes == {}
        await host.aclose()  # idempotent: no error, still closed

    asyncio.run(scenario())


def test_aclose_awaits_factory_aclose_after_runtime_settlement(room):
    """Engine release happens only once no runtime can still be running."""
    _, groups, host, factory = room
    order = []

    class Factory:
        def __call__(self, g):
            return factory(g)

        async def aclose(self):
            order.append("factory_close")

    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=Factory())
    group2 = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group2.id, audiences=AUDIENCES)
        await host2.wait(group2.id)  # runtime settles before close
        await host2.aclose()
        assert host2._closed is True

    asyncio.run(scenario())
    assert order == ["factory_close"]  # factory closed only after settlement


def test_aclose_factory_close_failure_is_retriable(room):
    """A failed factory close keeps runtimes and does not flip closed."""
    _, groups, _, factory = room
    attempts = []

    class Factory:
        def __call__(self, g):
            return factory(g)

        async def aclose(self):
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("close failed")

    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=Factory())
    group2 = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group2.id, audiences=AUDIENCES)
        await host2.wait(group2.id)
        with pytest.raises(HostError, match="engine factory close failed"):
            await host2.aclose()
        assert host2._closed is False  # not closed; runtimes retained for retry
        assert group2.id in host2._runtimes
        # Retry: runtimes already settled (wait returns immediately), close succeeds.
        await host2.aclose()
        assert host2._closed is True
        assert host2._runtimes == {}

    asyncio.run(scenario())
    assert len(attempts) == 2


def test_aclose_attempts_all_runtimes_despite_individual_error(room):
    """A failing runtime must not prevent settling the other runtimes."""
    _, groups, host, _ = room
    group = create(host)
    group2 = host.create(mode="conductor", members=PROFILES, conductor="lead")
    stopped = []

    class Runtime:
        def __init__(self, gid):
            self.gid = gid

        def stop(self):
            stopped.append(self.gid)
            if self.gid == group.id:
                raise RuntimeError("stop failed")

        async def wait(self):
            return ()

    host._runtimes[group.id] = Runtime(group.id)
    host._runtimes[group2.id] = Runtime(group2.id)

    async def scenario():
        with pytest.raises(HostError, match="runtime settlement failed"):
            await host.aclose()
        # Both runtimes attempted despite the first raising.
        assert set(stopped) == {group.id, group2.id}
        assert host._closed is False  # fail closed: retained for retry

    asyncio.run(scenario())


def test_aclose_rejects_new_start_and_create(room):
    """Once closing begins, no new create/start is accepted."""
    _, _, host, _ = room
    group = create(host)

    async def scenario():
        await host.aclose()  # close with no runtimes: clean, closed
        assert host._closed is True
        with pytest.raises(HostError, match="closing or closed"):
            host.create(mode="conductor", members=PROFILES, conductor="lead")
        with pytest.raises(HostError, match="closing or closed"):
            host.start(group.id, audiences=AUDIENCES)

    asyncio.run(scenario())


def test_aclose_awaits_active_task_cancellation_before_close(room):
    """Engines are never released while a runtime task can still be running."""
    _, groups, host, factory = room
    settled_before_close = []

    class Factory:
        def __call__(self, g):
            return factory(g)

        async def aclose(self):
            # Record whether every member had already settled by close time.
            settled_before_close.append(
                all(t.done() for t in host2._runtimes[group2.id]._runner._tasks.values()))

    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=Factory())
    group2 = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group2.id, audiences=AUDIENCES)
        await host2.aclose()  # stops then awaits full settlement before factory close

    asyncio.run(scenario())
    assert settled_before_close == [True]


def test_aclose_concurrent_callers_share_one_cleanup(room):
    """Concurrent aclose() calls invoke the factory cleanup exactly once."""
    _, groups, _, factory = room
    calls = []
    gate = asyncio.Event()
    release = asyncio.Event()

    class Factory:
        def __call__(self, g):
            return factory(g)

        async def aclose(self):
            calls.append(True)
            gate.set()
            await release.wait()

    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=Factory())
    group2 = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group2.id, audiences=AUDIENCES)
        await host2.wait(group2.id)
        t1 = asyncio.create_task(host2.aclose())
        t2 = asyncio.create_task(host2.aclose())
        # Deterministic: cleanup is blocked inside the factory close before
        # we let it finish, so both callers are waiters on the shared task.
        await gate.wait()
        release.set()
        await asyncio.gather(t1, t2)
        assert host2._closed is True
        assert host2._cleanup_task is None  # dropped synchronously on success

    asyncio.run(scenario())
    assert len(calls) == 1  # one shared cleanup across both concurrent callers


def test_aclose_cancelled_waiter_cleanup_completes(room):
    """A cancelled aclose() waiter does not abort the shared cleanup task."""
    _, groups, _, factory = room
    order = []
    gate = asyncio.Event()
    release = asyncio.Event()

    class Factory:
        def __call__(self, g):
            return factory(g)

        async def aclose(self):
            order.append("factory_close_entered")
            gate.set()
            await release.wait()
            order.append("factory_close_done")

    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=Factory())
    group2 = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group2.id, audiences=AUDIENCES)
        await host2.wait(group2.id)
        t1 = asyncio.create_task(host2.aclose())
        await gate.wait()  # shared cleanup is parked inside the factory close
        t1.cancel()  # caller cancels; shield keeps the cleanup task alive
        try:
            await t1
            raise AssertionError("waiter cancellation must propagate")
        except asyncio.CancelledError:
            pass
        assert host2._closed is False  # cancellation says nothing about cleanup
        assert host2._cleanup_task is not None  # cleanup still running
        release.set()  # let the shared cleanup finish
        # Explicit later aclose() awaits the still-running shared task.
        await host2.aclose()
        assert host2._closed is True

    asyncio.run(scenario())
    # Safe ordering: the factory close completed exactly once, after entry.
    assert order == ["factory_close_entered", "factory_close_done"]


def test_aclose_failed_shared_cleanup_explicit_retry(room):
    """A failed shared cleanup is dropped; an explicit retry re-attempts it."""
    _, groups, _, factory = room
    calls = []
    gate = asyncio.Event()

    class Factory:
        def __init__(self):
            self.fail = True

        def __call__(self, g):
            return factory(g)

        async def aclose(self):
            calls.append(True)
            gate.set()
            if self.fail:
                raise RuntimeError("factory close failed once")

    fac = Factory()
    host2 = GroupHost(groups, owner="host", known_profiles=PROFILES,
                      engine_factory=fac)
    group2 = host2.create(mode="conductor", members=PROFILES, conductor="lead")

    async def scenario():
        host2.start(group2.id, audiences=AUDIENCES)
        await host2.wait(group2.id)
        t1 = asyncio.create_task(host2.aclose())
        t2 = asyncio.create_task(host2.aclose())
        await gate.wait()  # both callers are waiters on the one shared attempt
        # The shared failure surfaces in every waiter.
        for t in (t1, t2):
            try:
                await t
                raise AssertionError("failed shared cleanup must raise")
            except HostError:
                pass
        assert host2._closed is False
        assert host2._cleanup_task is None  # failed attempt dropped once retrieved
        assert len(host2._runtimes) == 1  # runtimes retained across the failure
        fac.fail = False  # explicit retry re-attempts the cleanup
        await host2.aclose()
        assert host2._closed is True

    asyncio.run(scenario())
    assert len(calls) == 2  # exactly one call per attempt: shared, then retry
