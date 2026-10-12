#!/usr/bin/env python3
"""Measure the agent wire (docs/AGENT-WIRE.md section 3): bytes and real tokenizer counts.

    python3 scripts/wire_bench.py [--upstream <protean-sym2p checkout>] [--tokenizer tokenizer.json ...]

Counts each delegation sample in five forms: Hermes-style result JSON, Exo canonical JSON,
the Exo line, the Exo line with the -rcp checksum, and the English render. Token counts use
tiktoken (o200k_base, cl100k_base) and any Hugging Face tokenizer.json files given, when
those libraries are importable; bytes are always exact. No network, no model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exo.wire import Brief, Receipt, encode, render, to_json  # noqa: E402

GOAL_LONG = ("Add a --dry-run flag to `exo upstream report` that prints the ports it would propose without "
             "writing the report file. Follow the existing argparse style in src/exo/cli.py, add a test in "
             "tests/upstream/test_upstream.py that runs it against the fixture mirror, and keep the change "
             "under 60 lines.")
CTX_LONG = ("The report command lives in src/exo/upstream.py (function report). Tests use a tmp_path mirror "
            "built by make_mirror(). Do not touch PROVENANCE.toml.")
SUMMARY_LONG = ("Added --dry-run to `exo upstream report` (src/exo/cli.py, src/exo/upstream.py: report() takes "
                "write=False). New test test_report_dry_run_writes_nothing passes; full upstream suite green "
                "(14 passed). No other files changed.")

BRIEFS = {
    "brief.scout": Brief(id="k_3f9a2c1d", thread="s_8e7d6c5b", goal="Find every caller of load_config and list "
                         "them as file:line.", role="scout", tools=["read_file", "grep", "glob"], max_turns=12,
                         max_tokens=60000, timeout_s=300),
    "brief.builder": Brief(id="k_4a0b1c2d", thread="s_8e7d6c5b", goal=GOAL_LONG, context=CTX_LONG,
                           role="builder", max_turns=24, max_tokens=200000, timeout_s=900,
                           accept=["the new test passes", "no other test changes"]),
}
RECEIPTS = {
    "receipt.scout": Receipt(id="r_3f9a2c1d", thread="s_8e7d6c5b", brief="k_3f9a2c1d", role="scout",
                             summary="3 callers: src/exo/cli.py:52, src/exo/onboard.py:118, "
                                     "src/exo/core/engine.py:342.",
                             input_tokens=18240, output_tokens=388, cost_usd=0.0213, duration_ms=9400, rounds=3,
                             tool_calls=4, child_session="s_1b2c3d4e"),
    "receipt.builder": Receipt(id="r_4a0b1c2d", thread="s_8e7d6c5b", brief="k_4a0b1c2d", role="builder",
                               summary=SUMMARY_LONG, input_tokens=96100, output_tokens=2410, cost_usd=0.1442,
                               duration_ms=71800, rounds=9, tool_calls=14,
                               files=["src/exo/cli.py", "src/exo/upstream.py", "tests/upstream/test_upstream.py"],
                               child_session="s_5f6a7b8c"),
    "receipt.timeout": Receipt(id="r_9c8b7a6f", thread="s_8e7d6c5b", brief="k_9c8b7a6f", status="failed",
                               role="builder", summary="Partial: reproduced the flaky test; root cause not found.",
                               input_tokens=140200, output_tokens=3900, cost_usd=0.2101, duration_ms=900000,
                               rounds=24, tool_calls=31, error_code="timeout", error="no result within 900 s",
                               child_session="s_0d1e2f3a"),
}

LEGEND = (  # what a model must be told to read Exo lines without guessing (minimum)
    "Packets are one line: V|ID|TK|F|T|A|ST|CF|CV|BODY|RQ|BASE|EXP[|SUM]. Roles: 1 coordinator, 2 builder, "
    "3 verifier, 4 scout, 5 critic, 6 operator. Acts: 11 delegate, 12 report. States: 1 proposed, 2 running, "
    "6 done, 100 failed, 101 cancelled. BODY ops: (100 v) profile, (101 \"goal\"), (102 \"context\"), "
    "(103 tool...) allowed tools, (104 turns tokens seconds) limits, (105 depth max), (106 \"cwd\"), "
    "(107 \"done when\"), (110 \"summary\"), (111 in out micro_usd) usage, (112 ms rounds calls) work, "
    "(113 \"file\"...) files changed, (114 code \"why\") error, (115 session) child. 0 means none.")


def hermes_json(r: Receipt) -> str:
    """The shape Hermes's delegate_task returns to the parent model (tools/delegate_tool_child_run.py)."""
    entry = {"task_index": 0, "status": "completed" if r.status == "done" else "failed", "summary": r.summary,
             "api_calls": r.rounds + 1, "duration_seconds": r.duration_ms / 1000, "model": "provider/model-name",
             "exit_reason": "completed" if r.status == "done" else r.error_code, "truncated": False,
             "tokens": {"input": r.input_tokens, "output": r.output_tokens},
             "tool_trace": [{"tool": "read_file", "status": "ok"}] * r.tool_calls,
             "cost_usd": r.cost_usd, "cost_status": "estimated"}
    if r.error:
        entry["error"] = r.error
    return json.dumps({"results": [entry]}, ensure_ascii=False)


