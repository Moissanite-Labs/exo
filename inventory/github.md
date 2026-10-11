# Project Exo: GitHub inventory

Built 2026-10-11 from the gh CLI as ahrazzle. Read-only pass. "Active" means a push within the last 14 days. Most repos show a push on 2026-10-11, so "active" is a weak signal here.

Verdict key:
- keep-as-is: stays its own repo, not part of Exo.
- merge-into-exo: code or content moves into the Exo tree (fork source, bundled plugin, or bundled skill).
- reference-only: read it for ideas or history, carry nothing.
- drop: no Exo value.

## Summary counts

| Item | Count |
|---|---|
| ahrazzle repos | 31 (13 forks, 2 archived, 4 private) |
| aska-digital repos | 39 (3 forks, 0 archived, 8 private) |
| Verdict merge-into-exo | 28 |
| hermes-agent PRs by A | 49: 25 open (20 drafts), 4 merged, 20 closed unmerged |
| hermes-agent issues by A | 37: 22 open, 15 closed |
| Closed-unmerged PRs whose code still reached upstream | 9 (5 cherry-picked or salvaged, 4 fixed by another PR) |
| Closed PRs folded into A's own open PRs | 5 |
| ahrazzle/hermes-agent branches | 1691 total, 122 not on upstream, 49 are A's PR heads |
| aska-digital/hermes-agent branches | 2458 total, 29 not on upstream, 1 is A-team work |
| Other upstream repos with A PRs/issues | 9 |

## Repos: ahrazzle

