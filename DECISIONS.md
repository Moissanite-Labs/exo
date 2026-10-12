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
