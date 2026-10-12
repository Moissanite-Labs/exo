# Taste baseline: ship one person's taste, then fit the next person (D-044)

Status: **Design, not built.** Proposed in D-044. Builds on D-040 (author provenance), D-012 and D-022 (the
learning loop), D-015 (decision models) and D-028 (one continuous memory).

## The idea in one paragraph

A new Exo is a stranger. It does not know how its user likes answers written, when to ask and when to act,
or what "done" means. Today it starts empty and learns all of that from scratch. The ruling is to start it
instead from a taste that was tuned by hand for a long time, the principal's, with every personal detail
removed. Then let it drift toward the new user. Old lessons fade on their own, slowly. A lesson the user
keeps acting against fades faster, but only after the user has acted against it several times on different
days, never on one signal. The bigger the gap between the old lesson and the new behaviour, the faster it
fades. A cheap decision model judges whether a new behaviour really goes against a lesson and how far. Only
the user's own messages and actions count. Text written by an agent, a peer, a job or a web page never does.

## 1. What "taste" is in Exo today, and what ships

### What exists now

| Part | Where | What it holds |
|---|---|---|
| Belief nodes | `<store>/nodes/<domain>/*.md` | Typed notes (trait, value, preference, rule, procedure, fact, entity, episode, question) with confidence and stability. Schema in `method/SCHEMA.md`. |
| Beliefs and their weights | the store's signed ledger; `src/exo/taste.py`, `src/exo/consolidate.py` | `belief_proposed`, `belief_accepted`, `belief_reinforced` events. Weight is a pure function of the evidence: each piece decays by half-life (preferences 30 days, rules never) and they combine by noisy-or. |
| Proposals | `<store>/proposals/`, `src/exo/learn/promote.py` | Lessons waiting for promotion under the rules in `docs/LEARNING.md`. |
| `taste.md` export | `exo.taste.export_taste_md` | The same beliefs in Command Code's format. |
| Policies | `<store>/policies/` | Gate matrix, approvals, budget, posting rules, consolidation knobs, brief, interruptions. |
| Soul | a seat's `SOUL.md` | The seat's standing voice and stance, re-read each turn. |
| Skills | `skills/` (shipped, with `MANIFEST.json`), `$EXO_HOME/skills`, seat folders | How to do a class of task. |
| Preference pairs | `<store>/training/preference-pairs.jsonl` | Real answers and the user's better versions. |
| One-page pack | `<store>/PRINCIPAL.md` | Who the principal is, in one page. |

### What ships as the baseline

The baseline is called the **house taste**. It is a generic pack, not a person. It holds:

- **Taste items**: accepted preference, rule, procedure and value beliefs about *how work is done*:
  writing style, answer shape, verification habits, when to ask, how to report. Each one becomes one item
  with an id, a domain, a claim in neutral form ("The user wants ..."), its signature, and a starting
  weight.
- **Policy defaults**: the gate matrix, approval categories, consolidation knobs and brief settings, with
  every name, seat, target and amount removed. These ship as the template store's `policies/`, as they do
  now, only better tuned.
- **A house soul**: a seat `SOUL.md` template with the voice and stance, no names.
- **Skills**: the shipped `skills/` pack, already scrubbed and versioned by its own manifest. The house
  taste lists the skills it expects but does not copy them.

### What never ships

- Nodes of type trait, fact, entity or episode, and anything in `person/`, `relationship/` or a client or
  project node. They describe a person or the people around them, not a way of working.
- Any node linked to those, or tagged `private`, `person` or `client`.
- The ledger, quotes, evidence references, dates, timeline, preference pairs, proposals, questions,
  `PRINCIPAL.md`, `agents/`, `corpus/private/`, approval grants, budget figures, seat names, account
  names, machine names, paths and internal ids.

### The export: `exo taste export-house` (spec)

    exo taste export-house --store <principal store> --out <dir> --version 1.3.0 [--check-only]

1. **Select.** Accepted beliefs only, of type preference, rule, procedure or value, in an allowed domain
   list (default `mind`, `work`, `systems`; open question 3). Confidence medium or higher, stability core or
   durable, current weight at least 0.45. Drop anything excluded above.
2. **Neutralise.** Rewrite each claim in neutral form ("The principal" and any name become "the user").
   Drop sources, quotes and dates; keep only the evidence count. Compute the signature with
   `exo.taste.signature`. The item id is a hash of the normalised claim, so it is stable across exports.
