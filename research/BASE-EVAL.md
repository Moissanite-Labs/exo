# BASE-EVAL: which codebase Exo should stand on

Date: 2026-10-11. Author: saga-divan (base-evaluation subagent). Read-only on ~/.hermes, except one temporary worktree that was created and removed.

## Result

Build Exo on Hermes Agent, as a distribution, not a hard fork.
Track upstream at weekly release tags. Put every Exo feature in Hermes plugins plus a separate `exo` package. Carry a tiny patch queue, and every patch in it must also be an open upstream PR.
Score: Hermes distribution 81, Hermes hard fork 73, OpenClaw 72, OpenCode 68, pi 68, Goose 67. Claude Agent SDK from scratch scored lowest at 54.

## 1. Criteria and weights

Each option is scored 1 to 5 per criterion. Weighted total is out of 100.

| # | Criterion | Weight | What it measures |
|---|---|---|---|
| C1 | Exo feature coverage | 20 | Gateways (WhatsApp, Discord), cron, task board, subagents, memory hooks, compaction, TUI, desktop, live view, already working |
| C2 | Extensibility without core edits | 15 | Plugin API depth: hooks, memory provider, context engine, platforms, tools, UI panels |
| C3 | Model-agnostic, local models | 10 | Any provider, local llama.cpp/Ollama/LM Studio, routing |
| C4 | Maintenance cost | 15 | Cost to stay current with upstream, conflict exposure, codebase size you must understand |
| C5 | Replicability | 15 | A new user installs and personalizes from zero: install path, onboarding, generic code kept apart from one user's data, license allows redistribution, no dependence on A's accounts or machine |
| C6 | Fit with A's investment | 10 | A's PRs, plugins, skills, profiles, running Protean team, his knowledge of the code |
| C7 | License and governance | 5 | Permissive license, neutral or stable stewardship |
| C8 | Architecture clarity | 10 | Size, layering, client/server split, how easy it is to reason about |

## 2. Scored comparison

| Option | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | Total |
|---|---|---|---|---|---|---|---|---|---|
| (b) Hermes, distribution model | 5 | 4 | 5 | 3 | 4 | 5 | 4 | 2 | **81** |
| (a) Hermes, hard fork | 5 | 5 | 5 | 1 | 3 | 4 | 4 | 2 | 73 |
| OpenClaw (openclaw/openclaw) | 5 | 4 | 4 | 3 | 4 | 1 | 3 | 3 | 72 |
| OpenCode (anomalyco/opencode) | 2 | 4 | 5 | 4 | 4 | 1 | 4 | 4 | 68 |
| pi (earendil-works/pi) | 1 | 5 | 5 | 5 | 3 | 1 | 3 | 5 | 68 |
| Goose (aaif-goose/goose) | 2 | 4 | 4 | 4 | 4 | 1 | 5 | 4 | 67 |
| OpenHands | 3 | 3 | 4 | 4 | 3 | 1 | 4 | 3 | 62 |
| Letta Code (letta-ai/letta-code) | 3 | 3 | 3 | 4 | 3 | 1 | 4 | 4 | 62 |
| Cline | 2 | 3 | 4 | 4 | 4 | 1 | 4 | 3 | 61 |
| OpenAI Codex CLI | 3 | 3 | 3 | 3 | 3 | 2 | 3 | 4 | 60 |
| (d) Fresh on Claude Agent SDK | 1 | 4 | 1 | 4 | 2 | 3 | 2 | 4 | 54 |
| Roo Code | archived 2026-05-15, not scored | | | | | | | | |

