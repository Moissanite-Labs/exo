"""GroupHost injection-layer tests. Fakes only; no CoreEngine, no live install.

Each full start/control/wait lifecycle runs inside ONE asyncio.run loop: no
throwaway start loop, no sleep-based timing; blocking members are released
with asyncio.Event instead.
"""
import asyncio
from types import SimpleNamespace

import pytest

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
    await asyncio.wait_for(host.wait(group_id), timeout=5)


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
    with pytest.raises(HostError, match="^engine factory failed$"):
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
        async def partial_start():
            with pytest.raises(HostError, match="one engine and audience"):
                partial.start(group2.id, audiences=AUDIENCES)

        asyncio.run(partial_start())
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
        note = host2.send(group.id, audience=PROFILES, text="question")
        host2.start(group.id, audiences=AUDIENCES)
        result = {s.profile: s.state for s in await host2.wait(group.id)}
        assert set(result.values()) == {"failed"}
        pending = host2._messages.pending(group.id, owner="host", member="two")
        assert note in pending  # no acknowledgement, no retry

    asyncio.run(scenario())


def test_invalid_audience_rejected_before_claim(room):
    _, groups, host, _ = room
    group = create(host)
    async def invalid_start():
        with pytest.raises(HostError, match="worker must target conductor"):
            host.start(group.id,
                       audiences={"lead": ["one"], "one": ["lead"], "two": ["one"]})

    asyncio.run(invalid_start())
    snap = host.status(group.id)
    assert snap["receipt"] is None and snap["reconciliation_required"] is False
    assert {s.state for s in snap["members"]} == {"idle"}
    assert GroupExecutions(groups).get(group.id, owner="host") is None


@pytest.mark.parametrize("settled", [False, True])
def test_existing_receipt_rejects_before_factory(room, settled):
    _, groups, host, _ = room
    group = create(host)
    executions = GroupExecutions(groups)
    receipt = executions.claim(group.id, owner="host")
    if settled:
        executions.finish(group.id, owner="host", execution_id=receipt.id,
                          members={p: "completed" for p in PROFILES})
    expected = executions.get(group.id, owner="host")
    calls = []

    def factory(g):
        calls.append(g.id)
        raise AssertionError("existing receipts must not construct engines")

    fresh = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=factory)

    async def scenario():
        with pytest.raises(HostError, match="already claimed"):
            fresh.start(group.id, audiences=AUDIENCES)
        assert fresh.status(group.id)["local_runtime"] is False

    asyncio.run(scenario())
    assert not calls
    assert executions.get(group.id, owner="host") == expected


@pytest.mark.parametrize("error", [ValueError, RuntimeError, TypeError])
def test_factory_errors_have_fixed_message_and_no_claim(room, error):
    _, groups, host, _ = room
    group = create(host)

    def factory(g):
        raise error("factory implementation detail")

    broken = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=factory)

    async def scenario():
        with pytest.raises(HostError, match="^engine factory failed$"):
            broken.start(group.id, audiences=AUDIENCES)
        assert broken.status(group.id)["local_runtime"] is False
        assert GroupExecutions(groups).get(group.id, owner="host") is None

    asyncio.run(scenario())
    asyncio.run(run_start(host, group.id))


def test_start_outside_loop_rejects_before_factory_and_claim(room):
    _, groups, host, factory = room
    group = create(host)
    calls = []

    def counting(g):
        calls.append(g.id)
        return factory(g)

    fresh = GroupHost(groups, owner="host", known_profiles=PROFILES, engine_factory=counting)
    with pytest.raises(HostError, match="running event loop"):
        fresh.start(group.id, audiences=AUDIENCES)
    assert not calls
    assert GroupExecutions(groups).get(group.id, owner="host") is None
    assert fresh.status(group.id)["local_runtime"] is False
    asyncio.run(run_start(fresh, group.id))
    assert calls == [group.id]


def test_invalid_factory_result_has_fixed_message(room):
    _, groups, host, _ = room
    group = create(host)
    broken = GroupHost(groups, owner="host", known_profiles=PROFILES,
                       engine_factory=lambda g: [object()])

    async def scenario():
        with pytest.raises(HostError, match="^engine factory failed$"):
            broken.start(group.id, audiences=AUDIENCES)
        assert GroupExecutions(groups).get(group.id, owner="host") is None
        assert broken.status(group.id)["local_runtime"] is False

    asyncio.run(scenario())


