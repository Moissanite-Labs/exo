# Exo standalone: how the core is built and how Hermes improvements still flow in

Status: proposal, 2026-10-12. Author: the standalone-core research agent.
Inputs: `DECISIONS.md` (D-004, D-005, D-009), `docs/ARCHITECTURE.md`, `docs/CORE.md`,
`docs/PINNING.md`, `research/BASE-EVAL.md`, Exo's `src/` and `plugins/`, and the local Hermes checkout
`~/.hermes/hermes-agent` (main at `dce1e9b`, 2026-10-09, `v0.21.6+canary`). Read only. Nothing in Hermes
was changed.

## 0. The answer in one paragraph

Build a lean new core in Python inside this repo (option b). Lift the few Hermes modules that are small,
clean and proven (the memory and context slot interfaces, provider message types, some built-in tools,
compressor prompts). Rewrite the rest small, using Hermes as the reference for edge cases. Drop the
gateway framework, the dashboard, the desktop app and the TUI gateway. Every lifted or ported module gets
a row in `PROVENANCE.toml` with its upstream path and commit. A script, `exo upstream`, diffs upstream
Hermes for those paths each week and proposes ports. The core runs as a server (`exod`) with one typed
event stream; the TUI, web, desktop, chat channels and a future device are all clients. The six Exo
plugins keep their `register(ctx)` shape and move onto the new core with small import changes.

## 1. What Exo gets from Hermes today

### 1.1 How Exo touches Hermes now

Exo's own package (`src/exo`, about 12k lines) imports nothing from Hermes. Only the plugins do, in 20
import sites across 5 plugins:

| Plugin | Hermes imports |
|---|---|
| exo-memory | `agent.memory_provider` (MemoryProvider, RecallStatus, spawn_context_thread), `agent.context_compressor.is_compaction_summary_message`, `agent.auxiliary_client.call_llm` |
| exo-context | `agent.context_engine.ContextEngine`, `agent.context_compressor` (wraps the whole 5,947-line ContextCompressor as its inner engine), `hermes_cli.config.load_config` |
| exo-gate | no imports; uses hooks `pre_tool_call` and `post_approval_response` |
| exo-bus | `gateway.config`, `gateway.platforms.base.BasePlatformAdapter`, `gateway.platforms.event`, `gateway.platforms.helpers`, `gateway.platforms._shared` |
| exo-command | `hermes_cli.config.load_config_readonly`; reads each profile's `state.db` and `kanban.db` directly (read-only SQL) |
| exo-orchestra | no imports; uses `register_command`, optional `register_skill`, Hermes profile folders |

The coupling is narrow. That is the good news. The one deep dependency is exo-context, which delegates the
actual summarizing to Hermes's compressor.

Tests: `tests/` has 293 tests and the plugin folders add more, for 509 collected in total. One collection
error today: `plugins/exo-memory/tests/test_hermes_smoke.py` needs Hermes importable. (The brief said 232;
the suite has grown since.)

### 1.2 Hermes subsystem map and verdicts

Sizes are tracked non-test files in the local checkout. "Tangle" means imports across layers: 125 files in
`agent/` and 134 in `tools/` import `hermes_cli`, so the "core" and the CLI are not separable as they stand.

