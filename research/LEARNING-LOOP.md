# Exo learning loop: one fused feedback loop

Status: research, 2026-10-11. Input to D-010. Nothing here is built yet beyond the modules it names.

A's ask (D-009): fuse Hermes's branch-off review loop (memory and skill learning) with Command Code's
branch-off taste learning into one feedback loop that aligns the harness to its user and serves the
guardian-angel idea. Added later by A: "i also want to bake in some kind of hill-climbing/eval system
that strengthens the alignment feedback loop further." Section 6 covers that.

Sources read: the local Hermes checkout at `~/.hermes/hermes-agent` (commit `dce1e9b3758`, 2026-10-09;
the pinned `~/.exo/hermes` does not exist yet, so line numbers are for this checkout), Command Code's
public docs, A's Hermes PRs, `research/PRINCIPLES.md`, and Exo's own `src/exo/` modules.

---

## 1. Hermes: the background review fork

### What it is

After a turn, Hermes may fork a copy of the agent in a daemon thread. The fork replays the
conversation and asks one question: should any memory or skill be saved or updated? It writes
straight to the memory and skill stores. The live conversation and its prompt cache are never touched
(`agent/background_review.py:1-6`).

### Triggers

Two counters, both default 10.

- **Memory nudge.** Counts user turns. Fires every `memory.nudge_interval` turns
  (`agent/agent_init.py:1301,1325`; tick in `agent/turn_context.py:751-760`). It needs the `memory`
  tool and a memory store.
- **Skill nudge.** Counts tool iterations. Fires when `_iters_since_skill` reaches
  `skills.creation_nudge_interval` (`agent/agent_init.py:1380-1382`; check in
  `agent/turn_finalizer.py:759-766`). It needs `skill_manage`.
- If the main agent already called `memory` or `skill_manage` itself, that counter resets
  (`agent/tool_executor.py:744-747`). The agent that saved on its own is not nagged.
- The fork only runs when the turn produced a final answer, was not interrupted, and
  `skip_background_review` is off. Cron turns skip it because the fork costs about 30K tokens with no
  human in the loop (`agent/turn_finalizer.py:774-786`).
- Subagents never spawn reviews. A config flag can disable reviews entirely; a broken config fails
  open with a warning (`run_agent.py:760-768`, `agent/background_review.py:228-243`).
- **Manual:** `/refine [focus]` forces a review now and appends the user's focus text to the prompt
  (`agent/background_review.py:1301-1322`).

### When it runs

- Immediately, after the answer is delivered (`agent/turn_finalizer.py:770-786`).
- On a managed local llama-server, the review is queued until the machine is idle, so it does not
  steal the GPU from the next prompt. One slot per session, newest snapshot wins, items older than
  30 minutes run anyway (`agent/review_idle_queue.py:1-11`, `run_agent.py:777-780`).
- A new live turn cancels a running review. If the review does not stop within a bounded deadline,
  the live turn proceeds anyway (`agent/background_review.py:129-140`). A preempted deferred review is
  requeued up to 3 times (`run_agent.py:825-845`).

### The forked agent

- Same provider, model, credentials and cached system prompt as the parent, so request one is a warm
  cache read (`agent/background_review.py:956-982`). If routed to a different auxiliary model, older
  turns are collapsed into a digest and only the last 24 messages are kept verbatim (`:324-358`).
- `skip_memory=True` so an external memory plugin does not also fire (`:912-926`).
- Max 16 iterations and an input-token budget of 75% of the review model's context, capped at 600K
  (`:173-183`).
- Dispatch-side tool whitelist: `memory` (only when the memory nudge fired) plus the skills toolset
  plus read-only `read_file` and `search_files`. Write tools and the terminal are denied. The
  advertised tool list stays byte-identical to the parent's so the cache still hits (`:1079-1119`).
- Dangerous-command prompts resolve to "deny" automatically (`:1042-1046`, set at `:1225`).

### Prompts

Three prompts: memory only, skills only, combined (`agent/background_review.py:375`, `:440`, `:511`).
Key content:

