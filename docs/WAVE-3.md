# Wave 3: UI and learning

Plan of record (D-022). Same method as waves 1 and 2: parallel builders on disjoint paths, no builder
commits, the lead reviews, wires the shared files, runs every suite, commits and pushes.

## Fixed before the wave (lead, done)
`src/exo/protocol/schema.py` now has `feedback.send`, `ui.respond`, `schedule.edit` and the `ui.panel`
event; the generated JSON Schema and TS types are refreshed. Builders code against these and do not edit
schema.py. If a builder needs a field, they say so in their report.

## Builders

| Id | Owns (only these paths) | Builds |
|---|---|---|
| W3-A TUI | `clients/tui/` (package `exo_tui`), `tests/clients/tui/` | Textual client over the protocol Client: chat screen per research/UI.md 4.3 (transcript, activity strip, composer, status line), `/` commands, `@` file context, `!` shell, ctrl+p palette, inline approval card, session picker, ui.panel rendering (all kinds; compact fallback to `text`), verdict keys on the last answer (sends feedback.send), ui.respond on panel open/dismiss/act. Entry `exo_tui.main(argv)`. |
| W3-B Web | `plugins/exo-command/` | exo-command grows into the main web UI on 127.0.0.1:7342: live chat over the protocol (SSE), approvals, ui.panel rendering (full panels), feedback buttons, task and schedule views. Read-only rules for anything it served before stay as they are. |
| W3-C Learn | `src/exo/learn/` (new), `src/exo/eval.py`, `src/exo/consolidate.py`, `src/exo/taste.py`, `src/exo/capture.py`, `tests/learn/`, `tests/eval/`, `tests/consolidate/` | The fused loop: signals (feedback.send, ui.respond, capture incl. "Important correction ... NOT X" phrasing), fast loop after a turn on the aux route, typed proposals, promotion policy (LEARNING-LOOP 5.6), revert, nightly climb. Eval adds per 6.8: judge_grader, rule_grader, build_replays, split, compare, gate, scoreboard. Handler functions `handle_feedback(engine, body)` and `handle_ui_respond(engine, body)`, and `attach(engine)` that registers hooks. |
| W3-D Schedule and ui tool | `src/exo/core/scheduler.py` (new), `src/exo/core/tools/builtin/ui.py` (new), `tests/core/scheduler/`, `tests/core/tools/test_ui_tool.py` | Scheduler: jobs in `$EXO_HOME/schedule.sqlite`, cron line or "every N", runs a fresh session at the job's tier, reports through task.updated, delivers by notice. `handle_schedule_edit(engine, body)`. The `ui` tool the model calls to open, update or close a panel; emits ui.panel; validates kind and requires a text fallback. |
| W3-E Carry-overs | `plugins/exo-gate/`, `src/exo/core/models/router.py`, `src/exo/core/loop.py`, `src/exo/core/agents/delegate.py`, `src/exo/core/sessions.py`, `src/exo/core/seats.py`, matching tests | D-021 open items: seat signing key (generated per seat, 0600, path in seat toml) so gate decisions are ledgered; usage.updated.model reports the route that actually served after fallback; child usage rolls into parent totals; finished child sessions are locked against new turns; delegation to another seat over the bus (design note plus minimal send if small). |

**Lead only:** `src/exo/core/engine.py` (handlers `_feedback_send`, `_ui_respond`, `_schedule_edit` calling the
builders' functions, `learn.attach`, scheduler start and stop), `src/exo/cli.py` (`exo tui`), `pyproject.toml`
(`exo[tui]` extra with textual), docs, DECISIONS.md.

## Rules for builders
- Model: sonnet. Subagents of a builder: haiku for reading and running.
- Hermetic tests: build engines with `env={}`; no network, no real keys. Plugin suites run one at a time.
- Core line budget stays under 15k (`tests/integration/test_wave1_acceptance.py`); `clients/` and `exo.learn`
  are outside the core.
- Plain short English in docs and user-facing strings, no em dashes. No path, name or account of A in code.
- Report: files changed, tests added and passing counts, what is stubbed, any schema field you need.

## Acceptance (lead)
1. Root suite and every plugin suite green; budget test green.
2. Live on OpenCode Go in a temp EXO_HOME: `exo tui` session with a tool call; the model opens a diff panel
   with the ui tool; an `edit` verdict is captured and the fast loop writes a typed proposal; a
   `schedule.edit add` job with "every 1m" runs once and reports; a gate decision is written to the ledger;
   Jev answers a gate call (or the fallback is recorded).
3. Footprint on charminar (via saga-minar): TUI cold start and streaming check against D-022's escape hatch.
4. Canary: A uses the Exo TUI on the canary seat continually and sends notes as he goes (D-024). A lesson
   is promoted only after it scores better on held-out cases.
