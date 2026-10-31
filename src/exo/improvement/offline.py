"""Explicit public fixtures; root-list guard only, no store contents or activation."""
from __future__ import annotations

import json
import os
import re
import stat
from contextlib import suppress
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from exo.eval import compare, gate
from exo.improvement.harness2 import UPSTREAM, UPSTREAM_COMMIT, Limits, _digest, _evidence, run_cycle

HELP = "offline Harness² proposals from explicit public fixtures (review only)"
ADAPTERS = ("signed-decimal-v1", "text-normalize-v1")
SLUG = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
AMOUNT = re.compile(r"[+-]?[0-9]{1,8}(?:\.[0-9]{1,4})?\Z")
FILE_CAP = 256 * 1024


@dataclass(frozen=True)
class ReviewProposal:
    """Export type deliberately distinct from principal-authored ledger proposals."""
    id: str
    kind: str
    status: str
    namespace: str
    notes: str
    development_evidence: str
    held_out_evidence: str


def _slug(value):
    if not isinstance(value, str) or not SLUG.fullmatch(value):
        raise ValueError("expected a lowercase fixture/run identifier")
    return value


def _local_file(value):
    p = Path(value)
    if p.name != value or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\.json", value):
        raise ValueError("fixture must be a plain JSON filename in the working directory")
    return p


