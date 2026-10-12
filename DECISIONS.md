# DECISIONS

Each significant choice: what, why, what was rejected. Newest last. A reads along and redirects.
Format: `D-NNN (date) title` then Decision / Why / Rejected / Revisit if.

## D-001 (2026-10-11) Work lives in this repo; phases leave handoffs
- **Decision:** All Exo planning and code lives in `aska-digital/exo` (private). Each phase ends with a
  handoff in `~/Work/handoffs/exo-phase-N.md` and a commit.
- **Why:** A asked for work that survives compaction and session switches.
- **Rejected:** Keeping plans in agent memory or the Drive vault (lost on compaction; slow sync).

## D-002 (2026-10-11) Research and inventory run as parallel subagents writing to files
- **Decision:** Five subagents: guardian-angel crawl, GitHub inventory, local ~/.hermes inventory,
  local documents and exports inventory, base evaluation. Each writes one file in `research/` or
  `inventory/` and returns a short summary.
- **Why:** A asked to keep the lead session lean; the four tasks are independent.
- **Rejected:** One long crawl in the lead session (context cost); protean lanes for the crawl
  (their small models handle one mechanical task well, not open-ended judgment-heavy reading).

## D-003 (2026-10-11) hafiz is an input, not a side project
- **Decision:** `~/hafiz` (the principal knowledge base built earlier today) is treated as Exo's
  memory seed and its method as Exo's ingestion pipeline v0.
- **Why:** It already implements the guardian-angel record, router, provenance and training seeds.
- **Rejected:** Starting the memory model from scratch.