| Subsystem | Hermes files | Lines | Tangle | Verdict | What Exo does |
|---|---|---|---|---|---|
| Agent loop | `run_agent.py` + 63 loop files (`agent/turn_*` x32, `conversation*`, `agent_*`, `interrupt*`, `stream*`, `message*`) | 33,081 | High. `run_agent.py` alone has 84 imports; turn logic is split across 32 files | **Rewrite** (~900 lines) | A plain async turn loop: build prompt, stream, run tools, loop, compact. Read `agent/turn_recovery.py` and the interrupt files as the edge-case checklist. |
| Provider message types and transport base | `agent/transports/types.py`, `base.py` | 151 | None (stdlib only) | **Lift** | Becomes `exo.core.models.types`. |
| Provider transports | `agent/transports/anthropic.py`, `chat_completions.py`, adapters (`anthropic_adapter`, `anthropic_message_convert`, `gemini_native_adapter`, `message_sanitization`) | ~8k | Medium. Pull in `tools.registry`, `prompt_builder`, `reasoning_effort` | **Lift, trimmed** (Anthropic Messages and OpenAI-compatible chat only) | Two transports cover Anthropic, OpenRouter, OpenAI, llama.cpp, Ollama, LM Studio, vLLM. Others added on demand. |
| Credentials, aux client, routing | `agent/auxiliary_client.py` (8,416), credential pool x6, fallback, rate files: 82 files under providers+aux | 38,663 | High; the hottest files upstream (170 touches a month) | **Rewrite** (~500 lines) | One routing table in Exo config: role to model, fallback list, cooldown. The logic of A's PR #119652 (lone-credential reprobe) is written in from day one. This is where 0005 (aux routing hook) is no longer needed. |
| Provider plugins | `plugins/model-providers/` 85 files | 2,790 | Low; mostly tables | **Lift as data** | A provider catalog file (base URL, auth style, model ids). Not code. |
| Tool registry | `tools/registry.py` (1,043), `model_tools.py` (992), toolsets | ~3k | High (imports `gateway`, `model_tools`, `toolsets`) | **Rewrite** (~350 lines) | Tool = name, JSON schema, handler, `check_fn`, `risk` hint for exo-gate. Toolsets kept as a concept. |
| Built-in tools | file read/write/patch, search, terminal (subset of 43 tool-core files) | ~20k | Medium | **Lift selectively** | Lift the patch/edit logic and terminal timeout handling, which carry hard-won fixes. Rewrite the wrappers. |
| All other tools | `tools/` 354 files | 127,827 | Varies | **Drop**, lift one at a time on need | Browser, image, video, computer use come later as extensions. |
| MCP client | `tools/mcp_*` 26 files | 11,188 | Medium (OAuth, gateway) | **Rewrite** on the official `mcp` Python SDK (~400 lines) | MCP is how Exo gets most external tools cheaply. |
| Delegation | `tools/delegate*`, `async_delegation`, `subagent_worktree` 12 files | 5,902 | High | **Rewrite** (~300 lines) | A subagent is a child session on the same core with a narrower toolset. A's #107130 (stalled barrier release) logic included. |
| Sessions and state.db | `hermes_state*.py` 34 files | 20,646 | High (gateway, telegram, repair, lock owners, pid namespaces) | **Rewrite** (~500 lines) | `sessions.sqlite`: sessions, messages, events, FTS5 search. Hermes `state.db` stays readable through the existing `exo.ingest.hermes` adapter. A's #98901 (session overrides survive) is a schema property, not a patch. |
| Memory provider slot | `agent/memory_provider.py` | 206 | None (contextvars only) | **Lift** | Becomes `exo.core.slots.MemoryProvider`, trimmed to the methods exo-memory implements. |
| Context engine slot | `agent/context_engine.py` | 250 | Low (one `agent` import) | **Lift** | Becomes `exo.core.slots.ContextEngine`. |
| Default compressor | `agent/context_compressor.py` | 5,947 | High (`hermes_cli`, `tools`, sqlite) | **Rewrite** (~400 lines), lift its summary prompt text | exo-context already holds the part that matters (pinned rulings, handoffs). The core supplies a small default summarizer underneath it. |
| Plugin system | `hermes_cli/plugins.py` (2,264), `plugins/plugin_loader.py` etc. | 2,652 | Medium; 83 hooks, ~25 register methods | **Rewrite** (~400 lines), same method names | Same `plugin.yaml` + `register(ctx)` format, so the six Exo plugins load with little change. Only the hooks Exo uses at first (about 12). |
| Skills loader | `agent/skill_*`, `tools/skill*` 31 files | 12,429 | Medium (gateway, platform) | **Rewrite** (~300 lines) | SKILL.md with frontmatter, same format as Hermes and Claude Code skills, so packs stay portable. A's #110011 (reversible skill ledger) becomes the default: every skill change is a ledger event. |
| Skill content | `skills/` 327, `optional-skills/` 783 files | 298k | n/a | **Drop**; Exo ships its own pack | Individual skills can be lifted as content with provenance. |
| Review and learning loop | `agent/background_review.py` (1,340), `curator.py` (1,212), `review_engine.py`, `learning_*` | ~4k | High (`run_agent`, `hermes_cli`) | **Rewrite** as `exo.learn`, port prompts | D-009: fuse Hermes's branch-off review with Command Code's taste learning and the hill-climbing eval. Builds on `exo.consolidate`, `exo.taste`, `exo.eval`. |
| Cron | `cron/` 41 files | 18,860 | High (`scheduler.py` imports `run_agent`, `gateway`) | **Rewrite** (~400 lines) | Jobs table, asyncio scheduler, a job runs as a session and delivers through a channel. |
| Gateway framework | `gateway/` 187 files | 97,456 | High | **Drop** | Replaced by a small `Channel` interface (~200 lines) on top of the protocol. |
| Platform adapters | `plugins/platforms/` 115 files; core adapters in `gateway/platforms/` 45 files | 86,655 | Medium per adapter | **Lift per adapter, on demand** | WhatsApp, Discord, Telegram first. The platform-specific protocol code is where the edge cases live, so lifting beats rewriting here. |
| Kanban | `plugins/kanban/` | 8,417 | Medium | **Drop**; rewrite small | A `tasks` table in the core plus the subagent model covers the board. |
| Web dashboard | `web/` 242 files + server 36 files | 77,040 | Medium | **Drop** | exo-command becomes the web client of the protocol. |
| TUI gateway | `tui_gateway/` 107 files, 257 JSON-RPC methods | 43,566 | High | **Drop** | Replaced by the Exo protocol (target under 30 methods). |
| Ink TUI | `ui-tui/` 540 files | 105,124 (TS) | Medium | **Drop**; lift components on need (markdown render, diff view) | New small TUI client. |
| Desktop | `apps/desktop/` 3,871 files | 892,316 | n/a | **Drop** | A later desktop is the web client in a thin shell. |
| ACP adapter | `acp_adapter/` 14 files | 4,501 | Low | **Rewrite later** (~300 lines) | Lets editors drive Exo; maps ACP onto the Exo protocol. |
| Profiles | `~/.hermes/profiles/<name>` | n/a | n/a | **Rewrite** as seats | A seat is a config folder: model routing, toolsets, SOUL, skills. Data stays in the principal store. |

