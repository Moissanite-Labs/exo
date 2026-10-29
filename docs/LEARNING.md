# The learning loop (D-012, D-022)

Exo learns from what you do, and only keeps a lesson when the evidence is good. Code: `src/exo/learn/`. The design
notes are in `research/LEARNING-LOOP.md`. This page is the short reference.

## Where learning needs a store

Learning writes to your principal store and its signed ledger. The store comes from config `learn.store`, else the
`exo-memory` plugin's store (its config, then `EXO_STORE` or `EXO_PRINCIPAL`), else the active principal in
`$EXO_HOME/config.json`. That last one is what `exo init` and the installer set, and exo-memory falls back to it the
same way, so memory and learning use one store. With none of these, learning is off and says so once. With no
principal at all, that notice is logged at info level and not printed, since exo-memory already shows one quiet
notice in the client. It names `exo init <id>`.

## Signals in

| Signal | How it arrives |
|---|---|
| A verdict on an answer: accept, edit, reject, should_have_been | protocol command `feedback.send` (TUI: ctrl+y accept, ctrl+e edit, ctrl+r reject) |
| What you did with a panel: opened, dismissed, acted, ignored | protocol command `ui.respond` (see PANELS.md) |
| A correction or ruling in chat | principal capture (MEMORY-MODEL.md) |

Each signal becomes a ledger event. An edit also stores a preference pair: the answer, and your better version.

A correction or ruling is matched against active beliefs only (proposed, held or accepted). One that agrees with an
active belief reinforces it; one that opposes an active belief goes through the conflict rule, even when a rejected
belief matches it better (#861). A rejected belief is never reinforced and stays recorded. A correction or ruling
that agrees with a rejected belief and with no active one becomes a separate proposal, which then goes through
promotion like any other: an unguarded, ungated one that cites your ruling is accepted at once by rule 1, and one
held on a conflict stays held (rule 3, see "Promotion"). A reviewer taste or fact lesson whose evidence is all
already held by the rejected beliefs it agrees with, one or several together, teaches nothing again: it is not
proposed, raises no conflict question and reinforces no other belief. A reviewer skill lesson is not checked this
way and is still proposed. New evidence, alone or beside that evidence, is matched as usual. The rule of three
still counts a rejected belief that agrees as known, so saying the same instruction three more times in chat does
not raise a rejected lesson again; a new correction or ruling does.

A correction or ruling in chat is written once, by its capture key. exo-memory writes the same keys, so neither
doubles the other. After each turn the learner checks the chat against the keys already in the ledger. It keeps
those keys between turns, with the last row it read, and reads only the rows after that row (#263). The ledger is
append-only and nothing takes a key back, so the kept keys are the ones a full read would find. If the ledger no
longer holds that row in its place (another ledger, or a file swapped or cut short), the learner reads it again from
the first row. One case waits for the next start: a row deleted or changed by hand before that row, past the
ledger's triggers. `exo log verify` reports that ledger as broken, and exo-memory's own key set keeps such a key too.

A private thread gives no signal (D-028), and neither does a session under it (its delegate children, at any depth).
The loop captures no correction from them and starts no review for them. `feedback.send` on one of their answers
returns `recorded: false` and files no finding. `ui.respond` on a panel a private thread opened or updated is not
noted.

Only your own words are signals (D-040). The loop reads a ruling, verdict, answer or utterance only when its author is
you (`exo.provenance.principal_names`: `principal`, your store's id, and the principal ids in the key registry). A
ruling the agent wrote with `exo_note`, a correction it reported with `exo_should_have_been`, or text that came from a
peer, a bus seat or a job notice is never read as yours, whatever it says. Consolidation counts these as
`not_principal` and skips them. History you import with `exo ingest` is authored as you, so it counts like a live
turn. A Hermes row that may be an agent's brief is authored `hermes:unverified-user` and never does. The eval cases
built from the ledger follow the same rule: see "The slow loop".

Imported history counts as of when you said it: its evidence carries the row's source time, not the time of the
import. Superseded text does not count for the rule of three: the older versions of an edited ChatGPT prompt
(`branch: alt`) and Hermes rows marked inactive. Rule 4 never applies a lesson whose evidence is all imported. You
review that first batch (`exo proposals`).

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
  The slow loop evaluates. With no held-out cases the gate fails and the proposal remains open. A case built
  from the lesson's own evidence (the replay of a cited verdict, or a preference pair that cites it) does not
  count as held out.
- Rule 1: an ordinary unguarded, ungated proposal citing your ruling or answer promotes immediately.
  The event's author is checked, so an agent-written ruling never triggers this rule (D-040).
- Rule 4: ordinary taste or fact with high weight, at least `min_sessions` sessions, and no critic objection
  promotes automatically and is logged for the brief. When all its evidence came from `exo ingest`, it waits for
  your review instead.
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

The climb adds only ordinary lessons: not guarded, not gated by rule 5, and with no critic objection. Only rule 5
promotes a gated lesson on its own. It runs earlier the same night and keeps its critic, weight, session and
held-out checks. A lesson is a candidate only while its computed evidence weight, decayed to now, is medium or high
(the stored `confidence` does not decide). Every rule reaches every domain, so the gate reads all held-out cases in
two parts. The held-out cases in the candidate's own domains (its domain and `general`) and the pinned cases, from
any domain, are still checked as one pool, as before: in that pool a gain on one case can still offset a loss on
another, such as a `general` or pinned case against a case in the lesson's domain. Each other domain's held-out
cases are checked on their own, so a gain in the pool never pays for a loss in another domain's held-out cases, and
a gain there never pays for a loss in the pool. A candidate with no held-out case in its domains fails the gate. An
accepted candidate is scored again on every case, so the cached incumbent is its own scores in every domain.
A lesson's own evidence never counts for or against it, on dev or on held-out cases, whether the candidate adds the
lesson or drops it. The climb can add a lesson drawn only from imported history, but only when the eval shows it
helps.

The cases are your eval Q&A, your preference pairs, and replays built from the ledger. A replay comes only from
your own verdict, answer or ruling, and its question is your own message before it (D-040). An agent's
`exo_should_have_been` report, or a peer's message, is never a gold answer or a question.

The incumbent's scores are cached in `<store>/eval/incumbent.json`. They are scored again when the incumbent's rules
or skills change, or when anything else that moves a score changes: an eval case (its text, not only its id), the
checks in `eval/rules.jsonl`, the rubric in `eval/rubric.md`, the judge samples, or the models on the main and
eval routes.

The judge's reply is read as a score from 0 to 1 (`exo.eval.parse_score`). A named scale ("0 to 1", "(0-10)") is
only a label. A number that opens the reply is the score, as the prompt asks. Otherwise a reply that calls the
answer wrong, incorrect or not correct is 0 ("nothing wrong" does not), a bare "correct" is 1, and the first
number counts. A full score in a reply that also calls the answer wrong is 0, and a reply with no number is 0.

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

The rule-of-three check looks for an active agreeing belief before considering inactive
beliefs (#882). A more similar rejected conflict cannot hide an existing active agreement
and cause a duplicate proposal. A rejected agreeing lesson still counts as already known;
repeated chat instructions alone do not override the principal’s rejection.