Replicability (C5) notes per option:
- Hermes (b): MIT, one-line installer, `apps/bootstrap-installer`, profiles keep each user's SOUL, memory and config in `~/.hermes/profiles/<name>/`, outside the code. Exo adds an onboarding interview and keeps A's data in a separate private store. Score 4.
- Hermes (a): same license, but a hard fork means Exo owns the installer, docs and update channel forever and drifts from upstream docs. Score 3.
- OpenClaw: MIT, native apps on every OS and phone, ClawHub registry. Score 4.
- OpenCode, Goose, Cline: clean installers and permissive licenses, but a new user gets a coding agent, not a personal system. Score 4 on install, the gap shows up in C1.
- pi: MIT library stack. A new user gets nothing until Exo builds all of it. Score 3.
- Letta Code: Letta Cloud is the default backend and remote machines and secrets need a Letta account. Fully local use is unverified. Score 3.
- OpenHands: one Docker container per conversation. Heavy for a phone-first user. Score 3.
- Codex: Apache-2.0, but OpenAI steers it and contributing needs a CLA. Score 3.
- Claude Agent SDK: the TypeScript SDK is "all rights reserved, subject to Anthropic Commercial Terms". Third-party products may not offer claude.ai login or subscription limits without approval, so every Exo user would need an Anthropic API key. Claude models only. Score 2.

Facts behind the alternatives (GitHub API, 2026-10-11; commits on the default branch since 2026-08-12):

| Repo | License | Lang | Stars | Commits 60d | Gateways | Memory | Multi-agent |
|---|---|---|---|---|---|---|---|
| NousResearch/hermes-agent | MIT | Python | 252.8k | 29,534 | 30 platforms | provider ABC, FTS5 session search | delegate tool, profiles, kanban |
| openclaw/openclaw | MIT | TS | 391.7k | ~29,700 | 20+ incl. WhatsApp, iMessage | local | multi-session |
| anomalyco/opencode | MIT | TS | 212.7k | 3,532 | none | sessions only | subagents |
| earendil-works/pi | MIT | TS | 114.3k | 1,202 | none | durable runtime pkg | none by design |
| openai/codex | Apache-2.0 + CLA | Rust | 128.7k | 2,977 | none | memories crate | agent-roles crate |
| aaif-goose/goose | Apache-2.0 | Rust | 55.2k | 494 | none | limited | subagents (unverified) |
| OpenHands/OpenHands | MIT | TS + Py | 90.6k | 514 | Slack, GitHub | per conversation | drives other agents via ACP |
| letta-ai/letta-code | Apache-2.0 | TS | 3.6k | 663 | Telegram, Slack, Discord | strongest: blocks, git MemFS, sleep-time | subagents |
| cline/cline | Apache-2.0 | TS | 70.2k | 627 | unverified | partial | unverified |
| claude-agent-sdk py / ts | MIT / proprietary | Py / TS | 8.2k / 1.8k | 135 / 60 | none | CLAUDE.md files | subagents |

Governance changes in 2026: pi acquired by Earendil (April). Goose moved to the Linux Foundation's Agentic AI Foundation. OpenClaw's creator joined OpenAI and the project sits in an OpenAI-sponsored foundation. Roo Code shut down. Letta's original server is retired in favor of Letta Code.

## 3. Hermes measurements

Checkout: `~/.hermes/hermes-agent`, branch main at dce1e9b (2026-10-09), equal to origin/main before the fetch. After `git fetch origin main`, origin/main is 1fd1827 (2026-10-11).

### Size (tracked files)

| Area | Lines |
|---|---|
| Python total | 2,252,544 |
| tests/ | 1,326,380 (59%) |
| Python excluding tests | 926,164 |
| hermes_cli/ | 274,925 |
| agent/ | 134,912 |
| tools/ | 122,995 |
| gateway/ | 96,276 |
| plugins/ | 77,713 |
| tui_gateway/ | 43,414 |
| scripts/ | 37,180 |
| root modules (incl. ~40 `hermes_state_*.py`) | 33,642 |
| optional-skills/ | 31,400 |
| evals/ | 20,007 |
| cron/ | 18,705 |
| pm/ | 12,493 |
| skills/ | 11,588 |
| acp_adapter/ | 4,501 |
| TypeScript + TSX (desktop Electron app, Ink TUI, web dashboard) | 671,887 + 327,108 |