- **Two stores, one fact each.** `USER.md` holds who the user is: persona, preferences, style.
  `MEMORY.md` holds environment facts: tool quirks, paths, conventions. Never write one fact to both
  (`:362-373`).
- **What a skill is.** A procedure for a class of tasks done this user's way. Procedure first.
  Pitfalls as general rules with one clause of why. No PR numbers, dates or incident stories. The same
  lesson learned twice is one rule. Fix a wrong sentence in place; do not append "UPDATE" (`:385-413`).
- **Corrections are first-class skill signals.** "Stop doing X", "too verbose", "remember this" must
  update the skill that governs that task, not just memory. A preference lives in exactly one place:
  the governing skill if there is one, else `USER.md` (`:458-462`, `:551-557`).
- **Preference order:** patch a loaded skill, then an existing umbrella, then add a support file
  (`references/`, `templates/`, `scripts/`), and only then create a new class-level skill.
- **Do not capture:** environment failures, negative claims about tools ("X is broken"), transient
  errors, one-off narratives, or an unresolved failure written up as a "reliable workflow"
  (`:415-438`).
- The skill prompt leans active: "A pass that does nothing is a missed learning opportunity."

### Safeguards

- **Read before write.** `skill_manage` refuses to patch a skill the fork has not loaded with
  `skill_view` during this review (`tools/skill_manager_guards.py:20-66`).
- **No unattended deletes.** In a background fork, memory `add` is allowed but `replace` and `remove`
  are staged for the user's approval in `/memory pending` (`tools/memory_tool.py:171-213`). A general
  write-approval gate can also block or stage any memory write (`tools/memory_tool.py:84-102`).
- **Size caps.** `MEMORY.md` 2,200 chars, `USER.md` 1,375 chars by default (`tools/memory_tool.py:56`).
- **Skill audit ledger.** Every skill mutation appends a JSONL entry with content-addressed before
  and after snapshots, so one edit can be rolled back (`tools/skill_ledger.py:1-9`). A's PR #110011
  (open, draft) closes the gaps where some writes left no entry.
- **Scanning.** Agent-created skills go through `skills_guard` (`tools/skills_guard.py:32`).
- **Visible result.** The fork's actions are summarized and printed to the user
  (`agent/background_review.py:699-735`).

### The curator: the slow loop

A second, slower loop maintains the library (`agent/curator.py:1-7`). It runs when the agent is idle
at least 2 hours and the last run is over 7 days old (`:31`). Unused skills go stale at 14 days and are
archived at 30 (`:32`). An optional forked agent consolidates overlapping skills into umbrellas
(prompt at `:290`). It snapshots all skills before a run (`:931-945`), never deletes (archive only), and
never touches pinned skills. Users can edit or archive learned nodes from the "journey" view
(`agent/learning_mutations.py`).

### What Hermes lacks

- Lessons have no confidence, no evidence links and no decay. A memory line is just text.
- No accept, edit or reject signal is captured. The fork infers corrections from chat text only.
- No eval. Nothing checks whether a new skill or memory line made the agent better.
- No questions back to the user. The fork guesses or stays silent.

---

## 2. Command Code: taste learning

Sources: `commandcode.ai/docs/taste`, the npm README, a third-party deep dive, and the source notes
in A's PR #126940. The product is closed and the `taste-1` model is unpublished, so details below are
as the vendor describes them.

- **Signals.** Every accept, reject and edit of the agent's output. Also correction diffs from the
  user's commits, prompts and explicit statements. The core signal is the diff between what the model
  generated and what the user kept: a clean, low-noise preference signal.
- **Branch-off.** Learning runs in the background, off the main turn. Each signal goes through four
  steps: (1) an LLM turns raw text and diffs into structured signals; (2) `taste-1`, a small
  "neuro-symbolic" classifier, decides real preference or noise and assigns a category and a
  confidence from 0 to 1; (3) an LLM writes the result as markdown; (4) the CLI stores it. The model for
  steps 1 and 3 is configurable (`featureModels.tasteLearning`).
