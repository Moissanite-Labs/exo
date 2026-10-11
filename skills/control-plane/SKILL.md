---
name: control-plane
description: "Use for any multi-agent dispatch, rotation, concurrency, GitHub workflow, or learning decision. Compact trigger index; load only the bundle the task names."
version: 2.0.0
author: Protean Team, generalized for Exo
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Orchestration, Dispatch, Rotation, Concurrency, Gates, SOP]
    related_skills: [operating-doctrine, council, clean-writing, github-pr-audit, github-draft-review-html, github-issues, choreography]
---

# Control plane: the one home for dispatch procedure

This skill is the procedure half of the control plane (the law). The state half (the records) is
append-only and lives in the principal store, not in this skill.

## Roles and layers

Three layers. Each layer only talks to the one below it.

| Layer | Role | Does | Never does |
|---|---|---|---|
| 1 | mission control (`{{mission_control_seat}}`) | relays to the principal, condenses state, dispatches and supervises orchestrator instances across projects | project work, file edits, public writing, merges, deploys, dispatching a specialist directly |
| 2 | orchestrator (`{{orchestrator_seat}}`) | runs one project or session, dispatches specialists, integrates, gives final review | reads or patches project code itself, bypasses QA |
| 3 | specialists | researcher, architect, designer, builder, reviewer (QA) | work outside their owned files |

Mission control's only execution surface is launching an orchestrator. Its verification surface is
the delegation receipt (section N). The seat names are placeholders. A principal binds them to real
agents in the principal store under `agents/`.

**Records.** Default location: `<store>/records/ops/` (override with `$EXO_OPS_DIR`). Files:
`ROTATION-STATE.md`, `INFLIGHT.md`, `LEARNINGS.md`, `DISPATCH-LEDGER.md`, `OWNERSHIP-MATRIX.md`,
`CONTRIB-STATE.md`. Each is append-only. The orchestrator owns this skill and the records. QA owns
gate verdicts and may not author fixes. The builder builds gates.

**Principal-specific rules** (named agents, standing rulings, owner-only action lists, posting
rules, budgets) live in the principal store under `policies/` and `agents/`. This skill ships the
mechanism only. Where a rule needs a value, it says so with a `{{placeholder}}`.

## A. Trigger index (route here, nowhere else)

Load the minimum bundle. Do not load all skills. A trigger names procedures; it never copies them.

| Intent | Minimum skill bundle | Roles | Mandatory gates |
|---|---|---|---|
| GitHub notification triage | `github-issues`, `github-pr-audit` for review or merge, `clean-writing` for human text, `github-draft-review-html` for drafts the principal reviews | researcher writes, builder implements, reviewer audits, orchestrator integrates | no post or merge without approval plus read-back; prose gate; draft pipeline gates (section C) |
| GitHub PR or issue draft | `github-draft-review-html` (one command: `draft_pipeline.py`) | researcher authors; a second member audits | quotes sliced from source, never retyped; prose gate |
| Merge or review decision | `github-pr-audit` | reviewer audits; orchestrator integrates | live-head read-back; no merge without approval |
| Any multi-agent project | `operating-doctrine`, `choreography` | orchestrator dispatches; reviewer QA; orchestrator final review | model receipt; rotation; inflight claim; learnings |
| Major decision (canonical home, layer model, gate, dispatch rule, public surface, credential or model policy, anything irreversible) | this row plus the decision's own bundle | designer renders the report; orchestrator approves | an HTML decision report exists and is linked before the change lands |
| Two-team bounce (build team and review team on one PR) | `operating-doctrine` section 15, `github-pr-audit` | builders implement and iterate; reviewers audit and merge | fresh live-head read-back before each review and merge; formal review plus read-back |
| Council | `council` | facilitator plus research and QA minimum | bounded question; evidence and dissent recorded; owner gates restated verbatim |
| Contribution mode | section G, `github-pr-workflow`, `github-draft-review-html` | one idle evaluator dispatches one lane; builder builds; reviewer audits | live owner authorization for external writes; evidenced gap and duplicate search; exact-bytes approval |

## B. Mandatory preflight (before the first write of any dispatch)

1. Read `ROTATION-STATE.md`, `INFLIGHT.md`, `OWNERSHIP-MATRIX.md`. Record the read time and md5 of
   the matrix in the ledger row.
2. The brief declares: role, allowed decisions, forbidden decisions, owned files, upstream stable
   handoff, exit evidence. A dispatch without these fields is invalid.
3. Acquire or verify an inflight claim on the exact file set before the first write. Record an
   md5 or mtime baseline right before each write.
4. Read the target profile's `config.yaml` right before dispatch. The brief names the provider and
   model literally, as the config reads at that moment. Never a remembered model, never a preset
   alias.