Net: Exo carries roughly 10 to 15k lines of core in place of the ~926k lines of non-test Hermes Python it
would otherwise inherit. Lifted code is about 1 to 3k lines at first.

### 1.3 License

Hermes is MIT, "Copyright (c) 2025 Nous Research". MIT allows copying, changing and redistributing as long
as the notice stays with the copied code. So:

- `LICENSES/hermes-agent-MIT.txt` holds Hermes's license text verbatim.
- Each lifted file starts with one header line:
  `# Lifted from NousResearch/hermes-agent <path> @ <commit> (MIT, Copyright (c) 2025 Nous Research). See PROVENANCE.toml.`
- Exo itself stays MIT (`LICENSE`), so lifting is compatible and others can still install Exo (D-004).
- The optchat code already carries its own license file (`plugins/exo-memory/exo_memory/episodic/LICENSE-optchat`); same pattern.

## 2. Options for the core

Criteria fit A's ruling and the device horizon, not the old "distribution" question, so they differ from
BASE-EVAL. Scores 1 to 5, weighted total out of 100.

| # | Criterion | Weight |
|---|---|---|
| K1 | Freedom to evolve (no inherited structure that limits change; A's "no persistent lie") | 20 |
| K2 | Iteration speed (how fast one person plus agents can change the core and trust the tests) | 15 |
| K3 | Reuse of what exists (Exo's 12k-line package, 6 plugins, 509 tests; A's 2026 Hermes PRs and knowledge) | 15 |
| K4 | Lean and device fit (footprint, dependencies, runs on a small board, small-model path) | 15 |
| K5 | Feature coverage at start | 15 |
| K6 | Upstream improvements can still flow in | 10 |
| K7 | Model-agnostic and license | 5 |
| K8 | Clean core/UI boundary | 5 |

| Option | K1 | K2 | K3 | K4 | K5 | K6 | K7 | K8 | Total |
|---|---|---|---|---|---|---|---|---|---|
| (a) Detached hard fork of Hermes, renamed exo | 2 | 2 | 5 | 1 | 5 | 3 | 5 | 2 | 60 |
| **(b) Lean new Python core, lifting chosen Hermes modules** | 5 | 5 | 4 | 4 | 2 | 4 | 5 | 5 | **83** |
| (c1) New core in TypeScript (opencode-style) | 5 | 3 | 1 | 3 | 2 | 2 | 4 | 5 | 60 |
| (c2) New core on the Claude Agent SDK | 2 | 4 | 3 | 2 | 2 | 1 | 1 | 3 | 47 |

Why each scores as it does:

- **(a) Hard fork.** Everything works on day one. But Exo would own 926k lines of Python, 1.3M lines of
  tests and 1M lines of TypeScript that no one on the team can hold. The "lie" A wants gone does not go;
  it just becomes Exo's. The gateway, TUI gateway (257 methods) and desktop shape the UI. Merging upstream
  works for a few weeks and then stops (about 490 commits a day, hottest files touched 86 to 170 times a
  month). It cannot shrink to a device without a rewrite anyway.
- **(b) Lean Python core.** Exo's package, plugins and tests stay in Python and keep working. The lifted
  Hermes modules are Python, so a port is a real diff, not a translation. A's 2026 work survives as logic:
  the four carried patches describe behaviors the new core builds in. Python runs on Raspberry Pi class
  boards; the core needs SQLite, PyNaCl, httpx and a small HTTP server. Coverage starts low, which is the
  cost. The phased plan below gets a usable chat with tools in wave 1.
- **(c1) TypeScript.** opencode's split is the UI model A likes, and TS clients are a good fit. But the
  core in TS means rewriting Exo's 12k lines and 509 tests, and Hermes ports become translations. The UI
  benefit is available to (b) anyway, because clients can be written in any language over the protocol.
- **(c2) Claude Agent SDK.** It drives the Claude Code runtime underneath: Claude models only, a closed
  runtime, no subscription login for third parties (BASE-EVAL section 6). It fails model-agnosticism and
  the small-model device path. Keep Claude as a provider and as a worker over ACP or MCP.

Hybrid note: (b) does not mean every client is Python. The core is Python; the TUI may be TypeScript or
Python, decided by a spike in wave 3. The protocol makes that choice cheap to change.

## 3. Selective rebase: tracking Hermes after detaching

Goal: no runtime dependency, but upstream fixes and good ideas still reach Exo on purpose, reviewed, one
port at a time.

### 3.1 `PROVENANCE.toml` (repo root)

One entry per Exo module that came from Hermes, whether lifted or ported. TOML, read with the stdlib
`tomllib`.

```toml
[upstream.hermes]
repo = "https://github.com/NousResearch/hermes-agent"
license = "MIT"
notice = "LICENSES/hermes-agent-MIT.txt"

[[module]]
exo = "src/exo/core/slots.py"
mode = "lift"                 # lift = copied then edited; port = rewritten from it; watch = idea source only
upstream = ["agent/memory_provider.py", "agent/context_engine.py"]
commit = "dce1e9b3758"        # the upstream commit last reconciled
reconciled = "2026-10-12"
changes = "Trimmed to methods exo-memory and exo-context use; no contextvars thread helper."

[[module]]
exo = "src/exo/core/models/router.py"
mode = "port"
upstream = ["agent/auxiliary_client.py", "agent/credential_pool*.py", "agent/fallback_cooldown.py"]
commit = "dce1e9b3758"
reconciled = "2026-10-12"
changes = "Rewritten as one routing table. Includes PR #119652 reprobe behavior."

[[watch]]                     # upstream areas Exo has no code for yet but wants to hear about
upstream = ["plugins/model-providers/**", "plugins/platforms/whatsapp/**"]
why = "New providers; WhatsApp adapter to lift in wave 4."
```

Rules:
- Every file with a lifted header must have an entry, and every entry's `exo` path must exist. A test
  (`tests/upstream/test_provenance.py`) enforces both.
- `commit` only moves forward when someone has looked at the upstream changes since the last value and
  ported or rejected them.

### 3.2 `exo upstream` (module `src/exo/upstream.py`)

1. Keeps a bare mirror at `$EXO_HOME/upstream/hermes.git` (fetch only, no checkout, never touches
   `~/.hermes`).
2. Picks a target: the latest stable release tag by default (same rule as `pin-hermes.sh` today), or
   `--to <ref>`.
3. For each entry, runs `git log -M --follow` and `git diff <commit>..<target>` on its upstream paths.
   Rename detection matters: Hermes splits files often (the 34 `hermes_state_*` files came from one).
4. Drops noise: commits that touch only tests, docs, website or catalogs; bot merges with no code change.
5. Ranks what is left: `fix(` and security first, then `feat(`, then refactors. Size and whether the
   commit touches code Exo actually lifted (by matching hunks against the lifted functions) raise the rank.
6. Writes `upstream/REPORT-<date>.md`: per Exo module, the ranked upstream commits with one-line
   summaries and links.
7. For `lift` entries, also writes a candidate patch with paths rewritten to the Exo file and tries
   `git apply --3way --check` against a scratch worktree of Exo. Clean ones are marked "applies".
   `port` entries get the diff only; a person or agent reads it and writes the change by hand.
8. `exo upstream mark <exo path> --commit <sha> [--rejected "reason"]` moves `commit` forward and logs
   the outcome as a ledger event, so the history of what was taken and what was refused is kept.

Cadence: weekly, as a cron job in Exo itself once cron exists (wave 3). The report lands in the command
centre's decisions queue. A big upstream change ("progress significantly", in A's words) shows up as a
cluster of high-ranked commits on one module, which is the signal to schedule a deliberate port.

