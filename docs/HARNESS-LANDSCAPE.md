# Harness landscape and modular evolution

**Status:** Research record for D-088 ([#496](https://github.com/aska-digital/exo/issues/496)). The profiles it led to are built. The roadmap items below are not.

Research snapshot: **2026-10-23**. Scope: current public documentation, repository code/configuration,
and selected issue reports. This is an engineering comparison, not evidence that any harness is best or
that a majority of users prefer a feature. Default branches move. Revision anchors appear below.

The product goal is a capable distribution with a small, inspectable runtime: users should choose what
loads, what enters context, what runs in the background, and what they install. Feature count alone is
not the success metric. Task success, recovery, understandable controls, cost, and resource use matter.

## Evidence and design choices

| Harness | Verified strengths | What Exo should adopt or preserve |
|---|---|---|
| [Pi](https://github.com/earendil-works/pi) | Individually selectable package resources. Typed extension lifecycle. Branch-preserving sessions. Shared CLI/SDK/RPC runtime. Virtual model routing. | Explicit composition and resource filters, deterministic hooks and cleanup, context provenance. Keep advanced behavior optional. |
| [Codex](https://github.com/openai/codex) | Named config and permission profiles. Hooks. Bounded skill catalog. Model/provider settings. Multi-agent limits. Programmatic app-server. | Inspectable effective configuration, independent execution policy, bounded discovery/context, tested protocol contracts. |
| [OpenCode](https://github.com/anomalyco/opencode) | Headless server with generated SDK. ACP. Plan/build agents. Custom tools/plugins. Granular permission rules. Optional LSP diagnostics. | Client/runtime separation, optional editor adapters and diagnostics, explicit role toolsets. |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Ordered profile bundles and overlays. Replaceable services. Owned/disposable registrations. Shared execution backend. Append-only session facts. | Resolve composition before startup, declare dependencies, release registrations on unload, keep file and shell execution in the same environment. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Selective toolsets. Persistent memory/session search. Skills. Isolated delegation. Terminal backends. Messaging and schedules. | Improve Exo's existing feature selection and provider boundaries. Make installation and runtime costs separable. |
| [Claude Code](https://code.claude.com/docs/en/overview) | Lifecycle hooks, plugins/skills, checkpoints with separate code/conversation restoration, targeted context summarization. | Dependable recovery and precise checkpoint coverage. Preserve Exo's exact-payload approval semantics. |
| [Aider](https://aider.chat/docs/repomap.html) | Token-budgeted repository map. Symbol/dependency ranking. Lint/test repair. Git diff and undo workflows. | Optional repository context and verification providers, with strict budgets and measurable effect on task success. |
| [Goose](https://github.com/aaif-goose/goose) | MCP extensions. Configurable distributions. Recipes/subrecipes. ACP clients. | Shareable workflow/profile bundles and optional protocol adapters over existing core services. |

Current names matter: `badlogic/pi-mono` redirects to `earendil-works/pi`. `block/goose` moved to
`aaif-goose/goose`. “Deekseek Harness” is interpreted as DeepSeek's official harness, whose README
labels it a developer preview with breaking changes. Exo should adapt contracts, not depend on its internals.

Pi now includes MCP, codemode, a durable runtime, telemetry contracts, and virtual models. Older comparisons
that describe these as absent are stale. Its README deliberately leaves built-in subagents and plan mode
to packages. Its extensions run with host-process permissions, not an OS sandbox. OpenCode's current LSP
documentation defaults LSP to **off**, explaining memory, synchronization, and latency tradeoffs. These are
strong reasons to offer optional capability packages rather than always-on feature accumulation.

## What public requests actually establish

Reaction/comment counts below were observed on 2026-10-23. They indicate interest in particular reports,
not independent users, representative preference, or a current unresolved defect. Closed issues remain
useful design history. Do not market their original behavior as a competitor's present limitation.

| Signal | Evidence | Exo implication |
|---|---|---|
| Lifecycle customization | Codex [#2109](https://github.com/openai/codex/issues/2109), closed. Requests event hooks. 688 reactions, 76 comments. | Stable hook contracts and examples deserve first-class documentation. |
| Provider stalls | Pi [#4945](https://github.com/earendil-works/pi/issues/4945), open. Reports waits with no events. 35 reactions, 85 comments. | Test first-event/idle deadlines, cancellation, bounded retries, and useful status. |
| Logging overhead | Codex [#28224](https://github.com/openai/codex/issues/28224), closed. 616 reactions, 154 comments. Author records merged reductions. | Bound logging volume and retention. Benchmark idle writes. The author's extrapolation is not a general measurement. |
| Inspectable compact UI | OpenCode [#8501](https://github.com/anomalyco/opencode/issues/8501), closed. Requests expanding/editing summarized paste. 328 reactions. | Compact output and attachments must remain inspectable and editable. |
| Config/state separation | Pi [#2870](https://github.com/earendil-works/pi/issues/2870), closed. Requests XDG locations. 89 reactions. | Preserve explicit config/state/cache paths and a migration plan. |
| Lean clients | Hermes [#38602](https://github.com/NousResearch/hermes-agent/issues/38602) requests client-only installation. | Runtime presets do not replace separately installable clients and extras. |
| Execution locality | Hermes [#18715](https://github.com/NousResearch/hermes-agent/issues/18715) requests remote reasoning with local tools. | Represent executor identity and locality explicitly. Test filesystem/shell consistency. |
| Workflow composition | Hermes [#344](https://github.com/NousResearch/hermes-agent/issues/344) requests dependency-aware roles and recovery. | Reuse sessions/jobs for recipes. Treat the issue's implementation descriptions as historical. |
| Interoperability | Aider [#3314](https://github.com/Aider-AI/aider/issues/3314), [#2525](https://github.com/Aider-AI/aider/issues/2525) request MCP. Goose [#1817](https://github.com/aaif-goose/goose/issues/1817) reports local-model tool problems. | Make Exo's existing MCP discoverable and test provider behavior, not just provider enumeration. |

## Exo baseline and this change

Exo is already a standalone harness with substantial functionality. The current sources and
[developer guide](DEVELOPER-GUIDE.md) take precedence over the historical Hermes-based architecture sketch.

| Area | Existing baseline | Remaining work |
|---|---|---|
| Memory and sessions | Signed ledger. Append-only agent log and summary tree. Recall, compaction, fork/resume/undo. | Explicit context-source budgets. Recovery coverage. Portable export/replay semantics. |
| Execution and consent | Exact-payload outward-action approvals, signed responses, required-executor contract. | Additional local/remote/container implementations and coverage tests. Tool selection is not sandbox isolation. |
| Extensibility | Plugin folders/entry points, hooks/tools/commands, memory/context slots, rollback on registration failure. | Dependable teardown/dependency contracts and clearer compatibility/version guarantees. |
| Product capabilities | Skills, MCP, background jobs, subagents, peers, schedules, model fallback, learning. | One effective composition report and optional integrations that incur no disabled startup work. |
| Interfaces | Shared protocol, TUI/web clients, stdio/socket transports, resumable event sequence. | ACP adapter and documented embedded SDK supported by conformance tests. |
| Measurement | Per-call routing/time/cost, explicit unknown prices, traces/findings, held-out learning evaluation. | Public harness/task evaluations, resource budgets, provider conformance, recovery fault injection. |
| Packaging | Small base requirements (`pynacl`, `httpx`), optional `mcp`, `tui`, `bus` extras. | Validate fresh installations by extra. Distinguish wheel/install size from runtime enabled features. |

This change implements three foundations. [HARNESS.md](HARNESS.md) is the configuration reference:

- **Runtime profiles:** `lean` keeps core coding tools, sessions, tracing and the gate. `coding` adds memory,
  context, skills, workspace instructions, configured MCP, jobs and delegation. `full` also selects peers,
  scheduling, learning, bus channels, web and orchestra. Explicit capability overrides are dependency-checked.
  `exo harness list/show` inspects configuration. `use/enable/disable` supports dry runs. Absent harness
  configuration preserves legacy discovery. Service-specific settings can still disable selected services.
- **Plugin lifetime:** folder manifests declare `requires`. Dependencies load first and missing/excluded,
  cyclic or failed requirements block dependent import. Entry-point metadata requires importing the entry
  point first, so its check prevents registration, not import. Synchronous cleanup callbacks run in reverse
  order on failed registration or final manager close, with idempotence and failure isolation. This does
  not add live unloading, hot reload, dependency installation or version solving.
- **Workspace instructions:** opt-in, root-to-session-cwd discovery bounded by the nearest repository.
  `AGENTS.override.md` precedes `AGENTS.md`, with optional `CLAUDE.md` fallback. A 32 KiB content ceiling,
  regular-file checks, rereads, and source/scope/truncation diagnostics make inclusion inspectable. Local
  guidance never becomes approval authority. See [WORKSPACE-INSTRUCTIONS.md](WORKSPACE-INSTRUCTIONS.md).

Profiles select runtime services and tools. They do not grant permissions, provide OS isolation, uninstall
dependencies, or remove foundational session storage. Disabled jobs/delegation use inert implementations
without their managers/resources, but some bundled implementation modules remain imported. This is runtime
selection, not a claim of zero imports or a smaller installation. Required-executor policies remain mandatory.

The extension direction follows [ENGINE-SPLIT.md](ENGINE-SPLIT.md): product wiring belongs in composition,
public interfaces replace private-state reads, and the existing core-line ratchet remains in force.
Do not add protocol commands merely to expose each new feature. Existing protocol extension points and
optional adapters should carry them. Preserve omitted-config behavior and test explicit empty selections.

## Delivery sequence and acceptance gates

The [roadmap issue #497](https://github.com/aska-digital/exo/issues/497) tracks these phases. They are proposed follow-on work
unless explicitly listed in the implementation reference. Each phase should deliver a usable vertical
slice with docs, migration behavior, and reproducible evidence.

| Phase | Deliverable | Gate before claiming completion |
|---|---|---|
| 1. Composition | Named presets, per-capability overrides, effective-config explanation, plugin lifecycle, instruction discovery. | Legacy config still works. Empty/disabled selections behave literally. Required policy cannot be removed. Integration tests observe absent services/tools and deterministic loading. |
| 2. Resource contracts | Context providers for instructions, skills, memory and optional repository map. Token attribution and limits. | Disabled sources contribute zero context. Overflow is bounded and explained. Large/generated/vendor trees are excluded. Edits invalidate cached symbols. No-secret fixtures pass. |
| 3. Editing and recovery | Optional lint/typecheck/test feedback. Checkpoints and transcript/code recovery controls. | Cover built-in writes, patches, renames/deletes, shell writes, subagent edits, concurrent external edits and dirty worktrees separately. Mark unsupported coverage. Restore never silently destroys unrelated changes. |
| 4. Workflow recipes | Versioned declarative DAGs, role-specific profiles, bounded concurrency, cancellation and persisted progress. | Cycle/missing-dependency rejection. Cancellation stops owned work. Restart resumes without duplicating completed side effects. Approval identity survives delegation. Failure/retry behavior is explicit. |
| 5. Clients and execution | Optional ACP client adapter, supported Python embedding API, remote/container executor adapters. | Same task/events/consent across clients. Protocol conformance. Shell/file/LSP use one execution world. Disconnect/reconnect and cancellation pass. Disabled adapters import no extra SDK. |
| 6. Ecosystem and release | Versioned plugin API, compatibility declarations, package manifests, provenance and upgrade diagnostics. | Clean-room installs for each extra. Deterministic lockable resolution. No implicit project-code execution before trust. Uninstall/upgrade releases all owned resources. Backwards-compatibility fixtures pass. |

Model routing, memory, delegation and scheduling already exist in Exo. Strengthen their interfaces and
tests as these phases proceed rather than introducing parallel implementations. Consider constrained
tool-composition/codemode only after policy, cancellation, structured results and accounting remain correct
for nested calls. Its payoff must be measured against extra runtime and debugging complexity.

The [Hermes plugin catalog follow-up](HERMES-PLUGIN-OPPORTUNITIES.md) ranks eleven additions from the
2026-10-23 catalog snapshot. It includes reversible tool-output compression, setup migration, document
conversion, durable task questions and optional trace export. Each proposal names existing Exo support,
the remaining gap and acceptance criteria. Capability packs and a versioned observer API support
independent installation. These are proposed features, separate from the implementation above.

## Evaluation plan: earn comparisons

Maintain two tracks: deterministic harness conformance without network/model keys, and real-model task
evaluation. Run model evaluations only with an explicit run budget and available credentials. Do not
describe mocked task execution as evidence of coding quality.

1. Pin repository commits, harness versions/config, prompts, model IDs, reasoning settings and tool policies.
   Record OS/CPU/RAM, Python/runtime versions, optional extras, network conditions and provider date.
2. Compare Exo legacy/default, lean, coding and full configurations on identical tasks. Compare external
   harnesses using their closest supported common configuration. Separately label product-default runs.
   Provider-native features or unavailable models form separate cohorts, not disguised equal comparisons.
3. Start with 24 held-out tasks: six bug fixes, six multi-file refactors, four unfamiliar-repository navigation
   tasks, four tool/API workflows and four interruption/recovery tasks. Freeze repositories and acceptance
   tests before runs. Never tune prompts against the held-out split. Publish task exclusions and reasons.
4. Run at least three trials per task/config where stochastic models are involved. Randomize order, isolate
   workspaces, and use fresh sessions. Evaluate warm-memory behavior in a separate, identically seeded cohort.
5. Report task acceptance rate, regressions, completion time, first useful action, input/output/cache tokens,
   estimated/actual cost, intervention count, recovery success and policy violations. Keep unknown cost
   unknown. Publish distributions and paired differences. Avoid rankings from small, noisy differences.
6. Capture cold/warm startup, peak and idle RSS, idle CPU, imported optional modules, active processes,
   tool-schema/instruction tokens, installed bytes and log bytes/minute. Measure before and after on the
   same host. Never count line movement or hidden UI controls as a lean-runtime improvement.

Initial **proposed release budgets**, to validate on a documented reference host:

| Budget | Acceptance target |
|---|---|
| Disabled capabilities | Zero tools/context contributions, background workers, sockets or optional SDK imports from the disabled capability. Foundational shared services and bundled-module imports are explicitly documented. |
| Lean initialization | No network calls merely to discover disabled providers/plugins. No MCP/peer/scheduler/channel workers when disabled. |
| Startup and memory | Establish baseline with 20 cold and 20 warm samples. Block unexplained >10% p95 startup or peak-RSS regression for the same profile. Publish absolute numbers too. |
| Context growth | Record per-source token estimates and model tokenizer where available. New optional features add zero tokens when off and remain within configured caps when on. |
| Idle behavior | No feature-owned periodic polling when disabled. Idle CPU/log growth does not increase beyond baseline noise during a 10-minute no-input run. |
| Reliability | All deterministic cancellation, teardown, dependency, no-permission-escalation and replay fixtures pass before model-quality comparisons. |

These are targets, not benchmark results. A successful feature can justify a measured resource cost in
the profiles that enable it. The evidence should make that choice understandable to users.

### Reproducible internal footprint smoke measurement

Run `.venv/bin/python scripts/benchmark_harness.py --samples 3 --json` from an installed source checkout.
Each sample creates a fresh interpreter, disposable `EXO_HOME`, template-only principal store and fake
model configuration, then composes and closes the actual engine. It loads only bundled plugins, starts
no services or model turns, and rejects Python network-connect/bind and subprocess attempts. An empty
temporary `PATH` prevents installed-CLI probes. User configuration and credentials are not inherited.
JSON includes every sample, platform/Python, Git SHA and a digest of live sources. Changing sources during
the run invalidates it. Without `--json`, it prints a table. The user's Exo home is never changed.

Observed **2026-10-23**, Python 3.11.17, Linux 6.18.55 x86_64/glibc 2.41. Three samples/profile,
base Git `01101d3c60aeafbf04949bee1128b4e40e1e92ec`, working-source digest `69fa6acb0c16…`:

The [raw benchmark JSON](evidence/harness-footprint-2026-10-23.json) contains every sample.

| Profile | Median compose + close | Median process wall | Peak RSS median | Registered tools | Schema characters | Approx. schema tokens |
|---|---:|---:|---:|---:|---:|---:|
| lean | 38.2 ms | 426.0 ms | 42.2 MiB | 12 | 7,870 | 1,968 |
| coding | 74.2 ms | 586.4 ms | 44.5 MiB | 21 | 15,306 | 3,827 |
| full | 89.7 ms | 618.4 ms | 45.2 MiB | 27 | 18,234 | 4,559 |

Compose timing excludes imports/store setup. Process timing includes them. RSS covers the whole child.
Schemas include unavailable registered tools and tokens are merely characters/4, not actual provider
tokenization. OS caches remain warm. Three trials on a shared host establish neither a stable performance
baseline nor task-quality/full-startup/competitor comparisons. The clearest result is selectable tool surface.

## Sources, snapshots and licenses

Primary sources inspected for the comparison:

- Pi: [packages](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md),
  [extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md),
  [sessions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md),
  [codemode](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/codemode.md),
  [virtual models](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/virtual-models.md).
- Codex: [configuration schema](https://github.com/openai/codex/blob/main/codex-rs/core/config.schema.json)
  and [app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md). Schema presence
  is configuration evidence, not proof that every experimental flag is stable or available to every account.
- OpenCode: [server](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/server.mdx),
  [plugins](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/plugins.mdx),
  [permissions](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/permissions.mdx),
  [LSP](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/lsp.mdx),
  [ACP](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/acp.mdx).
- DeepSeek: [architecture](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)
  and [Cordis primer](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cordis-primer.md).
- Hermes: [tools](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/tools.md)
  and [memory](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md).
- Claude Code: [hooks](https://code.claude.com/docs/en/hooks),
  [checkpointing](https://code.claude.com/docs/en/checkpointing). Aider: [repository map](https://aider.chat/docs/repomap.html).
  Goose: [custom distributions](https://github.com/aaif-goose/goose/blob/main/CUSTOM_DISTROS.md).

Repository HEADs observed: Pi `ede2c9b468d8e061c557d16957513715e6d49cd6`. Codex
`2839ed763bd3f404398e1f0c95bf4e2d172213c0`. OpenCode `1e89b18186bc73c64d65ac380e39bc1a177c247c`.
File-blob anchors (not repository commits): DeepSeek architecture
`03d88148c7992f1c856efc44df0a930470f90f70`. Hermes tools `9059cffeefc17658429fe2e7dbdaf0f5b0f7b549`.
Goose README `44c960f0193de1ccd3a13721cfe2daf5e7707ac5`. Reads used live default branches, so these
anchors identify the observed snapshot rather than claiming all fetched files were atomically pinned.

Pi, OpenCode, Hermes and DeepSeek Harness use MIT licenses. Codex, Aider and Goose use Apache-2.0.
Claude Code's repository is proprietary under Anthropic commercial terms: use documented product ideas,
not copied implementation. This report recommends independently implemented behavior. Any future source
imports require license/notice review and attribution in [PROVENANCE.toml](../PROVENANCE.toml).
