# Project Exo: merged inventory (decision-ready)

Built 2026-10-11 by saga-divan from `github.md`, `hermes-local.md`, `local-docs.md` and `model-history-exports.md`, under D-004 (mechanism vs principal) and D-005 (Hermes as a distribution). Read-only. One extra check: GitHub user `abu-aska` (see Q7).

## Counts per verdict

| Verdict | Count |
|---|---|
| `core` | 18 |
| `plugin` | 13 |
| `skill` | 18 |
| `patch` | 5 |
| `upstream` | 9 |
| `data` | 28 |
| `reference` | 11 |
| `retire` | 10 |
| `drop` | 25 |
| **Total** | 137 |

Counting rule: one per table row in sections 2, 3 and 5. A must-fuse row with a split verdict counts once per verdict listed. Grouped rows count once.

## 1. Verdict vocabulary

| Verdict | Meaning |
|---|---|
| `core` | Becomes part of the `exo` Python package. |
| `plugin` | Ships as an Exo-installed Hermes plugin. |
| `skill` | Ships as a skill in Exo's skill pack. |
| `patch` | Carried core patch. Max 5. Each must be an open upstream PR. |
| `upstream` | Keep pursuing upstream only. Exo does not carry it. |
| `data` | Migrates into a principal store. Principal-specific, never in the generic package. |
| `reference` | Design input only. |
| `retire` | Was live or useful; stop running it and archive. |
| `drop` | No Exo value. For other people's or A's non-Exo repos, `drop` means "not part of Exo"; the repo stays where it is. |

Privacy tiers (section 5): **P1** generic, safe to ship. **P2** A's private work (team ops, agent memory, task history). **P3** personal (conversations, mail, notes, profile). **P4** secret (credentials, session keys): never migrated, re-created.

## 2. Must-fuse table

