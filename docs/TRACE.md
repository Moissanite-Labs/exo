# Trace and findings

Exo watches itself, so a fix can be measured and an agent can judge its environment from data (D-025).
The loop: record, read, report, verify.

## What is recorded
- `model.call` (event): per model call, the role and route asked for, the route that served it, every attempt
  (`route`, `outcome` ok, timeout, error or fallback, short `error`, `ms`), time to first token, total time,
  tokens, loop retries.
- `tool.completed`: `duration_ms` and `is_error` on every call.
- `turn.completed.timing`: `wall_ms`, `model_ms`, `tool_ms` (without approval waits), `approval_ms`,
  `model_calls`, `fallbacks`, `tool_errors`.
- `harness.error` (event): an exception caught in a hook, handler, plugin, memory call, channel or the TUI
  event path: `where`, `error_type`, `message`, a short `traceback`. Never silent.
- Keys, auth headers and token-like strings are removed before any of this is written.

## Read
`exo trace [SESSION] [--last N] [--json]` shows one row per turn: when, wall time, model served, calls,
fallbacks, tools, tool errors, tokens, flag count, then the flags. Subagent sessions roll into the parent's
turn. Sessions recorded before D-025 have none of the new events, so their rows are derived from timestamps
(marked `~`). In a session `/trace` and the `exo_trace` tool show the same table plus the model you run on.

## Rules
Each flag has a stable signature, so the same problem counts instead of repeating. Limits are in the `trace`
table of config.json.

| signature | fires when | default |
|---|---|---|
| `turn.slow` | turn wall time over the limit | 60 s (`turn_wall_s`) |
| `model.wait:<route>` | time to first token over the limit | 30 s (`model_wait_s`) |
| `model.fallback:<route>`, `model.timeout:<route>` | a try failed and the call moved on | any |
| `tool.error:<tool>:<kind>` | a tool returned an error | any |
| `harness.error:<where>:<type>` | a caught exception | any |
| `tool.repeat:<tool>` | the same call (a shell command ignoring cd, timeout, pipes) twice in one turn | 2 |
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
