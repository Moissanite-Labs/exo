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
  an inventory folder (kept outside the repo, see D-042) and returns a short summary.
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
- **Decision:** The inventory verdicts (kept outside the repo, see D-042) stand (core 18, plugin 13, skill 18, patch 5, upstream 9,
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
  who uses the Linux seat's GitHub account. Batched in the phase report.
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
  interaction with the digital realm". Built by a builder agent on the Linux test machine.
- **Decision:** phase 0 is research and design only (BRIEF-07, folder `os/` on branch
  `exo-os/phase-0`): base system, shell, security, small hardware, roadmap and proposed decisions.
  Exo OS runs the same core and protocol as Exo; no fork.
- **Shared executor interface (2026-10-12):** the core loads a named installed `exo.executors` factory,
  freezes validated post-approval arguments and dispatches through its broker. Required mode refuses missing
  executors, unsupported MCP startup and failed required plugins. The OS supplies isolation and an audited
  trusted plugin inventory; imported Python code remains trusted. See `docs/EXECUTORS.md`.
- **Workspace subprocess closure:** required mode omits host Git handoff facts and uses filesystem-only
  completion, because workspace Git configuration can execute helpers. Cross-seat delegation requires a
  separate broker capability; local child setup passes tools through the shared registry.
- **Required gate audit (2026-10-13):** execution requires committed decision and approval evidence.
  Missing stores/keys, failed audit writes and trusted gate hook exceptions block before the executor.
  Required mode refuses tool-selected body-file host reads, including symlinks; ordinary gate behavior
  remains compatible. See `docs/GATE.md` "Required executor audit".
- **Approval and authority ordering:** pending approvals use unique call tokens separate from reusable
  rule keys. Policy acceptance is audited before pinning; required grant authorization is audited before
  persistence, with an explicit authorization-only record if persistence fails. Separate stores are not
  claimed to activate atomically. Concurrent approvals retain the correct session and decision reference.
- **Ledger authority (2026-10-13):** core writers open the ledger through one factory. With no authority
  configured it is the D-040 local key path unchanged: pinned keys, head anchor, `exo log verify`. With
  `EXO_LEDGER_AUTHORITY` set, an installed backend signs and commits each event and checkpoint before
  replying; verification replays its pinned identity read-only and never falls back to local keys. Enabling
  it on an existing store fails closed (unconfirmed local tail). There is no migration. The separate
  `exo-witness` transport is implemented but disabled by default. It starts only when invoked explicitly.
  No endpoint or receipt-delivery worker is enabled by core startup. Deployment and Exo OS delivery remain separate work. See `docs/LEDGER-AUTHORITY.md`
  and `docs/WITNESS-TRANSPORT.md`.
- **Lead call:** nothing on the Linux test machine's live OS changes in phase 0. It is someone's working machine
  (CRM, messaging, assistants). Where the OS first runs is A's call after the design.

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
  if on the low-power Linux test machine cold start is over 1.5 s or streaming a 2,000-message transcript
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
- **Linux audit:** the Linux builder's fixes merged (#9 to #12, #14 to #16); Hermes is now opt-in in install.sh.
- **Open:** the canary day (A, seat `canary`, launcher `exo-canary`, a copy of the principal store so the
  real one is untouched); TUI footprint on the Linux test machine (#17, D-022 escape hatch); nightly learning trigger;
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
  *Amendment 2026-10-12:* with slow routes a 6-call turn spends 150 s or more in the model, so a rule that counted
  model time fired on normal turns. `turn.slow` now means harness overhead only (wall minus tool, approval and
  model-call time), 30 s by default (`turn_wall_s`, same key and seat override). Slow providers get their own
  rule, `model.slow:<route>`, per call and per route, 60 s by default (`model_call_s`, with `model_call_routes`
  for a per-route limit). The TUI long-turn tip uses the same line. Existing `turn.slow` findings keep their
  signature.
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
  engine refuses other answerers as `forbidden`. A seat answers as `seat:<name>` and must sign the answer with its
  seat key over request id, decision, binding hash and seat name (`exo.core.approvals`); the key comes from the
  verified bus registry, else `<keys dir>/<seat>.pub`. Unsigned seat answers are refused. A principal answer is
  not signed: it is as strong as the socket's permissions. A real setup where a lead seat approves public posts is expressible as
  `github_post: ["role:public-reviewer"]` with `roles: {public-reviewer: ["seat:<lead>"]}`; it is not configured
  live.
- **Grants:** "always" becomes a grant with a scope (category plus exact target; outward also the text hash), an
  expiry (30 days, outward 7) and a ledger row, kept in the principal store (`state/approval-grants.json`, 0600),
  else in exod's home. Only the principal can create one: a seat may approve one exact text with `allow`, and
  its `allow_always` is refused. `/grants` lists live grants and `/grants revoke <id>` removes one with a ledger
  row; it is a plugin command reached through `command.run`, so no protocol command. The store, not exod state, because a grant is the principal's consent and belongs with
  their policies and ledger, and it then holds for every seat of that principal. The gate checks grants itself
  and tells the core (`persist`) not to keep its own unscoped in-memory allow. Credentials never get a grant.
- **Scrub:** posts to public targets are swept for seat names (seat files, bus registry, store agents), home
  paths, model ids from the catalog and internal ids, plus the user's own list. A hit holds the post with each
  violation listed. Plain-word names match only as whole tokens; distinctive names (hyphen, underscore or digit)
  also match inside words and with any separator. The footer stays empty in code; the oversight line is an
  example in the template. `footer_exposures` can limit the footer to public targets.
- **Protocol:** no new command or event. Optional fields: `ApprovalRequested.approvers`, `.binding`;
  `ApprovalRespond.binding`, `.sig`.
- **Rejected:** an unscoped "always" for outward acts (one approval would cover any later text); keeping grants
  in the core's memory (lost on restart, no expiry, no audit); a hard-coded footer (that is one principal's
  branding); substring matching for every name (a seat called "canary" would hold every post that says canary
  release).
- **Rulings (coordinator, 2026-10-12):** seats sign their answers; the principal can always answer; standing
  grants are the principal's alone; list and revoke as a `command.run` command.
- **Not done:** escalation along an org graph (P1-2); the TUI and web clients do not yet show `approvers`, echo
  `binding`, or sign as a seat (left for the UI work); no client helper beyond `sign_approval` for a seat to answer.

## D-034 (2026-10-12) Providers setup: one engine, three surfaces, keys write-only
- **Why:** a new user had no clean way to add a model key. Keys went in by hand (env, `security`, or a 0600
  file), `exo models` only reported, and the web client had no provider settings at all.
- **Engine:** `exo/providers_setup.py`. `rows` gives every catalog provider with status (key found and where,
  local server reachable, last live test, model count, default), never a value. `set_key` and `remove_key` write
  the Keychain on macOS, the Secret Service via `secret-tool` on Linux when present, else `keys.env` (0600, temp
  file, fsync, rename). `test_provider` makes one tiny call and returns latency and an error class. `set_default`
  does what `/default` does. Keys are write-only: only `set_key` takes one and nothing returns one. A new key is
  taught to redaction (`keys.remember_secret`) before it is stored, so even a failed store cannot leak it.
- **Surfaces:** `exo providers [add|test|remove|default]` (getpass, needs a terminal, its own parser that never
  repeats argv, so a pasted key is not echoed); the TUI `/providers` panel (client side, masked input, never the
  composer); the web Providers tab. No new protocol command: the protocol is full at 30. The core gets one
  `command.run` built-in, `providers` (status) and `providers reload <name>` (forget cached credentials), and only
  a provider name crosses the protocol.
- **Where keys are written:** in the client process (the CLI, the TUI, the web server), not in the core. The key
  then never enters a protocol message, an event, a session log or the core's memory; the core reads it from the
  store on reload like any other key.
- **Web security:** routes under `/api/providers` need a loopback peer, a loopback `Host` (DNS rebinding), a
  same-host `Origin` when sent (and `Sec-Fetch-Site` same-origin or none), and a per-launch token. `exo web`
  prints the token in the URL fragment; the page keeps it in `sessionStorage` and sends `X-Exo-Token`. Threat model
  in `docs/PROVIDERS.md`.
- **Agent:** no tool exposes this. The gate classes `exo providers add|remove|test|default`, `secret-tool`, and
  shell or code naming `/api/providers` or `providers_setup` as `credentials` (always ask, no grant).
- **Rejected:** a protocol command to set a key (the key would ride the protocol and could reach event logs);
  accepting a key as an argument (shell history, `ps`); `security add-generic-password -w VALUE` (the value is in
  argv; `security -i` reads the command from stdin instead); storing the token in `localStorage` or a cookie
  (outlives the launch, a cookie rides cross-site requests); returning a masked key or its last four characters
  (nothing about the value comes back).
- **Not done:** a remote core (`--url` on another machine) still needs keys on that machine; the TUI and web store
  on the machine they run on. Rotating several keys per provider (`key_env` lists) writes the first name only.

## D-035 (2026-10-12) Typing `exo` opens Exo: bare command, installer on PATH, `exo update`
- **Why:** the principal wants a live Exo to start the way `hermes` does: type the name. Before, `exo` with no
  arguments was an argparse error, the client was `exo tui`, and the installer left `exo` inside a venv.
- **Routing:** `main` sends no arguments, or a first argument that starts with `-` and is not `-h`, `--help` or
  `--version`, to the terminal client with every argument passed through (`exo --resume`, `exo --seat X`,
  `exo --no-intro`). `exo resume` is a real subcommand for `exo --resume`, extra flags passing through. Everything else is the old subcommand parser, so `exo models` and the rest are unchanged and
  `exo -h` lists both. No flag lives on the top-level parser, so nothing can clash with the client's flags.
  `exo --version` prints the package version, plus the git sha when Exo runs from, or was installed from, a checkout.
- **First run:** the client already noted a missing provider (D-034) and sent `/intro` (D-030). Those two now
  compose. With no working provider the providers panel opens and the intro waits, because the orientation is an
  agent turn and fails without a model. Closing the panel with a provider ready sends the one intro. With a
  provider ready, behaviour is as before.
- **Installer:** keeps the dedicated venv at `$EXO_HOME/venv` (the plugins and the optional Hermes need it), now
  installs `exo[tui]`, and links `~/.local/bin/exo` to it (`--bin-dir`). If that folder is not on PATH it prints
  the line for the user's shell and does not edit rc files unless `--modify-path` is given, and then only once. A
  foreign file at the link path is left alone. It writes `install` (mode, source, checkout, venv, python) to
  `config.json`. It never reads or writes keys. Re-running pulls a URL source and reinstalls, which is the upgrade.
- **`exo update`:** reads `install`; pulls the checkout and re-runs `install.sh` (which refreshes the venv and the
  plugins); or pull and `pip install -e` for a development checkout; or `uv tool upgrade exo`. Prints old -> new and
  runs a self-check: import, `exo.protocol.export --check`, `exo models`. `--dry-run` prints the steps. No protocol
  command (the protocol stays at 30).
- **Rejected:** `uv tool install` as the installer's only path (the plugin copy and the optional Hermes need the
  fixed venv; it stays an accepted layout for `exo update`); editing rc files by default (a silent change to
  the user's shell); a top-level `--seat` or `--resume` on the main parser (clashes with subcommand flags).
- **Not done:** Windows; a published package on PyPI (the git URL is the source until then); an
  `exo uninstall`.

## D-036 (2026-10-12) `exo` and `exo web` in any order share one core
- **Why:** a live bug. `exo web` connected to `run/exod.sock`, found no `exo serve`, and the chat page showed
  `ConnectError: [Errno 2] No such file or directory`. The terminal client had already started the core in process
  and held the agent's writer lock (D-028), so the two clients only shared an agent if the user ran `exo serve` first.
- **Found:** the in-process core already serves `run/agent-<seat>.sock` (the same HTTP protocol) so other windows
  join it. The web client never looked there. A second core does not forward: it fails on the lock (`WriterBusy`).
- **Decision:** `exo.ensure_core.ensure_core`: use `exod.sock` if it answers, else `agent-<seat>.sock`, else start
  `exo serve` detached (new session, same env, log `logs/exod.log`, pid `run/exod.pid`) under a file lock
  (`run/exod-start.lock`), remove a stale socket file first, wait up to 40 s for it to answer, and fail with the log
  tail. If the writer lock is held by a process that serves no socket, say so and name the way out, never start a
  second core. `exo web` runs this in its feed loop, so it also re-resolves when the core goes away (a terminal window
  that held it closed): it re-joins or starts exod. The terminal client's `_open` now prefers a live exod, and a stale
  `exod.sock` no longer counts as a server.
- **Lifetime:** exod stays running until `exo stop` (or `exo serve --stop`). Simplest to explain and safe for the
  work a core does with no window open (background tasks, channels, schedules). Idle exit rejected: it can end a
  core between a client's check and its connect, and quietly kill long jobs.
- **Not changed:** the terminal client still runs the core in process when none exists (it does not spawn a daemon:
  tools and approvals stay tied to its window). Web with `--socket`/`--url` never starts anything. Page errors are
  sentences (`core_message`), not exception text. No protocol change.
- **Not done:** `--in-process` flag (the client already falls back in process); Windows.

## D-037 (2026-10-12) Completion and guidance in the composer
- **Why:** the principal asked for command autofill, suggestion and guidance, "a better version of what Hermes CLI
  has". The reference is the Hermes CLI (`hermes_cli/commands_completion.py`): a prompt_toolkit completer with a `/`
  menu, subcommand lists, ghost text from history and `@` file search. We studied it and built our own.
- **Decision:** one engine, `src/exo/complete.py`, pure Python with no UI: fuzzy ranking (exact, prefix, word start,
  contains, letters in order, then a word of the description), argument slots per command (`plan`), a `FileIndex` for
  `@`, `closest` for typos, `History`, `ghost`, and a `Guide` with the tip rules. The terminal client draws it
  (`Composer.key_hook`, `CompletionMenu`, ghost text drawn after the caret). The web client has the same logic in
  `static/complete.js` (checked against the Python in a test that runs node) and draws it in `composer.js`.
- **Sources of truth:** the command list is the client's own table plus the core's `commands.list`, asked once at
  start. Models come from `models.list` (the picker's source, so unsupported models are already left out), threads
  from `session.list`, grant ids from `command.run grants`, the default handoff path from `handoff_facts`, provider
  names from `providers_setup.rows`. Each is fetched on first use and cached 30 seconds.
- **Safety:** `@` lists names only, never reads a file, stays inside the working folder (no `/`, `~` or `..`), and
  drops anything `gate.is_sensitive_path` names. On the web it is a server route that needs the same loopback, Host
  and page-token guard as the key routes. History is per seat, mode 0600, 500 lines, and never keeps a line that
  looks like a key or password.
- **Guidance:** one dim line in the empty composer (the placeholder), from local rules over state the client already
  has: first run, a failed model call, a finished job, open findings, a slow turn, a long thread. At most one new tip
  per 25 seconds, each rule twice a session, none while a turn runs, a rule retires once you run its command, Tab
  fills it in. No model call. Off with `/tutorial off` (which also sets `guidance.enabled` false) or that config key.
  Under reduced motion there is no timer: it only updates on events.
- **Typo help:** an unknown `/comand` is not sent. The note says "did you mean /compact?" and the corrected line waits
  in the composer. With nothing close, the line goes to the core as before. The web does the same for its commands.
- **Rejected:** a model call to suggest commands (cost, latency, and the rules are enough); a second ranking in each
  client (they would drift); storing history in the browser (the web client keeps none across page loads, on purpose).
- **Not done:** file completion for bare paths (`./x`, `~/x`) outside `@`; `/findings <id>` (the core takes only
  `count`); the web client does not run the other slash commands, so its menu lists only what it runs.

## D-038 (2026-10-12) Gate default ask: a shell allowlist, the store is credentials, policy edits only tighten
- **Why:** the 2026-10-12 audit reproduced that the gate classified by literal program name. A heredoc, `python -c`,
  `G=gh; $G` or a shell function walked past it as `shell` (allow). The model could also rewrite its own policy file,
  grant store and ledger with an allowed write, and the plugin reloaded policy on mtime, so the rewrite took effect on
  the next call. Audit priorities 1 and 2.
- **Decision, shell:** an allowlist. A small lexer splits the line into simple commands and finds each program
  position. A command is `allow` only when every program is known and nothing is hidden. Anything else is the new
  category `opaque_exec` (asks): variables, `$( )` or globs in program position, script files, shells and
  interpreters running text, `make`, `npm run`, `npx`, `xargs`, `find -exec`, `eval`, `source`, functions, aliases,
  git and gh aliases, unknown programs. Where the gate can see inside it classifies the inner text too and keeps the
  stricter; the `indirection` signal makes `decide` ask even if the inner part only logs. An "always" on opaque code
  covers that exact command only. The principal can lower `opaque_exec` only by naming it in a category line or rule.
  The old program and prefix cases are gone; one classifier serves the core and the `exo-gate` plugin.
- **Decision, store:** `policies/`, `state/`, `ledger.sqlite*`, `keys/`, seat files and the sockets under `$EXO_HOME`
  or the principal store are on a protected list. Any write is `store_write`: a floor like credentials, no standing
  grant, principal-only (`approvals.yaml` cannot add seats), enforced in observe mode. Reads stay `read`; `keys/`
  reads stay `credentials`.
- **Decision, policy reload:** pin the policy at first load. A changed file is loaded and logged (`policy_change`
  row with file hashes) and each call is decided under both; the stricter wins. Loosening takes effect only after
  `/gate accept` or a restart. Chosen because it is simple and sound: a tool cannot loosen policy by any write path,
  seen or unseen, and the principal's own tightening still works at once. Rejected: an approval card for every
  policy edit (new protocol surface, and the principal's own editor edits would trip it); trusting mtime plus a trace
  event (a tool write that the gate missed would still take effect); detecting "made by a non-tool path" (background
  jobs run beside the turn, so the gate cannot tell who wrote a file).
- **Cost:** about 70 everyday commands are pinned as `allow` in `plugins/exo-gate/tests/test_default_ask.py`
  (`pytest`, `uv run pytest`, `git status`, `ls`, `rg`, `npm install`, `cargo test`...). New asks a user will see:
  `make`, `npm run`/`npm test`, scripts, `python -c`, unknown programs such as `kill` or `open`, and any write to the
  store. Each opaque ask is bound to the exact command.
- **Not done:** the known limit stays. A trusted program that runs project code (pytest and conftest.py, git hooks,
  package install scripts) can run code the agent wrote earlier. A sandbox is the fix. `/gate accept` goes through
  `command.run`; binding who may call it to the connection is the server identity work (audit priority 3).

## D-039 (2026-10-12) Approval identity from the connection; web token on every route; one secret scrub
- **Why:** the 2026-10-12 audit. (a) `approval.respond` took the answerer from its body: an empty `client` meant
  the principal, so a bash tool call (same user, socket 0600) could connect to the core's socket and answer its own
  approvals with `allow_always`. (b) `exo web` re-exposed the whole protocol and the event stream with no token;
  its only CSRF defence was Origin equals Host, which DNS rebinding defeats; `asgi.py` had no check at all. (c)
  Secret scrubbing for tool subprocesses was a suffix rule written three ways, and the claude-cli provider passed
  almost the whole parent environment.
- **Decision, identity:** the transport stamps a caller for every command (`exo/principal.py`, a context variable
  set by `Dispatcher.run` and cleared when the command returns, so a task it started does not inherit it). In
  process (the terminal client's own core, the web's in-process dispatcher) the caller is `principal:<label>`.
  On the Unix socket it is the principal only when the request carries the per-launch credential in
  `X-Exo-Principal` and the connecting process (`LOCAL_PEERPID` on macOS, `SO_PEERCRED` on Linux) is not a
  descendant of the core. Everything else, stdio and TCP included, is `local`. The credential is a random token
  in `<socket stem>.principal.key` beside the socket (0600, in the 0700 run folder), new per launch, deleted on
  stop, never in an environment variable. Clients that are the principal (`exo`, joined windows, `exo chat`, the
  web) read it per request, so they re-join a restarted core.
- **Decision, approvals:** a principal connection answers as the principal (`client` is a label: `by` is
  `principal:tui`). A local connection answers only as `seat:<name>` with the D-033 signature, and never with
  `allow_always`. `approver_identity` returns `unknown` for an empty or unrecognised name, and `unknown` may not
  answer. The attack test (`tests/security/test_approval_identity.py`) runs a background bash job that finds the
  pending approval and tries `allow_always` with every body and with the stolen credential: all refused.
- **Decision, web:** every `/api/` route and both event streams need the page token (`X-Exo-Token`, or `?token=`
  on the streams, since EventSource cannot set headers). Host must be `127.0.0.1:<port>`, `localhost:<port>` or
  `[::1]:<port>`, for the page too. Origin and Sec-Fetch-Site checks stay. Only the page shell is served without
  the token. The token stays per launch, in memory, delivered in the URL fragment that `exo web` prints and opens.
  `asgi.py` is deleted (unused).
- **Decision, environment:** one function, `exo.envscrub.scrub_env`, for bash, jobs and `code_find`: catalog
  `key_env` names, secret suffixes and words, `AWS_*`, `GH_*`, `GITHUB_TOKEN`, `*DATABASE_URL`, `*_DSN`, the bus
  identity, the core socket and credential variables, and a new seat `[tools] env_deny` (names or patterns).
  `env_allow` keeps a name; `env_deny` wins. The claude-cli child gets `minimal_env` (PATH, HOME, locale, terminal,
  CLAUDE_CONFIG_DIR, proxies, CA bundles) plus its switches.
- **Rejected:** dropping `EXO_HOME` from tools (the default path is guessable, and tools run `exo`); a token in a
  cookie for the event streams (rides cross-site requests); trusting the TCP bearer token as the principal (TCP
  has no peer process to check); a one-time bootstrap code for the page (more moving parts than the fragment buys
  today).
- **Limits:** a tool process is the same OS user. Code that reads the credential file without the gate seeing it
  (the gate classes a plain read as `credentials`, always ask; indirection is the gate branch's work) and also
  detaches from the core's process tree could still pass. The real fix is an OS sandbox for tool subprocesses.
  The page link sits in browser history and, briefly, in the browser launcher's argv. The env scrub is a
  heuristic.
- **Not changed:** the TUI approval card's focus behaviour (another branch is editing the TUI); noted for later.
  The echo engine (a test double) still accepts any answer.

## D-040 (2026-10-12) Who wrote it: authors on every message, pinned keys, an anchored ledger head
- **Why:** the 12 October audit (priorities 4 and 6). (a) `exo_note` let the agent write `kind="ruling"`, and
  consolidation and `principal_said` checked the kind, not the author, so an agent-written "always force push to
  main" became a belief proposal that promotion rule 1 would accept. (b) Per-turn capture got bare role and content,
  so a peer message or job notice framed as a ruling was saved as the principal's. (c) The bus channel turned any
  inbox envelope into `turn.send`: a principal turn, no hold or trust policy, reply posted back; and the relay's key
  was accepted as any seat's. (d) Ledger verify trusted the key inside each event, nothing passed `trusted`, and
  dropping the trigger, deleting the tail and restoring the trigger verified clean.
- **Authors:** the core stamps `meta.author` on every message from its way in: `principal` (a client's
  `turn.send`), `agent`, `tool`, `peer:<name>`, `bus:<seat>`, `job`, `delegate`, `schedule`, `system`
  (`src/exo/provenance.py`). Never from the text. `is_principal` needs `principal`. Every memory and learning hook
  gets the author and a `principal` flag per message; capture, consolidation, promotion, review signals and handoff
  pins read text as the principal's only when it is. The agent's `exo_note` keeps `ruling` and `answer` as a
  `note` of type `agent_note`; `exo_should_have_been` is signed as the agent. Ledger authors that are the principal:
  `principal`, the store's id, and ids in the key registry.
- **Bus:** the channel calls `peers` action `bus` (one more action, no new command). The core
  (`src/exo/bus/ingress.py`) checks the envelope against the seat's pinned key (pinned on first sight, refused when
  it changes), frames it as a bus message with author `bus:<seat>`, and runs the peer policy: `remote` is held by
  default. The channel posts a reply only for a turn that an auto-delivered message started. The relay signs with its
  own key only on a post it marks `{"relay": {"composed_for": seat}}`, and readers never count that as the seat.
- **Keys:** `$EXO_HOME/keys/registry.json` (0600) pins local signing keys (on creation in `keys/`), bus seat keys,
  and principal ids. `exo log verify` and `exo memory verify` pass it as `trusted`: an unpinned key fails, and so
  does a seat signing as another pinned name. `exo keys` lists and pins.
- **Head anchor:** each append writes seq, count and chain hash to `<ledger>.head` and
  `$EXO_HOME/keys/heads/<hash>.json` (0600). Verify fails when the ledger no longer reaches an anchor or its hash
  there differs, or when a trigger was missing on open. Append refuses to write past a broken anchor.
  `exo memory verify` now checks every store's ledger too.
- **Migration:** a home without a registry gets one on first open, pinning every key already in `keys/` and every
  store under `principals/` (that moment is trusted as is). A ledger without anchors verifies as `anchored: false`
  and is anchored by its next append (`exo log anchor` does it now). Messages stored without an author are read by
  their tags as before. Rulings the agent wrote before D-040 stay in the ledger but are skipped
  (`not_principal`); proposals already made from them are not withdrawn and need a look.
- **Rejected:** signing every principal message with a separate principal key (the agent's seat records the
  principal's words, so the key cannot prove who typed them; the author from the ingress path is the honest
  claim). A hash of the head in the ledger itself (a truncation removes it too). Publishing the head to the bus
  (not every home has one). Refusing bus messages outright (the hold path already exists).
- **Not covered:** a process running as the same user that rewrites the database and both anchors, or edits the
  registry, or sends `turn.send` on the seat's socket (that counts as the principal until approval identity is
  bound to the connection, a separate change). The gate is what asks before writes to `keys/` and the store; the
  registry, the anchors and `ledger.sqlite.head` belong on its sensitive-path list.

## D-041 (2026-10-12) Audit correctness fixes: eight bugs no test caught
- **Why:** an external audit read `src/exo/core` and found bugs the suite passed over. Each got a failing test first.
- **Loop:** a provider error after text was shown now stores that text (marked `interrupted` and `error`) like an
  interrupt does. A stream that closes cleanly without a finish event or stop reason is the retryable error
  "stream ended without a response", even after text.
- **Memory:** undo deletes the agent-tree summaries (`agent:<name>`, keyed by log position) that cover undone
  entries, and a summary already running cannot bring one back. A failing summary is retried with backoff up to
  three times, then the span stays verbatim, marked `skipped`, with one `harness.error` event.
- **Scheduler:** a failing tick is recorded and survived; an unexpected exit of the loop is recorded and noticed.
  Scheduled runs are ordinary turns: same context tree, memory, soul refresh and turn hooks, with the job's model
  and tools.
- **Delegation over the bus:** the brief goes as `kind=directive`; the wire grammar has no `task`, so a real relay
  answered 422. The test bus now rejects kinds outside `exo.bus.wire.KINDS`. The receipt kind in the design is
  `report` for the same reason.
- **MCP:** `ToolResult` results get the character cap; long or odd tool names get a hash suffix and a clash is
  refused, not replaced; remote descriptions are marked untrusted third-party text.
- **Not done:** terminal event-loop blocking and approval focus (another branch); gate, approvals, capture and
  ledger items (other branches).

## D-042 (2026-10-12) Repo hygiene: privacy, licensing, CI
- **Why:** an outside audit found that D-004 (nothing in the package names the principal) was not met, that no CI
  or lint ran, that the wheel left out a licence, that some imports were undeclared, that the plugin tests could
  not be collected together, and that the site and docs claimed things the code does not do.
- **Decision (privacy):** the early inventory and the project brief moved out of the repo into the handoffs folder.
  `docs/STORE-V2.md` lost its machine detail and says "Design, not built". Tests no longer hold a deny-list of
  names: they load one from an untracked file (`$EXO_PRIVACY_DENYLIST` or `~/.config/exo/privacy-denylist.txt`) and
  skip without it, and a generic check (home paths, email addresses) always runs (`exo.privacy`,
  `tests/test_privacy.py`). `scripts/deploy-site.sh` reads the Vercel team and token file from the environment or an
  untracked config. `.gitignore` covers env files, keys and `auth.json`.
- **Decision (packaging):** the wheel ships `LICENSE` and `LICENSES/*` (`license-files`); a notice covers the embedded
  github-markdown-css; `httpx2`, `starlette` and `rich` are declared in the extras that import them; the terminal
  client no longer depends on the PyPI project named `exo`; `uv.lock` is committed.
- **Decision (hygiene):** GitHub Actions runs the root suite and each plugin suite separately, on macOS and Ubuntu,
  Python 3.11 and 3.13, plus ruff with the F rules. Ruff runs with `--exit-zero` until the existing unused imports
  are cleaned up. Duplicate plugin test names were renamed so `pytest plugins` collects. The default handoff folder
  is `$EXO_HOME/handoffs` (or `handoff_dir` in the config), not one person's layout.
- **Decision (site):** planned clients are marked planned; the explain key and the session tree panel were removed
  from the copy because the clients do not have them; "the whole learning curve" now says "get you started"; the
  whitepaper and site no longer pin a test count.
- **Not done:** git history still holds the removed files and the earlier constants. It must be rewritten before the
  repo goes public. The F401 baseline is not fixed yet.

## D-043 (2026-10-12) Esc never stops a turn; the approval card keeps out of your typing
- **Why:** the principal ruled that Esc interrupting a turn is bad UI. It is too easy to hit by habit. Esc should be soft.
- **Esc:** closes the completion menu or dismisses ghost text, then clears a guidance line, then leaves a panel,
  otherwise nothing: no notice, no beep. On the approval card it does not deny. The web client never had an Esc stop.
- **Stop:** ctrl+c while a turn runs (the composer text stays, a dim "stopped" line shows), `/stop`, and the web
  Stop button. Idle ctrl+c is unchanged: Textual's hint that ctrl+q quits; a single press never quits. Typing while a
  turn runs still steers.
- **Approval card (client side only):** it does not take focus from a composer that has text or was typed in during
  the last 1.5 s, and it claims focus later once the composer is empty and quiet. Single keys act only when the card
  has focus. Allow always needs `a` then Enter, or `A`. This matches the web guard. The gate and approval identity
  in the core are untouched.
  *Amendment 2026-10-13 (issues #33, #34):* the late focus claim let ordinary typing answer a card: after a send
  empties the composer, "ok" allowed once and "Act" granted `allow_always`. The card now never takes focus by
  itself; `ctrl+f`, Tab or a click moves to it, and Esc on the card returns to the composer. `A` waits for Enter
  like `a`. The web card uses the same `o` / `a` / `d` keys, with a separate confirm for always. Esc still never
  stops a turn or answers a card; `ctrl+g` stays the composer pane (D-046); D-045 reconciliation is unchanged.
- **Failover:** `SharedClient._failover` runs the socket probe, the engine start and the wait for another window in
  a thread, so the terminal keeps drawing.
- **Docs:** user guide, UI, COMMANDS, WEB, JOBS, TRACE, ONBOARDING, the site, the whitepaper and the intro skill.

## D-044 (2026-10-12) Taste baseline: ship a house taste, then fit the new user
- **Status:** Accepted 2026-10-12. Design in docs/TASTE-BASELINE.md. **Wave 1 built** 2026-10-12 (layers and
  export, `src/exo/baseline/`): the pack loader (a pack is used only once released, with every value and contested
  item ticked by the principal), per-item lookup with source tags, house precedence, `exo taste`,
  `exo taste export-house` with the privacy and value checks, `exo taste review`, and the taste block in the
  prompt. The shipped pack is an empty 0.0.0 placeholder until the principal releases the first one. Waves 2 and 3
  are not built.
- **Contested questions (principal, verbatim, 2026-10-12):** "C ship the implications behind the world view and the
  kind of thinking behind it rather than specifics like religious prescriptions and border-based politics". Value
  items on contested questions ship as reasoning style and its implications only; no faith, nation, party or
  territory, no prescribed practice.
- **Ruling (principal, verbatim, 2026-10-12):** "the project should ship with my taste as a baseline because its a
  good starting point and its been finely tuned with a lot of hard work. then over time it should mould and readapt
  to the new user that it works with. perhaps an innate decay mechanism for older memories/learnings, perhaps
  stronger if large delta from new behaviours (but after a repeated reinforcement of new behaviours, not
  immediately), perhaps the decision model could play a role in this"
- **Rulings on the open questions (verbatim, 2026-10-12):** (1) "agreed with C, but users should have the ability
  to toggle the choice presented in A in case they prefer silent adaption vs active control". (2) "should be even
  slower actually. think about how change happens in a person, vs moods, swings, circumstances, etc. changes
  matter but only if sustained over time. repitition also only matters if sustained." (3) "everything in A, safety
  defaults should not be related to taste, C yes the values and worldview should carry but shift/adapt faster to
  a new user on fresh install and/or detection of a strong/significant change because value/worldview alignment
  is the most offputting factor a companion can have"
- **Decision:** a scrubbed, versioned, read-only **house taste** pack is exported from the principal's store by
  `exo taste export-house`, reproducible and failing on any personal or internal name. It holds method items
  (writing and answer style, work method and verification), identity-free value and worldview items, a house soul
  with no values or safety rules, and the list of shipped skills. People, clients, history, quotes and evidence
  never ship (D-004). Lookups resolve per item: ruling, pin, learned (or scoped) item, then house item, each shown
  with its source. House items lose precedence slowly (`max(0.3, 0.7 * 2^(-t/365))`) and never vanish.
- **Method items change only on sustained change (ruling 2):** signals collapse into one episode per session or
  day; a week counts when its episodes mostly go one way; a persistence score adds contradicting weeks and
  subtracts agreeing ones. An item arms at 3 net weeks within 8, fades by `2^(-n * d / 4)` per further net week,
  and swaps once below 0.3 with the user's version ahead by 0.15. A full swap takes 8 to 12 weeks of sustained
  change. Moods and swings disarm with no residue; place- or task-bound patterns become scoped items; period-bound
  ones (a deadline week) make nothing. 90 days quiet after a swap, undo or no.
- **Adaptation mode (ruling 1):** `/taste mode ask|silent`, for inferred changes only. Ask: one proposal, no
  answer means no change, never re-asked. Silent: the change happens, shows in `/taste` and the brief, and can be
  undone. The principal defaults to ask and the shift rule runs on his own inferred items; new users default to
  silent, because swaps are rare, earned and visible and early questions cost more than they save.
- **Values (ruling 3):** retreat fast, adopt slow. On a fresh install (30 days and 20 sessions, at most 90) house
  values start at 0.5 and step back on one clear signal; later, one strong signal or a stated opposing view
  steps a value back at once. Stepped back means neutral, and never needs a yes. The user's own view is adopted
  only after they state it and hold it for 4 net weeks within 12; adoption follows the mode. Value items pass a
  tighter export check: no named people, groups, places, politics, religion or personal history; each value is
  generalised to a principle and read by the principal before release.
- **Safety defaults are not taste (ruling 3):** gate, approval, budget, posting and other policy defaults ship as
  product policy in `templates/principal-store/policies/` (docs/GATE.md), change only by code change or the
  user's explicit edit, and are never exported, weighted or decayed. Guarded items do not ship.
- **Guards:** only `author == principal` signals count (D-040); pasted text, quoted views and role play are not the
  user's view. The decision model only classifies (relation, delta, scope, voice), abstains below 0.6, is capped,
  and never swaps, adopts or steps back.
- **Why:** D-004 asks for both fit and replicability. A new user gets a tuned start instead of an empty one, and
  the same learning loop that fits the principal then fits them, at the pace people actually change.
- **Rejected:** shipping the principal's store as is (personal data, D-004); starting every user empty (wastes the
  tuning); counting raw signals or swapping within weeks (moods would move taste); silent swaps for the principal
  and turning the rule off for him (ruling 1); letting the decision model act (a classifier error would become a
  lesson); shipping gate defaults as taste (ruling 3); adopting a value from behaviour alone (a guess; neutral is
  the safe fallback).
- **Contested questions:** answered by ruling 4 (above): reasoning style only.

## D-045 (2026-10-12) Approval cards never outlive their request; the web stream resumes; a queued steer can be pulled back
- **Why:** the principal reported an approval card stuck on screen in the terminal and on the web with nothing pending in
  the core, a card the terminal could not answer, a web chat that stopped responding, and wanted to cancel a queued steer.
- **Stuck card, core:** `CoreEngine.ask` published `approval.resolved` only from the respond path. A turn stopped or
  cancelled while the card was open (ctrl+c, `/stop`, a peer hold that expired) closed nothing in the clients. Now one
  `announce_resolved` runs on every exit, once per request: answered, expired, or `cancelled` by `core`.
- **Stuck card, web:** the web server keeps a mirror of open approvals built from replayed events, and the page merged
  that list into what it held and never removed anything. A request replayed from the log after a core restart (no
  resolved event was ever written) came back on every refresh. Now `CommandCentre.reconcile_approvals` compares the
  mirror with `approvals.list` at bootstrap and before `/api/section/approvals` answers (only requests older than the
  call are dropped), and the page replaces its cards with that list on each thread open and stream reconnect. A 404
  on answering also removes the card. A resolved id is remembered so a late replay cannot bring it back.
- **Stuck card, terminal:** the window had no check against the core. It now asks `approvals.list` every 3 s while a
  card is open and drops any it no longer holds. The card was reachable by Tab, but only that way: `ctrl+f` (first `ctrl+g`, moved when D-046 took it for the composer) now moves
  focus to it from anywhere and the card says "tab, click or ctrl+f". The shared-socket path as the principal (second
  window, `X-Exo-Principal`) is covered by a test and works.
- **Web chat stopped:** `/api/events` took `?since=` before `Last-Event-ID`. A browser that reconnects by itself repeats
  its first URL, so every dropped connection replayed the whole history from page load and `message.delta` text doubled.
  The server now resumes from the later of the two and the page ignores a seq it has handled. A browser EventSource also
  never retries after an HTTP error (403 after the web server restarts with a new token, 502 while the core restarts), so
  the page listened to nothing. `bus.js` now reopens the stream itself with backoff from the last seq, and shows "page
  link expired" on a 403. Terminal: `Client.events(reconnect=True)` let a `ClientError` (non-200) end the feed for good;
  it and `SharedClient.events` now back off and retry.
- **Cancel a queued steer:** the command cap is full, so it is a `command.run` built-in, `unsteer` (args: message id,
  empty means the newest), and one new event, `steer.cancelled` (29 events, still under the cap of 30). `turn.send`
  in steer mode now returns `message_id`. The terminal shows `steer queued ›`; Up in an empty composer or `/unsteer`
  pulls it back into the composer. The web shows a chip above the box with an `×` and Up does the same. A steer that
  landed first answers "nothing queued" and is not removed twice. Esc is not used (D-043).
- **Not changed:** the gate, approval identity (D-033, D-039) and the page token.
- **Docs:** user guide, UI, COMMANDS, WEB, DEVELOPER-GUIDE, CORE counts, the intro skill.

## D-046 (2026-10-12) Focus follows the user, a big composer pane, and an inverse layout (experiment)
- **Why (A, verbatim):** "for the cli the user shouldnt have to click exactly inside the tiny text bar for them to start
  prompting." And: "something like a composer built into the cli so write longer more structured prompts." And on a chat
  that opened at its top: "instead of fixing it and flipping it, lets be creative. lets experiment with an inverse ui where
  the text bar is at the top and text/reasoning lines appear from the top instead of the bottom, pushing text down."
- **Focus (terminal):** a click on the transcript or any plain surface returns focus to the composer, and a printable key
  that reaches the app with focus on the transcript (or nowhere) is typed into the composer. A focused panel, the
  completion menu, an input field and the approval card keep their own keys.
- **Composer pane (terminal only):** the same composer widget grows to most of the screen, so the D-037 menu, `@` files and
  ghost text work unchanged and the draft is just the composer text: closing the pane cannot lose it. Open with `ctrl+g`
  (ctrl+e was taken by "edit the last answer", and is cursor-to-end in a text area) or `/compose`. Enter adds a line,
  `ctrl+s` or `ctrl+enter` sends (ctrl+enter only arrives on terminals that report it, so ctrl+s is the dependable key),
  Esc closes (the menu and ghost text first; it never stops a turn, D-043). Header with words, lines and keys, soft wrap,
  light markdown colour, `ctrl+l` line numbers, `ctrl+t` or `/compose edit` for `$VISUAL`/`$EDITOR`, `/compose task` for a
  Goal / Context / Constraints / Done when scaffold. Up and down only move the caret there (history would eat the draft).
  Rejected: a separate modal editor (would need a second completion path); ctrl+x ctrl+e (ctrl+x is cut).
- **Inverse layout (experiment, both clients):** the box is at the top, messages stack newest first, a thread opens on
  its newest item, which is at the top. Inside one message the text reads top to bottom. A tool line or system line is its
  own item, so it goes above the message before it: newest first at item level, with the live streaming message directly
  under the box while it grows. The recommendation to keep tool lines under the live message was not taken: it needs a
  second rule for where "the current turn" ends, and item-level newest-first is one rule that can be explained in a line.
  Terminal: `/layout inverse|classic`, saved in `$EXO_HOME/state/ui.json`, `EXO_LAYOUT` overrides for one run. Web: a
  "Layout" button and `/layout`, saved in this browser (`localStorage`, key `xc-layout`; the one place the page uses it,
  and a test pins that). The approval card, activity line and menu sit under the box. The status strip goes with the box:
  directly above it in inverse, under the thread in classic (issue #102, both clients).
- **Default and how to flip it back:** inverse is the default for the experiment only. Terminal: `DEFAULT_LAYOUT` in
  `clients/tui/exo_tui/prefs.py`. Web: `DEFAULT_LAYOUT` in `static/layout.js`. Change it to `"classic"`; anyone who chose a
  layout keeps their choice. The older TUI tests pin classic through `tests/clients/tui/conftest.py`.
- **Bug found and fixed (web classic):** `.xc-chat` was `flex: 1` in a column with no fixed height, so its own `height` was
  ignored and a long thread grew the whole page: the log never scrolled and the box was off screen. It now has a definite
  height, the log scrolls inside it and opens at its newest item. The terminal already opened at the bottom (a test says so).
- **Not done:** a composer pane on the web page (the ask named the terminal); keeping tool lines under the live message.
- **Docs:** `docs/UI.md`, `docs/ui/WEB.md`, `docs/COMMANDS.md`, `docs/USER-GUIDE.md`, the intro skill, `docs/media/README.md`.

## D-047 (2026-10-12) Staging first for the public site
- **Decision:** `scripts/deploy-site.sh` deploys to `staging.exo.askaconsult.com` by default. Production needs an explicit
  `--prod`, prints the target and, when run interactively, asks for the word `production` typed back. Staging is its own
  Vercel project (`exo-site-staging`) with its own domain and a DNS CNAME to `cname.vercel-dns.com`, and it is never indexed
  (`robots.txt` disallowing all, plus an `X-Robots-Tag: noindex, nofollow` header). Both targets deploy from a git-free copy.
- **Why:** the site is public and the old script went straight to production, so a bad page was live the moment it was
  deployed. A separate project means a staging deploy cannot touch production at all.
- **Rejected:** a preview deployment of the production project (shares settings and domain, easy to promote by mistake);
  a branch-based flow (the site is deployed from a git-free copy on purpose).
- **Revisit if:** the site gains a build step or more than one maintainer deploys, when CI should own promotion.
- **Docs:** `docs/DEVELOPER-GUIDE.md` "Deploying the site".

## D-049 (2026-10-12) Brand: the lattice egg and the X/V mark
- **Status:** Accepted 2026-10-13 (the principal: "a work of beauty"). Proposed 2026-10-12, previewed on staging
  only. On acceptance the lattice egg became the front-page default on every host (`?egg=off` keeps the old orb as an
  escape hatch). The favicon, nav mark and og image still wait for their own change.
- **Why (A, verbatim):** "what if in the logo and branding we made the top half of the x in exo orange so it also looks
  like a v and center our branding around a continually evolving harness, ecosystem, etc." And: "i want the logo for exo
  and all this to be based on my favourite geometric figure since childhood ... an alive evolving pulsing matrix of
  information." The site orb was named "the perfect analogy for a digital egg".
- **Decision:** one figure for everything: a square, its X, and the incentre of each triangle (credit: the
  aska-digital/incentre-lattice research). The wordmark is "exo" with the x drawn as the square's diagonals at 45°; the
  x's top half, which owns the crossing, is `#ff5b14` and reads as a V (exo, and also evo). The e and o are cut from
  regular octagons, the octagon the tiled dots make. Pick: variant A, "facet"; C "seed" is the display form and D "caps"
  the label form; B "round" is rejected (a second shape language, blurs first at 16 px). The mark is the drawing with the
  V and the top dot (the seed) in orange; at 16 px only the X with its V remains.
- **The egg:** `site/brand/egg.js` replaces the front-page orb's shell of nodes with the honeycomb the drawing makes in
  3D and 4D. Cells pulse, packets of information travel the edges, and a heartbeat in the core sets the pace. One stage
  parameter (0 egg, 1 hatchling, 2 lattice, 3 hyper, 4 spare) adds cells, structure, a dimension and brightness;
  fractional stages blend. This is the hook the exopet evolution line can drive. API in `docs/BRAND.md`.
- **Rejected:** a WebGL renderer (the lattice is at most about 2,000 lines; Canvas 2D costs under 1 ms a frame); a
  Fibonacci-sphere shell (it is not the figure); replacing the favicon and nav mark now (they wait for acceptance).
- **Open:** whether the terminal header takes the braille mark (`site/brand/tui-mark.txt`, a proposal only); the
  migration of the nav mark, favicons and og image; the exopet mapping from its evolution line to stages.
- **Docs:** `docs/BRAND.md`; assets generated by `scripts/brand_assets.py`.

## D-050 (2026-10-12) Consumer path: local first, desktop apps after the build settles
- **Why:** the 2026-10-12 audit (item 10) asked for the consumer path to be decided before building toward it.
  The principal ruled on four options for how Exo reaches a new user and where their keys live.
- **Local first (standard):** Exo runs on the user's machine; the web client is served by that machine on a
  loopback address; keys go from the page or terminal straight into the OS keychain and never cross the network
  except to their provider (D-034, D-039). Signed downloadable desktop apps come after the build settles, so the
  package is not rebuilt for every change.
- **Remote access (architecture option):** a hosted page that pairs with the user's own core over an end-to-end
  encrypted relay, so keys and memory stay at home. Kept as a valid design, not scheduled.
- **Hosted cores (roadmap only):** running user cores on our servers would make us the custodian of keys and
  memory. Considered only through cryptographic or decentralized compute that keeps the operator blind.
- **Own inference (later):** a keyless path where users never handle API keys, once funded.
- **Rejected for now:** a hosted service that stores user keys on our servers.
- **Implementation acceptance:** `docs/CONSUMER-PATH.md` proposes the first user, local web surface, first five
  minutes and calm defaults for review under #50. It separates implemented fixes from usability and distribution
  checks still required; it does not replace these principal rulings or claim consumer acceptance.

## D-051 (2026-10-12) muse-cli: a Muse Code subscription through the `muse` CLI
- **Decision:** add a keyless provider `muse-cli` that runs `muse exec` as a subprocess, modelled on `claude-cli`.
  Exo never reads, copies or stores the Muse token; the CLI owns it in the OS keychain. Status reads only the
  non-secret `mechanism` and `subs_active` fields of `~/.config/muse/auth.json`. The test call is one tiny
  `muse exec` prompt, mapped to the existing error classes plus a new one, `unsigned`.
- **Why:** the subscription is reachable only through the vendor's login. A subprocess keeps the token out of Exo
  and out of every log, which is the same line D-034 and D-039 draw for keys.
- **Limits, accepted:** text only (a turn with tools is refused with status 501 so the router falls back), one
  process per turn, no token stream, no usage counts, history flattened into one prompt. On macOS native binaries
  need signing for keychain access (`OSStatus -67068`). Exo checks native Mach-O commands with `codesign -dv`;
  a shell launcher's signing state is unknown, and Exo lets it run without claiming its binary is verified.
  An actual signing failure remains `unsigned`, and signing or startup errors allow router fallback.
- **Locked down:** the run has no shell, no writes, no web tools, no reminder agents, no imported foreign
  personal context and no session log, in a private temporary workspace. Timeout and stop clean the entire
  process group, including descendants after leader exit, with bounded waits before returning an error.
  `META_API_KEY` is dropped from the child so the subscription, not an API account, is used.
- **Gate:** `muse login`, `muse auth` and `muse logout` (any position, executable paths and nested shells too)
  and `~/.config/muse/` are `credentials`: always ask, regardless of lowering policies or standing grants.
- **Rejected:** reading the token from the keychain or `auth.json` and calling the API directly (Exo would hold a
  vendor secret); passing `META_API_KEY` through (bills an API account, not the subscription).
- **Revisit if:** `muse exec` gains a structured output or tool bridge, which could lift the text-only limit.
- **Docs:** `docs/PROVIDERS.md` "Use a Muse Code subscription", `docs/GATE.md` "Muse CLI credentials",
  `docs/DEVELOPER-GUIDE.md` "Add a CLI-backed provider", `docs/WHITEPAPER.md` §13.1, `docs/TOOLS.md`.

## D-052 (2026-10-12) Principal authority applies to messages, learning and settings
- **Why:** an audit reproduced local callers submitting turns, steering, verdicts and panel actions that became
  principal-authored learning evidence. The same callers could accept relaxed gate policies and change settings
  or model routes. Approval answers already checked transport identity, but these adjacent operations did not.
- **Decision:** snapshot the live transport caller when a turn or steer is accepted. Only a principal connection
  produces principal-authored text. Local automation may still drive turns, with author `local`. Background turns
  without an explicit author are system input. Feedback, panel responses, settings, model selection and gate
  policy acceptance require a live principal caller. The policy adoption method checks authority itself, covering
  command aliases and direct plugin callbacks. Its ledger record names the authenticated caller.
- **Transport and clients:** stdio always stamps local, even if its dispatcher has an interactive default. The web
  provider route returns 403 on a core authority refusal and cannot use its offline disk fallback to evade it.
  Preference-pair context includes only principal-authored messages. No protocol command or event was added.
- **Peers and steers:** `peers send` with `via="human"` is a body claim. From a non-principal caller it is sent as
  `via="agent"`, so the receiver never frames it as the principal typing in another client. `unsteer` from a
  non-principal caller pulls back only non-principal steers; the principal's queued words stay.
- **Validation:** regression cases cover untrusted send and steer, feedback and panel actions, policy relaxation,
  settings, aliases, direct calls, caller expiry, TCP, stdio, Unix sockets and a tool child holding the test
  credential. Authenticated user operations remain covered by positive controls.
- **Limits:** same-user processes still need an OS sandbox. Earlier misattributed messages are not rewritten.
- **Docs:** `docs/GATE.md`, `docs/MEMORY-MODEL.md`, `docs/COMMANDS.md`.

## D-053 (2026-10-12) Verification isolates terminal preferences and documents current behavior
- **Why:** the baseline root suite failed four TUI checks solely because the invoking terminal exported
  `NO_COLOR`. Removing that variable made all four pass. Changing `TERM` did not. Product behavior was correct,
  but the tests inherited an unrelated user's display preference.
- **Decision:** the repository test fixture clears inherited `NO_COLOR`. Tests of no-colour mode set it explicitly.
  The product continues to honor the user's setting. Existing tests cover both modes.
- **Documentation corrections:** the approval focus shortcut is ctrl+f. Ctrl+g opens the composer. Undo hides log
  entries and invalidates covering summaries, matching D-041 and the implementation.
- **Docs:** `docs/DEVELOPER-GUIDE.md`, `docs/UI.md`, `docs/MEMORY-MODEL.md`.

## D-054 (2026-10-12) Ruling citations do not waive mandatory learning checks
- **Why:** the direct-ruling shortcut ran before the guarded-domain and evaluation rules. A reviewer could cite
  a real but unrelated principal statement and install a derived skill or guarded lesson without those checks.
- **Decision:** held conflicts, guarded proposals and mandatory evaluation checks run before the shortcut.
  Skills always need evaluation, even if the configured type list omits them. Configured high-impact or gated
  lessons retain their critic, evidence-weight and session-count requirements. Missing held-out cases cannot pass.
  Rule numbers stay stable in the ledger. Ordinary unguarded taste and fact lessons retain the direct-ruling path.
- **Principal control:** original rulings remain available in the ledger and context. Explicit acceptance of a
  concrete proposal remains a separate path. A reviewer merely citing a ruling does not exercise that authority.
- **Validation:** regressions prove unrelated ruling citations cannot bypass skill evaluation or guarded review,
  and cover configured gates, evaluation success and failure, critic objections, held conflicts and direct acceptance.
- **Limit:** citation authorship alone does not establish semantic support for an ordinary unguarded lesson.
  Binding derived claims to the cited words still needs a separate design and evaluation.
- **Docs:** `docs/LEARNING.md`, `research/LEARNING-LOOP.md`.

## D-055 (2026-10-12) Keep staging current; package with Electron after the core and browser
- **Why (A):** update the latest staging site as the work advances, improve it where useful, and eventually package
  the browser client as an Electron desktop app. Packaging is the last step after polishing the core engine and codebase.
- **Delivery order:** core verification and review, then browser workflow polish, then Electron packaging. The desktop
  package is a chosen direction, not a built client. Its eventual checks include installation, core lifecycle, signing
  and updates on the intended platforms. The roadmap gives the exit criteria for each stage.
- **Evidence:** the site identifies the authority and learning fixes at `1532fa9` as in review in draft PR #57, with
  links to the revision and CI results. A successful local installation does not make an unmerged change built on main.
  The four existing roadmap states stay unchanged. House taste wave 1 is layers and export tooling; the shipped
  `0.0.0` pack is empty. A curated pack and adaptation remain future work.
- **Research:** AIOS and AI Legion are public references, not new dependencies. Frozen delegated permissions,
  isolated tools with a credential broker, durable steerable work and shared budget admission are exploring directions.
  Application authority checks are not described as OS isolation.
- **Site:** preserve the latest staging design, keep mobile page navigation reachable, provide section jump links,
  and show status in words as well as color. The lattice egg was a staging-only preview under D-049 until it was accepted on 2026-10-13.
- **Deployment scope:** this instruction authorizes staging updates. Deploy to `exo-site-staging` from a git-free copy,
  preserving staging's robots exclusion and noindex header. Production promotion remains separate under D-047.
- **Docs:** `docs/SITE-STATUS.md`; `docs/DEVELOPER-GUIDE.md` "Deploying the site"; `site/roadmap/` and the landing and
  web-client pages. No user commands changed, so the user guide is unaffected. Whitepaper updates remain batched.

## D-056 (2026-10-13) Versioning and naming
- **Why:** versions, branches and documents had no common naming, so an iteration could not be found again. The
  site had two ad hoc tags and Exo itself had no release tag.
- **Decision:** Exo is SemVer below 1.0, tagged `exo-vX.Y.Z`, with a codename per MAJOR from the exopet line (0.x egg,
  1.x cell, 2.x tile, 3.x cube, 4.x frame, then 4D and up) shown as `exo 0.2.0 (egg)`. Main becomes 0.2.0. The site
  is `site-vMAJOR.MINOR`, archived before each production deploy, and the deploy script tags and writes
  `version.json`. The whitepaper is `whitepaper-vX.Y`, the house taste pack `house-vX.Y.Z`. Decision numbers are never
  reused. Branch prefixes are listed. `CHANGELOG.md` follows Keep a Changelog.
- **Numbering:** D-055 is used by branch `feat/roadmap-page`, so this is D-056.
- **Validation:** tests check the `--version` form and that the changelog's latest release equals the pyproject version.
- **Docs:** `docs/VERSIONING.md`, `CHANGELOG.md`, `docs/DEVELOPER-GUIDE.md`.

## D-061 (2026-10-13) Web sub-pages to the site v2 standard
- **Why:** the chat page and the site v2 (front page, roadmap) set a high bar; Tasks, Schedule, Command and Providers
  still looked like a debug console: small mono panel titles, cramped lanes, colour-only status, plain one-line empty
  states, no 400px layout for the nav, and a DOM quirk that printed `null` above the lists.
- **Decision:** every sub-page gets the site's section head (mono eyebrow with an orange rule, Inter display title,
  one line of purpose, a readout on the right), a designed empty state that says what will appear and how to start,
  status as a pill whose led form carries the state (filled, hollow, dashed, crossed, barred) next to the word,
  tabular numbers, and a working 400px layout (the nav wraps to two rows). Tasks and Schedule read like the roadmap
  grid and cards; Command and Providers like an instrument panel. The lattice egg (`site/brand/egg.js`, copied
  byte for byte to `static/egg.js`) appears once, in the Tasks empty state. Presentation only: no route, protocol,
  token (D-039), approval (D-045), layout (D-046) or completion (D-037) behaviour changes; element ids stay.
- **Numbering:** D-057 to D-059 may be held by draft PRs #107 and #108, so this is D-061.
- **Validation:** `plugins/exo-command/tests/test_subpages_static.py` (egg copy matches the site, used once, every
  sub-page has a head and an empty state, every pill state has a led form, no `null` children); plugin and root
  suites; before and after screenshots from `scripts/shots_web_tabs.py`.
- **Docs:** `docs/ui/WEB.md` "Sub-pages (D-061)".

## D-067 (2026-10-13) Engine split step 4: delete what is unused, move peers and the scheduler out of the core
- **Why:** issue #46. The counted core sat about 24 lines under its 16,000-line cap, so every new core feature was
  blocked. The first pass of `docs/ENGINE-SPLIT.md` only moved the engine's diagnostics and factory, which the plan
  itself says does not reduce code.
- **Decision:** reduce the counted core by real cuts first, then by two seams the plan already names.
  - Delete code that grep shows nothing outside tests uses, and collapse duplicates. Examples: the decision models'
    score and noul asks, unreachable fuzzy strategies, a second `_maybe_await` and its inline copies, a copy of
    `HubBus`, duplicated CLI provider process code, the per-session-tree branch the engine never takes, and 59
    `@dataclass` lines the protocol decorators now apply.
  - Open approvals become `ApprovalDesk` (`exo.core.approvals`), one record per request instead of five dicts.
  - The peer transport moves to `exo.peers` (step 3's "peer transport"). The scheduler moves to `exo.scheduler`.
    `exo.compose` attaches both (`attach_peers`, `attach_scheduler`) at the same point in construction as before,
    so tool order and prompts do not change. `PeerConfig` and `EngineTarget` stay in the core, because bus ingress
    and jobs use them.
  - Members that the outside code needs get public names: `run_turn`, `channels`, `Delegator.route`.
  - The memory and context slot defaults that Exo never calls stay, because exo-context and exo-memory also run
    under Hermes, which does call them.
- **Rejected:** removing `exo upstream merge` or echo engine commands (user-facing behaviour), moving trace
  rendering out again (a view move, not a seam), raising the cap.
- **Budget (lead call, 2026-10-13):** the core cap goes from 16,000 to 15,000 lines to keep the gain
  (`tests/integration/test_wave1_acceptance.py`). Step 4's completion criteria (private-state reads, inward product
  imports) remain open under #46.
- **Behaviour:** no protocol, event or command changes. D-033, D-038, D-039, D-040, D-045 and D-052 to D-054 checks
  run as before, and required-executor mode (#70) still fails closed. An engine built without composition no longer
  has peers or a scheduler: `peers` and `schedule.edit` answer `unsupported` there.
- **Numbers:** 15,986 counted lines on main before, 14,812 after (1,174 fewer): about 300 deleted, 561 moved with peers, about 320 moved
  with the scheduler, 20 added by the approvals desk.
- **Validation:** root suite (twice) and every plugin suite, `ruff check`, the mypy baseline (it shrank from 277 to
  249 entries; no new ones), `python -m exo.protocol.export --check`.
- **Docs:** `docs/ENGINE-SPLIT.md` "Progress", `docs/DEVELOPER-GUIDE.md` §1, `docs/CORE.md`, `docs/PEERS.md`,
  `docs/SCHEDULE.md`, `CHANGELOG.md`.

## D-068 (2026-10-13) Turns do not wait for memory upkeep
- **Why:** `build` waited up to `settle_timeout` (60 s) for pending summaries before every model call, so slow
  bookkeeping delayed replies (issue #99).
- **Decision:** build the prompt from ready summaries plus the verbatim tail and let summaries and merges finish in
  the background. Only when that view would overflow `view_budget` does the turn wait, for `overflow_settle` (5 s)
  at most. A summary node turns `done` in one step and `build` reads in one synchronous stretch, so a turn sees
  either the old view or the merged one, never a mix. Logic is in `src/exo/turn_settle.py`, outside the counted core.
- **Validation:** `tests/core/test_turn_settle.py`, `tests/core/loop/test_loop.py`.
- **Docs:** `docs/MEMORY-MODEL.md` "A turn does not wait for memory upkeep".

## D-069 (2026-10-13) Session rename is a built-in command.run, not a protocol command
- **Decision:** `/rename <title>` is the built-in `rename` of `command.run`, answered in `src/exo/diagnostics.py`. It
  calls `update_session(title=...)` and announces the change with the existing `session.updated` event. The protocol
  stays at its cap of 30 commands and 30 events. Only the principal may rename (D-052); anyone else gets `forbidden`.
- **Why:** issue #98. Titles are what make `/sessions` and `/resume` scannable, and the title is already stored in SQLite.
  A new command type would have broken the cap; the `unsteer` pattern (D-045) already shows the way.
- **Clients:** the TUI has `/rename`; the web page has `/rename` and a double-click on a thread in the list
  (`POST /api/chat/sessions/<id>/rename`). Auto-titling from the first message is out of scope.
- **Numbering:** D-062 to D-068 may be held by open PRs, so this is D-069.
- **Validation:** `tests/core/test_rename.py`, `plugins/exo-command/tests/test_ghost.py`.
- **Docs:** `docs/COMMANDS.md`, `docs/USER-GUIDE.md`, `docs/ui/WEB.md`, `docs/DEVELOPER-GUIDE.md`.

## D-070 (2026-10-13) Inspect, steer and stop running subagents without a new protocol command (issue #97)
- **Why:** a delegated child is a real session with a live loop, but the principal saw only the final receipt. A
  child going wrong could be stopped only by stopping the parent.
- **Decision:** one built-in `command.run` name, `subagent`, with verbs `list`, `open`, `send` and `stop`. The protocol
  stays at its 30 commands and 30 events. The rules live in `src/exo/subagents.py`, outside the counted core. The core
  hook is a `marks` dict on the `Delegator` and two lines in `_receipt`.
- **Who may act (D-052):** the principal, or the task's parent session (a tool in that session calls
  `subagents.steer(..., by=<parent id>)`). Local callers and other sessions get `forbidden`.
- **Who wrote it (D-040):** a principal caller typed the note, so it is stored with author `principal`. The parent
  session speaks as `delegate`. Neither path can write a message the principal did not type under the principal's name.
- **Finished children:** a child that is done, failed or cancelled is locked. Steering or stopping it fails with
  `conflict` and a message naming the status. Nothing is written.
- **Receipt:** the child's receipt summary gets a line, `[steered by principal]` or `[stopped by parent]`, and a
  `task.updated` event carries the same words while the child runs.
- **Clients:** TUI `/tasks`, `/open <task>`, `/steer <task> <text>`, `/stop <task>`; web Tasks cards (Open, Steer, Stop)
  over `/api/tasks/<task>[/steer|/stop]`. `/open` reads the child with `session.get`, not `session.resume`, so a live
  loop is never repaired mid-turn.
- **Limits:** child approvals shown in the parent, and `followup` or `resume`, are follow-ups. Typing straight into a
  child's session still steers it through `turn.send`; that path works but leaves no note on the receipt.
- **Docs:** `docs/COMMANDS.md`, `docs/UI.md`, `docs/ui/WEB.md`, `docs/USER-GUIDE.md`, `docs/DEVELOPER-GUIDE.md`.

## D-073 (2026-10-13) Signed updates verify against a pinned trust list (issue #42)
- **Decision:** `update.require_signed` (default false) makes `exo update` verify the upstream tip with
  `git verify-commit` (SSH signatures) against `$EXO_HOME/update/allowed_signers`, then merge exactly that sha. The
  repo's `trust/allowed_signers` is only the source for the pin; `exo update --pin-trust [REV]` copies it, by hand.
  Code: `src/exo/update_verify.py`, two hooks in `src/exo/update_cmd.py`.
- **Why:** the repo cannot be its own root of trust. If the check read the list from the commit being installed, an
  attacker who can push could add their own key. A local pin makes the first trust decision a human one and makes
  every later key change visible. Opt-in because the project does not sign releases yet.
- **Limits:** trust on first use of the pin; SSH signatures only; commit signatures, not tags; `uv tool` installs
  cannot be verified. A key compromise needs the pin edited by hand.
- **Docs:** `docs/INSTALL.md` "Signed updates", `docs/DEVELOPER-GUIDE.md` "Cutting a release", `CHANGELOG.md`.

## D-077 (2026-10-15) Core budget: a ratchet with headroom, functionality and robustness first
- **Status:** Accepted 2026-10-15. The principal's ruling: "important to be lean and optimized but not at the cost of
  functionality and robustness and product-market fit". It replaces the budget part of D-067 (the hard 15,000-line
  ceiling). D-067's cuts and seams stand.
- **Why:** after #124 the count was 14,813. #91 (required executor boundary) added 46 lines and #87 (model spend
  boundary) added 140, so main stands at 14,999, one line under the wall. The next fix would have waited for a cut.
  A line count is a signal of a lean core. The aim is structure, and security, robustness and product work must not
  be blocked by the signal.
- **Decision:** `tests/integration/test_wave1_acceptance.py` records `CORE_FLOOR = 14813` and `CORE_HEADROOM = 1000`
  and asserts only `total < CORE_FLOOR + CORE_HEADROOM` (ceiling 15,813).
  - The floor ratchets down: every real reduction PR (deletions, simplification, real seams per ENGINE-SPLIT) sets it
    to its post-merge count in the same PR. It never goes up.
  - Headroom is for shipping: fixes and features land inside it with no ceremony. The test prints the count, the
    floor and the headroom left, and its failure message shows them.
  - Security and robustness fixes are never blocked. If one would cross the ceiling, its PR raises `CORE_HEADROOM` by
    the minimum needed with a one-line note here, and the lead schedules a reduction within the week. A feature that
    would cross the ceiling waits for a reduction or carries a recorded decision.
  - The goal is structural (ENGINE-SPLIT step 4: no private-state reads across module boundaries, no product imports
    into the engine, one responsibility per module). The test also prints the largest counted files and flags any
    above 1,000 lines, as information only.
  - The `CORE` list is never edited to dodge the count.
- **Rejected:** keeping the wall and raising it by a step each time (the count chases the work, with no pressure to
  cut); retiring the count (ENGINE-SPLIT asks for import-direction checks first, and none exist); excluding modules
  or moving code to dodge the count.
- **Numbering:** D-074 and D-075 are held by open PRs (#125, #126) and D-076 by a parallel reduction PR, so this is D-077.
- **Numbers:** floor 14,813 is the count at the merge of #124 (D-067 recorded 14,812 on the branch before its last
  merge from main). Main (859fbcc) is at 14,999, so 814 lines are left. No source changed.
- **Revisit if:** the headroom is raised more than once for non-security work, or the floor stops falling for a long time.
- **Validation:** the budget test, `tests/docs`, `tests/test_privacy.py`, `tests/test_versioning.py`, `ruff check`.
- **Docs:** `docs/ENGINE-SPLIT.md` "Budget policy", `docs/CORE.md`, `docs/DEVELOPER-GUIDE.md` §7, `docs/WHITEPAPER.md`
  §11 and §27, `CHANGELOG.md`.

## D-080 (2026-10-16) One meaning for `Usage.input_tokens`, and what the per-child token budget counts
- **Status:** Accepted 2026-10-16. It replaces the `input_includes_cache` flag that D-032 #6 added for pricing.
- **Why:** the two transports filled `Usage.input_tokens` differently. Anthropic reports only the uncached input,
  with cache reads and writes apart. The OpenAI transport copied `prompt_tokens`, which already holds the cached
  tokens. Only pricing knew. Every other reader assumed one meaning and got the other half the time:
  - The delegate budget (200,000 tokens per child) counted `input + output`. On Anthropic, where caching is on by
    default, it ignored the cached context and barely acted. On OpenAI-style routes it counted the whole cached
    prompt on every call, and a child with a 92k prompt was stopped after 3 calls.
  - The TUI context gauge read about double on OpenAI routes (182,012 for a 92,012-token prompt), and the cache
    share read 49% where 98% was real.
  - Context-engine plugins got `prompt_tokens` = `input_tokens`, so on Anthropic they saw only the uncached tail
    and usage-driven compression never fired.
- **Decision:**
  - `input_tokens` is the uncached input on every transport. Cache reads and writes are counted apart, so the
    prompt a call sent is the sum of the three. The OpenAI transport reports `prompt_tokens - cached_tokens` as
    input and the cached count as cache reads. A cached count it cannot split (above the prompt, negative, not
    an integer) makes the usage incomplete, so the spend boundary refuses it and keeps the reservation. Without a
    spend policy the whole prompt stays uncached input. The router prices `input_tokens` directly. Cost and
    settled spend are unchanged on both transports.
  - The per-child budget counts what the child newly processes: uncached input plus cache writes plus output.
    Cache reads do not count. The receipt's tokens in are the same count.
  - Readers that need the prompt size add the three counters: the TUI and web context gauges, the cache share
    (cache reads over the whole prompt), and the `prompt_tokens` handed to context engines.
- **Rejected:**
  - Keeping the flag and teaching every reader about it: each new reader would have to know, and four did not.
  - Counting all context processed (`input + cache read + cache write + output`) for the budget: every round
    re-reads the cached context, so a child re-reading 25k tokens a round would stop after about 8 rounds, where
    today it effectively never stops on the default provider.
  - Counting only uncached input plus output: cache writes are new tokens at a higher price, and leaving them
    out keeps the budget blind on Anthropic.
- **Revisit if:** a transport reports cache counters in yet another shape, or children hit `budget` on routes that
  report no cache counts at all (local servers with their own prompt cache), where every call's full prompt counts.
- **Follow-up:** `spend.py` and `billing.Price.actual` still take an `includes_cache` argument, now always false.
  Remove it once PR #129 (tiered spend rates, same files) lands. The turn usage handed to `on_turn_complete` is the
  sum of the turn's calls, not the last call's prompt.
- **Validation:** `tests/core/models` (transport split, router price, spend settlement), `tests/agents`,
  `tests/clients/tui`, `tests/core/plugins`, `tests/trace`, the core budget test, `ruff check`, the mypy baseline.
- **Docs:** `docs/TRACE.md` "Cost", `docs/MODEL-SPEND.md`, `docs/CORE-V2-INTERFACES.md`, `docs/AGENT-WIRE.md`
  "Limits", `docs/UI.md`, `CHANGELOG.md`.