### Extension surfaces

- **Plugin API** (`hermes_cli/plugins.py`): a plugin exposes `register(ctx)`. 83 named hooks in `VALID_HOOKS` (pre/post tool call, LLM call, API request, gateway dispatch, session start/end/reset/finalize, stream deltas, kanban dispatch and worker events, human-input request/resolve, approvals, skill lifecycle). About 25 `register_*` methods: tool, command, cli_command, middleware, system_prompt_section, context_engine, context_reference, memory_provider, platform, platform_handler, auxiliary_task, approval_transport, dashboard_auth_provider, secret source, locale, redaction patterns.
- **Memory provider ABC** (`agent/memory_provider.py`): 22 methods, including `prefetch`, `queue_prefetch`, `sync_turn`, `system_prompt_block`, tool schemas and handler, `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_delegation`, `on_memory_write`, `backup_paths`. In tree: byterover, holographic, retaindb.
- **Context engine ABC** (`agent/context_engine.py`): `should_compress`, `compress`, `update_from_response`. Pluggable via `register_context_engine`.
- **Gateway platforms**: 21 plugin platforms (a2a, buzz, dingtalk, discord, email, feishu, google_chat, irc, line, matrix, mattermost, ntfy, photon, raft, simplex, slack, sms, teams, telegram, wecom, whatsapp) plus core adapters (api_server with OpenAI routes, signal, bluebubbles, webhook, whatsapp_cloud, weixin, qqbot, yuanbao, msgraph). `gateway/platforms/ADDING_A_PLATFORM.md` documents the contract.
- **Model providers**: 40 provider plugins, including anthropic, openai-codex, openrouter, bedrock, vertex, ollama-cloud, lmstudio, router, custom. A already runs a llama.cpp router locally.
- **Delegation**: `tools/delegate_tool*.py` (9 files), `async_delegation.py`, `subagent_worktree.py`, `delegation_live_log.py`.
- **Kanban**: plugin plus `gateway/kanban_watchers*.py` and kanban hooks. **Cron**: `cron/` (18.7k lines) plus cron provider plugins.
- **MCP**: full client (`tools/mcp_tool*.py`, 26 files incl. OAuth) and a server (`mcp_serve.py`). ACP adapter for editor clients.
- **UI**: Ink TUI (`ui-tui/`), Electron desktop (`apps/desktop/`), web dashboard (`web/`), dashboard plugins.
- **Catalog**: 523 entries in `plugin-catalog/`.

### Tests

