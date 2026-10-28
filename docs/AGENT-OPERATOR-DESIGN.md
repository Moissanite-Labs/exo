# Agent and human work vessel

**Status: PROPOSAL, 2026-10-27.** Exo should help an agent and a person carry work
from intent to checked results. This proposal describes the experience and the
checks required before claiming it works. It changes no policy or defaults.

## Intended experience

A person states an outcome and can steer it while work continues. The agent keeps
the objective, constraints, evidence, open questions and next action inspectable.
After interruption, both can see what happened, what remains uncertain and where
to continue. The interface names the source and age of every saved claim.

The agent can inspect its own context, delegate work, compare results with the
acceptance criteria, and propose lessons from corrections. Initiative stays within
the stated scope and existing permissions. A human correction updates the work
without requiring the person to repeat instructions or approve routine steps.

The person sees attention requests when a decision or missing input blocks useful
progress. Each request explains the consequence and offers a concrete choice.
Status distinguishes running, waiting, interrupted, failed and completed work.
A completed turn alone does not establish that the requested outcome is achieved.

## Source status and stable primitives

The following existing primitives were verified by reading source in the design
checkout. This is source evidence, not native acceptance or deployment evidence.

| Primitive | Existing source and boundary |
|---|---|
| Saved conversation and exact log inspection | `src/exo/core/agentlog.py`: `AgentLogView` and `memory_zoom` open underlying records. |
| Optional working notes | `src/exo/work_notebook.py`, [WORK-NOTEBOOK.md](WORK-NOTEBOOK.md), D-086 and [issue 506](https://github.com/aska-digital/exo/issues/506): revisions, evidence references, bounded resume and stale notes. Disabled by default. |
| Context and recall | `plugins/exo-context/exo_context/engine.py` preserves principal signals and handoffs. `plugins/exo-memory/exo_memory/provider.py` assembles bounded recall. |
| Delegate execution and controls | `src/exo/core/agents/delegate.py` records tasks and receipts. `src/exo/subagents.py` provides list, open, steer and stop for [issue 97](https://github.com/aska-digital/exo/issues/97). |
| Source authority | `src/exo/provenance.py` distinguishes principal, agent, delegate and other ingress authors. Agent notes cannot grant permission. |
| Learning and spend | `src/exo/learn/` and `src/exo/model_spend/` provide existing mechanisms to extend, subject to their current policy and review rules. |

Use the session database, existing log, notebook revisions, task receipts and
protocol events as shared primitives. Keep one canonical implementation in Exo.
Desktop consumes merged shared changes and owns its native shell and packaging.
Do not create another objective store, scheduler or permission system for this UI.

## Underway source branches

Four slices are implemented for review in canonical Exo. The desktop checkout
was audited without edits. These branches are not merged or installed, and do not
establish acceptance of every gate below.

- [Issue 683](https://github.com/aska-digital/exo/issues/683): a bounded checkpoint
  derived from saved records for interruption and compaction continuity. It does
  not infer the current goal, inspect working-tree diffs or recover child agents.
  It must not imply that the entire earlier context has been restored.
- Thread drafts and failed-send recovery in
  `plugins/exo-command/exo_command/static/chat.js`: draft text stays with its
  originating thread during navigation. Drafts are held in memory. This does not
  promise recovery after reload, quit, crash or a different local origin.
- Durable queued steering on `fix/durable-steer-763-20261027`: accepted corrections
  and cancellations are saved before acknowledgement. Terminal and web clients
  restore queued text and controls after reconnect. Reading a session starts no
  work. The guarantee covers saved queue records and transcript insertion, not
  exactly-once external effects. See the [steering contract on its review branch](https://github.com/aska-digital/exo/blob/c59b81bd778671f51046ff599ad5703574279d10/docs/DURABLE-STEERING.md).
- Working notes on `feat/working-notes-inspector-20261027`: a web inspector shows
  the existing notebook's goal, steps, blockers, sources, revisions and stale
  status. It preserves the composer and labels notes as agent-authored claims.
  Reading does not enable or write notes. This is the inspection portion of gate 1,
  not a complete objective-recovery contract.

Desktop [issue 30](https://github.com/aska-digital/exo-desktop/issues/30) tracks
interface restoration. [Issue 31](https://github.com/aska-digital/exo-desktop/issues/31)
tracks a packaged runtime independent of development checkout paths. The desktop
`clients/desktop/ALPHA-PLAN.md` records these gaps. Neither is closed by drafts.

## Proposed priorities and acceptance gates

All gates below are proposed. Passing a gate requires recorded tests and, where
specified, a real client run. Each capability remains separately reviewable.

1. **Objective and evidence continuity.** Expose the existing notebook beside the
   transcript. Show revision, source references and stale status. Resume after a
   restart with the same goal and remaining work. Exercise correction, undo,
   forks, private ancestry and missing evidence. Never promote an extracted last
   message to the authoritative objective or call record presence verification.

2. **Cooperative steering and safe drafts.** Show whether steering was received
   and applied. Preserve thread drafts through navigation. A late send failure
   belongs to its original thread and cannot replace newer typing. Test switching
   threads during send, multiple submissions and lost acknowledgement. Distinguish
   rejected delivery from unknown delivery before offering a retry.

3. **Delegate supervision.** Show each child's task, parent, last activity,
   measured usage, receipt and evidence. Open, steer and stop use existing
   controls and author checks. Test queued work, nested cancellation and a core
   restart. Report abandoned execution honestly. Recovery requires a separate
   contract for remaining work and possible effects before restarting children.

4. **Agent self-inspection and memory sources.** Let the agent and person inspect
   the context and recalled records that shaped an answer. Link summaries to exact
   log entries. Show age, scope and author. Test hidden or removed records, stale
   summaries and truncation. Preserve private-thread boundaries across all views.

5. **Scoped initiative and shared spend.** Display scope, applicable limits and
   measured cost. Admission of parallel work must respect a shared budget, with
   estimates distinguished from billed usage. Test concurrent admissions, retries,
   escalation and incomplete provider counters. Existing per-child token limits
   alone do not establish an aggregate monetary ceiling.

6. **Honest attention.** Save pending questions and blockers with a reason,
   destination and acknowledgement state. Reconnect without losing or repeating
   them. Test interrupted delivery, duplicate events and an answer arriving after
   cancellation. Keep useful independent work moving within the agreed scope.

7. **Learning with correction.** Connect repeated corrections to inspectable
   candidate lessons through the existing learning system. Show supporting records
   and a way to revise or retire a lesson. Test contradictory evidence and stale
   sources. Learned preference must not become authority to change policy.

8. **Native continuity.** Package the desktop runtime and restore selected thread,
   layout and useful transcript position across changing local ports. Move the
   checkout out of reach, then verify launch, reply, reload and quit/reopen on
   Apple Silicon macOS. Keep tokens out of saved preferences and diagnostics.
   Attached mode must preserve the selected core and its data throughout.

## Delivery boundary

Review the checkpoint, draft, steering and notebook-inspector slices separately,
then verify their composition against current main. Shared client work continues
with delegate supervision and evidence inspection. Record each later gate's
evidence separately. Native persistence and packaging need native acceptance
before the desktop promises recovery across launches.