def hermes_brief(b: Brief) -> str:
    return json.dumps({"goal": b.goal, "context": b.context or None, "toolsets": b.tools or None,
                       "role": "leaf"}, ensure_ascii=False)


def counters(extra: list[str]) -> dict:
    out = {"bytes": lambda s: len(s.encode("utf-8"))}
    try:
        import tiktoken
        for name in ("o200k_base", "cl100k_base"):
            enc = tiktoken.get_encoding(name)
            out[name] = lambda s, e=enc: len(e.encode(s))
    except Exception as exc:  # no tiktoken or no cached encodings
        print(f"note: tiktoken unavailable ({type(exc).__name__}); bytes only", file=sys.stderr)
    for path in extra:
        try:
            from tokenizers import Tokenizer
            tok = Tokenizer.from_file(path)
            hub = [x for x in Path(path).parts if x.startswith("models--")]
            label = hub[0].split("--")[-1] if hub else Path(path).parent.name
            out[label] = lambda s, t=tok: len(t.encode(s, add_special_tokens=False).ids)
        except Exception as exc:
            print(f"note: tokenizer {path} unavailable ({type(exc).__name__})", file=sys.stderr)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", help="protean-sym2p checkout, to also count its bench corpus")
    ap.add_argument("--tokenizer", action="append", default=[], help="a Hugging Face tokenizer.json")
    a = ap.parse_args(argv)
    count = counters(a.tokenizer)
    cols = list(count)
    rows: list[tuple[str, str, str]] = []
    for name, b in BRIEFS.items():
        rows += [(name, "hermes-args", hermes_brief(b)), (name, "exo-json", to_json(b)),
                 (name, "line", encode(b)), (name, "line-rcp", encode(b, rcp=True)), (name, "english", render(b))]
    for name, r in RECEIPTS.items():
        rows += [(name, "hermes-json", hermes_json(r)), (name, "exo-json", to_json(r)),
                 (name, "line", encode(r)), (name, "line-rcp", encode(r, rcp=True)), (name, "english", render(r))]
    rows.append(("legend", "line-legend", LEGEND))
    if a.upstream:
        tasks = [json.loads(x) for x in (Path(a.upstream) / "bench" / "tasks.jsonl").read_text().splitlines()
                 if x.strip()]
        for t in tasks:
            if "v1" in t:
                rows += [("up." + t["id"], "v1-json", json.dumps(t["v1"], separators=(",", ":"))),
                         ("up." + t["id"], "v2-line", t["v2"])]
    print("| sample | form | " + " | ".join(cols) + " |")
    print("|---|---|" + "---:|" * len(cols))
    for sample, form, text in rows:
        print(f"| {sample} | {form} | " + " | ".join(str(count[c](text)) for c in cols) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
