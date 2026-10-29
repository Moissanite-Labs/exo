# Trace and findings

Exo watches itself, so a fix can be measured and an agent can judge its environment from data (D-025).
The loop: record, read, report, verify.

## What is recorded
- `model.call` (event): per model call, the role and route asked for, the route that served it, every attempt
  (`route`, `outcome` ok, timeout, error or fallback, short `error`, `ms`), time to first token, total time,
  tokens (cache reads and writes too), the estimated cost in USD, loop retries. A call that a stop cuts short is
  recorded too, with `stop_reason` `interrupted`. It keeps the attempts of routes that timed out or failed before
  the stop, so a stop on the fallback route still raises `model.timeout` or `model.fallback` for the route before.
  When its only tries timed out, it raises no `model.slow`, because `model.timeout` already names that route.
- `turn.started.soul`: a short hash of the seat's `SOUL.md` text the turn ran under (empty without one), so the trace
  shows which instructions a turn used after an edit.
- `tool.completed`: `duration_ms` and `is_error` on every call.
- `turn.completed.timing`: `wall_ms`, `model_ms`, `tool_ms` (without approval waits), `approval_ms`,
  `model_calls`, `fallbacks`, `tool_errors`. When a stop (ctrl+c or `/stop`) cuts a tool short, its time up to the interrupt counts in
  `tool_ms`, and `interrupted_tool` names it, so an interrupted test run does not read as harness time. A model call
  cut short the same way, mid stream or in the backoff before a retry, counts in `model_ms` and `model_calls`, so a
  stopped slow model does not read as harness time either: a long wait shows as `model.wait`, `model.slow` or `model.timeout`.
- `harness.error` (event): an exception caught in a hook, handler, plugin, memory call, channel or the TUI
  event path: `where`, `error_type`, `message`, a short `traceback`. Never silent.
- A setup state is not a harness error. exo-memory with no principal store raises `exo.core.slots.NotConfigured`
  from its `initialize`, with a message that says what is off and the command that turns it on. The engine catches it
  there and shows the message as one quiet info notice when a thread starts, with no event and no finding. That is
  the only supported place to raise it. Raised anywhere else it gets no notice, and `trace.report` logs it at info
  level with no `harness.error` event or finding, but the caller's own failure handling still runs. The hook runner
  still logs its warning with the traceback. A `pre_tool_call` hook that raises it fails like any approval hook:
  without a required executor that blocks the call, with a `tool.error` finding for the block. A tool handler that
  raises it is still a tool error, with its `tool.error` finding.
- Keys, auth headers and token-like strings are removed before any of this is written.

## Read
`exo trace [SESSION] [--last N] [--calls] [--json]` shows one row per turn: when, wall time, model served,
calls, fallbacks, tools, tool errors, tokens, cost, flag count, then the calls of the last turn (`--calls`: of
every shown turn; each with route, time, tokens in and out, cache read and write, cost), then the flags. Subagent sessions roll into the parent's
turn. Sessions recorded before D-025 have none of the new events, so their rows are derived from timestamps
(marked `~`). In a session `/trace` and the `exo_trace` tool (`last`, `calls`) show the same table plus the model
you run on and the route that served the last call. A call cut short by a stop or an error names no route, so the
header names the last call that does.

### Cost (D-032)
Cost is an estimate in USD per call and per turn (subagent turns included). The router prices each call from
the catalog entry of the route that served it (`input`, `output`, `cache_read`, `cache_write`, USD per million
tokens: uncached input at the input rate, cache reads and writes at their own rates, each token once), or uses the
provider's own figure when it reports one (OpenRouter does). Tokens in are the uncached input on every provider
(D-080). OpenAI-style `prompt_tokens` already count the cached tokens, so that transport splits them out into
cache read, the way Anthropic reports them. The prompt a call sent is tokens in plus cache read and write. Calls
recorded on those routes before D-080 keep the whole prompt in tokens in, and their cost as recorded. A
route with no price shows `unknown`, never `$0`. A turn with some unpriced calls shows the sum of the priced ones
with a `+` (`$0.0210+`). A call that used no tokens costs nothing and is not "unknown".
A call where a route stopped on its provider spend limit (HTTP 402) keeps the figure it did bill, and that figure
carries the same `+`, because that route did work this turn is not billed for and the sum may be short. `trace` JSON
says `undercounted_calls` for those calls, and lists `undercounted` on each call. `unpriced_calls` keeps its own
meaning: a route with no price at all (D-093).
Add or override a price in config.json under `models.models`, for example
`{"models": {"models": {"opencode-go:glm-5.3-flash": {"input": 0.3, "output": 1.2}}}}`; a seat can add or
override prices the same way under `[models.models]`, model by model, and config.json's other prices stay.
Prices of calls already recorded are as recorded: a price added later applies to new calls only.

## Rules
Each flag has a stable signature, so the same problem counts instead of repeating. Limits are in the `trace`
table of config.json. A seat can override any of them in its own `seat.toml` under `[trace]`, key by key; the
rest fall back to config.json (`exo trace` reads the seat of the session it shows).

