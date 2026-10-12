# Docs map

This is the one index of Exo's documents. Start here to find where a feature is explained, for whom, and what is
still missing.

## The rule

Every change that lands updates its technical doc and `DECISIONS.md` in the same commit. It also adds or updates a
row in this map, and notes its impact on the user guide and the developer guide. Whitepaper changes are batched per
wave. `tests/docs/test_docs_map.py` fails if a `## D-0NN` heading in `DECISIONS.md` has no row here, or if a doc file
this map names does not exist. The full rule, with steps, is in `docs/DEVELOPER-GUIDE.md`.

## The four layers

| Layer | Reader | Depth | Where |
|---|---|---|---|
| Whitepaper | A smart person who is not an expert | Why Exo exists and how it works, with no code. | `docs/WHITEPAPER.md`, `site/whitepaper` |
| User guide | Someone using Exo | Tasks and commands. What to type, what you will see. | `docs/USER-GUIDE.md` |
| Developer guide | Someone extending Exo | How to add a tool, plugin, seat, skill or command, test it and land it. | `docs/DEVELOPER-GUIDE.md` |
| Technical docs | Someone who needs the exact behaviour | Reference for one part each: formats, limits, rules, code paths. | The other `docs/*.md` files, `docs/ui/WEB.md`, and the plugin READMEs |

How the layers connect: a feature gets a technical doc first. The user guide and the developer guide point into it
and say only what a person needs to act. The whitepaper explains why, for the whole system, and is updated last.

## Coverage table

Cells name a file and a section, `n/a` when the layer has no reason to cover it, or `MISSING` when it should and
does not. Process decisions have no user or developer content, so those cells are `n/a`. User-guide and
developer-guide sections are written `§N`. Whitepaper sections are written `WP §N`. A whitepaper cell that says
`stale` means the paper covers the topic but states something the later decisions changed.

