# Exo local-documents inventory

Built 2026-10-11 by saga-divan. Read-only pass. It builds on `/Users/kethuda/hafiz/corpus/private/research-team6-inventory-20261011.txt` and does not repeat its deep dives.

Skipped on purpose: `~/Documents/team6/control/wrk/mct6/BRIEFS/`, `~/Documents/Ahraz-Wedding/`, every `.env`, `auth.json`, `.pem` and credential file.

Verdicts:
- **fuse**: the code or design goes into Exo itself.
- **migrate-data**: the data (memory, history, souls, KB) moves into Exo stores. The code does not.
- **reference**: read it while designing, then leave it.
- **drop**: no Exo value.

## 1. ~/Documents/team6/ agent infrastructure

Paths are relative to `/Users/kethuda/Documents/team6/`.

| Path | What | Status | Exo verdict | Reason |
|---|---|---|---|---|
| `divan-bus/` | Signed, hash-chained message relay for seats. Ed25519 identity, signed registry, SQLite append-only log, NaCl sealed bodies, daily checkpoints, vault bridge to Drive inboxes. Key files: `README.md`, `SPEC.md`, `relay/`, `clients/divan.py`, `tools/vault_bridge.py`, `tools/checkpoint.py`. | Live. Phase 1 committed (last commit: bridge seeding and Drive timeouts). | fuse | This is the transport layer Exo needs for multi-agent orchestration. It is already the newest and cleanest piece. |
| `team6/` | Six-profile Hermes team package (Lugia, KodeKoot, Sheikh, Azaraki, Halakukhan, Shayba). Key files: `wrk/t6gc/STATE.md`, per-agent `SOUL.md`, `install.sh`. | Superseded by team6-kit, then protean-kit. | migrate-data | The SOULs and the locked decisions in `STATE.md` are the user model in operational form. The installer is obsolete. |
| `team6-kit-*` (9 worktrees: codemap, learnings, makepad-docs, memwiki, pr20, shellward-pr, site, sitecopy, wrk) | One worktree per feature branch of `ahrazzle/team6-kit`, now `aska-digital/protean-kit`. Key files: `choreography/*.md` (artifact-contract, governance, io-delegation, model-policy, orchestration, router-security, run-evidence, safe-run-packet, side-effect-cost-preflight), `templates/personas`, `templates/contracts`, `build/`, `tests/test_gates.py`, `PR_WORK_STATUS.md`. | Stale since 2026-09-14. PRs #7 and #10 conflict on CHANGELOG, README and kit.yaml. | reference (choreography) / drop (worktrees) | The choreography docs are the best written contracts in the tree. The worktrees themselves are dead branches; keep the canonical repo only. |
| `control/wrk/mct6/` (Mission Control) | Routing and governance hub. Key files: `IDEA.md`, `AGENTS.md`, `ROUTING.md`, `SKILLS.md`, `MOA_DESIGN.md`, `OPERATIONS/`, `LOG/`. | Live but drifting. Recent activity is personal briefs only. | fuse (ROUTING, MOA) | `ROUTING.md` answers "where does new information go" and is the seed of Exo's memory router. `MOA_DESIGN.md` is a costed model-routing design. |
| `mct6/` | Output of the 2026-09-12 retroactive ingestion audit: license verdicts, wave receipts, `STATE.md`, `NEXT-STEPS.md`, `github-items.tsv`, `manifest.json`. | Audit done. "No adoption approved." | reference | Useful license verdicts and a template for gated adoption of outside tools. |
| `command/` | Stub for the "command centre for orchestrating our team of agents". Two audit briefs on hermes-agent PR #96748. | Stub. No verdicts. | reference | Proves the command-centre idea is old. Nothing to reuse. |
| `fanout/` | "Chief of Staff" Hermes Desktop plugin v0.4.0: live subagent cards, steer and interrupt. Key files: `README.md`, `plugin.yaml`, `desktop/plugin.js`, `V2_BRIEF.md`. | Viewer MVP done and likely published as `ahrazzle/hermes-subagent-viewer`. Full plugin partly reliable. | reference | The UX of live subagent cards is what Exo's command centre should show. The code is Hermes-specific. |
| `raptora/` | Eldunari drift monitor. Writes a daily `OUTPUTS/pulse.md`. Key files: `wrk/rpt6/IDEA.md`, `OUTPUTS/pulse.md`, `OUTPUTS/dashboard.html`, `ELDUNARI_RECOMMENDATIONS.md`. | Live cron. 29 open findings, 0 new for weeks. Nobody acts on it. | fuse (idea) | The hiatus-aware rule is right for Exo: silence on purpose, reconstruct on return (`dark_since = next_action_verified + 72h`). |
| `Eldunari/` | Earlier "persistent consciousness" design: Anima per agent, shared Nexus, codification and flow protocols. All in `archives - do not alter/`. Live copy at `~/.hermes/eldunari/`. | Archived. R1 to R8 recommendations unactioned. | migrate-data | The Anima and Nexus content is agent memory. Move the content, not the folder scheme. |
| `protean-handoff/` | Phone steering and approvals over the existing WhatsApp/Discord gateway. Key files: `SKILL.md`, `references/protocol.md`, `scripts/approvals.py`, `scripts/snapshot.py`, `tests/test_handoff.py`. | Implemented. Open items O1 to O6 unverified. | fuse | Exo needs remote approve, deny and snapshot from the phone. This is a tested start. |
| `orda-guide/` | Plain-language guides to the Orda state store and OrdaPilot, with `VERIFICATION.md` mapping 28 claims to source. | Complete. QA PASS. | reference | A model for how Exo docs should be verified. Also documents Orda gaps (`--tag` missing, `reconcile` cannot repair a tampered chain). |
| `hermes-agent-self-evolution/` | NousResearch DSPy plus GEPA repo for evolving skills and prompts. Local uncommitted `team6_targets.py`. | Phase 1 adaptation. No run recorded. Datasets empty. | reference | Skill self-evolution is a later Exo feature. Needs an eval set first, which Exo will produce. |
| `arif/` | Personal knowledge base (`wrk/gc1`). Ingests mail, X bookmarks, Grok export, Apple Notes, Chrome bookmarks, GitHub stars, Google Takeout, Discord. Chroma plus pgvector. About 919 items. Key files: `wrk/gc1/IDEA.md`, `ingest_*.py`, `access_layer.py`, `EMBEDDING_MIGRATION.md`, `mats/Google/` (12 GB Takeout), `mats/ttl/` (Grok export copy), `mats/xrape/`. | Live. `arif_kb.db` is 0 bytes; the real store is in Chroma/pgvector. No git. | migrate-data (and fuse the ingesters) | Arif is the closest thing to Exo's long-term memory. The ingest scripts are reusable. Holds personal data, so treat as private. |
| `airefea/` | "Airefea Ideation Hub" handoff (`wrk/HANDOFF/`: IDEA, INBOX, SOP-SPINOFF, 13-criteria viability pass) and `airefea-kit` (early team6-kit copy with `WHY.md`). | Handed off. Kit copy is stale. | reference (hub SOP) / drop (kit copy) | The capture, viability gate, spin-off funnel fits Exo's project command centre. |
| `metabot/` | `IDEA.md`: "Multi-agent system for research, planning, and implementation." Holds nani CLI progress UI research and a prototype HTML. | Stub plus UX research. | reference | The nani progress UI is a candidate for Exo's terminal status view. |
| `async/` | `IDEA.md`: "becoming the conductor for an orchestra of agents". | One-line stub. | drop | Same intent as Exo. No content. |
| `general/` | `IDEA.md`: "general assistance". | One-line stub. | drop | No content. |
| `meta work/` | `IDEA.md` plus `team6-model-config-reference.html` and `CPTcompare.html`. | Static references. | reference | Model-config reference is useful for Exo's model policy. |
| `charminar/` | Brief and `CLAUDE.md` for the Claude seat on Shahid's Omarchy machine. | Current (saga-divan work). | reference | Template for a remote seat brief with hard rules. |
| `team6-aska-deploy/` | Static deploy of the team6 site. | Done. | drop | Marketing only. |
| `discord-transcriber/`, `xingest/` | Scoped briefs, never built. | Stubs. | reference | Possible future Exo ingesters. |

