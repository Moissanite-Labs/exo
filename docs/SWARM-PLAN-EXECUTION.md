# Swarm plan execution

This page covers how a validated task plan runs on group members, what the tests prove, and what this layer does not do.

Code: `src/exo/core/group_plan_execution.py` and the `plan=`/`grants=` branch of `GroupHost.start` in `src/exo/core/group_host.py`. Plan validation is covered in `docs/SWARM-TASK-CONTRACT.md`.

## API

```python
from exo.core.group_task_contract import HostGrants, validate_plan

grants = HostGrants(owner=..., profiles=..., permits=..., write_roots=...,
                    routes=..., reviewer_profiles=...)   # built by the host, never from plan text
plan = validate_plan(raw_plan, grants, group=group)     # untrusted dict in, ValidatedPlan out
host.start(group.id, plan=plan, grants=grants)          # nothing runs before this call
await host.wait(group.id)
```

- `GroupHost.start(group_id, *, plan, grants, max_concurrent=None, ...)` runs the plan on the existing `GroupRuntime`. You must pass `plan` and `grants` together. You cannot combine a plan with `conversation=` or explicit audiences. A refused start raises `HostError`. Errors caught from the plan executor use the `plan execution refused: ...` prefix. Host preflight errors need not. `start` returns the initial member-status tuple, not a task or coroutine. Call it inside the same running event loop used for `wait` and teardown.
- `start_plan(host, group_id, plan=..., grants=...)` is the thin entry point the host calls. It is not a second host.
- `PlanExecutions(GroupStore(store))` reads durable state. `tasks(plan_id, group_id=, owner=)` returns `TaskRunState` rows. `plan_state(...)` returns `"running"` or `"settled"`.
- `PLAN_PREFIX` and `REVIEW_PREFIX` are the fixed prompt prefixes.

## Execution behavior

- Each member profile gets one loop. The loop runs only that profile's tasks, in the plan's topological order. A member that owns no task still settles as completed.
- Each task is one engine turn on the engine already bound to `task.profile`. The prompt is `PLAN_PREFIX` followed by one JSON document with `task_id`, `role`, `goal`, `acceptance`, `route`, `write_scopes`, `depends_on` and `dependency_results`. `route` appears only as data inside the prompt. It does not select a model.
- A reviewer task (role `reviewer`, `verifier` or `critic`) also gets `REVIEW_PREFIX` plus `{"reviewed_task", "reviewed_result"}`. The contract requires an empty write scope, a reviewer-granted profile and a dependency on the reviewed task. `authorize_task` rechecks reviewer-profile grants and empty write scopes before every turn. The task contract validates dependency structure. Read-only here is a contract and prompt constraint, not a tool/filesystem sandbox.
- Results are bounded engine text, at most 16,000 characters by default including the truncation marker. Later tasks receive them as quoted, untrusted data.
- If a dependency fails or stops, every task that depends on it fails closed with `dependency <id> failed` or `dependency <id> stopped`, and no engine turn runs for it. Nothing is retried, skipped silently or run speculatively.
- Once a member has entered its plan adapter, stopping it settles its remaining tasks as `stopped`, not failures of their own. Stopping before a member enters its adapter settles that member's tasks as `stopped` too. See "Lifecycle failure behavior".
- `max_concurrent` limits how many engine turns run at once. It does not limit loops.

## What the tests prove

All tests are offline. They use fake engines and no provider. They run through the real `GroupHost`, `GroupRuntime` and `PlanExecutions`.

- `tests/test_group_plan_execution.py` covers the happy path, dependency failure, stop, pause and resume, concurrency bounds, prompts and bounded results.
- `tests/test_group_plan_final_safety.py` covers fault injection and the lifecycle rules below. It includes a spawned-process test that the plan reservation is committed before the engine factory runs.

Run the commands in the last section. No test here uses a real model or a real `CoreEngine`. Whether a real model does what a task asks is not shown.

## What this does not claim

- **Write scopes are lexical, not a sandbox.** `write_scopes` are prefix strings checked against `HostGrants.write_roots` at component boundaries. This layer never opens or resolves a path and enforces nothing on the filesystem. A tool that writes during an engine turn must check authority and resolve the real path itself. A validated plan never authorizes a write. The tests use scopes like `/srv/w/build` that are never touched.
- No secret detection or scanning.
- `plan_id` is a content digest anyone can compute. It identifies a plan. It does not prove authority. Authority comes only from `HostGrants`, which are checked before the start and before each turn. Roles and profile names are data and grant nothing.
- The tests show how prompts and results flow through fake engines. They do not show that a real model does what the task asks.

## Durability and restart limits

- State is stored in two tables, `swarm_plans` and `swarm_plan_tasks`, in the same sessions DB as the group. The tables are created on the first claim or plan read. Preflight rejection of an invalid plan and the duplicate probe create no plan schema. A later claim failure may leave empty plan tables after discarding the unstarted rows.
- Each task gets exactly one attempt. There is no retry, restart recovery or stale-claim takeover. In-process pause/resume is supported. It is not durable recovery.
- A plan id can be started only once. A duplicate is refused before any engine is built, and refused again inside the claiming transaction.
- If a process crashes mid-run, the plan row stays `running` and its tasks stay `pending` or `running`. That plan id cannot be started again until someone reconciles it out of band. No tool does that reconciliation today.
- In-memory dependency results (the `_results` map) do not survive a restart. Only the stored rows do.

## Not done

This layer executes one validated plan on an already launched group. It is not the whole swarm. It does not plan, decompose tasks, pick models, retry, recover after a crash, enforce a filesystem sandbox, or verify output. Each of those would need separate work and separate evidence.

## Reading plan state through `/group plan_status`