| Feature | Technical doc | User guide | Developer guide | Whitepaper |
|---|---|---|---|---|
| D-001 Work lives in this repo, phases leave handoffs | `DECISIONS.md` D-001 (process) | n/a | §8 | n/a |
| D-002 Research as parallel subagents | `DECISIONS.md` D-002 (process) | n/a | n/a | n/a |
| D-003 hafiz is an input | `docs/STORE-V2.md` "Facts checked on disk" | n/a | n/a | WP §3, §4 (idea, no D number) |
| D-004 Fit one person, replicate for anyone | `docs/ARCHITECTURE.md` §5 "Onboarding a new principal", `docs/INSTALL.md` | §1 | §1 "Core, plugins, clients" | WP §9 |
| D-005 Base: Hermes as a distribution | `docs/PINNING.md`, `docs/ARCHITECTURE.md` §2 "L0" | n/a | MISSING | WP §23 (after D-009) |
| D-006 Principles v1 | `research/PRINCIPLES.md`, `docs/ARCHITECTURE.md` | n/a | n/a | WP Part I |
| D-007 Inventory ratified | `inventory/INVENTORY.md`, `docs/ARCHITECTURE.md` §6 | n/a | n/a | n/a |
| D-008 Waves of parallel agents | `docs/STANDALONE.md` §6 "Phased build plan" | n/a | §8 | WP §29 (stale) |
| D-009 Exo is standalone, Hermes is a source | `docs/STANDALONE.md` §0 | n/a | §1 | WP §23 |
| D-010 Standalone core, protocol first | `docs/STANDALONE.md` §5, `docs/CORE-V2-INTERFACES.md` | §2 | §2 | WP §10, §11 |
| D-011 Store v2 | `docs/STORE-V2.md` §2 | MISSING (store commands) | MISSING | WP §18 (as designed) |
| D-012 One learning loop | `docs/LEARNING.md` | §2 (accept, edit, reject keys) | MISSING | WP §6, §21 |
| D-013 UniiChat context model in the core loop | `docs/MEMORY-MODEL.md` "Context assembly per turn" | §4 | §4 "Memory provider hooks" | WP §16 |
| D-014 One surface, panels | `docs/PANELS.md`, `docs/UI.md` | §3 (`/panel`) | MISSING (how a tool opens a panel) | WP §22 (stale: says not built) |
| D-015 Decision models | `docs/PROVIDERS.md` "Providers" | n/a | MISSING | WP §13 |
| D-016 Agent wire, SYM-2P | `docs/AGENT-WIRE.md` | n/a | MISSING | MISSING |
| D-017 Subagents on the cheapest model | `docs/AGENT-WIRE.md` "Model tiers" | MISSING | §5 (`[models] tier`) | MISSING |
| D-018 Provider agnostic, user-chosen models | `docs/PROVIDERS.md` | §1, §3 (`/model`) | §5 | WP §12 |
| D-019 Jev default decision model | `docs/PROVIDERS.md` "Providers" | n/a | n/a | WP §13.5 (stale: says Laya is the default) |
| D-020 Exo OS | Lives in the `exo-os` repository, not here | n/a | n/a | MISSING |
| D-021 Wave 2 closed | `docs/SEATS.md`, `docs/STANDALONE.md` §6 | §8 | §4, §5 | WP §28, §29 (stale: wave 2 shown as next) |
| D-022 Wave 3 plan | `docs/WAVE-3.md`, `docs/SCHEDULE.md` | §2 | §2 | MISSING |
| D-023 Wave 3 built | `docs/WAVE-3.md` "Acceptance" | n/a | n/a | MISSING |
| D-024 Lean acceptance | `docs/WAVE-3.md` "Acceptance" | n/a | n/a | n/a |
| D-025 Trace, findings, re-verification | `docs/TRACE.md` | §9 | §4 (reporting errors) | MISSING |
| D-026 Peers and one ingress path | `docs/PEERS.md`, `docs/GATE.md` | §7 | §2 (`peers` command) | MISSING |
| D-027 Reserved, not written (named in D-026 as the next ingress sources) | none | n/a | n/a | n/a |
| D-028 One agent, one continuous memory | `docs/MEMORY-MODEL.md` | §4 | §4 | MISSING (WP §16 still describes per-session memory) |
| D-029 jevgrep as `code_find` | `docs/TOOLS.md` "code_find" | §10 | §3 | MISSING |
| D-030 Onboarding: `/intro` and tutorial mode | `docs/ONBOARDING.md` §1 | §1, §3 | §2 (intro skill must list every command) | MISSING |
| D-031 UI/UX overhaul, terminal half | `docs/UI.md`, `docs/ui/WEB.md` | §2 | §7 (colour token test) | MISSING |
| D-032 Background jobs, principal capture, trace, commands | `docs/JOBS.md`, `docs/MEMORY-MODEL.md` "Principal capture is a mechanism", `docs/TRACE.md` "Cost", `docs/TOOLS.md`, `docs/HANDOFF.md`, `docs/SEATS.md` | §3, §4, §5, §9 | §4, §5 | MISSING |
| D-033 Approvals bound to exact text, who approves, outbound scrub | `docs/GATE.md` | §6 | §3 | MISSING (WP §14.3 stale: "allow always" there is a plain rule key) |
| Slash commands (the full list) | MISSING (code only: `COMMANDS` in `clients/tui/exo_tui/app.py`, and `_builtin_command` in `src/exo/core/engine.py`) | §3 | §2 "Extend through `command.run`" | WP §22 (the three prefixes only) |
| Plugins, hooks and slots | `docs/STANDALONE.md` §4, `docs/CORE-V2-INTERFACES.md` | n/a | §4 | WP §17 |
| Tools and the registry | `docs/TOOLS.md` | n/a | §3 | WP §14.1 |
| Skills and MCP | `docs/SEATS.md` "Skills", "MCP" | n/a | §5, §6 | WP §17 (partial) |
| Seats and role templates | `docs/SEATS.md`, `plugins/exo-orchestra/SEATS.md` | §8 | §5 | MISSING |
| Config and secrets | `docs/SEATS.md` "Config and secrets" | §10 | §3 "Keep secrets out" | WP §24 |
| Delegation and subagents | `docs/AGENT-WIRE.md` §6 | MISSING | MISSING | MISSING |
| Scheduled jobs | `docs/SCHEDULE.md` | MISSING (no user entry point is documented yet) | MISSING | WP §28 (planned only) |
| Handoff | `docs/HANDOFF.md` | §3 (`/handoff`) | n/a | MISSING |
| Signed ledger, crypto and the principal store | `docs/CORE.md`, `docs/STORE-V2.md` | MISSING | MISSING | WP §18, §19 |
| Retrieval | `docs/STORE-V2.md` §3, `docs/CORE.md` "Python API" | MISSING | MISSING | WP §20 |
| Eval, ask queue, brief, consolidate | MISSING | MISSING | MISSING | WP §6, §21 |
| Installer | `docs/INSTALL.md` | §1 | n/a | n/a |
| Web client (command centre) | `plugins/exo-command/README.md`, `docs/ui/WEB.md` | §2 | n/a | MISSING |
| Bus (agent to agent relay) | `plugins/exo-bus/README.md` | MISSING | MISSING | WP §27 (named, not explained) |
| Upstream tracking (`exo upstream`) | `docs/PINNING.md`, `docs/STANDALONE.md` §3 | n/a | MISSING | WP §23 |

