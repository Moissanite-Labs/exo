# Seat-binding trace replay (CI gate interface)

## Command

From the repository root (Java 21+ on `PATH`):

```bash
python3 specs/seat-binding/conformance/replay.py specs/seat-binding/conformance/traces/good_new_after_resume.jsonl
```

Batch (every `*.jsonl` in a directory; exits with the worst code — includes negative fixtures under `traces/`):

```bash
python3 specs/seat-binding/conformance/replay.py --all specs/seat-binding/conformance/traces
```

The blocking CI gate must invoke **`replay.py` directly** (not rely on pytest alone). Install Java 21+ on `PATH`, fetch and verify the pinned jar, then run replay. A missing toolchain is **exit 3** and must fail the job.

### CI gate setup (required before `replay.py`)

Pinned release: **tla2tools v1.8.0**

- URL: `https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar`
- SHA-256: `7a0a1e90628ebff2e3e8733375f8b684fa59504d2e02bddc4cb51a7679bb7e04`

Example (from repository root):

```bash
JAR=specs/seat-binding/tla2tools.jar
curl -fsSL -o "$JAR" \
  https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar
echo "7a0a1e90628ebff2e3e8733375f8b684fa59504d2e02bddc4cb51a7679bb7e04  $JAR" | sha256sum -c -
export TLA2TOOLS_JAR="$PWD/$JAR"
python3 specs/seat-binding/conformance/replay.py specs/seat-binding/conformance/traces/good_new_after_resume.jsonl
```

If `TLA2TOOLS_JAR` is unset, `replay.py` defaults to `specs/seat-binding/tla2tools.jar` (same pin and checksum as in `replay.py`).

Local optional install (same pin):

```bash
curl -fsSL -o specs/seat-binding/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar
# 7a0a1e90628ebff2e3e8733375f8b684fa59504d2e02bddc4cb51a7679bb7e04
```

## Runtime

On this VM, `--all` over the sample traces finishes in about 1–2 seconds once Java and the jar are present. CI should allow roughly one minute for checkout, jar cache, and replay.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Trace conforms: every step is an allowed `SeatBinding` action and configured invariants hold. |
| 1 | Violation: TLC reported a failed invariant or an unreplayable step. stderr includes `VIOLATION <path> seq=N invariant=Name`. |
| 2 | Malformed trace: JSONL/schema validation failed before TLC (includes header-only files and impossible event order such as `resume` before `client_boot`). |
| 3 | Missing toolchain: Java not on `PATH`, jar missing, or jar SHA-256 mismatch. One-line message on stderr. |

### `--diagnose` (diagnostic only)

Optional flag for local debugging and CI log triage. **Not part of the blocking gate** — do not pass it in the wired CI job from exo #1152.

When `--diagnose` is set, violation lines gain a `diagnosed=` suffix. `replay.py` re-runs the same trace with `Fixed=FALSE` and sets `diagnosed=` to a policy invariant (`NoForeignBeforeCoreLoad`, `NewAfterResumeBindsThisCore`, or `NoSilentRefusal`) **only when TLC reports that invariant on the re-run at or before the primary failure’s JSONL `seq`** (the `seq=` on the `VIOLATION` line, including `TraceReplay` deadlocks where TLC `step` is one less). Violations on later events are ignored. Otherwise `diagnosed=none`. Exit codes and default output without the flag are unchanged.

Example:

```bash
python3 specs/seat-binding/conformance/replay.py --diagnose specs/seat-binding/conformance/traces/dropped_persist.jsonl
# VIOLATION ... seq=1 invariant=TraceReplay diagnosed=none
```

`replay.py` never skips checks.

**Pytest (`tests/specs/test_replay.py`) skips when Java or the pinned jar is unavailable** (missing file or checksum mismatch), so default CI jobs without the jar stay green. That is intentional: pytest is not the conformance gate. The wired CI gate must fetch/verify the jar, set `TLA2TOOLS_JAR`, and run **`replay.py` directly** so missing Java/jar fails with exit **3**.

With `--all`, every `*.jsonl` in the directory is checked, all failures are printed, and the process exits with the worst code (`3` > `2` > `1` > `0`).

## Path filter

The **spec conformance** workflow always runs on pull requests and merge groups (#1156). The list below is the historical scope filter (removed from the workflow); keep it aligned with seat-binding-related paths when documenting touch surfaces:

```
specs/seat-binding/
src/exo/core/engine.py
src/exo/core/loop.py
src/exo/core/sessions.py
src/exo/core/seats.py
src/exo/compose.py
clients/tui/exo_tui/app.py
clients/tui/exo_tui/conn.py
src/exo/protocol/schema.py
src/exo/server/app.py
tests/clients/tui/test_seat_new_session.py
tests/test_session_seat_binding.py
tests/test_seat_binding_conformance_traces.py
src/exo/core/seat_binding_conformance.py
.github/workflows/spec-conformance.yml
```

## Artifacts

- `TraceSeatBinding.tla` — trace replay spec (extends `SeatBinding` + generated `TraceInstance`).
- `replay.py` — validates JSONL, generates `TraceInstance.tla`, runs TLC.
- `check_scratch_mutations.py` — temp-copy production mutation harness (pinned `VIOLATION` seq+invariant; wired in spec-conformance workflow).
- Sample traces under `conformance/traces/`.
