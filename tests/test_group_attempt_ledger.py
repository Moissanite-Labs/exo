"""Offline ledger tests: independent on-disk connections, no engine or grants."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
from threading import Barrier

import pytest

from exo.core.group_attempt_ledger import (
    AdmissionDenied, AttemptLedger, IdentityConflict, InvalidTransition,
    OwnershipError, RequestConflict, SchemaConflict, Scope, StaleFence, TaskKey,
)
from exo.core.group_plan_execution import PlanExecutions
from exo.core.group_supervision import SupervisionPolicy, TaskIdentity
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


@pytest.fixture
def room(tmp_path):
    path = tmp_path / 'sessions.sqlite'
    store = SessionStore(path)
    groups = GroupStore(store)
    group = groups.create(owner='host', mode='socratic', members=['one', 'two'], known_profiles=['one', 'two'])
    # Seed exactly the legacy schema without activating a runtime.
    PlanExecutions(groups)._ensure_schema()
    store._q("INSERT INTO swarm_plans(plan_id,group_id,owner,execution_id,state,created)"
             " VALUES ('plan',?,'host','execution','running','fixture')", (group.id,))
    store._q("INSERT INTO swarm_plan_tasks(plan_id,task_id,attempt,group_id,profile,state)"
             " VALUES ('plan','task',1,?,'one','pending')", (group.id,))
    scope = Scope(group.id, 'host', 'plan', 'execution')
    key = TaskKey(scope, TaskIdentity('plan', 'task', 'one'))
    ledger = AttemptLedger(groups)
    yield path, store, ledger, key
    store.close()


def initialize(ledger, key, **kwargs):
    ledger.initialize(key.scope, plan_digest='digest', tasks=(key.identity,), request_id='init', **kwargs)


def reserve(ledger, key, request='reserve'):
    return ledger.reserve_initial(key, holder_id='holder', request_id=request)


def test_default_off_and_lazy_schema(room):
    _, store, ledger, key = room
    before = [tuple(r) for r in store._q('SELECT name,sql FROM sqlite_master')]
    with pytest.raises(AdmissionDenied):
        ledger.read(key)
    assert before == [tuple(r) for r in store._q('SELECT name,sql FROM sqlite_master')]
    with pytest.raises(AdmissionDenied):
        initialize(ledger, key, policy=SupervisionPolicy(1))
    assert before == [tuple(r) for r in store._q('SELECT name,sql FROM sqlite_master')]
    initialize(ledger, key)
    initialize(ledger, key)
    assert ledger.read(key).latest_number == 0
    assert store._q("SELECT state FROM swarm_plan_tasks")[0][0] == 'pending'


def test_reservation_reopen_dispatch_is_one_shot(room):
    path, store, ledger, key = room
    initialize(ledger, key)
    first = reserve(ledger, key)
    store.close()
    reopened = SessionStore(path)
    try:
        ledger = AttemptLedger(GroupStore(reopened))
        assert reserve(ledger, key) == first
        assert ledger.read(key).latest_number == 1
        with pytest.raises(AdmissionDenied):
            reserve(ledger, key, 'other')
        with pytest.raises(RequestConflict):
            ledger.reserve_initial(key, holder_id='other', request_id='reserve')
        assert ledger.mark_dispatch(first.fence, request_id='dispatch') is True
        assert ledger.mark_dispatch(first.fence, request_id='dispatch') is False
        with pytest.raises(InvalidTransition):
            ledger.mark_dispatch(first.fence, request_id='another-dispatch')
        initialize(ledger, key)
        assert ledger.read(key).latest_number == 1
    finally:
        reopened.close()


def race(path, call):
    barrier = Barrier(2)
    def worker(i):
        store = SessionStore(path)
        ledger = AttemptLedger(GroupStore(store))
        try:
            barrier.wait(timeout=10)
            try:
                return call(ledger, i)
            except (AdmissionDenied, InvalidTransition, StaleFence) as exc:
                return exc
        finally:
            store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(worker, range(2)))


def test_independent_reservation_and_dispatch_races(room):
    path, store, ledger, key = room
    initialize(ledger, key)
    results = race(path, lambda l, i: reserve(l, key, f'r{i}'))
    winners = [r for r in results if not isinstance(r, Exception)]
    assert len(winners) == 1
    results = race(path, lambda l, i: l.mark_dispatch(winners[0].fence, request_id=f'd{i}'))
    assert sum(r is True for r in results) == 1
    assert store._q('SELECT count(*) FROM swarm_supervision_attempts')[0][0] == 1
    assert ledger.read(key).epoch == 1


def test_concurrent_initialize(room):
    path, _, ledger, key = room
    assert race(path, lambda l, i: initialize(l, key)) == [None, None]
    assert ledger.read(key).latest_number == 0


@pytest.mark.parametrize('task_control', [True, False])
def test_pause_stop_sticky_and_reopen(room, task_control):
    path, _, ledger, key = room
    initialize(ledger, key)
    target = key.identity.task_id if task_control else None
    assert ledger.set_control(key.scope, task_id=target, expected_revision=0, paused=True, request_id='pause') == 1
    with pytest.raises(AdmissionDenied):
        reserve(ledger, key)
    assert ledger.set_control(key.scope, task_id=target, expected_revision=1, stopped=True, request_id='stop') == 2
    with pytest.raises(StaleFence):
        ledger.set_control(key.scope, task_id=target, expected_revision=1, paused=False, request_id='stale')
    with pytest.raises(InvalidTransition):
        ledger.set_control(key.scope, task_id=target, expected_revision=2, stopped=False, request_id='undo')
    ledger.set_control(key.scope, task_id=target, expected_revision=2, paused=False, request_id='resume')
    reopened = SessionStore(path)
    try:
        other = AttemptLedger(GroupStore(reopened))
        assert other.read(key).stop_requested
        assert not other.read(key).paused
        with pytest.raises(AdmissionDenied):
            reserve(other, key)
    finally:
        reopened.close()


def test_stop_dispatch_race(room):
    path, _, ledger, key = room
    initialize(ledger, key)
    fence = reserve(ledger, key).fence
    results = race(path, lambda l, i: l.mark_dispatch(fence, request_id='dispatch') if i == 0 else
                   l.set_control(key.scope, expected_revision=0, stopped=True, request_id='stop'))
    assert results[1] == 1
    assert results[0] is True or isinstance(results[0], AdmissionDenied)
    assert ledger.read(key).stop_requested
    with pytest.raises((AdmissionDenied, InvalidTransition)):
        ledger.mark_dispatch(fence, request_id='new-dispatch')


def test_uncertain_fences_all_old_work_and_persists(room):
    path, _, ledger, key = room
    initialize(ledger, key)
    fence = reserve(ledger, key).fence
    ledger.mark_dispatch(fence, request_id='dispatch')
    ledger.mark_uncertain(fence, reason='owner_lost', request_id='uncertain')
    reopened = SessionStore(path)
    try:
        other = AttemptLedger(GroupStore(reopened))
        snap = other.read(key)
        assert (snap.latest_number, snap.epoch, snap.reconciliation_required) == (1, 2, True)
        for req in ['dispatch', 'new']:
            with pytest.raises(StaleFence):
                other.mark_dispatch(fence, request_id=req)
        with pytest.raises(StaleFence):
            other.mark_uncertain(fence, reason='owner_lost', request_id='uncertain')
        with pytest.raises(AdmissionDenied):
            reserve(other, key, 'takeover')
        assert reserve(other, key).fence == fence  # receipt, not permission
    finally:
        reopened.close()


@pytest.mark.parametrize('field,value,error', [
    ('owner', 'intruder', OwnershipError), ('execution_id', 'new', IdentityConflict),
    ('group_id', 'missing', OwnershipError),
])
def test_scope_immutable(room, field, value, error):
    _, _, ledger, key = room
    initialize(ledger, key)
    changed = replace(key, scope=replace(key.scope, **{field: value}))
    with pytest.raises(error):
        reserve(ledger, changed)


def test_forged_identity_policy_and_fence(room):
    _, store, ledger, key = room
    initialize(ledger, key)
    fence = reserve(ledger, key).fence
    with pytest.raises(IdentityConflict):
        ledger.read(replace(key, identity=replace(key.identity, profile='two')))
    with pytest.raises(IdentityConflict):
        ledger.initialize(key.scope, plan_digest='changed', tasks=(key.identity,), request_id='new')
    for field, value in [('number', 2), ('epoch', 2), ('holder_id', 'other')]:
        with pytest.raises(StaleFence):
            ledger.mark_dispatch(replace(fence, **{field: value}), request_id='bad')
    store._q("UPDATE swarm_plans SET execution_id='changed'")
    with pytest.raises(IdentityConflict):
        ledger.mark_dispatch(fence, request_id='dispatch')


@pytest.mark.parametrize('mutation', ["state='running'", "state='completed'", "started='time'"])
def test_no_adoption_and_schema_rollback(room, mutation):
    _, store, ledger, key = room
    store._q(f'UPDATE swarm_plan_tasks SET {mutation}')
    with pytest.raises(AdmissionDenied):
        initialize(ledger, key)
    assert not store._q("SELECT name FROM sqlite_master WHERE name LIKE 'swarm_supervision_%'")


def test_failed_mutation_rolls_back_attempt_and_request(room, monkeypatch):
    _, store, ledger, key = room
    initialize(ledger, key)
    original = ledger._save
    def fail(*args):
        original(*args)
        raise sqlite3.OperationalError('injected before commit')
    monkeypatch.setattr(ledger, '_save', fail)
    with pytest.raises(sqlite3.OperationalError):
        reserve(ledger, key)
    assert ledger.read(key).latest_number == 0
    assert store._q('SELECT count(*) FROM swarm_supervision_attempts')[0][0] == 0
    assert store._q('SELECT count(*) FROM swarm_supervision_requests')[0][0] == 1
    monkeypatch.setattr(ledger, '_save', original)
    assert reserve(ledger, key).fence.epoch == 1


def test_unknown_schema_version_and_shape_fail_closed(room):
    _, store, ledger, key = room
    initialize(ledger, key)
    store._q("UPDATE swarm_supervision_meta SET component='future'")
    with pytest.raises(SchemaConflict):
        reserve(ledger, key)
    store._q("UPDATE swarm_supervision_meta SET component='initial_attempt'")
    store._q('ALTER TABLE swarm_supervision_tasks ADD COLUMN surprise TEXT')
    with pytest.raises(SchemaConflict):
        initialize(ledger, key)


def test_legacy_changes_block_dispatch(room):
    _, store, ledger, key = room
    initialize(ledger, key)
    fence = reserve(ledger, key).fence
    store._q("UPDATE swarm_plan_tasks SET state='running'")
    with pytest.raises(AdmissionDenied):
        ledger.mark_dispatch(fence, request_id='dispatch')
    store._q("UPDATE swarm_plan_tasks SET state='pending'")
    store._q("UPDATE swarm_plans SET state='settled'")
    with pytest.raises(AdmissionDenied):
        ledger.mark_dispatch(fence, request_id='dispatch')


@pytest.mark.parametrize('operation', ['dispatch', 'control', 'uncertain'])
def test_each_mutation_rolls_back_before_commit(room, monkeypatch, operation):
    path, _, ledger, key = room
    initialize(ledger, key)
    fence = reserve(ledger, key).fence
    before = ledger.read(key)
    original = ledger._save
    def fail(*args):
        original(*args)
        raise RuntimeError('injected')
    monkeypatch.setattr(ledger, '_save', fail)
    with pytest.raises(RuntimeError):
        if operation == 'dispatch':
            ledger.mark_dispatch(fence, request_id='mutation')
        elif operation == 'control':
            ledger.set_control(key.scope, expected_revision=0, stopped=True, request_id='mutation')
        else:
            ledger.mark_uncertain(fence, reason='owner_lost', request_id='mutation')
    reopened = SessionStore(path)
    try:
        other = AttemptLedger(GroupStore(reopened))
        assert other.read(key) == before
        assert not reopened._q("SELECT 1 FROM swarm_supervision_requests WHERE request_id='mutation'")
        assert other.mark_dispatch(fence, request_id='mutation') is True
    finally:
        reopened.close()


def test_dispatch_receipt_reopen_never_reissues_transition(room):
    path, _, ledger, key = room
    initialize(ledger, key)
    fence = reserve(ledger, key).fence
    assert ledger.mark_dispatch(fence, request_id='dispatch')
    reopened = SessionStore(path)
    try:
        other = AttemptLedger(GroupStore(reopened))
        assert other.mark_dispatch(fence, request_id='dispatch') is False
        assert other.read(key).latest_attempt == 'dispatch_intent'
    finally:
        reopened.close()


def test_control_replay_and_changed_payload(room):
    _, _, ledger, key = room
    initialize(ledger, key)
    args = dict(expected_revision=0, stopped=True, request_id='stop')
    assert ledger.set_control(key.scope, **args) == 1
    assert ledger.set_control(key.scope, **args) == 1
    with pytest.raises(RequestConflict):
        ledger.set_control(key.scope, **{**args, 'stopped': False})
    assert ledger.read(key).plan_revision == 1


def test_partial_schema_rejected_not_repaired(room):
    _, store, ledger, key = room
    store._q('CREATE TABLE swarm_supervision_meta(component TEXT,version INTEGER)')
    with pytest.raises(SchemaConflict):
        initialize(ledger, key)
    assert len(store._q("SELECT name FROM sqlite_master WHERE name LIKE 'swarm_supervision_%'")) == 1


def test_unknown_numeric_version_rejected(room):
    _, store, ledger, key = room
    initialize(ledger, key)
    store._q('PRAGMA ignore_check_constraints=ON')
    store._q('UPDATE swarm_supervision_meta SET version=2')
    store._q('PRAGMA ignore_check_constraints=OFF')
    with pytest.raises(SchemaConflict):
        reserve(ledger, key)


def test_missing_task_set_and_profile_change_refused(room):
    _, store, ledger, key = room
    with pytest.raises(AdmissionDenied):
        ledger.initialize(key.scope, plan_digest='digest', tasks=(replace(key.identity, profile='two'),),
                          request_id='init')
    initialize(ledger, key)
    store._q("UPDATE swarm_plan_tasks SET profile='two'")
    with pytest.raises(IdentityConflict):
        reserve(ledger, key)
