# Operations inbox

`exo inbox` prints one local snapshot of work that may need attention. It works when the core is stopped
and with the lean harness profile. It starts no runtime, model, plugin, socket connection or polling loop.

```sh
exo inbox
exo inbox --agent builder --limit 10
exo inbox --json
exo inbox --principal selected
```

The command uses `$EXO_HOME` and `--agent` (default `$EXO_SEAT`, then `default`). `--principal` selects
only the question store. Its default is the configured principal. `--root` overrides that question store.
It never discovers or scans other principal stores. Runtime records belong to the home and agent, not
to a stored principal ID. Changing the selected principal does not filter those records or prove their
historical ownership. The snapshot reports both scopes. Run it only against a home you may read.

## Coverage and uncertainty

| Section | Included | Meaning and limits |
|---|---|---|
| Sessions | Most recently updated threads of the selected agent | Stored open/locked metadata. Live or idle state is unknown. |
| Tasks | Tasks whose parent or child belongs to that agent | Recorded terminal outcomes remain visible. Queued/running records have unknown current state. |
| Jobs | Background shell jobs owned by the agent | Running jobs come first. Commands, logs and PIDs are omitted. `lost` records the core's loss of ownership, not proof that a process died. |
| Schedules | Schedules assigned to the agent, plus unbound schedules shared by the home | Paused configuration, last recorded outcome and next due time. An overdue time does not establish a missed run or failure. |
| Questions | Asked or proposed questions in the selected store | Whole-file replay through the existing question reducer. No ledger reads, ranking refresh or asked-marker writes. |
| Approvals | Unresolved requests in a recent persisted event window for that agent | Always unknown: only the running core's approvals list can establish what is waiting now. A restart can leave unresolved historical events. |
| Findings | Open, reopened or fixed findings whose latest session belongs to that agent | Fixed means awaiting verification. Global findings and findings last attributed to another agent are omitted. |

The command reads each database in its own transaction. These are separate observations, not one atomic
snapshot across sources. It does not determine process liveness, inspect PID files, recover jobs, repair
records, answer approvals or initiate work. Use the existing terminal/web client to inspect live work and
answer approvals. `exo ask list` handles the question queue and `exo findings` shows the wider findings store.

## Limits and JSON

The default is 20 items per section. `--limit` accepts 1 through 100. Database reads share a two-second
budget (`--timeout 0.1` through `10`), with a 50 ms lock wait per connection and a SQLite progress guard.
Filesystem I/O and Python imports are outside a hard wall-clock guarantee. SQLite rows are capped at 2 MiB.
Approvals replay the latest 1,000 matching events, at most 16 KiB per event. Oversized or malformed payloads
leave the section unknown. Questions require a complete regular file no larger than 2 MiB and 5,000 events.
An oversized, malformed or incomplete history is unknown, never a supposedly empty queue.

Databases open with SQLite `mode=ro` and `query_only`. No tables, records, migrations or new databases are
written. SQLite may create its existing database's `-wal`/`-shm` coordination files. The reader preserves
SQLite's locking and WAL visibility. It does not use `immutable=1`, which would ignore live WAL changes.

`--json` emits `version: 1`, observation time, home/agent/question-store scope, and a `sections` object.
Each section contains `status`, `source`, `truncated`, `note` and `items`. Status is `ok` for a successful
bounded read, `partial` when more rows exist than the display limit, or `unknown` for an unavailable source
or uncertain coverage. `ok` is source coverage, not a health verdict. Each item separates `state` from
`recorded_state`. Active historical records have `state: "unknown"`. Item labels are limited to 160 characters
and control characters are removed. The report contains private operational metadata. Keep it local.

Missing tables, optional features, locked/corrupt databases and unreadable files affect only their section.
Exit status is zero when a report is produced, including partial or unknown coverage. Consumers must inspect
section status and `truncated`. Empty items alone do not establish that no work exists.

## Extension boundary

`src/exo/inbox.py` is loaded only for this CLI command. `Snapshot` reuses `SessionStore`'s session read API
without its writing constructor. Other tables use narrow bounded SELECT adapters because their existing
manager APIs require runtime objects or lack agent scope and limits. Questions reuse `QuestionQueue.state`.
No core, protocol or Command Centre changes are needed. A future live adapter must provide a read contract
that cannot trigger runtime startup before it can replace the unknown live states (D-085, issue #505).
