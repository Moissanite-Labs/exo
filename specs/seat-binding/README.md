# Seat binding (TLA+)

Formal model of how a **seated core** binds `session.create` to its loaded seat, how the TUI **resume** path copies a thread’s seat into the display label, and how **`/new`** must keep using the window’s launch seat (exo #939 bug B1, fix in commit `5ba3813` on `feat/bot-profile-foundation`).

## Derivation from the failing trace

**Observable #939 B1 trace (what the user did and saw):**

1. A **worker** core is running (seat **X** = `worker`) in a home that already has a **scout** thread from an earlier unseated core or another process.
2. The user opens the TUI attached to that worker core (no `--seat`, or a launch seat that is not scout).
3. They **resume** the scout thread. The UI label shows **Y** = `scout` (copied from the resumed session).
4. They run **`/new`**. **Nothing useful happens**: no new worker thread, no error line in the transcript (`bad_request` absent in the regression test), the session id unchanged — a silent failure.

**User-facing expectation after step 4:** `/new` starts a **fresh thread of this core’s seat** (worker), same memory, with a visible outcome (new thread notice or an explicit refusal/join message), not a no-op.

From that trace alone:

| Invariant | Derived from observation | Code mapping (after derivation) |
|-----------|-------------------------|----------------------------------|
| **NewAfterResumeBindsThisCore** | After step 3, the client has resumed another seat’s thread (`resumedOtherSeat`). Step 4 must not leave the user on the old scout focus with no explanation; `/new` must end in a **visible refusal** or a **session bound to the core seat** (worker). | `ExoApp.new_session` + `CoreRuntime._session_create`; regression `test_new_after_resuming_another_seat_uses_this_core`. |
| **NoSilentRefusal** | Step 4 produced **no user-visible outcome** while the core rejected the create (wrong seat name sent) — that is exactly “nothing happens”. Any refused create must surface as refusal/join/error, not `SilentFail`. | Pre-fix `new_session` did not handle `ClientError`; post-fix notes or retries. |
| **SeatMatchesCore** | ⚠ **Justified primarily from product/docs**, not from the B1 user trace alone: the user did not report corrupt DB rows, but exo policy says sessions created on a seated core must carry that core’s seat unless they are pre-existing foreign rows. | `engine._session_create` seat normalization; `foreignSessions` in the model. |

Only **SeatMatchesCore** is flagged as relying on code/docs justification in addition to the failing trace; the other two are direct formalizations of what B1 violated.

## What is modeled

- **Core loaded seat** (`coreSeat`): the `Seat` passed to `CoreEngine` / `exo serve --seat` (`src/exo/compose.py`, `CoreEngine.__init__` in `src/exo/core/engine.py`).
- **Sessions** (`liveSessions`, `sessionSeat`): rows in the shared home store; `foreignSessions` marks threads created before this core instance loaded its seat (another core or an unseated engine), matching shared `sessions.sqlite` in `docs/SEATS.md`.
- **Client launch vs display seat** (`clientLaunch`, `clientDisplay`): `ExoApp.launch_seat` and `self.seat` (`clients/tui/exo_tui/app.py`).
- **Outcomes** (`lastOutcome`): whether a create/resume/`/new` produced a visible result, a core refusal, a join notice, or (pre-fix) a silent failure.
- **`fromClientNew`**: true on the state immediately after a `ClientNew` step (used by **NewAfterResumeBindsThisCore**).

## What is abstracted away

Turn execution, models, permissions, event sequencing, HTTP vs in-process transport, writer locks, and multiple simultaneous clients. **Reconnect** is a stutter step (`clients/tui/exo_tui/conn.py` `events(reconnect=True)`): it does not change seats or sessions in this model.

## Action map (TLA+ → code)

| TLA+ action | Real behavior | Location |
|-------------|---------------|----------|
| `LoadCoreSeat` | Load seat profile when starting core | `src/exo/compose.py` (`load_seat`), `CoreEngine(..., seat=...)` |
| `ClientBoot` | TUI startup records `--seat` | `clients/tui/exo_tui/app.py` (`launch_seat`, `self.seat`) |
| `PersistSession` | Session row already in DB from another core / unseated engine | `SessionStore.create_session` via another `CoreEngine` |
| `SessionCreate` | `session.create` RPC | `CoreRuntime._session_create` → `loop.create_session` (`src/exo/core/engine.py`, `src/exo/core/loop.py`, `src/exo/core/sessions.py`) |
| `ResumeSession` | `load_session` / `session.resume` | `ExoApp.load_session` (`clients/tui/exo_tui/app.py`), `CoreRuntime._session_resume` |
| `ClientNew` | `/new` command | `ExoApp.new_session` (`clients/tui/exo_tui/app.py`); `Fixed = FALSE` uses `self.seat`, `Fixed = TRUE` uses `launch_seat` and join fallback |
| `Reconnect` | Event stream reconnect (no seat change) | `LocalClient.events(..., reconnect=True)` in `clients/tui/exo_tui/conn.py` |

Seat normalization and refusal on a seated core:

```562:572:src/exo/core/engine.py
    async def _session_create(self, b: s.SessionCreate) -> dict[str, Any]:
        ...
        seat = b.seat  # an unseated core keeps whatever the client asked for (""/None included)
        if self.seat is not None:
            seat = b.seat or self.seat.name
            if seat != self.seat.name:
                raise CommandFailed(
                    "bad_request", f"this core runs the {self.seat.name!r} seat; it cannot start a thread for seat {seat!r}"
                )
```

(Exact lines on `feat/bot-profile-foundation`; same logic described in `docs/SEATS.md`.)

## Constants

- `Fixed = TRUE` — post `5ba3813` TUI behavior (`SeatBinding.cfg`).
- `Fixed = FALSE` — pre-fix TUI sends the display label (`self.seat`) on `/new` (`SeatBinding_prefix.cfg`).

## Invariants

1. **SeatMatchesCore** — every non-foreign live session’s seat equals `coreSeat` when the core is seated.
2. **NoSilentRefusal** — `lastOutcome` is never `"SilentFail"` (pre-fix `/new` when the core rejects the requested seat leaves no visible outcome).
3. **NewAfterResumeBindsThisCore** — when `resumedOtherSeat` is true and the last step was `/new` (`fromClientNew`), the step ends with either a **visible refusal** (`lastOutcome = "VisibleRefusal"`) or a **live session bound to `coreSeat`**. This matches exo #939 B1: after resuming another seat’s thread, `/new` must not silently fail; regression in `tests/clients/tui/test_seat_new_session.py::test_new_after_resuming_another_seat_uses_this_core`.

## Pre-fix counterexample (`SeatBinding_prefix.cfg`)

TLC reports **NoSilentRefusal** first on the shortest trace (worker core, client boot with `scout`, `/new` with no resume): `lastOutcome = "SilentFail"`.

The **#939 B1** path appears in the second section of `results/prefix-counterexample.txt` (same model, checking only **NewAfterResumeBindsThisCore**): `PersistSession("scout")` → `ResumeSession` → `ClientNew` with `resumedOtherSeat = TRUE` and `fromClientNew = TRUE`. Pre-fix `/new` sets `"SilentFail"`, which is neither a visible refusal nor a new session on `coreSeat`, so **NewAfterResumeBindsThisCore** fails. That is the same failure mode as the regression test (no new worker thread, no user-visible error).

## Run TLC

From this directory:

```bash
curl -fsSL -o tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar

java -XX:+UseParallelGC -cp tla2tools.jar tlc2.TLC -config SeatBinding.cfg SeatBinding.tla
java -XX:+UseParallelGC -cp tla2tools.jar tlc2.TLC -config SeatBinding_prefix.cfg SeatBinding.tla
```

Optional (Apalache):

```bash
apalache-mc check --init=Init --next=Next --inv=SeatMatchesCore SeatBinding.tla
```

(Apalache needs its own config; TLC is the checked-in reference.)

Recorded outputs: `results/fixed.txt` (`Fixed = TRUE`, all invariants pass), `results/prefix-counterexample.txt` (`Fixed = FALSE`, see **Pre-fix counterexample** above).

## Conformance plan (trace replay — not implemented)

Goal: replay exo session lifecycle traces and check each step is an allowed TLA+ action and invariants hold on the reconstructed state.

**Trace fields (per step):**

| Field | Meaning |
|-------|---------|
| `t` | Monotonic timestamp or seq |
| `action` | One of: `load_core_seat`, `client_boot`, `session_create`, `session_resume`, `client_new`, `session_refused`, `reconnect` |
| `core_seat` | Loaded seat name or null |
| `request_seat` | Seat argument on create (null = omitted) |
| `session_id` | Target session |
| `session_seat` | Seat stored on session after step |
| `client_launch_seat` | Window `--seat` |
| `client_display_seat` | TUI label seat |
| `outcome` | `ok`, `visible_refusal`, `visible_join`, `silent_fail`, `stutter` |

**Where exo would emit (future):**

- `load_core_seat` — core startup / `compose.create` after `load_seat`.
- `session_create` / `session_refused` — `CoreRuntime._session_create` success or `CommandFailed`.
- `session_resume` — `_session_resume` and TUI `load_session`.
- `client_boot` / `client_new` — `ExoApp.__init__` and `new_session`.
- `reconnect` — `conn.py` event pump reconnect loop.

**Check:**

1. Maintain model state variables mirroring the TLA+ module.
2. For each trace row, assert the corresponding action is enabled and matches the row’s outcome.
3. After each step, evaluate **SeatMatchesCore**, **NoSilentRefusal**, and **NewAfterResumeBindsThisCore** (with `Fixed` set from build/version).

Property tests can later assert byte-identical approval text separately; this spec does not cover approvals.
