# Exopet: Exo as a living lattice (D-048)

Status: **Proposed** (2026-10-12). Nothing here is built. D-048 in `DECISIONS.md`.
Builds on D-040 (signed authors, anchored ledger), D-012 and D-022 (the learning loop), D-044 (taste baseline) and
D-031 (the Exo look). The open questions for the principal are at the end.

## The idea in one paragraph

Exo learns you and adapts. Exopet makes that visible. The pet is not a mascot that sits beside Exo: it is Exo, drawn
as a small geometric figure that grows when real work and real teaching flow into it. Its growth is counted from the
signed ledger, so it cannot be faked by editing a file. Its shape is the incentre lattice, a drawing that gains cells,
then dimensions, then internal structure as it evolves. Its motion reports what the agent is doing (thinking, running
a tool, waiting on you), and its lit points show what Exo has learned about you. At launch, everyone's Exo starts as
one shared egg on the project site. It hatches only when the whole community has fed it enough work. After that,
every install grows its own.

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
| 0 | **egg** | 0 | the site orb: a shell of linked nodes; at its core, faint, the drawing | start | 0 | | |
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

### Light and colour

Exo's two colours keep their meaning. Orange is the signal: activity happening now. Cold blue is system knowledge:
what Exo has learned about you (section 5). Lines are the fg colours at low alpha, like the site orb. No other colour
is used except the error red, and only for one bar, for under a second.

## 2. Where XP comes from

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

## 3. The community hatch

### The rule

Until the hatch, every install's pet is the same egg. The egg is the orb on the project site. The
first evolution (egg to cell) is locked for everyone until the community, together, has earned enough XP.

| Number | Value | Why |
|---|---|---|
| Target | 2^20 = 1,048,576 community XP | a round binary number; "a million bits of information" reads well on the egg |
| Distinct contributors | at least 256 installs, each with at least 3 reporting days | one person, or a few, cannot hatch it alone |
| Daily cap per install | 1,000 XP (the local cap) | matches section 2 |
| New-key ramp | 300 XP a day for a key's first 7 days | a fresh key cannot add much |
| Crack phase | 24 hours from threshold to hatch | time to drop bad keys before the moment; and a day of "it is cracking" on the site |

How long it takes: 300 contributing installs at about 350 XP a day reach the target in about 10 days; 1,000 installs
in about 3 days. Fewer than 256 contributors never hatch it; see open question 2.

### What each person sees

- **Before the hatch.** The pet in the terminal and the web client is the egg, drawn like the site orb. Its fill is the
  community total, read once a day. The share of lit shell nodes rises from 8 to 100 percent with progress, the core
  brightens, and the inward pulses come faster when more installs reported in the last day. The user's own XP still
  accrues in their ledger as normal, so nothing is lost by waiting.
- **The crack.** For 24 hours the shell's edges flicker. A line on the site says when.
- **The hatch.** At the hatch time every egg opens at once: the shell nodes drift outward and fade, and the drawing
  inside grows to full size. This is the cell form. Banked personal XP then applies, one form per day.
- **The mark.** Installs that contributed before the hatch keep a thin orange ring around the cell, in every later
  form: a record that they hatched it. It is checked by the install's own receipt (below), not by a name or account.
- **After the hatch.** A new install starts from its own egg, which fills from its own XP and hatches at level 6 (260
  XP, usually on the first day). This is the EvoPet model.
- **The site.** After the hatch, the site's orb shows the community Exo and keeps evolving on a larger scale, one form
  per factor of 8 in community XP (2^20 cell, 2^23 tile, 2^26 cube, and so on). See open question 3.

An install that never goes online (or opts out) still hatches: the hatch time is shipped in the next Exo release, and
the egg opens on update.

### Anti-gaming

Exo is local-first. A server cannot see a user's ledger, and it must not, so it cannot prove that the work behind a
report was real. A modified client can lie. The design does not pretend otherwise. It makes cheating bounded, costly
and visible, and keeps the stakes low: the hatch is a launch event, not a prize.

- **Locally:** XP is the ledger function, with the limits in section 2. A stock client cannot report more than its
  ledger supports.
- **Per key:** one report per key per UTC day, capped at 1,000 XP, and 300 for the first 7 days.
- **Per new key:** registration needs a proof of work of about a minute of one CPU core, so a thousand fake installs
  cost a thousand CPU minutes and still add at most 300 XP a day each for a week.
- **Distinct days:** a key counts toward the 256 only after 3 reporting days, so a burst of new keys on the last day
  counts for nothing.
- **Crack phase:** keys that report the cap every day from registration, or that register in bursts from one network
  in the server's short-lived rate limiter, can be dropped by the operator during the 24 hours. The rules for dropping
  are published before launch.