- **Artifact.** Plain markdown packages: `.commandcode/taste/taste.md` plus per-category files such as
  `typescript/taste.md`, `architecture/taste.md`. Global at `~/.commandcode/taste/`. Each learning is
  one human-readable rule line ending in `confidence: <0..1>` (the app parses
  `confidence:\s*([0-9.]+)`). Categories come from a fixed set (cli, typescript, architecture, react,
  testing, ...).
- **Re-scoring.** Confidence rises when a preference recurs and falls when the user contradicts it.
  The marketing says "Rules decay. Taste compounds." The docs describe no time-based decay, and A's PR
  found none in the shipped code.
- **Applied back.** Learnings are read into every later turn. High confidence is enforced, low
  confidence is a hint, conflicts are flagged rather than applied.
- **Tools.** `/taste` settings, `npx taste lint/open/list`, `push/pull` to share packages across
  machines and teams (private unless `--public`), `/learn-taste` to bootstrap from old repos or other
  agents' sessions.
- **Gaps.** No validation gate between "the model inferred a preference" and "it is persisted", so a
  wrong lesson can compound. Profiles sync to the vendor by default. Taste only, no skills or facts.

---

## 3. A's PRs

**#126940** "feat(taste): taste learning with decay, conflict escalation, and review-passover
integration". Closed by A on 2026-10-11. Labeled `wontfix` and P3 by maintainer alt-glitch at triage on
2026-09-28, the same minute the PR opened.

- **Proposed:** a corroboration engine (bounded growth `1 - exp(-k n)`, 14-day half-life decay,
  conflict tracking, staleness escalation) and a `taste` tool with learn, forget, summary and write.
  `_run_taste_learning()` rode the existing review fork after the memory and skill writes, fail-open,
  and wrote established candidates to `taste.md` in Command Code's `confidence:` format.
- **Review (arkheioncorp, request changes):** decay math and the conflict contract judged sound.
  Asked to settle the `tools/` versus `agent/tools/` import layout (a two-path try/except repeated three
  times), mark `advance()` as test-only, clarify `should_write()`, and note that the fork hook validates
  more loosely than the tool. A fixed four of five; the layout question and a rebase stayed open.
- **Also on the branch:** an unrelated LCM context engine, which made a 5,195-line PR.
- **Related:** #110011 (skill ledger rollback, open draft), #119135 (tag fork turns in logs, closed),
  #132906 (you-should-know sidecar observer, open draft).

**What was missing relative to the fused idea:**

1. Taste was bolted onto the fork as a third pass. It did not share one signal model or one store
   with memory and skills. The same correction could land in USER.md, a skill and taste.md.
2. Inputs were still inferred from chat text. No accept, edit or reject capture, which is the core of
   Command Code's signal.
3. No evidence links. A taste line could not answer "why do you believe this?"
4. Conflicts escalated to a queue but never became a question to the user.
5. No eval. Nothing measured whether learned taste improved outcomes, and nothing reverted a lesson
   that made things worse.
6. Sidecar JSON state, so the taste file could not be rebuilt from a log.

Exo already fixed 3 and 6 in `exo.taste` (weight is a pure function of the evidence list) and 4 in
`exo.consolidate` (conflict rule raises a numbered question). This design adds 1, 2 and 5.

---

## 4. Guardian-angel tie-in (PRINCIPLES.md)

The fused loop is the guardian-angel core loop: log, ask when uncertain, corrections that stick,
evaluate on n=1. Principles it implements:

- **1. Log is truth.** Every lesson is a view rebuilt from ledger events.
- **3. Attention is scarce.** The loop spends compute, not the principal's time. It asks only the few
  questions worth asking.
- **4. Ask when uncertain.** Low confidence and conflicts become questions, scored by `exo.ask`.
- **5. Every correction is permanent signal,** captured on the agent's own output (DAgger). Rule of three.
- **6. Reflect in positive local form.** "The principal wants Y, not X, because Z." Never store a bad
  example with a "this was wrong" note.
