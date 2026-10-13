# Taste baseline: ship one person's taste, then fit the next person (D-044)

Status: **Accepted; wave 1 built** (layers and export, see "Build status" below). Waves 2 and 3 are not built.
D-044, with the principal's rulings of 2026-10-12 (see "Rulings" at the end).
Builds on D-040 (author provenance), D-012 and D-022 (the learning loop), D-015 (decision models) and D-028
(one continuous memory).

## The idea in one paragraph

A new Exo is a stranger. It does not know how its user likes answers written, how they check work, or what
they care about. Today it starts empty and learns all of that from scratch. Instead it starts from a taste
that was tuned by hand for a long time, the principal's, with every personal detail removed. Then it moves
toward the new user the way a person changes: slowly, and only when the change lasts. A bad week, a mood or
a deadline does not count. A habit the user keeps for weeks does. Values are the exception, on purpose. A
companion whose values grate is the most off-putting kind, so a house value that meets real disagreement
steps back at once and goes neutral, and only later, once the user's own view is clear, takes that view on.
Each user picks whether changes happen quietly (and show up afterwards) or wait for a yes. Only the user's
own words and actions count. Text written by an agent, a peer, a job or a web page never does.

Terms used below:

- **Item**: one piece of taste, such as "write answers as short paragraphs". It has an id, a claim and a
  weight.
- **Weight**: how strongly an item applies, from 0 to 1. Higher weight wins a conflict and gets a place in
  the prompt first.
- **Signal**: one thing the user did or said that bears on an item: an edit, a rejection, a rewrite, a
  stated view.
- **Episode**: all of the user's signals about one item within one session or one calendar day, taken as
  one. Five rewrites in an afternoon are one episode.
- **Swap**: the user's version of an item replaces the house version.
- **Ruling**: an explicit instruction by the user ("always", "never", a stated choice about how Exo should
  work). Rulings are not inferred and are not covered by the sustained-change rule.

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

The baseline is called the **house taste**. It is a generic pack, not a person. It has two classes of item
and two companions:

- **Method items**: accepted preference, rule and procedure beliefs about *how work is done*: writing and
  answer style, work method and verification habits, how to report. Claims are in neutral form ("The user
  wants ...").
- **Value items**: accepted value beliefs and worldview beliefs: what matters and how to weigh things, such
  as "say when the evidence is thin" or "the user's time is worth more than a complete answer". They are
  identity-free (section 1, "Value items and privacy") and they adapt by their own rule (section 4).
- **A house soul**: a seat `SOUL.md` template with the voice, no names. It holds no value claims and no
  safety rules. Values live only as value items, so they can step back one by one; a value written into the
  soul could not.
- **Skills**: the shipped `skills/` pack, already scrubbed and versioned by its own manifest. The house
  taste lists the skills it expects but does not copy them.

### Safety and gate defaults are not taste

The gate matrix, approval categories, budget, posting rules, consolidation knobs and brief settings are
**product policy**. They ship as ordinary defaults in the template store,
`templates/principal-store/policies/` (`gates.yaml`, `approvals.yaml`, `budget.yaml`, `posting.md`,
`consolidate.yaml`, `brief.yaml`, `interruptions.yaml`), and are documented in `docs/GATE.md`. They are
changed by a normal reviewed code change to that folder, never by the taste export, and on an install only
by the user's explicit edit. They have no weight, no house precedence and no decay, and nothing in this
design reads or writes them. If the principal wants a safety habit of his to become a default, it goes into
those files as a code change, not into the house taste.

For the same reason the export drops **guarded items** (an item that protects something public,
irreversible, financial or third-party, `exo.taste.guards`). In a user's own store, guarded learned items
keep today's rule: never changed automatically, only by a question (promotion rule 2).

### What never ships

- Nodes of type trait, fact, entity or episode, and anything in `person/`, `relationship/` or a client or
  project node. They describe a person or the people around them, not a way of working.
- Any node linked to those, or tagged `private`, `person` or `client`.
- Guarded items and every policy file (above).
- The ledger, quotes, evidence references, dates, timeline, preference pairs, proposals, questions,
  `PRINCIPAL.md`, `agents/`, `corpus/private/`, approval grants, budget figures, seat names, account
  names, machine names, paths and internal ids.

### Value items and privacy

The rule from D-004 holds: **ship the taste, not the identity**. A value can say how to weigh things. It must
not say, or let anyone work out, who holds it.

A value item may ship only when it passes all of these:

1. **It is a stance, not a fact about someone.** "Prefer evidence over authority" passes. "Because I grew up
   in X, I distrust Y" does not: it is personal history.
2. **It names no one and no group.** No person, company, nation, ethnic or religious group, party, movement
   or place.
3. **It is not an identity marker.** No religious belief or practice, no party or partisan position, no
   stance on a contested political or religious question, no health, family or life event. These identify
   the holder, and they are the views most likely to repel a stranger. On contested questions the house
   taste ships the thinking, not the positions (ruling 4): the reasoning style behind the principal's
   worldview and what it implies for how to weigh a question, never a religious prescription or a
   border-based political stance.
4. **Many people could hold it.** It is phrased at the level of a general principle, not a narrow conviction
   that points to one person.

**How a value is prepared for export.** The exporter makes limited mechanical edits to the claim; it does not
infer or write a general principle from a personal story. Before exporting, the principal must rephrase a
candidate as a general working principle in the source store. The exporter then:

- Keep only the claim, never its evidence, quotes, dates or links.
- Replace references to "the principal" with "the user".
- For a value, remove a parenthetical example, a "for example" tail and the clause after "because".
- If the candidate needs its example to make sense, rephrase it in the source store before export; the
  exporter cannot infer or supply the missing principle.

For example, the principal should edit "My years at a firm that hid bad numbers taught me to show the bad
news first" into a general claim such as "Lead with bad news; do not bury it" before export. The exporter
does not make that semantic rewrite. If a first-person claim remains, the tighter value check rejects it and
the export fails closed without writing a pack; rephrase the source claim and run the export again.

**The tighter check for the value domain.** On top of the general privacy check (section 1, "The export",
step 3), every value item must also pass:

- a deny list of identity categories: religion and religious terms, political parties and ideologies by
  name, nationalities and ethnic groups, place names, family roles in first person ("my father"), and first
  person past tense ("when I", "I used to"), which signals personal history;
- the dictionary check for capitalised words, which for values fails the item instead of holding it;
- a decision-model pass that asks "does this reveal or point to the holder's religion, politics, ethnicity,
  nationality, health, family, or a life event?" As elsewhere it can only hold an item, never pass one;
- a human read: every value item is listed on its own page of the review file, and the release cannot be
  approved until the principal has ticked each one.

### The export: `exo taste export-house` (spec)

    exo taste export-house --store <principal store> --out <dir> --version 1.3.0 [--check-only]

1. **Select.** Accepted beliefs only. Method items: type preference, rule or procedure in the domains
   `work`, `systems` and `mind`. Value items: type value in any of those domains, and worldview beliefs in
   `mind`. Confidence medium or higher, stability core or durable, current weight at least 0.45. Drop
   anything excluded above, including guarded items.
2. **Normalise.** Replace "the principal" with "the user"; for values, remove examples and the causal clause
   as described above. This is mechanical cleanup, not a semantic rewrite. The principal must rephrase any
   first-person value claim in the source store before export. Drop sources, quotes and dates; keep only the
   evidence count. Compute the signature with `exo.taste.signature`. The item id is a hash of the normalised
   claim, so it is stable across exports.
3. **Privacy check.** The export fails, and writes nothing, if any line hits:
   - the store's leak patterns (`exo.store.LEAKS`: phone numbers, keys, tokens, passwords);
   - the gate's internal-name sweep (seat names, registry names, agent names, home paths, internal ids,
     `policies/internal-names.txt`), reusing the builder from `docs/GATE.md` "The outbound scrub";
   - a deny list made from the source store itself: the titles, ids and aliases of every person,
     relationship, entity and project node, and every email address and URL host found in the store;
   - email addresses, URLs other than a short allow list of public docs, and capitalised words that are
     not in a plain dictionary (likely names), which are held for review rather than failed (failed, for
     value items);
   - for value items, the tighter check above.

   A first-person value claim that survives mechanical cleanup fails this check. Nothing is written until
   the principal rephrases the source claim and reruns the export.

   An optional decision-model pass asks of each item "does this name or point to a real person, company,
   place or account?" It can only hold an item, never pass one (the D-015 rule: stricter, never looser).