### 3.3 What happens to today's Hermes machinery

- `scripts/pin-hermes.sh`, `patches/`, `tests/contract/`, `docs/PINNING.md` keep working until cutover,
  because A's live Hermes profiles still run on Hermes. At cutover (wave 4) they move to
  `legacy/hermes-distribution/`.
- The four patches stop being Exo's concern. Their behaviors are built into the Exo core (see the table in
  1.2). The upstream PRs can stay open as A's contributions to Hermes, independent of Exo.
- 0005 (aux routing hook) is withdrawn, as D-009 says.

## 4. Re-homing the six plugins

The new core keeps Hermes's plugin format: a folder with `plugin.yaml` and `register(ctx)`. The context
object offers the same method names the plugins call today. So most of the rework is changing imports.

| Plugin | Today on Hermes | On the Exo core | Rework |
|---|---|---|---|
| exo-memory | `MemoryProvider` subclass; uses `call_llm`, `spawn_context_thread`, `is_compaction_summary_message` | Subclass `exo.core.slots.MemoryProvider`. `call_llm` becomes `exo.core.models.aux("summarize", ...)`. Thread helper becomes a core task helper. Compaction marker comes from `exo.core.context`. | Small: about 30 lines across `provider.py` and `episodic/compact.py`. Drop `test_hermes_smoke.py`, add a smoke test on the Exo core. |
| exo-context | `ContextEngine` wrapper around Hermes's `ContextCompressor`; reads Hermes `compression:` config | Subclass `exo.core.slots.ContextEngine`. Inner engine becomes the core's default compressor. Config from `exo.config`. Commands `/exo branch`, `/exo handoff`, `/exo resume` unchanged (they already use `exo.dispatcher`). | Medium: `default_inner_factory` and `hermes_compression_kwargs` rewritten (about 80 lines). Pinned-block and handoff logic unchanged. |
| exo-gate | hooks `pre_tool_call`, `post_approval_response`; Hermes approval transport | Same hook names in the core. Asking the principal becomes a protocol event `approval.requested`; any client (TUI, web, phone channel, device) can answer. The tool `risk` hint from the registry feeds the classifier. | Small: hook signatures checked, approval path swapped. `exo.gate` itself unchanged. |
| exo-bus | Hermes platform adapter (`BasePlatformAdapter`) plus tools | Exo `Channel` (a protocol client that maps bus messages to `turn.send` and events back to signed envelopes). Tools unchanged. `exo.bus` (relay, client, wire) unchanged. | Medium: `adapter.py` (201 lines) rewritten to the Channel interface; `settings.py` drops `get_scoped_secret` for the core's secret lookup. |
| exo-command | Hermes dashboard plugin; reads `state.db` and `kanban.db` read-only | Web client of the Exo protocol. Live agents, tasks, decisions and cost come from protocol queries and the event stream, not SQL on another program's database. The Hermes reader (`ro.py`, `agents.py`, `kanban.py`) moves to an importer used during migration. | Medium to large on the data layer; `projects.py`, `decisions.py`, `costs.py` logic reused. It grows into the main web UI. |
| exo-orchestra | Hermes profiles, `/orchestra` command, optional `register_skill` | Role templates become seat templates. Skills load natively from the Exo pack (no `skills.external_dirs` workaround). | Small: `orchestra.py` profile rendering targets the seat format. |

