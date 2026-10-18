# The learning loop (D-012, D-022)

Exo learns from what you do, and only keeps a lesson when the evidence is good. Code: `src/exo/learn/`. The design
notes are in `research/LEARNING-LOOP.md`. This page is the short reference.

## Where learning needs a store

Learning writes to your principal store and its signed ledger. The store comes from config `learn.store`, else the
`exo-memory` plugin's store. With neither, learning is off and says so once as a notice.

## Signals in

| Signal | How it arrives |
|---|---|
| A verdict on an answer: accept, edit, reject, should_have_been | protocol command `feedback.send` (TUI: ctrl+y accept, ctrl+e edit, ctrl+r reject) |
| What you did with a panel: opened, dismissed, acted, ignored | protocol command `ui.respond` (see PANELS.md) |
| A correction or ruling in chat | principal capture (MEMORY-MODEL.md) |

Each signal becomes a ledger event. An edit also stores a preference pair: the answer, and your better version.

Only your own words are signals (D-040). The loop reads a ruling, verdict, answer or utterance only when its author is
you (`exo.provenance.principal_names`: `principal`, your store's id, and the principal ids in the key registry). A
ruling the agent wrote with `exo_note`, a correction it reported with `exo_should_have_been`, or text that came from a
peer, a bus seat or a job notice is never read as yours, whatever it says. Consolidation counts these as
`not_principal` and skips them.

## The fast loop

After a turn, a cheap reviewer on the `aux` route reads new signals and writes typed proposals. It only proposes.
A live turn cancels it. Each proposal goes to one place: taste (style and values), skill (how to do a class of task),
fact, or question (when the signal is unclear). Limits live in `learn.fast_loop` in config.json: `every_turns` (10),
`deadline` (90 s), `daily_cap` (20 reviews a day).

## Promotion

`exo.learn.promote.apply_policy` checks mandatory constraints before the direct-ruling shortcut. The rule
numbers stay fixed for ledger records:

- Rule 3: a conflict already held by consolidation stays held. Its existing question awaits your answer.
- Rule 2: guarded proposals (irreversible, public, financial, third party) never promote on their own.
  They become a question once, even when they cite a ruling or answer you wrote.
- Rule 5: skill patches always need the held-out eval gate. Types in `eval_gate_for` and domains in
  `high_impact_domains` need it too. A ruling citation cannot bypass this check or a critic objection.
  Non-skill gated proposals also need high weight and `min_sessions` (2) sessions. The fast loop waits.
  The slow loop evaluates. With no held-out cases the gate fails and the proposal remains open.
- Rule 1: an ordinary unguarded, ungated proposal citing your ruling or answer promotes immediately.
  The event's author is checked, so an agent-written ruling never triggers this rule (D-040).
- Rule 4: ordinary taste or fact with high weight, at least `min_sessions` sessions, and no critic objection
  promotes automatically and is logged for the brief.
- Rule 6: anything else stays a proposal. After `stale_days` (14) it becomes a question or decays.

These checks govern lessons derived by the reviewer. Your original rulings remain in the ledger and context.
Explicitly accepting a proposal uses the separate `proposal_accept` path and can apply a guarded lesson or skill
without automatic promotion. A citation establishes authorship, not that the cited words support the derived claim.
Semantic evidence matching for ordinary unguarded proposals remains outside this policy.

A promotion can be reverted (`exo.learn.revert`), and the revert is a ledger row too. A promoted, accepted or reverted
belief reaches recall on the next turn, with no restart: exo-memory reads the nodes fresh each turn.

## The slow loop

`exo.learn.nightly(engine)` runs the climb. A candidate is the current rules and skills with one change: add a held
lesson, or drop an auto-promoted one. It replaces the incumbent only if it scores better on the dev cases by a margin,
is not worse on held-out cases, and passes the gate. The night is capped by `climb.candidates` (10) and
`climb.judge_samples` (3). The judge is the `eval` role, which defaults to the main route (D-018).

Nothing calls `nightly` on a timer yet. D-023 lists the trigger as open.

## The house taste (D-044)

Learned beliefs are one layer of taste. A new install can also start from a house taste: a released, read-only
pack of method and value items exported from the first user's store with every personal detail removed. Per item,
a ruling wins, then a pin, then a learned belief that matches the house item, then the house item. Each item in the
prompt shows its source. House items have no evidence; their precedence is `max(0.3, 0.7 * 2^(-t/365))` from
install day. The house layer is off on the store it was exported from. Learning itself is unchanged: the loop
never writes to the house pack, and nothing learned on one install flows back into it. Design and status:
`docs/TASTE-BASELINE.md`; code: `src/exo/baseline/`.

## Where to look

- Config: the `learn` table in `config.json` (defaults in `src/exo/learn/runtime.py`).
- Tests: `tests/learn/`.
- What was learned since a date: `exo.learn.learned_since`, used by the brief.
