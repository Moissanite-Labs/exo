"""Explicit, non-integrated attempt bookkeeping. Never execution authority.

Only initial reservations are implemented. No retries, completion, adoption or
holder takeover. See docs/SWARM-DURABLE-ATTEMPTS.md before using this adapter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import sqlite3

from exo.core.group_supervision import SupervisionPolicy, TaskIdentity
from exo.core.groups import GroupStore


class LedgerError(ValueError):
    """Closed admission; callers must not translate this into retry evidence."""


class OwnershipError(LedgerError):
    pass


class IdentityConflict(LedgerError):
    pass


class RequestConflict(LedgerError):
    pass


class StaleFence(LedgerError):
    pass


class AdmissionDenied(LedgerError):
    pass


class InvalidTransition(LedgerError):
    pass


class SchemaConflict(LedgerError):
    pass


def _text(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("expected nonempty bounded reference")


@dataclass(frozen=True)
class Scope:
    group_id: str
    owner: str
    plan_id: str
    execution_id: str

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            _text(value)


@dataclass(frozen=True)
class TaskKey:
    scope: Scope
    identity: TaskIdentity

    def __post_init__(self) -> None:
        if self.scope.plan_id != self.identity.plan_id:
            raise IdentityConflict("task plan mismatch")
        for value in asdict(self.identity).values():
            _text(value)


@dataclass(frozen=True)
class Fence:
    key: TaskKey
    number: int
    epoch: int
    holder_id: str

    def __post_init__(self) -> None:
        _text(self.holder_id)
        if any(type(n) is not int or n < 1 for n in (self.number, self.epoch)):
            raise ValueError("positive integer fence required")


@dataclass(frozen=True)
class Reservation:
    fence: Fence
    state: str
    request_id: str


@dataclass(frozen=True)
class Snapshot:
    key: TaskKey
    plan_digest: str
    policy_max_retries: int
    latest_number: int
    epoch: int
    logical_state: str
    stop_requested: bool
    paused: bool
    reconciliation_required: bool
    latest_attempt: str | None
    plan_revision: int
    task_revision: int


_SCHEMA = (
    """CREATE TABLE swarm_supervision_meta (
        component TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version=1))""",
    """CREATE TABLE swarm_supervision_plans (
        plan_id TEXT PRIMARY KEY, group_id TEXT NOT NULL, owner TEXT NOT NULL,
        execution_id TEXT NOT NULL, plan_digest TEXT NOT NULL,
        max_retries INTEGER NOT NULL CHECK(max_retries=0),
        stopped INTEGER NOT NULL DEFAULT 0 CHECK(stopped IN (0,1)),
        paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
        control_revision INTEGER NOT NULL DEFAULT 0 CHECK(control_revision>=0))""",
    """CREATE TABLE swarm_supervision_tasks (
        plan_id TEXT NOT NULL, task_id TEXT NOT NULL, profile TEXT NOT NULL,
        latest_number INTEGER NOT NULL DEFAULT 0 CHECK(latest_number IN (0,1)),
        epoch INTEGER NOT NULL DEFAULT 0 CHECK(epoch>=0), holder_id TEXT,
        logical_state TEXT NOT NULL DEFAULT 'pending'
            CHECK(logical_state IN ('pending','active','reconciliation_required')),
        stopped INTEGER NOT NULL DEFAULT 0 CHECK(stopped IN (0,1)),
        paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
        control_revision INTEGER NOT NULL DEFAULT 0 CHECK(control_revision>=0),
        reconciliation_required INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_required IN (0,1)),
        PRIMARY KEY(plan_id,task_id), FOREIGN KEY(plan_id) REFERENCES swarm_supervision_plans(plan_id))""",
    """CREATE TABLE swarm_supervision_attempts (
        plan_id TEXT NOT NULL, task_id TEXT NOT NULL, number INTEGER NOT NULL CHECK(number=1),
        epoch INTEGER NOT NULL CHECK(epoch>=1), holder_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('reserved','dispatch_intent','uncertain')),
        reservation_request_id TEXT NOT NULL, dispatch_request_id TEXT,
        uncertainty_reason TEXT CHECK(uncertainty_reason IN ('owner_lost','dispatch_outcome_unknown')),
        PRIMARY KEY(plan_id,task_id,number), UNIQUE(plan_id,task_id,epoch),
        FOREIGN KEY(plan_id,task_id) REFERENCES swarm_supervision_tasks(plan_id,task_id))""",
    """CREATE TABLE swarm_supervision_requests (
        plan_id TEXT NOT NULL, request_id TEXT NOT NULL, operation TEXT NOT NULL,
        payload_digest TEXT NOT NULL, result_json TEXT NOT NULL,
        PRIMARY KEY(plan_id,request_id))""",
)


def _normalized(sql: str) -> str:
    return ' '.join(sql.lower().split())


class AttemptLedger:
    """Trusted host adapter. Construction and reads never initialize schema.

    SessionStore supplies a per-connection RLock and BEGIN IMMEDIATE, which also
    serializes independent writers. All validation occurs inside that boundary.
    Owner strings validate stored identity; they do not authenticate the caller.
    """

    def __init__(self, groups: GroupStore):
        self.store = groups.sessions

    def _schema(self, db: sqlite3.Connection, *, create: bool = False) -> None:
        existing = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        expected = {sql.split()[2]: sql for sql in _SCHEMA}
        present = set(existing) & set(expected)
        if present and present != set(expected):
            raise SchemaConflict("partial supervision schema")
        if not present:
            if not create:
                raise AdmissionDenied("ledger not initialized")
            for sql in _SCHEMA:
                db.execute(sql)
            db.execute("INSERT INTO swarm_supervision_meta(component,version) VALUES ('initial_attempt',1)")
        else:
            if any(_normalized(existing[name]) != _normalized(sql) for name, sql in expected.items()):
                raise SchemaConflict("incompatible supervision schema")
            versions = [tuple(r) for r in db.execute("SELECT component,version FROM swarm_supervision_meta")]
            if versions != [('initial_attempt', 1)]:
                raise SchemaConflict("unsupported supervision version")

    def _legacy(self, db, scope, *, active=False):
        if not db.execute("SELECT 1 FROM swarm_groups WHERE id=? AND owner=?",
                          (scope.group_id, scope.owner)).fetchone():
            raise OwnershipError("group owner mismatch")
        p = db.execute("SELECT * FROM swarm_plans WHERE plan_id=?", (scope.plan_id,)).fetchone()
        if p is None or any(p[k] != getattr(scope, k) for k in ('group_id', 'owner', 'execution_id')):
            raise IdentityConflict("legacy execution mismatch")
        if active and p['state'] != 'running':
            raise AdmissionDenied("plan not running")

    def _plan(self, db, scope, *, active=False):
        self._schema(db)
        self._legacy(db, scope, active=active)
        p = db.execute("SELECT * FROM swarm_supervision_plans WHERE plan_id=?", (scope.plan_id,)).fetchone()
        if p is None or any(p[k] != getattr(scope, k) for k in ('group_id', 'owner', 'execution_id')):
            raise IdentityConflict("ledger execution mismatch")
        return p

    def _task(self, db, key, *, active=False):
        p = self._plan(db, key.scope, active=active)
        t = db.execute("SELECT * FROM swarm_supervision_tasks WHERE plan_id=? AND task_id=?",
                       (key.scope.plan_id, key.identity.task_id)).fetchone()
        legacy = db.execute("SELECT * FROM swarm_plan_tasks WHERE plan_id=? AND task_id=?",
                            (key.scope.plan_id, key.identity.task_id)).fetchall()
        if t is None or t['profile'] != key.identity.profile or len(legacy) != 1:
            raise IdentityConflict("task identity mismatch")
        row = legacy[0]
        if row['profile'] != t['profile'] or row['group_id'] != key.scope.group_id or row['attempt'] != 1:
            raise IdentityConflict("legacy task mismatch")
        if active and (row['state'] != 'pending' or row['started'] is not None):
            raise AdmissionDenied("legacy dispatcher touched task")
        return p, t

    @staticmethod
    def _admit(p, t):
        if any(r['stopped'] or r['paused'] for r in (p, t)) or t['reconciliation_required']:
            raise AdmissionDenied("stopped, paused or uncertain")

    @staticmethod
    def _request(db, scope, request_id, operation, payload):
        _text(request_id)
        digest = hashlib.sha256(json.dumps([asdict(scope), operation, payload],
                                          sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        old = db.execute("SELECT * FROM swarm_supervision_requests WHERE plan_id=? AND request_id=?",
                         (scope.plan_id, request_id)).fetchone()
        if old and (old['operation'] != operation or old['payload_digest'] != digest):
            raise RequestConflict("request payload changed")
        return digest, None if old is None else json.loads(old['result_json'])

    @staticmethod
    def _save(db, scope, request_id, operation, digest, result):
        db.execute("INSERT INTO swarm_supervision_requests"
                   "(plan_id,request_id,operation,payload_digest,result_json) VALUES (?,?,?,?,?)",
                   (scope.plan_id, request_id, operation, digest, json.dumps(result)))

    def initialize(self, scope: Scope, *, plan_digest: str, tasks: tuple[TaskIdentity, ...],
                   policy: SupervisionPolicy = SupervisionPolicy(), request_id: str) -> None:
        _text(plan_digest)
        if not isinstance(policy, SupervisionPolicy) or policy.max_retries != 0:
            raise AdmissionDenied("retry policies are not implemented")
        if not tasks or len({t.task_id for t in tasks}) != len(tasks):
            raise IdentityConflict("nonempty unique tasks required")
        for t in tasks:
            TaskKey(scope, t)
        identities = sorted((t.task_id, t.profile) for t in tasks)

        def write(db):
            self._schema(db, create=True)
            self._legacy(db, scope, active=True)
            rows = db.execute("SELECT * FROM swarm_plan_tasks WHERE plan_id=?", (scope.plan_id,)).fetchall()
            if sorted((r['task_id'], r['profile']) for r in rows) != identities or any(
                r['attempt'] != 1 or r['group_id'] != scope.group_id or r['state'] != 'pending'
                or r['started'] is not None or r['finished'] is not None for r in rows
            ):
                raise AdmissionDenied("not an untouched legacy plan")
            old = db.execute("SELECT * FROM swarm_supervision_plans WHERE plan_id=?", (scope.plan_id,)).fetchone()
            if old:
                self._plan(db, scope)
                frozen = sorted(tuple(r) for r in db.execute(
                    "SELECT task_id,profile FROM swarm_supervision_tasks WHERE plan_id=?", (scope.plan_id,)))
                if old['plan_digest'] != plan_digest or old['max_retries'] != policy.max_retries or frozen != identities:
                    raise IdentityConflict("frozen plan changed")
            digest, replay = self._request(db, scope, request_id, 'initialize', [plan_digest, identities, 0])
            if replay is not None:
                return
            if old is None:
                db.execute("INSERT INTO swarm_supervision_plans"
                           "(plan_id,group_id,owner,execution_id,plan_digest,max_retries) VALUES (?,?,?,?,?,0)",
                           (scope.plan_id, scope.group_id, scope.owner, scope.execution_id, plan_digest))
                for task_id, profile in identities:
                    db.execute("INSERT INTO swarm_supervision_tasks(plan_id,task_id,profile) VALUES (?,?,?)",
                               (scope.plan_id, task_id, profile))
            self._save(db, scope, request_id, 'initialize', digest, True)
        self.store._tx(write)

    def reserve_initial(self, key: TaskKey, *, holder_id: str, request_id: str) -> Reservation:
        _text(holder_id)

        def write(db):
            p, t = self._task(db, key, active=True)
            digest, replay = self._request(db, key.scope, request_id, 'reserve_initial', [asdict(key), holder_id])
            if replay is not None:
                return Reservation(Fence(key, replay[0], replay[1], holder_id), 'reserved', request_id)
            self._admit(p, t)
            if t['latest_number'] != 0 or t['logical_state'] != 'pending':
                raise AdmissionDenied("initial attempt already consumed")
            epoch = t['epoch'] + 1
            args = (key.scope.plan_id, key.identity.task_id)
            changed = db.execute("UPDATE swarm_supervision_tasks SET latest_number=1,epoch=?,holder_id=?,"
                                 "logical_state='active' WHERE plan_id=? AND task_id=? AND latest_number=0",
                                 (epoch, holder_id, *args)).rowcount
            if changed != 1:
                raise StaleFence("reservation lost")
            db.execute("INSERT INTO swarm_supervision_attempts"
                       "(plan_id,task_id,number,epoch,holder_id,state,reservation_request_id)"
                       " VALUES (?,?,1,?,?,'reserved',?)", (*args, epoch, holder_id, request_id))
            self._save(db, key.scope, request_id, 'reserve_initial', digest, [1, epoch])
            return Reservation(Fence(key, 1, epoch, holder_id), 'reserved', request_id)
        return self.store._tx(write)

    def _fence(self, db, fence, *, active=False):
        p, t = self._task(db, fence.key, active=active)
        if (t['latest_number'], t['epoch'], t['holder_id']) != (fence.number, fence.epoch, fence.holder_id):
            raise StaleFence("fence no longer current")
        a = db.execute("SELECT * FROM swarm_supervision_attempts WHERE plan_id=? AND task_id=? AND number=?",
                       (fence.key.scope.plan_id, fence.key.identity.task_id, fence.number)).fetchone()
        if a is None or (a['epoch'], a['holder_id']) != (fence.epoch, fence.holder_id):
            raise StaleFence("attempt mismatch")
        return p, t, a

    def mark_dispatch(self, fence: Fence, *, request_id: str) -> bool:
        """True ONLY for a freshly committed intent; False means already applied.

        Neither return value is grant, spend or engine authorization.
        """
        def write(db):
            p, t, a = self._fence(db, fence, active=True)
            digest, replay = self._request(db, fence.key.scope, request_id, 'mark_dispatch', asdict(fence))
            if replay is not None:
                return False
            self._admit(p, t)
            if a['state'] != 'reserved':
                raise InvalidTransition("dispatch already consumed")
            changed = db.execute("UPDATE swarm_supervision_attempts SET state='dispatch_intent',dispatch_request_id=?"
                                 " WHERE plan_id=? AND task_id=? AND number=? AND epoch=? AND state='reserved'",
                                 (request_id, fence.key.scope.plan_id, fence.key.identity.task_id,
                                  fence.number, fence.epoch)).rowcount
            if changed != 1:
                raise StaleFence("dispatch lost")
            self._save(db, fence.key.scope, request_id, 'mark_dispatch', digest, True)
            return True
        return self.store._tx(write)

    def set_control(self, scope: Scope, *, task_id: str | None = None, expected_revision: int,
                    stopped: bool | None = None, paused: bool | None = None, request_id: str) -> int:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid revision")
        if any(v is not None and type(v) is not bool for v in (stopped, paused)):
            raise ValueError("boolean controls required")
        if stopped is None and paused is None:
            raise ValueError("empty control")
        if task_id is not None:
            _text(task_id)

        def write(db):
            self._plan(db, scope)
            table = 'swarm_supervision_plans' if task_id is None else 'swarm_supervision_tasks'
            where = 'plan_id=?' + ('' if task_id is None else ' AND task_id=?')
            args = (scope.plan_id,) if task_id is None else (scope.plan_id, task_id)
            row = db.execute(f"SELECT * FROM {table} WHERE {where}", args).fetchone()
            if row is None:
                raise IdentityConflict("unknown control target")
            digest, replay = self._request(db, scope, request_id, 'set_control',
                                           [task_id, expected_revision, stopped, paused])
            if replay is not None:
                return replay
            if row['stopped'] and stopped is False:
                raise InvalidTransition("stop is sticky")
            if row['control_revision'] != expected_revision:
                raise StaleFence("control revision changed")
            revision = expected_revision + 1
            changed = db.execute(f"UPDATE {table} SET stopped=?,paused=?,control_revision=? WHERE {where}"
                                 " AND control_revision=?",
                                 (row['stopped'] if stopped is None else int(stopped),
                                  row['paused'] if paused is None else int(paused), revision,
                                  *args, expected_revision)).rowcount
            if changed != 1:
                raise StaleFence("control CAS lost")
            self._save(db, scope, request_id, 'set_control', digest, revision)
            return revision
        return self.store._tx(write)

    def mark_uncertain(self, fence: Fence, *, reason: str, request_id: str) -> None:
        if reason not in ('owner_lost', 'dispatch_outcome_unknown'):
            raise ValueError("unknown uncertainty code")

        def write(db):
            self._fence(db, fence)
            digest, _ = self._request(db, fence.key.scope, request_id, 'mark_uncertain', [asdict(fence), reason])
            args = (fence.key.scope.plan_id, fence.key.identity.task_id)
            changed = db.execute("UPDATE swarm_supervision_tasks SET epoch=epoch+1,"
                                 "logical_state='reconciliation_required',reconciliation_required=1"
                                 " WHERE plan_id=? AND task_id=? AND epoch=?", (*args, fence.epoch)).rowcount
            if changed != 1:
                raise StaleFence("uncertainty CAS lost")
            changed = db.execute("UPDATE swarm_supervision_attempts SET state='uncertain',uncertainty_reason=?"
                                 " WHERE plan_id=? AND task_id=? AND number=? AND epoch=?",
                                 (reason, *args, fence.number, fence.epoch)).rowcount
            if changed != 1:
                raise StaleFence("uncertainty attempt CAS lost")
            self._save(db, fence.key.scope, request_id, 'mark_uncertain', digest, True)
        self.store._tx(write)

    def read(self, key: TaskKey) -> Snapshot:
        def read(db):
            p, t = self._task(db, key)
            a = db.execute("SELECT state FROM swarm_supervision_attempts WHERE plan_id=? AND task_id=? AND number=?",
                           (key.scope.plan_id, key.identity.task_id, t['latest_number'])).fetchone()
            return Snapshot(key, p['plan_digest'], p['max_retries'], t['latest_number'], t['epoch'],
                            t['logical_state'], bool(p['stopped'] or t['stopped']), bool(p['paused'] or t['paused']),
                            bool(t['reconciliation_required']), None if a is None else a['state'],
                            p['control_revision'], t['control_revision'])
        return self.store._read(read)
