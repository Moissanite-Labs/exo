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

The blocking CI gate must invoke **`replay.py` directly** (not rely on pytest alone). Install Java 21+ and the pinned `tla2tools.jar` in the job; a missing toolchain is **exit 3** and must fail the job.

Optional: `TLA2TOOLS_JAR=/path/to/tla2tools.jar` (otherwise `specs/seat-binding/tla2tools.jar`).

Download pin (SHA-256):

```bash
curl -fsSL -o specs/seat-binding/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar
# e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf
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

`replay.py` never skips checks.

**Pytest (`tests/specs/test_replay.py`) skips all replay tests when Java is absent**, so `pytest -q` can exit 0 with zero checks. That is intentional for local dev without Java. The wired CI gate must **not** use pytest as the sole check; run `replay.py` as above so missing Java/jar fails with exit 3.

With `--all`, every `*.jsonl` in the directory is checked, all failures are printed, and the process exits with the worst code (`3` > `2` > `1` > `0`).

## Path filter

Paste into a workflow `paths:` block or `dorny/paths-filter` `paths` list:

```
specs/seat-binding/**
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
```

## Artifacts

- `TraceSeatBinding.tla` — trace replay spec (extends `SeatBinding` + generated `TraceInstance`).
- `replay.py` — validates JSONL, generates `TraceInstance.tla`, runs TLC.
- Sample traces under `conformance/traces/`.

Do not edit `.github/workflows` from this PR; wire the gate in a follow-up.
