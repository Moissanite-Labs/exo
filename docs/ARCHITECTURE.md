# Exo architecture v1

Status: v1, 2026-10-11. Charter: `research/PRINCIPLES.md`. Choices: `DECISIONS.md` (D-004 fit and
replicate, D-005 Hermes as a distribution, D-006 principles). Inventory: kept outside the repo (D-042).

## 1. Shape in one picture

```
 principal (A, or any user)
   │  chat · TUI · desktop · WhatsApp · Discord · phone
   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ L0  Hermes Agent, upstream, pinned release tag (unmodified + ≤5 patches)│
│     gateways · 40 providers · delegation · kanban · cron · TUI · desktop │
├──────────────────────────────────────────────────────────────────────┤
│ L2  Exo plugins (installed by Exo, first-class to the user)            │
│     exo-memory  exo-context  exo-gate  exo-bus  exo-command  exo-orchestra│
├──────────────────────────────────────────────────────────────────────┤
│ L1  exo package (generic mechanism, MIT)                               │
│     ledger · store · consolidate · retrieve · ask · gate · eval · ingest │
├──────────────────────────────────────────────────────────────────────┤
│ L3  principal store (one per person, data only, never in the package)  │
│     ~/.exo/principals/<id>/  log · beliefs · pack · timeline · private │
└──────────────────────────────────────────────────────────────────────┘
```

**Rule (D-004):** L0 to L2 are the mechanism and ship to everyone. L3 is the person. Nothing in L0 to L2
names A, his accounts, machines, agents or paths. A fresh install with an empty L3 runs end to end.

## 2. Layers

### L0 Engine: Hermes Agent
Pinned to a weekly release tag. Exo depends only on the plugin contracts: `MemoryProvider`
(`agent/memory_provider.py`), `ContextEngine` (`agent/context_engine.py`), the plugin context
(`hermes_cli/plugins.py`: `register_memory_provider`, `register_context_engine`, `register_hook`,
`register_tool`, `register_command`, `register_platform`, `register_system_prompt_section`,
`register_approval_transport`, `register_middleware`), the dashboard plugin surface, and profiles.
A contract test suite runs against every new tag before the pin moves.

### L1 The `exo` package (generic mechanism)

| Module | Job | Principle |
|---|---|---|
| `exo.ledger` | Append-only event log. SQLite, each event canonical JSON, Ed25519-signed by its author, hash-chained (Divan Bus crypto). Source of truth; everything else rebuilds from it. | P1, P18 |
| `exo.store` | Principal store on disk: router, principal pack, belief nodes (hafiz schema), timeline, private corpus, training sets. Create, load, validate, lint, graph. | P7, P9 |
| `exo.consolidate` | Turns log events into belief proposals: reflection on corrections, positive local form, importance weighting, rule-of-three detector. Proposals are diffs for review, never silent edits. | P5, P6, P9 |
| `exo.retrieve` | Context assembly under a token budget: router rules, BM25 over nodes (v1), embeddings and graph expansion (v2). | P7 |
| `exo.ask` | Active learning: question queue, value-of-information score, ask-or-act threshold from model disagreement, interruption budget. | P3, P4, P13 |
| `exo.gate` | Action classifier on reversibility × exposure × cost × third-party effect, plus the principal's policies. | P11, P10 |
| `exo.eval` | n=1 evaluation: held-out rulings, eval Q&A, next-decision prediction, edit distance, corrections per accepted output. | P12 |
| `exo.ingest` | Adapters into the ledger: Claude Code transcripts, Hermes `state.db`, claude.ai / ChatGPT / Grok exports, documents, Divan Bus. | P1 |
| `exo.onboard` | `exo init`: interview that builds a new principal store from zero (shared prior plus delta). | P8, P13 |
| `exo.brief` | Summary hierarchy: task → day → week, ranked, linked to evidence. | P16 |

### L2 Exo plugins for Hermes

