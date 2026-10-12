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
| D-004 Fit one person, replicate for anyone | `docs/ARCHITECTURE.md` §5 "Onboarding a new principal", `docs/INSTALL.md` | §1 | §1 "Core, plugins, clients" | WP §10 |
| D-005 Base: Hermes as a distribution | `docs/PINNING.md`, `docs/ARCHITECTURE.md` §2 "L0" | n/a | MISSING | WP §25 (after D-009) |
| D-006 Principles v1 | `research/PRINCIPLES.md`, `docs/ARCHITECTURE.md` | n/a | n/a | WP Part I |
| D-007 Inventory ratified | `docs/ARCHITECTURE.md` §6 (inventory kept outside the repo) | n/a | n/a | n/a |
| D-008 Waves of parallel agents | `docs/STANDALONE.md` §6 "Phased build plan" | n/a | §8 | WP §31 |
| D-009 Exo is standalone, Hermes is a source | `docs/STANDALONE.md` §0 | n/a | §1 | WP §25 |
| D-010 Standalone core, protocol first | `docs/STANDALONE.md` §5, `docs/CORE-V2-INTERFACES.md` | §2 | §2 | WP §11, §12 |
| D-011 Store v2 | `docs/STORE-V2.md` §2 | §11 (store commands) | MISSING | WP §19 (as designed) |
| D-012 One learning loop | `docs/LEARNING.md` | §2 (accept, edit, reject keys) | §9 "Work on the learning loop" | WP §6, §22 |
| D-013 UniiChat context model in the core loop | `docs/MEMORY-MODEL.md` "Context assembly per turn" | §4 | §4 "Memory provider hooks" | WP §17 |
| D-014 One surface, panels | `docs/PANELS.md`, `docs/UI.md` | §3 (`/panel`) | §9 "Open a panel from a tool" | WP §8, §24 |
| D-015 Decision models | `docs/PROVIDERS.md` "Providers" | n/a | §9 "Call a decision model" | WP §14 |
| D-016 Agent wire, SYM-2P | `docs/AGENT-WIRE.md` | n/a | MISSING | WP §16.1 |
| D-017 Subagents on the cheapest model | `docs/AGENT-WIRE.md` "Model tiers" | MISSING | §5 (`[models] tier`) | WP §8, §13.2, §16.1 |
| D-018 Provider agnostic, user-chosen models | `docs/PROVIDERS.md` | §1, §3 (`/model`) | §5 | WP §13 |
| D-019 Jev default decision model | `docs/PROVIDERS.md` "Providers" | n/a | n/a | WP §14.5 |
| D-020 Exo OS | Lives in the `exo-os` repository, not here | n/a | n/a | WP §27 (design branch, one paragraph) |
| D-021 Wave 2 closed | `docs/SEATS.md`, `docs/STANDALONE.md` §6 | §8 | §4, §5 | WP §18, §29, §31 |
| D-022 Wave 3 plan | `docs/WAVE-3.md`, `docs/SCHEDULE.md` | §2 | §2 | WP §22, §24, §31, §32 |
| D-023 Wave 3 built | `docs/WAVE-3.md` "Acceptance" | n/a | n/a | WP §22, §24, §29, §31 |
| D-024 Lean acceptance | `docs/WAVE-3.md` "Acceptance" | n/a | n/a | WP §31 |
| D-025 Trace, findings, re-verification | `docs/TRACE.md` | §9 | §4 (reporting errors) | WP §9, §23 |
| D-026 Peers and one ingress path | `docs/PEERS.md`, `docs/GATE.md` | §7 | §2 (`peers` command) | WP §7, §8, §12.7 |
| D-027 Reserved, not written (named in D-026 as the next ingress sources) | none | n/a | n/a | n/a |
| D-028 One agent, one continuous memory | `docs/MEMORY-MODEL.md` | §4 | §4 | WP §5, §17 |
| D-029 jevgrep as `code_find` | `docs/TOOLS.md` "code_find" | §10 | §3 | WP §15.1 |
| D-030 Onboarding: `/intro` and tutorial mode | `docs/ONBOARDING.md` §1 | §1, §3 | §2 (intro skill must list every command) | WP §24 "Learning the tool" |
| D-031 UI/UX overhaul, terminal half | `docs/UI.md`, `docs/ui/WEB.md` | §2 | §7 (colour token test) | WP §24 "Instruments, not chrome" |
| D-032 Background jobs, principal capture, trace, commands | `docs/JOBS.md`, `docs/MEMORY-MODEL.md` "Principal capture is a mechanism", `docs/TRACE.md` "Cost", `docs/TOOLS.md`, `docs/HANDOFF.md`, `docs/SEATS.md` | §3, §4, §5, §9 | §4, §5 | WP §5, §13.5, §15.5, §17.4, §23, §24 (`/handoff`) |
| D-033 Approvals bound to exact text, who approves, outbound scrub | `docs/GATE.md` | §6 | §3 | WP §7, §15.3, §15.4, §26 |
| D-034 Providers setup: one engine, CLI, TUI panel, web view | `docs/PROVIDERS.md` "Set up with the app or the TUI", `docs/COMMANDS.md`, `docs/UI.md`, `docs/ui/WEB.md` | §1, §3, §10 | MISSING | MISSING (written after v0.2) |
| Slash commands (the full list) | `docs/COMMANDS.md` Part 1 | §3 | §2 "Extend through `command.run`" | WP §24 (the three prefixes only) |
| CLI commands (`exo ...`) | `docs/COMMANDS.md` Part 2 | §9 (trace), §11 (store and ledger) | §2 "Extend through `command.run` built-ins" | n/a |
| Plugins, hooks and slots | `docs/STANDALONE.md` §4, `docs/CORE-V2-INTERFACES.md` | n/a | §4 | WP §18 |
| Tools and the registry | `docs/TOOLS.md` | n/a | §3 | WP §15.1 |
| Skills and MCP | `docs/SEATS.md` "Skills", "MCP" | n/a | §5, §6 | WP §18 (partial) |
| Seats and role templates | `docs/SEATS.md`, `plugins/exo-orchestra/SEATS.md` | §8 | §5 | WP §5, §18 (brief) |
| Config and secrets | `docs/SEATS.md` "Config and secrets" | §10 | §3 "Keep secrets out" | WP §26 |
| Delegation and subagents | `docs/AGENT-WIRE.md` §6 | MISSING | MISSING | WP §16.1 |
| Scheduled jobs | `docs/SCHEDULE.md` | MISSING (no user entry point is documented yet) | MISSING | WP §13.2, §24, §29 (brief) |
| Handoff | `docs/HANDOFF.md` | §3 (`/handoff`) | n/a | WP §24 |
| Signed ledger, crypto and the principal store | `docs/CORE.md`, `docs/STORE-V2.md` | §11 | MISSING | WP §19, §20 |
| Retrieval | `docs/STORE-V2.md` §3, `docs/CORE.md` "Python API" | §11 (`exo find`, `exo ctx`) | MISSING | WP §21 |
| Eval, ask queue, brief, consolidate | `docs/TOOLS-CLI.md` | MISSING (a pointer in §11 only) | §9 "Work on the learning loop" (consolidate and proposals) | WP §6, §22 |
| D-035 Typing `exo` opens Exo: bare command, installer on PATH, `exo update` | `docs/INSTALL.md` "The `exo` command", `docs/COMMANDS.md` Part 2 "Running Exo" | §1 | n/a | n/a |
| D-036 One shared core: `exo web` finds or starts it | `docs/ui/WEB.md` "Connecting to the core", `docs/USER-GUIDE.md` §2, `docs/COMMANDS.md` | §2 | n/a | n/a |
| D-038 Gate default ask: shell allowlist, store is credentials, policy edits only tighten | `docs/GATE.md` "Shell commands: an allowlist", "The store is credentials", "Policy changes during a session", `docs/COMMANDS.md` | §6 | n/a | MISSING (written after v0.2) |
| D-039 Approval identity from the connection, web token on every route, one secret scrub | `docs/GATE.md` "Who answered: the connection, not the body", `docs/ui/WEB.md` "Opening the page and the page token", `docs/TOOLS.md` "Environment of bash and jobs", `docs/SEATS.md` | §2, §6, §10 | §2 | WP §7 |
| D-037 Completion and guidance in the composer | `docs/COMMANDS.md` "Completion and guidance", `docs/UI.md`, `docs/ui/WEB.md` "The composer" | §3 | n/a | n/a |
| D-042 Repo hygiene, privacy and licensing | `docs/DEVELOPER-GUIDE.md` §7 "CI, lint, lockfile and the privacy deny-list", `docs/HANDOFF.md` | n/a | §7 | n/a |
| Web client (command centre) | `plugins/exo-command/README.md`, `docs/ui/WEB.md` | §2 | n/a | WP §24 |
| Bus (agent to agent relay) | `plugins/exo-bus/README.md` | MISSING | §9 "Use the bus" | WP §29 (named, not explained) |
| Upstream tracking (`exo upstream`) | `docs/PINNING.md`, `docs/STANDALONE.md` §3 | n/a | MISSING | WP §25 |