The `/group plan_status` command reads `PlanExecutions.plan_state` and `tasks` for the principal's own
seat and needs no host. It first uses the read-only `started` probe, so a database with no plan tables
is refused without creating them. Unknown, foreign-group and foreign-owner plans return one identical
refusal. Results and errors come back as clipped untrusted text with truncation flags. All of that text
shares one 32000-character budget per response, and the serialized JSON and metadata come on top. The task
list is capped by an explicit `limit` that is reported, never silent. Plan state and task rows come from
separate reads that share no transaction, so a mid-run snapshot can mix moments. Offline tests with the
fake provider cover settled,
running, failed and stopped plans. Starting a plan from a client is not implemented.

## Host controls and inspection

Use `host.pause(group.id, profile="checker")`, `host.resume(group.id,
profile="checker")`, and `host.stop(group.id, profile="builder")`. Omit
`profile` to target all members. Pause is a checkpoint gate, not suspension of
an in-flight model call. A paused reviewer stays pending even after its dependency
completes, then starts after resume. Stop cancels work rather than retrying it.
The plan adapter leaves the group message mailbox alone. Task results flow through
plan dependency data, not conversation replies.

`await host.wait(group.id)` returns member statuses. Inspect
`host.status(group.id)["receipt"]` separately from
`PlanExecutions(GroupStore(store)).tasks(plan.plan_id, group_id=group.id,
owner=group.owner)` and `plan_state(...)`. A settled receipt or settled plan
means execution ended, **not** that all tasks succeeded. Task rows include
`task_id`, `profile`, `attempt`, `state`, `started`, `finished`, `result`, and
`error`. Read them before closing the store. A launcher-backed caller should
always `await launch.aclose()` in `finally`, in the same event loop as start/wait.
The trusted plan API is `launch.host.start(...)`, not an assumed `plan=` argument
on the launch convenience method.

`max_concurrent` is `None` or a strict integer from 1 through the member count.
`knowledge=True` optionally decorates prompts with owner-published group knowledge.
`max_items`/`max_chars` on `GroupHost.start` bound that snapshot, not stored task
results. Without knowledge opt-in, the plan prompt uses the fixed prefixes above.

## Lifecycle failure behavior

These rules cover only exception safety. The executor still does not retry tasks.

- **Start order.** The plan reservation (`begin`) is committed before the engine factory runs. A lost race on the same plan id never allocates engines. If `begin` raises `sqlite3.Error` (for example a locked database), `start_plan` raises `PlanExecutionError("plan reservation failed: ...")` and `GroupHost.start` reports it as `HostError`. No factory call and no runtime registration happens.
- **Group claim comes later.** The group-level execution claim is taken when the runtime starts, after the factory. A second host that starts a different plan on a group whose claim is already held calls the factory once and is then refused with `group execution already claimed`. Its own plan reservation is released, but its engines were built and then dropped.
- **Setup fails before members run.** If the factory, adapter or runtime setup raises, the reservation is discarded only while every task is still pending and the caller's identity matches. If that discard also fails, the setup error is still the one raised. The discard failure is attached to it as a note.
- **Setup fails after members started.** If `runtime.start` raises after the member loops exist, the reservation is kept because a task may already be entered. The runtime is asked to stop and the original error is raised.

  The runtime is not registered, so no later `wait()` or `aclose()` can report a problem. A failure of that stop, and any durable-cleanup error it kept, are therefore attached to the original error as notes. `GroupHost.start` copies the notes onto the `HostError` it raises. The plan id stays unusable and a fresh host cannot restart it.
- **Stop is a request.** `stop()` cancels members first. It then settles tasks that never entered. If that durable cleanup raises, `stop()` still returns because cancellation cannot be undone. The cleanup attempts every task and keeps the first error.
- **Cleanup is retried.** A kept cleanup error may be transient, such as a locked database. Each `wait()` and each `aclose()` attempt retries it.

  The retry touches only the recorded tasks whose stored row is still `pending`. It never rewrites a terminal row or reaches another plan. It settles the plan row last, and only when every task row is terminal.

  When the retry succeeds the stored error is cleared and `wait()` returns normally. When it fails again, `wait()` raises the new error and the next call retries once more.
- **Closing the host.** `aclose()` fails closed. If the retry still fails, it raises `HostError("runtime settlement failed: ...")`, keeps the runtime and does not release the engine factory.

  The host is not marked closed and a later `aclose()` starts a fresh attempt. When the error has cleared, that call settles the rows, releases the factory and closes the host. A transient error therefore delays the close and cannot make it impossible. The guards stay as they were: start and create are refused once `aclose()` has been entered, and a stopped plan is never restarted.
- **Tasks that never entered.** A group stopped before its adapters enter has its pending tasks marked stopped. Dependent reviews fail closed.
- **Reading errors.** `GroupHost.wait` maps only `ValueError` to `HostError`. A stored `sqlite3.Error` from the plan runtime can reach the caller of `wait()` unwrapped. `aclose()` wraps it. Callers that catch `HostError` around `wait()` should also catch `sqlite3.Error`.

Regression tests: `tests/test_group_plan_final_safety.py` (offline, no providers).

```sh
EXO_NO_KEYCHAIN=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  python -m pytest -q \
  -p no:cacheprovider -p no:randomly \
  tests/test_group_plan_execution.py tests/test_group_plan_final_safety.py
```

Not covered: process-crash injection, plan-specific queued-turn concurrency, and
authorization revocation during a queued turn. Multi-process coverage is limited to the
plan reservation preceding the factory.

## Durable dispatch (opt-in)

See `docs/SWARM-DURABLE-DISPATCH.md`: `GroupHost.start(durable_attempts=True)` records each task attempt in
the attempt ledger (no retries); default off, existing behavior unchanged.
