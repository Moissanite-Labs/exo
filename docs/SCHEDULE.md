# Scheduled jobs (D-022)

A scheduled job is a prompt that runs by itself on a timer. Each run is a fresh session of kind `scheduled`
(see MEMORY-MODEL.md). The run reports through `task.updated`, and the final answer goes out as a `notice`
or to a channel. Code: `src/exo/scheduler.py` (outside the core since D-067; `exo.compose` attaches it to the engine).

## Make and manage jobs

The protocol command is `schedule.edit`. Its `action` is `list`, `add`, `remove`, `pause`, `resume` or `run_now`.

| Field | Meaning |
|---|---|
| `when` | A 5-field cron line such as `0 9 * * *`. The fields are minute, hour, day of month, month, day of week. `*`, lists, ranges and steps work. or `every <n>m`, `every <n>h`, `every <n>d`. |
| `prompt` | What the run should do. |
| `title` | A label. |
| `seat` | The seat the run belongs to. Empty means the active one. |
| `tier` | `fast` (default), `standard` or `deep`. The cheapest tier that can do the job is the right one (D-017). |
| `deliver` | `notice` (default) or a channel id. |

Jobs live in `$EXO_HOME/schedule.sqlite`. `exo serve` runs the timer. A one-shot engine, such as a test, does not.

## Rules of the run

- The run is told it is unattended and must end with a short plain-English result.
- It cannot use tools that need a person: `delegate`, `clarify`, `ask`, `memory`, `send_message`, `bus_post`,
  `cronjob_manage` and `ui` are blocked (`BLOCKED_TOOLS` in `core/agents/delegate.py`).
- It goes through the same gate as any turn. An ask waits for an approval, so an unattended run can stall on one.
- It is an ordinary turn of the agent (D-041): it uses the engine's context tree and memory provider, the soul
  refresh and system extras, and fires the `on_turn_start` and `on_turn_end` hooks. Only the model (the job's tier),
  the narrowed toolset and the unattended-job prompt differ.
- Each scheduled turn refreshes `SOUL.md` before copying the current composed system prompt. Its
  `turn.started` soul hash and the text sent to the model refer to the same soul; the unattended instruction
  is added once to that run, never to the interactive prompt.
- `turn.interrupt` for the run's child session (Ctrl+C or `/stop` in that thread) stops its model call, tool or
  approval wait through the ordinary turn loop. Other threads and background shell jobs keep running.
  Completion, failure and cancellation remove the live scheduled loop; cancellation also reaps pending work.
- The timer loop survives a failing check or next-run database listing: the error is recorded as a
  `harness.error` (`scheduler tick`) and the loop waits a few seconds and goes on. If the loop ever ends by
  itself, that is recorded too (`scheduler loop`) and a
  warning notice is shown.
- Runs missed while the engine was down happen once at start, not once per missed slot.

## Clock changes

Cron fields are local time, and a job's next run is always later than now. When clocks go back, an hour comes
twice. A job that ran in the first pass of that hour does not run again in the second: `30 1 * * *` runs once,
and `*/15 * * * *` goes from 01:45 to 02:00. A job added, resumed or overdue during the second pass counts from
that pass's time: added at 01:20 there, `*/15 * * * *` runs at 01:30, 10 minutes later. When clocks go forward, a slot in the skipped hour runs an hour later: where 02:00 jumps
to 03:00, `30 2 * * *` runs at 03:30 that day. `every` jobs count real time and do not move.
