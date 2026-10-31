# Automatic continuity checkpoints

Exo keeps a short index of recent tool and background-job records for an ordinary
conversation. It helps the next model request find what ran, what reported an
error, and which interrupted operations need a live-state check. The index is
separate from the agent's optional [working notes](WORK-NOTEBOOK.md).

## Use and settings

Checkpoints are enabled by default when the core is created through `exo.compose`.
Harness profiles do not change this: the setting below decides in every profile
([HARNESS.md](HARNESS.md)). Restarting the same conversation reconstructs the index from its durable
transcript, even if the previous process exited without a shutdown hook. There
is no automatic continuation, tool replay, or extra model call.

`/checkpoint` shows the index in the TUI or `exo chat`, using a principal
connection. Clients can also call `command.run` with `name: "checkpoint"` and the
session ID. The web composer does not yet run this command. The same index is
included in handoff facts and model workspace context.

```json
{
  "work_checkpoint": {
    "enabled": true,
    "resume_chars": 3000
  }
}
```

`enabled` is a boolean. `resume_chars` is an integer from 1024 to 8000, including
labels and the truncation notice. To disable it, run
`exo config set work_checkpoint.enabled false` and restart Exo. Disabling stops
capture and injection; it does not delete the previously saved index.

The default can add up to 3000 characters to each model request once tool or job
records exist. Private threads, child/fork threads, scheduled sessions, and other
agents' sessions receive no checkpoint. There is no cross-session inheritance.
For those sessions the checkpoint reads the session record first and opens no
write transaction.

## What a record means

- A successful result means the tool reported success. It does not independently
  verify filesystem changes or external effects.
- An error means the tool reported an error. Effects remain unknown.
- An interrupted or incomplete result means completion was not confirmed. An
  interrupted command may already have changed something. Check actual state
  before repeating it.
- A job state is the stored state, not a fresh process check.

The index includes at most the latest 12 tool calls from 40 visible transcript
messages, newest first, and at most 8 session-owned job records. Older work can
fall outside the window. It copies no user text, assistant text, tool arguments,
output bodies, commands, paths, or job logs. Identifiers are bounded and restricted
to single-line identifier characters. It grants no permission and carries no
principal instruction, goal, or plan.

Undo rebuilds from currently visible transcript records. Jobs have no transcript
anchor, so their index is omitted after any transcript undo; inspect live jobs
separately. Actual effects are not rolled back by hiding transcript messages.

## Developer contract

`exo.work_checkpoint` owns one replaceable row per eligible session in
`sessions.sqlite`, scoped to its agent and configured principal. It reconstructs
from source records before injection rather than trusting an old saved snapshot.
It also captures at turn end and before compaction through chained existing
callbacks. Raw transcript records survive compaction, allowing reconstruction.
No shutdown hook is relied on for crash recovery.

The module attaches through composition, chains workspace context, turn-end and
pre-compression callbacks, and supplies handoff facts. Capture failures are
reported through harness diagnostics and omitted from the prompt; an old snapshot
is never used as a fallback. Existing manual notebook revisions remain untouched.

This is the record-index slice of issue #683. It does not inspect uncommitted
filesystem changes, synthesize a task goal, track child-agent work, or reconstruct
a complete historical task. Tests are in `tests/test_work_checkpoint.py`.