| signature | fires when | default |
|---|---|---|
| `turn.slow` | the harness's own overhead in a turn over the limit: wall time minus tool time, approval waits and model-call time. A long build, a slow human or a slow provider does not fire it. Old sessions without a timing summary use the recorded tool and call durations. Bug at 5 times the limit | 30 s (`turn_wall_s`) |
| `model.slow:<route>` | one model call (one try on one route) took longer in total than the limit. It flags the route, not the harness. A try that timed out is `model.timeout` instead. Bug at 5 times the limit. A route can have its own limit in `model_call_routes`, for example `{"nous:stepfun/step-5-preview:free": 180}` | 60 s (`model_call_s`) |
| `turn.wall` | total turn wall time, work included, over a separate high cap | 30 min (`turn_total_wall_s`) |
| `model.wait:<route>` | time to first token over the limit | 30 s (`model_wait_s`) |
| `model.fallback:<route>`, `model.timeout:<route>` | a try failed and the call moved on. If the failure opened the route's circuit breaker the flag says how long the route is skipped, and that attempt in `model.call` carries `cooldown_s` (config `[models.breaker]`: `failures`, `cooldown_s`, `max_cooldown_s`; see PROVIDERS.md) | any; breaker 2 failures, 10 min, up to 1 h |
| `tool.error:<tool>:<kind>` | a tool returned an error | any |
| `harness.error:<where>:<type>` | a caught exception | any |
| `shell.masked_exit` | a bash result carried `note: pipeline stage N exited C`: an earlier stage failed but the last one succeeded, so the reported exit was 0 | any |
| `tool.repeat:<tool>` | the same call (a shell command ignoring cd, timeout, pipes) twice in one turn with no file change between. A `write_file`, `edit_file`, `patch` or a shell command that writes (`sed -i`, a redirect, `tee`, `git apply`, `mv`) starts a new count, so rerunning tests after an edit is not a repeat | 2 |
| `subagent.slow` | a subagent task over the limit | 90 s (`subagent_s`) |
| `model.mismatch` | the shown model is not the one that served, with no fallback to explain it | any |

`trace.enabled = false` turns filing off.

## Findings
A row in the `findings` table of sessions.sqlite: id, signature, source (anomaly, agent, principal), severity
(blocker, bug, papercut, idea), title, detail, session, turn, model, harness version, first and last seen,
count, status, fixed_in. One row per signature; a recurrence bumps the count.
- Anomalies are filed at turn end, with no model call.
- A `harness.error` in a main or scheduled turn is filed when that turn ends. One outside a turn, or in a
  subagent's turn, is filed at once.
- The agent files with `exo_harness_report(title, detail, severity)`; the same title dedups.
- `feedback.send` with reject or should_have_been files a principal finding (the learning step still runs). It files
  none for a private thread or a session under it, such as its delegate child (D-028): findings are listed in every
  thread, and the note would carry its text.

Lifecycle: `open`, then `fixed` (`exo findings fix ID --in SHA`), then `verified` after 20 quiet eligible turns
(`verify_turns`) or 3 quiet days of activity (`verify_days`) on a harness at or after SHA. The same signature
coming back on such a harness makes it `reopened`. On an older harness it only counts, and the quiet clock
restarts from zero. "At or after" is a git ancestor check on the source tree; when it cannot be told, the fix is
assumed live.

Only a turn that could have tripped the signature is evidence the fix held (`trace.eligible`; subagent turns count
as part of their parent's turn):

| signature | an eligible turn |
|---|---|
| `turn.slow`, `turn.wall` | any finished turn |
| `model.slow:<route>`, `model.wait:<route>`, `model.fallback:<route>`, `model.timeout:<route>` | made a model call that tried that route |
| `model.mismatch` | had a served model call |
| `tool.error:<tool>:<kind>`, `tool.repeat:<tool>` | called that tool |
| `shell.masked_exit` | called `bash` |
| `subagent.slow` | ran a subagent task |
| `harness.error:...`, an agent's or the principal's report | no rule can tell: counts toward days only |

A quiet eligible turn adds one to the turn count and marks its day (UTC). A turn where no rule can tell marks the
day but adds no turn, so a report with no rule is verified only by days. An unrelated turn, a turn that tripped the
signature, and a turn on a harness older than the fix count for nothing. A day of activity is a day with at least
one counted turn, so idle days do not verify anything. The count and days are kept per finding in `quiet`.

## Commands
- `exo trace [SESSION] [--last N] [--json]`
- `exo findings [--status open|fixed|verified|reopened|all] [--json]` lists every state by default, each fixed one with the commit it was fixed in
- `exo findings fix ID --in SHA`
- `exo findings export --md` (digest for the developer, grouped by severity, newest first)
- `exo findings backlog [--limit 20] [--json]` shows unresolved counts by severity and age, plus the oldest rows (0..100). Backlog includes open, reopened and fixed-awaiting-retest; verified findings remain in total state counts but leave the backlog. Age runs from first seen, in elapsed days; malformed historical timestamps have an unknown-age bucket and future timestamps count as zero. The counts and rows come from one database snapshot, read without the write lock, so the read itself neither waits for nor holds up a core that is filing a finding. Opening the store still runs its schema check in a short write transaction, so the command can wait behind a long write before it reads. This read does not update the last-view marker or any lifecycle state.
- In the TUI: `/trace`, `/findings`; the status line shows the serving route and the open-findings count.

The count reconciles by state. `exo trace`, `/trace` and `exo_trace` start with `findings 7: 3 open, 2 fixed (awaiting re-verification), 1 verified, 1 reopened` (`exo trace --json` has the same under `findings`). Without a session, they list up to five open or reopened findings by id and title (issue #100); the summary line counts the rest. A finding that was fixed moves from open to fixed, so the open count drops without any being lost; `/findings` and `exo findings` list fixed and verified ones too, and say what moved since you last looked (`open -> fixed`, `new`). The status-line badge counts open plus reopened.

The backlog command is the read-only visibility slice of #684. Owner assignment, a triaged state and exactly-once retest notifications are not implemented by it; the existing lifecycle and quiet-turn verification remain unchanged.
