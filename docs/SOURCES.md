# Sources, adaptations and evidence

This register explains where Exo's mechanisms come from, what Exo changed and where to inspect the result.
It is a first set of verified examples, not a complete dependency inventory or a claim of novelty.

Source snapshot: main `8c9d1676a31b2c6980625c29c416aff976fae9ce`, inspected 2026-11-03. The records below are
based on that repository's code, notices and technical references. An upstream revision is the version Exo
records as inspected or reconciled, not a claim that Exo tracks upstream's latest release.

[PROVENANCE.toml](../PROVENANCE.toml) remains the machine-readable authority for Hermes-derived modules.
This page explains it rather than maintaining a second list of module revisions. Dependency declarations
remain in [pyproject.toml](../pyproject.toml). The [docs map](DOCS-MAP.md) remains the one document index.

## How to read a record

**Relationship:** copied/modified code, rewritten implementation of a method, external tool integration or
inspiration only. These categories cannot substitute for each other. A paper citation does not license copied
code, and a dependency's license does not establish that Exo invented its method.

**State:** use the existing [site status vocabulary](SITE-STATUS.md): built, progress, designed, exploring.
Keep the concrete implementation and deployment evidence beside it. Built means tested and in daily use on
main. Source present on main alone does not establish that state. A reviewed PR, a main merge, a desktop sync
and a live installation are separate facts. The records below describe source presence and explicit limits
rather than assigning unsupported deployment badges.

## Hermes Agent: selected code, smaller boundaries