## 2. ~/Documents/ outside team6

| Path | What | Status | Exo verdict | Reason |
|---|---|---|---|---|
| `/Users/kethuda/Documents/Saga.rtf` | A's welcome letter to the saga seat: who he is, the meaning of his name, how he wants to be treated. | Current (10-09). | migrate-data | First-person user-model text. Belongs in hafiz. |
| `/Users/kethuda/Documents/ai work/PROPAGATION-JOURNAL.md` | Lugia's append-only journal of bug classes and which surfaces got the fix. | Live since 09-11. | fuse (pattern) | A "connected-surface propagation" ledger is how Exo keeps fixes in sync across repos. |
| `/Users/kethuda/Documents/ai work/Hermes/` | Older copies of arif, raptora, weave plus `sym2-handoff-doc.pdf`. | Older mirror of team6 folders. | drop | Duplicates. Use the team6 copies. |
| `/Users/kethuda/Documents/ai work/arz/` | v1 to v5 of an "arz" project. | Unknown, not opened. | reference | Check with A. Name suggests a personal assistant prototype. |
| `/Users/kethuda/Documents/Proteus/synthesis/SYNTHESIS.md` | "ASKA Agent Operations Overhaul" synthesis, chaired by sparky-lugia, 2026-10-05. Ten agents agree on a proof-carrying deploy gate and more; four open calls for A. | Current. | fuse | The tiered gate (T0, T1, T2), machine smoke gate by a different profile, and PASS bound to artifact digest are ready Exo rules. |
| `/Users/kethuda/Documents/Proteus/` (aesthetics, protean-feed, templates) | Persona art for the Protean profiles, blog feeds, decision-report template. Contains two `.pem` private keys (not opened). | Active. | reference | Persona art for the command centre. Move the `.pem` keys out of Documents. |
| `/Users/kethuda/Documents/Muse/collab-system/` | XCollab: two-way collaboration between an external AI (Muse) and Protean through a private git repo of SYM-2P packets. Key files: `SYSTEM-DESIGN.md`, `PACKET-SPEC.md`, `adapter/xcollab.py`. | Design plus reference adapter. | reference | Superseded in spirit by divan-bus, but the inbox and escalate acts are good message types. |
| `/Users/kethuda/Documents/agents/opencode/metawork/` | `SPEC-V2-DRAFT.md` for SYM-2P/2.0: symbolic, positional, pipe-separated packets with a translator to English. Plus hermes-agent clone, scripts, templates (1.4 GB). | Draft. | reference | Token-cheap agent-to-agent wire format. Worth weighing against divan-bus JSON envelopes. |
| `/Users/kethuda/Documents/OpenSource/aska/aska-nizam/protean-ordaprompt` | Orda prompt repo. | Unknown status. | reference | Prompt source for the orda profile. |
| `/Users/kethuda/Documents/OpenSource/drafts/hermes/` | Plugin API feedback and report, PR drafts. | Drafts. | reference | Hermes plugin limits that Exo must route around. |
| `/Users/kethuda/Documents/github-repos/` | Clones: hermes-agent, team6-kit, team6-kit-atropos, hermes-subagent-viewer, hermes-group-model-sync, hermes-desktop-mods, TamaHermes, TamaCodex, EvoPet, petdex. | Mixed. | reference | Source for anything Exo forks from Hermes. |
| `/Users/kethuda/Documents/Codex/` | Dated Codex task folders (09-07 to 10-11), mostly PR reviews. | Live scratch. | drop | Work outputs, not system design. |
| `/Users/kethuda/Documents/protean-projects/`, `command-code/`, `standardnotes/` | Protean venture builds; empty metawork; two Standard Notes text backups (not opened). | Mixed. | reference (notes) | The notes backups may hold prompts or ideas. A should confirm before ingest. |

