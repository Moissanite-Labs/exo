# Changelog

All notable changes to Exo. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions
follow [docs/VERSIONING.md](docs/VERSIONING.md): SemVer, with a codename per MAJOR (0.x is egg).

## [Unreleased]

### Added
- Versioning and naming convention (D-056), this changelog, and `exo --version` in the `0.2.0 (egg)` form.
- `/rounds [N|default]` sets this session's tool-round cap (principal only); seat `[loop] max_tool_rounds` and
  `EXO_MAX_TOOL_ROUNDS` set it for the core. A notice shows when a turn nears the cap (D-064).
- At the cap, one last call with tools forced off ends the turn with done, open and next step (#93, D-064).

### Changed
- The tool-round cap per turn is 64, up from 24 (D-064).
- Engine split, first pass (#46, `docs/ENGINE-SPLIT.md`): the diagnostic views (`/context`, `/compact`, `/trace`,
  `/findings`, handoff facts) moved to `exo.diagnostics`, and the engine factory moved to `exo.compose:create`, the
  new `exo serve` default. `exo.core.engine:create` still works. No behaviour change.

### Fixed
- `exo update` on a detached checkout fetches and moves to the tracked remote branch (fast-forward only unless
  `--force`) instead of failing on `git pull`, and rolls back if the reinstall fails (D-064).

## [0.2.0] (egg) - 2026-10-13

First tracked version. It covers everything on main since the 0.1.0 scaffold (D-001 to D-054).

### Added
- A standalone core with a protocol-first design: lean Python engine, JSON-RPC protocol, plugins for memory, context,
  gate, bus, command centre and orchestra (D-009, D-010, D-028).
- One agent with one continuous memory, a v2 store, and a learning loop of review, taste and held-out evaluation
  (D-011, D-012, D-013, D-028).
- A Textual terminal client and a web client, with command completion, a providers panel, onboarding (`/intro`,
  tutorial mode), background jobs, peers, handoffs and trace and findings (D-014, D-025, D-026, D-030 to D-032, D-034, D-037).
- A bare `exo` command, an installer that puts it on PATH, `exo web`, and `exo update` (D-035, D-036).
- Providers: decision models (Jev, Laya), a provider-agnostic router with a circuit breaker, a Muse Code
  subscription through the `muse` CLI, and `code_find` through jevgrep (D-015, D-018, D-019, D-029, D-051).
- A house taste pack and baseline export, then fitting the new user (D-044).
- A compulsory tool executor, durable ledger authority with recoverable projections.
- Agent wire format (SYM-2P) for machines and English for people (D-016), and cheapest-model subagent routing (D-017).
- The public site, whitepaper v0.2, user and developer guides, a docs map, CI, and a staging-first deploy (D-042, D-047).

### Changed
- Exo is a standalone harness. Hermes is a source of lifted parts, not a dependency (D-009).
- The gate asks by default, with a shell allowlist, and the store counts as credentials (D-038).
- Esc no longer stops a turn. Focus follows the user, with a larger composer pane (D-043, D-046).
- Consumer path: local first, desktop apps after the build settles (D-050).

### Security
- Approvals bind to the exact text, to who may approve, and pass an outbound scrub (D-033).
- Approval identity comes from the connection, the web token covers every route, one secret scrub (D-039).
- Message authors, pinned keys and an anchored ledger head show who wrote what (D-040).
- Principal authority covers messages, learning and settings. Ruling citations do not skip mandatory checks (D-052, D-054).
- Privacy deny-list, licences in the wheel, owner-only home files, a warning for keys files that are too open (D-042).

### Fixed
- Eight audit bugs no test caught, among them undo, scheduler, summaries and stream errors (D-041).
- Approval cards no longer outlive their request, and the web stream resumes (D-045).
- Tests are hermetic about terminal preferences such as `NO_COLOR` (D-053).
- Accepted transports now close reliably during server shutdown.

## Site

The public site at exo.askaconsult.com. Versions are `site-vMAJOR.MINOR` (docs/VERSIONING.md).

- **v2.0** (2026-10-13, live): the lattice redesign. The living lattice egg on the home page, the roadmap page with linked nodes, the brand page, credits limited to open source and genuine innovators.
- **v2.0-rc.1** (2026-10-13, staging): the approved redesign candidate, commit `6b8800f`.
- **v1.0** (2026-10-12, live): the first public site, commit `8d54b55`. Archive: tag `site-live-2026-10-13`.