def _workspace():
    # Read only configured store locations, never instantiate or read a store.
    # Normal tool authorization remains in force for agent CLI invocations.
    from exo.gate import is_sensitive_path
    from exo.store import explicit_roots
    cwd = Path.cwd().resolve()
    homes = [Path(os.environ.get("EXO_HOME") or Path.home() / ".exo").expanduser().resolve()]
    if os.environ.get("EXO_PRINCIPAL_STORE"):
        homes.append(Path(os.environ["EXO_PRINCIPAL_STORE"]).expanduser().resolve())
    homes.extend(root.resolve() for root in explicit_roots(homes[0]).values())
    if cwd in (Path(cwd.anchor), Path.home().resolve()) or any(cwd == p or p in cwd.parents for p in homes) or any(p.startswith(".") for p in cwd.parts[1:]):
        raise ValueError("run only in an explicit public workspace")
    if is_sensitive_path(str(cwd)):
        raise ValueError("workspace is a protected location")
    return cwd


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def load_fixture(filename, adapter, max_tasks):
    if adapter not in ADAPTERS:
        raise ValueError("unknown trusted adapter")
    path = _local_file(filename)
    from exo.gate import is_sensitive_path
    if is_sensitive_path(str(path.absolute())):
        raise ValueError("fixture is a protected path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > FILE_CAP:
            raise ValueError("fixture must be a bounded regular file")
        raw = source.read(FILE_CAP + 1)
    if len(raw) > FILE_CAP:
        raise ValueError("fixture exceeds size cap")
    fixture = json.loads(raw, object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    required = {"schema", "classification", "namespace", "baseline", "development", "held_out", "references"}
    if not isinstance(fixture, dict) or set(fixture) != required or type(fixture["schema"]) is not int or fixture["schema"] != 1:
        raise ValueError("invalid fixture schema")
    if fixture["classification"] != "public-synthetic":
        raise ValueError("fixture must be explicitly public-synthetic")
    namespace = _slug(fixture["namespace"])
    allowed = ({"positive integers", "signed", "decimal", "signed decimal"} if adapter == "signed-decimal-v1"
               else {"original", "trim", "lower", "trim lower"})
    if not isinstance(fixture["baseline"], dict) or set(fixture["baseline"]) != {"notes"} or fixture["baseline"]["notes"] not in allowed:
        raise ValueError("baseline must use the selected built-in adapter's notes")
    dev, held = fixture["development"], fixture["held_out"]
    if not isinstance(dev, list) or not isinstance(held, list) or not dev or not held or len(dev) + len(held) > max_tasks:
        raise ValueError("both disjoint splits must fit the task cap")
    ids, inputs = set(), set()
    for split, tasks in (("dev", dev), ("held", held)):
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {"id", "domain", "input"}:
                raise ValueError("invalid task schema")
            identity = _slug(task["id"])
            expected_domain = "accounting" if adapter == "signed-decimal-v1" else "text"
            if task["domain"] != expected_domain or not isinstance(task["input"], str) or not 1 <= len(task["input"]) <= 8000:
                raise ValueError("invalid domain or task input")
            if adapter == "signed-decimal-v1":
                amounts = task["input"].split(",")
                if len(amounts) > 256 or not all(AMOUNT.fullmatch(x) for x in amounts):
                    raise ValueError("expected bounded signed decimal amounts")
                canonical = tuple(sorted(Decimal(x) for x in amounts))
            else:
                canonical = task["input"].strip().casefold()
                if not canonical:
                    raise ValueError("empty normalized task")
            if identity in ids or canonical in inputs:
                raise ValueError("duplicate or overlapping task identity/input")
            ids.add(identity)
            inputs.add(canonical)
            # Namespaced IDs keep fixture evidence separate from ledger identities.
            task["id"] = f"fixture:{namespace}:{split}:{identity}"
    refs = fixture["references"]
    if not isinstance(refs, dict) or set(refs) != ids or not all(isinstance(v, str) and 1 <= len(v) <= 8000 for v in refs.values()):
        raise ValueError("references must exactly cover fixture task IDs")
    fixture["references"] = {task["id"]: refs[task["id"].rsplit(":", 1)[-1]] for task in dev + held}
    return fixture


def callbacks(adapter, references):
    """Only trusted built-in functions; evaluator references never enter editors."""
    choices = ("signed", "decimal") if adapter == "signed-decimal-v1" else ("trim", "lower")

    def extend(baseline, additions):
        capabilities = set(baseline.split())
        for notes in additions:
            capabilities.update(notes.split())
        return " ".join(c for c in choices if c in capabilities) or baseline

    def propose(context):
        baseline = context["baseline"]["proposal"]["notes"]
        return {"notes": extend(baseline, [choices[context["dimension"] == "domain"]])}

    def compose(context):
        base = context["baseline"]["observations"]
        changed = [c["proposal"]["notes"] for c in context["candidates"] if c["observations"] != base]
        return {"notes": extend(context["baseline"]["proposal"]["notes"], changed)}

    def probe(task, harness):
        notes = harness["notes"]
        if adapter == "signed-decimal-v1":
            amounts = [Decimal(x) for x in task["input"].split(",")]
            total = sum((x for x in amounts if "signed" in notes or x > 0), Decimal(0))
            answer = f"Total: {total:.2f}" if "decimal" in notes else f"Total: {int(total)}"
        else:
            answer = task["input"]
            if "trim" in notes:
                answer = answer.strip()
            if "lower" in notes:
                answer = answer.lower()
        # Exact bytes preserve arithmetic signs and two-decimal formatting, as well
        # as text whitespace. The generic QA grader intentionally normalizes these.
        score = float(answer == references[task["id"]])
        return {"observation": answer, "score": score, "rule_ok": True}
    return propose, compose, probe


def experiment(fixture, adapter, limits):
    namespace = fixture["namespace"]
    report = {"schema": 1, "kind": "offline_harness2_experiment", "namespace": namespace,
              "adapter": adapter, "source": {"url": UPSTREAM, "commit": UPSTREAM_COMMIT}, "activated": False, "status": "budget_exhausted", "calls": 0,
              "fixture_digest": _digest(fixture), "limits": asdict(limits)}
    reserve = 2 * len(fixture["held_out"])
    if limits.max_calls <= reserve:
        report["artifact_digest"] = _digest(report)
        return report
    propose, compose, probe = callbacks(adapter, fixture["references"])
    development = run_cycle(tasks=fixture["development"], baseline=fixture["baseline"],
                            propose=propose, compose=compose, probe=probe,
                            limits=Limits(limits.iterations, limits.max_tasks, limits.max_calls - reserve, limits.margin))
    report.update(development=development, calls=development["calls"], status=development["status"])
    if development["status"] == "proposal_ready":
        held = {"baseline": {"rows": []}, "candidate": {"rows": []}}
        report["held_out"] = held
        try:
            for label, harness in (("baseline", fixture["baseline"]), ("candidate", development["proposal"])):
                for task in fixture["held_out"]:
                    report["calls"] += 1
                    result = _evidence(probe(dict(task), dict(harness)))
                    held[label]["rows"].append({"id": task["id"], "domain": task["domain"], **result})
            contrast = compare(held["baseline"], held["candidate"], margin=limits.margin)
            verdict = gate(contrast)
            held.update(comparison=contrast, gate=verdict)
            held["evidence_digest"] = _digest(held)
            passed = contrast["better"] and verdict["pass"] and not contrast["worse"] and all(r["rule_ok"] for r in held["candidate"]["rows"])
            report["status"] = "review_ready" if passed else "held_out_rejected"
            if passed:
                report["proposal"] = asdict(ReviewProposal(
                    id="h2-" + _digest({"namespace": namespace, "notes": development["proposal"]["notes"],
                                        "tasks": development["task_digest"]})[:24],
                    kind="harness_notes", status="review_only", namespace=namespace,
                    notes=development["proposal"]["notes"],
                    development_evidence=development["artifact_digest"], held_out_evidence=held["evidence_digest"]))
        except Exception as exc:
            report.update(status="failed", error_type=type(exc).__name__)
    report["artifact_digest"] = _digest(report)
    return report


def _output_target(cwd, name):
    if name is None:
        return None
    target = cwd / "improvement-runs" / _slug(name)
    if target.parent.is_symlink() or target.exists() or target.is_symlink():
        raise ValueError("output must be a new run; links and overwrites are refused")
    return target


def register(sp, common):
    p = sp.add_parser("improve", help=HELP)  # Deliberately no --root/--principal.
    p.add_argument("--offline", action="store_true", required=True)
    p.add_argument("--adapter", choices=ADAPTERS, required=True)
    p.add_argument("--fixture", required=True, help="public JSON filename in current directory")
    p.add_argument("--output", help="new run ID under ./improvement-runs (default: stdout only)")
    p.add_argument("--iterations", type=int, default=1,
                   help="rounds (default: 1); later non-improvement discards earlier proposals; "
                        "built-in fixtures saturate after one round")
    p.add_argument("--max-tasks", type=int, default=16)
    p.add_argument("--max-calls", type=int, default=64)
    p.add_argument("--margin", type=float, default=.01)


def cmd_improve(args):
    try:
        cwd = _workspace()
        target = _output_target(cwd, args.output)
        limits = Limits(args.iterations, args.max_tasks, args.max_calls, args.margin)
        fixture = load_fixture(args.fixture, args.adapter, limits.max_tasks)
        report = experiment(fixture, args.adapter, limits)
        text = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if target is not None:
            from exo.core.private import atomic_write, private_dir
            private_dir(target.parent)
            target.mkdir(mode=0o700)  # exclusive directory; never replace an existing run
            try:
                atomic_write(target / "artifact.json", text)
            except BaseException:
                # Only our newly reserved empty run can be removed. rmdir refuses
                # nonempty directories and links; existing runs never reach here.
                with suppress(OSError):
                    target.rmdir()
                raise
        print(text, end="")
        return 0 if report["status"] == "review_ready" else 1
    except (ValueError, TypeError, OSError, UnicodeError, RecursionError):
        import sys
        print("exo improve: invalid public fixture, limits or output path", file=sys.stderr)
        return 2
