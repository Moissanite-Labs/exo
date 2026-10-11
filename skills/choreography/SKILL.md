---
name: choreography
description: "Use when a multi-agent team needs its shared working contracts: the self-improving flywheel, anti-loop discipline, open-source contribution, and local preprocessing."
version: 1.0.0
author: Protean Team, generalized for Exo
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Orchestration, Contracts, Flywheel, Anti-loop, Contribution]
    related_skills: [operating-doctrine, control-plane, council]
---

# Choreography: how the team works together

Four short contracts every role follows. Each one is a reference file. Load only the one the task
needs.

| Contract | File | Use when |
|---|---|---|
| Self-improving flywheel | `references/self-improving-flywheel.md` | a failure happened and must become one rule with one home and one check |
| Anti-loop discipline | `references/anti-loop-discipline.md` | an operation is about to repeat; name the new information it returns or stop |
| Open-source contribution | `references/open-source-contribution.md` | work touches a repository the team depends on |
| Local preprocessing | `references/local-preprocessing.md` | cheap local steps (redact, summarize, route) may run before text leaves the machine |

The contracts are generic. Thresholds, owner rulings and the names of the principal's agents live
in the principal store under `policies/` and `agents/`.
