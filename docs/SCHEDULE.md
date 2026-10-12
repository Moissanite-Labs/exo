# Scheduled jobs (D-022)

A scheduled job is a prompt that runs by itself on a timer. Each run is a fresh session of kind `scheduled`
(see MEMORY-MODEL.md). The run reports through `task.updated`, and the final answer goes out as a `notice`
or to a channel. Code: `src/exo/core/scheduler.py`.

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
- Runs missed while the engine was down happen once at start, not once per missed slot.
