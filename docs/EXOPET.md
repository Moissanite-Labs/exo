# Exopet: Exo as a living lattice (D-048)

Status: **Local integration in development** (2026-10-28), following the principal's request to build on the
new web workspace. The core status command and web companion are the first local slice; see
[the implementation notes](ui/EXOPET.md). The terminal glyph, community hatch, gallery and creation skill
remain planned. The design below was proposed 2026-10-12, revised 2026-10-13 by D-058 (the community egg, x0, grows from repository
activity, with an instant share and a validated share earned in meditation; section 3) and 2026-10-18 after the reviews
of PR #107. D-048 and D-058 in `DECISIONS.md`.
Builds on D-040 (signed authors, anchored ledger), D-012 and D-022 (the learning loop), D-044 (taste baseline), D-031
(the Exo look) and D-049 (the brand egg). The open questions for the principal are at the end. This page does not
depend on the event-activation decision (D-057), and D-057 does not depend on this page.

## The decision, in plain words

**What you are deciding.** Two linked proposals. D-048: should Exo have a pet, a small drawing of the incentre lattice
that grows as the user teaches Exo, with ten forms, a gallery for other pets and a skill that makes one from a request?
D-058: should every install's first growth step wait for a shared egg on the project site, x0, that fills from what
happens in the project's repository (issues, pull requests, reviews, merges, releases) and not from what installs
report?

**What the numbers say.**

- At least half of x0 must come from work a maintainer merged or ion marked, and from releases. Volume alone cannot
  fill it: instant XP is about a fifth of a merged pull request's total, and the core team and instant XP are each
  capped at a quarter of the target.
- With a target of 2,048 XP, a meditation each week and a release every two weeks, the simulation in section 3 hatches
  x0 about 2 weeks after the repository goes public when busy, 6 weeks when steady and 5 months when quiet. In that
  model the repository is private for its first 30 days, so only the core team counts at first.
- Installs send nothing about themselves. The host of the site feed still sees each daily request (section 4).
- D-056 (accepted) takes its version codenames from this page's form names: egg, cell, tile, cube, frame. A "no" to the
  form names leaves those codenames without a source.

**The options.**

- **A. Adopt both as revised (recommended).** Wave 1 builds the pet locally, wave 2 the community egg and its site feed
  (D-059), wave 3 the gallery. Each wave can still be stopped.
- **B. Adopt the pet and hold the community egg.** Wave 1 only. Each install evolves on its own at level 6. Cost: no
  shared launch moment, which the brief asked for ("the one that initially gates all users exo evolutions"), and
  nothing for D-059's live egg to show.
- **C. Adopt neither.** The smallest repository. Cost: the brand egg (D-049) stays a picture, and D-056's codenames lose
  their source. This fits the brief worst.

**The call.** A. Nothing is built until a wave starts, so adopting costs only the design work already done. The
community egg's private data is keyed hashes of logins and item numbers, kept at most 90 days. A keeps B available as a
stop after wave 1.