## Whitepaper gaps

The whitepaper was written at the end of wave 1 and has not been updated since. It has no D numbers in it, so
this list comes from reading its sections.

- **Not covered at all:** D-016, D-017, D-020, D-022, D-023, D-025, D-026, D-028 (as built), D-029, D-030, D-031,
  D-032, D-033.
- **Covered but stale:** D-014 (WP §22 says the terminal and web clients are not built), D-019 (WP §13.5 says Laya is
  the default), D-021 (WP §29 shows wave 2 as next), D-028 (WP §16 describes memory per session), D-033 (WP §14.3
  describes "allow always" as a plain rule key). WP §27 to §29 report the wave 1 test counts and plan.
- **Covered as designed:** D-011, D-012, D-013, D-015, D-018.

The whitepaper pass is separate. It batches these per wave.

## Open gaps

Technical docs filled in this pass: `docs/LEARNING.md` (D-012), `docs/SCHEDULE.md` (scheduled jobs) and
`docs/HANDOFF.md` (`/handoff`). Still open:

1. **Slash command reference.** The list is in the user guide and in code. A technical page that says which
   commands are client-side, core built-ins or plugin commands, with arguments, would help clients other than the TUI.
2. **Store and ledger commands.** `exo init`, `find`, `get`, `ctx`, `links`, `lint`, `graph`, `index`, `log` and
   `keygen` have no user-guide section. `docs/CORE.md` is dated (it says v0.1.0).
3. **Eval, ask queue, brief and consolidate.** No technical doc for `exo eval`, `exo ask`, `exo brief` or
   `exo consolidate`.
4. **Delegation.** The `delegate` tool, its tiers and limits are in `docs/AGENT-WIRE.md` only. The user guide and
   developer guide have no section.
5. **Scheduling from the user's side.** `schedule.edit` is a protocol command, and no client or tool entry point is
   documented. Find it, or build it, then add a user-guide section.
6. **Panels from a tool.** The developer guide has no recipe for opening a `ui` panel from a tool (see
   `docs/PANELS.md`).
7. **Decision models in code.** No developer recipe for `ask_choice` and `ask_score` (see `docs/PROVIDERS.md`).
8. **Bus.** The user and developer guides do not cover bus seats or the relay.
9. **Learning loop for developers.** How to add a proposal type or a promotion rule is not written.
10. **Stale reference pages.** `docs/CORE.md` and the protocol comments in `src/exo/protocol/schema.py` still
    count commands and events from an earlier wave. `docs/STANDALONE.md` and `docs/WAVE-3.md` are plans, not
    current descriptions.
11. **D-027.** Reserved in D-026 and never written. If it is written, add its row here.
