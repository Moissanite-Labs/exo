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

The blocking CI gate should use the **good** trace command above (exit 0). Pytest covers `b1_silent_fail.jsonl` (exit 1) and `malformed.jsonl` (exit 2) separately.

Optional: `TLA2TOOLS_JAR=/path/to/tla2tools.jar` (otherwise `specs/seat-binding/tla2tools.jar`).

Download pin (SHA-256):

```bash
curl -fsSL -o specs/seat-binding/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar
# e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf
```

## Runtime

On this VM, `--all` over the three sample traces finishes in about 1–2 seconds once Java and the jar are present. CI should allow roughly one minute for checkout, jar cache, and replay.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Trace conforms: every step is an allowed `SeatBinding` action and configured invariants hold. |
| 1 | Violation: TLC reported a failed invariant or an unreplayable step. stderr includes `VIOLATION <path> seq=N invariant=Name`. |
| 2 | Malformed trace: JSONL/schema validation failed before TLC. |
| 3 | Missing toolchain: Java not on `PATH`, jar missing, or jar SHA-256 mismatch. One-line message on stderr. |

`replay.py` never skips checks. Local pytest may skip when Java is absent.

With `--all`, every `*.jsonl` in the directory is checked, all failures are printed, and the process exits with the worst code (`3` > `2` > `1` > `0`).

## Path filter

Paste into a workflow `paths:` block or `dorny/paths-filter` `paths` list:

```
specs/seat-binding/**
src/exo/core/engine.py
src/exo/core/loop.py
src/exo/core/sessions.py
src/exo/compose.py
clients/tui/exo_tui/app.py
clients/tui/exo_tui/conn.py
src/exo/protocol/schema.py
src/exo/server/app.py
tests/clients/tui/test_seat_new_session.py
```

## Artifacts

- `TraceSeatBinding.tla` — trace replay spec (extends `SeatBinding` + generated `TraceInstance`).
- `replay.py` — validates JSONL, generates `TraceInstance.tla`, runs TLC.
- Sample traces under `conformance/traces/`.

Do not edit `.github/workflows` from this PR; wire the gate in a follow-up.
