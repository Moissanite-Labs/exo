"""Deterministic synthetic evaluation; no model calls or production-data reads."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from exo.working_set import Record, WorkingSet, estimate


def metrics(selected, expected):
    matched = len(set(selected) & set(expected))
    return {"selected": selected, "relevant": matched, "expected": len(expected),
            "precision": round(matched / len(selected), 6) if selected else float(not expected),
            "recall": round(matched / len(expected), 6) if expected else 1.0}


def evaluate(data):
    records = [Record(**row) for row in data["records"]]
    if len({r.id for r in records}) != len(records):
        raise ValueError("fixture record IDs must be unique")
    state = WorkingSet(data["session"], data["agent"], token_budget=data["token_budget"], max_items=data["max_items"])
    visible, previous, rows = set(data.get("visible_ids", [])), None, []
    for task in data["tasks"]:
        if set(task["expected"]) - {r.id for r in records}:
            raise ValueError("fixture expected IDs must name a source")
        result = state.select(task["query"], records, visible_ids=visible, previous=previous)
        previous = result
        baseline, cost = [], 0
        eligible = [r for r in records if r.agent == state.agent and r.visibility == "shared"
                    and r.thread_kind == "thread" and not r.private_ancestry and r.id not in visible]
        for record in sorted(eligible, key=lambda r: (-r.ordinal, r.id)):
            if len(baseline) == state.max_items:
                break
            if cost + estimate(record) <= state.token_budget:
                baseline.append(record.id)
                cost += estimate(record)
        rows.append({"task": task["name"], "selector": {**metrics([r.id for r in result.records], task["expected"]),
                                                     "estimated_tokens": result.estimated_tokens},
                     "recency_baseline": {**metrics(baseline, task["expected"]), "estimated_tokens": cost}})
    return {"scope": "Handwritten synthetic examples; recency comparator is not existing Exo recall",
            "limits": "No representative quality, true tokenizer, latency or runtime integration claim", "tasks": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", type=Path, default=Path(__file__).with_name("fixture.json"))
    args = parser.parse_args(argv)
    with args.fixture.open("rb") as stream:
        raw = stream.read(1_000_001)
    if len(raw) > 1_000_000:
        parser.error("fixture exceeds 1 MB")
    print(json.dumps(evaluate(json.loads(raw)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
