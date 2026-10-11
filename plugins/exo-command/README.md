# exo-command

The Exo command centre as a Hermes plugin. One read-only view with five panels:

1. **Projects.** Sessions from every profile, clustered into probable projects, with
   confidence bands and evidence tiers. Ported from the Continuum plugin.
2. **Live agents and tasks.** Worker cards for live sessions and subagents, plus background
   `delegate_task` fan-outs. Active and Past views. Ported from the subagent viewer.
3. **Kanban.** Tasks from every Hermes board, by column.
4. **Decisions waiting on you.** Read from a JSONL queue in the principal store.
5. **Cost per session.** From the cost columns Hermes already records in `state.db`.

It never writes to a Hermes database. Every `state.db` and `kanban.db` is opened with a
`mode=ro` SQLite URI and `PRAGMA query_only=ON`. There is no write route.

## How it attaches to Hermes

It is a dashboard plugin inside Hermes's own web dashboard. No separate server.

Hermes scans `<hermes home>/plugins/*/dashboard/manifest.json`. This plugin ships:

| File | Role |
|---|---|
| `dashboard/manifest.json` | Adds a **Command** tab at `/command`, after Chat. |
| `dashboard/plugin_api.py` | FastAPI router. Hermes mounts it at `/api/plugins/exo-command/`. |
| `dashboard/dist/index.js` | The tab. Vanilla JS. React and auth come from the host SDK (`window.__HERMES_PLUGIN_SDK__`). No build step, no dependencies. |
| `dashboard/dist/style.css` | Dark, square, high contrast. Works on a phone. |
| `plugin.yaml`, `__init__.py` | Agent side. `register(ctx)` adds the `/exo-command` slash command, a short text digest. `config_schema` declares the settings below. |

Install (not done by this build):

```sh
cp -R plugins/exo-command ~/.hermes/plugins/exo-command
hermes plugins enable exo-command   # or add it to plugins.enabled in config.yaml
```

Hermes only imports a user plugin's `plugin_api.py` when the plugin is in `plugins.enabled`.
Restart the dashboard, or enable it from the Plugins page, and the Command tab appears.

API, all GET:

- `/api/plugins/exo-command/snapshot?fresh=false&sections=projects,agents`
- `/api/plugins/exo-command/section/{projects|agents|kanban|decisions|costs}`
- `/api/plugins/exo-command/health`

## Settings

Set them under `plugins.entries.exo-command.settings` in Hermes `config.yaml`. Each one can
also come from an `EXO_COMMAND_<KEY>` environment variable.

| Key | Default | Meaning |
|---|---|---|
| `hermes_home` | running Hermes root | Which Hermes home to read. |
| `profiles` | all | Profile names to scan. The root `state.db` is called `default`. |
| `include_root_profile` | `true` | Include `<home>/state.db`. |
| `principal_store` | `$EXO_PRINCIPAL_STORE`, else `$EXO_HOME/principals/$EXO_PRINCIPAL` | Principal store root. No guess when unset. |
| `decisions_queue` | `queues/decisions.jsonl` | Relative paths resolve inside the store. |
| `live_window_seconds` | 900 | A session is live if it moved in this window. |
| `past_window_hours` | 24 | Finished subagents shown under Past. |
| `cost_window_days` | 7 | Cost panel window. |
| `cost_top_sessions` | 25 | Rows in the top sessions table. |
| `kanban_done_limit` | 20 | Newest done tasks per board. |
| `project_window_days` | 90 | Older sessions are not clustered. |
| `project_stall_days` | 7 | Quiet days before Stalled. |
| `project_abandon_days` | 60 | Quiet days before Abandoned?. |
| `cache_seconds` | 20 | Per panel cache. Projects never rescan faster than 120 s. |
| `generic_paths` | none | Extra directories that must never group sessions. The user's home, `/`, `/tmp` and similar are always generic. |

Nothing in the code names a principal, a profile or a machine.

## Projects: how clustering works

Same rules as Continuum, without its registry. The board is derived on each scan and never stored.

- **CS-1** shared `git_repo_root`: band **high**.
- **CS-2** shared workspace root, derived from `cwd` (a `work/`, `projects/`, `repos/`, `src/` or
  `code/` segment, else the nearest `.git`): band **medium**.