## D-004 (2026-10-11) Two goals held together: fit one person, replicate for anyone
- **Decision:** Every component separates the **mechanism** (generic, shipped in the repo) from the
  **principal** (one person's data, profile, tuning, rules, kept in a per-user store). A is principal #1.
  Nothing in the core may assume A's accounts, machines, agents or names; everything about A lives in
  his principal store (seeded from `~/hafiz`). A new user gets the same mechanism and an onboarding
  flow that builds their own store.
- **Why:** A: "there's two key aspects to this task, building it to fit me as well as possible and
  making it replicable for any user."
- **Test:** a fresh install with an empty principal store must run end to end; A's install must lose
  nothing when the core updates.
- **Rejected:** Building for A first and generalizing later (A-specific assumptions leak into core and
  are expensive to remove). Building generic first (loses the fit, which is the point of a guardian angel).

## D-005 (2026-10-11) Base: Hermes Agent as a distribution, not a hard fork
- **Decision:** Exo runs on upstream Hermes Agent pinned to weekly release tags. All Exo features ship
  as Hermes plugins plus a separate `exo` Python package (this repo). A patch queue of at most 5 core
  patches is allowed, and each must also be an open upstream PR; a patch is dropped when it merges or
  when a hook replaces it.
- **Why:** Evidence in `research/BASE-EVAL.md`. Hermes already covers gateways, cron, kanban,
  delegation, memory and context-engine plugin points, TUI, desktop and 40 providers (score 81, best).
  Upstream moves about 490 commits a day; A's open core PRs sit on files touched 86 to 170 times a month,
  while the plugin contract files change 5 to 9 times a month. All 4 of A's merged PRs were plugin-only;
  13 of 20 closed PRs were core-only. Plugin work lands; core work mostly does not. A already runs this
  way (vanilla checkout plus 8 external plugins).
- **This changes A's brief.** A leaned toward a fork that merges everything natively. "Native" here
  means first-class Exo plugins installed by the Exo installer, not edits to Hermes core. The user sees
  one system; the code stays rebasable.
- **Replicability (D-004):** one installer gives any user upstream Hermes plus Exo; personal data lives
  in a separate store.
- **Rejected:** hard fork (73: rebase lane forever on the hottest files); OpenClaw (72: strong gateways
  and phone apps but no investment, TypeScript, OpenAI-sponsored foundation; borrow ideas); OpenCode,
  pi, Goose, Codex (good engines, no personal-system layer; use as workers via ACP/MCP); Letta Code
  (best memory design, cloud-first; copy its ideas); fresh on Claude Agent SDK (54: Claude-only,
  proprietary TS SDK, no subscription login for third parties).
- **Revisit if:** Nous relicenses, or breaks the plugin API repeatedly, or a needed hook is refused
  upstream twice. Then freeze at the last good tag and fork from there (MIT allows it).

## D-006 (2026-10-11) Principles v1 adopted; three tensions resolved
- **Decision:** `research/PRINCIPLES.md` (18 principles from a 69-page crawl of gwern's guardian-angel
  essay and its links) is the design charter. Tensions: (1) cost: spend on compounding work under a
  ceiling A sets, frugal on routine calls; (2) local-first log and store, compute wherever cheapest and
  verifiable; (3) learn at the memory and prompt layer in v1, log training-ready for adapters later.
- **Why:** These keep A's rulings (frugality, model choice is his, local machines) while keeping gwern's
  core loop (log, ask-when-uncertain, corrections that stick, n=1 evaluation).
- **Rejected:** gwern's "$1,000+ a month" default spend (conflicts with A's budget rule); dynamic
  evaluation on open weights as v1 (A's stack is frontier APIs today); passive lifelogging as the main
  data source (low information per byte).

## D-007 (2026-10-11) Inventory ratified: 137 items, verdicts and open questions
- **Decision:** `inventory/INVENTORY.md` verdicts stand (core 18, plugin 13, skill 18, patch 5, upstream 9,
  data 28, reference 11, retire 10, drop 25), with these rulings on its open questions:
  - **Memory:** Hermes runs one active memory provider, so **optchat-memory becomes the episodic base of
    `exo-memory`** (its verbatim daily log and summary tree), not a separate plugin. exo-memory adds the
    principal pack, ledger capture, belief retrieval and correction capture on top. cognition-memory retires.
  - **Taste learning (#126940):** rebuilt in `exo.consolidate` and the principal store, not carried as a patch.
  - **Shared auth across profiles:** no. Per-profile auth as upstream ruled; a per-principal credential broker
    in exo later if needed.
  - **Single source of skills:** the Exo skill pack. protean-team-skills content moves into exo; its ops
    ledgers move to A's principal store as data.
  - **SYM-2P and Divan Bus:** layered. Bus = signed envelope (`exo.bus`); SYM-2P = optional body for
    agent-to-agent briefs and receipts (`exo.protocols.sym2p`). Anything shown to a person renders in plain English.
  - **ordopilot courier** becomes `exo.intake`; ordaprompt stays the front-door router plugin.
  - **Raptora** retires; its hiatus rule ("stale is not abandoned") moves into `exo.attention`.
  - **Patch queue:** #119652, #98901, #107130, #110011, plus a new upstream PR for an auxiliary routing hook.
- **Done now (reversible, local):** salvaged muse-bridge (untracked), the unpushed quota-governor commits
  (git bundle) and the ordaprompt workspace (bundle) into `~/Work/exo-salvage-20261011/`; froze the
  Team6-era Hermes state DBs from the iCloud backup into A's private corpus.
- **Left for A (irreversible or outward):** deleting the 32 GB quarantine; pruning ~70 stale branches on
  the forks; archiving protean-team and aska-digital/hermes-agent; disabling raptora's cron; confirming
  who uses the abu-aska account. Batched in the phase report.
- **Rejected:** carrying all 25 open core PRs (rebase cost, D-005); running optchat and exo-memory side by
  side (one provider slot); keeping protean-team-skills as a second source (drift).

## D-008 (2026-10-11) Build in waves of parallel agents with disjoint ownership
- **Decision:** Phase 4 runs in waves. Wave 1 (done): core package, 40 tests. Wave 2 (running): exo-command,
  Hermes pin + patch queue + contract tests, exo-memory (+ `exo.capture`), exo-context (+ `exo.handoff`),
  exo-gate (+ `exo.gate`), ingest adapters. Each agent owns disjoint paths and does not commit; the lead
  reviews, runs the full suite, commits and pushes. Wave 3: consolidate (taste and beliefs), ask loop,
  n=1 eval, exo.bus (Divan Bus port), exo-orchestra (Protean template and skill pack), installer and
  `exo init` interview, then migration stage M0 on a canary profile.
- **Why:** keeps the lead's context lean (A's instruction) and avoids git races between agents.
- **Rejected:** one agent per plugin committing directly (index-lock races, unreviewed commits); building
  plugins before the core API existed (rework).

## D-009 (2026-10-12) A's redirect: Exo is a standalone harness; Hermes is a source, not a dependency
- **Ruling (A, verbatim in `~/hafiz/nodes/work/exo.md`):** "I also dont want hermes to be a dependency.
  whenever hermes updates progress significantly or there's a meaningful improvement, fix, etc. we can
  rebase the appropriate parts of our system but I dont want a persistent lie that limits our ability to
  iterate and evolve. this will eventually be its own standalone harness so lets make it that way."
- **Supersedes D-005.** The plugin-and-patch-queue approach is retired as the end state. What was built on
  Hermes plugin contracts (exo-memory, exo-context, exo-gate, exo-command, exo-bus, exo-orchestra) is kept
  as logic and re-homed onto Exo's own core. The 0005 aux-routing PR is withdrawn: Exo owns its routing.
- **Also set by A in the same note:**
  - **UI:** the simplicity of opencode and Command Code, the robustness and customizability of Hermes.
  - **Learning loop:** fuse Hermes's branch-off review loop (memory and skill learning) with Command
    Code's branch-off taste learning into one feedback loop that aligns the harness to its user.
  - **Hill-climbing eval (mid-turn addition):** "i also want to bake in some kind of hill-climbing/eval
    system that strengthens the alignment feedback loop further." The loop climbs against a per-user eval;
    a lesson is promoted only if it scores better on held-out cases.
  - **Store:** hafiz's design becomes the store design, merged with Arif (external-data ingest) and
    Eldunari (router, anima, nexus). Their useful parts come in; their structures move to the new format.
  - **Horizon:** a portable companion device (mini robot or a Muse-charm-like object) in a few years.
    Design for this now: a portable principal store, a small-model path, local-first.
- **Next:** research (UI, learning loops, standalone core, store merge) in parallel, then D-010 sets how
  the standalone core is built and how Hermes improvements are pulled in.
- **Rejected:** keeping Hermes as a pinned runtime dependency (A's ruling); a full rewrite from zero
  before the research says what to lift (wastes the 2026 work in Hermes).

## D-010 (2026-10-12) Standalone core: lean Python core, lifted Hermes parts, protocol-first UI
- **Decision:** Exo gets its own core in Python, in this repo. It lifts a small set of Hermes modules
  (memory and context slot interfaces, provider message types and two transports, selected built-in tool
  logic, compressor prompts, and later individual platform adapters) and rewrites the loop, routing,
  tools registry, sessions, plugins, skills, cron and delegation small. It drops the gateway framework,
  TUI gateway, dashboard and desktop. The core runs as `exod`, a server with one typed, replayable event
  stream; TUI, web, desktop, chat channels and a future device are clients. Each lifted or ported module
  is recorded in `PROVENANCE.toml` with its upstream path and commit; `exo upstream` diffs upstream
  weekly and proposes ports, and `commit` moves only after a port or a recorded refusal. Hermes's MIT
  notice ships in `LICENSES/`. The six Exo plugins keep the `plugin.yaml` + `register(ctx)` shape and move
  over with import changes. Build in five waves (`docs/STANDALONE.md` section 6); wave 1 is the core
  skeleton. A's live Hermes runs untouched until each seat cuts over in wave 4.
- **Why:** A's ruling (D-009): no runtime dependency, freedom to evolve, improvements pulled in on
  purpose. Scored 83 against 60 for a hard fork, 60 for a TypeScript core and 47 for the Claude Agent SDK
  (`docs/STANDALONE.md` section 2). Exo's 12k-line package and 509 tests are Python and keep working.
  Exo's plugins touch Hermes at only 20 import sites, so re-homing is cheap. The slot interfaces Exo
  needs are 456 lines with almost no dependencies, while the rest of Hermes is about 926k lines with
  `agent/` and `tools/` importing the CLI in 259 files. A lean core with a line budget and a small
  protocol is the only option that can shrink onto a companion device. A protocol-first split gives
  opencode-style UI simplicity without tying the core to any one client.
- **Rejected:** detached hard fork (owns 926k lines and a 257-method TUI gateway; merges stop working
  within weeks; cannot shrink to a device); TypeScript core (rewrites Exo's package and tests; Hermes ports
  become translations; its UI benefit is available through the protocol anyway); Claude Agent SDK core
  (Claude only, closed runtime, no small-model path); keeping Hermes pinned as a runtime (A's ruling);
  a rewrite with no lifting (throws away Hermes's edge-case fixes and A's 2026 work).
- **Revisit if:** the lean core misses its wave 1 exit test by more than two waves of effort, or Hermes
  splits out a small, dependency-free core library that Exo could depend on without inheriting the rest.

## D-011 (2026-10-12) Store v2: hafiz format absorbs Arif and Eldunari
- **Decision:** adopt `docs/STORE-V2.md`. One format, three kinds of store: person (hafiz), agent (Eldunari
  anima), group (Eldunari nexus); agent and group stores nest inside the person's store so one folder can
  move to a device. Layers: profile, router, typed nodes, external items (Arif, Postgres to one SQLite
  file), signed ledger (plus Orda revision checks, seat leases, projections), derived indexes. Retrieval:
  router pins, BM25 plus embeddings merged, graph expansion, token budget. Migration runs on copies only;
  Eldunari history kept via `git filter-repo` plus merge.
- **Calls made by the lead (reversible, within standing approvals):** embeddings run locally with
  EmbeddingGemma 300M (Qwen3-Embedding 0.6B is the fallback); Arif's remote embedding calls stop. Mail is
  not re-ingested for now (it was deleted from Arif's DB; the mailbox file stays untouched). Backup is
  local only until A rules on a private remote.
- **Rejected:** keeping Arif in Postgres (cannot travel to a device); three separate stores per person
  (drift); remote embeddings (personal text leaves the machine).

## D-012 (2026-10-12) One learning loop: review, taste and hill-climbing eval
- **Decision:** adopt `research/LEARNING-LOOP.md` as `exo.learn`. One background reviewer that only
  proposes. Signals: corrections, edits, accepts, rejections, explicit asks, outcomes. Outputs: beliefs,
  taste rules, skills, memory, open questions. Every lesson is a typed proposal with confidence and evidence
  links; reverts append, never delete. A change is promoted only if it beats the current setup on a
  held-out per-user eval and passes regression gates (builds on `src/exo/eval.py`). Every step has a
  small-model or rules-only fallback. Learned taste shows inline in the chat with a link to the editable
  file (Command Code pattern, `research/UI.md`).
- **Calls made by the lead:** unguarded taste auto-promotes after evidence from two separate sessions,
  shown in the brief and reversible; taste syncs between A's machines only through the signed store.
- **Waiting on A (spend and model choice):** monthly ceiling for the nightly eval and climb; the judge
  model.
- **Rejected:** Hermes-style direct edits by the reviewer (no check that a lesson helps); Command Code
  style save-without-eval (same); keeping taste separate from memory and skills (the gap in PR #126940).

## D-013 (2026-10-12) Fuse UniiChat's context model into the core loop
- **Decision:** adopt the top picks in `research/UNIICHAT.md` (A's install, built on the same idea as
  optchat-memory). The wave 1 loop builds each turn as a fresh model call from the summary tree plus the
  recent verbatim tail, waits for pending summaries before a turn, and summarizes in parallel with separate
  queues for live chat and imports. Per-agent logs, history import and a `/goal` judge go to waves 2 and 3.
- **Licence:** the shipped UniiChat code's licence is not verified, so Exo reimplements ideas and copies no
  code or prompt text from it until the licence is known.
- **Rejected:** recall as an add-on to a growing transcript (the Hermes model; it degrades on long sessions).

## D-014 (2026-10-12) One surface: terminal feel, app power summoned by the model
- **A's idea (verbatim in `~/hafiz/nodes/work/exo.md`):** "the core typing and chat area feels just like a
  terminal but all the extra ui elements can be intelligently pulled up by the model when
  appropriate/relevant."
- **Decision:** Exo ships one surface, not a CLI and a separate app. The base is a terminal-like prompt and
  transcript (opencode / Command Code simplicity: `/` `@` `!`, one palette). Richer elements (diff viewer,
  task board, approval card, taste card, session tree, charts, file preview) are panels the model opens
  with a `ui` tool, which emits a `ui.panel` protocol event; every client renders what it can (the web and
  desktop clients render full panels; the terminal renders a compact form or a link; a device renders a
  card or voice). The user can always open or dismiss a panel by hand, and panel choices feed the learning
  loop (an ignored panel teaches as much as an opened one). Deciding when to show a panel is a
  decision-model job (D-015).
- **Build:** the `ui.panel` event and `ui` tool land in wave 3 with the clients. Wave 1's protocol stays as is.
- **Rejected:** separate CLI and app products (two UIs to learn, the norm we want to beat); an app that shows
  every panel at all times (Hermes-style clutter).

## D-015 (2026-10-12) Decision models: a `decision` role, local Laya by default, a custom exo-decide later
- **Decision:** adopt `research/DECISION-MODELS.md`. Picking a tool, panel, model or label, gating, and
  the learning loop's classifications go to a `decision` role in the router. Users choose the provider:
  local Laya (default, free, private), TypeSafe Jev, Perplexity pplx-decider, or OpenAI Decisions.
  Decision models return probabilities, not text, so a generative model still writes tool arguments.
  Every decision is logged to the ledger so the learning loop and eval can score it. A decision model may
  make a gate stricter, never looser. Exo will ship a fine-tuned Laya ("exo-decide", Apache 2.0 allows it),
  trained on users' own verdicts, rules and open-weight teachers. Never on Jev or OpenAI outputs: their
  terms forbid training other models on them.
- **Calls made by the lead (reversible):** local Laya is the default for every user; no rented GPU and no
  Claude-written training data until a terms check and a free-path measurement; exo-decide weights stay
  private until they beat base Laya and Jev on the generic held-out set.
- **Waiting on A (spend, model choice, publishing):** the decision provider for his own seats; a monthly
  cap for decision API keys (at current prices 10k decisions a day cost under $1); later, whether to
  publish exo-decide on Hugging Face under aska-digital.
- **Rejected:** a frontier model for every small decision (cost, latency); Jev as exo-decide's teacher
  (terms); Perplexity's 27B open model as the shipped default (needs about 49 GB of GPU memory, no device path).

## D-016 (2026-10-12) Agent wire: SYM-2P lines for machines, English for models and people
- **A's ask:** "project exo should integrate this tech, or a better implementation of its concept, in its
  backend systems for interagent communcation, delegation, and wherever else appropriate/relevant"
  (aska-digital/protean-sym2p).
- **Decision:** adopt docs/AGENT-WIRE.md. Agent records and transport use SYM-2P/2 lines with Exo's
  vocabulary (`exo.wire`): quoted strings, opcodes 100 to 115 under a (100 1) marker, failed and cancelled
  states, typed Brief and Receipt, strict decode with safe-only repair, JSON fallback, negotiation, Ed25519
  signatures, one English renderer. Code writes and reads lines; models and people read only English; no
  model emits a line. First use: the `delegate` tool (`exo.core.agents`) with depth, concurrency, round,
  token and time limits, cancel down the tree, a tasks table and `task.updated` (English in detail, line in
  packet). Bus bodies accept Exo lines. Lessons, decision calls, handoffs and summaries are unchanged.
- **Why:** measured with real tokenizers, lines save bytes (30 to 48% vs JSON) but not model tokens (a
  receipt is 121 o200k tokens as a line, 83 as English, plus a 193-token key). The value is checkability,
  signing and replay. The bigger token win is what a receipt carries: Hermes JSON receipts with tool traces
  cost about 3.5x Exo English.
- **Lead calls:** core line budget test moved from 8k to 15k per STANDALONE.md section 6 (wave 3 target).
- **Rejected:** models reading or writing lines; Hermes JSON receipts; upstream v2 unchanged (no text
  fields); a new format from scratch.
- **Later:** cross-seat delegation over the bus, signed receipts in the ledger, child usage in parent
  totals, locking finished child sessions, async delegation and worktrees, upstream model eval (spend,
  needs A), proposing the additions upstream as v2.2.
- **Revisit if** upstream's model eval shows models do better reading lines.
