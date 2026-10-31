# Harness² improvement experiments

Exo can run an opt-in, bounded Propose–Probe–Compose experiment through
`exo.improvement.harness2.run_cycle`. It produces a JSON-serializable, review-only
proposal and evidence record. It is not connected to the runtime, skill discovery,
principal memory, nightly climb, provider registry, or activation paths. No CLI
runtime defaults change. No model, benchmark, or upstream package is installed.

## Run the offline example

From a development checkout with Exo's normal test dependencies:

```sh
PYTHONPATH=src python -m exo.improvement.demo > /tmp/harness2-demo.json
PYTHONPATH=src python -m pytest tests/eval/test_harness2.py tests/eval/test_eval.py tests/eval/test_eval_climb.py -q
```

The fixture reconciles signed decimal amounts. Its general proposal includes
negative amounts; its domain proposal preserves decimal formatting. Both probe
observations enter composition; the composed notes are probed again. The fixed
fixture editors demonstrate mechanics only, not model effectiveness. Expected:
`proposal_ready`, `activated: false`, 11 callback calls, four probe stages, paired
score delta 1.0. Output is deterministic and contains no private data.

## Reuse the adapter

```python
from exo.improvement.harness2 import Limits, run_cycle

artifact = run_cycle(
    tasks=public_tasks,                 # explicit {id, domain, input} mappings
    baseline={"notes": existing_notes},
    propose=trusted_editor,             # context -> {notes: text}
    compose=trusted_composer,           # context -> {notes: text}
    probe=trusted_evaluator,            # (task, notes) -> observation/score/rule_ok
    limits=Limits(iterations=1, max_tasks=16, max_calls=64),
)
```

The proposer is called once for `general` and once for `domain` in each round.
It sees task inputs, the current notes and observed behavior, and lessons from
successful earlier rounds in this run. The composer sees the baseline and both
candidates' notes and behavior. Neither receives scores, rule verdicts, reference
answers, acceptance decisions, or score-based lessons. Keep hidden evaluator data
inside the caller-owned probe. Inputs and observations must themselves exclude
secrets, reference answers and grading rubrics; schema validation cannot detect
semantic leakage. Callback contexts are copied to prevent accidental mutation of
recorded state. Editor-facing lessons include only behavior-derived evidence IDs;
metric-bearing evidence hashes remain exclusively in the audit artifact. This
prevents hash matching against possible hidden scores. Callbacks are trusted application code and must not execute
proposal text or convert it into system-level authority.

Only `notes` text can be proposed. Extra fields, including guardrails, permissions,
plugins, scripts and filesystem paths, fail validation before probing. Notes are
still untrusted instructions: this schema is not a prompt-injection defense or a
sandbox. No interpreter, subprocess, tool dispatcher, network client, dynamic import,
or config writer executes them here. A future model-backed probe needs its own
fixed permissions, data isolation and action approval checks. It needs separate
explicit model/budget/data authority before any paid or private-data experiment.

Each round uses two editor calls, one composer call and three probes per task.
Baseline probes also count. A whole round must fit the remaining cap before it
starts; a failing callback also counts. Limits bound iterations (at most 8), tasks
(at most 64), individual text fields (8,000 characters), and calls (at most 2,048).
They do not bound callback wall time, nested calls, token usage or money: trusted
callbacks must enforce those independently. This API makes no calls by itself.

After composition, Exo's existing `eval.compare` and `eval.gate` evaluate the final
proposal against the incumbent on the identical task set. The adapter additionally
requires no individual regression and all final rule checks to pass. Only a strict
improvement exceeding the margin is retained. Evaluator values must be finite
scores in [0,1] and actual boolean rule checks. Invalid evidence, callback errors,
exhausted budgets or a non-improving round discard the run's retained proposal and
lessons; all completed and partial probe records remain auditable. Baseline notes
remain in the artifact for rollback. No runtime rollback is needed because no
activation occurs. The artifact carries source/task/evidence/artifact hashes; these
are reproducibility identifiers, not signatures or proof of evaluator honesty.

Lessons are mechanically scoped to these exact inputs, reference a completed probe
record and never assert transfer to unseen tasks. Persist the returned artifact in
a caller-selected experimental evidence location if needed. The module never writes
to the principal's ledger or memory. There is no automatic cross-task lesson import,
no nightly scheduling, no hidden data loading, and no promotion API. Generalization,
noise sensitivity and held-out effectiveness remain unverified. A later adoption
must pass Exo's independent current-head review and promotion process; this
experimental in-sample gate does not replace `eval.climb`'s held-out checks.

## Source, attribution and intentional differences

Inspected source: Google Research, **Harness²: Recursive Agent Harnessing for an
Open World**, commit
[`1cfe254e8d25b15f37048c3730134686abe60335`](https://github.com/google-research/harness2/tree/1cfe254e8d25b15f37048c3730134686abe60335),
the official default-branch head observed on 2026-10-31. License: Apache-2.0.
Paper: Xu et al., [arXiv:2610.14224](https://arxiv.org/abs/2610.14224).
This implementation is original Exo code adapting the method; no upstream code,
assets, third-party benchmarks or datasets are copied, vendored or imported.

Mechanism mapping at that exact source:

| Upstream source | Exo adaptation |
| --- | --- |
| `harness2/core/recursion.py:run_sequential` | General (INV) and domain (DOM) proposals, same-task behavioral probes, composition from both contrasts, then a fresh composition probe; bounded sequential rounds. |
| `harness2/core/spec.py:BenchmarkAdapter`, `RunView` | Explicit editor/probe seams; behavioral observations separated from hidden evaluator metrics. This is an Exo API, not a drop-in implementation of upstream's protocol. |
| `harness2/core/selection.py:reflect_adoption`, `core/library.py:Library.add` | Evidence-linked cue/lesson/evidence in the review artifact, scoped to a task digest. No automatic domain library or adoption. |
| `harness2/demo.py` | Credential-free deterministic demonstration of contrasting signed-amount/formatting behaviors. Exo uses trusted Python functions, never generated scripts. |

Upstream selects artifacts without ground-truth scores and judges afterward.
Exo similarly hides metrics from proposal/composition, but intentionally adds a
post-composition scalar evaluation and conservative regression gate before
retaining a review proposal. Later rounds continue only after that gate. This is
a constrained adaptation, not a reproduction of upstream experimental results.
It supports sequential rounds only, no parallel recursion, anonymous selector,
model-driven experience reflector, edit retries, or full eight-component harness.

The inspected `pyproject.toml` uses setuptools, Python 3.12–3.13, no mandatory
runtime dependencies, optional rendering/bridge dependencies, and CLI entry points.
`harness2/install.py` performs benchmark installations and runner overlays; none
was executed here. `harness2/substrate/codex.py:exec_argv` passes sandbox/hook-trust
bypass flags and `exec_env` builds an execution environment. Those flags alone do
not establish whether an outer research environment isolates a given deployment.
Exo imports neither the substrate nor its trust boundary. Upstream recommends Linux;
this offline Exo adapter remains compatible with Exo's Python >=3.11 requirement.
