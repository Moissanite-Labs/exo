# Store v2: hafiz merged with Arif and Eldunari

Status: draft for review, 2026-10-11. Ruling: D-009 ("hafiz's design becomes the store design, merged with
Arif and Eldunari"). Also bound by D-003 (hafiz is the seed), D-004 (mechanism vs principal), D-007
(Eldunari and Arif are data to migrate). Nothing in this document has been applied. All sources were read
only.

## 0. Facts checked on disk (they correct the inventory)

| Claim in `inventory/` | What is on disk today |
|---|---|
| Arif holds about 919 items | 4,835 items in Postgres `arif_kb`, 4,834 embeddings (1,024 dims). 919 is the August figure in `IDEA.md`. |
| Arif has no git | `arif/wrk/gc1` is a git repo: 5 commits, no remote, 59 dirty files. |
| Arif ingests mail | Mail was ingested and later removed (`delete_mail.py`). No `google_mail` rows remain. The mbox is still in `mats/Google/`. |
| Eldunari has 429 docs | 429 is the commit count. Git tracks 456 files, 422 of them Markdown. 2,084 files sit on disk, most of them ignored Orda rehearsal state under `nexus/state/orda2*`. |
| Eldunari semantic index | `.semantic-index.sqlite3` holds 406 documents, built 2026-10-07 with a remote model. |

Arif items by source today: chrome_bookmark 2,941; discord 580; x_bookmarks 489; apple_notes 318;
grok_conversations 193; x_bookmarks_quote 107; feed 85; github_starred 44; manual 35; google_drive 26;
google_chat 11; github_repo 5; external_report 1. Edges: shared_channel 158,519; similarity 18,870;
quote_of 109; co_occurrence 12.

## 1. What each system does well and badly

### hafiz (`~/hafiz`, mirrored in `src/exo/store.py`)

Good:
- One node per cluster of claims, with a strict frontmatter: type, confidence, stability, validity
  dates, sources with a `kind`, typed links (`method/SCHEMA.md`).
- A router (`INDEX.md`) with load rules, plus a one-page pack (`PRINCIPAL.md`) that is always loaded.
- Privacy tiers that are simple and enforced by lint leak patterns (`method/PRIVACY.md`, `store.py LEAKS`).
- Provenance down to a dialogue timestamp. Corrections become training pairs (`training/`).
- Stdlib only, plain files, fast to read by hand.

Bad:
- Retrieval is in-memory BM25 over 49 nodes (`tools/kb.py rank`, `store.py find`). It will not scale to
  thousands of items.
- No layer for external data (bookmarks, notes, mail). No agent memory. No group state.
- Some source prefixes name A's own paths (`memory:`, `vault:` in `SCHEMA.md`). Fine as data, wrong as schema.
- Summaries and `load_when` are hand-written. That does not scale to 400 migrated documents.
- `tools/kb.py` and `store.py` are two copies of the same code.

### Arif (`~/Documents/team6/arif/wrk/gc1`)

Good:
- Metadata first, deep extraction later (`IDEA.md` principle 1).
- Dedupe before embed: unique URL, normalized URL, content hash (`schema.sql`, `schema_fix_10.sql`).
- A quality gate at ingest: substantive in, reactions out (`IDEA.md` principle 3).
- Source tiers (curated, inferred, noise) and a review queue (`schema_access_layer.sql`,
  `access_layer.py SOURCE_TIERS`, `get_review_queue`, `resolve_review`).
- Deterministic ids from the source (`ingest_github_starred.py`, uuid5 of `owner/repo`), so reruns are safe.
- Incremental sync that stops at the first page with nothing new (`ingest_x_bookmarks.py`).
- One client owns the embedding model, dimensions and validation (`embedding_client.py`).
- Cards that degrade cleanly: full, then title only, then bare URL.

Bad:
- Needs a Postgres server with pgvector. Not portable to a small device.
- Embeddings are computed remotely (Nous Portal, `embedding_client.py base_url`). Private text leaves the
  machine, it costs money, and it breaks offline.
- Two vector stores for the same data (pgvector and Chroma `arif_knowledge_pplx_0_6b`).
- Absolute paths and DB config hard-coded in every script. Seven older copies in `vers/`.
- Docs disagree with the database (919, 4,703, 4,835).
- 90 percent of edges are `shared_channel`, which says "posted in the same Discord channel". Low signal.
- Cluster snapshot is stale (computed 2026-09-08, never rebuilt).
- Items never connect to beliefs. Nothing learns from them.
- Grok chats are stored as 500-character summaries, not the person's own words.

### Eldunari (`~/.hermes/eldunari`, design archive `~/Documents/team6/Eldunari`)

Good:
- Router first, one module per task, never load all (`ROUTER.md`). This is the same idea as the hafiz
  router and it has run in production for two months.
- The hot-path contract: always-loaded files are routers only; one fact has one home; a pointer that does
  not resolve is a defect (`ROUTER.md` "Hot-Path Contract").
- Agent memory kept apart from user memory: `anima/<role>/` holds each agent's patterns, experiences,
  principles, relationships.
- Group memory made explicit: `nexus/agreements`, `tensions`, `synthesis`, `practices`.
- A noteworthiness test before anything is written (`CODIFICATION.md`), and templates per document type
  (`templates/`).
- Orda's live state is event-sourced: `events.jsonl` with a sha256 chain, revision checks (CAS), seat
  leases, and a pointer-only projection (`nexus/state/orda/ARCHITECTURE.md` invariants I1 to I8). This is
  the best piece of engineering in the three systems.
- Git as the audit trail: 429 commits since 2026-08-19.
- Archives are kept but never routed (`domains/archive/`, `nexus/zero-context-archive/`).
- The Raptora review (`Eldunari/wrk/mats/ELDUNARI_RECOMMENDATIONS.md`, R1 to R8) names real failure modes.

Bad:
- Absolute paths inside documents (`/path/to/home/.hermes/...`).
- Frontmatter is loose: 119 of 422 tracked Markdown files have no `type`; no confidence, validity or
  per-claim source.
- Agent names from Generation A remain in content (an `anima/leo` experience is signed `agent: azaraki`).
- Numbers drift between prose and machine truth (R1, R2, R3).
- Patterns are written but rarely used: 18 of 78 were cited anywhere (R7). `reuse_count` is hand-edited.
- Facts about A live in `domains/user-profile.md` and also in hafiz. Two homes for one fact.
- A few files are far over any budget (`nexus/state/orda/OWNER-PROMPTS.md` is 15,500 words).
- The semantic index uses a remote model and lags the files (406 of 422).
- The split "Eldunari is internal, Arif is external" forces agents to know which base to ask.

## 2. Store v2 design

### 2.1 One format, three kinds

Every store has the same layout and the same node schema. A manifest says what kind it is.

| Kind | Holds | Pack file (always loaded) | Comes from |
|---|---|---|---|
| `principal` | One person: profile, beliefs, external data, ledger, policies | `PRINCIPAL.md` | hafiz, Arif items |
| `agent` | One agent's own memory: patterns, experiences, principles, relationships, charter | `ANIMA.md` | Eldunari `anima/<role>/`, `domains/<role>-roles.md` |
| `group` | Shared state of a team of agents: agreements, tensions, synthesis, practices, live project state | `NEXUS.md` | Eldunari `nexus/`, `domains/team-ops.md`, `env-tooling.md` |

Agent and group stores belong to a principal (D-004: A's agents are A's data). They nest inside the
principal store, so one folder carries a whole person and their team. That is the unit a companion device
syncs.

```
$EXO_HOME/principals/<pid>/              kind: principal
  STORE.toml                             kind, id, schema_version, pack, embed model, privacy default
  INDEX.md                               router: load rules + generated node table
  PRINCIPAL.md                           one-page pack
  nodes/<domain>/*.md                    typed beliefs (hafiz schema, extended in 2.3)
  timeline/<date>.md                     day summaries
  policies/ training/ method/
  items.sqlite                           external data (Arif layer)            private, git-ignored
  ledger.sqlite                          signed event log (exo.ledger)        private, git-ignored
  corpus/private/                        raw record, mode 700                  private, git-ignored
  corpus/sources/  corpus/archive/       curated source digests; never-routed archive
  index/                                 derived: fts.sqlite, vectors.sqlite, graph.json   git-ignored
  agents/<agent>/                        kind: agent, same layout, pack ANIMA.md
  groups/<group>/                        kind: group, same layout, pack NEXUS.md, plus state projections
```

A nested store has its own `nodes/`, `INDEX.md` and `index/`. It shares the parent's `ledger.sqlite` and
`items.sqlite` (one log and one item table per person keeps the chain and the dedupe simple). Each ledger
event carries a `scope` field (`principal`, `agent:<id>`, `group:<id>`).

### 2.2 Layers

| Layer | Format | Source of truth? | From |
|---|---|---|---|
| L1 Pack | One Markdown page per store, under ~1.5k tokens, links to nodes | Curated | hafiz `PRINCIPAL.md`, Eldunari `ANIMA.md`, `NEXUS.md` |
| L2 Router | `INDEX.md` load-rule table plus generated node table | Curated rules, generated table | hafiz `INDEX.md` + Eldunari `ROUTER.md` and its hot-path contract |
| L3 Nodes | Markdown with frontmatter, 150 to 450 words | Curated (proposals reviewed) | hafiz schema; Eldunari templates add `pattern`, `tension`, `agreed_by`, `review_by` |
| L4 Items | `items.sqlite` rows, metadata first | Rebuildable from ledger + corpus | Arif `items`, tiers, review queue, edges |
| L5 Ledger | `ledger.sqlite`, signed, hash-chained, append-only | Yes, the root | `exo.ledger` + Orda's CAS, lease and projection rules |
| L6 Index | `index/fts.sqlite`, `index/vectors.sqlite`, `index/graph.json` | No, derived, delete any time | Eldunari sidecar (content hash skip), Arif client (model and dims recorded per vector) |

Rules carried over:
- **Hot-path contract (Eldunari).** Only the pack and the router are always loaded. Packs hold pointers,
  never copies. A node id or path that does not resolve is a lint error.
- **One fact, one home (Eldunari).** A fact about the principal lives in the principal store only. Agent
  stores may link to it (`principal:mind/quality-bar`) but never copy it.
- **Machine numbers are truth (R2).** Counts live in snapshot rows or ledger events. Prose cites them
  (`cites: snapshot:<id>`) instead of restating them. Lint warns on bare large numbers in volatile nodes.
- **Archives are kept, never routed.** `corpus/archive/` is excluded from the index.
- **Metadata first, dedupe before embed, gate before ingest (Arif).**

### 2.3 Node schema changes

Same required keys as `method/SCHEMA.md`. Changes:

- `type` adds `pattern` (a recurring regularity an agent recognized, with evidence episodes) and
  `tension` (an open conflict between two positions, with a `review_by` date). Eldunari types map:
  experience to `episode`, pattern and anti-experience to `pattern` (anti gets `polarity: avoid`),
  agreement to `rule` with `agreed_by`, tension to `tension`, synthesis and practice to `rule` if they bind
  and `fact` otherwise, principles to `value`.
- New optional keys: `owner` (store scope that wrote it), `agreed_by` (list), `review_by` (date, R6),
  `polarity` (`do` or `avoid`), `privacy` (`curated` default, `private` keeps it out of agent scopes),
  `author_alias` (old agent names), `summary_auto: true` (summary written by the migrator, needs review),
  `cites` (snapshot or event ids).
- `reuse_count` is dropped as a hand-written field. Use counts come from `retrieval` ledger events (R7).
- Links may cross stores: `{to: "agent:proteus/patterns/verify-served-artifact", rel: see-also}`. Prefixes
  are `principal:`, `agent:<id>/`, `group:<id>/`. No prefix means the same store.
- New rel `derived-from` for a node built from items (`to: "item:<uuid>"`).
- Source `ref` prefixes become generic. Store-relative: `dialogue:`, `private:`, `sources:`, `timeline:`,
  `item:<uuid>`, `event:<id>`, `eldunari:<old path>@<commit>`. Machine-specific ones (`memory:`, `vault:`,
  `file:`) stay legal but are resolved through a per-principal map in `STORE.toml`, not hard-coded.
- Domains stay folders defined by each store's template, not by the schema. Principal template: person,
  mind, relationship, work, systems. Agent template: charter, patterns, experiences, principles,
  relationships. Group template: agreements, tensions, synthesis, practices, systems, projects.

### 2.4 Items layer (from Arif)

`items.sqlite`, one file, SQLite (no server):

```
items(id TEXT PK,            -- uuid5(source_type, source_id): deterministic, reruns are no-ops
      source_type, source_id, url UNIQUE NULL, normalized_url, domain,
      title, description, text,          -- text: short snippet or note body, redacted
      content_hash, tier,                -- curated | inferred | noise
      review_status, reviewed_at, review_note,
      created_at, ingested_at, raw_metadata JSON, privacy, event_id)
item_edges(src, dst, type, weight)       -- quote_of, co_occurrence, similarity (rebuilt), cites
snapshots(id, kind, computed_at, params JSON, counts JSON)   -- clusters, counts: Tier-0 truth
snapshot_members(snapshot_id, item_id, label, probability)
```

- Every insert also appends an `item` ledger event (metadata, content hash, adapter, watermark). The table
  can be rebuilt from the ledger plus `corpus/private/`.
- Items are evidence, not beliefs. Consolidation may propose a node from many items ("A bookmarks a lot
  about local models") with `derived-from` links. A reviews it like any other proposal.
- Chats are not items. Conversations (Grok, ChatGPT, claude.ai) go to the ledger as `utterance` events
  through the existing `exo.ingest` adapters, in the person's own words. Arif's Grok summaries are dropped.

### 2.5 Ledger additions (Orda's state model on the signed ledger)

- New kinds: `item`, `state`, `retrieval`, `lease`.
- `state` events keep Orda's invariants: monotonic revision per project, CAS on write (stale revision is
  refused), a live seat lease for writers, takeover recorded as an event. The ledger's signature and chain
  replace Orda's separate sha256 chain. Projections (`groups/<g>/state/brief.md` and `.json`) are derived
  and pointer-only, as in I6.
- `retrieval` events record which node and item ids went into a prompt. That feeds use counts (R7), the
  never-used lint, and n=1 eval.

### 2.6 What comes from each source

| From | Taken | Becomes |
|---|---|---|
| hafiz | Node schema, router, pack, privacy tiers, lint, provenance, training seeds, timeline | The base format |
| hafiz | A's 49 nodes, pack, timeline, corpus | A's principal store content |
| Arif | Item table, tiers, review queue, deterministic ids, URL and hash dedupe, quality gate, incremental watermark sync, card degrade, single embedding client | `exo.items`, ingest adapters, `exo.embed` |
| Arif | 4,835 item rows, quote_of and co_occurrence edges, last cluster snapshot as history | A's `items.sqlite` |
| Eldunari | MoE router discipline, hot-path contract, one fact one home, noteworthiness test, templates, archive rule | Router rules, lint, `exo.consolidate` gate, store templates |
| Eldunari | anima per agent, nexus per group | `agent` and `group` store kinds |
| Eldunari | Orda event-sourced state (CAS, lease, projection) | `state` events and `exo.state` |
| Eldunari | Heartbeat checks (stale tensions, pattern reuse, experience expiry, orphans), rebuild-index, link finder | `exo lint --health`, `exo index`, embedding-based link suggestions |
| Eldunari | 422 Markdown docs with 429 commits of history | A's agent and group stores, history kept |
| Raptora R1 to R8 | Snapshot truth, date rule, tension triage, discoverability | Lint rules and schema keys above |

### 2.7 What is dropped, and why

| Dropped | Why |
|---|---|
| Postgres and pgvector | Needs a server. At 5k to 100k items a flat or SQLite vector scan is fast enough and the file travels to a device. |
| Chroma | Second copy of the same vectors. |
| Remote embedding API as default | Private text leaves the machine; cost; no offline. Kept only as an opt-in backend behind the gate. |
| All existing vectors (pgvector, Chroma, Eldunari sidecar) | Wrong model for local use. Vectors are derived; rebuild them. |
| `shared_channel` and old `similarity` edges | 158k rows of weak signal; similarity is recomputed from new vectors. |
| HDBSCAN pipeline and `arif_map.html` as core | Nice view, not retrieval. Can return later as an exo-command page reading `snapshots`. |
| Grok 500-character summaries | Replaced by verbatim ingest through `exo.ingest.grok_export`. |
| SOUL.md, MEMORY.md, USER.md hot-path files | Replaced by pack plus router. The contract they enforced is kept. |
| Per-agent `index.md` files and hand-kept `reuse_count` | Generated from nodes and the retrieval log. |
| "Two knowledge bases" split (internal vs external) | One store, many layers. Agents ask one retrieval call. |
| Eldunari "every file links two others" | Replaced by an orphan warning; forced links were noise. |
| Gen A venture roster in `NEXUS.md` | Kept in archive, marked `valid_until`. Not loaded. |
| `vers/arifv1` to `v7`, `ai work/Hermes/arif` | Duplicates (D-007 already marks them drop). |
| Kanban scaffolding (R8) | Exo has its own task surface. |

## 3. Retrieval

One call, `exo.retrieve.assemble(query, scope, budget)`. Stages:

1. **Pack.** Always first, prompt-cached, outside the retrieval budget: the principal pack, plus the
   calling agent's `ANIMA.md` and its group's `NEXUS.md` when an agent is asking.
2. **Router pins.** Parse the load-rule table in each `INDEX.md` in scope. Match the query against each
   rule's trigger text (keyword overlap, then embedding similarity of the trigger, threshold 0.5). Matched
   rules pin their nodes. Pinned nodes enter first, in full.
3. **Candidates.** Two lists over nodes and items in scope:
   - BM25 through SQLite FTS5 with column weights (title 3, summary 2, load_when 2, tags 1, body 1).
     Top 50.
   - Embedding cosine over `index/vectors.sqlite`. Top 50.
   - Fuse with reciprocal rank fusion (k = 60). Nodes get a 1.5 prior over items.
4. **Filters.** Drop nodes past `valid_until` unless the query asks about the past. Drop `privacy: private`
   nodes and private items for agent scopes. Never return `corpus/private/` or `corpus/archive/` text.
5. **Graph expansion.** From the top 8 nodes, walk one hop. Weights: `supersedes` swaps in the newer node;
   `contradicts` pulls the other side in (both views matter); `explains`, `evidence-for`, `applies-to`
   0.5; `part-of`, `instance-of` 0.4; `see-also` 0.3; `derived-from` adds up to 3 item cards. Expanded
   nodes join the ranking at parent score times weight.
6. **Optional rerank.** A small cross-encoder on desktop only. Off by default and off on device.
7. **Pack under budget.** Default 1,500 tokens, set per task. Split: pinned nodes first, then about 70
   percent nodes and 30 percent items of what is left. Nodes go full, then summary only. Items go as
   cards: full card, then title only, then bare URL (Arif's three tiers). Each block carries its id so the
   model can call `exo get <id>`.
8. **Log.** Append a `retrieval` event with the ids and token count.

Token counting stays chars / 4 by default. A tokenizer can be plugged in.

### 3.1 Local embedding model

Must run on a laptop today and on a small ARM board with 2 to 4 GB of RAM later.

| Model | Size | Dims | Context | License | Fit |
|---|---|---|---|---|---|
| **EmbeddingGemma 300M** (default) | 308M; under 200 MB RAM quantized | 768, Matryoshka down to 128 | 2k | Gemma terms | Built for phones and small boards. Multilingual, which matters for A's Arabic and Urdu. |
| Qwen3-Embedding 0.6B (alternative) | 0.6B; about 640 MB at q8 | 1,024, Matryoshka down to 32 | 32k | Apache 2.0 | Stronger on long text. Use if the Gemma terms are not acceptable, or on the desktop. |
| bge-small-en-v1.5 (floor) | 33M | 384 | 512 | MIT | For very weak hardware. English only. |

Choices:
- Store vectors truncated to **256 dims**, normalized, as int8. 100k items is about 25 MB.
- Runtime: `llama-server --embedding` with a GGUF file (llama.cpp is already installed on A's Mac and runs
  on ARM boards), or ONNX Runtime. Both behind one `Embedder` protocol.
- Every vector row records `model`, `dims`, `content_hash`. Changing the model means `exo index rebuild`.
  Unchanged text is never re-embedded.
- Model choice is A's (his standing rule). The default above ships unless he picks otherwise. Decision
  listed in section 6.

## 4. Migration plan (copies only)

Ground rules:
- Sources are opened read-only. No commit, checkout, write or delete in `~/.hermes/eldunari`,
  `~/Documents/team6/arif`, the Arif Postgres database, or `~/hafiz`.
- The migrator refuses to run if the destination is inside a source path.
- First target is a staging store: `~/.exo/staging/a/`. It becomes `~/.exo/principals/a/` only after lint
  and eval pass (stage M1 in `docs/ARCHITECTURE.md`).
- Orda is live. Take the Eldunari copy at a quiet moment, then run Orda's `verify` on the copy. If the copy
  is mid-write, take it again.

### 4.1 Freeze copies

1. `git -C ~/.hermes/eldunari bundle create ~/Work/exo-salvage-20261011/eldunari-<date>.bundle --all`
   (reads the repo, writes only the bundle).
2. `rsync -a --exclude .git ~/.hermes/eldunari/ ~/Work/exo-salvage-20261011/eldunari-worktree-<date>/`
   (captures the 8 uncommitted Orda files and the ignored rehearsal state as files).
3. `git -C ~/Documents/team6/arif/wrk/gc1 bundle create .../arif-gc1-<date>.bundle --all`, plus an rsync of
   the work tree without `chroma/`, `*.jsonl` and mail exports.
4. Arif database: open a `SET TRANSACTION READ ONLY` session and export `items`, `item_edges`,
   `cluster_snapshots`, `cluster_memberships` to a private dump under `~/.exo/staging/a/corpus/private/arif/`
   (mode 600). Skip `item_embeddings` and `item_embeddings_backup_minilm`.
5. Chroma: read `chroma/chroma.sqlite3` read-only, export documents and metadata only (no vectors) from
   `arif_knowledge_pplx_0_6b`, dedupe against the Postgres export by content hash.

### 4.2 Keep Eldunari's git history

1. `git clone --no-local <bundle> ~/.exo/work/eldunari-import`.
2. In the clone only, commit the uncommitted Orda files from step 4.1.2 as
   "snapshot of uncommitted state, <date>".
3. Scan the full history for secrets (`gitleaks detect` or exo's `LEAKS` and `redact()` patterns). If
   anything hits, rewrite it out in the clone with `git filter-repo --replace-text`.
4. `git filter-repo --to-subdirectory-filter agents/_eldunari` in the clone. Every old commit now lives
   under one prefix. (`git-filter-repo` is installed.)
5. In the staging store repo: `git remote add eldunari ../work/eldunari-import`,
   `git merge --allow-unrelated-histories eldunari/main`. All 429 commits are now in the store's history.
6. One migration commit then moves and rewrites files to their v2 paths with `git mv` first and content
   edits second, so `git log --follow` traces every node back to its first Eldunari commit.
7. Each converted node gets a source `eldunari:<old path>@<last commit sha>`.

### 4.3 Eldunari file mapping

| Eldunari | Count | v2 destination | Type |
|---|---|---|---|
| `anima/<role>/ANIMA.md` | 6 | `agents/<role>/ANIMA.md` | pack |
| `anima/<role>/patterns/*` | ~84 | `agents/<role>/nodes/patterns/` | `pattern` |
| `anima/<role>/experiences/*` | ~146 | `agents/<role>/nodes/experiences/` | `episode` |
| `anima/<role>/principles/*`, `relationships/*` | rest of anima | `agents/<role>/nodes/principles/`, `relationships/` | `value`, `entity` |
| `anima/<role>/index.md` | 6 | dropped (regenerated) | |
| `domains/<role>-roles.md` | 6 | `agents/<role>/nodes/charter/`, split by heading into 150 to 450 word nodes | `rule`, `procedure` |
| `domains/team-ops.md`, `env-tooling.md` | 2 | `groups/protean/nodes/systems/`, split | `procedure`, `fact` |
| `domains/ventures.md`, `products.md` | 2 | `belief_proposed` events against A's `aska/` and `work/` nodes; text kept in `corpus/sources/` | review |
| `domains/user-profile.md` | 1 | `corpus/sources/eldunari-user-profile.md` plus proposed diffs to `mind/*`; no new node (one fact, one home) | review |
| `nexus/NEXUS.md` | 1 | `groups/protean/NEXUS.md`; Gen A roster moved to archive | pack |
| `nexus/agreements/*` | 6 | `groups/protean/nodes/agreements/` | `rule` + `agreed_by` |
| `nexus/tensions/*` | 11 | `groups/protean/nodes/tensions/`, `review_by` = migration date + 14 days | `tension` |
| `nexus/synthesis/*`, `practices/*`, `meta-patterns`, `map/*` | ~12 | `groups/protean/nodes/` | `rule` or `fact` |
| `nexus/events/*` | 12 | group ledger `note` events plus `timeline/` lines | events |
| `nexus/state/orda/events.jsonl` | 488 events | group ledger `state` events; each keeps `orig_seq`, `orig_hash`, `orig_prev` so the old chain still verifies | events |
| `nexus/state/orda/state.json`, `projection/*` | | rebuilt from events, then diffed against the copy as a check | derived |
| `nexus/state/orda/OWNER-PROMPTS.md` | 1 | A's ledger as `utterance` events (role principal, adapter `eldunari`); file in `corpus/private/` | private |
| `nexus/state/orda/OPERATIONS.md`, GitHub logs, handoffs | | `groups/protean/corpus/sources/`, not routed | sources |
| `nexus/state/orda/tool/` | | code: ported to `exo.state`, not data | mechanism |
| `nexus/zero-context-archive/*`, `domains/archive/*` | 72 | `corpus/archive/eldunari/` | never routed |
| Top-level protocol docs (`CODIFICATION`, `FLOW`, `ETL`, `ACCESS`, `GIT`, `HEALTH`, ...) | ~17 | `groups/protean/method/` as reference | reference |
| `templates/*` | 6 | Exo repo `templates/agent-store/`, `templates/group-store/` (generic, with names removed) | mechanism |
| `scripts/*` | 7 | Not copied. Ideas become `exo lint --health` and `exo index` | mechanism |
| `.semantic-index.sqlite3` | | dropped | derived |

Conversion rules for each node:
- `id` = new path. `title` = first H1. `valid_from` = frontmatter `date` or filename date (R5: filename
  date wins only if frontmatter is empty).
- `summary` = first sentence under 160 characters, `summary_auto: true`. A cheap local model may rewrite
  it later; humans review in batches.
- `confidence` from the body's "Confidence:" line, else `medium`. `stability`: `volatile` for episodes,
  `durable` for patterns and rules.
- Markdown links in the body become `see-also` links (pattern-to-experience links become `evidence-for`).
  Absolute `/path/to/home` paths are rewritten to store ids or flagged.
- `agent:` and `recognized-by:` map to `owner`. Gen A names map to current roles through
  `domains/archive/zero-context-map.md`; the old name goes in `author_alias`.
- The 119 files with no `type` get one from their folder. Anything still ambiguous goes to
  `migrate-review.jsonl`, not into `nodes/`.
- Nodes over 600 words are split by heading.

### 4.4 Arif items

1. Read the private dump from 4.1.4. For each row compute the v2 id `uuid5(source_type, source_id or url)`.
2. Map `source_tier` to `tier`, keep `review_status`. Redact `description` and `context_snippet` with
   `exo.ingest.common.redact`. Put `raw_metadata` through the same redactor.
3. Insert into `items.sqlite` and append one `item` event per row (adapter `arif-migrate`, source = dump
   file hash).
4. Keep `quote_of` and `co_occurrence` edges. Drop the rest.
5. Keep the 2026-09-08 cluster snapshot as a `snapshots` row for history only.
6. Skip `grok_conversations` items. Run `exo ingest grok` on the original export instead (verbatim).
7. Do not re-ingest mail. The mbox stays in `mats/Google/`, untouched (decision in section 6).
8. Embed everything locally with the chosen model, then build `similarity` edges (cosine at or above 0.80,
   at most 10 per item).
9. Check: row counts per source match the dump; every item has a vector; `exo lint` clean.

### 4.5 What stays private (never pushed)

The store is its own git repo. It is local only. Pushing it anywhere, even to a private remote, needs A's
approval each time (hafiz `PRIVACY.md`).

Git-ignored in every store and never committed:
`corpus/private/`, `ledger.sqlite`, `items.sqlite`, `index/`, `*.bundle`, `migrate-review.jsonl`,
`groups/*/state/` projections that name live projects, and any file with a leak lint hit.

Committed to the local store repo: packs, `INDEX.md`, `nodes/`, `timeline/`, `policies/`, `method/`,
agent and group nodes, `corpus/sources/` digests that pass the leak lint.

Never copied at all: `.env`, `auth.json`, any token or credential file, `My Drive/backup/`,
`control/wrk/mct6/BRIEFS/`, `~/Documents/Ahraz-Wedding/`, third-party message text outside
`corpus/private/`.

The Exo package repo (`aska-digital/exo`) never contains store data. Templates only.

## 5. Build task list (for a build agent)

Ownership: `src/exo/store.py`, `retrieve.py`, new `items.py`, `index.py`, `embed.py`, `state.py`,
`migrate/`, `ingest/` adapters listed below, `templates/`, matching tests. Do not commit; the lead reviews.

**Store format**
1. Add `STORE.toml` (kind, id, schema_version, pack, embed model and dims, ref prefix map). `Store` base
   class; keep `PrincipalStore` as a subclass for compatibility. Pack file comes from the manifest.
2. Nested stores: `Store.children()` finds `agents/*/STORE.toml` and `groups/*/STORE.toml`.
   `Store.resolve("agent:proteus/patterns/x")` loads across stores.
3. Schema: add `pattern`, `tension` to `TYPES`; `derived-from` to `RELS`; accept optional keys `owner`,
   `agreed_by`, `review_by`, `polarity`, `privacy`, `author_alias`, `summary_auto`, `cites`.
4. Lint: resolve cross-store links; error on unresolved pointers in packs and routers; warn on
   `summary_auto`, tensions past `review_by`, nodes not retrieved in 60 days (from `retrieval` events),
   bare large numbers in volatile nodes without `cites`, absolute home paths in curated files.
   `exo lint --health` runs all of these.
5. Templates: `templates/agent-store/` and `templates/group-store/` with packs, INDEX and empty domains.
   `Store.create(kind=...)`.
6. Fold `~/hafiz/tools/kb.py` behaviour into `exo` CLI so there is one copy of the code.

**Index and embeddings**
7. `exo/index.py`: build `index/fts.sqlite` (FTS5, one table for nodes, one for items, column weights) and
   `index/vectors.sqlite` (doc_id, doc_kind, model, dims, content_hash, vec as int8 blob). Incremental by
   content hash. `exo index rebuild|status`.
8. `exo/embed.py`: `Embedder` protocol with `LlamaCppEmbedder` (HTTP to local `llama-server`),
   `OnnxEmbedder`, and `OpenAICompatEmbedder` (opt-in, blocked unless `policies/gates.yaml` allows data to
   leave). Dimension check, Matryoshka truncation, normalize, int8 quantize. Config from `STORE.toml`.
9. Vector search: flat numpy scan (or `sqlite-vec` if installed). No server.

**Items**
10. `exo/items.py`: `ItemStore` over `items.sqlite` with the schema in 2.4. Upsert by uuid5, URL normalize,
    content-hash dedupe, quality gate hook (`ingest|review|skip`), review queue, edges, snapshots. Every
    insert appends an `item` ledger event; `ItemStore.rebuild(ledger)` replays them.

**Ledger and state**
11. `ledger.py`: add kinds `item`, `state`, `retrieval`, `lease`; add optional `scope` in body.
12. `exo/state.py`: port Orda's tool on top of the ledger: `upsert(project, data, expected_revision)` with
    CAS, `lease claim|renew|release|steal`, `verify`, `project()` writing pointer-only `brief.md` and
    `brief.json`.

**Retrieval**
13. `retrieve.py`: rankers `FtsRanker`, `EmbedRanker`, `rrf()`; `RouterPins` (parse load-rule tables);
    `GraphExpand` with the weights in section 3; scope and validity filters; budget split; item cards with
    three-tier degrade; `retrieval` event. `assemble()` returns sections (pack, pinned, nodes, items) and
    ids. Keep the `Ranker` protocol and the BM25 fallback when no index exists.
14. Keep `store.find` and `store.ctx` working for small stores and tests.

**Ingest**
15. Adapters that write items (rewritten from Arif, no Postgres, paths as arguments): `x_bookmarks`
    (incremental, stop on a full page with nothing new), `chrome_bookmarks`, `apple_notes`,
    `github_starred`, `google_takeout` (Drive, Chat), `discord`, `feed` (manual intake). `mail` exists but
    is disabled unless the principal turns it on.
16. `exo migrate eldunari --src <clone> --dest <store>`: the mapping and conversion rules in 4.3, writes
    `migrate-review.jsonl`, never touches `--src` beyond reads. Refuses if dest is inside src.
17. `exo migrate arif --dump <file> --dest <store>`: 4.4 steps 1 to 5.
18. `exo migrate hafiz --src ~/hafiz --dest <store>`: copy nodes, pack, timeline, training, corpus
    (private keeps mode 700), add `STORE.toml`.

**Tests**
19. Fresh empty principal store runs `assemble()` end to end with no index and no model (D-004).
20. Fixture mini-Eldunari repo (5 files, 3 commits, one Orda chain): migrator output lints clean, history
    follows (`git log --follow`), Orda chain re-verifies from `orig_hash`.
21. Fixture Arif dump (20 rows, 3 sources): counts match, uuid5 stable on rerun, rerun adds nothing.
22. Source safety: migrators open sources read-only (check mtimes and a `git status` before and after on
    fixtures).
23. Retrieval: router pin beats BM25; `supersedes` swaps; private nodes never reach an agent scope;
    budget never exceeded.

## 6. Decisions for A

1. **Embedding model.** Default EmbeddingGemma 300M at 256 dims. Alternative Qwen3-Embedding 0.6B
   (Apache 2.0). Default ships if no answer.
2. **Mail.** Re-ingest Gmail into items (private tier, third-party text redacted), or leave it out.
   Default: leave it out, as it was removed before.
3. **Store backup.** Local only, or an encrypted copy to a private remote. Default: local only plus the
   salvage bundles.
