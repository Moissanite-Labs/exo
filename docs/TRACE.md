# Trace and findings

Exo watches itself, so a fix can be measured and an agent can judge its environment from data (D-025).
The loop: record, read, report, verify.

## What is recorded
- `model.call` (event): per model call, the role and route asked for, the route that served it, every attempt
  (`route`, `outcome` ok, timeout, error or fallback, short `error`, `ms`), time to first token, total time,
  tokens (cache reads and writes too), the estimated cost in USD, loop retries.
- `turn.started.soul`: a short hash of the seat's `SOUL.md` text the turn ran under (empty without one), so the trace
  shows which instructions a turn used after an edit.
- `tool.completed`: `duration_ms` and `is_error` on every call.
- `turn.completed.timing`: `wall_ms`, `model_ms`, `tool_ms` (without approval waits), `approval_ms`,
  `model_calls`, `fallbacks`, `tool_errors`. When Esc cuts a tool short, its time up to the interrupt counts in
  `tool_ms`, and `interrupted_tool` names it, so an interrupted test run does not read as harness time.
- `harness.error` (event): an exception caught in a hook, handler, plugin, memory call, channel or the TUI
  event path: `where`, `error_type`, `message`, a short `traceback`. Never silent.
- Keys, auth headers and token-like strings are removed before any of this is written.

## Read
`exo trace [SESSION] [--last N] [--calls] [--json]` shows one row per turn: when, wall time, model served,
calls, fallbacks, tools, tool errors, tokens, cost, flag count, then the calls of the last turn (`--calls`: of
every shown turn; each with route, time, tokens in and out, cache read and write, cost), then the flags. Subagent sessions roll into the parent's
turn. Sessions recorded before D-025 have none of the new events, so their rows are derived from timestamps
(marked `~`). In a session `/trace` and the `exo_trace` tool (`last`, `calls`) show the same table plus the model
you run on.

### Cost (D-032)
Cost is an estimate in USD per call and per turn (subagent turns included). The router prices each call from
the catalog entry of the route that served it (`input`, `output`, `cache_read`, `cache_write`, USD per million
tokens; cache reads at the cache-read rate, and for OpenAI-style providers, whose `prompt_tokens` already count
cached tokens, only the uncached part at the input rate), or uses the provider's own figure when it reports one
(OpenRouter does). A route with no price shows `unknown`, never `$0`. A turn with some unpriced calls shows the
sum of the priced ones with a `+` (`$0.0210+`). A call that used no tokens costs nothing and is not "unknown".
Add or override a price in config.json under `models.models`, for example
`{"models": {"models": {"opencode-go:glm-5.3-flash": {"input": 0.3, "output": 1.2}}}}`; a seat can set the
same under `[models.models]`. Prices of calls already recorded are as recorded: a price added later applies to
new calls only.

## Rules
Each flag has a stable signature, so the same problem counts instead of repeating. Limits are in the `trace`
table of config.json. A seat can override any of them in its own `seat.toml` under `[trace]`, key by key; the
rest fall back to config.json (`exo trace` reads the seat of the session it shows).

| signature | fires when | default |
|---|---|---|
| `turn.slow` | the harness's share of a turn over the limit: wall time minus tool time and approval waits (model plus overhead). A long build or a slow human does not fire it. Old sessions without a timing summary use the recorded tool durations. Bug at 5 times the limit | 60 s (`turn_wall_s`) |
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
- The agent files with `exo_harness_report(title, detail, severity)`; the same title dedups.
- `feedback.send` with reject or should_have_been files a principal finding (the learning step still runs).

Lifecycle: `open`, then `fixed` (`exo findings fix ID --in SHA`), then `verified` after 20 quiet turns
(`verify_turns`) or 3 days (`verify_days`) on a harness at or after SHA. The same signature coming back on such
a harness makes it `reopened`. On an older harness it only counts, and the quiet clock restarts. "At or after"
is a git ancestor check on the source tree; when it cannot be told, the fix is assumed live.

## Commands
- `exo trace [SESSION] [--last N] [--json]`
- `exo findings [--status open] [--json]`
- `exo findings fix ID --in SHA`
- `exo findings export --md` (digest for the developer, grouped by severity, newest first)
- In the TUI: `/trace`, `/findings`; the status line shows the serving route and the open-findings count.