## Whitepaper gaps

The whitepaper was brought up to date through D-033 in version 0.2. It has no D numbers in it, so the
whitepaper column above comes from reading its sections. Section numbers are those of version 0.2.

- **Not covered:** none of D-001 to D-033 that need whitepaper coverage.
- **Not covered yet:** D-034 (providers setup), which landed after version 0.2.
- **Covered as designed, not built:** D-011 (store version 2, WP §19), the staged retrieval pipeline (WP §21),
  and the unbuilt parts of D-015 (calibration, `decision` ledger entries, exo-decide, WP §14).

The whitepaper pass is separate. It batches these per wave.

## Open gaps

Technical docs filled so far: `docs/LEARNING.md` (D-012), `docs/SCHEDULE.md` (scheduled jobs), `docs/HANDOFF.md`
(`/handoff`), `docs/COMMANDS.md` (every slash command and `exo` subcommand) and `docs/TOOLS-CLI.md` (`exo eval`,
`ask`, `brief`, `consolidate`). `docs/CORE.md` and the protocol comments in `src/exo/protocol/schema.py` now state the
real counts, and `tests/docs/test_docs_counts.py` keeps them honest. Still open:

1. **Delegation.** The `delegate` tool, its tiers and limits are in `docs/AGENT-WIRE.md` only. The user guide and
   developer guide have no section.
