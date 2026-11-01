# Exo Comprehensive Optimization Program — Hermes-Style

Date: 2026-10-18. Tracking issue: #252 (findings filed as #253-#266).
Method: Nous Research Hermes refactor method, scaled to Exo.
Status: taken over and brought up to date with `main` on 2026-10-19 (section
1a). Numbers in sections 1-2 are as measured at `eaf35f09` unless stated.
Evidence base: 11 audit lenses + adversarial critic + Hermes methodology
mapping, all read-only on the snapshot below. No claim in this plan comes
from outside that evidence. Where lenses counted the same finding several
times, the critic's merge verdicts win: one entry, one owner.

Hermes calibration (as reported, not re-inspected): about 1.06M LOC,
218 peak concurrency, a -34.4% LOC cut, two review rounds. Exo is ~27x
smaller. Do NOT copy the
Hermes >=30% LOC-cut mandate: Exo has 0 files >5,000 LOC and 0 functions
>300 lines (max 201), so a 30% cut of 39k LOC forces deletion of live code.
Target instead: file/function size, elif-chain depth, measured hot-path cost,
agent symbol-lookup tokens, and import time. Hermes import time rose during
its run; measure it per wave here.

## 1. Repo snapshot (as reported by every lens, consistent)

Current measurement work is in [OPTIMIZATION-BASELINE.md](OPTIMIZATION-BASELINE.md).
Its synthetic harness records the checked-out revision and does not claim the
optimizations below are complete. Keep the historical audit numbers separate.

