---
name: council
description: "Use when a structured team council is convened: bounded question, role seats, evidence-led deliberation, decision record with owner-held gates, and execution handoff with receipts."
version: 1.0.0
author: Hermes Agent (Protean Team)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Council, Decision, Deliberation, Gates, SOP]
    related_skills: [control-plane, operating-doctrine, clean-writing]
---

# Council: structured meeting and decision procedure

One canonical procedure home for the Council system. The control-plane trigger
index (§A, row "Council meeting") routes here; this file owns the procedure, the
templates own the formats. The Council advises and records — it never bypasses
owner gates, QA verdicts, external-write approval, or production promotion.

Provenance: piloted as a "next priorities" council and baked into this procedure from what
worked. Versioned: v1.0.0. Seat names below are roles; a principal maps them to their own agents
in the principal store (`agents/`).

## 1. Purpose and when to convene

A Council is a bounded, evidence-led deliberation with a written decision record.
Convene one when ALL of these hold:

- The question is strategic or cross-domain (not a single specialist's bounded task).
- The owner asked for team judgment, sequence, or priority — not just execution.
- The answer changes what multiple lanes do next (dependency or ordering effect).

Do NOT convene a Council for: a task one specialist already owns, an emergency
(steer the live lane instead, §I of the control plane), or a decision the owner
has already made (record it, don't re-litigate).

## 2. Roles

| seat | member | duty |
|---|---|---|
| Facilitator | orchestrator (mission control across instances) | owns the bounded question, dispatches seats, merges outputs, writes the decision record, never votes with authority over evidence |
| Research | Researcher | inventories current state with live sources, flags stale assumptions and contradictions |
| Analysis / architecture | Architect | sequences, dependencies, tradeoffs, kill conditions |
| Design | Designer | surface/UX implications, decision-report rendering where required (G-8) |
| Build | Builder | feasibility and cost of each recommendation; no implementation during deliberation |
| QA | Reviewer | verifies evidence claims, audits the record; the auditor never authors the fix or the recommendation it verifies |

A seat that has nothing to add on the bounded question is skipped, and the
agenda says why. Minimum viable Council: facilitator + research + QA.

## 3. Agenda and the bounded question

The facilitator dispatches a brief (template `templates/council-brief.md`) that
fixes exactly one bounded question, in quotes, plus the context each seat needs
— no seat may expand scope. Context rules:

- Current state stated as verifiable facts with sources; stale or contested
  items are labeled as such, not presented as ground truth.
- Items explicitly OUT of scope (e.g. "owner blockers closed — do not
  resurface") are listed so the council does not tunnel or re-litigate.
- Constraints on output form (e.g. "separate immediate / near-term /
  exploratory", "at least one option outside the current queue", "no more than
  five actions") are stated in the brief so the outputs are comparable.

## 4. Evidence and dissent capture

- Every factual claim in a seat's output carries its source (live read, file
  path, artifact). Unverifiable claims are marked `unverified`, never silently
  normalized.
- Disagreement between seats is captured as a named dissent in the evidence
  register (template `templates/evidence-register.md`): who dissents, against
  which claim, on what evidence. A dissent is never averaged away; it either
  resolves on evidence or becomes an explicit open item / owner question.
- The facilitator may not drop a dissent from the record.

## 5. Decision record, recommendations, owner-held gates

Output of the deliberation is a decision record (template
`templates/decision-record.md`) containing:

- the bounded question verbatim;
- the recommendation set — for a brainstorm council, a small bounded action
  list (the pilot used five) where each action carries: owner lane, acceptance
  signal, and stop condition;
- explicitly separated: what the team executes autonomously vs. what waits on
  an owner decision (owner-held gates: staging→production promotion, canonical
  naming/voice, external posts, irreversible changes);
- dissents and open items, verbatim-sourced;
- the model receipt (which profile/config served which seat — G-2 discipline).

Owner-held gates are restated verbatim in the record. A recommendation that
crosses one is marked `GATED: <gate>` and cannot be executed by the council.

## 6. Execution handoff

Execution of accepted recommendations runs through the normal Protean pipeline
(doctrine pipeline, inflight claims, rotation) — never as free-form follow-ups
(see `execute-team-council-next-steps` for the worked pattern):

1. The facilitator opens an execution brief per recommendation cluster, citing
   the decision record path and the exact recommendation text.
2. Each cluster produces its receipt in the execution lane dir; the terminal
   line format is fixed by the brief (`COUNCIL EXECUTION COMPLETE <path>` /
   `BLOCKED <reason>`).
3. Read-back discipline: every child receipt is read back on disk with its
   terminal marker before the integrator reports completion; the integrator
   re-runs checkable tools itself rather than trusting the receipt.

## 7. Action and receipt tracking

- Each accepted action gets one tracked row: action id, owner lane, decision
  record reference, receipt path, terminal marker, and read-back date.
- Follow-ups use the follow-up format (`templates/follow-up-action.md`): a
  follow-up either cites the receipt that authorized it or it is a new decision.
- The tracking rows live in the execution lane's journal and in the
  control-plane records (§B preflight appends); they are not a second
  copy of INFLIGHT — they reference lanes, they don't lease files.

## 8. No silent canonical edits while rulings are unresolved

While a Council ruling on a canonical surface (public copy, naming/voice,
canonical home, promotion) is OPEN: no lane edits that surface, not even
"obviously correct" fixes. The pilot codified this as: present the bounded
decision pack; the owner picks; only then does a lane edit canonical bytes.
Emergency repairs go through the owner, not around the ruling.

## 9. How Council outputs enter mission-control state and control-plane records

- Decision record path and terminal markers are appended to the mission-control
  state as an event (decisions or next-actions) by the seat holder, through the
  state tool, never by hand-editing the state files (control plane §J). In Exo
  that event is a signed ledger event (`exo log append`).
- Dispatch preflight/claim/release rows follow control-plane §B in
  `ops/INFLIGHT.md` / `ops/DISPATCH-LEDGER.md` / `ops/ROTATION-STATE.md`.
- Material failures found by the council become exactly one LEARNINGS row (§F).
- The council does not write to the principal store hot paths; it adds records and
  pointers only.

## 10. Limits

The Council advises and records. It does not:

- bypass owner gates (promotion, canonical naming, external writes) — §5;
- substitute for Reviewer QA — every executed cluster still gets an independent
  QA verdict (§6);
- approve external posts — an external write needs the owner's exact-post
  approval and the contribution gate (§G of the control plane);
- perform production promotion — that is always an owner action;
- override a live lane — adjustments follow §I steering, not council votes.

A Council recommendation without a decision record path and receipt is a
preference, not an outcome.

## Templates (shipped)

- `templates/council-brief.md` — the convening brief and bounded question
- `templates/agenda.md` — seat agenda with output constraints
- `templates/decision-record.md` — rulings, recommendations, owner-held gates
- `templates/evidence-register.md` — claims, sources, dissents
- `templates/execution-receipt.md` — per-cluster execution receipt format
- `templates/follow-up-action.md` — tracked follow-up format