3. **Privacy check.** The export fails, and writes nothing, if any line hits:
   - the store's leak patterns (`exo.store.LEAKS`: phone numbers, keys, tokens, passwords);
   - the gate's internal-name sweep (seat names, registry names, agent names, home paths, internal ids,
     `policies/internal-names.txt`), reusing the builder from `docs/GATE.md` "The outbound scrub";
   - a deny list made from the source store itself: the titles, ids and aliases of every person,
     relationship, entity and project node, and every email address and URL host found in the store;
   - email addresses, URLs other than a short allow list of public docs, and capitalised words that are
     not in a plain dictionary (likely names), which are held for review rather than failed.

   An optional decision-model pass asks of each item "does this name or point to a real person, company,
   place or account?" It can only hold an item, never pass one (the D-015 rule: stricter, never looser).
4. **Write.** `MANIFEST.json` (version, script version, item count, a sha256 of each file, and the sha256 of
   the source ledger head, which reveals nothing), `items.jsonl` sorted by id, `policies/*.yaml`,
   `soul/house.md`. No timestamps in the files other than the version.
5. **Review.** The export also writes a plain list of every item for the principal to read. Publishing is
   a public, irreversible act, so it goes through the gate and needs the principal's approval each release.

**Reproducible:** the same store snapshot and script version give byte-identical output. A test runs the
export twice and compares bytes.

## 2. Layers and how a lookup resolves

There are three layers.

1. **Baseline.** The house taste, shipped inside the package (`src/exo/baseline/house-taste/`), read-only,
   versioned. `exo update` brings a new version. A new baseline version never overrides anything the user
   has learned or said.
2. **User layer.** The user's own store: beliefs learned from their signals, their rulings, their pins. It
   changes, and its inferred items decay.
3. **Pins.** A pin is a user act on any item (baseline or learned) that freezes it: no decay, no
   replacement. Pins live in the user layer.

To decide which version of a piece of taste applies, Exo looks in this order and stops at the first hit:

1. a **ruling** by the user ("always", "never", an explicit choice) that matches the item;
2. a **pinned** item;
3. an active **learned** item that overrides it (either it names the baseline item in `overrides`, or its
   signature matches by `exo.taste.relation`);
4. the **baseline** item, unless the user turned it off or it was replaced.

Resolution is per item, not per layer. One user can keep 40 baseline items and replace 3.

Every item shown to the model or the user carries its source: `[house 1.3]`, `[learned 0.72, 5 signals]`,
`[you said, 2026-10-14]`, `[pinned]`. The taste block in the system message lists active items ordered by
effective weight within a token budget. When the budget cuts, low-weight baseline items drop out of the
prompt first; they stay in `/taste` and in recall. The block is rebuilt at most once a day, or on a ruling
or pin, so the cached prefix (D-028) stays stable.

## 3. Decay and the delta rule

### Two kinds of fading

- **Innate decay.** Learned items already decay: weight is the noisy-or of evidence strengths, each halved
  every 30 days for preferences. Nothing changes there. Baseline items have no evidence, so they get a
  *precedence* that falls slowly from install day and never reaches zero:

      P_base(t) = max(0.3, 0.7 * 2^(-t / 180))      t = days since install

  At day 0 a baseline item counts as a medium-strong belief (0.7). After a year it sits at its floor of
  0.3. It still applies when nothing contradicts it; it is just easier to override.
- **Contradiction decay.** This is the delta rule below. It only runs once a contradiction is confirmed.

Rulings (type rule, or any item whose evidence includes a user ruling) never decay, as today. Policies,
soul and skills are not taste items and do not decay at all. A gate policy changes only by the user's
explicit edit; nothing learned can loosen a gate.

### The delta rule

Each item keeps a **contradiction record**, rebuilt from the ledger like weights are (no side file):

- a list of user signals that went against it, each with a delta size `d` in [0, 1];
- signals that agreed with it.

Only a signal whose author is the user (D-040: `author == principal`) is counted. One signal changes
nothing at all.

The item becomes **armed** when, inside the last W days, it has:

- at least **N = 3** contradicting signals, each a distinct ledger event,
- spread over at least **M = 2** different calendar days (in the user's time zone),
- and contradictions minus agreements is still at least N.

While armed, the item's weight gets an extra fading factor that grows with the size of the gap:

    W_item(t) = W_innate(t) * 2^(-(t - t_arm) * d_mean / H_c)

    W_innate   P_base(t) for a baseline item, the evidence weight for a learned item
    t_arm      the day the item was armed
    d_mean     mean delta of the contradicting signals in the window
    H_c = 7    days; at d_mean = 1 the item halves every 7 days, at 0.5 every 14, at 0.25 every 28

The contradicting signals also build a **challenger**: the user's version of the item, written by the
reflector. Its weight is the usual noisy-or of its signals' strengths with the 30-day half-life. Strengths
are the existing table (`edit` and `reject` 0.6, `should_have_been` 0.8), and 0.3 for an inferred signal
(the user simply did it differently, with no verdict).

The challenger **replaces** the item when, while armed:

    W_challenger >= W_item + 0.15     and     W_challenger >= 0.45

On replacement the challenger becomes an accepted learned item with `overrides: <item id>`, a ledger row
`taste_replaced` is written, and the next brief mentions it. The old item is kept as `overridden` and can
be restored. It is never deleted.

**Disarm.** If contradictions age out of the window or agreements cancel them, so the count falls below N,
the item disarms and the extra factor is dropped. Until replacement, nothing is lost.

**Exceptions.**

- A guarded item (it protects something public, irreversible, financial or third-party, `exo.taste.guards`)
  is never replaced automatically. Arming raises one question instead.
- A user ruling wins at once. No counting, no decay: it is promoted now (promotion rule 1), and it
  overrides the matching item.
- Behaviour that goes against a user ruling never decays the ruling. After N signals over M days, Exo asks
  one question: "You said always X. You have done Y 3 times since. Keep the rule, change it, or limit it?"

### Defaults

| Knob | Default | Config key (`policies/consolidate.yaml`, `taste:` table) |
|---|---|---|
| Baseline start precedence | 0.7 | `baseline_start` |
| Baseline innate half-life | 180 days | `baseline_half_life` |
| Baseline floor | 0.3 | `baseline_floor` |
| Signals to arm (N) | 3 | `arm_signals` |
| Distinct days to arm (M) | 2 | `arm_days` |
| Window (W) | 30 days | `arm_window` |
| Contradiction half-life at full delta (H_c) | 7 days | `contradiction_half_life` |
| Replacement margin | 0.15 | `replace_margin` |
| Cooldown after a replacement | 14 days | `flip_cooldown` |
| Flips before Exo asks instead | 2 in 60 days | `max_flips` |

### Worked example

House item B: "Write answers as short paragraphs; no bullet lists unless asked." A new user, Sam, keeps
rewriting answers as bullet lists. The decision model rates each rewrite as a contradiction with delta 0.8.
Each is an inferred signal, strength 0.3.

| Day | What happens | B weight | Sam's version | Result |
|---|---|---|---|---|
| 0 | Install | 0.70 | none | B applies |
| 3 | Two rewrites in one thread | 0.69 | 0.51 | 2 signals, 1 day: not armed, nothing changes. `/taste` shows it as pending. |
| 5 | Third rewrite | 0.69 | 0.64 | 3 signals, 2 days: **armed**. Fading starts. |
| 8 | No new signals | 0.54 | 0.61 | Needs 0.69. B still applies. |
| 9 | No new signals | 0.49 | 0.60 | Needs 0.64. B still applies. |
| 10 | Fourth rewrite | 0.45 | 0.72 | 0.72 >= 0.60: **replaced**. Sam's "bullet lists for answers" is now his taste; B is kept as overridden. |

Without the fourth rewrite, the swap would come on day 11 as B keeps fading. With a small delta (0.25, say
Sam only uses lists for step-by-step instructions) the same four signals swap on day 20, not day 10. And
the decision model would more likely call that a *refinement*, which adds a narrower learned item ("lists
for steps") beside B and does not fade B at all.

## 4. The decision model's role

For each new user signal, Exo first finds candidate items cheaply: the signature overlap from
`exo.taste.similarity`, top 3 items with overlap at least 0.3. Only then does it ask the `decision` role
(D-015; local Laya by default, so free and private) two typed questions:

- **choice:** does this behaviour `contradict`, `refine`, `agree` with, or stand `unrelated` to the item?
- **score:** how far apart are they: none, small, medium, large, opposite (d = 0, 0.25, 0.5, 0.75, 1)?

Rules around it:

- **It only classifies.** Its answer adds a signal to a record. It never promotes, replaces or pins. The
  formula above does that, from the ledger.
- **It abstains when unsure.** Below confidence 0.6 the signal counts as unrelated.
- **Cheap and capped.** At most 3 items per signal and 50 taste questions a day (`taste.daily_cap`). Over
  the cap, signals wait for the next day. Without a decision route, Exo falls back to
  `exo.taste.relation` (conflict means contradict, with d = 0.5).
- **Logged.** Each answer is a ledger `note` of type `taste_signal` with the item, relation, delta,
  confidence, route and the evidence id, so the learning loop and eval can score it later.
- **What it sees.** The item text, the user's own words or action, and the agent reply being corrected,
  labelled as such. It never sees tool output, web pages, peer messages or agent notes as the "behaviour".

### Failure modes and their guards

- **Drift from noisy signals.** One bad day cannot move anything: N signals over M days, a window, abstain
  below 0.6 confidence, disarm when the pattern stops, refinements kept apart from contradictions.
- **Manipulation by injected text.** Only signals whose author is the user count (D-040). Agent-written
  rulings (`exo_note`), peer, bus and job text, tool results and fetched pages carry their own author and
  are skipped before the decision model is asked. Inside a user message, pasted blocks (quotes, code,
  attachments) are not counted as the user's behaviour. A test plants each of these and checks that no
  counter moves.
- **Oscillation.** Hysteresis: a replaced item needs a full new arming and the same margin to come back,
  and not within the 14-day cooldown. Two flips of one item in 60 days stop automatic changes for it and
  raise a question, because the answer probably depends on context the item does not capture.
- **Gate loosening.** Impossible by design: policies are not taste items, and a decision model may only
  make a gate stricter.

## 5. What the user sees

`/taste` (a `command.run` command, since the protocol is full at 30) and `exo taste` show four lists:

- **House** items in use, with their weight and version.
- **Learned** items, with weight, signal count and source (`learned`, `you said`, `pinned`).
- **Pending** contradictions, one line each: "You have written bullet lists 3 times over 2 days against
  house item 'short paragraphs'. Fading since Oct 15; likely switch in about 5 days." with three actions:
  *keep house* (pins B), *switch now* (records a ruling), *ignore* (no change).
- **Overridden** house items, each restorable.

Commands: `/taste show <id>`, `/taste why <id>` (the signals behind it), `/taste pin <id>`, `/taste unpin
<id>`, `/taste off <id>` (turn a house item off now; this is a ruling), `/taste reset <id>` (drop the learned
version and return to the house item), `/taste reset all` (clears the learned taste after a confirm; ledger
rows stay).

**First run.** The onboarding `/intro` (D-030) says, in about this many words: "Exo starts with a house
taste: working habits tuned by its first user over many months, such as plain short writing, checking work
before calling it done, and asking before anything public. It will adapt to you. When you do something
differently a few times over a couple of days, it starts to shift, and it tells you. Type /taste to see
what it is using and why." The user can also choose **start blank** there, which turns the house layer
off.

## 6. The principal's own installs

The principal's store is the source of the house taste, not a user of it.

- On an install whose store is the export's source (the manifest's source hash matches), the house layer
  is off. Nothing is counted twice.
- The baseline mechanism (baseline precedence and its innate decay) never touches the principal's items.
  They keep the learning rules they have today: preference half-life, rulings that never fade, conflicts
  that go through the conflict rule.
- Nothing learned on another user's install ever flows back into the house taste. It changes only when the
  principal runs a new export and approves it.

Whether the delta rule should also run on the principal's own inferred items is open question 1.

## 7. Build plan

**Wave 1: layers and export.**
`src/exo/baseline/` (loader, read-only pack, resolution with provenance tags), `exo taste export-house`
with the privacy check, the taste block in the prompt, the first pack exported and reviewed. Tests:
the export is byte-identical when run twice; a planted name, email, seat name, phone number or person node
fails the check; excluded types never appear; resolution order (ruling, pin, learned, house); a fresh
install with an empty store runs end to end with the house layer (D-004); the principal's install loses
nothing and shows the house layer as off.

**Wave 2: decay and the delta rule.**
Pure functions in `exo.taste` (`baseline_precedence`, `contradiction_record`, `effective_weight`,
`should_replace`), wired into `exo.consolidate` and promotion rule 3 for unguarded inferred items. Tests:
the worked example's numbers to two decimals; one signal changes nothing; three signals on one day do not
arm; signals authored by an agent, peer, job or `exo_note` never count (on top of the D-040 tests); a ruling
overrides at once; behaviour against a ruling raises one question and never fades it; disarm restores the
weight; cooldown and the two-flip question.

**Wave 3: the decision model and the view.**
`exo.learn.taste_classify` (candidate filter, the two questions, confidence floor, daily cap, fallback,
ledger notes), `/taste` and `exo taste`, the `/intro` text, user guide and whitepaper sections. Tests: a stub
model that says "contradict" with low confidence adds nothing; the cap holds; a classifier cannot cause a
replacement below the thresholds; a pasted block saying "always X" is not counted; `/taste` renders a
pending line with the right count and days.

## Open questions for the principal

1. Should the delta rule also run on your own inferred preferences (not your rulings), or only for other
   users? Proposed: off for you at first, on for everyone else.
2. Are the speeds right? Proposed: 3 signals over 2 days to start a shift, a full swap in roughly 1 to 3
   weeks depending on how far apart the habits are, house items reaching their floor after about a year.
3. Which domains may ship in the house taste? Proposed: writing and answer style, work method and
   verification, gate and approval defaults, and the skills pack. Not proposed: values and worldview.