Ran in a temporary worktree at origin/main 1fd1827 with the existing venv, HOME and HERMES_HOME pointed inside the worktree, no bytecode or pytest cache. Confirmed imports resolved to the worktree, not the live checkout. Subset: memory provider, context engine, plugin API compat, context engine user dir. Result: 66 passed in 3.69 s. Worktree removed with `git worktree remove` and `git worktree prune`; the other 13 registered worktrees are intact. The live working tree was untouched (its three untracked plugin files are A's).

## 4. Fork maintenance analysis

### Upstream velocity

| Window | Commits on origin/main | Distinct authors |
|---|---|---|
| 7 days | 2,469 | 264 |
| 30 days | 16,935 | 1,195 |
| 60 days | 29,534 (28,033 non-merge) | 1,911 |
| 2026-10-09 to 2026-10-11 (one fetch) | 376 | |

Line churn over the same 60 days: 112,359 file changes, 4,227,605 insertions, 2,672,798 deletions.

That is about 490 commits a day. Much of it is likely bot or auto-merge traffic, but every commit still moves code a fork would have to absorb.

### Where upstream churns (file touches, last 30 days, non-merge)

Total file touches: 67,683. By area: tests 20,081, apps (desktop) 15,389, hermes_cli 6,285, website 3,474, agent 3,205, tools 2,607, plugin-catalog 2,048, gateway 1,934, plugins 1,456, tui_gateway 1,274.

Hottest core files: `apps/desktop/electron/main.ts` 352, `gateway/run.py` 178, `agent/context_compressor.py` 170, `agent/auxiliary_client.py` 170, `tui_gateway/server.py` 162, `cron/scheduler.py` 144.

Plugin contract files are much calmer: `agent/memory_provider.py` 5, `agent/context_engine.py` 6, `plugins/plugin_loader.py` 8, `gateway/platform_registry.py` 9. `hermes_cli/plugins.py` is busier at 59, mostly new hooks and registrations. `run_agent.py` 48.

### A's PRs (`gh pr list --author ahrazzle --state all`)

| State | Count | Core only | Core + plugin | Plugin/skill only | Tests/docs only |
|---|---|---|---|---|---|
| Open | 25 | 14 | 1 | 10 | 0 |
| Merged | 4 | 0 | 0 | 4 | 0 |
| Closed | 20 | 13 | 1 | 1 | 2 |
| Total | 49 | 27 | 2 | 15 | 2 |

- Open PRs change 21,135 lines.
- 12 commits on origin/main are authored by A (some work lands via maintainer re-authoring).
- All 4 merged PRs were plugin-only. 13 of 20 closed PRs were core-only. Plugin work lands; core work mostly does not.
- Open core PRs hit the hottest files: `agent/auxiliary_client.py` (170 upstream touches in 30 days), `tui_gateway/server.py` (162), `hermes_cli/config_defaults.py` (135), `agent/chat_completion_helpers.py` (127), `hermes_cli/config.py` (110), `hermes_cli/main.py` (104), `agent/agent_init.py` (86).

### What a hard fork would cost

If Exo carried today's 15 open core PRs as fork patches, those files see 86 to 170 upstream commits a month each. Expect conflicts on most weekly syncs, and on a daily sync more days than not. A single person plus agents would spend a standing lane on rebases. Stopping syncs instead means losing about 15k upstream commits a month of fixes, providers and platforms.

### What the distribution model costs

- A already runs this way: the live checkout is vanilla origin/main plus untracked plugin files, and `~/.hermes/plugins/` holds 8 user plugins (continuum, homeassistant, voicestudio, protean-ordaprompt and others) outside the repo.
- Exo code lives against the calm contract files (5 to 9 touches a month) instead of the hot core files.
- Patch queue budget: at most 5 patches, each an open upstream PR, each dropped when merged or replaced by a hook.
- Pin to weekly release tags (v0.21.x cadence). Run a contract test suite (Exo plugins plus upstream `test_plugin_api_compat.py`) against each new tag before moving the pin.

## 5. Recommendation

**Base: Hermes Agent. Strategy: (b) distribution.**

Layout:
1. `hermes-agent` pinned at a release tag, installed unmodified (or with the small patch queue).
2. `exo` package (separate repo `aska-digital/exo`, MIT so others can install it): installer, `exo init` onboarding, and Hermes plugins.
3. User data store, one per person, never in code. For A this is `~/hafiz` plus his profiles.

Exo features mapped to Hermes surfaces:

| Exo need | Hermes surface |
|---|---|
| Guardian-angel record and principal profile | memory provider plugin backed by the hafiz format (append-only, provenance) |
| Active questioning | cron job plus `on_session_end` hook that queues questions to WhatsApp |
| Memory across sessions | memory provider `prefetch` / `sync_turn` / `on_memory_write` |
| Compaction, branching, handoffs | context engine plugin plus `on_pre_compress` and `on_session_switch` |
| Orchestrator and role agents | profiles plus delegate tool plus kanban hooks |
| Command centre and live view | dashboard plugin on the web dashboard; kanban and session events as data |
| TUI, desktop, phone | existing Ink TUI, Electron desktop, WhatsApp and Discord platforms |
| Model routing incl. local | provider plugins plus the llama.cpp router |
| Signed inter-agent bus (Divan Bus) | platform plugin (pattern exists in `plugins/platforms/a2a`) |
| Later fine-tuning | exo pipeline reading state.db and hafiz; Hermes trajectory tools as input |
| Other engines (Claude Code, Codex, OpenCode) | workers behind ACP or MCP, not the base |

Replicability under this plan: a new user runs one installer, gets upstream Hermes plus Exo plugins, answers the onboarding interview, and gets an empty personal store seeded from their answers. Nothing points at A's accounts, machine or Drive. Generic code and personal data never share a repo.

## 6. Rejected, and why

- **(a) Hermes hard fork.** Same features, but about 490 upstream commits a day and A's core patches sit on the hottest files. The merge record shows plugin work lands and core work does not. Revisit only if Nous changes the license or breaks the plugin API for good.
- **OpenClaw.** Closest rival on features (more gateways, native phone apps). Rejected because A has no investment there, it churns as fast as Hermes, it is TypeScript where A's team works in Python, and its foundation is OpenAI-sponsored. Borrow its gateway and app ideas.
- **OpenCode.** Clean client/server design and a good TypeScript SDK. No gateways, cron, or personal memory. Keep as a worker engine.
- **pi.** Best minimal core, but Exo would rebuild gateways, cron, board and UI from nothing. Recently acquired. Useful as a design reference for a lean core.
- **Goose.** Best governance (Linux Foundation). Weak memory, no gateways.
- **OpenHands.** A control centre for other agents with Docker per chat. Good orchestration ideas, wrong shape for a personal phone-first system.
- **Letta Code.** Strongest memory model (memory blocks, git-backed memory, sleep-time consolidation). Cloud-first and small. Copy its memory ideas into the Exo memory provider.
- **Cline.** IDE-first. Gateway and multi-agent claims unverified.
- **Codex CLI.** Solid Rust core with memories and roles, but OpenAI-steered, CLA, weaker non-OpenAI routing. Keep as a worker engine.
- **(d) Fresh on Claude Agent SDK.** Claude-only, closed runtime underneath, the TypeScript SDK is proprietary, and third-party products cannot use subscription login. That breaks model-agnosticism and replicability, which A requires. Use Claude through Hermes providers and as an ACP worker instead.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Upstream breaks a plugin contract | Pin tags. Contract tests in Exo CI against each new tag. Upstream any missing hook as a small PR instead of patching core. |
| Codebase is huge (926k non-test Python lines); nobody can hold it all | Exo touches only the plugin contracts. Keep a short map of the 10 files Exo depends on. |
| Nous changes license or direction | MIT allows freezing at the last good tag and forking then. Keep personal data in the engine-neutral hafiz format so the base can be swapped. Agents are replaceable; the operating system is not. |
| Bot-driven commit flood brings regressions | Move the pin weekly, not daily. Canary profile first, then the Protean team, then A's root profile. |
| A's 25 open PRs and the open-PR cap slow fixes Exo needs | Rank them: keep the plugin-only ones, convert core ones into hook requests where possible, drop stale ones. Patch queue limited to 5. |
| A's personal data leaks into the generic package | Separate repos and data dirs from day one. A redaction hook and a CI check that no path under the user store appears in `exo`. |
| Python single-process gateway limits scale | Fine for one person and a small team. If Exo serves many users, run one gateway per user. |

Sources: hermes-agent checkout and `gh` data gathered 2026-10-11; GitHub repos for each alternative (github.com/openclaw/openclaw, github.com/anomalyco/opencode, github.com/earendil-works/pi, github.com/openai/codex, github.com/aaif-goose/goose, github.com/OpenHands/OpenHands, github.com/letta-ai/letta-code, github.com/cline/cline, github.com/anthropics/claude-agent-sdk-typescript); code.claude.com/docs/en/agent-sdk/overview; kilo.ai/compare/roo-code-shutdown-roomote. Unverified: Goose and Cline memory and subagent depth, Letta Code fully offline use, OpenClaw foundation charter.