| Plugin | Hermes surface | What it does |
|---|---|---|
| `exo-memory` | `MemoryProvider` | `system_prompt_block` = principal pack (cached). `prefetch` = `exo.retrieve` for the turn. `sync_turn`, `on_memory_write`, `on_delegation` → ledger events. `on_session_end` → consolidation queue. `on_pre_compress` → captures corrections and rulings so compaction cannot drop them. Tools: `exo_recall`, `exo_get`, `exo_note`, `exo_should_have_been`. |
| `exo-context` | `ContextEngine` + commands | Compaction that keeps principal signals (rulings, corrections, open decisions) verbatim and writes a handoff file. Commands: `/exo branch`, `/exo handoff`, `/exo resume`. |
| `exo-gate` | hooks `pre_tool_call`, approval transport | Classifies each action; reversible and private runs, irreversible or public waits for the principal's channel with a link and a default. Applies posting rules from the principal store. |
| `exo-bus` | platform plugin | Divan Bus as a Hermes platform: signed, hash-chained seat-to-seat messages; SYM-2P packets as an optional body format. |
| `exo-command` | dashboard plugin | Command centre: projects (Continuum's clustering), live agents and tasks (subagent viewer), decisions waiting on the principal, the brief, cost per accepted output. |
| `exo-orchestra` | profiles + skills | The Protean system as a template: orchestrator and role profiles, the control-plane rules as a charter, the doctrine, the clean-writing gate, delegation receipts. Generic roles; a principal's own agents and names live in L3. |

### L3 Principal store (data only)
Layout = the hafiz layout (`~/hafiz` is A's store v0):
`INDEX.md` router, `PRINCIPAL.md` pack, `nodes/<domain>/*.md` beliefs with provenance, `timeline/`,
`corpus/private/` raw record (git-ignored, 600), `training/`, `policies/` (gates, posting rules, budget,
interruption budget), `agents/` (the principal's own named agents and charters), `ledger.sqlite`.

## 3. Memory and context model

**Four tiers, each rebuilt from the one below.**

| Tier | Holds | Built by | Loaded |
|---|---|---|---|
| T0 Ledger | every event, signed | live capture + ingest | never into a prompt |
| T1 Beliefs | propositions with evidence, confidence, validity | consolidation, reviewed | by retrieval, under budget |
| T2 Pack | one page: identity, rulings, voice, gates | curated from T1 | every turn, prompt-cached |
| T3 Working context | the turn's prompt | context engine | always |

- **Taste vs facts (P7).** Style, stance, values and rulings live in T2 (and later in an adapter). Dates,
  IDs, people and project facts live in T1 behind retrieval with sources.
- **Budgets.** T2 under ~1.5k tokens. Retrieval default 1.5k, configurable per task. Nodes 150 to 450 words.
- **Validity.** Beliefs carry `valid_from` / `valid_until`; superseded beliefs stay with a `supersedes` edge.
- **Conflict rule** (per principal, A's default): newer ruling wins unless the older guards something
  irreversible or public; then ask one numbered question.
- **Corrections (P5, P6).** A verdict event (accept / edit / reject / should-have-been) is the highest-
  weight event. Consolidation writes it as "wants Y, not X, because Z" the same day and proposes the edit.

## 4. Sessions and context management
- **Session** = Hermes session; every turn mirrored to the ledger with session id and lineage.
- **Compaction** (exo-context): before compressing, extract rulings, corrections, open decisions and live
  processes verbatim into a handoff note; the summary links to it.
- **Branching:** `/exo branch <topic>` writes `handoffs/<topic>-<date>.md` and starts a fresh session that
  loads only the pack, the router and that handoff (what A did by hand on 2026-10-11).
- **Resume:** `/exo resume <topic>` rebuilds working context from the handoff plus a retrieval pass.
- **Cross-harness:** Claude Code, Codex, OpenCode sessions are ingested into the same ledger, so memory
  follows the principal across engines (P17).

## 5. Onboarding a new principal (replicability)
1. `exo init` creates an empty store with the generic prior (default policies, default doctrine, the
   Protean role template, the question bank).
2. Interview: the interviewer drafts 20+ candidate questions, imagines answers, asks the top few by
   value of information (P13), and writes answers as `a-said` beliefs.
3. Import: point `exo ingest` at existing histories (Claude Code, Hermes, exports). Consolidation drafts
   beliefs; the principal reviews the first batch.
4. From then on, the ask loop runs at a falling rate inside the interruption budget.

A is principal #1: his store is seeded from `~/hafiz`, his policies from his standing rulings.

## 6. Mapping of fused projects (from the inventory, ratified in D-007; the inventory is kept outside the repo)

| Today | Becomes in Exo | Layer | Generic part | Principal part |
|---|---|---|---|---|
| **hafiz** (`~/hafiz`) | `exo.store`, `exo.retrieve`, `exo.ingest`, `exo.onboard`; the store format | L1 | schema, tools, pipeline, privacy tiers | A's nodes, pack, timeline, corpus = store #1 |
| **optchat-memory** | episodic base of `exo-memory` (verbatim daily log + summary tree, zoom and date tools) | L2 | provider code | the log and tree, stored under the principal store |
| **SYM-2P** (protean-sym2p) | `exo.protocols.sym2p`, optional body in bus envelopes | L1 | spec, validator, bench | peer registry, party names |
| **Divan Bus** | `exo.bus` (client, relay service, MCP), `exo-bus` platform plugin, skill | L1/L2 | relay, crypto, checkpoints | seat registry, keys, message log |
| **protean-ordaprompt** | front-door router plugin (route, ask, abstain) + desktop pane | L2 | router, calibration harness | topic catalog, calibration labels from corrections |
| **protean-ordopilot** | `exo.intake` courier, wired to real sessions | L1 | courier | route history |
| **subagent viewer / chief-of-staff** | `exo-command` live-process pane; steering after a reliability gate | L2 | all | none |
| **continuum** | `exo-command` projects board, as a dashboard route (not a separate server) | L2 | scanner, board, action log | project list, scan roots |
| **raptora** | retired; hiatus rule → `exo.attention` | L1 | rule | thresholds |
| **Protean control plane, check scripts, ops schemas** | `exo.dispatch`, `exo.records`, `exo.gates.prose` | L1 | gates, pipeline, schemas | LEARNINGS, ledgers, seat names (data) |
| **Protean doctrine, choreography, team pipeline, council, github pack** | Exo skill pack + `exo-orchestra` role template | L2 | roles, pipeline, rules | A's own agents and names (store `agents/`) |
| **group-model-sync** | plugin: per-seat model pins | L2 | pinning | A's model choices (his budget rule) |
| **taste learning (#126940)** | `exo.consolidate` taste beliefs with decay and conflict escalation | L1 | mechanism | taste values |
| **muse-bridge** | plugin (salvaged) | L2 | bridge | A's Muse seats |
| **Hermes core PRs** | 5 carried patches (D-007); the rest stay upstream PRs | L0 | | |
| **Hermes profiles, state DBs, kanban, Eldunari, memories** | ingested into A's ledger and store | L3 | ingest adapters | all content |

## 7. Migration path from what runs today

Each stage is reversible and leaves the current system running until the next stage proves itself.

| Stage | What changes | Proof before moving on |
|---|---|---|
| **M0 Install beside** | Exo package and plugins installed into a **canary profile** only. A's live profiles untouched. Hermes stays on its current checkout. | Contract tests pass; canary chats with exo-memory loaded from a copy of hafiz. |
| **M1 Store and ledger** | `~/hafiz` becomes A's store at `~/.exo/principals/a/` (symlink first, then move). Ingest Claude Code, Hermes and the Team6-era DBs into the ledger. | `exo lint` clean; ledger verifies; eval Q&A answered from the store. |
| **M2 Memory and context** | exo-memory and exo-context enabled for the lead agent's Claude sessions (via the hafiz skill bridge) and for one Hermes profile (proteus). optchat log migrated under the store. | Corrections captured as verdict events; a compaction keeps rulings verbatim; handoff files written automatically. |
| **M3 Gate and bus** | exo-gate on all profiles for public and irreversible actions; Divan Bus becomes `exo.bus`; vault bridge kept until amp-lugia polls the bus. | No public post without a gate record; bus chain verifies. |
| **M4 Command centre** | Continuum and the subagent viewer served as one `exo-command` dashboard route; raptora cron disabled; morning brief from `exo.brief`. | A reads one brief and one board; nothing else needed to see state. |
| **M5 Orchestra** | Protean control plane and skills from the Exo pack; profiles install skills once from Exo (ends 433 duplicated copies). | Lanes dispatch with Exo gates; duplicate skill copies removed after a week clean. |
| **M6 Pin and patch** | Hermes pinned to a release tag with the 5-patch queue applied by `scripts/pin-hermes.sh`. | Contract suite green on the tag; canary, then protean, then root profile. |
| **M7 Ask and eval loop** | Daily question loop inside the interruption budget; n=1 eval on held-out rulings each week. | Corrections per accepted output falling; prediction accuracy reported. |

**For a new user** the same stages collapse into one install: `scripts/install.sh` → pinned Hermes +
Exo plugins → `exo init` interview → optional `exo ingest` of existing histories.

## 8. Repo layout

```
exo/
  DECISIONS.md  README.md  pyproject.toml  LICENSE (MIT)
  research/  docs/
  src/exo/            ledger.py store.py consolidate.py retrieve.py ask.py gate.py eval.py
                      ingest/ onboard.py brief.py cli.py crypto.py
  plugins/            exo-memory/ exo-context/ exo-gate/ exo-bus/ exo-command/ exo-orchestra/
  templates/          principal-store/ (empty store + generic prior) orchestra/ (role profiles, doctrine)
  tests/              unit + contract tests against pinned Hermes
  scripts/            install.sh  pin-hermes.sh
```
