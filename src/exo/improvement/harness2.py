"""Bounded Propose–Probe–Compose, adapted from the Harness² method (not its code).

Callbacks are trusted application code, not a sandbox. Their returned proposals and
observations are untrusted data. This module never imports or executes proposal text,
loads a principal store, changes runtime configuration, or activates an improvement.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass

from exo.eval import compare, gate

UPSTREAM = "https://github.com/google-research/harness2"
UPSTREAM_COMMIT = "1cfe254e8d25b15f37048c3730134686abe60335"
MAX_TEXT = 8000


@dataclass(frozen=True)
class Limits:
    iterations: int = 1
    max_tasks: int = 16
    max_calls: int = 64
    margin: float = 0.01

    def __post_init__(self):
        for value, maximum in ((self.iterations, 8), (self.max_tasks, 64),
                               (self.max_calls, 2048)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("limits must be positive bounded integers")
        if type(self.margin) not in (int, float) or not math.isfinite(self.margin) or not 0 <= self.margin <= 1:
            raise ValueError("margin must be finite and in [0, 1]")


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError("expected nonempty bounded text")
    return value


def _proposal(value):
    # No path, guardrail, plugin, script, authority, or arbitrary config surface.
    if not isinstance(value, Mapping) or set(value) != {"notes"}:
        raise ValueError("only the notes proposal surface is permitted")
    return {"notes": _text(value["notes"])}


Editor = Callable[[dict], Mapping]
Probe = Callable[[dict, dict], Mapping]


def run_cycle(*, tasks: Sequence[Mapping], baseline: Mapping, propose: Editor,
              compose: Editor, probe: Probe, limits: Limits = Limits()) -> dict:
    """Return a serializable audit artifact; never adopt it into the agent runtime.

    Tasks are explicit public/synthetic inputs {id, domain, input}; no implicit files.
    propose/compose receive behavioral observations, not evaluator scores. probe is a
    caller-owned evaluator returning {observation, score, rule_ok}. Its score is not
    evidence of generalization. All callbacks count against one cap, including baseline
    probes and failing calls. Callbacks must enforce their own time/cost/isolation caps.
    """
    current = _proposal(baseline)
    if not 1 <= len(tasks) <= limits.max_tasks:
        raise ValueError("task count exceeds bounds or is empty")
    pool, ids = [], set()
    for task in tasks:
        if not isinstance(task, Mapping) or set(task) != {"id", "domain", "input"}:
            raise ValueError("tasks require exactly id, domain, input")
        row = {key: _text(task[key]) for key in ("id", "domain", "input")}
        if row["id"] in ids:
            raise ValueError("duplicate task id")
        ids.add(row["id"])
        pool.append(row)
    report = {"schema": 1, "source": {"url": UPSTREAM, "commit": UPSTREAM_COMMIT},
              "status": "no_improvement", "activated": False, "baseline": current,
              "task_digest": _digest(pool), "limits": asdict(limits), "calls": 0, "stages": [], "lessons": []}

    class Cap(Exception):
        pass

    def call(fn, *args):
        if report["calls"] >= limits.max_calls:
            raise Cap()
        report["calls"] += 1
        return fn(*(_copy(arg) for arg in args))

    def evaluate(harness, label):
        record = {"label": label, "proposal": harness, "observations": [], "rows": []}
        report["stages"].append(record)
        for task in pool:
            result = call(probe, task, harness)
            if not isinstance(result, Mapping) or set(result) != {"observation", "score", "rule_ok"}:
                raise ValueError("malformed probe evidence")
            score = result["score"]
            if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1 or type(result["rule_ok"]) is not bool:
                raise ValueError("invalid probe score or rule evidence")
            record["observations"].append({"id": task["id"], "observation": _text(result["observation"])})
            record["rows"].append({"id": task["id"], "domain": task["domain"],
                                   "score": score, "rule_ok": result["rule_ok"]})
        record["evidence_digest"] = _digest(record)
        return record

    def visible(record):
        return {key: record[key] for key in ("label", "proposal", "observations")}

    try:
        incumbent = evaluate(current, "baseline")
        for iteration in range(limits.iterations):
            # Reserve a whole round: two editors, three full probes, one composer.
            if report["calls"] + 3 + 3 * len(pool) > limits.max_calls:
                report["status"] = "budget_exhausted"
                break
            candidates = []
            for dimension in ("general", "domain"):
                candidate = _proposal(call(propose, {"dimension": dimension,
                    "tasks": pool, "baseline": visible(incumbent),
                    "lessons": [{key: lesson[key] for key in
                        ("cue", "lesson", "behavior_evidence", "task_digest")}
                        for lesson in report["lessons"]]}))
                candidates.append(evaluate(candidate, f"{iteration + 1}.{dimension}"))
            composed = _proposal(call(compose, {"tasks": pool, "baseline": visible(incumbent),
                                               "candidates": [visible(c) for c in candidates]}))
            final = evaluate(composed, f"{iteration + 1}.compose")
            contrast = compare(incumbent, final, margin=limits.margin)
            verdict = gate(contrast)
            # Stronger than the generic gate: no individual regression or broken rule.
            accepted = (contrast["better"] and verdict["pass"] and not contrast["worse"]
                        and all(row["rule_ok"] for row in final["rows"]))
            final["decision"] = {"proposal_retained": accepted, "comparison": contrast, "gate": verdict}
            if not accepted:
                report["status"] = "no_improvement"
                break
            report["proposal"] = composed
            report["status"] = "proposal_ready"
            report["lessons"].append({"cue": "these exact task inputs only",
                "lesson": "Compare the retained composition notes and observed behaviors in the cited record; review required.",
                "evidence": final["evidence_digest"],
                "behavior_evidence": _digest(visible(final)), "task_digest": report["task_digest"]})
            current, incumbent = composed, final
    except Cap:
        report["status"] = "budget_exhausted"
    except Exception as exc:
        # Do not turn partial/invalid evidence into a proposal or echo arbitrary errors.
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
    if report["status"] != "proposal_ready":
        report.pop("proposal", None)
        report["lessons"] = []
    report["artifact_digest"] = _digest(report)
    return report