- **Audit:** each day's reports are hashed into a Merkle tree and the root is published. Each install keeps its own
  proof of inclusion (its receipt), so anyone can check that their contribution was counted without seeing anyone
  else's.

## 4. Privacy and the counter service

### What is sent, and what is not

Community reporting is **off by default**. Exo asks once, in one line on first run, and the question shows the exact
fields that would be sent. `exo pet community on|off` changes it at any time.

| Sent, once a day | Never sent |
|---|---|
| a counter key made only for this (Ed25519, not any Exo key) | prompts, answers, file names, paths |
| the UTC date of the day reported | event kinds, counts of turns, verdicts or lessons |
| that day's XP, capped and rounded down to a multiple of 10 | model names, providers, costs, tokens |
| a report sequence number and a signature | times of day, session counts, taste items |
| once, at registration: the proof of work | anything about the user or the machine |

- The report for a day is sent at a random time during the next day, so its timing says nothing about when the user
  worked.
- Turning community off deletes the counter key locally and asks the server to drop it.
- Opting out of everything (`pet.network = off`) means no request at all, including the daily read of the egg's fill.
  The egg then fills from the user's own XP until the release that carries the hatch time.

### The service

The counter is the only shared service in the exopet design, and the only one Exo would run. It is small on purpose:
three routes and one counter.

| Route | Does |
|---|---|
| `POST /v1/register` | takes a counter key and its proof of work; records the key and its registration day |
| `POST /v1/report` | takes one signed day report; checks the signature, the cap, the ramp and one-per-day; adds to the day total |
| `GET /v1/egg` | public: target, total, contributors (count), reports in the last day, crack and hatch times, today's Merkle root, site form; cached at the edge for 60 seconds |

What the server keeps: each key and its registration day; the current day's set of keys that have reported (for one
per day); per-day totals and contributor counts; the Merkle trees for 7 days, then only their roots. No IP addresses
are stored and request logging is off. Per-key daily rows are deleted after 7 days.

Recommended shape: one serverless function in front of one strongly consistent store (a single-writer counter), so
two reports cannot race. Both the server code and the client code live in the open repository. The platform choice
is open question 4. The site reads `GET /v1/egg` to draw the egg; changes to the site go to the staging domain first.

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

Nothing about this view leaves the machine. The community report has no taste fields at all.

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
| Project site | the hero orb is the community egg, then the community Exo; a hatch page with the counter, the rules and the privacy table; a gallery page | |

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
| Network | at most one GET and one POST a day, never on the turn path; zero when off |
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
- **Wave 2, the hatch.** The counter service, `exo pet community`, the site egg on the staging domain first, the hatch
  page with the privacy table, receipts.
- **Wave 3, the gallery and the skill.** The format, `exo pet check, preview, install, publish, export`, the index
  repository and its CI, `skills/exopet-create/`.

## Open questions for the principal

1. **The hatch target.** (a) 2^20 XP with 256 contributors, as proposed: a launch-week event if a few hundred people
   install. (b) 2^18 XP with 64 contributors: hatches even with a small first wave, but less of an event. (c) Set the
   number on launch day from early sign-ups: fits reality, but the number moves after people see it, which feels less
   fair.
2. **If the community is slower than hoped.** (a) No fallback; the egg waits: honest, but a stalled egg is a bad look.
   (b) A published deadline (for example 30 days) after which it hatches anyway: always ends well, weaker as a shared
   goal. (c) The target steps down by 2x every two weeks, announced up front: keeps momentum, more rules to explain.
3. **The site creature after the hatch.** (a) It keeps evolving with community XP, one form per factor of 8: ongoing
   reason to visit, needs the counter to run for good. (b) It stays at cell as a monument: simple, and the counter can
   shut down after the hatch. (c) It shows the most common form among reporting installs: reflects users, but needs a
   new report field.
4. **The counter's platform.** (a) A serverless function with one strongly consistent object store at the edge
   provider that already serves the DNS: smallest and cheapest, a second platform to run. (b) A function on the
   platform that hosts the site plus a hosted key-value store: one platform, but two vendors and a weaker
   consistency story. (c) A tiny service on one small server: full control, but uptime is ours.
5. **Default on or off for the pet in the clients.** (a) On, as the small glyph: people see it, which is the point.
   (b) Off until the first evolution: no surprise for serious users, but most will never find it. (c) On in the web
   client, off in the terminal: matches where people expect play.
6. **The founders' mark.** (a) A thin ring for every pre-hatch contributor, as proposed. (b) No mark: no status
   difference between users. (c) A mark only for the first 1,024 contributors: more special, and more reason to game
   registration.