- **CS-3** shared strong token (repo, path, URL, issue id). It only corroborates a cluster that
  CS-1, CS-2 or CS-4 formed. It never creates or bridges one.
- **CS-4** parent and child sessions fold together: **medium** with CS-2 or CS-3 support, else **low**.
- Noise never seeds a cluster: cron, subagent children (folded into the parent), recurring
  titles, tests and handshakes, group relays, untitled fragments, model evals.
- Generic paths are context, never keys.

Evidence tiers: **T0** means session metadata only. **T1** means a rule matched in a bounded
message probe (first message plus the last six, 600 characters each). Tier 2, a model summary,
is not built.

Lanes come from the lifecycle: Waiting on you, Blocked, Ongoing, Inbox, Stalled, Done, Abandoned?.
Two changes from Continuum: only the newest top-level session decides blocked or waiting, so an
old blocker does not pin a live project; and messages of noise sessions are not read, which
halves scan time.

## Live agents: where the data comes from

The desktop subagent viewer listened to the live `subagent.*` event stream. A dashboard backend
has no such stream, so the cards are built from what Hermes persists:

- `sessions`: live means no `ended_at` and activity inside the live window. A subagent is a
  session with a `parent_session_id` or `source = 'subagent'`.
- `messages`: the newest tool call, the newest assistant text (capped) and its reasoning.
- `async_delegations`: background fan-outs that are still dispatched.

Each card shows status, kind, profile, model, goal, tool line, thinking, output and a copy
button. It observes only. It never steers, interrupts or spawns.

## Decisions queue format (v1)

A UTF-8 JSONL file, append-only. Each line is one event. The state of a decision is the fold of
its events in file order. The JSON Schema is `schemas/decision-event.schema.json`.

```json
{"v":1,"op":"open","id":"dec-20261011-1a2b","ts":"2026-10-11T09:00:00Z","title":"Publish the release?","context":"Changelog is ready.","options":[{"key":"a","label":"Publish"},{"key":"b","label":"Hold"}],"default":"b","deadline":"2026-10-12T09:00:00Z","priority":"high","reversible":false,"exposure":"public","asked_by":{"agent":"release-bot","profile":"ops","session_id":"..."},"links":["https://..."]}
{"v":1,"op":"update","id":"dec-20261011-1a2b","ts":"...","context":"New detail."}
{"v":1,"op":"snooze","id":"dec-20261011-1a2b","ts":"...","until":"2026-10-11T18:00:00Z"}
{"v":1,"op":"resolve","id":"dec-20261011-1a2b","ts":"...","choice":"a","note":"go","by":"principal"}
{"v":1,"op":"withdraw","id":"dec-20261011-1a2b","ts":"...","reason":"moot"}
```

- Required on every line: `op`, `id`, `ts` (ISO-8601 or epoch seconds). `open` also needs `title`.
- Waiting = open and not snoozed. Order: overdue, then priority, then irreversible first, then
  deadline, then age.
- Bad lines are counted and shown. They never break the panel.
- Producers append with `exo_command.decisions.append_event(path, event)`. The file is created
  with mode 600. The dashboard itself never writes it; answers go through the principal's
  normal channel and the producer appends the `resolve`.

## Cost panel

Per session cost is `actual_cost_usd` when above zero, else `estimated_cost_usd` (marked `~`).
Per model figures come from `session_model_usage`. Older Hermes builds without these columns
show the panel as unavailable. Cost per accepted output needs verdict events from the ledger
and is left for when `exo.eval` exists.

## Read-only details

- Every database goes through `exo_command/ro.py`. No other module calls `sqlite3.connect`
  (a test enforces this).
- A WAL database that cannot be opened read-only (no `-shm` file and no right to create one)
  is retried with `immutable=1`. That is still read-only but skips the WAL, so the source is
  flagged as a snapshot.
- Each panel fails on its own. One unreadable profile shows as an error row, not a blank page.

## Tests

```sh
cd plugins/exo-command
python -m pytest -q tests
```

The tests build small fixture databases with the same table and column names the code reads.
They check that a full snapshot leaves every source byte identical, that writes are refused,
the clustering rules, live cards, kanban, the queue fold, costs, settings, the slash command,
and the router (the router test needs `fastapi` and `httpx`, so it skips without them).