## 3. iCloud Documents backup (2026-09-04)

Base: `/Users/kethuda/Library/CloudStorage/iCloudDrive-iCloudDrive (2026-09-04 9:59 PM)/com~apple~CloudDocs/Documents/`. The folder name has an unusual space before PM; use a glob.

| Path (under base) | What | Status | Exo verdict | Reason |
|---|---|---|---|---|
| `hermes/agenticsystem/` (28 GB with soulbackups) | 23 numbered snapshots of the Team6 era (1-optimization to 23-reoptimization, 2026-08-19 to 08-29). Each holds `eldunari/` and `profiles/` with `state.db` session stores. | Frozen. | migrate-data | Only copy of Team6-era session history (see the exports file) and the evolution of the Eldunari docs. |
| `hermes/soulbackups/` | SOUL and skill backups for 8 profiles in 5 states: originals, eldunar1, finetuned, lugiaoptimizedall, lugiashadow. | Frozen. | migrate-data | Shows how persona prompts were tuned. Good eval material for Exo's persona layer. |
| `hermes/contribution/modelsyncplugin` | Model sync plugin bugfixing. | Old. | reference | Ancestor of hermes-group-model-sync. |
| `hermes/temp/ttl/30d/export_data/` | Copy of the Grok data export. | Frozen. | migrate-data | Duplicate of the Downloads zip. |
| `ai work/Claude/` (fullbring, metrade, M's Kitchen, 3F, carle, instnace, the search) | Claude-era project outputs. `fullbring/cowork/` holds `aide.py`, `aide-spec.md`, `CLAUDE.md`, `HANDOFF-PROMPT.md`, `ROADMAP.md`. | Frozen (July to Aug 2026). | reference | `aide-spec.md` and `HANDOFF-PROMPT.md` are early harness and handoff designs. |
| `ai work/Hermes/arif/` | Older Arif versions v1 to v7 and gc1 with ingest scripts. | Frozen. | drop | Older than the team6 copy. |
| `ai work/GPT/Evidence_Operating_System_White_Paper.pdf` | White paper on an evidence operating system. | Static. | reference | Matches the evidence-first doctrine. |
| `ai work/Grok/`, `Codex/` | Small Grok and Codex outputs. | Static. | drop | No system value. |
| `Aetherean/ai docs/`, `Aetherean/Automation workflow designer/` | One PDF (LeCun AGI) and one demo video. | Static. | drop | Not Exo material. |

## 4. Google Drive (excluding backup/)

Base: `/Users/kethuda/Library/CloudStorage/GoogleDrive-a@askaconsult.com/My Drive/`. The `.gdoc` files are pointers; content needs the Drive API.

| Path | What | Exo verdict | Reason |
|---|---|---|---|
| `vaultofsouls/` | The old shared vault: agent inboxes, archives, INGEST notices, FROM/FOR notes. | migrate-data | Inter-agent history. Divan Bus replaces the transport. |
| `Prompt buffer.gdoc`, `Output buffer.gdoc`, `pastebin team6.gdoc` | A's scratch buffers for prompts and outputs. | migrate-data | Likely hold A's raw prompts. Strong user-model signal. |
| `Azaraki Soul.gdoc`, `Azaraki Soul 2.0.gdoc`, `Lugia Pet Config.gdoc` | Persona drafts. | migrate-data | Persona history. |
| `Team6 OpenCode.gdoc`, `localmodel limits.gdoc`, `local beast.gdoc`, `hermes update temrinal text.gdoc`, `halakukhan crash investigation.gdoc` | Harness and model notes. | reference | Model and harness constraints A already hit. |
| `Asymptotic Intelligence.gdoc`, `Yggdrasil.gdoc`, `Daily Meeting Framework.gdoc` | Concept docs. | reference | May hold Exo-relevant vision. Not opened. |
| `Gemini Artifacts/`, `Meeting Minutes/` (Notes by Gemini) | Gemini outputs and meeting notes. | reference | Gemini history, partial. |

## 5. Reusable design ideas found

1. **Route by durability and user.** Chat is the triage inbox, never a store. Four questions decide memory, skill, KB or board. `/Users/kethuda/Documents/team6/control/wrk/mct6/ROUTING.md`.
2. **Layered mixture of agents with cost routing.** Heterogeneous proposers, aggregator plus QA gate, cost controlled by routing, not model count. `/Users/kethuda/Documents/team6/control/wrk/mct6/MOA_DESIGN.md`.
3. **Proof-carrying deploy gate.** Tier by blast radius, set by the dispatcher. Machine gate run by a different profile. Every PASS bound to artifact digest and environment. `/Users/kethuda/Documents/Proteus/synthesis/SYNTHESIS.md`.
4. **Measure at the recipient's end.** Exit 0 is not proof. Read back served bytes, aliases, file locations. Same file, plus the read-back doctrine in `team6/wrk/t6gc/STATE.md`.
5. **Hiatus-aware monitoring.** Silence is correct when A is away. On return, reconstruct with dated stall records. `/Users/kethuda/Documents/team6/raptora/wrk/rpt6/IDEA.md`.
6. **Signed, hash-chained relay.** Seat keys, signed registry, optimistic append with 409 retry, sealed bodies, daily notarized checkpoints. `/Users/kethuda/Documents/team6/divan-bus/README.md`, `SPEC.md`.
7. **Symbolic wire format.** English is a render target, packets are codes. `/Users/kethuda/Documents/agents/opencode/metawork/SPEC-V2-DRAFT.md`.
8. **Connected-surface propagation ledger.** One append-only entry per bug class, listing every surface and its sync status. `/Users/kethuda/Documents/ai work/PROPAGATION-JOURNAL.md`.
9. **Phone approvals over the existing gateway.** No new adapter; snapshot, approve, deny, verify. `/Users/kethuda/Documents/team6/protean-handoff/SKILL.md`.
10. **Idea funnel.** Append-only inbox, 13-criteria viability pass, user gate, spin-off into a project and room. `/Users/kethuda/Documents/team6/airefea/wrk/HANDOFF/`.
11. **Kit contracts.** Artifact contract, I/O delegation, side-effect and cost preflight, safe-run packet, run evidence, router security. `/Users/kethuda/Documents/team6/team6-kit-wrk/choreography/`.
12. **Cold-start briefs.** Role, references, allowed and forbidden decisions, owned files, exit evidence. Self-contained. Seen across `team6/`, `3F/handoff prepped/`, `airefea/wrk/HANDOFF/`.
13. **Gated adoption of outside tools.** License verdict first, one isolated proof at a time, written pass/fail gate. `/Users/kethuda/Documents/team6/mct6/OUTPUTS/retroactive-ingestion-2026-09-12/NEXT-STEPS.md`.
14. **Live subagent cards with steer and interrupt.** `/Users/kethuda/Documents/team6/fanout/README.md`, `V2_BRIEF.md`.
15. **Verified docs.** Each claim mapped to a source line or live run. `/Users/kethuda/Documents/team6/orda-guide/VERIFICATION.md`.

## 6. Hygiene notes

- Two private keys sit in `~/Documents/Proteus/` (`protean-group.2026-09-17.private-key*.pem`). Not opened. They should move to a key store.
- `~/Documents/team6/arif/wrk/gc1/arif_kb.db` is 0 bytes. Arif's real data is in Chroma and pgvector only, and Arif has no git.
- Old docs still cite `/Users/kethuda/Documents/ai work/Hermes/<project>`. Read those as `~/Documents/team6/<project>`.