5. Record provider and model in the ledger row. After completion, check the profile's usage record.
   Config model, ledger model and usage model must match, or the row is `needs-decision`. A harness
   fallback after a provider error is not a worker substitution: accept it when the receipt names
   the cause.
6. Rotate to a different capable worker where possible. Never interrupt a healthy worker.
7. Never prewrite the session id. Write the ledger row as `pending actual`, then fill the real id
   from the worker's own output log.
8. Spawn shape: pass the absolute lane directory as the worker's write root. A `cd` in the
   wrapping shell does not set it. A kanban card that writes outside a scratch dir needs a `dir`
   workspace pointing at its targets.
9. A card that edits a live install needs one line saying in-place edits are intended and why that
   is safe. A prohibition names the action (reload, restart, delete), never the file.
10. A fan-in parent never parents a lane it depends on. Create working lanes parent-free.
11. A decomposition never re-creates a lane that is already running. Never split one file across
    concurrent writers. QA never authors the artifact it verifies.
12. A killed lane leaves a real artifact. Snapshot and hash it, classify what is there, close the
    dead card as budget-exit (never as done), and dispatch a finish-and-account lane that says
    `do not start over`.
13. A brief that requires a receipt pastes the receipt grammar verbatim, inside a fenced block.
14. Before reporting a file as missing, read the constant in the code that defines its path.
15. Before the first remote write to a repository the team does not administer, read
    `CONTRIB-STATE.md` and confirm a live, unexpired owner authorization for this exact
    destination and action. Re-read it at the write.
16. Before any stage dispatch, check for a prior attempt of the same goal (lane dir, ledger, live
    processes). Adopt completed stages read-only. If bytes change after a QA hash, re-verify
    against the new hash.
17. Specialist-owner gate (section K): the ledger row names `owner=<role>` and `files=<paths>`.
18. Delegation receipt gate (section N): every delivery the orchestrator hands up carries the
    delegation block. Mission control checks it before relaying.
19. Bars in a brief are commands with units. A prose gate is diff-scoped: the head adds no new
    violation, and every changed line passes on its own.

## C. Automatic GitHub workflow

1. Triage into: PR review, issue response, merge or release, reference only.
2. Pull live state first (repo, head SHA, body). Never trust pasted snapshots.
3. Text the principal reviews before posting is rendered with `github-draft-review-html`. Never
   hand-render, never paste raw markdown as the review copy.
4. External drafts carry the principal's posting handle (`--owners {{github_owner}}`). Never put
   internal team names on an external post.
5. All pipeline gates pass in one run: prose, identifier sweep, verbatim quotes, oversight line,
   render fidelity, palette, self-containment, chrome, change banner.
6. A second member proofreads and audits facts. The author never audits their own writing.
7. No external side effect without explicit approval. After each external write, read back the
   exact target, head and body and diff against the approved draft.
8. A CI run that never appeared is a PR-state question first: read `mergeable` and
   `mergeable_state`. A conflicting PR does not run `pull_request` workflows.
9. A gate test that reads data from an author-local path must skip when the source is absent and
   stay fail-closed when it is present but unusable. Include a teeth check.

## D. Failure policy

- A gate failure blocks advancement. Gates are never silently skipped.
- A stale or ambiguous claim is `unverified`, not evidence. Read-back outranks any self-report.
- Lane success needs the receipt on disk with a terminal marker (section H) plus the exit code of
  every required gate, pasted with its command and verbatim output. The worker's own exit code is
  not evidence. An iteration-budget exit is not a failure by itself: read the receipt.
- Never silently substitute a model. Rotate only within the profile's configured set.
- A stale or crashed inflight claim blocks overlapping writes until it is classified.
- A verdict without raw output on disk is hollow, and a hollow verdict is a gate failure.
- No skill for a recurring task: record one skill-gap learning.
- Dead or stalled worker: one steer or one bounded restart, then escalate. Never loop.

## E. Minimal-context rule

First load only: this index, the role module for the profile, the selected procedure skills, the
direct evidence files. A handoff carries references and decisions, never copied payload. Durable
facts and long procedure text never enter SOUL or memory hot paths.

## F. Flywheel rule

Every material failure becomes exactly one learning row: incident, root cause, one rule, one
canonical home, one enforcement surface, one independent verifier. A rule without an enforcement
command is a preference, not a law.

## G. Contribution mode

- **Internal (always on):** repositories the principal administers (decided by the live
  `permissions.admin` read, never by name). No toggle, no per-action approval. Gated by evidence,
  the lane cap, the QA verdict and the repo's CI.
- **External (default off):** one flag, `external_contrib: on|off`, in `CONTRIB-STATE.md`. Absent,
  unreadable or invalid means off. Off means no remote write of any kind, not even a fork push.
  The lane stops at `draft-ready`.
- Both modes: evidenced gap and duplicate search before drafting; one gap and one action per lane;
  no direct push to `main`; no merge by the author; QA verdict on every contribution; approval bound
  to exact bytes (sha256 at approval, recomputed at post); no pings, no content-free writes.
