## Summary

Stacked on #1152 (`mithrax/spec-conformance-ci-4652`). Adds a test-only seat-binding conformance recorder wired into the real persist, load-seat, session-create/refusal, and TUI boot/resume/`/new` paths. CI emits JSONL traces from pytest, validates them against `trace.schema.json`, then replays emitted traces together with static `good_*.jsonl` fixtures through `replay.py --all` (nonzero exit fails the job). Negative fixture exit-code checks from #1152 are unchanged.

## What changed

- `src/exo/core/seat_binding_conformance.py` — global recorder (no-op when unset; one branch check at each hook).
- `src/exo/core/sessions.py` — after each real session row insert, notify recorder (`persist_session` vs `session_create` decided from core load + command context).
- `src/exo/core/engine.py` — `load_seat` on seated `CoreEngine` init; `session_create` / `refusal` around `_session_create`.
- `clients/tui/exo_tui/app.py` — `client_boot` at TUI connect, `resume` after `load_session`, `client_new` after `/new` (with `client_new_trace` so core inserts map to the TLA `ClientNew` event, not `SessionCreate`).
- `tests/test_seat_binding_conformance_traces.py` — drives `test_new_after_resuming_another_seat_uses_this_core` (boot resumes scout via `session=scout_id`) and seated refusal; writes traces under `EXO_SEAT_BINDING_TRACE_DIR` in CI; validates schema + runs `replay.py` per file when Java/jar present.
- `.github/workflows/spec-conformance.yml` — Python setup, emission pytest step, merge emitted `*.jsonl` into replay batch.

## Event capture (real call sites)

| Trace event | Hook location |
|-------------|----------------|
| `persist_session` | `SessionStore._new_session` → `maybe_note_session_row` when core not loaded (or cross-seat persist after load) |
| `load_seat` | `CoreEngine.__init__` after seat assignment |
| `session_create` / `refusal` | `CoreRuntime._session_create` (+ store row under `session_create_trace`) |
| `client_boot` | `ExoApp.on_mount` after successful `Hello` |
| `resume` | `ExoApp.load_session` after session payload applied |
| `client_new` | `ExoApp.new_session` after successful create (store insert suppressed as separate event via `client_new_trace`) |

Production behavior is unchanged when no recorder is active.

## Mutation evidence (scratch only — not on this branch)

### (a) Revert 5ba3813 (pre-fix `/new` sends display seat)

Scratch edits only: `clients/tui/exo_tui/app.py` uses `seat=self.seat` on `SessionCreate` again; seated-core refusal in `_session_create` disabled so the trace reaches a violating binding.

Emission from the new-after-resume scenario, then:

```
VIOLATION /tmp/exo-mutation-scratch-3187-a/traces/new_after_resume.jsonl seq=5 invariant=TraceReplay
```

Final emitted post-state binds the new session to **scout** on a **worker** core after `resumed_other_seat` + `client_new` — the regression 5ba3813 fixed. TLC reports `TraceReplay` first because that post-state is not a legal `ClientNew` step under `Fixed=TRUE`; the substantive gate invariant for this scenario is **`NewAfterResumeBindsThisCore`**.

### (b) Drop `PersistSession` foreign guard

Scratch edit only: in `seat_binding_conformance.note_session_row`, mark persisted rows foreign even when `core_seat` is still unset (drops the `NoForeignBeforeCoreLoad` guard on persist).

```
VIOLATION traces/new_after_resume.jsonl seq=1 invariant=TraceReplay
```

Seq 1 post-state has `foreign: true` while `core_seat` is null — exactly the state **`NoForeignBeforeCoreLoad`** forbids; TLC fails replay at the same step because `PersistSession` with the guard cannot produce that post-state.

### Restore + green gate

This PR branch restores correct code. Local gate simulation (emit + replay good + emitted + negative fixtures): ~**5–8 s** added wall time beyond the existing replay-only step (dominated by `pip install -e ".[dev,tui]"` and TLC on three conforming traces). Green gate: https://github.com/Moissanite-Labs/exo/actions/runs/41897687531/job/126811486817

## Format gaps

None observed for current scenarios; all emitted lines validate against `trace.schema.json` and replay with exit 0 on this branch.

> Automated posting by agentic team with human oversight.