- **7. Split by kind.** Taste in the cached pack, facts in retrieval, procedures in skills.
- **8. Shared prior plus delta.** Ship default taste and skills; learn only deviations.
- **9. Beliefs are reviewed propositions** with evidence, confidence and history.
- **10. Content is data.** The reviewer never learns instructions from tool output or web pages.
- **12. n=1 eval, longitudinal, held out.** Section 6.
- **14. Twin and critic.** The reviewer predicts the principal; a separate critic pass argues back.
- **15. Idle compute compounds.** The slow loop runs at night.

---

## 5. The fused loop for Exo

### 5.1 One sentence

Capture every signal as a ledger event; a branch-off reviewer turns events into typed lesson proposals
with evidence; proposals are promoted by confidence and by the eval, or turned into questions; every
lesson can be traced, decayed and reverted.

### 5.2 Picture

```
 live turn ──► capture ──► ledger (signed, append-only)
                                │
            fast loop (after turn, off the main thread)
                                ▼
                    reviewer fork ──► proposals (typed, with evidence, confidence)
                                │
               ┌────────────────┼──────────────────┐
               ▼                ▼                  ▼
        auto-promote     hold for eval gate    question to principal
       (low risk, high        (Section 6)          (exo.ask)
        confidence)             │                  │
               └──────► belief_accepted ◄──────────┘
                                │
          views: principal pack, taste rules, skills, memory, retrieval
                                │
            slow loop (idle, nightly): decay, merge, curate, re-eval, revert
```

### 5.3 Signals in

All signals are ledger events. Most kinds already exist (`src/exo/ledger.py:34-38`).

| Signal | Ledger kind | Source | Strength (default) |
|---|---|---|---|
| Explicit rule ("from now on", "always", numbered rule) | `ruling` | `exo.capture` | 1.0, no decay |
| Correction in chat ("no", "I said", "instead") | `verdict: edit` or `should_have_been` | `exo.capture` | 0.8 |
| Edit of agent output (diff kept vs generated) | `verdict: edit` with diff | harness editor, git commits after an agent change | 0.7 |
| Rejection (discarded, reverted, rerun) | `verdict: reject` | harness UI, git revert | 0.6 |
| Accept without change | `verdict: accept` | harness UI, merge, sent as-is | 0.2 (reinforces only) |
| Answer to a question | `answer` | `exo.ask` | 1.0 for the asked node |
| Outcome (tests pass, PR merged, post published, task reopened) | `action` with outcome | tools, gate, bus | 0.3 |
| Repeated instruction, 3 times | derived | rule of three in `exo.consolidate` | proposal |

Edits and rejections are Command Code's contribution. They carry more information per byte than chat.
Exo should capture them in the harness UI (accept, edit, reject, "should have been" buttons) and by
watching git: an agent commit followed by a principal commit on the same lines is an edit verdict.

Content from tools, web or email is tagged as data and is never a signal source (principle 10).

### 5.4 The branch-off reviewer

One reviewer, not three passes. It runs off the main turn, like Hermes's fork.

- **Fast loop trigger.** After a turn, if any of these hold: a correction, edit or reject verdict
  arrived; a ruling arrived; N user turns (default 10) or M tool iterations (default 10) passed since
  the last review; or the user ran `/refine`. A turn with a correction always triggers. Turns with no
  new signal skip it. Never for cron, subagents or unattended runs.
- **Off the main path.** Runs after delivery. A new live turn cancels it with a bounded deadline. On a
  local model it waits for idle, as Hermes does.
- **Input.** The new ledger events since the watermark (not the whole transcript), the beliefs and
  skills those events touch (via `exo.retrieve`), and a short digest of the turn. This is smaller than
  Hermes's full-snapshot replay and works on a small model.
- **Two stages,** borrowed from Command Code:
  1. **Classify** each event: signal or noise; type (taste, fact, procedure, open question); domain;
     the node it touches; agree, conflict or new. This can be a small model or rules. `exo.taste`
     signatures and `exo.capture` heuristics already do a rule version.
  2. **Reflect** only on events that passed stage 1: write the lesson in positive local form, its
     implication beyond the instance, and the evidence ids. `exo.consolidate` has this seam
     (`RuleReflector`, `ModelReflector`).