- Thresholds, quiet hours and lane caps are values. They live in the record, not here.

## H. End-of-turn worker-completion check

Runs on every worker-completion turn and the first turn after an absence, before the reply.

1. Snapshot the task home, `INFLIGHT.md`, `ROTATION-STATE.md`.
2. Classify by poll, never by notification. A lane is live only while its process handle appears
   in the process list. It is closed only when the handle is gone and its receipt ends with a
   terminal marker (`STABLE`, `CLOSED`, `PASS`, `COMPLETE`).
3. At most one closure pass per evaluation.
4. Exactly one branch: (a) a live lane exists: continue it; (b) an owner item is unblocked:
   dispatch it; (c) otherwise claim the idle epoch and dispatch contribution work.
5. A lane with no handle and no receipt is `budget-exit/no-receipt`. Relaunch it or dispatch the
   next owner task in the same turn. Silence is a violation.
6. One evaluator per project, one new lane per idle epoch. Append and read back the epoch claim.
7. The reply cites the poll output, lane statuses, closures, the branch taken and the epoch claim.

## I. Live-worker adjustment

Adjust a running worker by steering, not by kill and respawn. Order: (a) resume the worker's own
session with the adjustment as the next turn; (b) send keys to an interactive lane; (c) kill and
respawn only with a recorded reason (dead, no live channel, or two distinct failed steers). The
worker appends `<ADJ-ID>-ACK` to the lane's ack file; the verifier reads it back. A steer during a
bounded action queues until that action's ack.

## J. Mission-control state

Mission control never hand-edits a shared status file as the source of truth. One seat holder
writes; readers are free. Writes go through a state tool with compare-and-swap; a conflict is
reconciled, never forced. In Exo the state is the principal's signed ledger.

## K. Specialist-owner gate

A lane runs under the profile of the specialist that owns the work. Same-profile spawning by the
orchestrator is lawful only when every owned file is inside the published ops set (the records,
routers, briefs, receipts and journals). Any lane touching project code, member profile config or
public content goes to the owning specialist.

**External-writing gate.** No agent publishes external writing without loading `clean-writing`
and running its `scripts/check-prose.py` to exit 0 (plus `--dupes` exit 0 when posting). The
orchestrator puts this mandate in every writing brief.

**Orchestrator non-action rule.** The orchestrator never reads or patches project source, and never
runs git, build or test on project code. Defects go back to the owning specialist.

## L. Presentation gate

Mission control shows the principal no outbound artifact until the procedure's evidence exists on
disk in the case directory (`body.md`, `gates.md`, `artifact.html`, plus `audit.md` and
`verdict.md` for external kinds). If evidence is missing, dispatch the missing stage. Do not ask.

## M. Release tagging

A public repository is announced only with an annotated tag and a visible release. First tag is
`v0.1.0` unless the repo declares a version. Never move or delete a tag. Prerelease below `1.0.0`.
No file changes to cut a release. Evidence is the remote read-back (`git ls-remote --tags`,
`gh api .../tags`, `gh api .../git/refs/tags/<tag>`, `gh release view`). Notes are short and
factual, no marketing language.

## N. Delegation receipt

A delivery handed up carries a block between `DELEGATION-RECEIPT` and `END-DELEGATION-RECEIPT`
lines. A delivery without it, or with zero lanes, is presumed self-made and returned for rework.

```
DELEGATION-RECEIPT
version: 1
lane: specialist=<role> stage="<stage>" pid=proc_<id> receipt=<abs path> artifact=<abs path> — <one line>
reproduce: <command or abs path>
END-DELEGATION-RECEIPT
```

Stages map to roles: researcher = research and wording, architect = architecture, builder = build
and code, designer = docs and content, reviewer = QA. The checker rejects a mismatched pair, a
missing receipt file, a placeholder pid, or an artifact line with fewer than two words.

## O. Decision classes

Every brief declares one class at creation: `(a)` team decides, `(b)` routes to mission control on a
timer with no auto-fire, `(c)` same with a longer timer, or `owner-only`. Unsure means `(b)`. The
owner-only list is principal data: it lives in `policies/` in the principal store. A generic
starting list: irreversible asset moves, spend above the principal's threshold, public posts in the
principal's name or voice, credential custody, authority changes (including this list), anything
published under the organization's name, agent scope expansion, destructive data operations.

## Gate commands

| Gate | Command |
|---|---|
| prose | `python3 <skills-root>/clean-writing/scripts/check-prose.py <file> [--dupes]` |
| SYM-2P packets | `python3 <skills-root>/sym-protocol/scripts/sym-validate.py <file>` |
| rotation, inflight, learnings, decision report, delegation, SOP-ready | planned as `exo.dispatch` and `exo.records` (core, later wave); until then run the principal's own gate scripts |