- **Source and credit:** [Nous Research's Hermes Agent](https://github.com/NousResearch/hermes-agent), MIT.
  Retained notice: [hermes-agent-MIT.txt](../LICENSES/hermes-agent-MIT.txt).
- **Relationship:** selective lifts and ports. The per-module paths, revisions, reconciliation dates and
  changes are in [PROVENANCE.toml](../PROVENANCE.toml). Some records use abbreviated revisions. Do not silently
  upgrade them to an unverified full hash or newer upstream version.
- **Adaptation:** memory/context slot bases are combined into Exo's slot interface. Provider conversions
  retain selected edge-case handling while Exo supplies its streaming machinery. Routing is rewritten around
  roles, fallback and cooldowns. The context module lifts summarizer prompt text, not the compressor or the
  summary-tree implementation. Delegation reuses specific prompt wording, blocked-tool names and defaults,
  not the whole child runtime.
- **Evidence:** [slot implementation](../src/exo/core/slots.py), [router](../src/exo/core/models/router.py),
  [context](../src/exo/core/context.py), [delegation](../src/exo/core/agents/delegate.py) and
  [provenance checks](../tests/upstream/test_provenance.py).
- **Limit:** this record does not make Exo a Hermes distribution or establish equivalence to its features.
  [STANDALONE.md](STANDALONE.md) retains historical planning measurements. Use current code and the developer
  guide for current architecture, not that proposal's old import counts.

## OptChat and UniiChat: credit the idea and the intermediate code

- **Method credit:** Victor Taelin and Higher Order Company, [OptChat/UniiChat](https://uniichat.com), as credited
  in the [README](../README.md). Exo's core reimplements the continuous log and summary-tree ideas.
- **Code lineage:** the plugin's retained [episodic layer](../plugins/exo-memory/exo_memory/episodic/tree.py)
  separately declares a vendor origin of `aska-digital/optchat-memory` v1.0.0, revision `8682354`, MIT,
  copyright 2026 aska-digital. Its [license](../plugins/exo-memory/exo_memory/episodic/LICENSE-optchat) and
  `exo:` annotations are retained. That header is the evidence inspected here. A full upstream revision and
  independent upstream comparison remain to be resolved. No unverified repository link is asserted.
- **Adaptation:** the current core stores the agent log in SQLite, builds the agent summary tree and gives the
  memory provider an [AgentLogView](../src/exo/core/agentlog.py). This lets recall read the core's log rather
  than requiring a second transcript. Private threads, undo, fork copies and context assembly have explicit
  [rules](MEMORY-MODEL.md).
- **Evidence:** [agent log tests](../tests/core/test_agent_log.py) and
  [agent context tests](../tests/core/test_agent_context.py).
- **Limit:** “reimplemented ideas” describes the core. It must not erase the separate vendored plugin code's
  attribution. The record establishes lineage and inspectable behavior, not a measured memory-quality gain.

## jevgrep: a tool integration, not a new search algorithm

- **Source and credit:** [dzhng/jevgrep](https://github.com/dzhng/jevgrep), MIT, credited in the README.
- **Relationship:** Exo wraps the external `jg` program as `code_find`.
- **Adaptation:** [codefind.py](../src/exo/core/tools/builtin/codefind.py) checks runtime availability and wraps
  execution and output. Its npm fallback names `@dzhng/jevgrep@0.8.0`. An existing `jg` on PATH can differ, so
  this is not a claim that every install runs 0.8.0. Exo's [tools reference](TOOLS.md) documents the boundary.
- **State and limit:** wrapper code is on main. Availability depends on the local tool setup. This documentation
  change installs nothing and makes no search-quality or cost comparison.

## Harness²: a bounded method adaptation with review-only output

- **Source and credit:** Google Research, *Harness²: Recursive Agent Harnessing for an Open World*,
  [source revision 1cfe254e8d25b15f37048c3730134686abe60335](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335),
  Apache-2.0. Paper: Xu et al., [arXiv:2610.14224](https://arxiv.org/abs/2610.14224).
- **Relationship:** original Exo code adapting Propose–Probe–Compose. The
  [integration reference](integrations/HARNESS2.md) records the inspected source and method mapping. No upstream
  code, benchmark, dataset or runner is copied or installed by this adapter.
- **Adaptation:** bounded sequential general/domain proposals and composition, hidden evaluator metrics,
  strict improvement and regression checks, typed review proposals and separate held-out CLI evidence.
  The [implementation](../src/exo/improvement/harness2.py) retains the upstream revision in its artifacts.
- **Evidence:** [adapter tests](../tests/eval/test_harness2.py),
  [offline CLI tests](../tests/eval/test_improve_cli.py) and the integration reference's reproduction commands.
- **State and limit:** offline experimental code is on main. Output is not activated, scheduled, imported into
  principal memory or promoted automatically. Synthetic fixture success establishes mechanics only. Upstream
  benchmark results are not Exo results.

## Keep design influences distinct from implementation claims

The README also credits [opencode](https://opencode.ai), [Command Code](https://commandcode.ai),
[Letta Code](https://github.com/letta-ai/letta-code) and Gwern Branwen's
[Guardian Angel](https://gwern.net/guardian-angel) for surface, taste and memory ideas. Those acknowledgments
alone establish neither copied code nor a precise mechanism mapping. Keep them as inspiration until a record
identifies the exact source, adaptation and evidence. Do not infer code authorship from a product resemblance.

## Add a source or another Moissanite Labs project

Keep each project's technical truth beside its implementation. Exo shared behavior lives here. Native desktop
behavior lives in its repository. A Labs-wide catalog can link these records without duplicating their
contracts. This first register covers Exo only. It is not an inventory of every Labs project.

For each new record, include:

1. **Problem and mechanism:** what it does and the constraint it addresses.
2. **Root source:** author/project, canonical public URL, exact inspected revision or paper version, date and
   license/notice evidence. Mark unresolved attribution explicitly.
3. **Relationship and changes:** copied paths, rewritten method or inspiration. What was kept, changed and
   deliberately left out. Preserve existing notices when code is carried forward.
4. **Implementation and state:** owning repository, paths, decision and PR. Distinguish proposed, implemented,
   tested, merged, integrated and deployed facts. Use the site's existing status terms when a badge is needed.
5. **Evidence and limits:** tests, commands, environment, fixture or benchmark, result and what remains untested.
   Keep evidence public and synthetic where possible. Never publish principal stores, private transcripts,
   credentials or private research links merely to make the trail look complete.
6. **Maintenance:** record the author responsible in the PR or issue. Update this record with a source or
   behavior change, link it from the docs map and note guide impact. If a result cannot be reproduced, retain
   that fact rather than silently replacing it with the intended outcome.

The public narrative can then be specific: name the mechanism, credit its source, explain the adaptation and
show the evidence. Claims of first invention, formal security, autonomous improvement or benchmark superiority
need their own support. A sophisticated architecture does not supply that evidence by itself.