- **Tools.** Read-only store and skill access, plus one write tool: `propose`. The reviewer cannot
  edit nodes, skills or the pack directly. Everything is a proposal. This is stricter than Hermes and
  closes Command Code's missing validation gate.
- **Routing rule (from Hermes, extended).** One lesson goes to one place:
  - **Taste rule:** style, tone, format, stance, values. Goes to the principal pack.
  - **Skill patch:** how to do a class of task. Goes to the governing skill. If a correction is about
    a task with a skill, it patches the skill, not the pack.
  - **Fact:** dates, people, paths, project state. Goes to retrieval with provenance.
  - **Open question:** the event is ambiguous or conflicts with a guarded belief. Goes to `exo.ask`.
- **Do-not-capture list.** Carry Hermes's list over unchanged: environment failures, negative claims
  about tools, transient errors, one-off narratives, unresolved failures dressed as workflows.
- **Critic.** For any proposal that would change a high-confidence belief or a skill used often, a
  second short pass argues against it ("is this a one-off? does it contradict a ruling?"). Its
  objection is attached to the proposal. This is principle 14 at small scale.

### 5.5 Outputs

Each output is a `belief_proposed` event plus a draft file in `<store>/proposals/`, as
`exo.consolidate` already does. Proposal fields:

```
id, type (taste | skill | fact | question), domain, node or skill target,
claim ("The principal wants Y, not X, because Z"), implication,
evidence [event ids], strength per evidence, confidence, half_life,
guarded (irreversible or public?), supersedes, critic_note, diff (for skills and nodes)
```

Rendered views, all rebuildable from the ledger:

- **Principal pack** (cached system block): taste rules grouped by domain, each with its confidence
  label. High is enforced. Medium is a hint. Low is not shown; it waits for more evidence or a question.
  This is Command Code's enforce versus hint split.
- **`taste.md` export** in Command Code's format (`- rule. confidence: 0.82`) for interop.
- **Skills:** patches to `skills/<name>/SKILL.md` with the diff and evidence in the proposal.
- **Memory and facts:** store nodes with source lines.
- **Questions:** entries in `queues/questions.jsonl`, scored by value of information.
- **Training rows:** accepted taste and corrections append to `training/preference-pairs.jsonl`
  (context, rejected, chosen, evidence). These later train an adapter (D-006) and feed the eval.

### 5.6 Confidence and promotion

Weight comes from `exo.taste.combine`: noisy-or of decayed evidence strengths. Labels: high at 0.75 or
more, medium at 0.45 or more (`src/exo/taste.py:26-27`). Half-life 30 days for preferences, none for
rulings.

Promotion rules, in order:

1. **Principal said it** (a ruling, an answer, or the principal accepts the proposal): promote now.
2. **Guarded** (touches irreversible, public, financial or third-party acts; A's list lives in his
   store): never auto-promote. Becomes a question or waits for review.
3. **Conflict** with an existing belief: the store's conflict rule. Default newer wins unless the older
   one is guarded; then a numbered question. Never overwrite silently.
4. **Taste or fact, not guarded, weight high, at least 2 independent evidence events from different
   sessions, and no critic objection:** auto-promote, logged and shown in the next brief as "learned".
5. **Skill patch:** promote only if the eval gate passes (Section 6). Skills change behavior most, so
   they carry the strictest gate.
6. **Everything else** stays a proposal. Medium weight may enter the pack as a hint. Low weight waits.
   If it sits open past a staleness window (14 days) and the value of information is high, it becomes a
   question; otherwise it decays away.

The principal sees promotions in the morning brief, a few lines, ranked. Not an approval queue for
every item (principle 3, Bainbridge).

### 5.7 Reverting a wrong lesson

Lessons are views of the ledger, so reverting is adding an event, never deleting one.

- **Explicit revert.** "That's wrong" or `exo proposals revert <id>` writes a `belief_rejected` note
  with a reason. The belief leaves the pack and skills on the next render. The rejection is itself a
  signal: an opposite-polarity lesson is proposed if the reason implies one.
