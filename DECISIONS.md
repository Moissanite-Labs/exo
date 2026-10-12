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

## D-017 (2026-10-12) Subagents run on the cheapest model that can do the job
- **A's ask:** "you should be putting the subagents on most cost effective models whenever they dont need
  the full power of the model youre on ... encode this into SOPs and also into the exo harness behaviours".
- **Decision:** the router has tier roles `fast`, `standard`, `deep` (fast falls back to standard then
  main; standard and deep fall back to main). The catalog maps them to Haiku 5.5, Sonnet 5.5 and Opus 5.5
  for Anthropic; users override. `delegate` gives each child a tier: scout fast, builder and verifier
  standard, critic deep, or the calling model's own `tier`. A child never runs above its parent's tier
  unless `delegation.allow_upgrade`. A failed child is retried once a tier up unless
  `delegation.escalate_on_fail` is false. Tier and resolved model go in the receipt and `task.updated`.
- **Why:** most delegated work is retrieval and simple implementation; a small model does it well at a
  fraction of the price. Judgment-heavy work keeps its tier; a failure buys one stronger retry.
- **Consequences:** the learning loop can score cost choices per task. A tier with no key, or one that
  resolves to the same model, falls back to main, so local setups behave as before. Wire opcodes 108 and
  116 added; old packets still decode. Tests build engines with `env={}` so a real key in the shell
  never causes spend.
- **Later:** tiers for aux side calls; tier auto-selection from past receipts.

## D-018 (2026-10-12) Provider agnostic; users choose models; default eval and decision models
- **A's rulings:** "exo should be model and provider agnostic"; "users config which models are used. by
  default, the decision model used is laya (locally run) and the evaluation model is same model as what's
  configured and being used in the harness/chat"; "i have a typesafe api as well so we can experiment with
  jev"; the site's repo link stays dead for now.
