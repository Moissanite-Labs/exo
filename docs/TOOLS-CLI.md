# Store tools on the command line: eval, ask, brief, consolidate

Four modules in `src/exo/` read and write a principal store and its ledger, and each has an `exo` subcommand. They
hold the mechanism only. The questions, thresholds and wording live in the principal's store, so another person
changes them without touching code. The full argument list of every command is in `docs/COMMANDS.md`. This page says
what each one reads, what it writes, and the rules inside. The model-driven learning loop that calls some of them is
in `docs/LEARNING.md`.

All four take `--principal ID` or `--root DIR` to pick the store (see `docs/COMMANDS.md`, "Choosing a store").
None of them calls a model API on its own. Where a model can plug in, it is a parameter.

## `exo ask`: the question queue and the interruption budget

Code: `src/exo/ask.py`. Idea: ask only what is worth the principal's time, and never too often.

**Queue.** `<store>/queues/questions.jsonl`, append-only, one JSON event per line. The events are `proposed`,
`asked`, `answered` and `dropped`. The state of a question is the fold of its events in file order. Ids look like
`q-20261011-1a2b3c`.

**Value of information.** `voi = impact x uncertainty x (1 + R)`. `impact` and `uncertainty` run from 0 to 1 and come
from whoever proposes the question (`--impact`, `--uncertainty`). `R` adds weight for recent corrections on the
question's node: each `edit`, `reject` or `should_have_been` verdict decays with a half life (default 14 days), and
the sum is capped (default 3). So `voi` runs from 0 to 4. It is recomputed from the live ledger every time you read
the queue. The stored value is only a snapshot.

**Budget.** The policy file `<store>/policies/interruptions.yaml` sets the limits. All keys are optional:
`questions_per_day`, a rolling `window` (`hours` and `max`), a front-loaded `curve`, `quiet_hours`, `timezone`,
`ask_threshold`, `min_predictions` and `voi`. `exo ask budget` prints the result as JSON: `daily_cap`,
`asked_today`, `asked_in_window`, `quiet` and `remaining`. `remaining` is the smaller of the daily and window room, and
zero inside quiet hours. `exo ask next` never returns more than `remaining`.

**Ask or act.** `exo ask decide` reads a JSON list of predicted choices on stdin (from different models or prompts).
It measures disagreement as the normalized entropy of the pooled votes, from 0 (all agree) to 1 (spread evenly).
Above the domain's `ask_threshold` the answer is to ask. Below it, act and log. Fewer than `min_predictions`
predictions always asks.

**Ledger link.** `exo ask answer` writes a signed `answer` event and so needs `--author` and a key
(`--key`, else `$EXO_HOME/keys/<author>.key`). `exo ask next --mark --key ... --author ...` writes `question` events
when it marks questions as asked.

## `exo eval`: an evaluation of one

Code: `src/exo/eval.py`. Idea: with one principal there is no big test set, so measure against that person's own
cases and own history.

**Inputs** (JSONL, in `<store>/training/` unless you pass a path):

- `eval-qa.jsonl`: `{"id", "question", "gold", "node"?, "domain"?}`.
- `preference-pairs.jsonl`: `{"id", "context", "chosen", "rejected", "node"?, "domain"?}`.

The domain of an item is `domain`, else the part of `node` before the first `/`, else `general`.

**Three measures.**

1. *Q&A.* Ask each question, grade the reply. The default grader scores an exact match as 1, else the share of the
   gold answer's content words found in the reply. A reply at or above the pass mark (0.5) counts as correct. A
   responder that raises scores zero and the run goes on.
2. *Held-out choices.* For each pair, show `chosen` and `rejected` as A and B in random order (the order comes from
   `--seed`) and ask which the principal would pick. An unparseable reply is wrong. Chance is 0.5.
3. *Longitudinal.* From the ledger, by ISO week: accepted outputs, corrections (`edit`, `reject`,
   `should_have_been`), corrections per accepted output, the mix of verdicts, questions asked and answers given.

**Responder.** A responder is any function from string to string. The default is a retrieval baseline that answers
from the store with no model, so it is a floor to beat. `--responder module:function` loads your own.

**Output.** `<store>/reports/eval-YYYY-MM-DD.json` and `.html`, and a one-line JSON summary on stdout. With
`--no-write`, it prints the whole JSON report and writes nothing. `src/exo/eval.py` also holds the scoreboard
(`reports/scoreboard.jsonl`) and the pieces the nightly learning loop uses to compare candidates (`docs/LEARNING.md`,
"The slow loop"). `build_replays` and `load_items` turn only the principal's own verdicts, answers and rulings into
cases (D-040). Pass `principals` to name who that is. `climb` caches the incumbent's scores against its config, the
cases' full contents, the checks and a caller's `fingerprint` (the models and rubric). A candidate's `exclude` names
the ledger events its lesson came from. Cases built from them never count for or against it.

