# Hermes plugin opportunities for Exo

**Status:** Proposed follow-on work for [#497](https://github.com/aska-digital/exo/issues/497).
This report adds research and acceptance criteria. It implements no new runtime features.

Initial implementation issues: [compression #501](https://github.com/aska-digital/exo/issues/501),
[setup migration #502](https://github.com/aska-digital/exo/issues/502) and
[document conversion #503](https://github.com/aska-digital/exo/issues/503).

## Evidence and scope

Reviewed on **2026-10-23**, against Exo `5cf5abf2` on `feat/modular-harness`.
The [live catalog](https://hermes-agent.nousresearch.com/docs/plugins) loads its entries from
[plugins.json](https://hermes-agent.nousresearch.com/docs/api/plugins.json).
Its [metadata](https://hermes-agent.nousresearch.com/docs/api/plugins-meta.json) reports 763 entries:
18 official, 4 verified and 741 community, across nine categories.

The catalog was generated at `2026-10-23T12:23:20.513831+00:00`. The fetched JSON has SHA-256
`3d0444c1ce882c670dee4424d871513da12c9385e64b4a804481fbe0b52b8fcb`.
Catalog metadata was screened for relevant capabilities. Selected pinned documentation and source
were inspected more closely. Plugins were not installed or executed. Catalog inclusion, test files
and repository stars do not establish reliability, adoption or measured benefit for Exo.

Priority below is an engineering judgment about user value, existing Exo support and implementation
cost. It is not the catalog's popularity order. Several plugins share one repository's star count.
The source register pins each selected example so later catalog updates do not change this review.

## Recommended additions

| Order | Capability | Value to users | Existing Exo support and actual gap |
|---|---|---|---|
| 1 | Reversible tool-output compression | Long builds, searches and browser results consume less context while original evidence remains available. | Context trees, compaction and `memory_zoom` already exist. Add a deterministic, bounded rendering of large tool results before model summarization, with exact expansion. |
| 2 | Agent-setup import and refresh | Bring useful setup from Claude Code, Codex, Cursor and Hermes into Exo without rebuilding it by hand. | `exo ingest` imports conversations and workspace instructions are supported on this branch. Config, skills and MCP migration with conflict-aware refresh is a separate gap. |
| 3 | PDF and Office document conversion | Work from specifications, reports, spreadsheets and presentations as well as code. | File tools and MCP are available. No dedicated local document-to-Markdown conversion capability was found. |
| 4 | Unified operations inbox | See stalled jobs, failed schedules, pending decisions and completed work waiting for attention in one place. | The web Command panel already combines operations views. Extend it with durable triage and a shared CLI/JSON query over public status interfaces. |
| 5 | Questions that resume the right task | Keep independent work moving while a human decision is outstanding, then resume the affected task. | `QuestionQueue` in `src/exo/ask.py` already persists learning questions and answers. Wire durable task continuation and client delivery to that mechanism. Protocol question types alone are not a working runtime handler. |
| 6 | Memory and session inspector | Explain what Exo remembers, where it came from, and what happened on a given day. | Signed ledgers, recall, log zoom and memory verification exist. Add a browsable read-only view with source links and principal boundaries. |
| 7 | Memory and skill history | Inspect changes and recover from a bad learning update or accidental skill edit. | Ledger history, proposal undo records and optional Git commits for accepted skill changes exist. Add consistent coverage and reviewable restore plans for mutable knowledge files. |
| 8 | Optional OTLP/Langfuse trace export | Diagnose latency, token use, failed tools and child-agent behavior in existing monitoring systems. | Per-call traces, costs and findings exist. Export those events through a supported adapter instead of recording a second trace model. |
| 9 | General Exo MCP server | Use selected Exo capabilities from another agent or editor without moving the whole workflow. | Exo is an MCP client and its bus already has an MCP server. A permission-scoped adapter for runtime sessions and selected tools is additional work. |
| 10 | Budget visibility and subscription quotas | Explain remaining allowance, pending reservations and why a route is unavailable before a task stalls. | Principal daily/monthly model-spend admission already exists. Improve its UI and add explicit subscription-quota adapters without replacing the journal. |
| 11 | Budgeted skill discovery | Install a large skill collection without putting every description into every prompt. | Skill contents already load progressively, but `SkillIndex.prompt()` emits all descriptions. Add bounded local ranking and explicit-name access. |

### 1. Reversible tool-output compression

Stage 1 is implemented for review in [TOOL-OUTPUT.md](TOOL-OUTPUT.md): optional excerpts
over retained text with exact session-scoped pages. The earlier capture stage below
remains future work in [#501](https://github.com/aska-digital/exo/issues/501).

**Inspiration:** Tameru. Its pinned version shortens older tool results and can retrieve originals.
It currently subclasses private Hermes compressor methods. Exo should expose a supported rendering
interface rather than copy that coupling.

Implement as an optional context provider over existing transcript storage. Preserve tool-call IDs,
exit codes, distinct errors and source labels. Store one authoritative original, then render a shorter
view. Expansion must be paginated and bounded. An unavailable or expired original must be reported.

The current registry caps results at 100,000 characters before logging. Some tools truncate earlier,
including the shell's 30,000-character output cap, split equally between head and tail. Log expansion cannot recover
discarded bytes. Full-output recovery requires a separately bounded capture path before those caps,
with explicit tool coverage, retention and access rules. Keep these two delivery stages distinct.

Acceptance: byte-exact recovery for retained, sanitized text, including after restart. Existing secret
redaction applies before archival and expansion. Tool/result pairing and
untrusted-source labels survive compression and expansion. Fixtures cover logs, JSON, diffs, repeated
errors and malformed text. Measure context savings and CPU time on fixed fixtures before enabling
it in a preset. Do not claim task-quality gains from smaller text alone.

### 2. Agent-setup import and refresh

**Inspiration:** official Agent Importers. Separate setup migration from Exo's existing conversation
ingestion. Begin with Claude Code and Codex setup, then add Cursor and Hermes adapters through the
same normalized import plan. Hermes support is an Exo proposal, not a claim about Agent Importers.

Acceptance: preview mapped, skipped and conflicting items without writes. Never modify the source.
Do not import credentials or translate foreign permission rules into standing authorization. Retain
the scope of imported instructions. Validate MCP configuration without starting its servers.
Hash source and imported copies so refresh preserves local edits, and make identical imports idempotent.
Apply only the reviewed plan and provide a rollback record.

### 3. PDF and Office document conversion

**Inspiration:** doc-markdown, an early community plugin using MarkItDown. Offer converters as a
separate extra, with OCR another optional dependency. Return Markdown and source/page/sheet metadata
under explicit size limits. Image-heavy documents may need OCR and must say when it was not performed.

Acceptance: fixtures for PDF, DOCX, PPTX, XLSX, HTML and CSV. Preserve useful tables and source locations.
Reject unsupported or encrypted inputs clearly. Bound input size, decompression, runtime, memory and
output. Cancellation stops the owned worker. Resolve reads through the selected executor and policy.
Do not fetch external document links or execute embedded content. Disabled conversion imports no parser.

### 4. Unified operations inbox

**Inspiration:** Inbox, a Nous Research Labs plugin classified as community and explicitly unsupported.
Its read-only aggregation is useful independently of its support status. It excludes pending questions
and approvals. Including those in Exo requires supported status interfaces and access checks.

Build a CLI/JSON query first, with the existing Command panel using the same result. Reuse jobs, scheduler,
subagent, findings and approval-desk interfaces. Keep it separate from the signed message-bus inbox.
No model call or polling worker is needed merely to inspect current state.

Acceptance: report unavailable sources as unknown, rather than an empty healthy section. Distinguish
completed work from delivered results. Use executor-aware liveness and process identity, not a bare PID.
Limit history and query time. Respect principal/session access. Reads must not repair, retry or delete
anything. Test mixed failed/running/completed states and missing or locked stores.

### 5. Durable task questions

**Inspiration:** deferred-questions. Its explicit answer IDs, expiry and held uncertain deliveries are
useful. Its private gateway dependencies and platform limits are reasons to implement Exo contracts
independently. The pinned README still describes pre-catalog submission, while the live catalog lists it.

Extend or adapt Exo's existing learning question queue. Preserve its interruption budget and signed
answer evidence. Add task/session binding, expiry, cancellation and a persisted continuation receipt.
Continue independent work while a question is open. Silence never counts as an answer or approval.

Acceptance: wrong-principal, wrong-session, stale and duplicate answers cannot resume work. Restart
retains pending questions. A crash around delivery or continuation produces an explicit uncertain state,
not a blind resend. Queue and transport behavior need concurrency tests. Answers do not bypass the
separate approval desk or grant standing authority. Start with local clients before messaging adapters.

### 6. Memory and session inspector

**Inspiration:** official Memory Wiki. Start with deterministic subject/day/session views over Exo's
existing stores. Use source excerpts and links, with labels separating user statements from derived
beliefs. The first version needs no model or embedding service.

Acceptance: reads cannot mutate memory or write summaries. Pagination bounds large histories.
Private threads and principals remain isolated. Deleted or inaccessible sources are shown accurately.
Keep UI dependencies outside headless installations, and add no prompt text merely to enable the view.

### 7. Memory and skill history

**Inspiration:** memory-rewind. Extend Exo's existing learning rollback rather than snapshotting the
entire principal store or rewinding its signed ledger. Restore a mutable knowledge file as a new,
audited change. Define which tool, learning, terminal and external edits the history actually covers.

Acceptance: a restore preview lists added and removed content. Changed-since-preview files cause a
conflict. Preserve current state for undo. Exclude credentials, caches and raw private transcripts by
default. Document retention and deletion because old versions can retain facts the user removed.
Never claim complete edit coverage from post-tool hooks alone.

### 8. Optional trace export

**Inspiration:** official Langfuse and community hermes_otel. Use Exo trace events and routing costs as
the source. Export session/turn/model/tool/child relationships and approval wait time through an
optional adapter. Start with metadata-only payloads and an explicitly configured destination.

Acceptance: bounded queues, timeouts and shutdown flush. Export failures cannot stall tools or the
gate. No provider key or prompt content in default fixtures. Content capture requires a separate
setting. Disabling export removes its SDK imports, connections and workers. Measure dropped events
and queue pressure instead of silently retrying forever.

### 9. General Exo MCP server

**Inspiration:** official mcp-serve. Begin with selected read-only session/status resources over stdio.
Reuse the existing protocol and bus contracts. Do not start another engine or grant the connecting
client every capability available to the principal.

Acceptance: explicit resource/tool allowlist and identity binding. Cross-session reads obey access
rules. Mutation, outbound messages and approval responses need their existing authority checks.
Cancellation and disconnect release owned work. Test protocol errors and reconnect behavior. Remote
HTTP exposure is a separate authenticated transport, not an implied part of local stdio support.

### 10. Budget visibility and subscription quotas

**Inspiration:** hermes-telemetry and usage-guard. Exo's [existing spend mechanism](MODEL-SPEND.md)
already reserves before dispatch across routed main, child, summary, title and learning calls.
It distinguishes unknown prices and protects policy from seat overrides. Preserve those guarantees.

Build a view of caps, settled spend, pending reservations, refusal reasons and quota freshness.
Subscription providers must be separate adapters with explicit coverage. A quota endpoint cannot
prove a dollar ceiling. Model accounting also does not cover arbitrary paid tools or plugin traffic.

Acceptance: reuse journal inspection without writing it. Stale or unavailable quota data is visibly
unknown. Do not reinterpret it as zero usage or silently disable policy. No default polling, price
fetches, new caps or billing changes at plugin import. CLI subscription support requires its own
reviewed contract before it can participate in strict model-spend admission.

### 11. Budgeted skill discovery

**Inspiration:** Skill Retrieval. Rank skill descriptions locally, within a configurable context
budget, and leave full skill contents behind the existing progressive-load interface. Explicitly
requested skills must remain reachable even when they did not appear in the ranked shortlist.

Acceptance: relevant-skill fixtures and explicit-name cases pass. Disabled skills never reappear.
Edits invalidate the index. Sessions cannot influence one another's selections. Stable instructions
retain a cacheable prefix. Default ranking makes no model or network calls. Compare task success
and prompt size before enabling automatic selection, since a shorter but wrong shortlist can hurt.

## Make these capabilities independently installable

The catalog's packaging is as valuable as individual integrations. Extend roadmap phase 6 with
declarative capability packs containing selected skills, MCP configuration and optional plugin
requirements. A pack is configuration and metadata. It must not need to execute code to explain itself.

Show source revision, compatibility, license, dependencies, tools, hooks, external services and
estimated resource requirements before installation. Keep installed, enabled and available states
distinct. Support pinned resolution, a lock record, previewable upgrades and uninstall with cleanup.
Existing [#261](https://github.com/aska-digital/exo/issues/261),
[#435](https://github.com/aska-digital/exo/issues/435) and
[#459](https://github.com/aska-digital/exo/issues/459) cover related API and execution boundaries.

Add a versioned, read-only observer API over existing Exo events for exporters and status views.
Hermes separates [observer contracts](https://github.com/NousResearch/hermes-agent/blob/db1fa153fe24a051af44ef8ee862b93e27998060/website/docs/developer-guide/observer-hooks.md)
from [behavior-changing middleware](https://github.com/NousResearch/hermes-agent/blob/db1fa153fe24a051af44ef8ee862b93e27998060/website/docs/developer-guide/middleware.md).
Exo currently supports six plugin hooks and rejects unsupported names. Hermes plugins require
compatibility checks before installation. New observer interfaces need correlation IDs, tests and owned
subscriptions. They must not change approval or spend decisions.

Every addition above should be off unless selected. Installation extras should be independent of
runtime presets. An optional provider must not add tools, context, SDK imports, workers or network
calls when disabled. Keep mandatory gate/executor policy outside pack control. Extend the existing
footprint measurement to record enabled and disabled costs before changing any default preset.

## Further optional packs

- **External memory:** Honcho and Hindsight are useful provider examples, with cloud and self-hosted
  modes. Keep Exo's original log authoritative and make destinations, retrieval budgets and identity
  boundaries explicit. This is provider choice, not a reason to replace native memory by default.
- **Artifacts:** Artifact Relay suggests durable private links for reports. Start with local export,
  then optional publishing with a content digest, expiry and the existing exact-content gate.
- **Backup:** S3 suggests an optional remote destination. Exo needs consistent database snapshots,
  checksums and restore verification, rather than copying live database files as generic objects.
- **Vertical integrations:** creative tools, home automation, voice and business-service connectors
  belong in optional packs or existing MCP integrations. Their dependencies should not enlarge the
  coding runtime. A catalog badge does not replace checking the selected version and external service.

## Pinned source register

These are the revisions selected by the observed catalog. Status labels are the catalog's labels.
The examples below use MIT, except hermes_otel, which uses Apache-2.0. No implementation was copied. Future code
reuse must review the specific files and transitive dependencies and update `PROVENANCE.toml`.

| Example | Status | Pinned documentation |
|---|---|---|
| Tameru 1.5.0 | Community | [README](https://github.com/0xWhiteMage/hermes-tameru-plugin/blob/5a19df4af88b848adc29d7170182e226c0700dec/hermes_tameru_plugin/README.md) |
| Agent Importers 1.1.0 | Official | [README](https://github.com/NousResearch/hermes-official-plugins/blob/b5932f1b03ccd0f7738cb4975318df990408f3a3/agent-importers/README.md) |
| doc-markdown 0.1.0 | Community | [README](https://github.com/TakeshiTGAL/hermes-plugin-doc-markdown/blob/4206b7f8066fe84f2c295f480a441f47154d9315/README.md) |
| Inbox | Community, Labs/unsupported | [README](https://github.com/NousResearch/hermes-labs-plugins/blob/62dbc160fab57f893a9eaace79c16c00518c4eca/inbox/README.md) |
| deferred-questions 0.2.4 | Community | [README](https://github.com/web3blind/hermes-deferred-questions/blob/eeb86254725e8ba0d599f85d25c63488efd06364/README.md) |
| Memory Wiki | Official | [README](https://github.com/NousResearch/hermes-official-plugins/blob/abec9e822a23089943dd1d476a104bf746f19f86/memory-wiki/README.md) |
| memory-rewind 1.3.2 | Community | [README](https://github.com/AhmetArif0/hermes-memory-rewind/blob/354927184de03cdc4a9e9e48ddb2865c36368b1f/README.md) |
| Langfuse 2.0.0 | Official | [README](https://github.com/NousResearch/hermes-official-plugins/blob/c2a95963c442f3f23e4bc6fe0da898e1dd1fb63d/langfuse/README.md) |
| hermes_otel 1.18.2 | Community | [README](https://github.com/briancaffey/hermes-otel/blob/d234865b13e997e0b3a50dc18aaad7c7d7b1b1fe/README.md) |
| mcp-serve 1.0.0 | Official | [README](https://github.com/NousResearch/hermes-official-plugins/blob/6389dd08c1e485db708688c69e42c2b7a7360f4e/mcp-serve/README.md) |
| hermes-telemetry 0.9.1 | Community | [README](https://github.com/nujovich/hermes-telemetry/blob/872eadc4b1e6797bc72c8eed3daf54f199d2bed5/README.md) |
| usage-guard 0.3.9 | Community | [README](https://github.com/mister-ideas/usage-guard/blob/e740dcd65849a09a470b2d19b786c996ca44bfe6/README.md) |
| Skill Retrieval 0.6.0 | Community | [Implementation](https://github.com/moonlight-lupin/agent-skills/blob/79801c69e797f5f8efd619f8f451ca89d18c7240/plugins/skill-retrieval/__init__.py) |
| Honcho | Community | [README](https://github.com/plastic-labs/honcho/blob/e2d2aba182862deeb846e5cd4a30a41bce0ed82a/hermes-plugin-honcho/README.md) |
| Hindsight | Community | [README](https://github.com/vectorize-io/hindsight/blob/d952b6e655fccab4581ee0722b51c5f103873d12/hindsight-integrations/hermes/README.md) |
| Artifact Relay | Community, hosted beta | [README](https://github.com/eloktev/hermes-artifact-relay/blob/c91fa9229121dd616a275a45447301eacca45e6f/README.md) |
| S3 | Community | [README](https://github.com/lagoon-labs/hermes-s3-plugin/blob/3d1514e270914d89b4e99db9917eaec25d4e6688/README.md) |
