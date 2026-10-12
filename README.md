# Exo

One system that gives a person everything they need to work well with AI models.

Exo fuses five things that are usually separate:

1. **A guardian angel.** A model of you, built from an append-only record of your work and words, that
   knows how you think, decide and write, and asks when it is unsure.
2. **A harness.** Agents that do the work: an orchestrator, role agents, tools, model routing across
   cloud and local models.
3. **Memory.** A continuing relationship across sessions, platforms and agents, with provenance.
4. **Context and session management.** Budgeted retrieval, compaction that keeps what matters,
   branching, handoffs.
5. **A command centre.** One view of every project, every running agent and task, and every decision
   waiting on you.

## Install

Needs `git` and Python 3.11 or newer, on macOS or Linux (tested on Arch with Python 3.14).

    git clone <exo repo> && cd exo
    scripts/install.sh --dry-run      # print the plan, change nothing
    scripts/install.sh                # install

This is the only install section. Options, what each step does, safety rules and re-runs are in
`docs/INSTALL.md`. To add a model key, see `docs/PROVIDERS.md`.

## First launch and learning Exo

The first time `exo-tui` runs on an `EXO_HOME`, the agent gives you a short orientation (`/intro`) and turns on
tutorial mode: small dim "tip:" lines the first time something comes up (the approval gate, typing to steer a
running turn, a model fallback, and so on). `/intro` runs the orientation again any time. `/tutorial on`, `off`
or `reset` controls the tips. Skip the first-run intro with `exo-tui --no-intro`. Details in
`docs/ONBOARDING.md` (D-030).

Commands you will use in the TUI (`/help` lists all): `/new`, `/sessions`, `/fork`, `/undo`, `/cwd`, `/context`,
`/compact`, `/model`, `/default`, `/usage`, `/trace` (timing and estimated cost per turn), `/findings`, `/peers`,
`/send`, `/handoff [path]` (the agent writes a handoff note for the next session to
`~/Work/handoffs/<seat>-<date>.md`, or to the path you give), `/intro`, `/tutorial`. Exo warns once when a
thread's folder is not inside a git repo; a seat sets its default folder with `[session] cwd` in `seat.toml`.

## Two goals, held together

- **Fit one person as closely as possible.** The first principal is Ahraz Arifuddin.
- **Replicate for anyone.** The core is generic. Everything about a person lives in their own
  principal store, built by an onboarding flow. A fresh install with an empty store runs end to end.

See `DECISIONS.md` (D-004).

## Status

Phase 1 (inventory) and phase 2 (research) in progress. Read along:

| File | What |
|---|---|
| `DECISIONS.md` | Every significant choice, why, and what was rejected |
| `inventory/` | Everything built so far that could fold into Exo, with keep / merge / drop |
| `research/` | Guardian-angel reading notes, principles, base evaluation |
| `docs/DOCS-MAP.md` | Index of all docs: whitepaper, user guide, developer guide, technical docs, and what is missing |
| `docs/ARCHITECTURE.md` | Components, memory and context model, migration path (phase 3) |

## Credits and inspirations

- [dzhng/jevgrep](https://github.com/dzhng/jevgrep) (MIT): the `jg` code search behind the `code_find` tool and
  the `jevgrep` skill (D-029).

Private repository of Aska Digital.
