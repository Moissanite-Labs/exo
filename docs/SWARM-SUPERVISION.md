# Plan-attempt supervision policy (not integrated recovery)

`exo.core.group_supervision` is a pure, default-off decision policy. It performs
no work, persists nothing, reserves no attempt, and grants no authority. The
existing plan executor still runs attempt 1 only and refuses duplicate starts.
Importing or calling this policy does not change that behavior.

## Contract

- Logical identity is `(plan_id, task_id, profile)` and remains unchanged. Only
  the attempt number advances. IDs are identity, not authority or fencing tokens.
- `SupervisionPolicy(max_retries=0)` disables retries. An explicitly chosen
  integer from 0 through 10 bounds **lifetime retries per logical task**, not a
  resettable time window. With 3 retries, attempts 1 through 4 are possible.
- Only `state="failed"`, `Failure.TRANSIENT`, and positively evidenced
  `Effects.NOT_DISPATCHED` can yield `Action.RETRY` within the budget.
- Cancellation (including a current stop request), denial, spend exhaustion,
  and permanent failure are final, regardless of remaining budget. They are not
  transient crashes. Completed work is never replayed.
- Unknown failure classification, pending/running/unknown states, and unknown
  or external effects on a failed attempt require reconciliation, never replay.
  Final cancellation/denial/spend decisions also set `reconciliation_required`
  when effects are uncertain or external; final means no retry, not cleanup done.
  Successful completed work does not require reconciliation merely because it
  produced an expected external effect.
- Invalid typed inputs raise `ValueError`; callers must not treat validation
  errors as permission to retry. Missing failure/effects facts default to unknown.

```python
from exo.core.group_supervision import (
    Attempt, Effects, Failure, SupervisionPolicy, TaskIdentity, decide,
)

# row is an existing TaskRunState read from the durable plan store.
# These explicit classifications require trusted execution evidence.
observation = Attempt(
    TaskIdentity(row.plan_id, row.task_id, row.profile), row.attempt, row.state,
    failure=Failure.TRANSIENT, effects=Effects.NOT_DISPATCHED,
)
advice = decide(observation, SupervisionPolicy(max_retries=2))
# Advice alone must not launch a turn or create a replacement task ID.
```

Do not infer transient/no-dispatch from an exception string, model output, a
read-only role, or absence of a result. The current generic `engine turn failed`
receipt is insufficient evidence. Dispatched turns are outside the safe retry
class even if a caller believes they were harmless. The policy trusts the caller's
observations; it cannot discover side effects or verify an attempt ledger.

## Next integration seam (future work)

Use the existing `PlanAdapter._execute` / `_run_profile` lifecycle, before the
logical task's final `_finalize` and `PlanGate.settle`. Never reopen a dependency
gate or add a second scheduler. `PlanExecutions.mark`, `begin`, and `finish`
currently hardcode attempt 1: they need explicit, atomic, fenced attempt
reservation and durable accounting before this advice can safely drive execution.
The table key already includes `(plan_id, task_id, attempt)`, but that is not an
implemented retry API. Preserve group/owner/execution ownership and single-start
guards; a stale owner must not write or take over.

Before every retry, re-read the durable attempt count, verify the same logical
identity and ownership, re-check checkpoint/stop/pause, dependency state, grants,
spend limits, and turn-slot admission. Permission denial is final even pre-turn.
Approval waits must remain waits, not failures. Persist trusted dispatch/failure
classification rather than reconstructing it from the current coarse receipts.
Stop must win any race after advice and before dispatch. Settle dependencies only
after final outcome, not after an intermediate failed attempt.

There is no automatic restart, backoff, jitter, window budget, reconciliation
tool, stale-claim takeover, durable retry ledger, or recovery after process death
in this slice. There is no OS/process isolation: even future coroutine retries
in the existing runtime would share its interpreter and permissions. No model,
route, authorization configuration, or engine binding is changed by this policy.

Offline tests in `tests/test_group_supervision.py` cover decision behavior only,
not runtime retry integration, restart fencing, or exactly-once external effects.