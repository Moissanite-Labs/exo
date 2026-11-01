"""Credential-free fixture exercising the adapter, not model effectiveness.

Run: PYTHONPATH=src python -m exo.improvement.demo > /tmp/harness2-demo.json
"""
import json
from decimal import Decimal

from exo.eval import exact_grader
from exo.improvement.harness2 import run_cycle

TASKS = [
    {"id": "signed", "domain": "accounting", "input": "100.25,-20.10,5.00"},
    {"id": "precision", "domain": "accounting", "input": "0.10,0.20"},
]
# Fixture evaluator data never enters propose/compose context.
_REFERENCES = {"signed": "Total: 85.15", "precision": "Total: 0.30"}


def probe(task, harness):
    notes = harness["notes"]
    amounts = [Decimal(x) for x in task["input"].split(",")]
    total = sum((x for x in amounts if "signed" in notes or x > 0), Decimal(0))
    answer = f"Total: {total:.2f}" if "decimal" in notes else f"Total: {int(total)}"
    return {"observation": answer,
            "score": exact_grader({"gold": _REFERENCES[task["id"]]}, answer), "rule_ok": True}


def propose(context):
    return {"notes": "signed" if context["dimension"] == "general" else "decimal"}


def compose(context):
    # Fixed fixture editor combines both demonstrated changes from the probe pool.
    return {"notes": " ".join(c["proposal"]["notes"] for c in context["candidates"])}


def demo():
    return run_cycle(tasks=TASKS, baseline={"notes": "positive integers"},
                     propose=propose, compose=compose, probe=probe)


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, allow_nan=False))