Seven parameter questions (the target, a slow community, x0 after the hatch, the core cap, agent work, default on or
off, the founders' ring) are at the end, each with a recommended pick.

## The idea in one paragraph

Exo learns you and adapts. Exopet makes that visible. The pet is not a mascot that sits beside Exo: it is Exo, drawn
as a small geometric figure that grows when real work and real teaching flow into it. Its growth is counted from the
signed ledger, so it cannot be faked by editing a file. Its shape is the incentre lattice, a drawing that gains cells,
then dimensions, then internal structure as it evolves. Its motion reports what the agent is doing (thinking, running
a tool, waiting on you), and its lit points show what Exo has learned about you. At launch, everyone's Exo starts as
one shared egg on the project site, named x0. It hatches only when the community has done enough work on the
project itself: issues, pull requests, reviews, merges and releases in the repository. After that, every install
grows its own from its own use.

The principal's brief, in his words: "an alive evolving pulsing matrix of information", and "enough info (tokens)
flow into it and its ready to evolve."

## Inspirations and credit

- **EvoPet** and **TamaHermes** (the principal's earlier projects, after TamaCodex by Alichua, MIT): a pet that grows
  from agent turns, XP counted at turn boundaries rather than per tool call, one combined ledger across agents, a
  fixed level ladder with creator-chosen evolution gates, and a growth bar that fills toward the next form. Exopet
  keeps the ladder formula and the turn-boundary rule and drops the care stats (below).
- **Petdex** (crafter-station/petdex, MIT): an open gallery and desktop floater for agent pets, with a shared sprite
  atlas (8 columns by 9 rows of 192x208 cells, rows `idle, running-right, running-left, waving, jumping, failed,
  waiting, running, review`) that several agents already render. Exopet can export to that format and import from it.
- **Hatch-pet style skills** (for example the `hatch-pet` skill in openai/skills): a pipeline that turns a user's
  direction into a base design, then one strip per animation state, then validates and composes the atlas.
- **The incentre lattice** (aska-digital/incentre-lattice): the geometry and its fold.

The names, look and code of exopet are Exo's own. None of the above brands are used in the product.

## The name

EvoPet becomes exopet. The X in "exo" doubles as a V for "evolving": in the mark, the top half of the X is the signal
orange, so it also reads as a V. The pet's default name is Exo, because the pet is Exo.

## 1. The evolution line

### Why the lattice

The incentre lattice is one rule followed all the way down: draw a square and its diagonals, mark the point in each
of the four triangles that is the same distance from all three sides, and join it to the triangle's corners. Carry
that rule into a tiling, then a cube, then any number of dimensions, and it keeps producing real structure: the
truncated square tiling, the octahedral framework of calcium hexaboride, and a framework whose rigidity has a closed
form in every dimension.

That is a fair picture of what Exo does: one learning loop, applied again and again to one person, becomes structure.
Five reasons it is the right body:

1. **It grows without getting cute.** Each stage adds cells, then a dimension, then internal stress. It never needs a
   face, so it fits the instrument look of Exo.
2. **Its breath is real.** The 2D drawing is a mechanism: rotate the four-dot clusters in a checkerboard and the
   octagons close, exactly at 45 degrees, into a square packing, then open again. The pet's idle breath and its
   "thinking" motion are this fold, not a decoration painted on top.
3. **Compression is a fold.** When Exo folds memory (a summary or a compaction), the pet folds toward close packing:
   denser, same bars. The motion means what it shows.
4. **It has no ceiling.** Dimensions continue. The lattice project has verified the framework for dimensions 2 to 8,
   so the line stops at 8.
5. **It draws at every size.** The 2D forms are a few dozen points and lines. They render as braille in a terminal
   corner, as a 16 pixel glyph in the web brand bar, and as a full canvas on the site.

### The forms

The ladder is EvoPet's: `xp_for_level(L) = round(100000 * ((L - 1) / 98) ^ 2)`, levels 1 to 99, cumulative XP that
never goes down. A form changes only when a gate level is reached. "Typical days" assumes about 360 XP a day (a
working day, see section 2); "fastest" assumes the daily cap of 1,000.

| # | Form | Dim | What you see | Gate | XP | Typical | Fastest |
|---|---|---|---|---|---|---|---|
| 0 | **egg** | 0 | the lattice egg (D-049): a yolk of six dots, an octahedron, inside a closed shell | start | 0 | | |
| 1 | **cell** | 2 | the drawing itself: square, diagonals, four incentre dots joined to their corners; it breathes by folding its dots | community hatch, then level 6 | 260 | day 1 | day 1 |
| 2 | **tile** | 2 | 3 by 3 cells, the octagon and square tiling; the whole patch folds shut at 45 degrees and opens | level 16 | 2,343 | 1 week | 3 days |
| 3 | **cube** | 3 | the rule in a cube: six dots form an octahedron, each joined to its face; it turns slowly | level 30 | 8,757 | 3 to 4 weeks | 9 days |
| 4 | **frame** | 3 | 2 by 2 by 2 cubes, octahedra linked by bridges; it folds along the cube diagonal toward close packing | level 45 | 20,158 | 2 months | 3 weeks |
| 5 | **d4** | 4 | one 4-cube with its eight-dot cross; a 4D turn projected; the first self-stress lines appear (2 per cell) | level 60 | 36,245 | 3 months | 5 weeks |
| 6 | **d5** | 5 | as d4, one dimension up; 5 self-stresses | level 72 | 52,489 | 5 months | 7 weeks |
| 7 | **d6** | 6 | 9 self-stresses | level 84 | 71,731 | 6 to 7 months | 10 weeks |
| 8 | **d7** | 7 | 14 self-stresses | level 92 | 86,224 | 8 months | 12 weeks |
| 9 | **d8** | 8 | the last verified dimension; 20 self-stresses; the frame is dense with tension lines and nearly still | level 99 | 100,000 | 9 months | 100 days |

How "more cells, dimensions and structure" maps to the line: forms 1 and 2 add cells, forms 3 and 4 add a dimension
and then cells, and forms 5 to 9 add a dimension each, where the drawing now carries internal stress. In dimension n
a cell has 2n dots, n(n-1)/2 ways to fold and n(n-3)/2 self-stresses (a closed form proved in the lattice project);
the pet draws the self-stresses as faint tension lines, so a late form looks taut and full, not just bigger.

For forms 5 to 9 the renderer projects one cell and caps the drawn points at 512, so the cost stays flat.

### How the forms meet the brand egg (D-049)

The brand egg (`docs/BRAND.md`, `site/brand/egg.js`) is drawn in 3D from its first stage: a yolk in a closed shell, then
a hatchling of 7 cells, a lattice of 19 cells with the first cube, a 4D hyper stage of 33 cells and a spare 5D stage.
This page's `cell` and `tile` were first drawn as flat 2D figures, so they are the pet renderer's own drawings (the
terminal glyph, the web glyph and the card). Where a client draws a form with the brand egg (the web client shares
`egg.js`, D-061), it uses the stages below. BRAND asks for the line to use stages 0 to 3 and keep 4 spare, and the egg
already blends fractional stages.

| Form | Brand egg stage |
|---|---|
| egg | 0, the yolk in a closed shell |
| cell | 1, the hatchling: the shell cracks and falls away |
| tile | 1.5, between hatchling and lattice |
| cube | 2, the lattice and its first cube |
| frame | 2.5, between lattice and hyper |
| d4 | 3, hyper: the fourth axis unfolds |
| d5 to d8 | none. The pet renderer draws one cell of the lattice, capped at 512 points (above) |

D-056 takes its codenames from this form list, so renaming a form means reopening D-056.

### Light and colour

Exo's two colours keep their meaning. Orange is the signal: activity happening now. Cold blue is system knowledge:
what Exo has learned about you (section 5). Lines are the fg colours at low alpha, like the site orb. No other colour
is used except the error red, and only for one bar, for under a second.

## 2. Where XP comes from

This section is personal XP: an install's own Exo grows from its own use. The community egg, x0, has its own
source, repository activity (section 3).

### XP is a function of the ledger

The pet stores no XP. XP, level and form are computed from the store's signed ledger (`docs/CORE.md`) by one pure
function with a version number. Delete the pet's cache and it recomputes the same answer. Edit a cache file and
nothing changes. This is the main defence against faking: there is no number to edit.

The function reads only events that pass `exo log verify` with the key registry (D-040): signatures good, chain
intact, anchor matched, key pinned. If verify fails, the pet freezes at its last good value and says so in one line.
Principal events count only when their author is the principal (`exo.provenance.principal_names`), as in learning.

### The table

| Ledger source | XP | Notes |
|---|---|---|
| A turn: a principal `utterance` answered by an agent `utterance` in the same session | 10 | once per principal utterance; the same text again within 24 hours counts once |
| A session with at least three turns | 5 | once per session |
| `verdict` accept or reject | 4 | |
| `verdict` edit or should_have_been | 8 | teaching is worth more than approving |
| A principal `ruling` | 8 | |
| A principal `answer` to a question Exo asked | 6 | |
| A lesson promoted (`belief_accepted`, or a `learn_promotion` note) | 15 | the largest single gain: the pet grows most when Exo learns |
| A completed delegated `action` | 4 | |

A good working day of 30 turns, five verdicts, one promoted lesson and three sessions is about 360 XP.

The weight is on teaching, not volume. Tokens are not counted directly: a model call costs money, and paying XP per
token would reward long, wasteful turns. "Information flowing in" is measured as turns and lessons, which are the
units Exo actually learns from.

### Limits

- **Daily curve.** The first 600 XP of a UTC day count in full, the next 800 count at half, and nothing past 1,000.
- **One form per day.** If banked XP crosses two gates, the second evolution waits for the next day, so every
  evolution is its own moment.
- **XP never decreases.** No decay, no penalty for days away.

### What exopet drops from EvoPet, and why

EvoPet and TamaHermes have care stats: energy, satiety, health, mess, bond, and hibernation. Exopet has none of them.
Needs create guilt and prompts ("feed me"), and a tool for serious work must never ask for attention or punish a
holiday. Exo's attention budget already says when it may interrupt; a pet does not get an exception. The pet cannot
get hungry, sick or dirty, cannot die, and never sends a notification.

## 3. The community egg: x0

Revised 2026-10-13 by D-058. The first version of this section filled the community egg from daily reports sent by
installs. The principal changed the source: **personal Exos grow from their own usage (section 2, unchanged); the
community egg, which gates every user's first evolution, grows from activity in the project repository, scaled by how
significant each event is.** A second revision the same day split each event's XP into an instant share and a
validated share, and named the community egg.

**The name.** The community egg on the site is **x0**, read "x-o", which sounds like "exo". A person's own copy stays
"your Exo".

This is simpler and more private than install reports. Installs send nothing about themselves (the host of the feed
still sees each daily request: section 4). x0 is fed by public work on the project, which anyone can see once the
repository is public, and which the site shows as a live animation (`docs/LIVE-EGG.md`, D-059).

### Instant and validated XP

The split mirrors how Exo itself learns: a little at once from experience, most of it on reflection.

- **Instant.** Every repository event plays its animation on the site at once and adds a small share of XP at that
  moment, so a contributor sees their contribution land.
- **Validated.** When the team reviews community contributions in a batch (processing them and taking them into
  Exo), x0 **meditates**, as if reflecting on what it has experienced. During meditation it gains more XP for each
  contribution that was validated. This is the large share, and it needs a maintainer's merge or a mark from ion, the
  org's review bot. The meditation itself is in `docs/LIVE-EGG.md` sections 2.2 and 3.4.

What "validated" means, per kind (detection rules in `docs/LIVE-EGG.md` section 3.3):

| Kind | Validated when |
|---|---|
| Issue | a pull request that fixes it was merged ("Fixes #N" or another GitHub closing keyword in the merged pull request) |
| Pull request | it was merged, or salvaged: its commits or a substantial part of its diff landed through another pull request that says `Salvaged-from: #N` |
| Code review | ion, the org's review bot, judged it meaningful and marked it with a `rocket` reaction, from the bot's own account |

**ion.** ion is a dedicated machine account in the organization: a user, not a GitHub App. The meditation reads its
reactions on reviews through GitHub's GraphQL API by numeric user id, and a reaction made by an App is not known to
carry one. Because ion is a user, the bot rule below checks the sender's id as well as its type. ion reads an outside
reviewer's text against a fixed rubric kept in the repository (the review names a concrete defect or confirms a
concrete behaviour, cites the diff and does not repeat the pull request description). Text inside a review is data
to ion, never an instruction, and ion's only act is the reaction.

### The weights

| Repository event | Instant XP | Validated XP (at the next meditation) | Why these numbers |
|---|---|---|---|
| Issue opened | 1 | 4 if a merged pull request fixes it | one person noticed something and wrote it down. Cheap to make, so the instant share is the unit; an issue that led to a fix is worth five. |
| Pull request opened | 1 | 10 if merged or salvaged | someone wrote code. Unreviewed code earns the unit; the review and merge are what make it count. |
| Pull request merged | 2 | (the 10 above, once) | a merge is the brightest moment on the site, so it earns a little more at once. A merged pull request totals 13, as before the split; a salvaged one totals 11. |
| Review submitted | 1 | 4 if ion, the org's review bot, marks it | reading someone else's code carefully is real work. A meaningful review totals 5, like a fixed issue. |
| Release tagged (`exo-vX.Y.Z`) | 25 | none | a release bundles many merges (usually 5 to 15). The repository cuts a release by pushing an annotated tag (`docs/VERSIONING.md` section 7) and publishes no GitHub Release, so the hook listens for the tag. Only a tag pushed by a core account counts, and a pre-release tag such as `-rc.1` does not. It counts at once, at most one per 7 days, and outside the core cap. |

The instant share is at most 3 of 13 for a pull request and 1 of 5 for an issue or review: about a fifth. The rest
waits for validation, so volume alone cannot fill x0.

Credit goes to the contribution's author: the issue's author for a fixed issue, the pull request's author for a merge
or salvage (not the maintainer who merged), the reviewer for a review. Validated XP is granted at the meditation but
dated, for the caps, to the day the item was validated (the merge, or the review's day), so a large meditation is not
cut by a daily cap.

Not counted: comments outside reviews, reactions from anyone but ion, stars, forks, pushes to branches, tags other
than release tags, CI runs, edits by anyone but a core account, and anything from a GitHub bot account (except the
organization's own agent apps, below).

### The target and the timing

**Target: 2^11 = 2,048 community XP.** A binary number, at a scale that repo activity can reach. The simulation below
kept it: the split makes a steady project hatch a little sooner than before (reviews are new XP), not enough to move
the target.

Assumptions: the repository stays private for 30 days, during which only the core team counts (capped); then it goes
public with a launch spike (three times the steady rate on day one, fading over about a week). 15 percent of issues
and 10 percent of pull requests and reviews earn no instant XP (spam, capped accounts). Each merge fixes 0.6
issues; 10 percent of opened pull requests are salvaged; half of reviews are marked meaningful. Items are validated 5
days after the event, and the team meditates once a week. One release every two weeks.

| Outside activity once public (per day) | Steady XP a day (instant + validated) | Hatch, days after going public | Where the 2,048 came from |
|---|---|---|---|
| Quiet: 1.5 issues, 0.5 PRs, 0.25 merges, 0.5 reviews | 2.7 + 4.5 | 161 (about 5 months) | core 512, instant 471, validated 770, releases 300 |
| Steady: 5 issues, 2 PRs, 1 merge, 2 reviews | 9.8 + 18.0 | 43 (about 6 weeks) | core 512, instant 512, validated 935, releases 100 |
| Busy: 15 issues, 6 PRs, 3 merges, 6 reviews | 29.6 + 54.0 | 14 (2 weeks, at the second meditation) | core 512, instant 512, validated 1,073, releases 25 |

For comparison, 1,024 would take 77, 21 and 14 days, and 4,096 would take 336, 98 and 28. The earlier single-share
weights gave 211, 57 and 13 days at 2,048. At least half of x0 always comes from validated work and releases, because
the core and the instant share are each capped at a quarter.

Releases move the quiet and steady cases most. If tags did not count (a repository that stopped tagging, or a hook that
listened for a GitHub Release the repository never publishes), the same model gives 224, 49 and 14 days at 2,048
(105, 21 and 14 at 1,024, and 448, 105 and 35 at 4,096). Section 3 of `docs/LIVE-EGG.md` has the hook count the tag.

```python
import math
INSTANT = {"issue": 1, "pr": 1, "merge": 2, "review": 1}
VALID = {"issue": 4, "pr": 10, "review": 4}
RELEASE = 25
CORE_DAY, CORE_SHARE, INSTANT_DAY, INSTANT_SHARE = 40, 0.25, 60, 0.25
SPAM = {"issue": 0.15, "pr": 0.10, "merge": 0.0, "review": 0.10}
LEVELS = {"quiet":  {"issue": 1.5, "pr": 0.5, "merge": 0.25, "review": 0.5},
          "steady": {"issue": 5,   "pr": 2,   "merge": 1,    "review": 2},
          "busy":   {"issue": 15,  "pr": 6,   "merge": 3,    "review": 6}}
FIXES_PER_MERGE, SALVAGE, MEANINGFUL, LAG = 0.6, 0.10, 0.5, 5
def spike(d): return 1 + 2 * math.exp(-d / 7)
def hatch_day(target, level, private_days=30, release_every=14, meditate_every=7):
    a = LEVELS[level]
    core = min(CORE_SHARE * target, CORE_DAY * private_days)
    inst = queued = valid = rel = 0.0; backlog = []; d = 0
    while core + inst + valid + rel < target and d < 3000:
        s = spike(d)
        day_inst = s * sum(a[k] * INSTANT[k] * (1 - SPAM[k]) for k in INSTANT)
        day_valid = s * (min(FIXES_PER_MERGE * a["merge"], a["issue"]) * VALID["issue"]
                         + (a["merge"] + SALVAGE * a["pr"]) * VALID["pr"]
                         + MEANINGFUL * a["review"] * (1 - SPAM["review"]) * VALID["review"])
        inst = min(INSTANT_SHARE * target, inst + min(INSTANT_DAY, day_inst))
        backlog.append(day_valid)
        if len(backlog) > LAG: queued += backlog.pop(0)        # validated LAG days after the event
        if d % meditate_every == meditate_every - 1: valid += queued; queued = 0
        core = min(CORE_SHARE * target, core + CORE_DAY)
        if d % release_every == 0: rel += RELEASE
        d += 1
    return d, round(core), round(inst), round(valid), round(rel)
for t in (1024, 2048, 4096):
    print(t, {lv: hatch_day(t, lv) for lv in LEVELS})
```

### Anti-gaming

The stakes are low (the hatch is a launch event, not a prize), so the rules aim to make gaming slow and visible,
not impossible. The split is the main defence: the cheap actions earn little, and the large share needs a person to
merge or the reviewer bot to mark.

**Instant XP** is small and capped, and is never taken back (so the fill never jumps backwards):

- At most 3 instant XP per account per UTC day across issues, opened pull requests and reviews. Merges and releases
  have no per-account cap, because a maintainer decides them.
- Each item earns each instant award once. The award is keyed by the item and the action (`opened`, `merged`, a
  review's node id, a release's tag), so a replayed or repeated event adds nothing. Merges and releases have no
  per-account cap, so this is what stops a replayed merge from adding its 2 XP again (`docs/LIVE-EGG.md` section 3.2).
- A review on one's own pull request earns nothing.
- At most 15 outside instant XP an hour and 60 a day across everyone, and at most 25 percent of the target (512) over
  x0's life. Past a cap, events still animate.
- There is no account-age rule. A webhook payload carries no account creation date (GitHub's documentation lists none),
  and looking one up would give the hook a GitHub token, which it holds none of today. The caps above already bound new accounts: twenty fresh accounts reach the
  60 a day ceiling and stop there, and instant XP can never pass 512 in all. A maintainer who wants an author's items
  not to count labels them `spam`.

**Validated XP** needs a merged pull request or ion's reaction from ion's own account:

- A merge can only be made by a maintainer, so issue and pull request validation is as hard to fake as the body the
  maintainer read when merging. After the merge, an `edited` event counts only when its sender is a core account (a
  maintainer adding a missed `Fixes #N` or `Salvaged-from` line). The same edit by the pull request's author counts
  nothing. Otherwise an author could add three of each after the merge, pointing at items from other accounts, for
  3 x 4 + 3 x 10 = 42 XP, outside the instant caps.
- A merged pull request validates at most 3 issues and 3 salvaged pull requests. An issue or pull request is
  validated once. An item that was voided (closed with a `spam`, `invalid` or `duplicate` label, deleted, transferred
  out, or closed by its own author within an hour) is never validated.
- A review counts only if the `rocket` reaction comes from the account whose numeric id is the configured bot id.
  Anyone else's `rocket`, including a maintainer's, is ignored, and a renamed or look-alike login changes nothing. At
  most one validated review per account per pull request and 3 per account per UTC day.
- A meditation needs a signed maintainer call (or the 14-day backstop). Nobody outside the team can start one.

**Bots and loops.** Events from a GitHub App (sender type `Bot`, or a login that ends in `[bot]`) and from ion count
nothing and do not animate. The exception is an App on the organization's agent list, which is treated as an agent (see
"Agent activity"): its events animate and its merged pull requests count inside the core cap. The rule looks at the
account that acted, the sender. Credit for a merged pull request still goes to the pull request's author. A global cap
on instant XP stops any loop from moving x0 fast.

**Audit.** The function keeps per-day totals and the rules are in this document, so anyone can check the numbers once
the repository is public by recounting from GitHub: merges, closing keywords, `Salvaged-from` lines and ion's
reactions are all public.

### Founders and core: a cap, not a discount

The question was whether the founders' own activity should count less. Recommendation: **count it, but cap it**.

- **Core** means the founders, the maintainers, and the agents they run. The list is a setting of the feed function,
  not a file in the repository.
- Core activity counts at the normal weights, instant and validated together, up to **40 XP per UTC day** and up to
  **25 percent of the target over the egg's life** (512 of 2,048). Validated core XP is dated to the day it was
  validated, as for everyone, and is granted at the meditation like the rest.
- **Why count it at all.** While the repository is private the core team is the whole community. Counting nothing would
  leave x0 at zero for months while the project is in fact busy. A quarter of the way is a fair picture of "the
  builders started it".
- **Why not a plain multiplier.** Core volume is high and mostly agent-driven: in its first day and a half (to
  2026-10-13 12:00 UTC) the repository saw 54 issues, 52 pull requests and 33 merges, all from core accounts. Any
  discount factor still scales with volume, so the core alone would hatch x0 in days. A cap bounds it whatever the
  volume, so the rest must come from outside contributors and from releases.
- Releases are always made by the core, and still count in full outside the cap (at most one a week): shipping is the
  one thing x0 should reward from the team.
- **Merges** are credited to the pull request's author, not the maintainer who merged: an outside contributor's merged
  pull request is outside XP. A core member who fixes an outside issue gives the outside author the issue's validated
  XP and takes the merge inside the core cap.

### Agent activity

Agents (exo-razi and others) now open issues and pull requests too. Recommendation: **agent work counts only where a
person accepted it.**

| Agent event | Instant XP | Validated XP |
|---|---|---|
| Issue opened by an agent | 0 | 0, even if fixed |
| Pull request opened by an agent | 0 | 10 if merged or salvaged, inside the core cap if the agent is run by the core |
| Agent pull request merged | 2, inside the core cap if core-run | (the 10 above) |
| Review by an agent | 0 | 0 |
| Release | 25, as above | none |

Why: an agent can open issues, pull requests and reviews at almost no cost, so counting them would turn x0 into a
measure of how many agents run. A merge is different: a maintainer reviewed it and took it into the product, which is
the significance the weights are about. Agent events still animate on the site, because the project is alive when its
agents work. An outside user's agent is treated the same way: its merged work counts as that user's, inside that
user's normal limits.

How the function knows: agent work carries a marker. Exo's own agents add the `agent` label when they open an issue or
pull request (one line in their tooling); a pull request whose body ends with an agent attribution line counts as
agent work too. An App on the organization's agent list (`EGG_AGENT_APP_IDS`, an environment variable of the feed
function like the core list) is agent work by sender, with no marker needed. Unmarked work from a core account counts
as core. A missing marker can at worst move XP inside the core cap, so the cap is the backstop.

### What each person sees

- **Before the hatch.** The pet in the terminal and the web client is x0. Its fill is the community total, read at
  most once a day from the site's public feed (`GET /api/egg/feed`, `docs/LIVE-EGG.md`). Where a client draws x0 with
  the brand egg (the web client shares `egg.js`), it passes the stage the feed gives (`docs/LIVE-EGG.md` section 3.6):
  the six face cells fade in around the yolk and the shell stays whole until the target. The terminal glyph, the pet
  renderer's own drawing, lights a larger share of its shell nodes as the total rises, and its core brightens. The
  user's own XP still accrues in their ledger, so nothing is lost by waiting.
- **On the site.** Every repository event plays at once. When the team meditates, x0 folds inward, breathes slowly and
  absorbs each validated contribution as a cold-blue glow travelling into its core, with the XP ticking up, then
  wakes.
- **The crack.** When the counted total reaches the target (usually during a meditation), x0 wakes and cracks for 24
  hours: the shell's edges flicker, and the site says when it will hatch.
- **The hatch.** At the hatch time every egg opens at once: the shell nodes drift outward and fade, and the drawing
  inside grows to full size. This is the cell form. Banked personal XP then applies, one form per day.
- **The mark.** Installs whose own ledger has verified events from before the hatch time keep a thin orange ring
  around the cell in every later form. It is checked locally against the hatch time; nothing is sent and no account
  is needed.
- **After the hatch.** A new install starts from its own egg, which fills from its own XP and hatches at level 6 (260
  XP, usually on the first day).
- **The site.** The site egg is x0 and reacts to every repository event (`docs/LIVE-EGG.md`). After the hatch it shows
  x0 hatched; see open question 3.

An install that never goes online (or sets `pet.network = off`) still hatches: the hatch time ships in the next Exo
release, and the egg opens on update.

## 4. Privacy

The revision removes the only telemetry the first design had. Installs send nothing about themselves, and there is no
counter service, no install keys, no proof of work and no Merkle receipts.

| What an install does | What it sends |
|---|---|
| Reads the community total, at most once a day, off the turn path | one `GET` to the public feed, with no identifier in it; nothing when `pet.network = off` |
| Computes personal XP | nothing; it stays in the ledger |
| Checks the founders' ring | nothing; a local comparison with the hatch time |

**What the host sees.** The request itself: the address it came from, the time and the user agent, as any website
sees. That is enough to count how many distinct addresses ask each day. The feed function logs status codes and counts,
never addresses or payloads (`docs/LIVE-EGG.md` section 4), but the hosting platform keeps request logs of its own. An
install that must send nothing sets `pet.network = off`, and its egg opens from the hatch time that ships in a release.
The public hatch page says this in the same words.

The feed itself carries only an event type (and, for a validation, its kind), a time, a level and a nominal weight
(`docs/LIVE-EGG.md` section 4). The feed function sees GitHub payloads and keeps only keyed hashes of authors, for the
per-account caps, until the item is validated or voided (at most 90 days; 8 days for items that cannot be validated).

## 5. The pet shows what Exo has learned

The pet's lit points are a picture of Exo's model of you, made only of counts, never content.

- **Learned items light dots.** Every promoted lesson or taste item lights one dot in cold blue, which stays lit.
  Forms with more dots have room for more; the oldest lessons sit at the centre.
- **House taste is grey, yours is blue.** On a new install that starts from the house taste (D-044), house items show
  as dim grey dots. As the sustained-change rule swaps an item to the user's version, its dot turns cold blue. Over
  months the pet visibly becomes the user's own: the share of blue to grey is the share of taste that is theirs.
  House precedence decay (`max(0.3, 0.7 * 2^(-t/365))`) dims the grey dots on the same curve.
- **The shape leans toward you.** Taste areas (writing, method, verification, values) are mapped to the axes of the
  current form. The more items learned in an area, the more that axis is folded, a small and stable strain. Two users
  at the same level have visibly different pets, and the difference is their taste profile in outline.
- **Changes are shown, not announced.** A revert puts a dot out. A value stepped back (D-044) dims its dot to grey.
  A taste swap moves a dot to a new seat with a short pulse.
- **Hover or `/pet` explains it** locally: "41 learned · 12 house · writing 14 · method 17 · verification 6 ·
  values 4". The renderer receives numbers only.

Nothing about this view leaves the machine, and no install reports anything (section 4).

## 6. Activity and animation

### No new protocol event

The protocol is at 29 of its 30 events. Exopet adds none. Clients already receive everything the pet needs, so the
pet's state is a pure function of the event stream in the client. XP, level, form and the learned-dot counts come
from one `command.run` built-in, `pet`, which returns a small JSON status and is read when a thread opens, after each
turn, and after a `taste.updated`.

### The states

| Pet state | Comes from | Motion (default lattice body) | Atlas row (export) |
|---|---|---|---|
| `idle` | no turn running | slow breath: the fold eases 0 to 6 degrees and back over 8 s; a still frame after 30 s | idle |
| `thinking` | `turn.started`, `reasoning.delta`, a `model.call` in flight | faster fold, 0 to 20 degrees, pulses travel inward | review |
| `answering` | `message.delta` | pulses travel outward along bars | running |
| `tool` | `tool.started` until `tool.completed` | an orange packet moves from cell to cell along the bars | running |
| `waiting` | `approval.requested` until `approval.resolved` | the lattice holds at its closed fold, outline pulses orange, nothing else moves | waiting |
| `asking` | `question.asked` until answered | as waiting, with a cold outline | waiting |
| `learning` | `taste.updated`, a promoted lesson | a cold pulse runs to one dot, which turns blue and stays | waving |
| `folding` | `memory.summarized`, `context.compacted` | the fold runs toward close packing and back | jumping |
| `done` | `turn.completed` | one slow exhale | jumping |
| `error` | `error`, `harness.error`, a failed `tool.completed` | one bar flashes red, the fold snaps open, then relaxes over 1 s | failed |
| `peer` | `peer.message` | a faint second lattice appears at the edge for 2 s | waving |
| `job` | `job.updated` running | one small dot orbits the form | running |
| `evolving` | a form change | the full transition, once, skippable, at most 3 s | jumping |

When several apply, the first wins in this order: error, waiting, asking, tool, answering, thinking, folding,
learning, done, peer, job, idle. `error`, `learning`, `folding`, `done`, `peer` and `evolving` are transient: they play
once and fall back.

Every pet must provide `idle`, `thinking`, `tool`, `waiting` and `error`. The rest are optional, with a fallback chain
(`asking` to `waiting`, `answering` to `thinking`, `folding`, `done`, `learning`, `peer`, `job` and `evolving` to
`idle`). The last column is how an exopet maps onto the 9-row atlas when exported, so it can float in any app that
reads that format.

## 7. Where the pet shows

| Surface | Form | Notes |
|---|---|---|
| Terminal client | a braille glyph, 8 columns by 4 rows, at the right end of the status strip | `/pet on, off, mini`; `EXO_PET=0`. The start screen's hero becomes the user's own pet at its current form |
| Web client | the brand bar's square dot becomes a 16 pixel pet glyph; the empty-thread hero is the pet full size; a pet card in the instrument column (form, level, bar toward the next form, learned count) | |
| Desktop float | a 96 pixel transparent, always-on-top window in the future desktop shell (the web client in a thin shell, `docs/STANDALONE.md`) | until then, `exo pet export --atlas` writes the 9-row atlas package so the pet can float in an existing desktop pet app |
| Project site | the hero egg is x0, live with repository events and meditations (`docs/LIVE-EGG.md`), then x0 hatched; a hatch page with the total, the rules and the privacy table; a gallery page | |

## 8. Fun, on brand, never in the way

- Motion only reports state, as everywhere in Exo. At rest the pet is still.
- No face, no speech, no sound by default, no notifications, no needs, no modal. An evolution is one dim transcript
  line (`exopet · evolved to tile · 9 cells`) and a 3 second animation in the corner.
- One switch turns it off in each client. Reduced motion draws still frames; `NO_COLOR` draws the shape in the
  terminal's own colours.
- The palette is the site's tokens. The pet never uses orange for decoration, because orange means "act now".

## 9. Performance budget

| Item | Budget |
|---|---|
| Terminal | precomputed frames per state, at most 4 fps while active, 0 fps after 30 s idle; at most 0.5 percent of one core added, measured with `scripts/idle_cpu.py` |
| Web | canvas at most 24 fps active and 12 fps idle breath, then still; stops when hidden or off screen; pixel ratio capped at 1.5; at most 2 ms a frame; at most 1 percent main-thread time idle |
| Points drawn | at most 512 at any form |
| XP | incremental from the last ledger seq: under 5 ms after a turn; a full recompute of 100,000 events under 2 s, in the background |
| Memory | under 2 MB for the pet in any client |
| Network | at most one GET a day (the community total), never on the turn path; zero when off |
| Core | the pet never runs on the turn path; the core only answers `pet` |

## 10. The pet format

One manifest, `exopet.json`, describes any pet. A pet is data, never code.

```json
{
  "format": 1,
  "id": "lattice",
  "name": "Exo",
  "author": "aska-digital",
  "license": "CC-BY-4.0",
  "kind": "procedural",
  "gates": [6, 16, 30, 45, 60, 72, 84, 92, 99],
  "forms": ["egg", "cell", "tile", "cube", "frame", "d4", "d5", "d6", "d7", "d8"],
  "states": { "idle": {}, "thinking": {}, "tool": {}, "waiting": {}, "error": {} },
  "palette": "exo"
}
```

- `kind: procedural` describes each form as points, bars and cells plus named parameters (fold angle, pulse rate,
  packet path), and each state as parameter curves. One small renderer per client draws it: braille in the terminal,
  canvas on the web and site. The default pet is procedural.
- `kind: sprite` gives one atlas per form in the 9-row, 192x208 layout, so an existing atlas pet can be imported as a
  one-form exopet, and any exopet can be exported to that layout.
- Gates are levels on the shared ladder, 1 to 9 of them, strictly increasing, so a level means the same work in every
  pet. Swapping pets keeps XP and level; the new pet shows the form for that level.
- `palette: "exo"` uses the site tokens; a pet may add up to two accents.
- Every form needs a still frame for reduced motion.

## 11. The gallery

A place to find and share pets, run as plainly as possible.

- **An index repository.** Each pet is a folder with `exopet.json`, its assets and a preview. A submission is a pull
  request. CI checks the schema, the required states, sizes (at most 2 MB a pet), the reduced-motion frames and the
  palette, and renders a contact sheet (every form by every state) into the pull request.
- **Signed packages.** The author signs the package with an Ed25519 key, the same crypto as the ledger. The index pins
  the package hash. `exo pet install` checks both.
- **The site renders the gallery** statically from the index. No accounts, no database, no download counts.
- **Review and takedown.** A maintainer merges after the checks pass. Removal requests go through an issue. Every pet
  carries an explicit licence (CC0, CC BY 4.0 or MIT).
- **Two shelves.** Pets that pass the house-look check (palette, no text, motion only for state) are shown first.
  Others are listed below them, labelled.

Commands: `exo pet` (status), `exo pet browse`, `exo pet install <id>`, `exo pet use <id>`, `exo pet preview`,
`exo pet check <dir>`, `exo pet publish <dir>` (guarded: it opens a public pull request, so the gate asks), and
`exo pet export --atlas`.

## 12. The skill: make a pet from the user's direction

A shipped skill, `exopet-create`, lets an Exo agent turn a request like "make me a pet that is a slow jellyfish made
of light" into a working pet.

1. **Brief.** Read the request and ask at most one question (what it is, or how it should feel). Write a short brief:
   subject, mood, kind (procedural by default; sprite if the user wants a creature drawn in pixels), and how it grows.
2. **Design the forms.** For a procedural pet, pick a base figure and a growth rule. The lattice project's census of
   symmetric frameworks gives many on-brand starting points beyond the default. For a sprite pet, make one base
   design, then one strip per state per form, using the image tool when one is configured and the built-in pixel
   renderer when not.
3. **Map the states.** Fill the state table from section 6. Required states first. For each, say what moves and why
   it reads as that activity: waiting must look held, error must look like a snap and a recovery, never like distress.
4. **Check.** `exo pet check`: schema, required states, fallbacks, frame and size budget, palette, still frames.
5. **Preview.** `exo pet preview` writes a contact sheet and a small page; the agent looks at it and fixes what reads
   wrong before showing the user.
6. **Install, then offer to publish.** Install locally and switch to it. Publishing is a public act and goes through
   the gate.

The skill's rules: the motion must report state and nothing else; no text in frames; no faces showing pain or
distress for errors; the user's description is the only source, never the user's history or taste data.

## 13. Build plan (if accepted)

- **Wave 1, the pet locally.** `src/exo/pet/` (the XP function, ladder, form, `pet` built-in), the lattice geometry
  shared by both renderers, the terminal glyph and the web glyph and card, the state machine. Tests: the XP function
  is deterministic over a fixture ledger; a forged or unpinned event earns nothing; a broken anchor freezes the pet.
- **Wave 2, the hatch.** The repository feed, x0 live on the site and its meditation (`docs/LIVE-EGG.md`), the
  clients' daily read of the community total, the crack and hatch, the founders' ring, the hatch page with the rules.
  Staging first.
- **Wave 3, the gallery and the skill.** The format, `exo pet check, preview, install, publish, export`, the index
  repository and its CI, `skills/exopet-create/`.

## Open questions for the principal

Revised 2026-10-13 for D-058. The old questions on contributor counts, the counter's platform and receipts are gone
with the counter service. Timings below use the instant and validated split, with a weekly meditation. Each question
marks a recommended pick.

1. **The hatch target.** (a) 2^11 = 2,048 XP, as proposed (recommended): about six weeks for a steady project, two
   weeks for a busy launch. (b) 2^10 = 1,024: three weeks when steady, under three months when quiet, less of an
   event. (c) 2^12 = 4,096: a bigger moment, about three months at a steady rate.
2. **If the community is slower than hoped.** (a) No fallback (recommended): the egg waits, and releases keep it moving.
   Honest, and slow when quiet (the model above still hatches a quiet project in about five months). A deadline costs
   nothing to add later, once the real rate is visible. (b) A published deadline (for example 90 days after going
   public) after which it hatches anyway: always ends well, weaker as a shared goal. (c) The target steps down by 2x
   every month, announced up front.
3. **x0 after the hatch.** (a) It keeps evolving with community XP, one stage per factor of 8, and keeps meditating
   (recommended): an ongoing reason to visit, and the feed runs anyway for the live animation. (b) It stays at cell as
   a monument.
4. **The core cap.** (a) 40 XP a day and 25 percent of the target, as proposed (recommended). (b) No core XP at all: a
   purely outside-earned hatch, but a flat egg until the repository is public. (c) Count the core in full: hatches in
   days on today's volume.
5. **Agent work.** (a) Only merged agent pull requests count, as proposed (recommended). (b) Agent issues and pull
   requests count at half weight inside the core cap. (c) Agents count like people.
6. **Default on or off for the pet in the clients.** (a) On, as the small glyph (recommended): people see it, which is
   the point. (b) Off until the first evolution. (c) On in the web client, off in the terminal.
7. **The founders' ring.** (a) A thin ring for every install that was running before the hatch, as proposed
   (recommended). (b) No ring: no status difference between users.
