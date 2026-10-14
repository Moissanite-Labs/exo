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
- 2026-10-13, step 4 (D-067): counted core lines 15,986 before (main at `58f064f`), 14,812 after, 1,174 fewer
  (15,951 to 14,777 on the starting base `2c55d26`).
  About 300 of those are code that is gone. The rest left the core through two seams. Every change is its own commit.
  - **Deleted or collapsed** (about 300 lines):
    - The decision models' score and noul asks and the `key_found` lookup, which nothing outside tests called.
    - Two fuzzy edit strategies that `line_trimmed` always beat.
    - The process-group stash and win32 branches (every spawn starts its own session).
    - A second `_maybe_await`, five inline copies of it, and two copies of the plugin keyword filter, now
      `hooks.maybe_await` and `hooks.accepted`.
    - The scheduler's copy of `HubBus`.
    - The CLI providers' duplicated process helpers and constructor (now `CliProvider`), and the router's per-CLI
      credential branches.
    - 59 `@dataclass` lines that the protocol decorators now apply.
    - Dead helpers in `slots.py`, `sessions.py`, `peer_target.py`, `mcp_client.py` and `skills.py`.
    - The engine's branch for a context tree without recall, which never ran.
  - **Seam: open approvals** (`exo.core.approvals.ApprovalDesk`, in the core). The engine kept five parallel dicts
    per request. The desk holds one record per request and owns ask, list, respond and close-once. The engine calls
    `engine.approvals`. The D-033 and D-039 checks and the D-045 close are unchanged. This made the engine simpler,
    not the core smaller (20 lines more).
  - **Seam: peer transport** (`exo.peers`, out of the core, 561 lines). This is step 3's "peer transport" item. The
    transport was already host-agnostic: the Hermes bus plugin runs `PeerNode` with no engine. `PeerService` owns
    the node, the `peers` and `peer_send` tools and the in-process peer socket. The engine knows four calls
    (`start`, `stop`, `ready`, `command`) on an object `exo.compose` passes in as `attach_peers`. It no longer
    imports `exo.server.app`. `PeerConfig` (ingress policy, which bus ingress also reads) and `EngineTarget` (the
    ingress target for peers, bus and jobs) stay in the core. Tests: `tests/peers`, `tests/contract/test_peers_hermes.py`,
    the exo-bus plugin suite.
  - **Seam: scheduler** (`exo.scheduler`, out of the core, about 320 lines). Scheduled jobs are a product feature
    built on the engine's turn API, like `exo.learn`, which composition already attaches. The engine starts, stops
    and interrupts the scheduler and forwards `schedule.edit`. It does not know about cron, `schedule.sqlite`, the
    per-run loops or delivery. The members the scheduler uses became public (`run_turn`, `channels`,
    `Delegator.route`). Tests: `tests/core/scheduler`.
  - **Slot obligations reviewed, kept.** `update_from_response` is called (`ContextAdapter.on_turn_complete`), so the
    audit's note that it never is was wrong. The memory and context base-class defaults that Exo itself never calls
    stay: exo-context and exo-memory also run under Hermes, which calls them, and exo-context delegates to them.
  - **Still open for #46.** Step 1: the engine, `peer_target`, `diagnostics`, `findings`, `jobs` and `delegate` still
    read `loop._state`, `store._q` or tree `_view`/`_agent_key`. Inward imports left in the engine: `exo.principal`,
    `exo.config`, `exo.provenance`, `exo.diagnostics` and lazy `exo.learn` and `exo.models_cmd` (the `exo.gate`
    approver checks moved with the desk to `exo.core.approvals`). The budget decision below is still pending.

## Budget policy

Decided 2026-10-13 (D-067): the acceptance ceiling is **15,000** counted lines
(`tests/integration/test_wave1_acceptance.py`), down from 16,000. Step 4 brought the count under the historical
15,000-line goal (14,812 at D-067), two thirds of it by moving two subsystems out through real seams, and the lower
ceiling keeps that gain. Do not raise it again to accommodate a wave or exclude existing counted modules to make a
failing test pass. A new core feature fits under 15,000 or comes with a cut or a seam.

The ceiling is not the completion test for #46. Step 4's completion criteria still stand: private-state reads
(`loop._state`, `store._q`, the tree's `_view` and `_agent_key`) and the inward product imports listed under
"Progress" remain to be removed.

Before changing the ceiling, decide whether total line count remains useful. If it is retired, replace it
with enforceable import-direction and collaborator-interface checks, with explicit temporary exceptions
that shrink as the steps above land. Dropping the test without those checks leaves the original finding open.

Completion requires public collaborator interfaces, removed inward product imports, behavior-preserving
protocol tests and an honest budget decision. The unsupported-hook repair in #26 addresses one part of
the audit; it does not complete this split. The required executor in #31 changes the dispatch contract and
must be integrated before those interfaces are treated as stable.