## `exo brief`: the morning brief

Code: `src/exo/brief.py`. Idea: result first, failures first, a few numbered decisions, a few questions.

**Inputs**, all in the store: the ledger (what agents did in the window), `queues/decisions.jsonl` (decisions waiting
on the principal, schema in `plugins/exo-command/schemas/decision-event.schema.json`) and
`queues/questions.jsonl` (only what the interruption budget allows).

**Format.** `<store>/policies/brief.yaml` is optional. It sets the title, the section `order`
(`status`, `blockers`, `decisions`, `questions`), the headings, `window_hours` (default 24), how to `group_by`
workstream, the caps (`max_status`, `max_decisions`, `max_questions`), `min_voi`, the line formats, `mark_asked` and
the `empty` text. A workstream with a failed or blocked event goes to Blockers first.

**Output.** Markdown on stdout, or HTML with `--html`. `--write` also writes
`<store>/reports/brief-YYYY-MM-DD.md` and `.html`.

## `exo consolidate` and `exo proposals`: from events to reviewed beliefs

Code: `src/exo/consolidate.py`. Idea: the ledger is the truth. Beliefs are a view that is only changed by a
reviewed proposal.

**What `consolidate` does.** It reads ledger events after its watermark: `verdict`, `ruling`, `utterance`, `answer`,
and `note` events of type `consolidate_pending`. Then:

- Each correction (`edit`, `reject`, `should_have_been`) and each ruling becomes a **proposal** in positive form,
  "The principal wants Y, not X, because Z", with its evidence, domain, confidence, and a preference pair.
- **Rule of three:** an instruction given in three or more utterances with no belief that encodes it gets a proposal.
  A rejected belief that agrees counts as encoding it, so repeating a rejected lesson in chat proposes nothing.
  Your live turns (role `user`) and the messages `exo ingest` imported as yours (role `principal`) both count. Only
  your own authors count (D-040): a Hermes brief of doubtful authorship (`hermes:unverified-user`), a peer's turn or
  an import's summary (`secondary`) never does.
- **Reinforcement:** a new correction or ruling that agrees with an active belief (proposed, held or accepted)
  reinforces it. Only active beliefs are matched, so a rejected belief is never reinforced and never hides an active
  one; a correction or ruling that agrees only with a rejected belief becomes a new proposal (#770, #861). Weight
  decays by half life unless reinforced.
- **Conflict:** an event that opposes an active belief goes through the conflict rule. The default,
  `newer_wins_unless_guarded`, lets the newer one win unless the old belief guards something irreversible or
  public. In that case it raises a numbered `question` and holds the proposal until an `answer` arrives. Nothing is
  overwritten silently.

**Outputs.** `belief_proposed` events, and drafts in `<store>/proposals/`: `<id>.md` (the node as it would be
written), `<id>.diff` (when it edits a node) and `<id>.json`. Decided drafts move to `proposals/archive/`. Each run
that reads events ends with a `consolidate_run` note holding the last sequence number, so the next run resumes there.
Proposal ids are hashes of their evidence, so a lost watermark cannot duplicate proposals.
`proposals/state.json` caches the rule-of-three clusters. Delete it and the next run rebuilds it from your
utterances up to the watermark, with the same author check.

**Flags.** `--dry-run` reports without writing (and so needs no key). Otherwise it signs with `--as SEAT`
(default from `DEFAULT_AUTHOR` in the module) using `--key`, else `$EXO_HOME/keys/<seat>.key`.

**Policy.** `<store>/policies/consolidate.yaml`, all optional: `conflict_rule` (`newer_wins_unless_guarded`,
`newer_wins` or `ask`), `guard_words`, `half_life_days`, `similarity`, `rule_of_three`, `target_overlap`, `domains`,
`stale_below`.

**Reviewing.** `exo proposals` lists, shows, accepts, rejects or reverts. Accepting writes the node edit and a
`belief_accepted` event. Reject and revert need `--reason`. A revert undoes the file change and records why.
Accept and reject sign as `--as` (default `principal`).

**The model seam.** Reflection, the step that words a proposal, is a seam. The default is deterministic templates.
A model can be plugged in. It must return valid JSON or the rules take over. The learning loop (`docs/LEARNING.md`)
uses `propose_typed` to write proposals of type taste, skill, fact or question.