| Item | Locations | What it is | Maturity | Verdict | Exo component | Generic part / principal-specific part |
|---|---|---|---|---|---|---|
| SYM-2P | aska-digital/protean-sym2p; `~/.hermes/team-skills/sym-protocol/`; `~/Documents/agents/opencode/metawork/SPEC-V2-DRAFT.md`; `~/Documents/Muse/collab-system/` (XCollab) | Compact agent message language (101 B vs 481 B JSON), validator, publisher, bench. | v1.0.0 tagged, pinned by protean-stoa. O-1 to O-9 open; #15 transport signing open. | `core` | `exo.protocols.sym2p`: validator as a library, used as the optional body inside Divan Bus envelopes for briefs and receipts. | Generic: spec, validator, bench. Principal: peer registry, party names, stoa mailboxes. |
| protean-ordaprompt + ordopilot | aska-digital/protean-ordaprompt and /protean-ordopilot; `~/.hermes/plugins/protean-ordaprompt`; `~/.hermes/desktop-plugins/protean-ordaprompt`; source in kanban workspace `t_245b66d4/repo`; `~/Documents/OpenSource/aska/aska-nizam/protean-ordaprompt` | One-input front door. Router scores all topics and sessions at once and returns route, ask or abstain. Receipts hold hashes and scores only. | ordaprompt in upstream catalog (#119124), auto-route locked until calibration passes. ordopilot v0.1, fake backend. | `plugin` (ordaprompt) + `core` (ordopilot courier) | Exo front door. Router ships as plugin plus desktop pane. Courier becomes `exo.intake`, wired to real Hermes sessions and ordastate. | Generic: router, abstain logic, calibration harness, courier. Principal: topic catalog, calibration labels from A's corrections, route history. |
| optchat-memory | aska-digital/optchat-memory; catalog entry (#132907) | Endless memory: append-only verbatim daily JSONL plus binary summary tree, zoom and date tools. | In upstream catalog. Tests for store, tree, view, provider. | `plugin` | Default Hermes memory provider in Exo installs. Its log is the episodic tier under principle 1. | Generic: provider code. Principal: the log and summary tree under the principal store, not `HERMES_HOME`. |
| Subagent viewer / chief-of-staff | aska-digital/protean-subagent-viewer; `~/Documents/github-repos/hermes-subagent-viewer` (local clone, same origin); `~/Documents/team6/fanout/`; `~/.hermes/desktop-plugins/chief-of-staff` (ahrazzle/hermes-chief-of-staff, live in shaka); `~/.hermes/tui-widgets/subagent-monitor.mjs` | Read-only desktop pane with one live card per subagent. Parent "Chief of Staff" adds steer and interrupt. | Viewer v0.1.2 in catalog, verified. Steer and interrupt unreliable. | `plugin` | Command-centre pane. Ship the viewer now; add steering only after it passes a reliability gate. hermes-local's `core` verdict for chief-of-staff becomes `plugin` under D-005. | All generic. No principal data. |
| raptora / continuum | `~/Documents/team6/raptora/` and `~/Documents/ai work/Hermes/raptora/` (local only, cron on proteus); aska-digital/protean-continuum; `~/.hermes/plugins/continuum`; `~/.hermes/desktop-plugins/continuum`; `~/.hermes/mozi-continuum-review-test/` (dup) | Raptora: daily drift monitor, 29 stale findings, nobody acts. Continuum: FastAPI kanban over sessions and projects plus external-action log. | Raptora live cron, ignored. Continuum live on 18771/18772, 158 files. | `plugin` (continuum) + `retire` (raptora) + `core` (hiatus rule) | Continuum becomes the Exo dashboard, ideally a dashboard plugin route, not a separate server. Raptora's "stale is not abandoned" rule (`dark_since = next_action_verified + 72h`) goes into `exo.attention`. | Generic: scanner, board, action log, hiatus rule. Principal: project list, scan roots, profile names. |
| Protean system: kit, doctrine, team-skills, control plane, ops | aska-digital/protean-kit, /protean-doctrine, /protean-team-skills (`~/.hermes/team-skills`), /protean-control-plane, /protean-ops; ahrazzle/team6-kit (archived); `~/Documents/team6/team6-kit-*` (9 worktrees); profile skill `protean-system` | Composer installing 7 ingredients from a lock; choreography contracts; five-stage pipeline, role map, QA gates; clean-writing; 22 to 30 fail-closed check scripts; ops record schemas and ledgers. | Kit 1.8.0, full install fails (#14, #15). Doctrine stable (8 PRs). team-skills live, in sync, 4 issues. | `core` (control plane, check scripts, ops schemas, prose gate) + `skill` (doctrine, choreography, team pipeline, council, github pack) + `retire` (kit composer and worktrees) + `data` (ops ledgers) | `exo.dispatch` (control plane, preflight gates from `scripts/check-*.py`), `exo.records` (ops schemas), `exo.gates.prose`. Skills move to `exo/skills`. One source: Exo. | Generic: gate mechanism, pipeline, role map, record schemas, choreography. Principal: clean-writing rules (A's style profile), LEARNINGS lrn-001 to 069, decision reports, rotation and ownership ledgers, seat names. |
| divan-bus | aska-digital/divan-bus; `~/divan-bus` and `~/Documents/team6/divan-bus/`; skill `~/.hermes/skills/divan-bus`; launchd ai.aska.divan-bus, divan-bridge, divan-checkpoint, cloudflared-divan; aska-digital/divan-checkpoints | Signed hash-chained relay: Ed25519 seats, signed registry, 409-retry append, sealed bodies, daily checkpoints, MCP server, web view, vault bridge. | Phase 1 live on one Mac over Tailscale, tests pass. | `core` + `skill` | `exo.bus`: client and MCP server in the package, relay as an Exo service. Replaces the Drive vault, bot_relay, hermes-bridge and the stoa git transport. Skill teaches agents to post and read. | Generic: relay, crypto, client, MCP, checkpoint tool. Principal: seat registry, keys (P4), message log and checkpoints (data). |
| hafiz | `~/hafiz` (git, 1 commit 92db4ba, 421 MB); skill `hafiz` | Guardian-angel KB for A: INDEX router, PRINCIPAL.md, nodes, timeline, corpus manifests, training sets, `tools/kb.py` and `extract.py`, method and schema. | v0, built 2026-10-11. D-003 names it Exo's memory seed and ingestion v0. | `core` (method, schema, tools) + `data` (content) | `exo.principal`: store format, router, kb find/get/ctx/links, lint, graph, extractors. `exo.onboard` builds an empty store for a new user. | Generic: schema, pipeline, tools, privacy rules. Principal: PRINCIPAL.md, nodes, timeline, corpus, training sets. hafiz content becomes A's principal store #1. |

## 3. Everything else

Data sources are listed once, in section 5.

| Item | Location | Status | Verdict | Reason |
|---|---|---|---|---|
| **Repos** | | | | |
| hermes-agent fork (ahrazzle) | github ahrazzle/hermes-agent | 25,585 behind; 48 PR heads | `upstream` | PR head host only; never the base. |
| hermes-agent fork (aska-digital) | github aska-digital/hermes-agent | 1,264 behind; one team branch | `retire` | D-005 uses upstream tags. Move fix/compress-timeout-abort out first. |
| exo | github aska-digital/exo; `~/Work/exo` | empty | `core` | Home of the `exo` package, plugins and patch files. |
| EvoPet + TamaHermes | ahrazzle/EvoPet, ahrazzle/TamaHermes; `~/.hermes/plugins/tamahermes`; `~/evopet-pet`; `~/.hermes/tamahermes/`; `~/.hermes/pets/` | catalog; EvoPet main stalled | `plugin` | Optional pet plugin. EvoPet shell stays its own product. |
| protean-model-sync + group-model-sync | aska-digital/protean-model-sync; `~/.hermes/desktop-plugins/group-model-sync` | catalog, live | `plugin` | Fleet model control for the cost ruling. hermes-local `core` becomes plugin. |
| protean-handoff | aska-digital/protean-handoff; `~/Documents/team6/protean-handoff/` | built, unaudited | `core` | Phone approvals belong in Exo's gate. Audit before trusting. |
| protean-ordastate | aska-digital/protean-ordastate | v2 | `core` | Hash-chained session continuity. Merge with the bus log design. |
| protean-drafts | aska-digital/protean-drafts | live | `skill` | External-post gate for every public post. |
| protean-github-flow | aska-digital/protean-github-flow | live | `skill` | Issue-to-PR workflow. |
| protean-skills (public tap) | aska-digital/protean-skills | live | `skill` | Becomes a generated export of the Exo pack. |
| protean-skills-internal | aska-digital/protean-skills-internal | live | `skill` | Ingestion routing; fold into the hafiz pipeline. |
| protean-edu | aska-digital/protean-edu | stub | `plugin` | /edu command. Lessons later. |
| protean-voice-studio | aska-digital/protean-voice-studio; `~/.hermes/plugins/voicestudio` | live; catalog PR #134468 | `plugin` | Optional voice sidecar. |
| protean-lcm | aska-digital/protean-lcm | stale | `plugin` | Optional context engine. |
| internal-lane-gate | aska-digital/internal-lane-gate | stale | `plugin` | Lane safety for workers. |
| protean-stoa, internal-stoa | aska-digital/protean-stoa, /internal-stoa | live exchanges | `skill` | Repos stay as mailboxes. Exo ships the stoa client skill. |
| protean-context | aska-digital/protean-context | empty scaffolds | `retire` | hafiz supersedes it. |
| protean-team | aska-digital/protean-team | duplicate | `retire` | Duplicates protean-kit. Archive. |
| protean-desktop-mods + caps patchers | aska-digital/protean-desktop-mods; `~/.hermes/scripts/patch-groupchat-*.py`; launchd com.team6.caps-watchdog | watchdog exits 1 | `retire` | Bundle patching is not rebasable. See H5. |
| cognition-memory | aska-digital/cognition-memory; PR #133775 | "temp" | `retire` | optchat is the default. See Q2. |
| cmd-narration-guard | aska-digital/cmd-narration-guard | live (Command Code) | `core` | Port the "claimed work, zero tool calls" check into `exo.gates`. |
| cmd-compaction-guard | aska-digital/cmd-compaction-guard | live (Command Code) | `reference` | Idea for compaction checks. |
| hydro-os, musarchy | aska-digital/hydro-os, /musarchy | live on charminar | `reference` | Stay separate. Jarvis safe-action list and guarded blocks inform Exo voice and Linux install. |
| Competing harness forks | ahrazzle/command-code, /desktop, /Edge0, /hermes-Custom-CLI-Themes, /hermes-jev-curator, /particle-dashboard | stale | `reference` | Ideas only. |
| Math lane | ahrazzle/integer-mult-bounds, /gf2m-circuits, /mobius-x1 | active | `drop` | Not Exo. Feeds frontier-math. |
| Product and client repos | frontier-math, rhino-crm, docu-sort, acc-training, nutrak, core-dashboard, typemon, typejoy, aska-website, jormil-site, sharia-docs, EvoPet-site, nanaveda, workforce-proposal, tafsir, tafsir-staging, vibetrade | mixed | `drop` | Not Exo. frontier-math is a model gated lane. |
| Dead forks | bshiyat, human-atlas, my-test-vuegg-project, detectron2 | stale | `drop` | No value. |
| **Local plugins** | | | | |
| muse-bridge | `~/.hermes/hermes-agent/plugins/platforms/muse-bridge/` | untracked, exists nowhere else | `plugin` | Phone pairing and tunnel. Move to an Exo plugin repo today. |
| MCP Events receiver | PR #132908 | draft | `plugin` | Platform plugin; ship from Exo, keep the PR. |
| you-should-know observer | PR #132906 | draft | `upstream` | Needs a core observer seam; reconsider if a hook lands. |
| llama router | `~/.hermes/scripts/llama-router.sh`, `~/.hermes/llama-local/router.ini` | live | `core` | Local endpoint manager in `exo.services`; models list lives in the principal config. |
| Orda lease heartbeat | launchd ai.hermes.orda-lease-heartbeat | live | `core` | Generalize as seat leases in `exo.dispatch`. |
| hermes-bridge | `~/.hermes/tools/hermes-bridge/` | live | `retire` | /tmp queue. Bus and muse-bridge replace it. |
| bot_relay, bridge-exchange | `~/.hermes/bot_relay/`, `~/.hermes/bridge-exchange/` | stale | `drop` | Replaced by the bus. Test files. |
| TUI skins | `~/.hermes/skins/` (12) | live | `plugin` | Optional theme pack. |
| petdex shell hooks | root `config.yaml` hooks to `~/.petdex/bin/petdex-hook` | live | `retire` | Move pet telemetry to the plugin hook. |
| Third-party plugins | claude-subscription-directsdk, homeassistant, pixel-office, petdex-desktop, backsearch, spotify, dice-roller | live | `drop` | Exo does not ship them. Catalog installs stay available. |
| **Core mods and branches** | | | | |
| #119652 sole-credential re-probe | fix/119163-sole-credential-reset-clamp | open, 44 ahead / 163 behind | `patch` | See section 4. |
| #98901 session override persistence | feat/session-override-persistence | open draft, 3,702 behind | `patch` | See section 4. |
| #107130 stalled-subagent barrier | fix/goal-barrier-parked-resume | open draft, 16,879 behind | `patch` | See section 4. |
| #110011 reversible skill ledger | fix/skill-ledger-complete-reversible (+ stash@{2}) | open draft, 228 behind | `patch` | See section 4. |
| Routing hook (new PR from #119778, #119780) | branch to open | not yet a PR | `patch` | See section 4. |
| Other open core PRs | #134235, #133007, #129481, #120840, #119785, #114436, #111597, #110172, #110156, #108021, #106650 | open | `upstream` | Useful, not essential. Exo does not carry them. |
| Catalog PRs | #134468 (voicestudio), #133775 (cognition) | open | `upstream` | Plugins ship from Exo regardless. Close #133775. |
| Optional-skill PRs | #118951 to #118956 | open drafts | `skill` | Ship in the Exo pack now. Leave PRs open. |
| Taste learning | PR #126940 (closed wontfix), feat/taste-corroboration-engine | 10,363 behind | `core` | Rebuild in `exo.principal` as a memory-layer feature. See Q3. |
| Shared Nous grant | PR #113706 (closed) | ruled against | `drop` | See Q4. |
| Closed PRs already upstream | #113702, #108007, #119135, #96748, #111106, #108020, #119344, #135155 | landed | `drop` | Nothing to do. |
| Closed duplicates and folded PRs | #119172, #111612, #98845, #97510, #133772, #109768, #119722 | closed | `drop` | Folded or duplicate. |
| "Carry fix" issues | #134995, #133539, #133538, #133506, #132986, #132934, #126925, #120983, #95777 | open | `upstream` | Pursue upstream. #133538 (credentials in summaries) also gets an Exo-side redaction in `exo.gates`. |
| fix/compress-timeout-abort | aska-digital/hermes-agent | +1, abu-aska | `upstream` | See Q7. |
| Provider quota governor | local commits 6e39c1c, 35a7e4e | unpushed | `upstream` | Push to a branch now so it is not lost. See H8. |
| Kanban docs branches | docs/kanban-dispatcher-liveness, docs/kanban-skill-visibility | local | `upstream` | Spec docs; offer upstream. |
| Local stashes 0 and 1, solstice untracked files | `~/.hermes/hermes-agent` | uncommitted | `upstream` | Solstice belongs to #134235; desktop monitor stash is scratch. |
| Stale branches and worktrees | ~70 stale upstream copies per fork; 9 worktrees (3.9 GB); prune list in hermes-local section 2 | stale | `drop` | Rebuildable. See Q9. |
| **Skills** | | | | |
| Orchestration doctrine pack | profile skills (~20: lean-six-sigma-orchestration, supervision trees, subagent-stall-recovery, connected-surface-propagation...) | live, duplicated | `skill` | Distill into 3 to 5 skills; rest is design input. |
| Hermes ops and diagnostics | ~22 profile skills | live | `skill` | Merge into exo-diagnostics, exo-models, exo-profiles. |
| Local models | 7 profile skills + `local-endpoint-repair` | live | `skill` | One local-models skill. |
| Memory and knowledge | eldunari-absorption, -optimization, -maintenance, knowledge-base-*, hermes-memory-migration-prototype | mixed | `skill` | One principal-ingest skill aligned with the hafiz method. |
| Verification and release | ~20 Shaka skills | live | `skill` | Small verification pack. |
| Research and writing | ~14 Hazen skills | live | `skill` | Overlaps optional-skill PRs; dedupe. |
| GitHub pack | team-skills github/* (7), upstream-contribution-pr | live | `skill` | Replaces the stale github-* copies. |
| Packaging and plugin dev | ~8 profile skills, agentskills-io | live | `skill` | Needed to build Exo plugins. |
| Merge reconciler | `~/.hermes/skills/merge-reconciler`; PR #118954 | live | `skill` | Multi-agent merge need. |
| Model identity verification | feat/model-identity-verification-skill (local) | local only | `skill` | Ship as skill, not core. |
| Design and web skills | ~25 profile skills | live | `drop` | Project-level. `user-design-aesthetic` moves to data. |
| Project-specific skills | ~25 (fantasy-football-*, nani-notes...) | live | `drop` | Move to project repos. |
| Pet ops, openshorts | hermes-petdex-ops, media/openshorts-video | live | `drop` | Not Exo. |
| Upstream-removed copies | 10 names, in root and all 10 profiles | stale | `drop` | team-skills has maintained versions. |
| Curator archive | `~/.hermes/skills/.archive` (6) | archived | `reference` | History. |
| **Profiles** | | | | |
| Seat roles | proteus, orda, mozi, shaka, hazen, leo, frida, aetherean, kindi, sina, root | 10 live + root | `core` | Role templates (leader, relay, builder, QA, research, analysis, design, small tasks, sector seat) ship generic. SOULs, models and memories are data. |
| Empty shells | eaprofile, lugia, `.deleted/`, `proprofiles/` | empty | `drop` | Nothing inside. |
| **team6 and Documents projects** | | | | |
| team6 package | `~/Documents/team6/team6/` | superseded | `retire` | Installer obsolete. SOULs and STATE.md go to data. |
| Mission Control ROUTING.md | `~/Documents/team6/control/wrk/mct6/ROUTING.md` | live, drifting | `core` | Seed of Exo's memory router (memory, skill, KB or board). |
| MOA_DESIGN, model-config refs | `control/wrk/mct6/MOA_DESIGN.md`; `meta work/` | static | `reference` | Costed model-routing design. |
| Deploy-gate synthesis | `~/Documents/Proteus/synthesis/SYNTHESIS.md` | current | `core` | T0 to T2 gates, smoke by another profile, PASS bound to digest. |
| Eldunari router pattern | `~/.hermes/eldunari/` (ROUTER.md, scripts) | live | `core` | Router-plus-modules context model; heartbeat and index become services. Content is data. |
| Gated adoption audit | `~/Documents/team6/mct6/` | done | `reference` | License-first adoption template. |
| Design references | orda-guide, airefea hub, metabot, command/, discord-transcriber, xingest, charminar brief, PROPAGATION-JOURNAL, hermes-agent-self-evolution, arz | mixed | `reference` | Patterns: verified docs, idea funnel, progress UI, propagation ledger, later self-evolution. Ask A about arz. |
| Hermes plugin API notes | `~/Documents/OpenSource/drafts/hermes/` | drafts | `reference` | Limits Exo must route around. Read before writing hooks. |
| Arif ingesters | `~/Documents/team6/arif/wrk/gc1/ingest_*.py`, `access_layer.py` | live, no git | `core` | Reusable ingesters for `exo.principal`. Data separate. |
| Persona art, templates | `~/Documents/Proteus/` (minus keys) | active | `reference` | Command-centre visuals. |
| Older Claude-era designs | iCloud `ai work/Claude/fullbring/cowork/` (aide-spec, HANDOFF-PROMPT); GPT evidence white paper | frozen | `reference` | Early harness and handoff designs. |
| Duplicates and stubs | `ai work/Hermes/`, iCloud `ai work/Hermes/arif`, async, general, team6-aska-deploy, team6-kit worktrees, airefea-kit, `~/Documents/github-repos/`, Codex folders, iCloud Grok/Codex/Aetherean, modelsyncplugin | stale | `drop` | Duplicates, stubs or work output. |
| Drive harness and concept notes | Team6 OpenCode, localmodel limits, local beast, Asymptotic Intelligence, Yggdrasil, Daily Meeting Framework, Gemini Artifacts, Meeting Minutes | not opened | `reference` | Constraints and vision. Read via Drive API. |

## 4. Patch queue proposal (max 5)

Rule: a patch is only for behaviour no plugin hook can reach, that Exo depends on daily. Each is rebased on the pinned tag, stored as a file in `exo/patches/`, and dropped when it merges or a hook replaces it.

1. **#119652 re-probe a lone rate-limited credential.** Exo runs a cheap and free model fleet where many seats have one key. Without it, a single 429 parks a seat. Freshest branch (163 behind). Credential pool code has no plugin seam.
2. **#98901 session model overrides survive idle reaping.** Per-seat model pins are how the cost ruling is enforced. model-sync can set pins but cannot stop the gateway from dropping them. 3,702 behind; real rebase.
3. **#107130 release a stalled-subagent wait barrier.** Exo delegates constantly. A parked `/goal` (issue #120381, still open) silently stops a lane. Delegation internals have no hook. 16,879 behind; rebuild on the tag rather than rebase.
4. **#110011 reversible skill ledger.** Exo's learning loop writes and edits skills (principle 5). Curator mutations must be reversible and audited. 228 behind.
5. **New PR: auxiliary routing hook** (from issues #119778 budget downshift and #119780 sticky escalation). One hook that lets a plugin choose the model per call. It turns three core proposals into Exo plugin logic, so it should shrink the queue later. Open it upstream before carrying it.

Everything else core-side is `upstream` or `retire` (section 3). If upstream accepts #2 or #5, the next slot goes to #120840 (busy-turn routing) or #133007 (codex replay window), by measured pain.

## 5. Data migration list

Target is A's principal store (seeded from `~/hafiz`), with areas: `archive/` (raw, read-only, indexed), `log/` (episodic), `profile/`, `nodes/`, `agents/` (SOULs, seat configs), `rulings/`, `tasks/`, `config/`.

| Source | Location | Size | Format | Target | Tier | Verdict |
|---|---|---|---|---|---|---|
| hafiz content | `~/hafiz` | 421 MB | Markdown, JSON, JSONL | whole store seed | P3 | `data` |
| Hermes sessions (live) | `~/.hermes/state.db`, `profiles/*/state.db` | 4.3 GB; 9,088 sessions, 584K msgs | SQLite WAL | `archive/hermes` + index; extracts to `log/` | P3 | `data` |
| Hermes sessions (Team6 era) | iCloud `Documents/hermes/agenticsystem/23-reoptimization/profiles/*/state.db` | 417 MB; 464 sessions, 52,736 msgs | SQLite | `archive/hermes-team6`; freeze now, only copy | P3 | `data` |
| Other agenticsystem snapshots | iCloud `agenticsystem/1..22` | ~27 GB | SQLite, Markdown | keep Eldunari doc history only | P2 | `drop` |
| State snapshots, retired WALs | `profiles/*/state-snapshots`, `*.retired-wal-*` | ~4.1 GB | SQLite | none after one verified export | P3 | `drop` |
| Grok export | `~/Downloads/8dc893b8-...zip` (+2 unzipped copies in arif/mats/ttl and iCloud) | 56 MB zip | JSON | `archive/grok`; delete copies | P3 | `data` |
| Claude Cowork transcripts | `~/Library/Application Support/Claude/local-agent-mode-sessions/` | 155 MB; 7,340 msgs | JSONL | `archive/claude-cowork` | P3 | `data` |
| Claude Code CLI transcripts | `~/.claude/projects/` (-Users-kethuda, -work, -hermes-team-skills, 7 Aetherean dirs) | ~77 MB; ~7,100 msgs | JSONL | `archive/claude-code`; dedupe 2 pairs | P3 | `data` |
| Claude Desktop code metadata | `.../Claude/claude-code-sessions/` | 40 KB | JSON | none | P2 | `drop` |
| Codex sessions | `~/.codex/sessions/`, `state_5.sqlite`, `thread_history_1.sqlite` | 42 MB; 27 threads | JSONL, SQLite | `archive/codex` | P3 | `data` |
| ChatGPT desktop cache | `.../com.openai.chat/conversations-v3-*` | 1.6 MB | encrypted cache | none; request real export | P3 | `drop` |
| Gemini and AI Mode activity | arif `Takeout 2/My Activity/...` | 422 KB; 13 entries | HTML | `archive/google` | P3 | `data` |
| Google Takeout | `~/Documents/team6/arif/mats/Google/` | 12 GB | mixed | `archive/google`, selective ingest | P3 | `data` |
| Arif KB | Chroma + pgvector (arif_kb.db is 0 B) | ~919 items | vectors + text | `nodes/` via re-ingest | P3 | `data` |
| Eldunari KB | `~/.hermes/eldunari/` | 31 MB, 429 commits | Markdown in git, JSONL, SQLite index | `nodes/` and `agents/`, history kept; commit 8 dirty files first | P2 | `data` |
| Eldunari archives | `~/Documents/team6/Eldunari/` | small | Markdown | `agents/` (Anima, Nexus content) | P2 | `data` |
| Agent memories | `~/.hermes/memories/`, `profiles/*/memories/` | 73 KB | Markdown | MEMORY to `agents/`; USER.md to `profile/`, dedupe vs hafiz | P3 | `data` |
| SOULs and persona history | `profiles/*/SOUL.md`; iCloud `hermes/soulbackups/`; team6 SOULs; Drive Azaraki Soul, Lugia Pet Config | ~15 KB live + backups | Markdown, gdoc | `agents/`; backups as persona eval set | P2 | `data` |
| Team rulings | team-skills `ops/` (LEARNINGS, decision-reports); team6 `wrk/t6gc/STATE.md` | 2.2 MB | Markdown in git | `rulings/`; ledgers to `tasks/` | P2 | `data` |
| Kanban | `kanban.db`, `boards/team6-site`, attachments | 6 MB | SQLite + files | `tasks/`; rescue workspace repos t_245b66d4 and t_bb1d5841 | P2 | `data` |
| Kanban workspaces | `~/.hermes/kanban/workspaces/` | 1.4 GB | git clones | none after rescue | P2 | `drop` |
| Cron definitions | `cron/jobs.json`, `profiles/*/cron/jobs.json` | <100 KB | JSON | `config/`; definitions only | P2 | `data` |
| Configs | `config.yaml`, `profile.yaml`, `profiles/*/config.yaml` | ~75 KB | YAML | `config/`, by hand; no secrets | P2 | `data` |
| Misc state DBs | `projects.db`, `verification_evidence.db`, `shared-state.db` | <300 KB | SQLite | look once at projects.db | P2 | `data` |
| Divan message log | `~/divan-bus` store, aska-digital/divan-checkpoints | small | SQLite, signed JSON | `log/bus` | P2 | `data` |
| Vault of souls | Drive `My Drive/vaultofsouls/` | not measured | text, JSONL | `archive/vault` | P2 | `data` |
| Prompt and output buffers | Drive Prompt buffer, Output buffer, pastebin team6 | not measured | gdoc (needs API) | `archive/drive`; strong style signal | P3 | `data` |
| Saga letter | `~/Documents/Saga.rtf` | small | RTF | `profile/` | P3 | `data` |
| Design taste | profile skill `user-design-aesthetic` | small | Markdown | `profile/taste` | P2 | `data` |
| Standard Notes backups | `~/Documents/standardnotes/` | not opened | text | `archive/notes` after A confirms | P3 | `data` |
| Missing exports | claude.ai (each account), ChatGPT, Grok after 08-23, Gemini JSON, Claude Code on charminar | n/a | zips | `~/Work/exo/raw-exports/<platform>-<date>/` then `archive/` | P3 | `data` |
| WhatsApp gateway session | `~/.hermes/whatsapp/session` (+ stale copy) | 17.6 MB | Baileys files | none; re-pair | P4 | `drop` |
| Private keys | `~/Documents/Proteus/protean-group.*.pem` | 2 files | PEM | move to a key store, never migrate | P4 | `drop` |
| Pet, pixel-office state | `pets/`, `pixel-office/events.jsonl`, `tamahermes/*.json` | 144 MB | sprites, JSONL | stays with the pet plugin | P1 | `drop` |
| Caches, installs, binaries | `cache`, `profiles/*/cache`, `installs`, `bin`, `tools`, `logs` | ~31 GB | mixed | none | P2 | `drop` |
| Quarantine | `~/.hermes/_quarantine-20261011/` | 32 GB | mixed | none; check pre-reset tarball first (H6) | P4 (contains .env backups) | `drop` |

## 6. Answers to open questions (all recommended, for the lead to ratify)

From github.md:

- **Q1 Exo base.** Recommended: neither fork. Pin upstream NousResearch/hermes-agent to a weekly release tag (D-005). `aska-digital/exo` holds the `exo` package, Exo plugins and `patches/`. ahrazzle/hermes-agent stays the PR head host. Archive aska-digital/hermes-agent once its one team branch is moved.
- **Q2 Memory.** Recommended: optchat-memory is the default provider. Retire cognition-memory and close PR #133775. Fact extraction is covered by `exo.principal` nodes with provenance (principle 9), not a regex layer.
- **Q3 Taste learning (#126940).** Recommended: yes, revive, but in `exo.principal`, not as a core patch. Taste with decay and conflict escalation is principle 7 and 9 work and belongs at the memory layer (D-006 resolution 3). Taste values are principal data.
- **Q4 Shared Nous grant (#113706).** Recommended: no. Keep per-profile auth as upstream ruled. If A wants one login across seats later, build a per-principal credential broker in `exo`, never a core patch.
- **Q5 Single source.** Recommended: the Exo skill pack is the single source. protean-team-skills content moves into `exo/skills` and `exo/gates`; its `ops/` ledgers go to A's principal store. Public ingredient repos (protean-skills, protean-doctrine, protean-drafts, protean-github-flow) become generated exports or get archived.
- **Q6 protean-team.** Recommended: archive it.
- **Q7 abu-aska.** Account "shah", company @aska-digital, created 2026-10-04. Likely the charminar seat or Shahid; A to confirm. Recommended: if the compress-timeout fix reproduces on the pinned tag, open it upstream from ahrazzle. Exo does not carry it.
- **Q8 Raptora.** Recommended: retire now. Disable its proteus cron once A confirms. Keep the hiatus rule in `exo.attention`.
- **Q9 Stale branches.** Recommended: prune the ~70 stale upstream-copy branches on both forks and the merged local branches. Keep all live PR heads. Low priority.
- **Q10 ahrazzle install paths.** Recommended: fix the musarchy and protean-model-sync READMEs to aska-digital paths now. It is a two-line change each.

From hermes-local.md (implicit):

- **H1 muse-bridge exists only in a working tree.** Recommended: copy it today into an Exo plugin repo (or `exo/plugins/muse-bridge`). A `hermes update` reset would lose it.
- **H2 chief-of-staff and group-model-sync marked `core`.** Recommended: `plugin` under D-005. Steering ships only after a reliability gate.
- **H3 Divan Bus as skill or core.** Recommended: `core` (`exo.bus`) plus a skill. It is the transport and the signed log (principles 1 and 18).
- **H4 SYM-2P vs Divan Bus wire format.** Recommended: both, layered. Divan Bus is the signed envelope; SYM-2P is an optional body for agent-to-agent briefs and receipts. Anything shown to the principal renders in plain English.
- **H5 Group chat caps patchers.** Recommended: retire the patchers and the broken watchdog. Check whether config-driven caps from #101101 already shipped upstream; push the slash command via issue #98662.
- **H6 Quarantine.** Recommended: diff the 2026-09-07 pre-reset tarball against the current branches for lost local mods, then delete the 32 GB after A confirms.
- **H7 ordaprompt source in a kanban workspace.** Recommended: push `t_245b66d4/repo` and `t_bb1d5841/repo` to their repos before any workspace cleanup.
- **H8 Unpushed quota governor commits.** Recommended: push to a branch on ahrazzle now. Decide later whether it becomes an upstream PR or plugin logic behind the routing hook (patch 5).
- **H9 Skill duplication across profiles (433 copies, 182 names).** Recommended: install skills and plugins once from the Exo pack and enable per profile. Profiles keep only principal-specific skills.
- **H10 lugia has no live state.db.** Recommended: freeze a read-only copy of the Team6-era iCloud state.db files before the backup ages out.
