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
    approver checks moved with the desk to `exo.core.approvals`).
- 2026-10-15, budget policy (D-077): the hard 15,000-line ceiling became a ratchet with headroom (see "Budget
  policy"). No code changed. Counted core lines: 14,999 before and after, against the floor of 14,813 and a ceiling
  of 15,813 (that ceiling is off since 2026-10-27, D-083).
- 2026-10-27, ceiling dropped (D-083): the count is reported, not asserted. The floor and the two step 4 structure
  caps stay.

## Budget policy

Decided 2026-10-15 (D-077), amended 2026-10-27 (D-083). It replaced the hard 15,000-line ceiling that D-067 set on
2026-10-13; the ceiling is now off entirely. The principal's ruling of 2026-10-15: "important to be lean and
optimized but not at the cost of functionality and robustness and product-market fit". The ruling of 2026-10-27:
"we're dropping the ceiling for now as we build out the codebase, it was too restrictive".

1. **One recorded number, and no ceiling.** `tests/integration/test_wave1_acceptance.py` holds
   `CORE_FLOOR = 14813`, the count right after the last real reduction, #124 (D-067). That branch measured 14,812
   before its last merge from main, and the count at the merge is 14,813. The line ceiling was failing its own test:
   on 2026-10-27 `main` at `13cea687` counted 15,828 against a 15,824 ceiling, 4 over, while the work it blocked was
   ordinary product work. D-083 took the ceiling off. The count is printed on every run and nothing about lines is
   asserted.
2. **The floor ratchets down.** Every real reduction PR (deletions, simplification, real seams per this plan) sets
   `CORE_FLOOR` to its post-merge count in the same PR. The floor never goes up, so a reduction that ends above the
   current floor leaves it where it is.
3. **The count is a report.** The test prints the count and its distance from the floor (`pytest -s` or `-rP` shows
   it). No PR waits on a line number and no headroom note is needed to land one.
4. **Nothing is blocked by the budget.** Fixes and features land on their own merits. A count that climbs far past
   the floor is news for the lead and a reason to schedule a reduction, never a reason to refuse work.
5. **The structural goal carries the real intent.** Step 4's criteria are the goal: no private-state reads across
   module boundaries (`loop._state`, `store._q`, the tree's `_view` and `_agent_key`), no product imports into the
   engine (the inward imports listed under "Progress"), and one responsibility per module. Two of the counts are
   asserted, and they ratchet down like the floor (D-083): `INWARD_IMPORTS_MAX = 39` imports of
   a product module from the core, and `PRIVATE_STATE_MAX = 8` cross-module private-state reads. Relative imports
   such as `from .. import envscrub` count like `from exo import envscrub`. Exported symbols such as `__version__`
   are not modules, and repeated forms in one file count once. Neither cap may be raised
   to pass a false positive; fix the reader. The test also prints the largest counted files and flags any above 1,000
   lines. That report is information, not an assertion.
6. **The `CORE` list is never edited to dodge the count,** as before. Excluding an existing counted module to make a
   failing test pass is not a reduction.

The count is not the completion test for #46. The private-state reads and inward product imports in point 5 remain to
be removed whatever the count says.

If total line count is ever retired as a signal, replace it with enforceable import-direction and
collaborator-interface checks, with explicit temporary exceptions that shrink as the steps above land. Dropping the
test without those checks leaves the original finding open.

Completion requires public collaborator interfaces, removed inward product imports and behavior-preserving protocol
tests. The unsupported-hook repair in #26 addresses one part of the audit; it does not complete this split. The
required executor in #31 changes the dispatch contract and must be integrated before those interfaces are treated as
stable.
