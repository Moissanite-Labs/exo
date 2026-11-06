# Durable initial attempts: the ledger and its opt-in use

**Not crash recovery, retries, or execution authorization.** The ledger is
connected to plan execution only when a caller passes `durable_attempts=True` to
`GroupHost.start` (or the launch wrapper) together with a plan. The default is
`False`, and then no `swarm_supervision_*` table is created. See
`docs/SWARM-DURABLE-DISPATCH.md` for how the opt-in path runs. This page
describes the ledger itself.

- `AttemptLedger(groups)` performs no database work. Explicit `initialize` is the
  only schema creator, and the opt-in start path calls it.
- The ledger never retries and never recovers a crashed run. Each task gets one
  attempt.
- The ledger does not record how a task ended. The legacy plan and task rows
  stay the only terminal record. They are the authority for the final state.
- If startup fails once ledger initialization has begun, the unstarted task rows are
  marked `failed` and the plan settles. That plan id is used up for a later start
  with the flag on or off.
- Do not initialize this ledger on a plan that a legacy (flag-off) run is already
  using. The legacy path does not honor these fences or controls.

## Implemented contract

All mutations use the existing `SessionStore._tx`: a per-connection `RLock`,
`BEGIN IMMEDIATE`, rollback on callback failure, commit before returning. Different
connections therefore serialize through SQLite, not a process-local gate. Reads
use `SessionStore._read`, never create schema, and return frozen value objects.
All inserts name columns. Only five new `swarm_supervision_*` tables are written;
legacy plans/tasks, group claims, `user_version`, grants and sessions are untouched.

- `Scope(group_id, owner, plan_id, execution_id)`, `TaskKey(scope, identity)` and
  `Fence(key, number, epoch, holder_id)` are immutable identity values.
- `initialize(scope, plan_digest=..., tasks=(TaskIdentity(...), ...),
  policy=SupervisionPolicy(), request_id=...)` validates the existing group owner,
  matching running plan/execution and **exact** pending attempt-1 task/profile set,
  without started/finished timestamps. No adoption of dispatched or settled work.
  Digest, identities and zero retry ceiling are frozen; matching initialization
  cannot reset counts. Nonzero retry policies are explicitly rejected.
- `reserve_initial(key, holder_id=..., request_id=...)` atomically consumes the
  sole attempt, increments its epoch, and returns `Reservation`. A reservation is
  a durable receipt, not authority to execute. Distinct racing requests have one
  winner. There is no refund, expiry, takeover or reset.
- `mark_dispatch(fence, request_id=...) -> bool` performs a one-shot
  `reserved -> dispatch_intent` CAS. **Only `True` denotes a newly committed
  transition; matching replay returns `False` and MUST NOT call an engine.**
  Different request IDs cannot consume intent again. Current ownership,
  execution, legacy untouched-task state, fence and controls are checked within
  the same transaction. Even `True` supplies no grant/spend/engine authorization.
- `set_control(scope, task_id=None, expected_revision=..., stopped=None,
  paused=None, request_id=...) -> int` controls either plan or task using a
  monotonically increasing CAS revision. Stop is sticky; pause may be cleared.
  Both plan and task controls gate admission. Controls do not cancel existing
  work or change authority. Reads expose both control revisions.
- `mark_uncertain(fence, reason='owner_lost'|'dispatch_outcome_unknown',
  request_id=...)` durably freezes the logical task for reconciliation and
  increments its epoch. The attempt remains counted. **Every old fence is stale,
  including a repeated uncertainty request.** No clear/replay API exists.
- `read(key) -> Snapshot` exposes immutable identity, frozen digest/ceiling,
  attempt count, epoch, aggregate controls, reconciliation bit, revisions and
  latest attempt **state string** (not a detailed attempt object).

Payload digests include scope, operation, identity/fence and supplied controls.
Request IDs are unique per plan; reused IDs with changed payload raise
`RequestConflict`. Reservation replay returns its original fence even after
uncertainty; it is only a receipt, and fresh dispatch validation rejects it.
Dispatch replay still checks current fence/execution and cannot resurrect work.
Control replay returns the original revision, not a claim about current controls.
Idempotency is mutation idempotency, never permission to replay effects.

Errors are bounded codes/messages, not raw engine errors. SQLite failures
propagate; they are not transient-work evidence. Owner strings and holder IDs are
identity checks for a **trusted host**, not authentication or capabilities against
malicious in-process code or direct SQL writers.

## Schema and conservative versioning

This deliberately smaller schema uses component `initial_attempt`, version 1,
not a claim to implement the full proposed retry schema. It caps number at one
and max_retries at zero. Initialize validates the exact normalized table DDL and
version before using an existing schema, rejecting partial, future or changed
shapes instead of guessing or repairing. Creation and initialization are one
transaction; failed validation rolls back DDL too. It does not silently adopt
other supervision schemas. Future retries need an explicit reviewed migration
and a new compatible contract, not `CREATE IF NOT EXISTS` alone.

## Explicit omissions and enablement blockers

The design's `TrustedFailure`, `Resolution`, `resolve`, `reserve_retry`, terminal
completion/evidence/result receipts, timestamped history, retry budget exhaustion
and policy `decide` integration are **not implemented**. There is no finalization
API; this slice is intentionally only admission, dispatch intent, controls and
uncertainty. Detailed `latest_attempt` objects and automatic post-crash detection
are also omitted. Uncertainty's repeated stale request fails rather than replaying
a successful result; this preserves the old-fence prohibition explicitly.

No production classifier is available. No supervision evidence is inferred from
exceptions, provider flags or model text. No retry is possible, including after a
crash before dispatch. A committed dispatch intent means **may have dispatched**;
a crash between intent and engine call cannot safely prove no effects. A host
must explicitly mark uncertainty using the original current fence; reopen alone
neither marks nor resumes it.

The opt-in integration does not solve authenticated host controls, the durable
group claim, authoritative grants/spend admission, or engine/tool effect fencing.
SQLite cannot revoke an already issued external call. The ledger itself does not
update the legacy task row with an outcome. In the opt-in path the plan executor
writes the terminal state to the legacy row before it tells dependent tasks. A
flag-off run must not coexist on the same plan. There is no exactly-once effects,
recovery, cancellation or lease guarantee here.

## Offline validation

`tests/test_group_attempt_ledger.py` uses on-disk temporary sessions databases,
independent connections and synthetic legacy pending rows; no engine/model calls.
The opt-in path through the host has its own tests in
`tests/test_group_plan_durable_attempts.py`.
It covers constructor/read default-off behavior, lazy/concurrent initialization,
reservation/dispatch races, same-request replay, control races and sticky stop,
reopen, immutable identities, stale/forged fences, uncertain receipts, schema
rejection, refusal to adopt legacy work, and callback-fault rollback. It does not
claim real process-kill/power-loss or external engine fencing coverage. Host
integration is covered only by the separate opt-in test file named above.

```sh
EXO_NO_KEYCHAIN=1 PYTHONPATH=src:tests python -m pytest -p no:randomly \
  -q tests/test_group_attempt_ledger.py
```
