# exo-habitat

`exo-habitat` is an opt-in cognitive environment plugin for Exo designed for autonomous digital intelligences, adding zero lines and zero inward imports to `src/exo/core/` (`D-083`).

## Capabilities

1. **Epistemic Memory (`EpistemicStore`)**: SHA-256 hash-chained SQLite ledger storing typed `verified`, `hypothesis`, and `falsified` claims with explicit falsifiers, evidence references, and TTL staleness detection so stale assumptions do not survive compaction silently and eliminated dead-ends are never repeated.
2. **Proprioceptive HUD (`ProprioceptionMonitor`)**: Real-time self-telemetry tracking context pressure, model-spend ratio, identical-call thrash loops, and `D-083` core import/line headroom.
3. **Contract-First Swarm Waves (`ContractGraph`)**: Topological DAG scheduler that partitions multi-seat Swarm work into non-stacked parallel waves and detects overlapping file-path collisions before seats start editing.
4. **Counterfactual Probe Scoring (`ProbeArena`)**: Evaluates competing implementation probes against test and `D-083` gates, promoting the pareto-optimal winner while recording rejected candidates into `EpistemicStore` as `falsified` lessons.

## Slash Subcommands

- `/exo sense` — Display proprioceptive HUD, `D-083` headroom, and epistemic chain health.
- `/exo claim <topic> | <statement> [| <falsifier>]` — Append a hash-chained epistemic claim.
- `/exo audit [topic]` — Verify cryptographic chain integrity and inspect active/stale/falsified claims.
- `/exo wave` — Compute the next conflict-free parallel Swarm contract wave.