2. **Scheduling from the user's side.** `schedule.edit` is a protocol command, and no client or tool entry point is
   documented. Find it, or build it, then add a user-guide section.
3. **Bus for users.** The developer guide has a bus recipe (§9). The user guide does not cover bus seats or the relay.
4. **Eval, ask queue, brief and consolidate for users.** Only a technical page exists. The user guide has a pointer.
5. **Plans that read as descriptions.** `docs/STANDALONE.md` and `docs/WAVE-3.md` are plans, not current
   descriptions. `docs/CORE-V2-INTERFACES.md` still says 25 commands and 21 events, which is the wave 2 shape.
6. **D-027.** Reserved in D-026 and never written. If it is written, add its row here.
| D-041 Audit correctness fixes (partial text on error, truncated streams, undo and failed summaries, scheduler, bus directive kind, MCP names and cap) | `docs/CORE-V2-INTERFACES.md`, `docs/MEMORY-MODEL.md`, `docs/SCHEDULE.md`, `docs/SEATS.md` "MCP", `docs/TOOLS.md`, `docs/AGENT-WIRE.md` | n/a | n/a | WP §17.3 |
| D-043 Esc never stops a turn; ctrl+c and `/stop` do; approval card keeps out of typing; failover off the event loop | `docs/USER-GUIDE.md` "Approvals", `docs/UI.md`, `docs/COMMANDS.md`, `docs/ui/WEB.md`, `skills/exo-intro/SKILL.md` | n/a | n/a | WP §12 |
