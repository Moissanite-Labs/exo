# Bend ideas across Project Exo and Moissanite Labs

2026-11-03. **Status: architecture proposal and bounded experiments, not an adopted stack or a claim of formal verification.**

## Proposed direction

Bend 2 is a programming language with dependent types, a proof checker and a parallel runtime for CPUs and GPUs. This proposal explores its ideas for Exo and related projects. The principal decides whether to adopt it.

The proposed approach is to apply Bend's ideas to how the ecosystem is built, not only for Proven Commons: explicit machine-checkable laws, a separation between requirements and generated implementation, pure computation behind effectful adapters, explicit ownership, and parallel computation where independence is real.

Use Bend 2 itself where an experiment demonstrates value. Keep Python/TypeScript/native clients where they already do the job. A requirement can be language-neutral. Its implementation need not be.

This extends the [Proven Commons assessment](https://github.com/Proven-Commons/mission/pull/31), rather than moving Exo work into that programme. Shared core work stays in Exo. Desktop/OS clients consume the canonical implementation under [the repository workflow](../docs/REPOSITORY-WORKFLOW.md). No exo-cli fork or parallel desktop implementation is proposed.

## Evidence and existing work

Inspected Exo main: [4fb581675aedd965464884e472131f2dce388e17](https://github.com/Moissanite-Labs/exo/tree/4fb581675aedd965464884e472131f2dce388e17).

- [Core interfaces](../docs/CORE-V2-INTERFACES.md) already define typed commands/events, strict input decoding, forward-compatible reads and interruption behavior. [Schema](../src/exo/protocol/schema.py) and [types](../src/exo/core/types.py) are implementation anchors, not a blank slate.
- [Exo #497](https://github.com/Moissanite-Labs/exo/issues/497) already tracks versioned workflow DAGs, bounded concurrency, cancellation, restart, plugin contracts and conformance. It remains open. Its branch documents `docs/HARNESS-LANDSCAPE.md` and `docs/HERMES-PLUGIN-OPPORTUNITIES.md` are not on inspected main. The roadmap is not fully implemented.
- [Exo #252](https://github.com/Moissanite-Labs/exo/issues/252) already requires measured performance and expressly rejects parallel tool calls because order preserves interruption and spend semantics. Current [Loop._tools](../src/exo/core/loop.py) executes calls serially.
- [Synbios](https://github.com/Moissanite-Labs/synbios/blob/69daee09295e341ff8deb856ec32cce0a2b0d2f1/README.md) already ties test evidence to an exact artifact, makes it stale on a new artifact, and requires independent review.

  Its [proof-evidence RFC](https://github.com/Moissanite-Labs/synbios/blob/69daee09295e341ff8deb856ec32cce0a2b0d2f1/docs/proof-evidence-rfc.md) already proposes artifact and law hashes, stale-evidence rules and independent review of changed laws. Proof records remain advisory.
- [Synmerit](https://github.com/Moissanite-Labs/synmerit/blob/5b5f4f9eaffe45a718601744366b8ac507ffc4b6/README.md) currently documents the Koinos reference protocol. Its [open #14](https://github.com/Moissanite-Labs/synmerit/issues/14) already proposes language-neutral negative vectors, expected derived state and stable errors for independent implementations.

  Reuse that issue and the Exofab consumer, rather than invent a second conformance scheme.
- [ABIOS](https://github.com/Moissanite-Labs/abios/blob/21154fdc2fe925047560134fdedea5d0c073ca25/README.md) currently retains SYM-2P naming and normative packet/validator material. Treat names and version boundaries as observed, not as a completed ecosystem rename.
- [Exo OS](https://github.com/Moissanite-Labs/exo-os/blob/bf489013a217b99aa8a0d238fbfb398b78354685/README.md) explicitly pins Exo rather than copying it and distinguishes source tests from unrun native acceptance. A proof model cannot substitute for that acceptance.

## Architecture mapping

| Bend idea | Ecosystem application | First boundary |
| --- | --- | --- |
| Laws separate from implementation/proofs | Reviewed, versioned rules beside Exo modules. Agents supply code and evidence without silently weakening the requirement | Contract IDs and requirement hashes linked to existing tests |
| Dependent types and explicit invariants | Make invalid transitions explicit in protocol/workflow models. Distinguish pending, completed, interrupted and unknown outcomes | Small pure model, not a replacement for the engine |
| Purity and explicit effects | Separate deterministic budget selection, decoding and event reduction from files, network, model calls and execution | Pure input/output fixtures and adapter tests |
| Affine ownership | Model one active owner and non-reusable completion/approval capabilities | Transition model with durable IDs and revocation assumptions |
| Automatic parallel computation | Evaluate independent immutable shards, then deterministically combine results | Synthetic compute-only benchmark, not concurrent tool side effects |
| Proof-carrying changes | Synbios evidence can carry law-set digest, source digest, checker version and checker output | Existing RFC mapping and untrusted-proof validation design |
| Reusable formal components | Proven Commons supplies reviewed component specifications and proofs to consumers | Explicit conversion/FFI boundary, licenses and runtime trust listed |

Dependent types let a type depend on a value, so a type can express a requirement such as a length or bound. Affine ownership permits a value to be used at most once. Proof-carrying changes attach checkable evidence that an implementation satisfies named laws.

An affine source-language value is not distributed at-most-once execution. Process restarts, copied messages and retries still require durable state and reconciliation. Likewise, a proof file is untrusted input and can consume resources: checking it requires a bounded isolated design before any automated forge execution.

## Pilot A: a budget-selection kernel for Exo

Start with the actual [retrieval seam](../src/exo/retrieve.py) and [PrincipalStore.ctx](../src/exo/store.py), not a toy unrelated to the product.

The current implementation estimates tokens from character count, reserves a header, tries each ranked full node, and falls back to its summary. Therefore **do not claim a proof about model-token limits**. Very small budgets can be smaller than the header itself. Define this case explicitly rather than assume the current API already enforces every proposed law.

Proposed experimental input: an ordered list of synthetic IDs with natural-number full/summary costs, a header cost and a natural-number budget. Output: selected IDs, full/summary choices, total accounted cost, or an explicit insufficient-budget result. Ranking, text construction, tokenizer accuracy and privacy filtering remain adapter responsibilities.

Proposed laws:
1. Successful output's accounted cost, including header and separators, is within budget.
2. Selected IDs come only from the input and retain its order. Define duplicate-ID behavior explicitly.
3. A selected full or summary representation uses the corresponding declared cost.
4. The deterministic full-first/summary-fallback policy is followed, so an always-empty implementation cannot satisfy all claims.
5. Budget below mandatory overhead returns the specified failure, with no underflow.

Write and independently review the language-neutral specification first. Implement a Bend 2 experiment beside a small Python reference. Prove its laws with ordinary checking plus `--verdict`, review emitted kernel terms, and compare identical fixtures.

Include zero/exact/one-short budgets, duplicate IDs, zero-length candidates and very large natural costs. Character accounting and actual rendered output need separate adapter tests.

Do not route live personal context through the experiment. No production behavior changes in this pilot.

Planning estimate: 3–5 engineer-days plus review. Exit with a reproducible comparison and an adopt/watch/pass decision. Stop if the proof needs unsafe/foreign assumptions, the behavior cannot be specified without changing product requirements, or integration overhead exceeds its benefit.

## Pilot B: proof-linked change evidence, not a new forge

Build on the [existing Synbios proof-evidence RFC](https://github.com/Moissanite-Labs/synbios/blob/69daee09295e341ff8deb856ec32cce0a2b0d2f1/docs/proof-evidence-rfc.md) in its repository, with its owner. The RFC already defines the proposed artifact bindings, stale-evidence rules and independent review of law changes. Reuse those requirements rather than propose a second schema.

Pilot B adds a consumer experiment for Pilot A's budget-selection result. Map its reviewed laws, source, proof and toolchain identities into the RFC's evidence bundle. Test whether a consumer can distinguish a current result from stale, failed and unavailable evidence. This complements the RFC's merge-eligibility model without adding merge authority.

Prototype with synthetic records and a fake checker. Cover changed source or laws, mismatched artifacts, missing checker identity, resource failure and a failed proof followed by a pass. Reuse the transport/state vectors proposed in Synmerit #14 once they exist and apply. Evidence stays advisory and cannot weaken passing-test or independent-review gates.

A real checker runner is a later, separately reviewed boundary with no credentials/network, resource limits, reproducible inputs and malicious-proof tests.

A model proof does not prove the Python/JavaScript implementation unless their correspondence is also established. Planning estimate: 2–4 days for the consumer mapping and local fixture tests, excluding a secure runner.

## Later: deterministic parallel kernels and lifecycle proofs

Use #497's existing workflow track for cancellation/restart models. Prove only named transitions. External exactly-once effects remain outside a pure model.

Use the existing [measurement baseline](../docs/OPTIMIZATION-BASELINE.md) and [baseline script](../scripts/optimization_baseline.py), the first #266 slice of #252, before selecting a compute kernel. Candidate work is independent integer/count reductions over immutable synthetic records, with deterministic ordering and serial equivalence.

Do not parallelize ordered tool calls, model-spend commits, approval resolution, ledger appends or OS actions. Do not rewrite BM25 in F32 and call it equivalent: Bend's current floating-point proof limitations and different reduction order need explicit treatment.

Include startup, serialization and memory costs in any speed comparison. GPU availability is a prerequisite, not a reason to acquire hardware or spend money.

## Trust, licensing and adoption gates

Primary Bend evidence:

- [README](https://github.com/bendlang/bend/blob/ea66e0044fc1632c28e35b7b0f0d2263d77e3449/README.md)
- [guide](https://github.com/bendlang/bend/blob/ea66e0044fc1632c28e35b7b0f0d2263d77e3449/guide/GUIDE.md)
- [kernel source](https://github.com/bendlang/bend/blob/ea66e0044fc1632c28e35b7b0f0d2263d77e3449/bend2/bendtt.lean)
- [Apache-2.0 license](https://github.com/bendlang/bend/blob/ea66e0044fc1632c28e35b7b0f0d2263d77e3449/LICENSE).

Bend 2 is not compatible with old Bend or HVM. The ordinary checker, source-to-kernel translation and executable backend are separate trust boundaries. A proved kernel does not prove an application's natural-language intent.

Bend's small library, explicit types, manual proof effort, compilation costs and unsupported numeric cases count against integration value. Reuse ideas separately from copying code.

Synmerit's inspected README leaves licensing undecided. Linked protocols and source are evidence, not permission to redistribute implementation code. Inspect each component's current license and dependencies before reuse.

Adopt a component only after independent specification review, useful-behavior and negative obligations, reproducible checker evidence, tests at every conversion boundary, measured end-to-end value, a named maintainer and a fallback. Formal verification complements adversarial tests and native integration checks. No global 'verified Exo' claim follows from a small proved component.

This proposal adds documentation only. It does not install Bend, run benchmarks, change the gate, create credentials, migrate the ledger, alter OS policy, duplicate a client, or adopt a runtime.