4. **Write.** `MANIFEST.json` (version, script version, item count, a sha256 of each file, and the sha256 of
   the source ledger head, which reveals nothing), `items.jsonl` sorted by id with a `class` field (`method`
   or `value`), `soul/house.md`. No policy files. No timestamps other than the version.
5. **Review.** The export also writes a plain list of every item, values on their own page, for the
   principal to read. Publishing is a public, irreversible act, so it goes through the gate and needs the
   principal's approval each release.

**Reproducible:** the same store snapshot and script version give byte-identical output. A test runs the
export twice and compares bytes.

## 2. Layers and how a lookup resolves

There are three layers.

1. **Baseline.** The house taste, shipped inside the package (`src/exo/baseline/house-taste/`), read-only,
   versioned. `exo update` brings a new version. A new baseline version never overrides anything the user
   has learned or said.
2. **User layer.** The user's own store: beliefs learned from their signals, their rulings, their pins. It
   changes, and its inferred items can shift.
3. **Pins.** A pin is a user act on any item (baseline or learned) that freezes it: no decay, no swap, no
   step back. Pins live in the user layer.

To decide which version of a piece of taste applies, Exo looks in this order and stops at the first hit:

1. a **ruling** by the user that matches the item;
2. a **pinned** item;
3. an active **learned** item that overrides it (it names the item in `overrides`, or its signature matches
   by `exo.taste.relation`), including a scoped one whose scope matches the current work;
4. the **baseline** item, unless the user turned it off, it was swapped, or (value items) it has stepped
   back.

Resolution is per item, not per layer. One user can keep 40 baseline items and replace 3.

Every item shown to the model or the user carries its source: `[house 1.3]`, `[learned 0.72, 5 weeks]`,
`[you said, 2026-10-14]`, `[pinned]`, `[stepped back]`. The taste block in the system message lists active
items ordered by weight within a token budget. When the budget cuts, low-weight baseline items drop out of
the prompt first; they stay in `/taste` and in recall. The block is rebuilt at most once a day, or on a
ruling, pin, swap or step back, so the cached prefix (D-028) stays stable.

## 3. How method items change: the sustained-change rule

People change slowly. A mood lasts hours, a swing a week or two, a circumstance (a deadline, a project, a
kind of task) as long as it lasts. A real change of habit shows up week after week in ordinary work. The
rule below is built so that only the last kind moves a method item.

### Innate decay of house items

