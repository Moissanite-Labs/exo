# Hermes local inventory for Exo

Snapshot: 2026-10-11. Read-only scan of `~/.hermes/`. No secret files were opened.
"Ours" means authored by A or his agent teams (ahrazzle, ahraz.arifuddin@gmail.com, ahraz.a@proton.me, mozi@askaconsult.com, Team6/Protean seats).

Verdict key: **core** = native-in-core (fork carries it), **plugin** = native plugin shipped with Exo, **skill** = bundled Exo skill, **migrate** = migrate-data, **drop**, **ref** = reference-only.

## Summary counts

| Area | Total | Ours | Upstream / third party | Notes |
|---|---|---|---|---|
| User plugins `~/.hermes/plugins` | 8 (+ `web/keenable` core plugin enabled) | 4 | 4 | continuum, protean-ordaprompt, tamahermes, voicestudio are ours |
| Desktop plugins `~/.hermes/desktop-plugins` | 5 | 4 | 1 sample | chief-of-staff, continuum, group-model-sync, protean-ordaprompt |
| Profile-only plugins | 2 | 0 | 2 | backsearch, spotify (NousResearch) |
| Core plugins `hermes-agent/plugins` | 103 plugin.yaml | 1 | 102 | only `platforms/muse-bridge` is ours (untracked) |
| Root skills `~/.hermes/skills` | 97 | 7 | 90 | 58 bundled, 14 hub, 2 upstream, 10 copies of skills upstream removed |
| Profile skills (10 profiles) | 1157 SKILL.md | 433 copies, 182 unique | rest bundled/hub | heavy duplication across profiles |
| team-skills repo | 16 skills, 22 check scripts, ops ledgers | all | 0 | aska-digital/protean-team-skills, clean, in sync |
| Profiles | 12 dirs | 10 live, 2 empty shells | | proteus, orda, mozi, shaka, hazen, leo, frida, aetherean, kindi, sina |
| Cron jobs | 15 named | 8 enabled | | most live jobs on orda and proteus |
| Local branches in hermes-agent | 36 | 31 ours | | `main` has zero local commits; 3 untracked paths; 3 stashes |
| A's upstream PRs (ahrazzle) | 49 | 4 merged, 25 open, 20 closed | | open PRs are the fork's carry list; a few closed ones landed as direct commits (16 A commits in origin/main) |
| launchd services around Hermes | 11 | 11 | | 1 broken (caps-watchdog exits 1) |

Disk: `~/.hermes` is roughly 85 GB. Most of it is not Exo material: `profiles` 23 GB (caches, snapshots, state.db), `installs` 22 GB (venvs), `_quarantine` 32 GB, `worktrees` 3.9 GB, `kanban/workspaces` 1.4 GB, `tools` 971 MB (binaries).

## 1. Plugins

### 1a. User plugins (`~/.hermes/plugins/`, all enabled in root config)