- **Decision:** every role (main, aux, decision, eval, tiers) is user config. Defaults: `decision` = local
  Laya, `eval` (judge) = the `main` route. Providers added beyond Anthropic: OpenCode Go and Nous Portal
  (A's main providers) and TypeSafe Jev as an opt-in decision provider for experiments. Keys come from
  env, then the OS keychain (service `exo`), then `~/.config/exo/keys.env` (mode 0600); never from chat.
- **Resolves:** the D-012 judge-model question (eval = main) and the decision provider for A's seats
  (Laya default, Jev for experiments). Still open: the eval monthly ceiling and a decision-API cap.

## D-019 (2026-10-12) Jev is the default decision model; Laya is the local fallback
- **A's ruling:** "lets use jev as default instead if laya quality is subpar".
- **Evidence:** base Laya on two probes: "Is deleting a production database reversible?" yes 0.62
  (confidence 0.04); printer-ink receipt filed under taxes 0.46. Jev: no 0.80; office-supplies 0.99.
  Laya's server warns its confidence is uncalibrated.
- **Decision:** default `decision` route = Jev, then local Laya, then aux, then main. The gate consults
  the decision model by default, tighten-only (D-015 unchanged). Amends D-015 and D-018's "local Laya
  default". exo-decide (a fine-tuned Laya) can take the default back only by beating Jev on the held-out
  set; never trained on Jev outputs (terms).

## D-020 (2026-10-12) Exo OS: a new branch of Exo, design first
- **A's ask:** "Exo OS. An OS form of the exo harness that replaces Omarchy entirely with an innovative
  AI-first operating system ... making it the foundation for the user's entire experience and
  interaction with the digital realm". Built by saga-minar on charminar.
- **Decision:** phase 0 is research and design only (BRIEF-07, folder `os/` on branch
  `exo-os/phase-0`): base system, shell, security, small hardware, roadmap and proposed decisions.
  Exo OS runs the same core and protocol as Exo; no fork.
- **Lead call:** nothing on charminar's live OS changes in phase 0. It is Shahid's working machine
  (CRM, WhatsApp, Jarvis). Where the OS first runs is A's call after the design.

## D-021 (2026-10-12) Wave 2 closed
- **Done:** the six plugins run on the Exo core (no Hermes imports in memory, context, gate, bus,
  command, orchestra); skills loader, MCP client, seats (one canonical format, docs/SEATS.md), `exo web`,
  `exo decide`, config.get/set, seat.list, approvals.list, session status. Root suite 513 passed; plugin
  suites green.
- **Acceptance (STANDALONE.md section 6), live on OpenCode Go (Kimi K3 main, GLM 5.3 Flash aux):** a
  turn-1 correction survived compaction and was quoted verbatim; `rm -rf` on a scratch path raised
  approval.requested and a deny blocked it; bus round trip verified (tests, signed both ways).
- **Open, carried to wave 3:** capture misses corrections phrased as "Important correction ... NOT X";
  the gate has no signing key so decisions are logged, not ledgered; confirm Jev answers gate calls
  live; usage.updated.model reports the configured route, not the one that served after fallback.

## D-022 (2026-10-12) Wave 3 plan: Textual TUI, three protocol adds, learn package
- **TUI stack:** Textual (Python), not a spike first. Reasons: one language and one install with the core,
  stdio boundary, `textual serve` gives a browser mode for free, and the device horizon favours no Node
  runtime (research/UI.md 4.7). The protocol is the boundary, so a TS client can join later. Escape hatch:
  if on charminar (low-power Linux) cold start is over 1.5 s or streaming a 2,000-message transcript
  stutters, spike Ink before wave 4.
- **Protocol adds (29 of 30 commands, 22 of 30 events):** `feedback.send` (accept, edit, reject,
  should_have_been: the main learning signal), `ui.respond` (opened, dismissed, acted, ignored), `schedule.edit`
  (cron jobs; a run is a fresh session reported through task.updated), and the `ui.panel` event (D-014;
  every panel carries a plain-text fallback). One command slot is left; new needs add fields, not types.
- **Learning:** a new `exo.learn` package carries the fused loop (research/LEARNING-LOOP.md section 5 and 6):
  capture, typed proposals, promotion policy, revert, and the eval additions. Eval judge = main route (D-018).
  A lesson that changes behaviour (skill patch, high-impact taste) is promoted only if it beats the
  incumbent on held-out cases and passes the regression gates.
- **Ownership and acceptance:** docs/WAVE-3.md.

## D-023 (2026-10-12) Wave 3 built; live acceptance passed; canary next
- **Built (6a82c41):** Textual TUI (`exo tui`), the web UI in exo-command, `exo.learn` (signals, fast loop,
  typed proposals, promotion, revert, nightly climb with held-out gate), scheduler, `ui` panels
  (docs/PANELS.md; subagents and scheduled jobs get no ui tool), and every D-021 carry-over.
- **Live acceptance (scripts/accept_wave3.py, OpenCode Go, 88 s, 6 main turns):** diff panel opened by the
  model; an edit verdict produced 3 proposals through the fast loop; an every-1m job ran and reported;
  `rm -rf` asked, deny kept the folder, the gate decision is in the signed ledger and verifies; Jev answered
  3 of 4 gate calls; usage names the route that served.
- **Linux audit:** saga-minar's fixes merged (#9 to #12, #14 to #16); Hermes is now opt-in in install.sh.
- **Open:** the canary day (A, seat `canary`, launcher `exo-canary`, a copy of the principal store so the
  real one is untouched); TUI footprint on charminar (#17, D-022 escape hatch); nightly learning trigger;
  brief "learned" section; climb candidates beyond add and drop.

## D-024 (2026-10-12) Lean acceptance: continual feedback, not a day-long gate
- **A's words:** "day done? im a lean methodology user, thats not how we operate. i will give you continual
  feedback and pointers as i use it."
- **Decision:** wave 3 has no "full day in the TUI" exit test. A uses the canary seat (agent exo-razi) every
  day and sends notes as he goes; each note is a small cycle (fix and ship, or log a design item). A
  separate session handles that feedback (`~/Work/handoffs/exo-canary-feedback.md`); product design and
  feature building continue in the main session. Promotion of lessons still needs held-out wins (D-022).

## D-025 (2026-10-12) Harness self-observation: trace, findings, re-verification
- **Why:** A asked the first agent (exo-razi) to evaluate its environment. It missed the biggest problems
  because it could not see how the harness performed: an 8 min 20 s first turn with no event saying why, no record of
  which route served a call or of fallbacks, a shown model that disagreed with the served one, a test suite
  run twice by a subagent, two tool errors, and `cost_usd` always null. A wants a recursively improving
  feedback loop built into the harness.
- **Decision:** one loop, record, read, report, verify (docs/TRACE.md). Two new events, `model.call` and
  `harness.error` (24 of 30), a `timing` summary on `turn.completed`, `is_error` on `tool.completed`.
  `exo.core.trace` reads a session into per-turn rows (old sessions are derived from timestamps) and applies
  eight anomaly rules with stable signatures and thresholds in config (`trace` table). Findings live in a
  `findings` table in sessions.sqlite, not a jsonl file: a dedup is one transaction, the server and the CLI both
  write, and a copy of the database carries the findings with their sessions. A finding marked fixed in a commit
  is re-measured: a recurrence on a harness at or after that commit reopens it, and quiet for 20 turns or 3 days
  verifies it.
- **Agent surface:** `exo_trace` and `exo_harness_report` (main agent only; subagents are covered by the rules).
  `/trace` and `/findings` are built-in commands over `command.run`, so no new protocol command was needed.
  The TUI shows the serving route and an open-findings count.
- **Not done:** pricing (`cost_usd` stays null for models missing from the catalog) is a separate fix; a
  provider time to first token is measured at the loop, not inside each provider.

## D-026 (2026-10-12) Peers and one ingress path
- **Why:** A watched two Claude Code sessions avoid a file collision by messaging each other and asked for it in
  Exo, and for Hermes agents too (docs/PEERS.md).
- **Decision:** anything outside the principal's keyboard goes through one path, `Ingress(source, sender, trust,
  body)` -> exo-gate policy (auto, hold, deny) -> deliver (steer, queue, wake) -> receipt, event, trace. Peers are
  the first source; the bus channel, scheduler and MCP Events plug in later (D-027) with only their transport.
  Policy is the receiving seat's: auto for the same principal, hold otherwise, deny for a bad signature; held
  messages wait 30 minutes for an approval card. A peer message is input, never consent: attempts to approve
  are refused and shown to the principal.
- **Protocol:** one command `peers` with actions list, send, deliver and receipt (the brief said
  `peers.list` and `peers.send`, but a second command would break the cap; this uses the last slot, 30 of 30, so
  the test now allows 30). Events `peer.message` and `peer.delivery` (26 of 30).
- **Hermes:** the exo-bus plugin gets a peers module. Mid-turn delivery uses `agent.steer`, not
  `ctx.inject_message`, because in the CLI inject lands on the interrupt queue and interrupts. The bus is not
  registered as a Hermes platform: that is a gateway adapter admitting bus text as user input, so it needs its own
  review.
- **Not done:** web client view, receipts for bus sends, held messages across restarts, live gateway test.


## D-028 (2026-10-12) One agent, one continuous memory
- **Why (A, verbatim):** "well if the harness is built according to my vision for the implemenation of the optchat
  memory system all sessions should technically be the same agent with continuous memory". Exo had taken the turn
  model (D-013) but kept sessions: a summary tree per session, `/new` as a blank mind, exo-memory's daily log as a
  second log reached through recall. Two windows of one seat were two minds.
- **Decision:** docs/MEMORY-MODEL.md. A seat is one agent with one append-only log (in sessions.sqlite, a position
  and hash chain per entry, `exo memory verify`) and one summary tree. Windows, channels, scheduled runs and peer
  messages are threads of it. A call is the agent's tree in a byte-stable system message, the thread's own tail,
  and stored notes of what other threads did since this thread last looked. `/new` is a new thread (same memory),
  `/new private` is logged but never shared, `/sessions` lists threads. One process writes an agent
  (`run/agent-<id>.lock`); windows without exod join the first window's core and take over if it closes.
  Subagents keep their own logs (`memory_zoom(subagent=...)`); their report reaches the parent as the delegate
  result. exo-memory reads the core log instead of writing its own. Existing sessions were imported once, in time
  order, with `origin=import`; nothing deleted.
- **Protocol:** still 30 commands: `session.create` takes `kind`, `SessionInfo` carries `agent` and `kind`. One
  event, `memory.summarized` (27 of 30): tree upkeep was showing as `context.compacted`, which read as context
  compaction in the trace and TUI.
- **Calls made by the lead:** messages stay stored per thread with an agent position on top, so undo, fork, tool
  repair and every session-keyed event keep working; undo hides log entries rather than deleting them. Recall moved
  out of the system message to just before the turn. Small spans (the 0bb212e floor) stay out of the system
  message and count toward the view budget, so they merge instead of piling up. Providers that key their cache on a
  conversation id get one id per agent. A steer that lands during or after the final reply now gets its own model
  call in the same turn. Peers between threads of one agent were left in place (harmless), not removed.
- **Live check (temp copy of ~/.exo, glm-5.3-flash, 25 turns):** a fact told in window A was answered in a new
  window B on its first turn with no recall call; two windows ran at once and each saw the other's fact at its next
  call; after `/new` the earlier facts were answered; after a restart the agent continued and the imported canary
  thread was listed; the log verified (104 entries). Cache: the 20-turn run read 18.8% from cache against 58.7% in
  the old canary trace (kimi-k3), traced to a per-thread provider session id splitting the cache, plus the import
  backlog changing the prefix. Fixed after the run; the fixed code was not re-measured live at 20 turns (turn
  budget). Offline, on the same 20-turn script with an ideal prefix cache, prefix reuse is 84% against 93% before
  (77% against 79% with recall).
- **Not done:** a live 20-turn cache measurement on the fixed code; importing exo-memory's old episodic log (it
  holds copies of the same sessions); summaries for imports are slow on glm-5.3-flash (about 75 s per leaf).

## D-029 (2026-10-12) jevgrep as code_find; native port later if the measured win holds
- **Why:** "where is X decided" questions cost an agent many grep and read rounds. dzhng/jevgrep (MIT, `jg`
  0.8.0) asks the Jev decision model, which Exo already uses, to pick files and verbatim excerpts.
- **Decision:** a low-risk `code_find(question, path)` tool that shells out to `jg` (installed, else
  `npx -y @dzhng/jevgrep@0.8.0`). Backends: TypeSafe Jev with Exo's jev key, then local Laya through jg's custom
  endpoint. Hidden when Node 22+ or the package is missing, with the reason in `exo models`. Available to the main
  agent and subagents. An Exo skill adapted from theirs, with credit. Docs in docs/TOOLS.md.
- **Key:** `jg` ignores key environment variables and reads only a saved config, so each call writes a throwaway
  config in a 0700 temp dir, feeds the key to `jg auth --stdin`, searches, and deletes it. The key is never in argv,
  the child environment, a log or a result.
- **Measured (2026-10-12, Exo repo, 4 questions):** the right files each time, 9 to 19 s per call. Against a
  glm-5.3-flash subagent with only grep and read_file, code_find used fewer tokens (about 4.5k against 8k input)
  and fewer tool calls, but took longer in wall time and was not more correct on this small, well-named repo; the
  model also miscounted line numbers inside excerpts. Two questions is a weak sample.
- **Next:** a native port (Python, Exo's own decision route, no Node) only if more measurement shows a win on
  larger or less tidy repos. **Not done:** per-call spend accounting, a jg result cache owned by Exo.


## D-030 (2026-10-12) Onboarding: an agent-led /intro, and a tutorial mode of client-side tips
- **Why (A, verbatim):** "want to add a tutorial mode that users can toggle, like the one many games have ... so
  that when users are new to the software it guides them through things and gives little hints/explanations
  about everything. i also think there should be a slash command like slash intro or something where the exo
  agent gives the user an orientation of what the harness is, what features it has, how to use it, etc. because
  theres a lot of new unique and unfamiliar aspects. for example many users will be used to having to manage
  compaction manually or be aware of context limits. the slash intro orientation should also just automatically
  trigger when a user runs the exo harness for the first time".
- **Decision:** TUI, one skill and config only; the protocol stays at 30 commands.
  - `/intro` sends a normal user turn (shown as "/intro"). The agent loads the `exo-intro` skill and gives a short,
    staged, interactive orientation, tailored to what the user is used to, ending with `/tutorial`. The facts live
    in `skills/exo-intro/SKILL.md`; a test requires every slash command in `/help` (TUI table and core commands)
    to appear in it.
  - First run: `$EXO_HOME/state/onboarding.json` has no `onboarded` value, so `exo-tui` runs `/intro` and turns
    tutorial mode on. The marker is written when the intro turn starts (the `turn.started` event), not when it is
    sent. Skips: `--no-intro` (also records the home as onboarded), `EXO_NO_INTRO=1` (no write), `--resume` and
    `--session`. Only the `exo-tui` entry point asks for it (`ExoApp(auto_intro=True)`), so tests, `exo serve`
    and headless runs never trigger it.
  - Tutorial mode: `/tutorial on|off|reset`, saved as `tutorial.enabled` in config.json. Tips are in
    `clients/tui/exo_tui/hints.toml`, drawn by the client as dim "tip:" lines: no model tokens, not in the
    transcript the model sees, each once (seen list in onboarding.json), at most one per turn. 11 moments: first
    message, first memory summary, first approval, first steer, first Esc, first fallback or route change, first
    peer message, first `/new`, first finding, first slow turn (60 s, approval waits excluded), first `code_find`.
  - While on, the core appends one line to the stable system text (`TUTORIAL_LINE`, last, after the memory block
    and before the summary tree). Off, the text is byte-identical to before (tested); a toggle costs one cache miss.
- **Calls made by the lead and by me:** content in a skill, not a canned screen, so the user can ask questions and
  the text is reviewable; tips client side so they are free and cannot pollute memory; a tip that loses its turn
  to another is not used up; the line sits after the memory block rather than after the tree because the tree
  view is built by the context engine, and changing that would touch every caller for a once-per-toggle miss.
  `--no-intro` writes the marker so an existing user is asked to skip once, not forever.
- **Rejected:** a new protocol command for onboarding state (cap is full); model-written tips (cost, and they
  would enter memory); tracking seen tips in the core (a second client would then show them again for no reason).
- **Not done:** the web client has no tips; upgraders without the marker see the intro once (pass `--no-intro`).

## D-031 (2026-10-12) UI/UX overhaul, terminal half: make the TUI look like the site's terminal figure
- **Why (A, verbatim):** "UI/UX needs an overhaul. the project website at exo.askaconsult.com gives such a
  futuristic feeling - our ui/ux should be deliver on it".
- **Decision:** the language already exists on the site (`figure.term`, `style.css`), so the TUI takes it
  as is instead of inventing one. Presentation only: no protocol or core change, 30 commands, same keys.
  - A Textual theme from the site tokens (`exo_tui/theme.py`), with a test that fails if it drifts from `style.css`.
  - Instruments: brand bar with a live dot, a status strip (served model and the fallback it took, memory gauge,
    cache share, cost, findings), a wire with a travelling packet for data in flight.
  - Restyled transcript, one-line tools with elapsed time, approval, taste, finding, peer and `/context` panels
    with key chips, `memory.summarized` as a one-line cold event, tips, an empty-start card, a short skippable
    boot sequence, a block caret and a visible steer state.
  - `EXO_REDUCED_MOTION=1` turns motion off, `NO_COLOR` paints nothing. Details and screenshots: `docs/UI.md`.
- **Calls made:** the boot sequence is opt-in (`boot_anim`, set only by the entry point) so tests and headless
  runs never see it; "memory verified" is not claimed because the core sends no verification result, the line
  says `ready`; the served route stays in the strip after `usage.updated` (which carries the requested route);
  diff `+` and `-` keep the terminal's own green and red; the web client is a separate change.
- **Not done:** a `[?] explain` key on approvals (it would be new behaviour); a visual check of `NO_COLOR` in a
  real terminal (the SVG export cannot show the terminal's default colours).


## D-032 (2026-10-12) What exo-razi asked for: background jobs and principal capture (Part A); tools, trace and commands (Part B)
- **Why (A asked exo-razi, the canary Exo agent, what it would want; the brief is ~/Work/handoffs/exo-razi-asks-brief.md):**
  of seven asks, the two that hurt most in real use were a shell that holds the turn (a build or a long test run) and
  the principal's words reaching memory by discipline instead of by mechanism.
- **Item 1, background shell jobs (docs/JOBS.md).** `bash(background: true)` returns a job id at once; output goes to
  `$EXO_HOME/run/jobs/<id>.log` (capped, one rotation); `jobs`, `job_output(id, tail, grep)` and `job_kill(id)`. A job
  belongs to the agent, not the turn: Esc stops the turn, never the job. On exit the owning thread gets a completion
  notice through the ingress path peers use (`core/ingress.py`, a `job` source): steered into a running turn, else
  queued for the next one; a turn starts only if the seat allows (`[jobs] wake`, same rule as peers: off while a client
  is attached). Limits: 4 concurrent per agent, 2 h wall, 5 MB output. Jobs end with exod; after a crash the next
  start reports them `lost` (the process is not touched: a pid can be reused). The gate classifies the command exactly
  as foreground bash (a test compares classifications and decisions).
- **Item 4, principal capture as a mechanism (docs/MEMORY-MODEL.md).** (a) The summary template has a verbatim
  `Principal Directives` section, and a harness check restores any ruling or correction the model dropped; (b) before
  a leaf with principal messages is summarized, the span is offered to the memory provider's `capture_principal`
  (exo-memory: the signed ledger) and the leaf records that they were captured; (c) `/context` shows the uncaptured
  count and `/compact` captures first.
- **Protocol:** commands stay 30. One new event, `job.updated` (28 of 29), with a `state`, not the two the brief named
  (`job.started`, `job.completed`): the cap is tight and `task.updated` is the precedent. The start is state `running`;
  the end state, exit code and how the notice was delivered are in the second event. `/context` and `/compact` stay
  `command.run` built-ins; capture adds no event.
- **Calls made by the builder:** reuse of `EngineTarget` through a `JobTarget` subclass rather than a second delivery
  path; the notice skips the ingress consent regex because the harness wrote it (the body is framed as data); a job
  killed by `job_kill` sends no notice; subagents cannot start jobs; lost jobs are not killed on restart; capture marks
  positions in a new `captures` table because the log's hash chain must not change, and the `summaries` table gained
  `principal` and `captured` columns; imports, scheduled prompts, peer messages, job notices and bare acks are not
  "principal messages"; a hook that fails leaves messages uncaptured and does not stop summarizing; the capture hook
  runs after `on_pre_compress` so the ledger gets correction targets from the full span before the strict pass.
- **Not done (Part A):** the web client has no job view; Hermes has no job tools; `capture_principal` exists only in
  exo-memory (a seat without a principal store shows its uncaptured count as never captured, which is true).
- **Part B (items 2, 3, 5, 6, 7).** No new protocol command (the cap of 30 stays).
- **2. `exo_get(id, body_only)` in exo-memory, not an `expand` flag on `exo_recall`.** Recall prints
  `summary only; exo get <id>`; the agent now has the tool that line names. A separate tool because recall is a
  search under a token budget and `expand` would turn it into two verbs with two result shapes; a read by id needs
  no query and no budget; the name matches `exo get` so what the agent reads in recall is what it types. Same
  store and same access as recall (the seat's principal store, nothing else); a missing id returns near matches
  and rereads the store once; output cut at 40k characters.
- **3. `/handoff [path]`, agent-led like `/intro`.** The core answers `command.run` name `handoff_facts` (an
  unlisted built-in, so no protocol command): thread, git state of the cwd, open findings, running tasks,
  the latest tree summaries (the decisions), the last three trace rows, the default path, and one key per entry of
  `CoreEngine.handoff_sources`. `jobs` is the pre-seeded hook (empty list): Part A registers
  `eng.handoff_sources["jobs"] = fn(session)` and `/handoff` lists open background jobs with no other change; a
  source that raises is reported and skipped. The TUI sends one user turn (shown as "/handoff") with the facts and
  the path; the agent loads the `exo-handoff` skill (template: goal, state, done, decisions, next, blockers, open
  jobs and tasks, paths) and writes the file with its normal write tool, so the gate sees it. Default path
  `~/Work/handoffs/<seat>-<date>.md`; a folder argument gets that file name inside it.
  The client needs only a row in its command table and `exo_tui/handoff.py`.
- **5. Trace thresholds measure the harness.** `turn.slow` now fires on wall time minus tool time and approval
  waits (model plus overhead), 60 s by default, bug at 5 times; a new `turn.wall` (papercut) caps total wall at
  30 min (`turn_total_wall_s`). A seat's `[trace]` table overrides config.json key by key (engine and
  `exo trace` both read it). Old sessions without a timing summary subtract the recorded tool durations. Existing
  `turn.slow` findings keep their signature, so they count and verify as before.
- **6. Cost in the trace.** Per call and per turn (subagents included), in `exo trace`, `/trace` and `exo_trace`
  (`--calls`, `calls`). The figure is the usage the router priced from the catalog entry of the serving route,
  or the provider's own. `unknown` when unpriced, never `$0`; a mixed turn shows `$x+`. Two fixes it needed: the
  router charged cached tokens twice for OpenAI-style providers (their `prompt_tokens` includes the cached part),
  and config could not set a price at all, so `models.models."provider:model"` now overrides the catalog. The
  catalog has no price for the subscription routes (opencode-go); they read `unknown` until a price is set.
- **7. Cwd guard and default cwd.** One warning line (a `notice`, user only, not in the model's context) when a
  thread starts in a folder that is not inside a git repo, and again, once per folder per thread, when a shell
  call works in one (its `cwd` argument or a leading `cd X &&`). A seat sets `[session] cwd`; it beats
  config.json's `cwd`, and a missing folder is ignored and said so. A persistent `cd` does not exist (each shell
  call is its own process), so the guard watches the folder a call runs in.
- **Rejected:** `expand` on recall; a protocol command for handoff; writing the handoff file from the core
  (it would bypass the gate); appending the cwd notice to bash results (changes tool output for every test and
  every model).
- **Not done:** prices for subscription routes; cost in the TUI status line; a git check that runs `git`
  (the guard walks up looking for `.git`, so a bare or broken repo counts as a repo).

## D-033 (2026-10-12) Approvals bound to the exact text, who may approve, and an outbound scrub
- **Why:** orda's review (P0-4, P0-5). An "always" answer covered any later text under the same rule, was lost
  on restart, and any local client could answer any approval. A public repo once shipped with task ids, profile
  names, model strings and a home path in it (lrn-042); the gate's posting checks were empty by default.
- **Binding:** for an outward act (post, message, email, social, payment, account, or anything public or
  irreversible) the gate hashes the exact text, title, target, tool and arguments. The hash is the approval's
  `binding`; an edit gives a new hash and a new ask. A client may echo the binding in `approval.respond`; a
  mismatch is refused as `stale`. Binding to the full arguments, not only text and target, means nothing else
  can change under an approval either.
- **Who may approve:** `policies/approvals.yaml` maps category to `principal`, `seat:<name>` or `role:<name>`.
  Default is principal only, so single-user installs behave as before. The principal can always answer: their
  consent is the root. Peers still cannot (D-026). The list rides on `approval.requested` as `approvers`; the
  engine refuses other answerers as `forbidden`. Identity is the `client` string a local client declares, so it is
  as strong as the socket's permissions. A real setup where a lead seat approves public posts is expressible as
  `github_post: ["role:public-reviewer"]` with `roles: {public-reviewer: ["seat:<lead>"]}`; it is not configured
  live.
- **Grants:** "always" becomes a grant with a scope (category plus exact target; outward also the text hash), an
  expiry (30 days, outward 7) and a ledger row, kept in the principal store (`state/approval-grants.json`, 0600),
  else in exod's home. The store, not exod state, because a grant is the principal's consent and belongs with
  their policies and ledger, and it then holds for every seat of that principal. The gate checks grants itself
  and tells the core (`persist`) not to keep its own unscoped in-memory allow. Credentials never get a grant.
- **Scrub:** posts to public targets are swept for seat names (seat files, bus registry, store agents), home
  paths, model ids from the catalog and internal ids, plus the user's own list. A hit holds the post with each
  violation listed. Plain-word names match only as whole tokens; distinctive names (hyphen, underscore or digit)
  also match inside words and with any separator. The footer stays empty in code; the oversight line is an
  example in the template. `footer_exposures` can limit the footer to public targets.
- **Protocol:** no new command or event. Optional fields: `ApprovalRequested.approvers`, `.binding`;
  `ApprovalRespond.binding`.
- **Rejected:** an unscoped "always" for outward acts (one approval would cover any later text); keeping grants
  in the core's memory (lost on restart, no expiry, no audit); a hard-coded footer (that is one principal's
  branding); substring matching for every name (a seat called "canary" would hold every post that says canary
  release).
- **Not done:** signed answers from seats (a seat key over request id, decision and binding) to make seat identity
  stronger than a declared string; escalation along an org graph (P1-2); TUI and web do not yet show
  `approvers` or echo `binding`; a command to list or revoke grants (delete the file or the row for now).