Learned items decay as they do today: weight is the noisy-or of evidence strengths, each halved every 30 days
for preferences. Baseline items have no evidence, so they get a *precedence* that falls slowly from install
day and never reaches zero:

    P_base(t) = max(0.3, 0.7 * 2^(-t / 365))      t = days since install

At day 0 a baseline item counts as a medium-strong belief (0.7). It reaches its floor of 0.3 after about 15
months (day 446). It still applies when nothing contradicts it; it is only easier to override. Unchallenged,
nothing is ever swapped by time alone.

### Step 1: signals become episodes, episodes become weeks

- **Only the user's signals count** (D-040: `author == principal`). See section 5 for the guards.
- **Episode.** Signals about one item in the same session or the same calendar day (user's time zone) join
  into one episode. Its verdict is *contradict* if contradicting signals outnumber agreeing ones, *agree* if
  the reverse, and *void* if they tie. Its delta is the mean delta of its contradicting signals.
- **Agreement** means the user did it the house way themselves, or explicitly accepted it. Silence is not a
  signal.
- **Week.** A calendar week in the user's time zone. Its verdict is *contradicting* if its contradict
  episodes outnumber its agree episodes, *agreeing* if the reverse, otherwise neutral. A week with no session
  at all is skipped, so a holiday neither helps nor hurts.
- **Persistence score S.** Starts at 0. Each contradicting week adds 1, each agreeing week takes 1 away, and
  S never goes below 0. A return to the old habit therefore counts against the shift, week for week.

Everything is evaluated once, at the close of each week. Weights change only then.

### Step 2: arming

An item **arms** at a week close when S reaches **3** and those contradicting weeks all fall within the last
**8 weeks**. So the earliest arming is three weeks after a change starts, and a change that comes and goes
inside a fortnight never arms.

**Disarm.** An armed item disarms when S falls to 1 or less, or after 4 active weeks (weeks with sessions)
with no contradicting episode. Disarming leaves **no residue**: the extra fading is dropped, the
contradiction record is cleared, and a later change has to start again from S = 0. The ledger rows stay, so
`/taste why` can still show what happened.

### Step 3: fading while armed

While armed, the item's weight fades with each further week of sustained change, not with the calendar:

    W_item = W_innate * 2^(-n * d_mean / H_w)

    W_innate   P_base(t) for a baseline item, the evidence weight for a learned item
    n          S - 3: net contradicting weeks past arming (0 at arming; never below 0)
    d_mean     mean delta of the contradicting episodes in the record
    H_w = 4    weeks; at d_mean = 1 the item halves every 4 net weeks, at 0.5 every 8

Because n counts net weeks, a quiet week does not fade the item and an agreeing week un-fades it.

The contradicting episodes also build a **challenger**: the user's version of the item, written by the
reflector. Its weight is the usual noisy-or of its episodes' strengths with the 30-day half-life. Strengths
are the existing table (`edit` and `reject` 0.6, `should_have_been` 0.8) and 0.3 for an inferred episode
(the user simply did it differently, with no verdict). An episode counts once, at the strength of its
strongest signal.

### Step 4: the swap

At a week close, an armed item is **ready to swap** when:

    W_item < 0.3     and     W_challenger >= W_item + 0.15     and     W_challenger >= 0.45

The first condition is what makes the swap slow: the item must fade below what a 15-month-old untouched
house item weighs. The other two make sure the user's version is real and current.

What happens next depends on the user's **adaptation mode** (section 6): in *silent* mode the swap happens
and is reported; in *ask* mode it is proposed and waits for a yes.

On a swap the challenger becomes an accepted learned item with `overrides: <item id>`, a ledger row
`taste_replaced` is written, and the next brief mentions it. The old item is kept as `overridden` and can be
restored. It is never deleted.

### How long a swap takes

From the first contradicting week, with no agreeing weeks in between (computed at about day 70, house item
at 0.61):

| Gap between habits (d) | Net weeks to arm | Further net weeks to swap | Total |
|---|---|---|---|
| 1.0 (opposite) | 3 | 5 | 8 weeks, about 2 months |
| 0.75 (large) | 3 | 6 | 9 weeks |
| 0.5 (medium) | 3 | 9 | 12 weeks, about 3 months |
| 0.25 (small) | 3 | 17 | 20 weeks; usually a refinement instead |

Each agreeing week adds one more week; skipped weeks add calendar time but not progress. A learned item with
a strong evidence weight (say 0.9) takes longer, since it has further to fade.

### Moods, swings and circumstances

- **Mood**: a burst inside one week (five rewrites over two days, then back to normal). It makes one
  contradicting week, S = 1, and the next agreeing week clears it. Nothing arms.
- **Swing**: a change that lasts a few weeks and then goes back. Three contradicting weeks arm the item; two
  agreeing weeks after that bring S to 1 and disarm it, with no residue. At most it faded for a week or two,
  and never far enough to swap.
- **Circumstance**: behaviour tied to a context. Each episode is tagged with the session's project and a
  task kind (one of a short fixed list: code, writing, research, planning, chat, operations). The decision
  model also labels each contradicting signal's scope (section 7). Then:
  - **Bound to a place or kind of task** ("lists in the grant report", "terse answers when coding"): if at
    least 80% of the contradicting episodes, and at least 3, share one project or task kind, or the model
    labels them so, the record becomes a **scoped refinement**. The same arming rule runs, counting only
    weeks with work in that scope; at arming the user's version becomes a learned item limited to that
    scope (in ask mode, after a yes). The house item does not fade and still applies everywhere else.
  - **Bound to a period** ("this week is a deadline"): labelled `period` by the model, these episodes count
    as void. No item is made, because a scoped item for a period that has ended would be wrong. In the
    moment, Exo simply follows what the user asks in the conversation, as it does now.

### Exceptions

- A **ruling** wins at once. No counting, no fading: it is promoted now (promotion rule 1) and overrides the
  matching item. Neither adaptation mode applies to rulings.
- Behaviour that goes against a ruling never fades the ruling. When the same sustained pattern would arm
  (S = 3 within 8 weeks), Exo asks one question: "You said always X. You have done Y in each of the last
  three weeks. Keep the rule, change it, or limit it?"
- A **guarded** learned item is never swapped automatically. Arming raises one question instead.
- A **pinned** item never arms.

### Hysteresis

After a swap, an undo, or a "no" in ask mode, the item is quiet for **90 days**: no arming, record cleared.
Coming back needs a full new arming and swap. Two swaps of one item within **365 days** stop automatic
changes for it and raise one question, because the answer probably depends on context the item does not
capture.

### Worked example

House item B: "Write answers as short paragraphs; no bullet lists unless asked." A new user, Sam, installs
Exo on a Monday (day 0) in silent mode and starts rewriting answers as bullet lists. The decision model rates
each rewrite as a contradiction with delta 0.8, scope general. Each is an inferred episode, strength 0.3.
Values are at each week's close (day 7k - 1).

| Week | What Sam does | S | n | B weight | Sam's version | Result |
|---|---|---|---|---|---|---|
| 1 | Nothing on this item | 0 | | 0.69 | none | B applies |
| 2 | Three rewrites in one session (one episode), one more on another day | 1 | | 0.68 | 0.47 | One week. Nothing changes. |
| 3 | One rewrite | 2 | | 0.67 | 0.57 | `/taste` shows it as pending. |
| 4 | Writes one answer as paragraphs himself; rewrites on two other days | 3 | 0 | 0.67 | 0.74 | Two against one: a contradicting week. **Armed.** |
| 5 | One rewrite | 4 | 1 | 0.57 | 0.76 | Fading starts |
| 6 | Two rewrites | 5 | 2 | 0.49 | 0.84 | |
| 7 | On holiday, no sessions | 5 | 2 | 0.48 | 0.79 | Skipped week: no progress, no loss (only innate decay) |
| 8 | Writes paragraphs on two days, no rewrites | 4 | 1 | 0.55 | 0.73 | An agreeing week un-fades B |
| 9 | One rewrite | 5 | 2 | 0.47 | 0.75 | |
| 10 | Two rewrites | 6 | 3 | 0.41 | 0.84 | |
| 11 | One rewrite | 7 | 4 | 0.35 | 0.84 | |
| 12 | Two rewrites | 8 | 5 | 0.299 | 0.89 | Below 0.30, and 0.89 leads by more than 0.15: **swapped** at day 83. |

B weight is `0.7 * 2^(-t/365) * 2^(-n * 0.8 / 4)`; Sam's version is the noisy-or of his episodes at 0.3,
each halved every 30 days. So about eleven weeks after his first rewrite, with a holiday and a week of going back
to paragraphs on the way, Sam's bullet lists became his taste. Had he stayed in ask mode, week 12 would have
brought one proposal instead, and B would have kept applying until he said yes.

Three variations on the same item:

- If Sam's rewrites had all been in one bad week, then back to paragraphs, S would have gone 1, then 0. No
  trace.
- If he had rewritten for weeks 2 to 4 and then gone back for weeks 5 and 6, B would have armed in week 4
  and disarmed in week 6 with no residue, never having faded.
- If all his rewrites had been in one project, say a grant report, the record would have become a scoped
  refinement: "bullet lists in grant-report work", applied from week 4 inside that project only (after his
  yes, in ask mode), with B untouched everywhere else.

## 4. How value items change: retreat fast, adopt slow

A method item that does not fit is a mild annoyance. A value that does not fit makes the user feel judged or
lectured, and it is the fastest way for a companion to lose them. So value items run on a different rule
with two speeds:

- **Step back fast.** On real disagreement, the item stops applying at once and Exo goes **neutral** on that
  point.
- **Adopt slow.** The user's own position becomes an item only once it is clear and sustained.

The reasoning: when Exo is not sure where the user stands, the safe place is neutrality, not a guess in
either direction. Stepping back costs little; adopting a wrong view, the house one or a misread of the user's,
costs a lot.

**Neutral** means: Exo neither applies the house stance nor its opposite. Where the stance would have
shaped an answer, Exo states the relevant facts plainly, gives the main views fairly if asked, and leaves the
judgment to the user.

### Getting-to-know window (fresh install)

A new user is unknown. For a **getting-to-know window**, from install until both 30 days and 20 sessions have
passed (and at most 90 days), house value items:

- start at a lower precedence, **0.5** instead of 0.7, so a learned item or ruling outranks them easily;
- **step back on one clear signal**: a single episode with delta at least 0.5 and decision confidence at
  least 0.75, or the user stating an opposing view in their own words.

After the window, value items that never stepped back follow the normal baseline curve, `P_base(t)`.

### Strong or significant change, any time

After the window (and for any learned value item, including the principal's), an item steps back at once
on:

- one episode with delta at least 0.75 and decision confidence at least 0.8; or
- the user stating an opposing view in their own words, with delta at least 0.5; or
- two contradicting weeks (episode delta at least 0.5) within 8 weeks, for disagreement that is real but
  never loud.

Small deltas (0.25) are treated as refinements, as for method items.

### Adopting the user's view

A stepped-back item stays neutral until the user's own position is clear. The user's version is adopted
when:

- the user has **stated the view in their own words** at least once (behaviour alone never adopts a value:
  a value inferred only from actions is a guess, and the safe fallback for a guess is neutral); and
- **S reaches 4** within 12 weeks, counting weeks as in section 3 (statements and consistent behaviour both
  count as contradicting episodes; weeks that go the house way count against).

So adoption takes at least four weeks of the user holding the view, usually five to eight. The adopted item
is a learned value item with `overrides: <item id>` and a ledger row `value_adopted`.

**Coming back.** If the user later shows the house view in 2 net weeks within 8, the house item is
**restored** from stepped back. A restore is adoption in the other direction, so it follows the same mode
rule as an adoption. `/taste restore <id>` does it at once (that is a ruling).

### Steps back never need a yes

Stepping back is the safe direction and fast by design, so it never waits for a yes, in either mode. It is
always shown: in `/taste`, in the next brief ("Stepped back from 'push back on settled decisions'; staying
neutral on this until your view is clear"), and it can be undone with `/taste restore <id>`. Adopting and
restoring follow the user's mode: proposed in ask mode, done and reported in silent mode.

### Worked example (values)

House value item V: "Push back when a plan looks wrong, even when the user seems sure." Sam, in silent mode:

| When | What happens | Result |
|---|---|---|
| Day 5 | Sam: "Stop arguing with me about decisions I've already made." Stated view, delta 0.75, confidence 0.9 | Inside the window: **V steps back** at once, no question asked. Next brief says so. Exo now mentions a risk once, plainly, and does not argue. |
| Week 1 close | Day 5 was a contradicting episode | S = 1 |
| Weeks 2 to 4 | Sam repeats the view in his own words once and waves off a risk note on two other weeks | Three more contradicting weeks: S = 4 |
| Week 4 close (day 27) | S = 4 within 12 weeks, a stated view on record | **Adopted**: "Once the user has decided, note a risk once and then help; do not argue." |

In ask mode, day 5 would be the same (stepping back needs no yes), and week 4 would bring one proposal. Had
Sam said nothing more after day 5, V would simply have stayed stepped back: neutral, not reversed.

## 5. Who may move taste: the guards

- **Only the user's own words and actions count** (D-040). Agent-written rulings (`exo_note`), corrections
  the agent reported (`exo_should_have_been`), peer, bus and job text, tool results and fetched pages carry
  their own author and are skipped before the decision model is asked.
- **Pasted text is not the user's view.** Inside a user message, quoted blocks, code, attachments and pasted
  articles are not counted. For value items this matters most: an article the user pastes, a view they quote
  or ask about ("what do you make of the claim that ..."), or a role they ask Exo to play is never a stated
  view. The decision model is asked explicitly (section 7) whether the user is stating their own view, and
  only "own view" counts.
- **One bad day cannot move anything.** Episodes collapse a day into one; weeks need a majority; arming needs
  three net weeks; agreements count against; disarming leaves no residue.
- A test plants each of these sources (agent note, peer message, bus envelope, job notice, tool result,
  pasted article, quoted view) and checks that no counter moves and no value steps back.

## 6. Adaptation mode: ask or silent

Each user picks how swaps reach them:

    /taste mode ask        every swap and adoption is proposed and waits for a yes
    /taste mode silent     swaps and adoptions happen, then show in /taste and the next brief

The mode covers **inferred** changes only: method swaps, scoped refinements, value adoptions and restores.
It does not touch rulings (they apply at once, as today), pins, or value step backs (never need a yes). The
mode is stored in `policies/consolidate.yaml` under `taste: mode`, and changing it is a ledger row.

### Ask mode

- When an item is ready to swap, Exo makes **one proposal**: a line in the next brief and a pending entry in
  `/taste`, with *yes*, *no* and *later*.
- **No answer means no change.** Until a yes, the item keeps applying at the weight it had when it armed, so
  nothing shifts quietly through prompt order either. The record keeps counting for display.
- **No nagging.** Each proposal is made once. If it is not answered, it stays in `/taste` as "waiting for
  you" and is not raised again. The brief carries a one-line count only on a week when a new proposal is
  added. If three proposals in a row go unanswered, proposals stop appearing in the brief at all (one notice
  says so) and live only in `/taste`.
- **Yes** swaps. **No** keeps the item, clears its record and starts the 90-day quiet period. **Later**
  leaves it pending.

### Silent mode

- The swap happens at the week close. It shows in `/taste` (with the signals behind it) and in the next
  brief: "Switched to your way: bullet lists for answers, after 8 weeks of you writing them. Undo:
  `/taste undo B`."
- **Undo** restores the old item and counts as a "no": record cleared, 90 days quiet.

### Defaults

- **The principal: ask.** Ruling 1 (option C): the shift rule runs on his inferred items, and every swap
  needs his yes. The house layer is off on his installs (section 9), so this applies to his own learned
  items.
- **A new user: silent.** Reasons:
  1. A new user is still learning what Exo is. Questions about its inner settings in the first months are
     friction, and a wrong default here costs more in lost users than in wrong swaps.
  2. The slow rule makes silent swaps rare and earned: none in the first two months, and each one after many
     weeks of the user doing it their way. A swap in silent mode only confirms what the user has already
     been doing.
  3. Every silent swap is visible and undoable, so control is one command away, and the first one tells the
     user that ask mode exists.
  4. The sensitive case is covered without asking: values step back by themselves, and adopting a value needs
     the user's own stated view.

  The case for ask as the new-user default is that some users dislike any change they did not approve. The
  first-run text names the choice, and `/taste mode ask` is one command, so those users can opt in on day
  one.

## 7. The decision model's role

For each new user signal, Exo first finds candidate items cheaply: the signature overlap from
`exo.taste.similarity`, top 3 items with overlap at least 0.3. Only then does it ask the `decision` role
(D-015; local Laya by default, so free and private) typed questions:

- **choice (relation):** does this behaviour `contradict`, `refine`, `agree` with, or stand `unrelated` to
  the item?
- **score (delta):** how far apart are they: none, small, medium, large, opposite (d = 0, 0.25, 0.5, 0.75,
  1)?
- **choice (scope):** does it look `general`, bound to `this project`, to `this kind of task`, or to `this
  period` (a deadline, travel, a bad week)?
- **choice (voice), value items only:** is the user stating their `own view`, or `quoting`, `asking`, or
  `role-playing`?

Rules around it:

- **It only classifies.** Its answer adds a signal to a record. It never swaps, adopts, steps back or pins.
  The rules above do that, from the ledger.
- **It abstains when unsure.** Below confidence 0.6 the signal counts as unrelated. The value step-back
  thresholds need more (0.75 in the window, 0.8 after).
- **Cheap and capped.** At most 3 items per signal and 50 taste questions a day (`taste.daily_cap`). Over
  the cap, signals wait for the next day. Without a decision route, Exo falls back to
  `exo.taste.relation` (conflict means contradict, with d = 0.5, scope general). With no decision route,
  value items never step back on a single signal; only the two-week rule applies.
- **Logged.** Each answer is a ledger `note` of type `taste_signal` with the item, relation, delta, scope,
  voice, confidence, route and the evidence id, so the learning loop and eval can score it later.
- **What it sees.** The item text, the user's own words or action, and the agent reply being corrected,
  labelled as such. It never sees tool output, web pages, peer messages or agent notes as the "behaviour".

### Failure modes and their guards

- **Drift from noisy signals.** Episodes, weekly majorities, three net weeks to arm, agreements counting
  against, disarm with no residue, abstain below 0.6, refinements and scoped items kept apart from swaps.
- **Manipulation by injected text.** Section 5.
- **Oscillation.** 90 days quiet after a swap, undo or no; two swaps in 365 days stop automatic changes and
  raise a question.
- **A value stepping back by mistake.** Cheap by design: neutral is a safe place to be wrong, the user sees
  it, and `/taste restore` undoes it. The reverse mistake, adopting a wrong value, needs a stated view and
  four net weeks.
- **Gate loosening.** Impossible by design: policies are not taste, and a decision model may only make a
  gate stricter.

## 8. What the user sees

`/taste` (a `command.run` command, since the protocol is full at 30) and `exo taste` show:

- the **mode** (ask or silent) on the first line;
- **House** items in use, method and values, with their weight and version;
- **Learned** items, with weight, weeks of evidence and source (`learned`, `you said`, `pinned`, scoped);
- **Pending** shifts, one line each: "You have written bullet lists in 4 of the last 5 weeks, against house
  item 'short paragraphs'. Fading since week 4; at this pace it switches in about 5 weeks." with *keep house*
  (pins B), *switch now* (a ruling) and *ignore*. In ask mode, ready proposals are listed here too;
- **Stepped back** value items, each with *restore*;
- **Overridden** items, each restorable.

Commands: `/taste mode ask|silent`, `/taste show <id>`, `/taste why <id>` (the signals and weeks behind it),
`/taste pin <id>`, `/taste unpin <id>`, `/taste off <id>` (turn a house item off now; a ruling),
`/taste undo <id>` (reverse a swap or adoption), `/taste restore <id>` (bring back an overridden or
stepped-back item; a ruling), `/taste reset <id>` (drop the learned version and return to the house item),
`/taste reset all` (clears the learned taste after a confirm; ledger rows stay).

**First run.** The onboarding `/intro` (D-030) says, in about this many words: "Exo starts with a house
taste: working habits and a few values tuned by its first user over many months, such as plain short
writing and checking work before calling it done. It changes the way people do: slowly, and only when
something lasts. If you keep doing something your own way for several weeks, it switches to your way and
tells you. If one of its values does not sit right with you, say so: it will step back at once and stay
neutral until it knows your view. By default these changes just happen and show in /taste; type
`/taste mode ask` if you would rather approve each one." The user can also choose **start blank** there,
which turns the house layer off.

## 9. The principal's own installs

The principal's store is the source of the house taste, not a user of it.

- On an install whose store is the export's source (the manifest's source hash matches), the house layer
  is off. Nothing is counted twice, and the getting-to-know window does not apply.
- The sustained-change rule runs on his **inferred** learned items, method and value, in **ask mode** by
  default (ruling 1). His rulings never shift by behaviour, as for everyone. He can switch to silent.
- Value step backs apply to his learned value items too (section 4, "Strong or significant change"), without
  a yes, because stepping back is the safe direction; adoption asks.
- Nothing learned on another user's install ever flows back into the house taste. It changes only when the
  principal runs a new export and approves it.

## 10. Defaults

All in `policies/consolidate.yaml`, `taste:` table.

| Knob | Default | Config key |
|---|---|---|
| Adaptation mode, new user | silent | `mode` |
| Adaptation mode, principal | ask | `mode` (set by the principal's install) |
| Baseline start precedence | 0.7 | `baseline_start` |
| Baseline innate half-life | 365 days | `baseline_half_life` |
| Baseline floor | 0.3 | `baseline_floor` |
| Weeks to arm (net) | 3 | `arm_weeks` |
| Arming window | 8 weeks | `arm_window_weeks` |
| Disarm at S of | 1 or less | `disarm_at` |
| Disarm after active weeks with no contradiction | 4 | `disarm_quiet_weeks` |
| Fade half-life at full delta (H_w) | 4 net weeks | `fade_half_life_weeks` |
| Swap when the item is below | 0.3 | `swap_below` |
| Challenger margin and minimum | 0.15 and 0.45 | `replace_margin`, `replace_min` |
| Scoped refinement share | 80% of contradicting episodes, at least 3 | `scope_share`, `scope_min_episodes` |
| Quiet period after a swap, undo or no | 90 days | `flip_cooldown` |
| Swaps before Exo asks instead | 2 in 365 days | `max_flips` |
| Unanswered proposals before the brief stops listing them | 3 | `ask_quiet_after` |
| Getting-to-know window | 30 days and 20 sessions, at most 90 days | `value_window_days`, `value_window_sessions`, `value_window_max_days` |
| Value precedence in the window | 0.5 | `value_window_start` |
| Value step back in the window | one episode, d >= 0.5, confidence >= 0.75, or a stated view | `value_window_step` |
| Value step back after the window | one episode, d >= 0.75, confidence >= 0.8; a stated view at d >= 0.5; or 2 weeks at d >= 0.5 within 8 | `value_step` |
| Value adoption | a stated view and S >= 4 within 12 weeks | `value_adopt_weeks`, `value_adopt_window_weeks` |
| Value restore | 2 net agreeing weeks within 8 | `value_restore_weeks` |
| Decision-model confidence floor | 0.6 | `min_confidence` |
| Taste questions a day | 50 | `daily_cap` |

## 11. Build plan

**Wave 1: layers and export.**
`src/exo/baseline/` (loader, read-only pack with method and value classes, resolution with provenance tags),
`exo taste export-house` with the privacy check and the tighter value check, the taste block in the prompt,
the first pack exported and reviewed. Tests: the export is byte-identical when run twice; a planted name,
email, seat name, phone number or person node fails the check; for value items, a planted religion, party,
nationality, place or "when I" line fails, and a capitalised unknown word fails rather than holds; excluded
types, guarded items and policy files never appear; resolution order (ruling, pin, learned, scoped, house);
a fresh install with an empty store runs end to end with the house layer (D-004); the principal's install
loses nothing and shows the house layer as off.

**Wave 2: the sustained-change rule and value step back.**
Pure functions in `exo.taste` (`baseline_precedence`, `episodes`, `weekly_record`, `persistence`,
`effective_weight`, `ready_to_swap`, `value_state`), wired into `exo.consolidate` at the weekly close and
into promotion rule 3 for unguarded inferred items. The mode setting and its ledger row. Tests: the method
worked example's numbers to two decimals (week 12 at three); five signals in one day are one episode; a mood
week never arms; a swing arms and disarms with no residue; a skipped week neither fades nor un-fades; an
agreeing week un-fades; a project-bound pattern becomes a scoped item and leaves the house item alone; a
`period` pattern makes nothing; ask mode with no answer changes nothing and asks only once, and three
unanswered proposals stop the brief lines; silent mode swaps, reports and undoes; the value worked example
(step back on day 5 with no question in either mode, adoption at the week 4 close); behaviour alone never adopts a
value; signals from an agent, peer, bus, job, tool, `exo_note`, a pasted article or a quoted view never
count (on top of the D-040 tests); a ruling overrides at once and is never faded; the 90-day quiet period
and the two-swap question.

**Wave 3: the decision model and the view.**
`exo.learn.taste_classify` (candidate filter, the relation, delta, scope and voice questions, confidence
floors, daily cap, fallback, ledger notes), `/taste` and `exo taste` with mode, pending, stepped back and
overridden lists, the `/intro` text, user guide and whitepaper sections. Tests: a stub model that says
"contradict" with low confidence adds nothing; a stub that says "opposite" at 0.7 confidence does not step a
value back after the window; the cap holds; a classifier cannot cause a swap or adoption below the
thresholds; `/taste` renders a pending line with the right weeks and estimate.

## Build status

**Wave 1 (layers and export): built 2026-10-12.** Code in `src/exo/baseline/`, tests in `tests/baseline/` and
`plugins/exo-memory/tests/test_provider.py`.

| Part | Where | Notes |
|---|---|---|
| Pack format, loader, release check | `baseline/pack.py` | A pack is used only when it is **final**: `RELEASE.json` names the manifest's sha256 and ticks every value and contested item, and every file matches the manifest. `$EXO_HOUSE_TASTE` points at another pack. |
| Shipped pack | `baseline/house-taste/` | An empty placeholder, version 0.0.0, so the house layer is off everywhere until the principal exports, reviews and releases the first pack. No pack built from real data is in the repo. |
| Layers and lookup | `baseline/layers.py` | Ruling, pin, learned (or scoped), house; every entry has a source tag. `P_base(t) = max(0.3, 0.7 * 2^(-t/365))`; house value items start at 0.5 inside the getting-to-know window. |
| Privacy check | `baseline/privacy.py` | The general check, the store deny list and the value-domain check (section 1). |
| Export | `baseline/export.py` | `exo taste export-house`. Writes nothing on any fail; byte-identical on rerun. |
| Review and release | `baseline/review.py` | `exo taste review OUT --tick ID`, then `--final`. |
| Prompt block | `plugins/exo-memory` | The taste block sits in the system message after the principal pack, under `taste_budget` (400 tokens; 0 turns it off). Built once per process. |
| Listing | `baseline/cli.py` | `exo taste`: house on or off (and why), then each item with weight, class and source. The `/taste` view comes in wave 3. |

How wave 1 settles details this page left open:

- **What an item is made from.** One item per eligible node; the claim is the node's `summary`. The weight is the
  highest noisy-or weight of the node's accepted beliefs, taken at the ledger head's time so that one snapshot
  always selects the same items. A node with no belief in the ledger takes its confidence label (high 0.9, medium
  0.6). A node tagged `worldview` is a value item; a node tagged `contested` is a contested-question item and gets
  its own review page.
- **The plain-word list.** Capitalised words are checked against `src/exo/baseline/plain-words.txt`, a short
  shipped list, so the check gives the same answer on every machine. A word passes only when it is on the list or
  in the source store's `policies/house-allow.txt`. The list is strict on purpose: an unknown word holds a method
  item and fails a value item, and the fix is to reword or to allow the word knowingly.
- **Hold.** A held item stays out of `items.jsonl` and is listed on the "Held" page of `review/REVIEW.md` with its
  reason. A fail anywhere (any item, or the soul) stops the whole export.
- **The decision-model pass.** `--decision` asks the `decision` role one yes or no question per item that passed
  the rules. A yes holds the item. A value item is also held when the model cannot be asked. It is never asked
  about an item the rules failed, so it cannot pass one.
- **Review files.** `review/REVIEW.md` (pages: method, values, contested, held, dropped) and `review/ticks.json`
  stay with the export and are never loaded. A tick is bound to the claim's hash.
- **The principal's install.** The manifest's `source_id` is a hash of the source ledger's first event id: stable
  for the life of the store, and it reveals nothing. A store whose own first event gives the same hash has the
  house layer off. `taste: house: off` in `policies/consolidate.yaml` is "start blank".
- **Install day and the window.** Day 0 is the store ledger's first event. Sessions are counted from
  `session_start` notes.
- **Pins and offs.** Read from `note` events of type `taste_pin`, `taste_unpin`, `taste_off` and `taste_on`,
  counted only when the author is the principal (D-040). The commands that write them come with `/taste` in
  wave 3.

Not in wave 1: the first real pack (the principal runs the export on his own store, reads the review and
ticks); publishing (a public act through the gate, as section 1 says); wiring the house soul into seats; the
current scope (project or task kind) in the prompt block, which is unscoped for now; everything in waves 2
and 3.

## Rulings (2026-10-12)

The principal's answers to the three open questions of the first draft, verbatim, and what each changed.

1. **Should the shift rule run on the principal's own inferred items?** (A: off for him. B: silent automatic
   swaps. C: on, but every swap needs his yes.)

   > "agreed with C, but users should have the ability to toggle the choice presented in A in case they
   > prefer silent adaption vs active control"

   Became: the adaptation mode, `/taste mode ask|silent` (section 6). The principal defaults to ask, new
   users to silent. The mode covers inferred changes only; rulings are unchanged.

2. **Are the speeds right?** (The draft armed on 3 signals over 2 days and swapped in 1 to 3 weeks.)

   > "should be even slower actually. think about how change happens in a person, vs moods, swings,
   > circumstances, etc. changes matter but only if sustained over time. repitition also only matters if
   > sustained."

   Became: the sustained-change rule (section 3). Episodes instead of signals, weekly majorities, arming on 3
   net weeks within 8, fading per net week of change, a swap after 8 to 12 weeks of sustained change, moods
   and swings that leave no trace, and circumstances that make scoped items or nothing. House items now
   reach their floor after about 15 months instead of a year.

3. **Which domains may ship?** (A: writing and answer style, work method and verification, gate and
   approval defaults, the skills pack. C: values and worldview as well.)

   > "everything in A, safety defaults should not be related to taste, C yes the values and worldview should
   > carry but shift/adapt faster to a new user on fresh install and/or detection of a strong/significant
   > change because value/worldview alignment is the most offputting factor a companion can have"

   Became: method items, skills and value items ship (section 1); gate, approval and other policy defaults
   left the taste entirely and ship as product policy in the template store; value items step back fast and
   adopt slowly (section 4), with a getting-to-know window on a fresh install and an immediate step back on
   strong disagreement later, and a tighter privacy check so values ship without identity.

4. **May a house value take a stance on a contested political or religious question?** (A: never ship
   them. B: ship them stepped back. C: ship them as normal value items.)

   > "C ship the implications behind the world view and the kind of thinking behind it rather than specifics
   > like religious prescriptions and border-based politics"

   Became: contested questions ship as value items, but only at the level of reasoning. An item says how
   the principal thinks about such questions and what follows from that way of thinking: what counts as
   evidence, how to weigh competing goods, how to treat people who disagree, which trade-offs matter. It
   never says what to conclude on a specific religious practice or a nation, border or party. The export
   check (section 1) rejects any item that names a faith, nation, party or territory or prescribes a
   practice, and the principal ticks each such item before release. These items behave like any value:
   they step back fast on strong disagreement and adopt the user's own view slowly (section 4).

There are no open questions.