| Plugin | What it is | Ours? | Status | Size | Verdict | Reason |
|---|---|---|---|---|---|---|
| continuum | Read-only cross-profile project recovery dashboard. Scans every profile state.db, clusters sessions into projects. Repo aska-digital/protean-continuum. | Ours (kodekoot, azaraki, shayba, sheikh-al-jabr) | live | 3.5M | plugin | Session-to-project view is core to Exo context management. |
| protean-ordaprompt | Fail-closed comparative classifier for OrdaPilot request routing (topic and session choice in one batch). Installed from a kanban workspace repo. Catalog entry merged upstream (#119124). | Ours (ahrazzle) | live | 1.6M | plugin | Request-to-session routing is an Exo harness function. Re-home source out of the kanban workspace. |
| tamahermes | Pet grown from real agent activity (prompts, tool runs, failures, tokens). Hermes port of TamaCodex. Catalog merged upstream (#118458, #120738). | Ours (ahrazzle) | live | 80K | plugin | Optional companion; keep as plugin, not core. |
| voicestudio | VoiceStudio TTS sidecar client (speak, voice design, chapters, dubbing). Catalog PR #134468 open. | Ours | live (enabled) | 112K | plugin | Useful sidecar, independent of core. |
| claude-subscription-directsdk-experimental | Drive Claude Pro/Max through the Claude Code CLI. Pinned from NousResearch/hermes-official-plugins. | Upstream official | live | 3.7M | ref | Keep installing from the official catalog. |
| homeassistant | Home Assistant gateway platform and tools. Pinned official. | Upstream official | live | 192K | drop | Not part of Exo scope; reinstall from catalog if wanted. |
| pixel-office | Pixel-art office where each agent is a character. teknium1/hermes-pixel-office. | Upstream community | live (writes `pixel-office/events.jsonl`) | 196K | ref | Nice visualizer; keep as optional catalog install. |
| petdex-desktop | Forwards desktop lifecycle events to the Petdesk companion. | Third party (Petdex) | live | 64K | ref | Pairs with the petdex hooks below; optional. |

Root config also wires 10 lifecycle `hooks:` to `~/.petdex/bin/petdex-hook` (pre/post tool, LLM, approval, subagent, session). Verdict: ref. Pet telemetry should go through a plugin hook, not shell hooks.

### 1b. Desktop plugins (`~/.hermes/desktop-plugins/`)

| Plugin | What it is | Ours? | Status | Size | Verdict | Reason |
|---|---|---|---|---|---|---|
| chief-of-staff | Fan-out command pane: live subagent worker cards, per-worker steer and interrupt, orchestrator model control. github.com/ahrazzle/hermes-chief-of-staff. | Ours (Halakukhan, Team6) | live in shaka profile | 64K | core | Multi-agent observation and steering is a headline Exo feature. |
| group-model-sync | Per-member model and reasoning control for group chats, plus agent-wide model sync. | Ours | live in proteus, shaka | 48K | core | Fleet-wide model control belongs in the Exo UI. |
| continuum | Desktop pane for the continuum plugin, with kanban interaction. | Ours | live | 80K | plugin | Ships with continuum. |
| protean-ordaprompt | Desktop pane for ordaprompt. | Ours | live in proteus | 8K | plugin | Ships with ordaprompt. |
| dice-roller | SDK sample: statusbar chip, palette command, pane. | Sample | stale | 8K | drop | Demo only. |

### 1c. Profile-only plugins

| Plugin | Where | Ours? | Verdict | Reason |
|---|---|---|---|---|
| backsearch | hazen, mozi | Upstream (NousResearch) | ref | Dated web archive search; install from catalog. |
| spotify | kindi, proteus, sina | Upstream (NousResearch) | drop | Not Exo scope. |

Every profile also carries its own copy of claude-subscription-directsdk and tamahermes. Exo should install plugins once and enable per profile.

### 1d. Core plugins in the checkout (`~/.hermes/hermes-agent/plugins/`)

103 plugin.yaml files across browser, computer_use, context_engine, cron_providers, kanban, memory, model-providers, platforms (22), web, observability and others. All tracked in origin/main and upstream-authored, except:

| Plugin | What it is | Ours? | Status | Verdict | Reason |
|---|---|---|---|---|---|
| `plugins/platforms/muse-bridge/` | Pairs the Muse mobile app with the gateway: loopback front server, single-use pairing code, reverse proxy to the A2A inbound plugin, cloudflared or tailscale tunnel. CLI `hermes muse link/refresh/tunnel/unlink/lock/status`. Has README, BUILD-RECEIPT, tests. | Ours (Protean team) | untracked, not committed anywhere | core | Mobile access to the live gateway is an Exo surface. Commit it on a fork branch now; it exists only in this working tree. |

A's merged upstream work touches core plugins only through `plugin-catalog/*.yaml` (tamahermes, protean-model-sync, protean-subagent-viewer, protean-ordaprompt, optchat, optchat-memory).

## 2. Local mods to hermes core (what the fork must carry)

**Checkout state.** `~/.hermes/hermes-agent` is on `main` at dce1e9b (2026-10-09), 376 commits behind origin/main, 0 ahead. Committed local mods on main: none. `fork/main` is 25,585 behind origin and carries nothing. All of A's core work lives on topic branches and open PRs.

**Uncommitted.**
- Untracked `plugins/platforms/muse-bridge/` (see 1d).
- Untracked `plugins/model-providers/solstice/constants.py` and `tests/plugins/model_providers/test_solstice_discovery.py`. This is the solstice lazy-load work from PR #134235 (open).
- stash@{0} (2026-10-09 update autostash): solstice `__init__.py` and `transport.py`, 7 lines. Same theme.
- stash@{1} (2026-09-18 update autostash): desktop `build-command-screenshot-monitor.mjs`, +40 lines.
- stash@{2} (pre-rebase on fix/skill-ledger-complete-reversible): skill ledger and skill manager, +273 lines. Likely superseded by the branch.

**Desktop bundle patches (outside git).** `~/.hermes/scripts/patch-groupchat-caps.py` and `patch-groupchat-slash.py` patch the minified desktop renderer to lift group chat caps (rounds, messages, continuations to 9999) and add a group slash command. `team6-caps-watchdog.sh` re-applies them hourly via launchd. The watchdog currently exits 1. The fork should make these caps config values and add the slash command in source, then drop the patchers.

**Carry list: open upstream PRs by A, grouped by theme.** The fork carries these until they merge.

| Theme | Branches / PRs | Exo relevance |
|---|---|---|
| Session and model control | feat/session-override-persistence (#98901), fix/sessions-model-audit-reset (#106650), fix/session-model-config-desync (merged as 9ddc6fb) | High. Per-session model pins that survive reaping, plus bulk audit and re-pin. |
| Multi-agent and goals | fix/goal-barrier-parked-resume (#107130), docs/kanban-dispatcher-liveness, docs/kanban-skill-visibility | High. Stalled-subagent barrier release, dispatcher watchdog and poison-task quarantine spec, skill checks at task create. |
| Credentials and provider resilience | fix/119163-sole-credential-reset-clamp (#119652), fix/codex-responses-terminal-retry (#111597), feat/codex-proxy-replay-window (#133007), provider quota governor commits (6e39c1c, 35a7e4e, unpushed) | Medium-high. Keeps a lone key or free model usable. |
| Skills and memory hygiene | fix/skill-ledger-complete-reversible (#110011), feat/model-identity-verification-skill (local only) | High. Reversible skill mutations under the curator. |
| Desktop | fix/desktop-profile-loading-recovery (#110156), fix/desktop-reasoning-completion-fallback (#110172) | Medium. |
| CLI and TUI | fix/editor-slash-busy-routing (#120840), fix/tui-banner-gradient-rows (#129481), mozi-p4-readfile-outline / proposal p4 (#119785) | Medium. Outline read mode saves context. |
| Update and doctor | fix/doctor-legacy-pre-update-backup-bool (#108021), fix/solstice-discovery-and-provider-warning-buffer (#134235) | Low-medium. |
| Optional skills | feat/optional-{subagent-oversight, protean-github-draft, merge-reconciler, macos-harness, evidence-reports, citation-integrity} (#118951 to #118956) | Ship as Exo skills directly. |
| Plugins and catalog | catalog/voicestudio (#134468), catalog/cognition-standalone (#133775), draft/mcp-events-receiver (#132908), draft/you-should-know (#132906) | Cognition memory provider and you-should-know observer are direct Exo memory candidates. |
| Evals | mozi-111237 (#114436) paired fail-closed toolperf verdict | Medium. Useful for model routing evals. |

Closed but worth reading for Exo design: feat/memory-provider-cognition (#133772), feat/taste-corroboration-engine (#126940, taste learning with decay and conflict escalation), proposals p1 to p3 (#119782 to #119784: formatter auto-repair, budget downshift, sticky tier escalation). Verdict: ref.

**Branch housekeeping.** 36 local branches. Merged or duplicate and safe to prune later: pr-106742 and pr106742 (duplicates), pr108007/resolve, rb/107130, rb/98901, backup/hermes-repair-20260907*, fix/prune-path-retirement, fix/multiplex-desktop-wal-conflict, fix-36514 and test/36514 (PR closed). Worktrees in `~/.hermes/worktrees/` (9 dirs, 3.9 GB) back the fix-1191xx and goal-barrier branches; the verify-* ones are disposable.

## 3. Skills

### 3a. Root skills (`~/.hermes/skills/`)

97 SKILL.md. 58 bundled, 14 hub official, 2 upstream names, 23 local.

Ours (7):

| Skill | What it is | Status | Size | Verdict | Reason |
|---|---|---|---|---|---|
| divan-bus | Post and read on the Divan Bus signed hash-chained relay (author saga-divan). | live | 4K | skill | Inter-agent messaging. Becomes core if Exo absorbs Divan Bus. |
| eldunari-absorption | Absorb project context into the canonical Eldunari. | live | 20K | skill | Memory ingestion procedure. |
| eldunari-optimization | Audit Eldunari structure after absorption. | stale (Aug 22) | 12K | skill | Merge with absorption and maintenance. |
| local-endpoint-repair | Repair the llama router at 18099. | live | 4K | skill | Ties to llama router; fold into a local-models skill. |
| hermes-petdex-ops | Fix pet cards and pet installs. | live | 12K | ref | Pet-specific. |
| agentskills-io | Create and publish skills to the agentskills.io standard. | stale (Aug 21) | 56K | skill | Exo skills should follow the open standard. |
| merge-reconciler | Neutral resolution of agent merge conflicts (PR #118954). | live | 8K | skill | Multi-agent need. |

Upstream-removed copies (10): github-auth, github-code-review, github-issue-to-pr, github-issues, github-pr-workflow (edited locally Oct 8), github-repo-management, nano-pdf, ocr-and-documents, plan, session-librarian. Verdict: drop the copies; team-skills already carries the maintained github-* versions.

Archived by the curator in `skills/.archive` (6): hermes-gateway-config, hermes-openrouter-provider-routing, hermes-pet-generation, hermes-profile-diagnostics, local-llama-model-troubleshooting, team6-model-configuration. Verdict: ref.

### 3b. Profile skills (`~/.hermes/profiles/*/skills/`)

433 local copies, 182 unique names. Local counts per profile: proteus 76, kindi 75, sina 74, mozi 49, shaka 36, frida 33, hazen 28, leo 26, orda 22, aetherean 14. kindi and sina were cloned from proteus (54 identical skills), so most of their skills are duplicates.

| Group | Unique | Examples | Verdict | Reason |
|---|---|---|---|---|
| Orchestration doctrine (proteus line) | ~20 | lean-six-sigma-orchestration, jit-assembly-line-handoffs, team6-supervision-trees, subagent-stall-recovery, subagent-pipeline-recovery, subagent-task-review, multi-agent-team-orchestration, protean-system, connected-surface-propagation, team6-subagent-spawning, spawn-under-profile, distributed-coordination, concurrent-agent-file-coordination | core (distill) + skill | This is the multi-agent pattern library. Distill into Exo's orchestrator design; keep 3 to 5 as skills. |
| Hermes ops and diagnostics | ~22 | hermes-context-window-diagnostics, hermes-session-model-overrides, hermes-profile-management, hermes-profile-fleet-operations, prompt-cache-path-diagnosis, compression-lifecycle, hermes-model-diagnostics, hermes-worker-exit-diagnostics, hermes-messaging-gateway-diagnostics, hermes-update-restore, hermes-desktop-update-repair, model-config-skill, model-policy-audit, team6-provider-switch | skill (merge) | Merge into 3 skills: exo-diagnostics, exo-models, exo-profiles. Several will be obsolete once the carry list lands. |
| Local models | 7 | llama-router-co-residency, local-model-deploy, apple-silicon-local-llm, local-llm-apple-silicon, local-model-endpoint-configuration, desert-ant-local, linux-vm-apple-silicon | skill (merge) | One local-models skill next to the llama router. |
| Memory and knowledge | ~10 | eldunari-absorption, eldunari-maintenance, knowledge-base-ingestion (x2), mass-knowledge-ingest, knowledge-base-consolidation, knowledge-base-management, project-context-ingestion, agent-consciousness-architecture, hermes-memory-migration-prototype | core (design input) + skill | Direct input to Exo memory design. hermes-memory-migration-prototype (mozi) is a migration starting point. |
| Verification and release discipline | ~20 | completion-verification, deploy-gate-discipline, deployment-verification-discipline, web-release-gates, invariant-regression-gates, code-review-verification, served-artifact-qa, adversarial-review, source-verification, long-run-deployment-discipline | skill (merge) | Shaka QA doctrine. Merge into a small verification pack. |
| Research and writing | ~14 | evidence-gated-research-reports, citation-ledger-field-notes, primary-source-discovery, source-evaluation, read-only-system-audit, project-takeover-recon, argument-analysis | skill | Hazen method. Some already in fork optional-skills PRs. |
| Design and web delivery | ~25 | ui-ux-pro-max, user-design-aesthetic, html-report-design, landing-page-design, static-site-production, staging-first-web-deployment, custom-domain-publishing, dabba-subdomain-deploy, aska-staging-channel-ops | skill (selective) | Keep user-design-aesthetic (taste data for the user model). Rest is project-level. |
| Project and venture specific | ~25 | fantasy-football-*, rhythm-game-development, team6-print-fit-render, nani-notes, typesafe-ai, whatsapp-bot, x-bookmarks-extract, unbagrnd-headless-bg-lane, integer-mult-bounds-verify | drop or ref | Project knowledge, not harness. Move to the project repos or Eldunari. |
| Packaging and plugin dev | ~8 | hermes-standalone-plugin-packaging, hermes-context-engine-plugin, framework-plugin-development, agent-team-kit-packaging, agent-kit-extraction, agent-kit-distribution, hermes-agent-local-slice | skill | Needed to build Exo plugins. |
| Upstream-removed copies in all 10 profiles | 10 | github-*, plan, nano-pdf, ocr-and-documents, session-librarian | drop | Stale copies. |

### 3c. team-skills repo (`~/.hermes/team-skills`, aska-digital/protean-team-skills)

Live, clean, in sync with origin, 53 commits since 2026-09-13, last commit 2026-10-11. 10M total.

| Item | What it is | Verdict | Reason |
|---|---|---|---|
| orchestration/protean-control-plane | Dispatch, rotation, concurrency, GitHub and learning SOP index. | core (distill) | It is the control plane spec. |
| orchestration/team6-operating-doctrine, protean-council, protean-stoa-exchange | Team pipeline, council protocol, Stoa exchange. | skill | Multi-agent procedures. |
| creative/clean-writing, external-writing-discipline + `scripts/check-prose.py` | A's writing rules and a prose linter. | core | Matches PRINCIPAL.md style. The prose check should be a built-in output gate. |
| sym-protocol | SYM-2P/1.0 packet and blackboard validator, stdlib, fail-closed. | ref | Compare against Divan Bus before choosing a wire format. |
| github/* (7 skills incl. github-pr-audit, draft-review-html, charts-in-posts, git-authorship-identity-cleanup) | Maintained GitHub workflow pack. | skill | Replaces the stale github-* copies. |
| upstream-contribution-pr, media/openshorts-video | Fork PR rework; short video creation. | skill / drop | First is useful, second is project-level. |
| `scripts/` (22 checks: check-inflight, check-rotation, check-delegation, check-hotpath-freeze, models-at-dispatch, onboard-profile, promote-skill...) | Fail-closed governance checks. | core | Port as orchestrator preflight hooks. |
| `ops/` (INFLIGHT, DISPATCH-LEDGER, LEARNINGS, OWNERSHIP-MATRIX, ROTATION-STATE, decision-reports, HISTORY-*) | Live ops ledgers, 2.2M markdown. | migrate | LEARNINGS (lrn-001 to lrn-069) and decision reports are durable rulings. Ledgers become kanban or bus state. |

## 4. Memory and knowledge systems

| Path | What it is | Ours? | Status | Size | Verdict | Reason |
|---|---|---|---|---|---|---|
| `eldunari/` | Agents' KB, git repo (429 commits). ROUTER.md is an MoE router: hot path (SOUL, MEMORY, USER) holds only a breadcrumb; one module loaded per task. `domains/` (11 modules incl. user-profile.md), `anima/` per-role patterns (6), `nexus/` (state, agreements, tensions, synthesis, events), `scripts/` (heartbeat, query, index, link graph), `.semantic-index.sqlite3` (406 docs). | Ours | live, 8 dirty files in `nexus/state/orda`; semantic index last built Oct 7 | 31M (nexus/state 15M) | core (design) + migrate | The router-plus-modules pattern is Exo's context model. Content migrates; heartbeat and index scripts become core services. |
| `memories/` (root) | MEMORY.md 1.1K, USER.md 0.3K. | Ours | stale (Sep 7) | 8K | migrate | Small; fold into the user model. |
| `profiles/*/memories/` | MEMORY.md and USER.md per profile. Largest: proteus 19K, kindi 12K, sina 11.5K, orda 11K. Others about 1K. | Ours | live | 73K total | migrate | Per-agent memory; USER.md files feed the guardian-angel model. Dedupe against hafiz. |
| `kanban.db` + `kanban/` | Hermes kanban: 145 tasks, 190 runs, 3,848 events, 178 comments, 160 attachments. Second board `boards/team6-site` (5 tasks). `workspaces/` holds 30 task repos (1.4G, one is 1.0G). `logs/` 17M. | Upstream feature, our data | live (last write Oct 8) | 2M DB, 1.4G workspaces | migrate (DB, attachments) / drop (workspaces) | Task history is useful; workspaces are clones. Re-home `t_245b66d4/repo` (ordaprompt source) and `t_bb1d5841/repo` first. |
| `bot_relay/` | Claimed/outbox/replies queue with roster.json (empty roster). | Ours | stale | 4K | drop | Replaced by Divan Bus. |
| `bridge-exchange/` | File exchange in/out for the Muse bridge; 4 test payloads. | Ours | stale (Sep 29) | 2M | drop | Test artifacts only. |
| `pixel-office/events.jsonl` | Tool and session event stream for pixel-office. | Plugin data | live | 376K | drop | Regenerated. Could seed an Exo event bus design. |
| `pets/` | 73 pet sprite packs (EvoPet/Petdex art). | Ours/community | live | 143M | ref | Asset library; keep outside core. |
| `tamahermes/` | Overlay state, hook state, pointers to `~/evopet-pet` repo and `~/.petdex`. | Ours | live (overlay supervisor running) | 168K | plugin data | Travels with the tamahermes plugin. |
| `hooks/` | Empty. | | empty | 0 | drop | Hooks live in config.yaml. |
| `desktop/` | Empty. | | empty | 0 | drop | |
| `plans/` | One plan: vibetrade-public-data-wire.md. | Ours | stale (Sep 9) | 4K | ref | Project plan, move to project repo. |
| `bin/` | tirith, uv, uvx binaries, browser-use shims. | Upstream | live | 57M | drop | Installer-managed. |
| `scripts/` | llama-router.sh (launchd), eldunari-heartbeat.sh, groupchat patchers, caps watchdog, a 2.9M bundle backup. | Ours | mixed: router live, watchdog broken | 2.8M | core (router) / drop (patchers, bundle backup) | See section 2. |
| `llama-local/` | Shared llama.cpp router config `router.ini` (one endpoint 127.0.0.1:18099, models-max 1, memory watchdog). Logs 26M. | Ours | live (com.team6.llama-router, llama-server running) | 27M | core | Local model routing is an Exo harness feature. Rotate the logs. |
| `skins/` | 12 TUI skins (kensei, tarnished, voyager...). None exist upstream. | Ours | live | 196K | plugin | Ship as an optional theme pack. |
| `tui-widgets/` | subagent-monitor.mjs, pet-loop.mjs, ascii art. | Ours | stale (Sep 9) | 12K | ref | subagent-monitor overlaps chief-of-staff. |
| `cron/` (root) | jobs.json (1 job), executions.db (2 rows), notepad.db (empty), usage_audit.jsonl. | Upstream feature | live | 84K | migrate (job defs) | |
| `mozi-continuum-review-test/` | Checkout of aska-digital/protean-continuum (FastAPI board app). | Ours | stale (Sep 18) | 13M | drop | Duplicate of the repo. |
| `worktrees/` | 9 hermes-agent worktrees for PR work. | Ours | live branches | 3.9G | drop after merge | Rebuildable from git. |

## 5. Profiles

| Profile | Role (from SOUL.md) | Model (provider / default) | SOUL.md | Local skills | state.db | Cron jobs | Verdict |
|---|---|---|---|---|---|---|---|
| proteus | Leader, orchestrator, alpha | openai-codex / gpt-6-luna-900k | 1,286 B | 76 | 1.3G, 2,288 sessions, 212K msgs | Raptora daily drift monitor, eldunari-heartbeat-recurring, weave-resolve-fed-dec; off: Tafsir MVP Monitor, Weekly WhatsApp Report, Change-propagation sweep, whatsapp-ingest-livecheck, stoa-cleanup-deletion-pass | migrate |
| orda | Operator relay, mission control | opencode-go / mimo-v2.6-flash | 1,491 B | 22 | 946M, 3,411 sessions, 136K msgs | stoa-drive-watch, board-brief, stoa-pr-watch, 3f-signups-sync; off: orda-heartbeat | migrate |
| mozi | Builder, coder, executor | opencode-go / step-5-preview-free | 1,214 B | 49 | 689M, 793 sessions, 85K msgs | off: x-bookmark-sync | migrate |
| shaka | QA, watcher, omega | opencode-go / glm-5.3-flash | 1,221 B | 36 | 520M, 869 sessions, 62K msgs | none | migrate |
| hazen | Research, writing | opencode-go / muse-spark-1.3-contributor | 1,190 B | 28 | 312M, 492 sessions, 29K msgs | none | migrate |
| leo | Analysis, architecture | nous / inclusionai/ling-3.1-flash | 1,200 B | 26 | 288M, 443 sessions, 29K msgs | none | migrate |
| frida | Design, UX | opencode-go / mimo-v2.6-flash | 1,203 B | 33 | 131M, 208 sessions, 13K msgs | none | migrate |
| aetherean | Small self-contained tasks | opencode-go / deepseek-v4.1-flash | 1,290 B | 14 | 70M, 213 sessions, 11K msgs | none | migrate |
| kindi | Finance sector seat | nous / stepfun/step-5-preview:free | 2,192 B | 75 | 15M, 7 sessions | none | migrate |
| sina | Biochemistry sector seat | nous / inclusionai/ling-3.0-flash-sante:free | 2,158 B | 74 | 332K, 1 session | none | migrate |
| root (`~/.hermes`) | WhatsApp gateway host, multiplexes profiles | opencode-go / mimo-v2.6-flash | 667 B | 7 | 20M, 356 sessions, 3.9K msgs | Weekly WhatsApp Report | migrate |
| eaprofile, lugia | Empty shells (only state-snapshots, journal) | none | none | 0 | none | none | drop |

Also: `profiles/.deleted/` (lugia, protean) and `proprofiles/proteus/cache` (16K). Verdict: drop. Root config: memory on (22,000 char memory, 13,750 user limit), delegation max 30 concurrent children, gateway `multiplex_profiles: true` with a `whatsapp-to-orda` route, MCP servers cloudflare (on) and cloudinary (off).

## 6. Tools A built around Hermes

| Tool | Path | What it is | Status | Verdict | Reason |
|---|---|---|---|---|---|
| hermes-bridge | `~/.hermes/tools/hermes-bridge/` | Filesystem-queue bridge from Muse (VM) to the gateway via `/tmp/hermes-bridge`. HMAC-signed, with receipts from mozi and shaka. | live (ai.hermes.bridge, poller running) | core (replace) | Fold into muse-bridge or Divan Bus; a /tmp queue is a weak transport. |
| muse-bridge platform | `hermes-agent/plugins/platforms/muse-bridge/` | Mobile pairing and tunnel (see 1d). | untracked | core | Commit it. |
| llama router | `scripts/llama-router.sh`, `llama-local/router.ini` | Single local endpoint for all profiles with a memory watchdog. | live | core | |
| TamaHermes overlay | `~/evopet-pet` repo, `~/.hermes/tamahermes/`, launchd com.autoark.tamahermes.overlay-supervisor | Desktop pet overlay fed by agent activity. evopet-drain launchd job. | live | plugin | |
| Divan Bus | `~/divan-bus` (outside ~/.hermes), skill `skills/divan-bus`, launchd ai.aska.divan-bus, divan-bridge, divan-checkpoint, cloudflared-divan | Signed hash-chained inter-agent relay. | live | core candidate | Exo's multi-agent message layer. |
| Orda lease heartbeat | launchd ai.hermes.orda-lease-heartbeat | Keeps the orda seat lease alive. | live | core (generalize) | Seat leases should be a core orchestrator feature. |
| Group chat caps patchers | `scripts/patch-groupchat-*.py`, launchd com.team6.caps-watchdog | Patch the minified desktop bundle. | broken (exit 1) | drop (move to source) | See section 2. |
| Continuum app | aska-digital/protean-continuum | FastAPI board over sessions and projects. | live as plugin | plugin | |
| Vendored binaries | `tools/` agent-browser, chromium-1208, cua-driver, ffmpeg, node, npm, python, ripgrep, tirith, uv | Installer-managed runtimes. | live | drop | Rebuilt by the installer. |

## 7. Data to migrate

| Data | Location | Format | Size | Notes |
|---|---|---|---|---|
| Session history, 10 profiles + root | `profiles/*/state.db`, `state.db` | SQLite (WAL). Tables: sessions, messages, system_prompts, session_model_usage, gateway_routing, async_delegations, plus empty hosted_room_* | about 4.3 GB. proteus 1.3G, orda 946M, mozi 689M, shaka 520M, hazen 312M, leo 288M, frida 131M, aetherean 70M, root 20M, kindi 15M | About 9,080 sessions and 584K messages. Read with `mode=ro&immutable=1`. hafiz `tools/extract.py hermes-exchanges` already reads these. Migrate as an archive plus an index, not into the hot path. |
| State snapshots | `profiles/*/state-snapshots`, `state-snapshots/` | SQLite copies | about 3.2 GB (proteus 1.0G, orda 956M, mozi 693M, shaka 510M) | drop after one verified export. |
| Retired WALs | `profiles/mozi/state.db.retired-wal-*` (3 x 231M), `profiles/shaka/...` (233M) | SQLite WAL | about 0.9 GB | drop. More are already in quarantine. |
| Eldunari KB | `eldunari/` | Markdown in git (429 .md files, 2,264 files), JSONL events in `nexus/state`, SQLite semantic index (406 docs, 9M) | 31M (+4.8M .git) | Migrate whole repo with history. Commit the 8 dirty orda state files first. Rebuild the index in Exo. |
| Agent memories | `memories/`, `profiles/*/memories/` | Markdown (MEMORY.md, USER.md) | 73K | Merge USER.md files into the guardian-angel model; dedupe against hafiz. |
| SOUL files | `SOUL.md`, `profiles/*/SOUL.md` | Markdown | about 15K total | Agent identities; migrate as Exo agent definitions. |
| Kanban | `kanban.db`, `kanban/boards/team6-site/kanban.db`, `kanban/attachments` | SQLite + files | 2.2M DBs, 4M attachments, 17M logs | 150 tasks, about 4,150 events. Workspaces (1.4G) are git clones: drop after rescuing the two repos named in section 4. |
| Ops ledgers and learnings | `team-skills/ops/` | Markdown in git | 2.2M | Already in a repo. LEARNINGS and decision-reports are rulings to fold into the user model. |
| Cron definitions | `cron/jobs.json`, `profiles/*/cron/jobs.json` | JSON | under 100K | 15 named jobs, 8 enabled. Migrate definitions, not run history. |
| Shared and misc state | `shared-state.db` (empty hosted rooms), `projects.db` (1 project, 2 repos), `verification_evidence.db` (2 rows), `cron/executions.db` | SQLite | under 300K | Low value; projects.db is worth one look. |
| WhatsApp gateway session | `whatsapp/session` (9.1M), `session.stale-20260907` (8.5M) | Baileys session files | 17.6M | Credential-like. Re-pair in Exo rather than copy. Drop the stale one. |
| Pixel-office and pet state | `pixel-office/events.jsonl`, `tamahermes/*.json`, `pets/` | JSONL, JSON, sprites | 144M | Optional. Pets are assets, not memory. |
| Config | `config.yaml`, `profile.yaml`, `profiles/*/config.yaml` | YAML (contain provider settings; secrets live in .env) | 15K + 57K + per profile | Port settings by hand. Many `config.yaml.bak-*` files in proteus can go. |
| Caches | `cache` 1.5G, `profiles/*/cache` (proteus 3.6G, shaka 3.6G), `installs` 22G, `logs` 106M | Mixed | about 31 GB | drop. |

## 8. Quarantine (listing only)

`~/.hermes/_quarantine-20261011/` was archived on purpose by saga-divan on 2026-10-11. Nothing was deleted. Total 32 GB.

| Item | Size | Content (from MANIFEST.txt) |
|---|---|---|
| installs-old/ | 13G | 23 superseded install environments |
| delegation-cache-old/ | 12G | 465 proteus delegation workspaces older than 14 days |
| retired-wal/ | 3.3G | retired state.db WAL dirs (root 5, proteus 10) |
| hermes-agent_backup_pre_reset.tar.gz | 1.6G | checkout backup from 2026-09-07 |
| backups/ | 1.5G | Aug to Sep config and model-reset backups |
| root-bak/ | 53M | pre-update state.db backups, config and .env backups (secrets, 0600) |
| team-skills-migration-backup-20260913/, team-skills-buffer/, optimization-backups-20260830/ | 1.4M | old team-skills and optimization backups |
| root-tmp/, root-locks/, cron-locks/, dead-scripts/ | under 100K | temp files, stale locks, two scripts pointing at a removed profile |

Verdict: drop once A confirms. The pre-reset tarball is the only item worth a quick check for lost local mods before deletion.
