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