def test_partial_start_failure_keeps_claim_without_cleanup_policy(room, monkeypatch):
    from exo.core.group_runner import GroupRunner

    _, groups, host, _ = room
    group = create(host)

    def fail_start(runner):
        raise RuntimeError("task setup failed")

    monkeypatch.setattr(GroupRunner, "start", fail_start)

    async def scenario():
        with pytest.raises(RuntimeError, match="task setup failed"):
            host.start(group.id, audiences=AUDIENCES)
        snapshot = host.status(group.id)
        assert snapshot["receipt"].state == "active"
        assert snapshot["reconciliation_required"] is True
        assert snapshot["local_runtime"] is False
        assert {s.state for s in snapshot["members"]} == {"unknown"}
        with pytest.raises(HostError, match="already claimed"):
            host.start(group.id, audiences=AUDIENCES)

    asyncio.run(scenario())


def test_claim_race_after_preflight_still_rejects_duplicate(tmp_path):
    """A second connection claims inside the first factory: preflight is not a lock."""
    path = tmp_path / "groups.sqlite"
    first_store, second_store = SessionStore(path), SessionStore(path)
    try:
        first_groups, second_groups = GroupStore(first_store), GroupStore(second_store)
        calls = []

        def engines(g, store):
            calls.append(store)
            return {m.profile: SimpleNamespace(agent=m.profile, store=store, run_turn=ack_turn)
                    for m in g.members}

        second = GroupHost(second_groups, owner="host", known_profiles=PROFILES,
                           engine_factory=lambda g: engines(g, second_store))

        def racing(g):
            second.start(g.id, audiences=AUDIENCES)
            return engines(g, first_store)

        first = GroupHost(first_groups, owner="host", known_profiles=PROFILES,
                          engine_factory=racing)
        group = create(first)

        async def scenario():
            with pytest.raises(HostError, match="already claimed"):
                first.start(group.id, audiences=AUDIENCES)
            receipt = second.status(group.id)["receipt"]
            assert first.status(group.id)["local_runtime"] is False
            assert first.status(group.id)["reconciliation_required"] is True
            await asyncio.wait_for(second.wait(group.id), timeout=5)
            assert first.status(group.id)["receipt"].id == receipt.id
            assert {s.state for s in first.status(group.id)["members"]} == {"completed"}

        asyncio.run(scenario())
        assert calls == [second_store, first_store]
        assert len(first_store._q("SELECT * FROM swarm_executions")) == 1
    finally:
        first_store.close()
        second_store.close()


def test_other_live_host_is_unknown_and_pause_does_not_suspend_turn(room):
    _, groups, host, factory = room
    group = create(host)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(command):
            entered.set()
            await release.wait()
            return await ack_turn(command)

        def running_engines(g):
            engines = factory(g)
            engines["two"].run_turn = blocked
            return engines

        live = GroupHost(groups, owner="host", known_profiles=PROFILES,
                         engine_factory=running_engines)
        observer = GroupHost(groups, owner="host", known_profiles=PROFILES,
                             engine_factory=factory)
        live.send(group.id, audience=["two"], text="question")
        live.start(group.id, audiences=AUDIENCES)
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            snapshot = observer.status(group.id)
            assert snapshot["reconciliation_required"] is True
            assert snapshot["local_runtime"] is False
            assert {s.state for s in snapshot["members"]} == {"unknown"}
            for _ in range(2):
                live.pause(group.id, profile="two")
                assert states(live, group.id)["two"] == "pausing"
            release.set()  # No resume: an in-flight turn is not suspended by pause.
            result = await asyncio.wait_for(live.wait(group.id), timeout=5)
            assert {s.state for s in result} == {"completed"}
            assert observer.status(group.id)["reconciliation_required"] is False
        finally:
            release.set()
            live.stop(group.id)
            await asyncio.wait_for(live.wait(group.id), timeout=5)

    asyncio.run(scenario())
