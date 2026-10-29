# exo-command

The Exo command centre: a web client of the Exo protocol. It holds no state of its own that matters.
Everything it shows comes from commands sent to the core (`exod`) and from the ordered event stream
the core publishes. It does not read another program's database.

Panels:

1. **Approvals.** Open `approval.requested` events. Answer from the page (`approval.respond`); any
   other client can answer too, and the page follows `approval.resolved`.
2. **Decisions waiting on you.** The decisions JSONL queue in the principal store, plus
   `decision.queued` events from the core.
3. **Live sessions.** Cards for sessions that moved recently, with status, model, newest tool call,
   newest output and cost. Subagent sessions (children of a delegated task) are marked, and finished
   ones move to Past.
4. **Tasks.** The delegation board. `tasks.list` gives the starting picture, `task.updated` events move
   cards across queued, running, done, failed and cancelled as they happen.
5. **Projects.** Sessions clustered into probable projects, with confidence bands and evidence tiers.
   Ported from the Continuum plugin.
6. **Cost per session.** Folded from `usage.updated` events, by session, seat and model.

## Run it

```sh
exo serve                      # the core, in one terminal
exo web                        # the web client: prints http://127.0.0.1:7342/#token=... and opens the page
```

Every `/api/` route except `POST /api/launch` needs the token in that link (D-039). `POST /api/launch` is served without it, because it trades a one-time launch code for the token (#177), and the page shell (`/` and `/static/`) is served without it, behind the Host check only.
`exo web` prints the link and opens the page in your browser with a one-time code that the page trades for the token (#166), so the token never sits in the browser
launcher's argv; open the latest complete printed link yourself after `--no-open`, a restart or a
non-loopback `--host` (no browser is opened then).
The bare URL has no token. Each web launch creates a new token; old links expire. The tab keeps it in
`sessionStorage` only, never permanent browser storage. See `docs/ui/WEB.md`.

`exo web` finds this folder (`$EXO_PLUGIN_DIRS`, `$EXO_HOME/plugins`, then the repo's `plugins/`) and
calls `exo_command.__main__.main()`; arguments after `web` go to it. `python -m exo_command` still works
with `PYTHONPATH=plugins/exo-command`.

Options: `--socket PATH` (default `$EXO_HOME/run/exod.sock`), `--url URL --token-file FILE` for a core
on TCP, `--host`, `--port`. It uses the standard library only. A second `exo web` on a port in use says whether
Exo's web client already runs there (its open page and printed link still work, Ctrl+C in its terminal stops it)
or another program holds it, and suggests `--port`.

The slash command `/exo-command` prints a short text digest of the same panels (it connects to the
running core).

## How it works

| Module | Role |
|---|---|
| `source.py` | `ClientSource` (HTTP + SSE through `exo.protocol.client.Client`) or `DispatcherSource` (an in-process server, for tests). Both give `call(body)` and `stream(since)`. |
| `state.py` | `State`, a pure reducer: `load_sessions`, `load_tasks`, `apply(event)`. No I/O. |
| `service.py` | `CommandCentre`: `bootstrap()` (hello, `events.subscribe` replay, `session.list`, `tasks.list`), `follow()` (live stream), `sync()` (one poll), section builders and `snapshot()`. |
| `server.py` | `WebServer`: asyncio HTTP, a Server-Sent Events feed of changes, one write route. |
| `projects.py`, `decisions.py`, `costs.py` | Panel logic. `projects.board` still scans Hermes databases for the importer. |
| `static/` | `index.html`, `app.js` (vanilla, no build step) and `style.css`. |

Web routes: `GET /`, `GET /api/snapshot?sections=a,b&fresh=true`, `GET /api/section/<name>`,
`GET /api/health`, `GET /api/stream` (SSE), `POST /api/approvals/<id>` with `{"decision": "allow|deny|allow_always"}`.
The server binds to loopback, and a POST from another origin is refused.

On start the client reads the last 5000 events so recent approvals, usage and task moves are not missed.
When its stream ends (a core restart, or a stream the core ended for falling behind, D-079), it replays from the
last event it applied, so it misses nothing. That holds up to 16,384 missed events (`RESUME_GAP`, four times the
core's limit). After a longer gap, such as a long outage while the core kept working, it reads the last 5000
events as on start, so one reply never carries the whole gap. An approval asked before those does not show.

## The web UI

`exo web` serves one page on 127.0.0.1:7342 with five views: **Chat** (the default), **Tasks**,
**Schedule**, **Command** (the overview below) and **Providers**. It is plain HTML, CSS and ES modules, with no build step
and nothing loaded from another site. Appearance offers Dark (the default), Light or System and saves
only that choice in browser storage. Navigation supports Back, Forward and a keyboard skip link.

Chat uses readable conversation text, a separate composer action row, local search over loaded
conversation titles, IDs and agent names, and a Show latest messages control for both conversation
layouts. Status filters show working conversations and pending approvals. Search never submits a
prompt or changes the selected conversation.

Exopet adds an activity glyph with Mini, Full and Off controls. Full includes a verified growth card and an
empty-thread lattice; Off stops pet reads and rendering. Personal XP is banked while the community hatch is
pending. No requests go to the project site. See [the implementation notes](../../docs/ui/EXOPET.md).

- **Chat:** sessions on the left, a transcript with streamed text, tool lines, approval cards and a composer
  in the middle, and the side panel area on the right. A reply streams as plain text and, once complete, is
  rendered as Markdown (headings, lists, code, quotes and pipe tables, built as DOM nodes, never as HTML).
  Each answer has accept, edit (with a text box) and reject, which send `feedback.send`. Enter sends; while
  a turn runs it steers.
  A context note from other threads (`meta.note`, D-028) is for the model and is not drawn, as in the terminal
  client.
- **Panels:** `ui.panel` events open panels beside the chat. Kinds: markdown, diff, table, tasks,
  approval, taste, choice, file, chart. A markdown panel uses the same renderer as a completed reply, so it
  keeps its line breaks. A panel whose data is missing or odd shows its `text`. The page sends `ui.respond`
  for opened, dismissed and acted (a choice or a taste verdict).
- **Tasks:** `tasks.list`, kept current by `task.updated`.
- **Schedule:** `schedule.edit` list, add, pause, resume, run now, remove. Remove has no undo, so it waits for a
  separate Confirm remove click.

The server relays the protocol, it adds no state of its own beyond a ring of the last 3000 events and the
list of open panels. When a resume cursor falls outside that ring, SSE signals the gap, replays the
core’s durable event log, and the page refreshes its transcript, live sessions, panels and approvals. Routes (`api.py` has the table): `GET /api/events` (SSE of every event, `?since=`,
`?session=` or `Last-Event-ID`), `/api/chat/sessions`, `/api/chat/sessions/<id>` (+ `/send`, `/interrupt`, `/model`),
`/api/chat/models`,
`/api/chat/panels`, `/api/complete/commands`, `/api/complete/files` (guarded, D-037), `/api/feedback`, `/api/ui/respond`, `/api/tasks`, `/api/schedule`. Every write checks
its input, refuses a request from another origin, and answers 501 if the core does not handle that
command yet.

Panel `data` shapes the page reads: diff `{files:[{path,diff}]}` or `{diff}`; table `{columns,rows}`;
tasks `{tasks:[{title,status,detail}]}`; choice `{prompt,options:[{key,label}]}`; approval `{options}`;
taste `{rules:[{rule_id,text}]}`; file `{path,content,start_line}`; chart `{series:[{label,value}]}` or
`{points:[{x,y}]}`; markdown `{markdown}`.

## Hermes importer

The Hermes readers moved to `src/exo/ingest/hermes_dashboard.py` (`live_agents`, `kanban_tasks`,
`cost_per_session`, and the read-only `open_ro`). They stay for migration: they turn a Hermes home into
plain dicts. Every database opens with a `mode=ro` URI and `PRAGMA query_only=ON`. A test checks that the
plugin itself never opens SQLite.

## Settings

Set them as `EXO_COMMAND_<KEY>` environment variables or pass them to `load_settings`.

| Key | Default | Meaning |
|---|---|---|
| `socket` | `$EXO_HOME/run/exod.sock` | Core socket. |
| `url`, `token`, `token_file` | none | Core on TCP. The token falls back to `$EXO_TOKEN`, then `$EXO_HOME/run/exod.token`. |
| `host`, `port` | `127.0.0.1`, `7342` | Where the web client listens. |
| `principal_store` | `$EXO_PRINCIPAL_STORE`, else the store of `$EXO_PRINCIPAL`: config `principals.<id>`, else `$EXO_HOME/principals/<id>` | Principal store root. No guess when unset. |
| `decisions_queue` | `queues/decisions.jsonl` | Relative paths resolve inside the store. |
| `live_window_seconds` | 900 | A session is live if it moved in this window. |
| `past_window_hours` | 24 | Finished subagent sessions shown under Past. |
| `cost_window_days` | 7 | Cost panel window. |
| `cost_top_sessions` | 25 | Rows in the top sessions table. |
| `project_window_days` | 90 | Older sessions are not clustered. |
| `project_stall_days` | 7 | Quiet days before Stalled. |
| `project_abandon_days` | 60 | Quiet days before Abandoned?. |
| `cache_seconds` | 20 | Projects never rescan faster than 120 s. |
| `generic_paths` | none | Extra directories that must never group sessions. The user's home, `/`, `/tmp` and similar are always generic. |

Nothing in the code names a principal, a seat or a machine.

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
message probe (first message plus the last six, 600 characters each, taken from `message.completed` events). Tier 2, a model summary,
is not built.

Lanes come from the lifecycle: Waiting on you, Blocked, Ongoing, Inbox, Stalled, Done, Abandoned?.
Two changes from Continuum: only the newest top-level session decides blocked or waiting, so an
old blocker does not pin a live project; and messages of noise sessions are not read, which
halves scan time.

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
  with mode 600. The command centre never writes it; answers go through the principal's
  normal channel and the producer appends the `resolve`.

## Cost panel

Per session cost is the core's `session_total.cost_usd` from `usage.updated`. A session the core could
not price (a local model, an unknown rate) shows its tokens and `unpriced`. The model shown is the one
named by the newest `turn.started`. Cost per accepted output needs verdict events from the ledger and is
left for when `exo.eval` publishes them.

A subagent's spend counts once. When a delegated child finishes, the core adds its total to the parent's
`session_total` and publishes `usage.updated` on the parent. From then on the table keeps the child's row,
marked `in parent` (`rolled_up` in the data), and the total, the seat and model bars and the session counts
leave it out. That spend then shows under the parent's seat and model. A child that is still running, the
session of a scheduled job and a fork are summed as their own sessions, because the core rolls none of them
into a parent.

## Tests

```sh
cd plugins/exo-command
python -m pytest -q tests
```

`test_protocol.py` runs a real core in process with a fake model: it delegates a task and checks that
the card appears as running and moves to done through `task.updated`, plus approvals, replay and the web
server. The other files cover the Hermes importer (fixture databases), clustering, the queue fold and settings.
