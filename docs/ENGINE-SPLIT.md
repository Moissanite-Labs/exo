# Core engine split proposal

Status: proposed 2026-10-13 for issue #46. Implementation ownership and scheduling are unassigned.
Core maintainers should accept the boundaries after the active authority and execution-broker changes
settle. This proposal is not an accepted decision or a claim that the split is complete.

## Problem and boundary

`CoreEngine` combines application construction, turn lifecycle, protocol dispatch, approvals, model
configuration and diagnostic rendering. It also reads private loop, context-tree and store state and
imports principal/product integrations. Moving methods between files alone cannot lower the measured
core line total: the acceptance test counts core, protocol, server and two CLI adapters together.

Keep the engine responsible for turn orchestration and its public lifecycle. Move application wiring to
a composition layer outside the core. Inject narrow interfaces for principal signals, gate policy,
model discovery and peer transport instead of importing these implementations inside the engine.
Keep protocol schemas and command names stable throughout the migration.

## Sequence and evidence

1. Add public read-only queries on `Loop`, the context engine and session/event store for active-turn state,
   context statistics, trace events and handoff facts. Migrate callers away from `loop._state`, tree `_view`
   and `_agent_key`, and raw `store._q`. Compare returned diagnostics and event ordering before and after.
2. Extract protocol command handling into an adapter over the engine's public methods. Session, approval,
   interrupt, schedule and configuration commands must retain their current authorization boundaries,
   schemas, errors and durable event sequences. Run protocol and real-process restart/replay tests.
3. Extract construction and principal/product adapters. Core imports must point toward shared interfaces;
   the composition layer supplies gate, principal, learning, model-discovery and transport implementations.
   Cover missing/disabled plugins and required-executor startup with integration tests.
4. Review unused slot obligations and duplicate await/bus adapters. Remove obligations only after checking
   plugin contracts, and consolidate equivalent adapters with behavioral tests. This is the part that can
   reduce total code, rather than merely move it.

Each extraction should be a separate PR with a behavior-preservation check and a measured before/after
count. Avoid one large rewrite while the broker and authority contracts are changing.

## Progress

- 2026-10-13, first pass: the diagnostic views (`/context`, `/compact`, `/trace`, `/findings`, handoff facts and the
  built-in `command.run` names) moved from `CoreEngine` to `src/exo/diagnostics.py`, as functions over the engine.
  The factory moved to `src/exo/compose.py` (`exo.compose:create`, the `exo serve` default), the start of step 3;
  `exo.core.engine:create` forwards to it. The engine keeps thin public methods (`trace_text`, `context_report`,
  `context_text`, `compact_text`, `handoff_facts`). Counted core lines: 16,023 before, 15,800 after. These modules
  still read private state (`loop._state`, `store._q`, tree `_view`), which step 1 replaces with public queries.

## Budget policy

Recommendation pending review: retain the current 16,000-line acceptance ceiling while #46 is open.
Do not raise it again to accommodate a
wave or exclude existing counted modules to make a failing test pass. The historical 15,000-line goal is
unmet; splitting one file is not evidence of meeting it.

Before changing the ceiling, decide whether total line count remains useful. If it is retired, replace it
with enforceable import-direction and collaborator-interface checks, with explicit temporary exceptions
that shrink as the steps above land. Dropping the test without those checks leaves the original finding open.

Completion requires public collaborator interfaces, removed inward product imports, behavior-preserving
protocol tests and an honest budget decision. The unsupported-hook repair in #26 addresses one part of
the audit; it does not complete this split. The required executor in #31 changes the dispatch contract and
must be integrated before those interfaces are treated as stable.