Where the plugins live: they stay in `plugins/` through wave 2 so history and tests stay put. Once stable,
they become built-in extensions under `src/exo/ext/` that a seat enables in config. They remain
plugins in shape, so a user can still turn any of them off or replace one (D-004).

## 5. Core/UI boundary: the Exo protocol

### 5.1 Shape

- **One process owns state:** `exod`, the core server. It holds sessions, runs turns, tools, plugins,
  cron and channels. Clients hold no state that matters.
- **Commands in, events out.** Clients send small JSON commands. The core publishes a single ordered event
  stream. Every event carries `seq`, so a client that drops off (a phone, a device on bad radio) resumes
  with `since=<seq>` and misses nothing.
- **Transports, one schema:**
  - HTTP + Server-Sent Events over a Unix socket by default (local-first, no open port).
  - The same over TCP on localhost or the LAN, with a token, for other devices.
  - stdio JSON lines, so a TUI can launch the core as a child process (how Hermes's TUI works today).
  - Remote over the Exo Bus or a tunnel later. The schema does not change.
- **Typed once:** the schema lives in `src/exo/protocol/schema.py` as dataclasses. A script generates
  JSON Schema and TypeScript types from it, so TS clients and Python clients agree.
- **Small on purpose:** under 30 commands and under 30 event types. Hermes's TUI gateway has 257 methods;
  that is the sprawl to avoid. New features add event types or fields, not ad hoc methods.
- **Versioned:** `protocol: 1` in the handshake. Additive changes only within a version.

### 5.2 First draft of the surface

Commands: `hello`, `session.create`, `session.list`, `session.get`, `session.fork`, `session.resume`,
`turn.send`, `turn.interrupt`, `approval.respond`, `question.answer`, `command.run` (slash commands,
including `/exo ...`), `events.subscribe`, `search`, `config.get`, `config.set`, `models.list`,
`tools.list`, `skills.list`, `tasks.list`, `seat.list`.

Events: `session.created`, `session.updated`, `turn.started`, `turn.completed`, `message.delta`,
`message.completed`, `reasoning.delta`, `tool.started`, `tool.output`, `tool.completed`,
`approval.requested`, `approval.resolved`, `question.asked` (from `exo.ask`), `context.compacted`,
`handoff.written`, `task.updated` (subagents and the board), `usage.updated` (tokens and cost),
`decision.queued`, `notice`, `error`.

Envelope: `{"seq": 1042, "ts": "...", "session": "s_...", "type": "tool.started", "data": {...}}`.
Events are written to `sessions.sqlite` before they are sent, so replay and the ledger mirror read the
same record.

### 5.3 Why this fits the UI A wants

opencode's simplicity comes from this exact split: a headless server and thin clients. Command Code's
branch-off learning is a background session on the same server whose results arrive as events. Hermes's
robustness lives in the core (retries, routing, gates, compaction, plugins), which every client gets for
free. A device is just another client at first, then a place the core can run.

### 5.4 Device budget (checked by tests from wave 1)

- Core runtime dependencies: PyNaCl, httpx, a small ASGI server (Starlette and uvicorn, already used by
  the bus relay). No Node, no browser engine, no Docker.
- Core source under 8k lines at the end of wave 1 and under 15k at the end of wave 3, excluding
  extensions, channels and clients. A test counts lines and fails over budget, so growth is a decision.
- Idle memory target under 80 MB; no thread per plugin; SQLite only.
- Small-model path: the OpenAI-compatible transport talks to llama.cpp, Ollama or LM Studio; routing can
  send routine calls to a local model.
- The principal store (D-009, hafiz format) and `sessions.sqlite` are plain files that copy onto a device.

## 6. Phased build plan

Same method as D-008: waves of parallel agents, disjoint paths, no agent commits, the lead reviews, runs
the full suite, commits and pushes. A's live Hermes keeps running, untouched, until cutover.

| Wave | Goal | Exit test |
|---|---|---|
| **1 Core skeleton** | Loop, providers, tools, sessions, slots, plugin loader, protocol server, provenance, `exo upstream` | `exo chat` runs a multi-turn session with tools against a local model or an API key, persists, resumes, and streams to two clients at once. Old suite still green. |
| **2 Re-home** | The six plugins on the core (section 4), skills loader, MCP client, seat config | exo-memory and exo-context run a session where a correction survives compaction verbatim; exo-gate blocks a public action until a client answers `approval.requested`; bus round trip verifies. |
| **3 UI and learning** | TUI client (spike TS Ink vs Python Textual, pick one), web client (exo-command grows), `exo.learn` (review loop + taste + hill-climbing eval), cron, subagents and tasks | A works a full day in the Exo TUI on a canary seat. A lesson is promoted only after it scores better on held-out cases. |
| **4 Channels and cutover** | Lift WhatsApp, Discord, Telegram adapters with provenance; ACP adapter; import Hermes `state.db` history; move the Protean team seats | Canary seat, then team seats, then A's root seat, each with rollback to Hermes. Hermes machinery moves to `legacy/`. |
| **5 Device** | Footprint audit, local small-model routing defaults, core on a Pi-class board, thin device client over the bus | The core runs on the board with an empty store and with a copy of A's store; the device client chats and approves. |

### 6.1 Wave 1 in detail

**Before the wave (lead, about 1 hour).** The lead writes `src/exo/core/types.py` and
`src/exo/protocol/schema.py` first, so the five agents code against fixed interfaces:
`Message`, `ContentPart`, `ToolCall`, `ToolResult`, `Usage`, `ToolSpec`, `Provider` (protocol class with
`stream(messages, tools, **opts) -> AsyncIterator[ProviderEvent]`), `Event` envelope, the command and event
dataclasses from 5.2. Also the empty `PROVENANCE.toml` and `LICENSES/hermes-agent-MIT.txt`.

**Five agents, disjoint ownership:**

| Agent | Owns | Builds | Lifts (with header and PROVENANCE row) | Tests (owns) |
|---|---|---|---|---|
| W1-A models | `src/exo/core/models/` | `anthropic.py` (Messages API, streaming, tool use, prompt caching), `openai_chat.py` (OpenAI-compatible: OpenRouter, llama.cpp, Ollama, LM Studio), `router.py` (role to model, fallback, cooldown, lone-credential reprobe), `catalog.toml`, `fake.py` (scripted provider for tests), `aux()` helper | `agent/transports/types.py`, `base.py`; message conversion from `agent/anthropic_message_convert.py` and `chat_completions.py`, trimmed | `tests/core/models/`: streaming parse, tool-call assembly, fallback on 429, reprobe, no network (recorded fixtures) |
| W1-B tools | `src/exo/core/tools/` | `registry.py` (spec, `check_fn`, `risk`, toolsets), `builtin/fs.py` (read, write, edit/patch), `builtin/search.py` (glob, grep), `builtin/shell.py` (bash with timeout, cwd, output cap), hook points `pre_tool_call` / `post_tool_call` | patch/edit apply logic and terminal timeout handling from Hermes file and terminal tools | `tests/core/tools/`: schema output, patch edge cases, timeout kills child, hook can block |
| W1-C loop and sessions | `src/exo/core/loop.py`, `sessions.py`, `context.py` | Async turn loop (stream, tool rounds, max-iterations, interrupt, retry), `sessions.sqlite` (sessions, messages, events, FTS5), fork and resume, default compressor, memory and context slot call sites, events written before publish | compressor summary prompt text from `agent/context_compressor.py` | `tests/core/loop/`: fake provider drives tool rounds, interrupt mid-stream, compaction trigger, resume after restart, fork lineage |
| W1-D plugins, slots, provenance | `src/exo/core/plugins.py`, `slots.py`, `hooks.py`, `src/exo/upstream.py`, `PROVENANCE.toml` (schema and checker) | Loader for `plugin.yaml` + `register(ctx)` folders and entry points; ctx with `register_tool`, `register_hook`, `register_command`, `register_memory_provider`, `register_context_engine`, `register_channel`, `register_skill`; `exo upstream report` and `mark` | `agent/memory_provider.py`, `agent/context_engine.py` | `tests/core/plugins/`, `tests/upstream/`: loads a sample plugin, slot exclusivity, provenance consistency, report on a fixture repo |
| W1-E protocol, server, client | `src/exo/protocol/` (except `schema.py`), `src/exo/server/`, `src/exo/cli_chat.py` | `exod` (HTTP + SSE on a Unix socket, TCP with token, stdio mode), subscribe with `since`, schema export to JSON Schema and TS types, `exo chat` line client, `exo serve` | none | `tests/protocol/`: two clients see the same ordered events, reconnect with `since` replays, unknown command rejected, schema export stable |

**Shared files** (`src/exo/cli.py` to add `serve`, `chat`, `upstream`; `pyproject.toml` to add httpx and
the server extra) are edited only by the lead after the agents finish.

**Wave 1 acceptance (lead runs):**
1. Full suite green: the existing 509 tests (minus Hermes-only smoke tests, which skip) plus the new ones.
2. `exo serve` then `exo chat` against `fake`, against a local llama.cpp model, and against an API key if
   present: a three-turn session that reads a file, edits it and runs a shell command.
3. Kill and restart `exod`; `exo chat --resume` continues the session.
4. A second client attached with `since=0` shows the same event sequence.
5. `exo upstream report` against a fresh mirror lists changes for the two lifted slot files since
   `dce1e9b`.
6. Budget test: core under 8k lines; `pip install` adds only the listed dependencies.
7. No path, name or account of A in any new file (the existing leak check).

## 7. Risks

| Risk | Mitigation |
|---|---|
| The lean core reinvents bugs Hermes already fixed | Lift where edge cases live (patch tool, terminal, platform adapters). For rewrites, the provenance `port` entry points `exo upstream` at the Hermes files whose fixes still matter. |
| Feature gap while Exo catches up | A keeps Hermes live until each seat moves in wave 4. Nothing is switched off early. |
| Core grows back into a monolith | Line budget test, protocol method cap, extensions outside the core. |
| Upstream ports pile up unread | Weekly report into the decisions queue; ranking keeps it to the top items; `mark --rejected` records refusals so they do not come back. |
| TUI choice wrong | It is a client. Changing it does not touch the core. |
| License notice lost on a lifted file | Header plus the provenance test fails the build. |

## 8. Proposed D-010

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
