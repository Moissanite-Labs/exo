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
| `docs/ARCHITECTURE.md` | Components, memory and context model, migration path (phase 3) |

## Model keys

Exo reads a provider key from the environment, then (macOS only) the Keychain, then
`~/.config/exo/keys.env` (mode 600). On Linux use the environment or `keys.env`. Steps are in
`docs/PROVIDERS.md`.

Private repository of Aska Digital.
