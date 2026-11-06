# Swarm durable dispatch (opt-in)

`GroupHost.start(..., plan=, grants=, durable_attempts=True)` routes each plan task through the
attempt ledger (`group_attempt_ledger`). The flag must be an exact `bool`, requires a plan, and defaults
to `False`. With it off, no `swarm_supervision_*` table is created and plans run the same way as before,
with two visible differences that apply to every plan: `plan_status` adds durable fields (`durable` is
`{"status": "none"}`), and the terminal state is always written to the plan row before the plan gate is
told (see "What this does not do"). Retries are disabled: one reservation and at most one dispatch
intent per task.

- Order per task: `reserve_initial` -> durable pause/stop gate -> `mark_dispatch` -> turn -> terminal mark.
  `mark_uncertain` is only for an interrupted dispatch, never on a normal run.
- Control ids are namespaced `ctl:<exec>:plan:<op>:r<rev>` (plan level) vs `ctl:<exec>:task:<task_id>:<op>:r<rev>`,
  so a task literally named `plan` cannot collide with plan-level controls.
- Pause/stop write the durable record first; if that fails the local pause/stop still happens and the host
  raises a visible `HostError` ("durable record NOT written"). Resume on durable failure stays paused.
  A healthy retry of the same operation clears its recorded error.
- A task durably paused after reservation awaits (bounded poll, loop stays free) holding the same fence;
  resume continues the same fence, stop settles it `stopped` without a provider turn.
- Tests: `tests/test_group_plan_durable_attempts.py`. They also cover the startup, terminal-commit,
  interrupted-turn and no-dispatch failure cases below.

## What this does not do

- The legacy plan and task rows stay the only record of how a task ended. The attempt ledger records
  reservations, dispatch intent and controls. It does not record a terminal result.
- There is no automatic crash recovery and no automatic retry. If the process dies after a dispatch
  intent but before the row is settled, `plan_status` reports the task as `in_flight_or_lost` or
  `needs_inspection_*`. A person has to inspect it.
- If startup fails after the ledger was set up, the unstarted rows are marked `failed` and the plan
  settles. That plan id is used up: a second start is refused with "plan already started", with the flag
  on or off. If marking a row failed also fails, the plan id stays blocked and the original error carries
  a note saying so.
- The terminal state is written to the plan row before the plan gate hears about it. If that write
  fails, the row stays `running`, dependents are failed, and nothing is announced as completed. The
  plan stays `running` until someone inspects it.