- **Contradiction.** A later correction that conflicts drops the weight through the conflict rule.
- **Decay.** Unreinforced preferences fade by half-life. Rulings never decay; only the principal
  retires them.
- **Eval regression.** If the nightly eval shows a score drop after a promotion, the slow loop bisects
  recent promotions (replay with and without each) and proposes reverting the culprit. Auto-revert is
  allowed for auto-promoted lessons; principal-promoted lessons get a question instead.
- **Skill rollback.** Skills are git-tracked in the store; every promoted patch is one commit linked to
  its proposal id. Revert is `git revert` plus a ledger note. This replaces Hermes's separate JSONL
  ledger with one mechanism.
- **Answer "why?"** Every pack line and skill rule links to its proposal, and the proposal to its
  evidence events. A wrong lesson is traced to the event that caused it.

### 5.8 Cost control

- No review on turns with no new signal. Corrections always qualify.
- Input is the delta since the watermark plus touched nodes, not the full transcript.
- Stage 1 classify on rules or a small local model. Stage 2 reflect only on survivors, batched.
- Critic only for high-impact proposals.
- Hard caps per review: 8 iterations, a token budget, and a daily budget for the fast loop.
- The slow loop runs on idle machines at night (A's night shift, principle 15) under the monthly
  ceiling A sets (D-006). Cost per accepted lesson is reported.
- Prompt cache parity where the main model is reused (Hermes's trick), digest when routed.

### 5.9 Small local models

The long-term target is a portable device. Design rules that keep the loop runnable there:

- Every stage has a rule fallback: `exo.capture` heuristics, `exo.taste` signatures, `RuleReflector`.
  A model makes each stage better but is never required.
- Stage 1 is a small classifier with structured output (signal or noise, type, domain, node). A 1-3B
  model can do this; it is the job Command Code gives `taste-1`.
- Stage 2 and the critic can wait for a bigger model when one is reachable. On the device, proposals
  queue and the sync step runs them later.
- The pack is short and greppable so it fits a small context.
- The store and ledger are plain files and SQLite; the device carries the store, not the compute.

### 5.10 Generic for any user, fitted to A

| Generic mechanism (ships with Exo) | Principal data (lives in the store) |
|---|---|
| Signal capture, verdict buttons, git edit detection | The events |
| Reviewer prompts, routing rule, do-not-capture list | Domains and categories |
| Confidence math, half-lives, promotion rules | Tuned thresholds, half-lives |
| Conflict rule mechanism | A's rule: newer wins unless guarded |
| Guard mechanism | A's guarded list (spend, model choice, public posts, logins, irreversible acts) |
| Default taste prior and default skills (principle 8) | A's delta |
| Eval harness (Section 6) | A's held-out set and judge rubric |

A new user starts with the prior, an onboarding interview (`exo init`), and an empty delta. A starts
with hafiz imported.

### 5.11 Mapping to Exo modules

| Module | Role in the loop | Change needed |
|---|---|---|
| `exo.ledger` | All signals and decisions as events | Add `verdict` diff body convention; `belief_rejected` stays a `note` type |
| `exo.capture` | Rule-based stage 1 from chat | Add git-edit and UI verdict sources |
| `exo.consolidate` | Reviewer core: proposals, rule of three, conflict rule, accept, reject | Add typed outputs (taste, skill, fact, question), critic note, auto-promote policy, revert command |
| `exo.taste` | Signatures, decay, weight, conflict | Add `taste.md` export; per-domain half-lives from policy |
| `exo.ask` | Questions from conflicts and stale proposals, VoI, interruption budget | Accept proposals as question sources |
| `exo.eval` | Score, gate, scoreboard (Section 6) | Add judge grader, replay set, candidate comparison, gate |
| `exo.retrieve` | Touched-node lookup for the reviewer; facts at turn time | None for v1 |
| skills (store, git) | Procedure lessons | Patch proposals as commits; eval gate before merge |
| `exo.brief` | Shows learned, reverted and asked items | A "learning" section, top few only |
| runtime (new core, D-010) | Fork after turn, cancel on live turn, idle queue | Port Hermes's fork mechanics, not its prompts verbatim |

---

## 6. Hill-climbing: the eval the loop climbs against

The loop proposes changes. The eval decides which changes stay. Without it, the loop can drift: it
learns things that sound right but do not predict the principal. With it, every change must make the
harness measurably better at predicting and pleasing this one user.

### 6.1 The eval set

Three parts, all per user, all in `<store>/training/` or `<store>/eval/`:

1. **Eval Q&A** (`eval-qa.jsonl`): questions about the principal with gold answers and the node they
   test. For A, 28 items exist today in `~/hafiz/training/eval-qa.jsonl`.
2. **Preference pairs** (`preference-pairs.jsonl`): context, rejected, chosen, evidence, node. For A,
   25 exist in `~/hafiz/training/preference-pairs.jsonl`. Each accepted correction adds one.
3. **Session replays.** Real past turns cut at the point before the principal's verdict. The input is
   the context the agent had. The target is what the principal did: accepted, the edit they made, the
   choice they picked, the ruling they gave. Built automatically from ledger `verdict`, `answer` and
   `ruling` events.

Split each part by time:

- **Train** (oldest): the reviewer may read it to write lessons.
- **Dev** (middle): candidates are scored on it during the climb.
- **Held out** (newest, and a fixed 20% sample the loop never reads): used only for the accept
  decision and the regression gate. Collect it before the profile that it tests (principle 12).

New items flow in weekly. Held-out items age into dev after a set time, and fresh ones replace them,
so the held-out set always tests recent behavior.

### 6.2 Scoring

Per user, n=1. Two kinds of grader, combined:

- **Checkable rules** (cheap, exact). Regex and structural checks from the principal's hard rules:
  for A, no em dashes, numbered decisions with defaults, a link or path in every approval request, no
  signing as an agent on external posts. Each taste rule that can be checked gets a checker. A rule
  violation is a hard fail for that item.
- **Judge model** (for everything else). Given the item, the gold or chosen answer, the rejected
  answer and the principal's rubric, score 0 to 1. Use a different model from the one being tested.
  Run several samples and average (principle 12: judge over many samples). Check stance and
  conclusion, not just voice. `exo.eval.parse_score` already parses judge output.
- **Choice accuracy.** For pairs: does the candidate pick chosen over rejected with order randomized
  (`exo.eval.run_choices`).
- **Replay match.** For session replays: did the candidate predict accept versus edit, and how close
  is its output to the principal's final version (edit distance, judged).
- **Live metrics** (not climbed on directly, but tracked): corrections per accepted output,
  endorsement rate, questions per day (`exo.eval.longitudinal`).

One composite score per run, plus the parts by domain. Weights live in the store.

### 6.3 What gets varied

A **candidate** is the current configuration with one change. Changes come from the reviewer's
proposals and from the slow loop's own search:

- **Prompt pack:** wording, order and grouping of taste rules; which rules are enforced versus hints.
- **Taste rules:** add, drop, merge, reword, change confidence label.
- **Skills:** a patch to one skill.
- **Memory selection:** retrieval budget, which nodes enter context, ranking weights.
- **Routing:** which model handles which task class (within models the principal approved; model
  choice stays A's call).
- **Thresholds:** ask-or-act threshold per domain, promotion cut points, half-lives.

### 6.4 Accept only if better

The climb is greedy and conservative:

1. Score the current configuration (the incumbent) on dev and held out. Cache it.
2. Score the candidate on dev. If it is not better by a margin (default 1 point, and outside the
   run-to-run noise measured by rescoring the incumbent), drop it.
3. If it is better on dev, score it on held out. Accept only if held out also does not drop, and no
   domain drops by more than a set amount. This is the overfitting guard: a change that helps dev but
   not held out is fitting the dev set.
4. Accepted candidates become the new incumbent. The decision is a ledger event with both scores.

Taste rules and skills are text, so this is hill-climbing over a prompt and file space, like DSPy-style
prompt search, but each step is one interpretable change the principal can read and revert.

### 6.5 Regression gates before promotion

Promotion rule 5 (skills) and any auto-promotion of a high-impact taste rule must pass:

- **No held-out drop** in the composite score.
- **No checkable-rule regressions.** Any item that passed before and fails now blocks the change.
- **No guarded-domain regression** at all (public posts, spend, irreversible acts).
- **Pinned cases.** A small set of must-pass items the principal marked as critical. Always run.

A failed gate turns the proposal into a held proposal with the failing items attached. If the
principal explicitly ruled the lesson, it is promoted anyway (the principal outranks the eval) and the
failing items are flagged as possibly stale gold.

### 6.6 Scoreboard over time

`exo eval` already writes `reports/eval-YYYY-MM-DD.json` and `.html`. Extend it to a scoreboard:

- One row per run: date, incumbent id, composite, each part, cost.
- Each accepted change on the timeline, linked to its proposal.
- Live metrics by week next to eval scores, so offline gains can be checked against real corrections.
- A short line in the weekly digest: "score 0.71 to 0.74 this week, 6 changes kept, 2 reverted".

If live corrections rise while the eval score rises, the eval set is stale or gamed. That triggers a
refresh of held-out items from recent sessions.

### 6.7 Cost caps

- Fast loop never runs the full eval. It runs only pinned cases and checkable rules for the touched
  domain (seconds, mostly no model calls).
- Full eval runs in the nightly slow loop, on idle machines.
- Cap candidates per night (default 10) and judge samples per item (default 3).
- Cache incumbent scores; rescore only items the candidate could affect (same domain or node).
- Checkable rules run first; the judge runs only on items that pass them.
- Judge model choice and the monthly ceiling are the principal's; Exo reports cost per point gained.

### 6.8 Reusing `src/exo/eval.py`

What already exists: `load_jsonl`, `run_qa` with pluggable `Responder` and `Grader`, `exact_grader`,
`keyword_grader`, `parse_score`, `run_choices` with randomized order, `longitudinal` metrics from the
ledger, `run_eval`, and HTML reports. Nothing in it calls an API.

What to add, in the same style:

- `judge_grader(complete, rubric, samples)`: a `Grader` that wraps any model call and averages.
- `rule_grader(checks)`: a `Grader` built from the store's checkable rules.
- `build_replays(ledger, cutoff)`: session replay items from verdict, answer and ruling events.
- `split(items, by="ts")`: train, dev, held-out split with a fixed held-out sample.
- `compare(incumbent, candidate, items)`: paired scores, noise band, per-domain deltas.
- `gate(result, policy)`: the regression gates in 6.5, returning pass or the failing items.
- `scoreboard(root)`: append a row and render the timeline.

The `Responder` seam is what makes candidates cheap: a candidate is a responder built from a pack,
skills and settings. The same code serves any user; only the files in their store differ.

---

## 7. Build order (suggested for D-010)

1. Verdict capture in the harness UI and git edit detection (signals in).
2. Typed proposals and the routing rule in `exo.consolidate`; `propose` as the reviewer's only write.
3. Eval additions: `build_replays`, `rule_grader`, `split`, `gate`. Rules-only first, no model needed.
4. Fast-loop fork in the new runtime: trigger on correction, cancel on live turn, idle queue.
5. Auto-promotion policy and revert command; brief section.
6. Judge grader, candidate comparison, nightly climb, scoreboard.
7. Small-model stage 1 classifier, measured against the rule version on the same eval.

## 8. Open questions for A

1. Auto-promotion: allow it for unguarded taste with two independent sessions of evidence, or review
   every promotion in the brief first? Default: allow, show in brief.
2. Monthly ceiling for the nightly eval and climb. Default: a small fixed amount, reported weekly.
3. Judge model for the eval. Default: a different family from the main model, chosen by A.
4. Should Command Code style `push/pull` of taste packages exist between A's seats and machines?
   Default: yes, via the signed store, never to a vendor.