- Audit snapshot: branch `main`, HEAD `eaf35f09` (2026-10-18). Every number
  in sections 1-2 is "at `eaf35f09`". Re-verify each finding on current
  `main` before starting work on it; the Wave 0 baseline (#266) is taken
  from `main` at the start of Wave 0, not from the audit commit.
- Stack: Python >=3.11, hatchling + uv (`uv.lock` 303KB), package exo 0.2.0.
  Core deps only `pynacl` + `httpx`. Extras: dev (pytest/ruff/mypy==2.4.0),
  mcp, tui (textual), bus (fastapi). No Node: `site/` is hand-written static
  HTML/CSS/JS, deployed to Vercel via `scripts/deploy-site.sh` from a
  git-free copy. SQLite + files for state.
- Entry points: `exo=exo.cli:main`,
  `exo-witness=exo.ledger_authority.witness_transport:main`, plus
  `exo.bus.__main__`, `exo.server.app`, `clients/tui`.
- Size (Python LOC): `src/exo` 39,062 / 151 files (core 12,386),
  tests 28,013 / 142 files (~1,499 funcs), plugins 15,515 (~365 funcs,
  6 plugins: exo-bus, exo-command, exo-context, exo-gate, exo-memory,
  exo-orchestra), clients 4,108, scripts 2,838. Only 3 src files >1,000 LOC:
  `gate.py` 2,756, `consolidate.py` 1,610, `core/context.py` 1,018.
  Site: 67 files, 2.4MB, 4,270 lines. 807 tracked files, 428 commits
  (2026-10-11..18).
- Repo commands (`.github/workflows/ci.yml`, `pyproject.toml`; no Makefile):
  - `uv sync --frozen --all-extras`
  - `uv run ruff check .` (ruff `select=["F"]` only)
  - `uv run python scripts/check_types.py` (mypy 2.4.0, shrink-only baseline
    `.github/mypy-baseline.json`: 248 diagnostics / 174 entries)
  - `uv run pytest -q` plus per-plugin suites
    `(cd plugins/<d> && uv run --project ../.. pytest -q)` and
    `uv run pytest plugins -q` (ubuntu/macos x py3.11/3.13; CI needs
    `ulimit -n 4096`, plugin suites serial per folder, own conftests)
- Layers (architecture lens): leaf crypto/ledger/store/wire ->
  core (types/models/tools/loop/engine) -> product
  (gate/consolidate/ask/ingest/learn/bus/server) -> cli/compose/application
  -> plugins/clients. Clean parallel seams: `core/types` Provider,
  `core/tools/registry`, `ingest/*` adapters, `learn/*`,
  `ledger_authority/*`, wire grammar, each plugin directory.

## 1a. Status on current main (2026-10-19, main `6e4860af`)

Since the audit, `main` has moved by many merged PRs, mostly the sweep
series. Measured on `6e4860af`: `gate.py` 4,700 LOC (was 2,756),
`consolidate.py` 1,639, `core/context.py` 1,046, TUI `app.py` 2,097; mypy
baseline 241 diagnostics in 170 entries (was 248 in 174). The core budget
test reports the count against `CORE_FLOOR` with no ceiling (D-077, D-083). Read it before sizing any CORE unit.

| Issue | Finding | Status on main | Where |
|-------|---------|----------------|-------|
| #253 | `_decode` primitives (rank 1) | Open. Fix in open PR #218 (strict decode, "Closes #253") | #218 |
| #254 | Session/store hot paths (rank 2) | Open. W1-5 done: `store.find` keeps its BM25 tables for the node set they were built from, and a reload (or a refresh that saw a change) makes a new set, so a repeat query tokenizes only itself. Ranking unchanged. 100 queries on 2,005 synthetic nodes went from 22.9 s to 0.36 s: one build of about 0.2 s, then about 1.6 ms a query. W2-4 part done: a turn counts its user turns with one SQL `COUNT` (`SessionStore.user_turns`) and reads only the 40 messages of its memory checkpoint (`SessionStore.last_messages`), so it no longer decodes the whole session twice. On a 10,000-message session the count went from 196 ms to 7.8 ms and the tail from 190 ms to 0.9 ms. A fork copies in one transaction with batched inserts: 5,000 messages went from 2.9 s to 0.25 s. Still open: `events_since(limit=1<<30)` (`engine.py:79`, `trace.py:540`, `diagnostics.py:155`) | #442 (W1-5) |
| #255 | N+1 and per-call reloads (rank 3a-c) | All three units done: W1-2 merged in #351 (counts in one query, 501 -> 1 at limit 500; final shape in #367: the limited page is a subquery and the correlated count runs outside it, so count work is bound by the limit, not the table), W1-3 merged in #354 (`Config.load()` caches on size and `mtime_ns`, deep copies out), W1-4 grep streams each file line by line instead of decoding and splitting it whole, output byte-identical | #351, #357, #367, #354, #362 |
| #256 | Ledger anchor writes (rank 3d) | Partly changed. Merged #221 (`e55183c1`, `066f5768`) removed the O(n) COUNT from append (indexed lookups), made append refuse past a frozen or mismatched anchor, and lets the anchor trail the ledger by at most one event. Two anchor atomic writes per append remain. Open PR #296 also changes `ledger.py` | #221 merged, #296 open |
| #257 | Split `gate.py` (rank 4) | Open, no split. `gate.py` is 4,700 LOC and six open PRs change it (section 1b) | none |
| #258 | Site asset pipeline (rank 5) | Open, no PR. No `vercel.json`; media unchanged | none |
| #259 | Monolith splits (rank 6) | Open, no PR. Sizes in the header of this section | none |
| #260 | CLI eager imports (rank 7) | W2-6 done: `cli.py` imports a command's module only when it runs (`LAZY` table, D-035 amendment). Best of 5: `import exo.cli` 190 to 14 ms, `exo --help` 251 to 42 ms, `exo --version` 240 to 73 ms. Every `--help`, usage line and parse error byte-identical (65 outputs compared with the old parser). `tests/test_cli_lazy.py` keeps the lazy and full parsers equal | #441 (W2-6) |
| #261 | Core coupling (rank 8) | Open, no PR. `core/engine.py` still imports `exo.server.engine` | none |
| #262 | Context/loop repeated work (rank 9) | Open, no PR. `context.py` changed for privacy (#216 merged), not for these paths | none |
| #263 | Learn/signals, HTTP clients, bus store (rank 10) | Partly done. W2-7's signals half: the chat capture keeps the known capture keys with the last ledger row read and reads only newer rows (10,000 captured rows: about 70 ms per turn before, under 1 ms after the first turn). Open: W2-7's HTTP client half (one client per transport and event loop adds 15 counted core lines, past the ceiling, so it waits for a core reduction or an owner call) and W3-4's bus store half (FTS5 changes what a search matches: needs an owner call) | #445 (W2-7 signals half) |
| #264 | Types/errors ratchet (rank 11) | Open. W1-6 done: `server/app.py` reads `Content-Length`, `since` and `Last-Event-ID` as ASCII digits up to 2^63 - 1 and answers anything else with 400 `{error}` (most of these got no reply before). Still open: the mypy burn-down (the baseline holds 234 diagnostics in 165 entries on main, down from 248 in 174 at the audit, seven of the drop pruned under #264), ruff `PERF`/`BLE`, typed settings for learn and eval, and the size gate | #444 (W1-6), #218 (partly) |
| #265 | CI and docs serialization (rank 12) | Open, no PR. `ci.yml` untouched since the audit; no `AGENTS.md` for the repo root, no `CODEOWNERS` | none |
| #266 | Wave 0 guardrails | Open, not started. No `tests/arch`, no `tests/bench`, no baseline script (only `scripts/wire_bench.py`) | none |

Status is from `gh issue view`, `gh pr view` and `git log eaf35f09..origin/main`
on 2026-10-19. Re-check it before assigning a unit.

## 1b. Owned by open PRs

A unit that touches one of these files waits for the PR, or rebases onto it.
Overlap is checked per file, not per finding.

| Open PR | Files that overlap this plan | Units affected |
|---------|------------------------------|----------------|
| #218 | `core/types.py` (`_decode`, closes #253), `server/app.py`, `server/engine.py`, `core/sessions.py`, `plugin_context.py`, `agents/delegate.py` | W1-1, W1-6, W2-4 |
| #296 | `config.py`, `ledger.py`, plugin settings (state files written through mkstemp) | W1-3, W3-3 |
| #286 | `core/sessions.py` (release SQLite handles) | W1-2, W2-4 |
| #302 | `core/context.py` (provenance escaping) | W2-5 |
| #125, #158, #231, #303, #305, #306 | `gate.py` (all six); #158 also removes gate entries from the mypy baseline that W3-2 counts on | W3-2 (starts only after these merge and `gate.py` is re-measured) |

Already merged and no longer open (they touched this plan's files):
#216 (`context.py`, `sessions.py`), #221 (`server/app.py`, `cli.py`,
`ledger.py`), #222 (`search.py`).

## 2. Ranked findings (deduped per critic verdicts)

Impact = measured evidence from the lenses. Speed claims are code-measured
(query/scan/byte counts), not benchmarked; Wave 0 turns them into numbers.

| # | Finding | Measured impact | Effort | Risk | Verification |
|---|---------|-----------------|--------|------|--------------|
| 1 | `core/types.py:214` `_decode` leaves primitives unvalidated (only Literal/list/dataclass checked, else `return value`); probe: `SessionGet{session:123}`, `Hello(screen_cols='wide')` pass silently across 30 commands/29 events | Correctness hole at every protocol boundary; strongest falsifiable finding (critic keep) | S | low | New bad-primitive tests raise `ProtocolError bad_body`; `uv run pytest -q` protocol suite green |
| 2 | Session/store hot paths (merged data+runtime): `sessions.py:285` `messages()` loads+json.loads ALL rows for count/`[-40:]` slices every turn; `sessions.py:333` `fork()` = N txns+FTS inserts; `engine.py:77`/`trace.py:479` `events_since(limit=1<<30)` then `sessions.py:500` json.loads all rows; `store.py:248` `find()` re-tokenizes all nodes and rebuilds Counter/df per query | O(history) work per turn x3+ loads; unbounded event materialization; BM25 rebuild per query | M each | med | LIMIT/COUNT/tail queries, batch fork, paged events, cached BM25 keyed to reload generation; benchmarks on 10k-msg / 100k-event / 1k-node fixtures; `uv run pytest -q` sessions/store/engine/trace |
| 3 | Code-measured data wins (critic keep): `sessions.py:186` `list_sessions` N+1 (51 queries @ limit 50, 501 @ `memory_cmd.py:65` limit 500); `config.py:140` `Config.load()` read+parse per call, 12 call sites in src; `search.py:101` grep `read_bytes` whole files <=2MB, scans past limit to count; `ledger.py:204` 2 anchor atomic-writes + COUNT per append | Query count 501->1; per-call disk+parse removed; whole-file reads bounded | S (first three), M (ledger) | low; ledger HIGH (crash-consistency) | sqlite query-count test; stale-after-edit semantics test; grep benchmark on 39k-LOC tree; ledger only behind crash/verify tests |
| 4 | `gate.py` god-file split (ONE finding, 5 lenses merged): 2,756 LOC, lexer `_Lexer:269` + classify `_classify_git:731`, `_classify_program:1000` (201L, 119 branches; an order-dependent if/return chain ending in the fail-closed opaque result; the "46 elif" figure is file-wide, the function itself has 3 `elif` lines and 64 `If` nodes) + decide + custom YAML; 15-28 mypy-baseline diagnostics | Blocks parallel edits; highest branch density; security-critical | L | high | Extract lexer/classifiers/decide behind facade re-exporting old names; program->handler dispatch table, behaviour-identical, a table miss returns the same opaque result; `uv run pytest -q` + the W0-3 gate golden corpus with zero diff (the existing `tests/security/test_gate_*` files are ordinary assertion tests, not goldens) |
| 5 | Site asset pipeline (ONE fix, 4 lenses merged): PNG-only full-res screenshots (13 shots ~1.57MB; 22 PNG = 1,724KB of 2,315KB site; roadmap page 960KB), 0 srcset/webp/avif; docs/site media duplicated (13-15 identical files, 1,567,708 bytes counted once — lenses double-counted); no prod `vercel.json` cache policy; dead orb code 5,512B of 9,479B `main.js` | ~1.57MB image bytes + 1.57MB repo duplication; repeat-visit revalidation | M | low | WebP/AVIF + 800w/1200w srcset from `scripts/*shots*.py`; single-source media copied at deploy; prod `vercel.json` immutable 1y media/assets; Lighthouse bytes/LCP + `curl -I` cache-control |
| 6 | Monolith splits, one entry each (merged): `consolidate.py` 1,610 LOC/9 section seams, thin tests (597 test LOC, 0.37 ratio vs 0.72 repo); `clients/tui/exo_tui/app.py` 1,940 LOC, one 1,855-line class/111 methods, 132L slash elif + 167L isinstance dispatch; `core/context.py` 1,018 LOC with lazy product imports | Churn/collision hotspots (TUI app 42 commits/2,350 churn lines in 7 days) | M-L | med | Split along existing seams (consolidate sections; TUI events/slash/sessions/panels + dispatch dicts; context hook registry); consolidate needs characterization tests FIRST; `uv run pytest -q tests/consolidate tests/clients tests/core -k context` |
| 7 | CLI eager imports: `cli.py:49-56` imports 16-17 subcommand modules at top (435 LOC, ~25 subparsers); `-X importtime exo.cli` cumulative 170ms, cold import 0.225s; any subcommand break breaks bare `exo` | Every `--help`/`--version` pays ledger+nacl+httpx graph | M | low-med | Lazy dispatch table, static `register()` metadata; `pytest tests/test_cli.py tests/test_cli_bare.py -q`; hyperfine `exo --help`/`--version` before/after |
| 8 | Core coupling: `core/engine.py` 793 LOC imports ~30 modules incl. `exo.server.engine` (core->server upward edge); 93 lazy `from exo...` + 16 TYPE_CHECKING hide cycles; `store`/`config` ambient `EXO_HOME` coupling (127 refs/20 files); plugins import src internals directly (`exo.store` x9, `exo.server.app` x8, `exo.core.*` x20+), no plugin SDK | Blocks independent work units; cycles invisible to tooling | M-L | med-high | Ports in `core/types.py` wired only in compose/application; `exo.plugin_api` facade + boundary test; arch test (core !-> server/bus/plugins) fails today, passes after; `uv run pytest -q` + per-plugin suites |
| 9 | Context/loop repeated work: `context.py` runs `summaries()` full SELECT 4x per build (7 sites, once per tool round); `estimate_tokens` fully renders incl. json.dumps repeatedly; `_thread_tail` O(k*tail); `_verbatim_cache` unbounded; per-delta `append_event` txn per streamed token (`sessions.py:538`+`loop.py:469`) | Per-turn SQL/render multiplication; unbounded memory in long-lived core; hundreds of txns/turn | S-M | low-med | Fetch summaries once per build; cached token counts; bounded LRU; 50ms delta coalescing; query/txn-count tests + 1,000-delta fake-stream timing |
| 10 | Learn/signals + HTTP reuse: `learn/signals.py:34,137` scans whole ledger per call (`ledger.py:94` json.loads per row); `core/models/base.py:193` new `httpx.AsyncClient` per stream, no pool/TLS reuse; `bus/store.py:82` `json_each` + `LIKE %q%` full scans, always fetches all 14 cols | O(ledger)/call; connection setup per request; full scans at 50k rows | S-M (bus L) | med | Index/watermark cache; one AsyncClient per transport; bus recipients table + FTS5 + projection; `EXPLAIN QUERY PLAN` + benchmarks; `uv run pytest -q` learn/models/bus |
| 11 | Types/errors ratchet (ONE item, 5 lenses merged): ruff F-only, mypy non-strict with 248 baselined diagnostics (gate ~28, learn 27, TUI app 20); 389 `dict[str,Any]`, 0 TypedDict; 135 `except Exception` (~79 silent-ish); 120 bare `raise ValueError`, 5 error shapes; `server/app.py:200-226` unvalidated `int()` header/query parsing drops connection silently | Silent failures indistinguishable from empty; boundary bugs invisible to checker | S-M per slice | low-med | Per-module baseline shrink only; `server/app.py` bounded parse -> 400; TypedDicts for learn/eval settings; ruff BLE/PERF report-only first; `uv run python scripts/check_types.py` count falls, `uv run pytest -q` green |
| 12 | Tests/CI speed + DX serialization (merged): 365 plugin test funcs run twice per cell x4 cells = 8 executions; 6 jobs each `uv sync --all-extras` (~88MB vs ~6.6MB core-only), no cache config; installer bypasses lockfile (`install.sh:254`, unbounded ranges); `DECISIONS.md` 1,382 LOC/83 commits + DOCS-MAP/CHANGELOG edited every change; no AGENTS.md/CODEOWNERS/Makefile; no coverage, no benchmarks, no `--durations` in CI; 120 sleeps in 43 test files, no pytest-xdist/timeout | CI minutes and merge conflicts scale with agent count; speed claims unverifiable | S-M | low | Drop duplicate plugin pass; per-job extras + setup-uv cache; constraints from `uv export --frozen`; DECISIONS -> per-decision files + index; AGENTS.md/CODEOWNERS; reporting-only coverage/durations first; CI wall-time on 3 runs |

Lower-ranked, still in scope (from lens evidence, not in waves unless Wave 3
capacity): sym2p v1 vs skill validator 74.2% line-similar drift risk (add
conformance test; do NOT delete v1 — see section 5); patches/ 3 of 4 fail
`git apply` on pinned Hermes v0.21.6 (379KB); 3 unhandled protocol commands
(taste.list, taste.edit, question.answer) + 4 never-published events at the
30-command cap (document reserved, additive-only rule); 5 duplicate `_git`
helpers; TUI packaged twice (root hatchling vs clients/tui setuptools) and
default install pulls ~19.6MB TUI stack vs ~6.6MB core; deploy fetches
unpinned `vercel@latest` per run; skill scripts with embedded CSS/JS strings (1,455 of 1,855
lines); site SEO gaps (no robots/sitemap/manifest, OG images wrong ratio,
brand page 25 imgs 0 lazy); TUI 0.12s tick repaints while idle.

## 3. Execution waves (parallel-agent sized, disjoint ownership)

Hermes rules applied throughout: orchestrator never edits; 1 brief / 1
worktree / 1 branch per work unit naming owned files, frozen interfaces, and
checks; commit after each verified step (<=1 unverified step per worker,
handoff note per worktree); never delete a public name (only 19 `__all__`
in src — surface is implicit; re-export shims mandatory); ban bulk semantic
rewrites (16 `suppress()` sites — per-site review only); cap concurrency
8-16 workers, share one mypy/ruff at integration, plugin suites serial.
Model tiers by role: a small fast model for scripted and benchmark units, a
mid-tier model for most implementation, the strongest model only for
gate, ledger and core-boundary judgment. Verify disjointness: `git diff --stat` per branch, zero
overlapping paths before merge.

### Wave 0 — Guardrails (must land before any optimization)

Goal: every later claim is falsifiable. No lens ran pytest, benchmarks, or
live sizes — this wave closes that before code moves.

| Unit | Owned files (exclusive) | Deliverable |
|------|------------------------|-------------|
| W0-1 Baseline freeze | new `scripts/baseline_snapshot.*`, snapshot artifacts only | On `main` at the start of Wave 0 (not `eaf35f09`, which is stale; record the commit): full pytest/ruff/mypy failure list; `exo --help` + every subcommand `--help` + tool JSON schemas snapshotted byte-identical; `python -X importtime -c 'import exo.cli'`; symbol-lookup token benchmark on fixed symbol set; file/func/elif metrics table (LOC, files >1k, funcs >100, max elif chain) |
| W0-2 Hot-path benchmarks | new `tests/bench/` (or `scripts/bench_*.py` if pytest-benchmark rejected) | Fixtures + baselines: 10k-message session tail/count, fork 5k, `events_since` 100k events, store `find()` x100 on 1k nodes, `list_sessions` query count, grep over this tree, ledger appends/sec, fake 1,000-delta stream txn count, context build SQL count |
| W0-3 Missing characterization tests | `tests/consolidate/`, `tests/protocol/` (new `_decode` cases), `tests/server/` (bad-header cases), new `tests/security/golden/` (gate corpus) | Consolidate golden proposals (blocks W2-2); bad-primitive-per-field-type tests (blocks W1-1 acceptance); bad/negative Content-Length/`since` tests; a gate golden corpus (blocks W3-2): recorded `classify_shell`/`decide` output over the `tests/security` and exo-gate corpora plus a generated argv set, with a required zero diff. Reporting-only coverage run to record per-module % for consolidate/eval |
| W0-4 Boundary guards | new `tests/arch/`, new name-diff script under `scripts/` | AST removed-public-name diff vs baseline (fails if any top-level name disappears without shim); import-boundary test asserting allowed layer edges (expected to FAIL on current core->server edge — xfail-marked with owner until W3-1) (the protocol freshness check already exists: `tests/protocol/test_export_chat.py::test_schema_export_stable_and_checked_in`) |
| W0-5 CI observation | `.github/workflows/ci.yml` (additive only) | `--durations=25` + junit artifact upload; record CI job durations, slowest/flakiest tests over 3 runs; measure real `~/.exo` DB sizes + `curl -I` live Vercel headers (read-only probes, recorded not fixed) |

Gate to Wave 1: baseline files committed; kill-and-resume drill passes (a
fresh agent resumes a killed worker from brief + handoff only).

### Wave 1 — Quick wins (low risk, disjoint; W1-1, W1-3, W1-4 and W1-6 are user-visible, flagged below)

| Unit | Owned files | Change |
|------|-------------|--------|
| W1-1 (rank 1) | `src/exo/core/types.py` (`_decode` only) | USER-VISIBLE: rejects bodies accepted today. Strict decoding only (non-strict callers `protocol/client.py:110`, `schema.py:678`, `sessions.py:536` and the TUI stay lenient). Validate str/int (excluding bool)/float/bool/dict[str,T] primitives; delete downstream guards only in strict-only handlers. Owned in practice by open PR #218 (strict-only, closes #253): do not duplicate it |
| W1-2 (rank 3a) | `src/exo/core/sessions.py` (`list_sessions`/`_info` only) | One query: the limited page as a subquery, a correlated count per kept row outside it; query-count test proves one statement at any limit, progress-handler test proves count work stays bound by the limit (shipped in #351/#357/#367) |
| W1-3 (rank 3b) | `src/exo/config.py` | USER-VISIBLE (changes when external edits are seen). Cache keyed on file size and `mtime_ns`; `load()` returns a deep copy so `set()` before a failed `save()` and mutation of a `get()` result never reach the next `load()` (readers include `update.require_signed` and `principal`); explicit reload on save. Tests: external edit seen, failed save invisible to the next load, mutating `get()`'s return does not leak |
| W1-4 (rank 3c) | `src/exo/core/tools/builtin/search.py` | USER-VISIBLE if it changes output. Stream lines instead of decoding and splitting each whole file; cheap binary skip. Grep has no count parameter, and the tool JSON schema snapshot must stay 0 bytes, so keep counting past the limit and keep the `[N more matches; narrow the search]` line exactly. Stopping early would change that line: needs decision, not part of this unit |
| W1-5 (rank 2 store) | `src/exo/store.py` (`find`/`reload` only) | Cache BM25 Counter/df/avg keyed to reload generation, invalidate in `reload()` |
| W1-6 (rank 11 slice) | `src/exo/server/app.py` (`_request`/`_client` only) | USER-VISIBLE: a dropped connection becomes a 400. Bounded int parsing -> 400 `{error}`; reject negative before MAX_BODY. #218 also changes this file: rebase onto it |
| W1-7 (rank 12 CI/install) | `.github/workflows/ci.yml`, `scripts/install.sh` | Drop duplicate plugin pass (combined run as single-cell job); per-job extras (lint=dev only); setup-uv cache; installer uses `uv export --frozen` constraints |
| W1-8 (rank 12 DX) | new `AGENTS.md`, new `CODEOWNERS`, `scripts/deploy-site.sh` (pin only) | Machine-readable ownership + command entrypoints from ci.yml; pin `vercel@<ver>` instead of `@latest` |

CORE units (W1-1, W1-2, W1-6, W2-4, W2-5, W3-1, W3-4) state their counted-line cost in the PR and ratchet `CORE_FLOOR` down when they reduce it (D-077).

Note: W1-2 and later W2-4 both touch `sessions.py` but in different waves —
never concurrently. Within Wave 1 all owned files are disjoint.

### Wave 2 — Structural (measured wins + monolith seams)

| Unit | Owned files | Change |
|------|-------------|--------|
| W2-1 (rank 5) | `site/media/`, `site/**/*.html` img tags, `scripts/*shots*.py`, `scripts/brand_assets.py`, new prod `vercel.json`, `scripts/deploy-site.sh` (media copy) | One asset pipeline: WebP/AVIF + srcset/sizes, single-source docs/site media copied at deploy, immutable cache headers, exclude unreferenced/brand assets from prod rsync, lazy/dimensions on brand page, robots/sitemap/manifest |
| W2-2 (rank 6a) | `src/exo/consolidate.py` -> `src/exo/consolidate/` package | Split along its 9 section seams (observe/reflect/propose/persist) behind facade; ONLY after W0-3 goldens exist; golden proposals byte-identical |
| W2-3 (rank 6b) | `clients/tui/exo_tui/app.py` (+ new sibling modules in `clients/tui/exo_tui/`) | Extract events/slash/sessions/panels; dispatch dicts replace 132L elif + 167L isinstance chains; CSS out of the class; gate 0.12s tick on busy state |
| W2-4 (rank 2 sessions) | `src/exo/core/sessions.py`, `src/exo/core/engine.py:78`, `src/exo/core/trace.py:484`, `src/exo/server/engine.py` (`Subscription`, `Hub.since`), `src/exo/server/app.py` (the `since` replay sites, around `:89` and `:323`), `src/exo/diagnostics.py:155` | COUNT/tail queries, one-txn batch fork (executemany FTS), `events_since` paged or streamed. `Subscription` promises replay of seq > since then live events with no gap or repeat and reads its backlog once, so a bounded first page would silently skip events: page the replay inside `Subscription` or stream without a bound. Replay acceptance test: subscribe from seq 0 across more than one page while live events publish; every seq arrives exactly once, in order |
| W2-5 (rank 9) | `src/exo/core/context.py` | Summaries fetched once per build; token counts cached at save; `_thread_tail` single fetch w/ lookback; `_verbatim_cache` bounded LRU. (Hook-registry extraction deferred to W3-1.) |
| W2-6 (rank 7) | `src/exo/cli.py` | Lazy subcommand dispatch; help/schema snapshots from W0-1 must diff 0 bytes |
| W2-7 (rank 10 partial) | `src/exo/learn/signals.py`, `src/exo/core/models/base.py` | Ledger scan -> indexed/watermarked known-set; one `httpx.AsyncClient` per transport, closed on shutdown |

### Wave 3 — Deep (high risk, sequenced, smallest parallelism)

| Unit | Owned files | Change |
|------|-------------|--------|
| W3-1 (rank 8) | `src/exo/core/engine.py`, `src/exo/compose.py`, `src/exo/core/types.py` (ports only), new `src/exo/plugin_api.py`, plugin import sites (one plugin dir per sub-branch, serial) | Move Hub/types down, inject Config/principal via compose; ports replace lazy imports; plugins consume `plugin_api` only; W0-4 arch test flips from xfail to pass |
| W3-2 (rank 4) | `src/exo/gate.py` -> `src/exo/gate/` package (lexer/classify/decide/policy) | Starts only after the six open gate PRs (section 1b) merge and `gate.py` is re-measured (4,700 LOC on 2026-10-19, not 2,756). Single L/high-risk split behind facade re-exporting every old name; `_classify_program` -> handler table. The default stays fail-closed: a program missing from the table returns the same `_opaque("runs {prog}, a program the gate does not know")` result as today, and precedence of the old if/return order is preserved. Typed lexer state shrinks the gate's mypy baseline entries (count them again after #158). Security review round mandatory (Hermes needed 2 review rounds) |
| W3-3 (rank 3d) | `src/exo/ledger.py` (anchor path only) | NEEDS DECISION (amends D-040). D-040 answers "drop the trigger, delete the tail, restore the trigger, verify clean" by anchoring seq, count and chain hash on every append; since #221 the anchor may trail by one event. Debouncing widens that lag: events appended since the last anchor write could be deleted the same way and verify would still pass. Only if the principal rules to amend D-040: batched anchors behind crash-consistency tests (kill-during-append + verify) AND a truncation test (append, drop the trigger, delete the last row, restore it, then `exo memory verify` must fail); no merge unless it still fails. Re-measure first: #221 already removed the O(n) COUNT, so the remaining win is small |
| W3-4 (rank 9/10) | `src/exo/core/loop.py` (delta path only), `src/exo/bus/store.py` | 50ms delta coalescing (TUI already flushes at 0.1s); bus recipients table + FTS5 + column projection with migration |
| W3-5 (rank 11, the size report: NEEDS DECISION if made a failing gate, which would amend D-077) | `pyproject.toml` (ruff/mypy config), `src/exo/learn/*` types, `src/exo/eval.py` types | Baseline burn-down per touched module; TypedDict/dataclass settings (delete per-use `int()`/`float()`); `require_runtime()` typed off-state; ruff PERF/BLE report-only -> enforced per-area; add size/complexity report (per-file line counts, report-only) |
| W3-6 (rank 12 remainder; NEEDS DECISION: moving `DECISIONS.md` changes the docs rule itself, since `tests/docs/test_docs_map.py` parses `^## D-\d{3}` there) | `DECISIONS.md` -> `docs/decisions/`, `docs/DOCS-MAP.md`, `CHANGELOG.md` fragments, `patches/`, `src/exo/protocol/` vs `protocols/` vs `wire/` docs map | Per-decision files + generated index (D-xxx ids stable, `test_docs_map` green); refresh or drop the 3 failing patches (`git apply --check` on clean v0.21.6); name/document the 3 wire layers, no code merge; sym2p conformance test (both validators over `tests/bus/sym2p_fixtures`, diff outputs) |

Integration after each wave: orchestrator merges branches one at a time,
runs the full gate (section 4), re-records the W0-1 metrics table +
importtime + lookup benchmark, and publishes a before/after table. Any
wave that regresses import time or the help/schema diff stops the program
until explained.

## 4. Per-unit acceptance checks (exact repo commands)

Every unit, no exceptions. The docs rule (`docs/DEVELOPER-GUIDE.md`, "The docs rule") applies to each: update its technical doc, `CHANGELOG.md`, `DECISIONS.md` where a decision changes, and a `docs/DOCS-MAP.md` row, and end the commit message with the guide-impact line ("User and developer guide impact: none" when the guides do not change). CORE units also ratchet `CORE_FLOOR` (D-077). Run in the worktree before handoff:

```
uv sync --frozen --all-extras
uv run ruff check .
uv run python scripts/check_types.py
uv run pytest -q
uv run pytest plugins -q
for d in plugins/*/; do (cd "$d" && uv run --project ../.. pytest -q); done
```

Plus the W0-1 guards: help/schema snapshot `diff` = 0 bytes; baseline-vs-
branch failure diff empty; removed-public-name diff empty; the unit's W0-2
benchmark re-run with before/after numbers in the handoff note. Targeted
suites per unit (from lens verify fields):

- W1-1: `uv run pytest tests/protocol -q` + new bad-primitive tests.
- W1-2/W2-4: `uv run pytest tests -q -k 'session or fork or memory'` +
  sqlite query-count test; benchmark 10k-msg tail/count, fork 5k,
  `events_since` 100k; W2-4 also the Hub replay test (subscribe from seq 0
  over more than one page while live events publish: every seq once, in
  order).
- W1-3: `uv run pytest -q -k config` + 1k-load benchmark + stale-edit test + failed-save-invisible and mutation-does-not-leak tests.
- W1-4: `uv run pytest -q -k search` + grep benchmark on this tree.
- W1-5: `uv run pytest tests/test_store.py -q` (store/find/ctx tests) +
  `find()` x100 on 1k nodes.
- W1-6: `uv run pytest -q -k server` + bad/negative header tests -> 400.
- W1-7: PR CI green; `pytest --collect-only` counts unchanged; fresh-venv
  `scripts/install.sh` then `pip freeze` diff vs `uv.lock` versions; CI
  wall-time compared over 3 runs.
- W2-1: staging deploy via `scripts/deploy-site.sh`; `curl -I` prod asset
  cache-control; Lighthouse transfer bytes/LCP; crawl for 404s;
  `git ls-files` hash-dedup recount = 0 duplicated media.
- W2-2: `uv run pytest tests/consolidate -q`, golden proposals
  byte-identical.
- W2-3: `uv run pytest tests/clients -q` (+ `tests/clients/tui` per-command
  cases); idle pilot test: refresh count over 5s.
- W2-5: `uv run pytest tests/core -q -k context` + SQL-count assertion per
  build + cache-bound unit test.
- W2-6: `uv run pytest tests/test_cli.py tests/test_cli_bare.py -q`;
  hyperfine `exo --version`, `exo --help`; `python -X importtime -c
  'import exo.cli'` diff reported.
- W2-7: `uv run pytest -q -k 'learn or models'`; mock-transport test
  asserts 1 client for N calls; 10k-event ledger benchmark.
- W3-1: arch test passes (grep core->server = 0); `uv run pytest
  tests/core tests/integration tests/contract -q`; per-plugin suites.
- W3-2: `uv run pytest tests/security tests/integration/test_required_gate.py
  -q` + W0-3 gate golden corpus with zero diff + table-coverage assert (every
  old branch has a handler) + a table-miss probe returning the opaque
  result + mypy baseline shrinks for gate.
- W3-3 (only after a ruling that amends D-040): `uv run pytest -q -k ledger`
  + crash/kill-during-append verify tests + the truncation test (drop the
  trigger, delete the last row, restore it: `exo memory verify` must fail)
  + appends/sec benchmark. No proof, no merge.
- W3-4: loop/streaming tests + txn count on 1,000-delta fake stream;
  `EXPLAIN QUERY PLAN` + 50k-row bus benchmark; `uv run pytest
  tests/bus -q`.
- W3-5: `uv run python scripts/check_types.py` (fewer entries, shrink
  only); `uv run ruff check .`; the size report lists the >1,000-LOC files
  (report-only unless a ruling amends D-077); `uv run pytest -q`.
- W3-6: `uv run pytest tests/docs/test_docs_map.py -q`; grep all D-xxx
  references resolve; `git apply --check` each patch on clean Hermes
  v0.21.6 worktree; both sym validators over `tests/bus/sym2p_fixtures`
  diff = 0.

## 5. What NOT to touch / behavior-change risks (critic drop verdicts)

These were proposed by lenses and falsified or reclassified. Do not do them
inside this optimization program:

- Do NOT treat `ingest/hermes_dashboard.py` as dead/zero-test:
  `plugins/exo-command/{costs,projects}.py` import `ProfileDb`/`ro` and 5
  plugin test files import it (test_costs, kanban, readonly, projects,
  agents). Live code.
- Do NOT delete the `websockets` dependency as unused: `bus/relay.py:205`
  `@app.websocket` needs the uvicorn ws implementation (a dependency of the
  `bus` extra, `pyproject.toml:23`) and `plugins/exo-command/tests/browser.py:97` imports
  `websockets.sync.client`. At most relocate to dev after a live
  websocket handshake test proves the need precisely.
- Do NOT parallelize loop tool calls (`loop.py:529`): serial order
  preserves ordering/interrupt/spend semantics; `asyncio.gather` changes
  side-effect order. That is a behavior change, not an optimization.
- Do NOT delete sym2p v1: `sym2p/__init__.py` validate dispatch still
  serves 1.0 validation. Add the conformance test (W3-6) instead.
- Do NOT split the `ProviderEvent` union / generic `Command[T]` as an
  optimization: API redesign, risk high. Needs its own design decision.
- Do NOT delete any "unused" public name anywhere in the program (Hermes
  regression mode). Dynamic surfaces falsify name-count proof: ingest
  `ADAPTERS` via `import_module`, `claude_cli_mcp.py` via
  `Path(__file__).with_name`, schema `@command/@event` decorators, engine
  `getattr` dispatch. Any dead-code tool needs a whitelist for these.
- Do NOT bulk-rewrite exception/control semantics (Hermes `suppress()`
  regression, ~65 sites). Exo has 16 `suppress()` sites, 135
  `except Exception` — per-site review only.
- Do NOT apply a >=30% LOC-cut target (see header). Do NOT merge the three
  wire layers (`protocol/`, `protocols/sym2p/`, `wire/`) — rename/document
  only. Do NOT widen mypy targets and shrink the baseline in the same PR.
- D-077 and D-083: the core budget is a reported count over a floor with no
  ceiling (dropped 2026-10-27), and the >1,000-line flag is information only. Every CORE unit states its counted-line cost and
  ratchets `CORE_FLOOR` down when it reduces it. A failing per-file size gate
  would fail on main today (`gate.py`, `consolidate.py`, `core/context.py`,
  TUI `app.py`), so W3-5 ships a report, not a gate, unless a ruling amends
  D-077.
- D-040: the ledger head anchor is the answer to tail deletion. Nothing in
  this program weakens it without a ruling (W3-3).
- Gate fail-closed: `_classify_program` ends in an opaque result for a
  program it does not know. The W3-2 handler table must return that same
  result on a miss and keep the old precedence order; the W0-3 golden corpus
  is what proves it.
- Ledger anchor batching (W3-3) and the gate split (W3-2) are the only
  high-risk units; both stay behind facade/crash/golden gates above, and
  W3-3 also waits for a ruling.
  `core/types.py` Provider seam is freeze/additive-only except the W1-1
  `_decode` fix and W3-1 ports.

## 6. Agent count and sequencing

Total work units: 26 (Wave 0: 5, Wave 1: 8, Wave 2: 7, Wave 3: 6) + 1
non-editing orchestrator + 1-2 reviewers per wave (Hermes needed 2 review
rounds; budget an adversarial review after Waves 2 and 3).

- Wave 0: 5 agents in parallel (all new files / additive CI). Sequencing:
  W0-1 first (baseline), rest concurrent. Gate: baselines committed.
- Wave 1: 8 agents in parallel, disjoint files, minus any unit whose files an open PR owns (section 1b). Depends on W0-1/W0-3.
- Wave 2: 7 agents in parallel. Depends on Wave 1 merged (sessions.py,
  cli.py, deploy script touched in both). W2-2 hard-blocked on W0-3.
- Wave 3: 6 units but max ~4 concurrent; W3-2 (gate) and W3-3 (ledger)
  run near-serially with dedicated review. W3-1 plugin migration is
  serial per plugin dir (6 sub-branches, one at a time).

Peak concurrency ~8 workers + orchestrator + reviewer, inside the 8-12
group sizing evidence supports (vs Hermes 218 peak for a 27x larger
codebase). Hard cap 16; shared single mypy/ruff at integration
(Hermes: ~30 Pyright copies = 8.7GB). Every worker: <=1 unverified step,
commit + handoff note per verified step, survives kill-and-resume drill.
Sequencing summary: W0 -> W1 -> W2 -> adversarial review -> W3 ->
adversarial review -> codify per-area size/complexity rules (W3-5) so the
gains do not regress.

## 7. Unresolved gaps from the audit

Adversarial critic gap verdicts:

- VERDICT gap | security/crypto/concurrency | no lens audited threat model, gate bypass correctness beyond LOC, ledger_authority/witness crypto, race/idempotency of bus+EventBus subscribers, or plugin-internal (19k LOC sampled only) and skills/ scripts (9k LOC)
- VERDICT gap | evidence quality | no lens ran pytest/benchmarks/live prod sizes (all read-only estimates); CI durations, coverage %, real ~/.exo DB sizes, live Vercel headers unverified — every speed claim needs baseline run before Wave work

Per-lens unresolved items, verbatim:

- Runtime import-cycle probe not run (exo not installed in system python; uv env not invoked read-only) - cycle evidence is static lazy-import count only
- site/ (static site) and research/, patches/ excluded from module graph per ruff extend-exclude; not audited
- Per-plugin internal graphs (exo-command etc.) sampled via import counts only, not file-by-file
- No runtime benchmarks run (read-only audit): split payoff is LOC/churn/branch-based, not profiled
- Vendored/generated code inspected but excluded from split ranking: src/exo/protocol/generated/protocol.schema.json (2091 LOC), protocol/schema.py (729 LOC/65 classes at 30-command cap), core/context.py lifted prompt text (file 1018 LOC)
- Plugin internals (plugins/, 19.1k LOC across 6 plugins) only sampled: their Hermes-host entry points make name-count dead-code proof unsafe without tracing host loading; core src+clients lens is covered, plugin-internal duplication is not
- ruff binary absent in checkout .venv (uv run would provision it, not executed read-only beyond CI definition), so current unused-import state is inferred from AST/grep, not a ruff run
- site/media/exo-banner.svg (68KB, 0 references in site HTML) not checked against README/GitHub social-preview usage, so not claimed as a finding
- websockets may be uvicorn's runtime WebSocket implementation for bus/relay.py FastAPI WebSocket routes despite zero imports; needs a live websocket handshake test (section 5) before removal
- No live profiling/benchmarks run (read-only audit, no long-session fixture executed); impacts are code-measured query/scan counts, savings need the listed benchmarks to confirm
- No builds/deploys run (read-only): actual wheel/sdist contents, CI wall-times, and Lighthouse scores unverified — sizes are source/venv measured estimates
- Local .venv is py3.14 with extra local pkgs (py-spy 4.5MB) not in uv.lock; CI py3.11/3.13 venv sizes may differ slightly
- Whether astral-sh/setup-uv@v5 enables cache by default not confirmed from repo files alone
- No live ~/.exo store/ledger/sessions DB inspected (sizes in production unknown); payload sizes inferred from code limits, not traffic capture.
- clients/tui, plugins/, site/ network/state not deep-audited; focus was src/exo data paths.
- plugins/* bodies (19132 LOC, Any 260, except Exception 72, type:ignore 4) counted but not individually opened - outside mypy targets so no baseline to rank per-file findings
- site/ JS/HTML (4270 LOC) and templates/skills/research not type-audited (no JS type tooling defined in repo)
- Did not run mypy/pytest (read-only, would need uv sync); baseline counts taken from committed .github/mypy-baseline.json, _decode probe run read-only via system python
- CI job durations and slowest/flakiest tests: not recorded in repo (no --durations/junit artifacts); requires running suite or Actions history, excluded by read-only/no-full-suite constraint
- Actual line/branch coverage % per module: unknown, no coverage tooling configured and suite not run
- site/ (static HTML/JS) and clients/ beyond Python TUI: no JS test/lint harness found in inspected configs; JS-side gates unverified
- hermes_dashboard.py live-vs-dead status: the lens found no test imports; superseded by section 5 (5 test files and two plugin modules import it, so it is live)
- Live-site headers/compression (Brotli, actual Vercel cache-control) not probed — audit is repo-only; verify with curl -I https://exo.askaconsult.com/assets/style.css
- Real-user LCP/CLS and WebP savings % are estimates from byte counts, not measured in a browser (read-only audit, no tools installed)
