# Measure before changing the hot paths

This is a reviewable first slice of Wave 0 (#266), supporting the outstanding
optimization audit (#252). It adds a runnable measurement harness and acceptance
checks. It does not implement the optimizations or mark the audit issues fixed.

Run from a frozen dependency environment:

```sh
uv sync --frozen --all-extras --python 3.11
uv run python scripts/optimization_baseline.py > baseline.json
uv run pytest -q tests/test_optimization_baseline.py
```

The default fixture has 200 synthetic messages and 1,000 synthetic events. For a
larger comparable run use `--messages 10000 --events 100000`. These are explicit
upper bounds, not defaults. Each run creates and removes its own temporary
databases. It does not open the user's Exo home, keys, transcript, provider, Bus,
or website. The ledger subprocesses use their own temporary `EXO_HOME` and fresh
synthetic signing identities. No credential or conversation enters the report.

Keep the JSON report with the code revision, Python version, fixture sizes and
platform it records. Compare equal fixtures on the same machine and repeat the
measurements before claiming an improvement. Wall time is descriptive and has no
pass/fail threshold. SQL statements are observed through SQLite's trace callback;
they are not CPU operations or visited-row counts.

| Open issue | Evidence supplied by this draft | Still required before claiming resolution |
| --- | --- | --- |
| #254 session/store cost | Production full-message, bounded-tail, session-list, fork and event-page calls; rows materialized, SQL and transaction counts; reference-tail equivalence | Optimize the affected callers and storage paths, preserve undo/fork semantics, and compare large fixtures. The store BM25 work already landed separately. |
| #256 anchor writes | Abrupt child-process exit immediately before each of the two anchor writes after a committed append; verify both the interrupted state and a subsequent append | No batching is implemented. These two process-exit cases are not exhaustive crash proof, disk power-loss simulation or authorization to weaken anchors. Add fault coverage for any proposed new algorithm. |
| #257 gate facade | AST public-name inventory and removed-name comparison; current structural size | A behavior-preserving split, all re-export contracts, gate differential checks and security review. AST names do not prove runtime or dynamic API compatibility. |
| #258 asset pipeline | Tracked image bytes and content hashes; duplicate excess counted once per extra copy; cache-config presence | Transform responsive assets, verify references and served cache headers, stage and measure browser transfer/LCP. Config presence alone says nothing about cache correctness. |
| #259 monolith seams | File/function size and public-name inventories for consolidate, context and the terminal client | Golden consolidation proposals, event/command behavior characterization and reviewed extraction. |
| #262 context and streaming work | Message/event transaction observations and AST summary-call-site count | A real context-build query count and fake-stream transaction benchmark, then bounded-cache/coalescing work. Source sites are not queries per build. |
| #263 model and Bus work | Production Bus inbox/search measurements, query plans and transport client-construction source sites | HTTP lifecycle fixture, large Bus fixtures, semantics-preserving migration and index validation. The query-plan projection is explicitly `seq`; timing uses production methods. |
| #265 CI observation | Current pytest command-site count and explicit cache/durations setting mentions | Collect exact tests, observe hosted durations when runners start, remove duplicates and validate lockfile installs. Mentions alone do not prove caching is enabled. |

The public-name check is available independently of timing comparisons:

```sh
uv run python scripts/optimization_baseline.py --compare baseline.json > after.json
```

It exits nonzero if a recorded public name disappears, including when its module
is removed. New names do not fail the check. A future facade must continue to
export the old names; this check supplements, and never replaces, runtime tests.

The optimization and refactor issues remain open. This draft supplies evidence
and testable prerequisites for review, not a promise that the older audit numbers
still describe current main or that every Wave 0 requirement is complete.