| Repo | What | Status | Last push | Exo verdict | Reason |
|---|---|---|---|---|---|
| hermes-agent (fork) | Fork of NousResearch/hermes-agent. Hosts 48 live PR head branches. main is +1/-25585 vs upstream. | active (branches) | 2026-10-11 | keep-as-is | PR heads must live here until each PR closes. Too stale to be the Exo base. |
| integer-mult-bounds (fork) | CrocSwap kappa race fork, A's proofs and audits. | active | 2026-10-11 | keep-as-is | Math lane, feeds frontier-math. |
| gf2m-circuits (fork) | GF(2^m) multiplier circuits with Lean 4 proofs. | active | 2026-10-11 | keep-as-is | Math lane. |
| EvoPet | Cross-agent pixel pet: Python pet half plus a native desktop shell fed by agent hooks. 897 files, TS. | active (main stalled since 09-20 per issue #35) | 2026-10-11 | merge-into-exo (optional plugin) | Pet hook adapter ships with Exo. Shell stays here. See deep entry. |
| mobius-x1 (fork) | GF(2) Mobius transform in o(N log N) XOR gates. | active | 2026-10-11 | keep-as-is | Math lane. |
| team6-kit | Archived origin of the team kit. README points to the modular ingredients. | archived | 2026-10-10 | reference-only | History of protean-kit. |
| acc-training | Small JS training app, no README purpose. | active | 2026-10-10 | keep-as-is | Not Exo. |
| command-code (fork) | Command Code AI coding agent for open models. | active | 2026-10-08 | reference-only | Competing harness, A filed bugs there. |
| bshiyat (fork) | Someone's profile README. A has a PR. | active | 2026-10-05 | drop | No Exo value. |
| Edge0 (fork) | Edge0-AI model tooling. | active | 2026-10-04 | reference-only | Local model packaging ideas. |
| nutrak | Nutrition tracker. | active | 2026-10-04 | keep-as-is | Not Exo. |
| core-dashboard | Web Bluetooth OBD-II car dashboard. | active | 2026-10-03 | keep-as-is | Not Exo. |
| typemon | Typing monster battler on Typejoy. | active | 2026-10-03 | keep-as-is | Not Exo. |
| aska-website (private) | Aska site. | active | 2026-10-03 | keep-as-is | Not Exo. |
| jormil-site (private) | Static site for jormil.askaconsult.com. | active | 2026-10-03 | keep-as-is | Not Exo. |
| hermes-Custom-CLI-Themes (fork) | Hermes CLI themes. A fixed Rich wrapping. | stale | 2026-09-29 | reference-only | TUI theming ideas. |
| desktop (fork) | Command Code desktop app. | stale | 2026-09-23 | reference-only | A filed proposals there. |
| TamaHermes (fork of Alichua/TamaCodex) | Tamagotchi pet for Hermes, fed by Hermes hooks. In the upstream catalog. | stale | 2026-09-23 | merge-into-exo (optional plugin) | Pet half of EvoPet. See deep entry. |
| sharia-docs | Qatar Sharia-compliance workbook MVP. | stale | 2026-09-23 | keep-as-is | Not Exo. Built via the stoa exchange. |
| hermes-jev-curator (fork) | Jev relation governance for Hermes Curator. A has 1 PR, 2 issues. | stale | 2026-09-22 | reference-only | Curator ideas only. |
| typejoy | Typing game and framework. | stale | 2026-09-19 | keep-as-is | Not Exo. |
| EvoPet-site | Astro site for EvoPet. | stale | 2026-09-14 | keep-as-is | Marketing site. |
| nanaveda | Traditional remedy recipes. | stale | 2026-09-08 | keep-as-is | Not Exo. |
| workforce-proposal (private) | Proposal page. | stale | 2026-09-08 | keep-as-is | Not Exo. |
| particle-dashboard | Local quantized AI demo on M1. | stale | 2026-09-08 | reference-only | Local inference demo. |
| tafsir-staging | Tafsir study staging build. | stale | 2026-09-08 | keep-as-is | Not Exo. |
| tafsir | Tafsir study site. | stale | 2026-09-08 | keep-as-is | Not Exo. |
| vibetrade (private) | Trading app, TS. | stale | 2026-09-08 | keep-as-is | Not Exo. |
| human-atlas (fork) | 3D anatomy explorer. | stale | 2026-09-06 | drop | Not Exo. |
| my-test-vuegg-project | Empty test. | archived | 2020-04-24 | drop | Empty. |
| detectron2 (fork) | FAIR detection library. | stale | 2019-10-13 | drop | Unused since 2019. |

## Repos: aska-digital

| Repo | What | Status | Last push | Exo verdict | Reason |
|---|---|---|---|---|---|
| exo (private) | "Project Exo: TBA". Empty. | active | 2026-10-11 | keep-as-is | The Exo home. Candidate target for the fork. |
| hydro-os | Omarchy customization layer for charminar, plus a "jarvis" voice assistant. | active | 2026-10-11 | keep-as-is | Machine layer, not harness. Jarvis is reference for Exo voice. |
| frontier-math | Live dashboard of the kappa race, gated feed, 15-minute Action. | active | 2026-10-11 | keep-as-is | Product, not harness. |
| divan-bus (private) | Signed hash-chained relay for agent seats, MCP server, web view. | active | 2026-10-11 | merge-into-exo | Exo's inter-seat transport. |
| protean-team-skills (private) | Source of team skills, ops records, 30+ check-*.py gates. 4 open issues. | active | 2026-10-11 | merge-into-exo | Core of the team doctrine. |
| divan-checkpoints (private) | Daily signed ledger checkpoints. | active | 2026-10-11 | keep-as-is | Data, not code. |
| hermes-agent (fork) | Fork of upstream. main is +1/-1264 (one mozi skills commit). 1 A-team branch. | active | 2026-10-11 | merge-into-exo | Best Exo base candidate: 20x fresher than the ahrazzle fork. |
| cognition-memory | Session-end regex fact memory provider. Description says "temp". | active | 2026-10-11 | reference-only | Overlaps optchat. Decide. Catalog PR #133775 still open. |
| rhino-crm (private) | CRM for Shahid on charminar. 4 open issues. | active | 2026-10-11 | keep-as-is | Client app. |
| protean-control-plane | Trigger index routing a request to the minimum skill bundle, gates, council procedure. | active | 2026-10-11 | merge-into-exo | Dispatcher brain for the team. |
| protean-doctrine | Five-stage pipeline, role map, handoff protocol, QA gates, clean-writing. | active | 2026-10-11 | merge-into-exo | Default doctrine for Exo teams. |
| protean-drafts | Render a draft as one HTML review page and gate prose before posting. | active | 2026-10-11 | merge-into-exo | External-writing gate. |
| protean-handoff | Phone steering and approvals over the existing gateway. | active | 2026-10-11 | merge-into-exo | Remote control surface. |
| protean-ops | Five append-only record schemas plus read-only gates. | active | 2026-10-11 | merge-into-exo | Records for dispatch and rotation. |
| protean-stoa | Public SYM-2P exchange with the Sparky family. | active | 2026-10-11 | keep-as-is | A live exchange, not code. Exo speaks to it. |
| optchat-memory | OptChat endless memory provider. In upstream catalog. | active | 2026-10-11 | merge-into-exo | Default long memory. |
| protean-model-sync | Per-member model control for group chats, fleet presets. In upstream catalog. | active | 2026-10-11 | merge-into-exo | Fleet model management. |
| protean-subagent-viewer | Read-only desktop pane for live subagents. In upstream catalog. | active | 2026-10-11 | merge-into-exo | Orchestration visibility. |
| protean-edu | /edu mode stub. Lessons not written. | active | 2026-10-11 | merge-into-exo | Small. Fits the user-model goal. |
| protean-sym2p | SYM-2P compact agent message language, validator, bench. | active | 2026-10-11 | merge-into-exo | Wire format for agent-to-agent traffic. |
| protean-skills | Public Hermes skills tap, seven skills. | active | 2026-10-09 | merge-into-exo | Bundle as Exo default skills. |
| protean-ordaprompt | Router plugin: topic and session routing that abstains when unsure. In upstream catalog. | active | 2026-10-09 | merge-into-exo | Session routing for Orda. |
| protean-team (fork of team6-kit) | README duplicates protean-kit. | active | 2026-10-09 | reference-only | Duplicate. Archive candidate. |
| protean-github-flow | Five GitHub procedures from issue to reviewed PR. | active | 2026-10-08 | merge-into-exo | Upstream contribution workflow. |
| protean-kit | Pinned composer for the ingredients, plus choreography docs. 2 open issues: full-kit install fails. | active | 2026-10-08 | merge-into-exo | Exo replaces the composer. Choreography docs move in. |
| protean-voice-studio | VoiceStudio TTS sidecar client. Catalog PR #134468 open. | active | 2026-10-08 | merge-into-exo (optional) | Optional voice plugin. |
| cmd-compaction-guard | Command Code mod that repairs compounding compaction summaries. | active | 2026-10-08 | reference-only | Port the check, not the code. |
| cmd-narration-guard | Command Code mod that flags turns claiming work with zero tool calls. | active | 2026-10-08 | reference-only | Port the check into Exo. Directly serves A's "send it back" rule. |
| protean-context | Empty scaffolds and CLI for an Eldunarya knowledge tree and Arif KB. | active | 2026-10-07 | merge-into-exo | Memory tree layout. Reconcile with hafiz. |
| protean-skills-internal (private) | Knowledge-ingestion skill. | active | 2026-10-07 | merge-into-exo | Ingestion routing. |
| protean-ordastate | Orda State v2: git-based multi-writer store, one merge seat, hash-chained events, handoff without transcripts. | active | 2026-10-07 | merge-into-exo | Session continuity layer. |
| musarchy | Pairs an Omarchy laptop with a Muse device, seven remote commands. 1 open issue. | active | 2026-10-06 | keep-as-is | Device bridge. Exo can call it. |
| protean-lcm | Opt-in LCM DAG context engine plugin. | stale | 2026-09-23 | merge-into-exo | Context engine option. |
| internal-lane-gate (private) | Per-worker lane enforcement plugin and skill. | stale | 2026-09-23 | merge-into-exo | Lane safety for workers. |
| protean-ordopilot | Orda front door: one plain input, stand-in backend, courier CLI. v0.1. | stale | 2026-09-22 | merge-into-exo | Exo's single-input UX. |
| internal-stoa (private) | Private exchange ops with Sparky team. 7 open issues. | stale | 2026-09-21 | keep-as-is | Live ops channel. Divan Bus replaces its transport. |
| docu-sort (fork) | Tax document classifier on Jev. | stale | 2026-09-20 | keep-as-is | Client work. |
| protean-continuum | FastAPI kanban over agent activity plus action log. Replaced raptora. | stale | 2026-09-18 | merge-into-exo | Exo dashboard. |
| protean-desktop-mods | Patchers for desktop group-chat caps and slash commands, watchdog. Has a quarantine notice. | stale | 2026-09-18 | merge-into-exo (then retire) | In a fork these become plain source changes. |

## Must-fuse deep entries

### protean-sym2p (SYM-2P)
- What: a compact message language for agents. Roles, actions and states become numbers, payloads become s-expressions. One packet per line. A brief packet is 101 bytes against 481 for JSON.
- Architecture: normative SPEC.md, a draft SPEC-V2, a stdlib validator with typed diagnostics (exit 0/1/2), a write-once publisher, templates, a worked review-fix-verify-merge loop, a bench harness.
- Key files: SPEC.md, SPEC-V2-DRAFT.md, scripts/protean-sym2p/sym-validate.py, sym-publish.py, bench/sym2-bench.py, gates/check-internal-names.py.
- Maturity: v1.0.0 tagged and pinned by protean-stoa. Open items O-1 to O-9 undecided. Issue #15 (transport signing) is open. 15 PRs merged.
- Exo merge: ship as exo/protocols/sym2p with the validator as a library. Divan Bus already signs envelopes, which closes O-1 if the body is SYM-2P. Use it for subagent briefs and receipts.

### protean-ordaprompt and protean-ordopilot
- What: ordopilot ("Orda") is the one-input front door. You type a request, Orda decides topic, session, profile and context. ordaprompt is the routing brain as a Hermes plugin. It scores all candidate topics and sessions at once and returns route, ask, or abstain.
- Architecture: ordaprompt has router.py, adapter.py, receipts.py (hashes and scores only, rejects free text), and an eval harness with calibration and negative probes. The auto-route answer is locked until calibration passes. ordopilot is a stdlib "courier" package: intake, redact, router, dispatcher, catalog projection, ledger, approvals, compactor, policy, CLI verbs.
- Key files: ordaprompt_router/router.py, eval/calibration.json, plugin.yaml; ordopilot docs/architecture.md and the courier package.
- Maturity: ordaprompt is in the upstream catalog (merged PR #119124), feature issue #119583 open. ordopilot is v0.1 with a fake backend.
- Exo merge: this is the Exo front door. Bundle ordaprompt as a core plugin. Wire ordopilot's courier onto real Hermes sessions and onto protean-ordastate for state. Calibrate with real feedback before unlocking auto-route.

### optchat-memory
- What: endless memory after Victor Taelin's OptChat spec. An append-only verbatim log plus a binary summary tree, exposed as recall through the MemoryProvider ABC.
- Architecture: daily JSONL log under HERMES_HOME/optchat, a background compactor that merges lines pairwise with a cheap aux model, a fixed-budget view tiled oldest-first, and tools optchat_zoom and optchat_date.
- Key files: optchat/provider.py, store.py, tree.py, view.py, compact.py, DESIGN.md.
- Maturity: in the upstream catalog (merged PR #132907). Tests for store, tree, view, provider.
- Exo merge: make it the default memory provider. Decide whether cognition-memory survives as a fact layer on top or is dropped.

### Subagent viewer (protean-subagent-viewer, hermes-subagent-viewer)
- What: a read-only Hermes Desktop pane. One live card per spawned subagent with goal, model, status, streaming thinking, last tool line, accumulated output, Active/Past toggle, and full-output copy.
- Architecture: one desktop/plugin.js, fs-watched, keyed by subagent_id, follows the active session.
- Key files: desktop/plugin.js, plugin.yaml.
- Maturity: v0.1.2 in the upstream catalog. It is the verified slice of a bigger "Chief of Staff" fan-out plugin whose steer and interrupt features were unreliable. "hermes-subagent-viewer" is not on GitHub. It is a local clone at ~/Documents/github-repos/hermes-subagent-viewer whose origin is protean-subagent-viewer. The parent lives locally at ~/Documents/team6/fanout/subagent-viewer.
- Exo merge: bundle the pane. Pair it with open PR #118956 (delegated subagent oversight) for the control half once steering is reliable.

### raptora and protean-continuum
- Raptora is not on GitHub. It lives locally at ~/Documents/team6/raptora and ~/Documents/ai work/Hermes/raptora. It monitored drift in the Eldunari and Team6 projects via a cron prompt (run_daily_checks.sh). On 2026-09-12 A asked for a replacement after moving to the GUI. That became Continuum.
- Continuum: a FastAPI app with a kanban over agent sessions and projects plus an action-log view scoped to external actions. SQLite WAL registry, scanner over ~/.hermes profiles, static pages-root with snapshots and a pin gate.
- Key files: server/dashboard/standalone.py, server/continuum/cli.py, continuum/action_log.py, server/config.yaml, docs/continuum-sync-architecture.md.
- Maturity: live on ports 18771 and 18772. Byte-faithful to the live tree. 158 files.
- Exo merge: Continuum becomes the Exo dashboard, ideally as a dashboard plugin route rather than a separate server. Keep raptora's rule "stale does not mean abandoned". Drop raptora itself.

### protean-kit
- What: the pinned composer that installs the seven ingredients from kit.lock.json with tag SHA and tree-hash checks. Also holds choreography docs: open-source contribution, orchestration, anti-loop, self-improving flywheel.
- Key files: kit.lock.json, build/install.py, build/check-lock.py, choreography/*.md, WHY.md.
- Maturity: 1.8.0. Issues #14 and #15 say the full-kit install fails closed and 11 of 21 post-install gates fail.
- Exo merge: Exo is one tree, so the composer is no longer needed. Move choreography/*.md into Exo doctrine. Retire the lock-and-fetch installer. This removes the cross-ingredient conflicts behind #14 and #15.

### protean-doctrine
- What: the five-stage pipeline (research, architecture, design, build, QA), the role map, the handoff protocol, QA gates, and the clean-writing contract.
- Key files: skills/protean-operating-doctrine/SKILL.md, skills/clean-writing/SKILL.md, two leak and reference gates.
- Maturity: 8 PRs merged. Stable.
- Exo merge: bundle as default skills. Add "scale process to risk" so cheap workers stop dying on iteration budgets, per protean-team.md.

### protean-team-skills
- What: the private source of record for team skills, ops ledgers and gates.
- Layout: creative/, github/ (issue-to-pr, pr-audit, pr-workflow, draft-review-html, authorship cleanup), orchestration/ (control-plane, council, stoa-exchange, team6 doctrine), sym-protocol/, ops/ (INFLIGHT, LEARNINGS, ROTATION-STATE, OWNERSHIP-MATRIX, DISPATCH-LEDGER), scripts/ with 30+ check-*.py gates.
- Maturity: 8 PRs merged, 4 open issues. It is the upstream source for several public ingredients, so content is duplicated.
- Exo merge: make this the single source. Move the skills to exo/skills and the gates to exo/gates. Keep ops/ ledgers as runtime data outside the code tree. Public ingredients become generated exports or are archived.

### protean-stoa
- What: the public SYM-2P exchange profile with the Sparky family. Packet mailboxes per party, a peer registry, procedures, and a pinned validator hash.
- Maturity: Phase 0, git as transport, no daemon, no keys. internal-stoa is the private twin with 7 open issues.
- Exo merge: keep the repo as the shared mailbox. Exo needs only a stoa client skill. Divan Bus can replace the git transport later.

### protean-handoff
- What: phone control at the skill edge. Read lane state, steer, approve or deny from WhatsApp or Discord with no new adapter. Disk snapshot per lane, append-only journal, idempotency keys, approvals bound to a target fingerprint.
- Key files: SKILL.md, references/protocol.md, scripts/approvals.py, command.py, snapshot.py, verify.py.
- Maturity: specified and built. The README itself says do not rely on phone approvals until deployed and audited.
- Exo merge: bundle as a skill. Audit on the Mac before trusting phone approvals.

### protean-model-sync
- What: fleet model control. Per-member model and reasoning for Bot Mode group chats, push one model to every session of an agent, and named presets with diff preview, backup and rollback.
- Key files: plugin.py, dashboard/plugin_api.py, dashboard/config_core.py, desktop/plugin.js, presets/presets.json.
- Maturity: in the upstream catalog. Good test coverage. Issue #1 open (room rename). Upstream issue #98923 open.
- Exo merge: bundle. This is how Exo keeps the cheap-model fleet consistent, which A's cost ruling needs.

### protean-edu
- What: /edu on|off|status|next|help teaches a non-technical user to read agent code.
- Maturity: stub. The command works, lessons are not written. Five tests.
- Exo merge: bundle the command. Write lessons later. Fits the guardian-angel model if Exo teaches A's family members.

### protean-drafts
- What: one command renders a draft as a dark HTML page and runs prose, identifier, quote-integrity and render-fidelity gates.
- Key files: scripts/protean-drafts/draft_pipeline.py, render_draft_html.py, check-prose.py, templates/pr-draft.md.
- Exo merge: bundle as the external-post gate. Every public post by an Exo seat goes through it.

### hydro-os
- What: the Omarchy layer that makes charminar Shahid's machine. Guarded Hyprland blocks, launchers, setup scripts, and jarvis (push-to-talk with Claude as brain and a fixed safe-action list).
- Maturity: 4 PRs merged, live on charminar.
- Exo merge: stays separate. Exo's Linux installer can reuse its guarded-block pattern. Jarvis's safe-action list is reference for an Exo voice mode.

### musarchy
- What: pairs an Omarchy laptop with a Muse device. Seven commands: run, file move, health, windows, notifications.
- Maturity: alpha, tested on hardware 2026-10-04. Issue #1 open. The install URL still says ahrazzle/musarchy, so the repo moved.
- Exo merge: stays separate. Exo can expose it as a tool for the charminar seat.

### divan-bus
- What: signed, hash-chained relay for agent seats. Ed25519 per seat, signed registry, optimistic chain append with 409 retry, NaCl sealed bodies, daily checkpoints, MCP server, web view.
- Key files: SPEC.md, relay/app.py, relay/crypto.py, relay/store.py, relay/mcp_server.py, clients/divan.py, tools/vault_bridge.py.
- Maturity: Phase 1 on one Mac over Tailscale. Tests for relay, crypto, MCP, client.
- Exo merge: Exo's transport between seats and machines. Ship the client and the MCP server in Exo. Run the relay as an Exo service. It replaces the vault and stoa git transport.

### frontier-math
- What: live kappa race dashboard. fetch.py parses upstream PR claims, gate.py enforces schema and claimed-vs-verified separation, an Action runs every 15 minutes.
- Exo merge: stays separate. It is a good example of a gated Exo lane, nothing to fuse.

### EvoPet and TamaHermes
- What: TamaHermes ports TamaCodex to Hermes: a pet that grows from Hermes hook events, in the terminal and on the desktop via Petdex. EvoPet joins that pet half with a native desktop shell that taps hooks from Claude Code, Codex, opencode, Gemini CLI and Hermes into one pet.
- Key files: TamaHermes tamahermes/hermes_events.py, bridge.py, evopet_drain.py, hermes/install-hermes.sh; EvoPet EVOPET.md, drizzle migrations, the desktop shell.
- Maturity: TamaHermes is in the upstream catalog (merged #118458, #120738). EvoPet main stalled since 2026-09-20 with 15 open branches (issue #35).
- Exo merge: ship the TamaHermes plugin as an optional Exo plugin. Keep EvoPet as its own product. No core changes needed.

## hermes-agent contributions

### Pull requests

Themes: creds = credentials and cooldown; codex = Codex and reasoning replay; desktop; tui = CLI and TUI; plugins; skills; sessions = session model config; update = update, backup, doctor; evals; routing; memory; catalog.

| # | Title | State | Theme | Exo verdict |
|---|---|---|---|---|
| 134468 | catalog: add voicestudio | open | catalog | carry-in-fork (bundle plugin, entry moot) |
| 134235 | fix: silence provider warnings on the update path | open | update | carry-in-fork |
| 133775 | catalog: add cognition standalone memory provider | open draft | memory | revive only if cognition stays |
| 133007 | feat(codex): bound encrypted-reasoning replay on proxy routes | open draft | codex | carry-in-fork |
| 132908 | MCP Events receiver platform plugin | open draft | plugins | carry-in-fork |
| 132906 | feat(plugins): you-should-know sidecar observer | open draft | plugins | carry-in-fork |
| 129481 | fix(tui): keep gradient banner segments on their source row | open draft | tui | carry-in-fork |
| 120840 | fix(cli): unify busy-turn routing across editor, Enter, exec | open draft | tui | carry-in-fork |
| 119785 | Add outline read mode to file tools | open draft | context | carry-in-fork |
| 119652 | fix: re-probe a lone rate-limited credential, consolidate cooldown fixes | open | creds | carry-in-fork (priority) |
| 118956 | Delegated subagent oversight | open draft | skills | carry-in-fork |
| 118955 | GitHub draft review artifact | open draft | skills | carry-in-fork |
| 118954 | harden agent merge-conflict arbiter | open draft | skills | carry-in-fork |
| 118953 | macOS Harness workflow | open draft | skills | carry-in-fork |
| 118952 | Evidence-gated research reports | open draft | skills | carry-in-fork |
| 118951 | Citation integrity field guide | open draft | skills | carry-in-fork |
| 114436 | feat(evals): paired fail-closed credit verdict for toolperf_abeval | open draft | evals | carry-in-fork |
| 111597 | fix(codex): retry a transient terminal response.failed in stream | open draft | codex | carry-in-fork |
| 110172 | fix(desktop): show summary reasoning from message.complete | open draft | desktop | carry-in-fork |
| 110156 | fix(desktop): recoverable state when a pinned scope parks | open draft | desktop | carry-in-fork |
| 110011 | fix(skills): harden the curator audit ledger | open draft | skills | carry-in-fork |
| 108021 | fix(doctor): flag undocumented pre_update_backup values | open draft | update | carry-in-fork |
| 107130 | fix(goal): release a stalled-subagent wait barrier | open draft | orchestration | carry-in-fork |
| 106650 | feat(sessions): session model audit/reset CLI | open draft | sessions | carry-in-fork |
| 98901 | fix(gateway): session model overrides persist across idle reaping | open draft | sessions | carry-in-fork |
| 132907 | plugin-catalog: add optchat-memory | merged | catalog | already-upstream |
| 120738 | fix(plugin-catalog): persist TamaHermes session-end events | merged | catalog | already-upstream |
| 119124 | plugin-catalog: add protean-ordaprompt | merged | catalog | already-upstream |
| 118458 | plugin-catalog: add TamaHermes (aska-digital plugins) | merged | catalog | already-upstream |
| 113702 | fix(desktop): retry Electron mirror only when missing | closed | desktop | already-upstream (cherry-picked in #115676) |
| 108007 | fix(update): disabled pre-update backup is a skip | closed | update | already-upstream (salvaged in #114873) |
| 119135 | fix(agent): tag background-review fork turns | closed | orchestration | already-upstream (cherry-picked in #120601) |
| 96748 | fix(gateway): drop stale model/provider keys | closed | sessions | already-upstream (merged via #97066) |
| 111106 | fix(config): preserve comments and key order | closed | config | already-upstream (class fixed in #118918) |
| 108020 | fix(backup): snapshot recovery hint | closed | update | already-upstream (fixed independently) |
| 119344 | fix(codex): durable issuer-scoped replay disable | closed | codex | already-upstream (#130787 took the opposite policy) |
| 135155 | fix(update): name the remedy when respawn fails | closed | update | already-upstream (#135003) |
| 119172 | fix(auth): clear cooldown when env secret changes | closed | creds | carry-in-fork (folded into #119652) |
| 111612 | feat(retries): configurable mid-stream retry count | closed | codex | carry-in-fork (folded into #111597) |
| 98845 | fix(gateway): session override persistence | closed | sessions | carry-in-fork (reopened as #98901) |
| 97510 | feat(sessions): session model audit/reset CLI | closed | sessions | carry-in-fork (rescoped as #106650) |
| 133772 | feat(memory): cognition memory provider plugin | closed | memory | drop (moved to cognition-memory repo) |
| 126940 | feat(taste): taste learning with decay and conflict escalation | closed (wontfix) | user model | revive (core of the guardian-angel model) |
| 119782 | Deterministic auto-repair in turn recovery (placeholder) | closed | routing | revive from issue #119777 |
| 119783 | Budget-aware downshift for auxiliary routing (placeholder) | closed | routing | revive from issue #119778 (fits cost ruling) |
| 119784 | Failure-triggered tier escalation with sticky lock (placeholder) | closed | routing | revive from issue #119780 |
| 113706 | fix(auth): adopt the shared Nous grant | closed | creds | decide (maintainer ruled against it in #111724) |
| 109768 | fix(desktop): reuse gateway backend for served profiles | closed | desktop | drop (duplicate of #106742) |
| 119722 | Test coverage for firecrawl provider | closed | tests | drop (duplicate of #117954) |

### Issues

| # | Title (short) | State | Theme | Exo verdict |
|---|---|---|---|---|
| 136797 | Docs deploy loses JS on cache eviction | open | docs | drop (upstream infra) |
| 134995 | serve-restart marker keyed on stopped PID | open | update | carry fix if it bites |
| 133539 | MCP loop raises user-interrupt error for system interrupts | open | plugins | carry fix |
| 133538 | Compaction summaries can carry credential-shaped values | open | memory | carry fix (redaction) |
| 133506 | Workspace-snapshot tests fail on macOS | open | tests | carry fix |
| 132986 | reset_codex_reasoning_replay clears an earned verdict | open | codex | carry fix |
| 132934 | Compaction handoff republished as assistant reply | open | memory | carry fix |
| 126925 | tests-js npm discovery fails on macOS | open | tests | carry fix |
| 120983 | Stale background heartbeats delivered as user turns | open | tui | carry fix |
| 120839 | Ctrl+G editor submit dispatches /compress mid-turn | open | tui | covered by PR #120840 |
| 120381 | /goal parked after stalled subagent wait | open | orchestration | covered by PR #107130 |
| 119781 | Outline read mode | open | context | covered by PR #119785 |
| 119780 | Failure-triggered tier escalation | open | routing | revive |
| 119778 | Budget-aware downshift hook | open | routing | revive |
| 119777 | Deterministic auto-repair in turn recovery | open | routing | revive |
| 119583 | protean-ordaprompt auto session and model routing | open | routing | carry (Orda) |
| 119163 | Absolute last_error_reset_at bypasses sole-credential short circuit | open | creds | covered by PR #119652 |
| 119162 | Replacing env key does not clear cooldown | open | creds | covered by PR #119652 |
| 98923 | Per-member model selector for group chats | open | desktop | covered by protean-model-sync |
| 98662 | Group-room slash commands | open | desktop | covered by protean-desktop-mods |
| 96745 | Session model audit/bulk reset CLI | open | sessions | covered by PR #106650 |
| 95777 | No HTTP/1.1 fallback when git HTTP/2 stalls | open | update | carry fix |
| 132985 | test | closed | none | drop |
| 132949 | Instruction answered only with interrupted placeholder | closed | tui | already-upstream (claimed by another dev) |
| 119336 | opencode-go replay 400 strips reasoning | closed | codex | already-upstream (#130787) |
| 114364 | Extracted-memory-provider pilot via plugins market | closed | memory | already-upstream (pilot happened) |
| 113720 | Benched credential triggers first-run wizard | closed | creds | already-upstream |
| 113719 | Switching provider leaves old base_url | closed | creds | already-upstream |
| 113718 | Nous relogin benched as transient | closed | creds | already-upstream |
| 113708 | macOS SDK and linker mismatch | closed | desktop | already-upstream |
| 111406 | Config-driven group chat caps | closed | desktop | already-upstream (dup of #101101) |
| 111105 | Unchanged mtime hides file replacements | closed | config | already-upstream |
| 109865 | Desktop spawns duplicate serve backend | closed (not planned) | desktop | drop |
| 107062 | background_review fires on_session_start | closed | orchestration | already-upstream |
| 95788 | runGit() has no timeout | closed | desktop | already-upstream (#107648) |
| 95759 | Desktop update does not heal a bricked venv | closed | update | already-upstream |
| 93492 | Group chats crash after deleting a connection | closed | desktop | already-upstream (#93668) |

## Fork branches (ahrazzle/hermes-agent and aska-digital/hermes-agent)

- ahrazzle fork main: diverged, +1 / -25585 vs NousResearch:main. The one commit is mozi e681c45c (skills pr-workflow and tdd rule bumps).
- aska-digital fork main: diverged, +1 / -1264. The one commit is mozi 3e2e2c45, the same change.
- Of 122 ahrazzle branches not on upstream, 49 are A's PR heads. About 70 are stale copies of deleted upstream branches by teknium1 and others (cline-port/*, lobehub-port/*, tools-scout/* and similar). 3 have no commits ahead.
- Of 29 aska-digital branches not on upstream, only fix/compress-timeout-abort (+1, author abu-aska, 2026-10-08) is team work. The rest are stale upstream copies.

PR head branches vs NousResearch:main (ahead / behind):

| PR | Branch | Ahead | Behind |
|---|---|---|---|
| 134468 | aska-digital:catalog/voicestudio | 3 | 33 |
| 134235 | fix/solstice-discovery-and-provider-warning-buffer | 20 | 242 |
| 133775 | catalog/cognition-standalone | 5 | 33 |
| 133007 | feat/codex-proxy-replay-window | 1 | 2929 |
| 132908 | draft/mcp-events-receiver | 11 | 3193 |
| 132906 | draft/you-should-know | 2 | 2935 |
| 129481 | fix/tui-banner-gradient-rows | 3 | 1264 |
| 120840 | fix/editor-slash-busy-routing | 13 | 2694 |
| 119785 | proposal/p4-python-outline | 2 | 11100 |
| 119652 | fix/119163-sole-credential-reset-clamp | 44 | 163 |
| 118951-118956 | feat/optional-* (6 branches) | 1-3 each | 11301 |
| 114436 | mozi-111237 | 2 | 3702 |
| 111597 | fix/codex-responses-terminal-retry | 7 | 2694 |
| 110172 | fix/desktop-reasoning-completion-fallback | 1 | 3711 |
| 110156 | fix/desktop-profile-loading-recovery | 1 | 3711 |
| 110011 | fix/skill-ledger-complete-reversible | 12 | 228 |
| 108021 | fix/doctor-legacy-pre-update-backup-bool | 5 | 3711 |
| 107130 | fix/goal-barrier-parked-resume | 4 | 16879 |
| 106650 | fix/sessions-model-audit-reset | 1 | 18387 |
| 98901 | feat/session-override-persistence | 4 | 3702 |
| 126940 (closed, revive) | feat/taste-corroboration-engine | 6 | 10363 |
| 119782-119784 (closed) | proposal/p1..p3 | 1 (empty) | 11100 |
| 133772 (closed) | feat/memory-provider-cognition | deleted | - |

Every branch is diverged. Branches more than about 3000 behind will need real rebases, not clean cherry-picks. The six optional-skill branches and 107130 and 106650 are the oldest.

## Other upstream repos

| Repo | Item | State |
|---|---|---|
| CrocSwap/integer-mult-bounds | PR #469, #464: conditional kappa improvements on #450's word | open |
| Swapnil-jain/gf2m-circuits | PR #1: GF(2^9..2^11) and GHASH multipliers | open |
| Swapnil-jain/mobius-x1 | PR #1: E1 full-network check | merged |
| CommandCodeAI/command-code | PR #1004 register cmd-compaction-guard (open); RFC-002, RFC-003 (closed); issues #1001, #1003 open, #1002, #915, #916 closed | mixed |
| CommandCodeAI/desktop | PR #111 open, #110 closed; issues #109 open, #108 closed | mixed |
| anpicasso/hermes-jev-curator | PR #1, issues #2, #3 | open |
| hookdeck/mcp-events-bridge | Issue #29: 410 handling | open |
| crafter-station/petdex | Issue #775: zombie session cards from review forks | closed |
| Edge0-AI/Edge0 | Issues #103, #104 | closed |
| Sahil-SS9/hermes-Custom-CLI-Themes | PR #1 Rich markup wrap fix | closed |
| bshiyat/bshiyat | PR #3 README snake fix | open |

No PRs or issues by ahrazzle on llama.cpp were found.

## Open questions

1. Exo base: fork aska-digital/hermes-agent (1264 behind) into aska-digital/exo, or start exo fresh from upstream main and cherry-pick? The ahrazzle fork is 25585 behind and should stay only as a PR-head host.
2. Memory: optchat as default, and cognition-memory dropped or kept as a fact layer? This also decides PR #133775.
3. Taste learning (#126940) was wontfix upstream. Revive it as the core of the guardian-angel user model?
4. Shared Nous grant (#113706): upstream ruled profiles must not share auth. Does Exo want shared auth across A's own profiles?
5. protean-team-skills vs the public ingredients: which is the single source after fusion? Should the public ingredient repos be archived or generated from Exo?
6. protean-team duplicates protean-kit. Archive it?
7. Who is abu-aska, author of aska-digital fix/compress-timeout-abort? Should it become a PR?
8. Raptora is local only. Confirm it can be retired now that Continuum replaced it.
9. Prune about 70 stale upstream-copy branches on each fork? (Not done. Read-only pass.)
10. musarchy and protean-model-sync READMEs still point at ahrazzle/ install paths. Fix after the repo moves?
