# Seat binding (TLA+)

Formal model of how a **seated core** binds `session.create` to its loaded seat, how the TUI **resume** path copies a thread’s seat into the display label, and how **`/new`** must keep using the window’s launch seat (exo #939 bug B1, fix in commit `5ba3813` on `feat/bot-profile-foundation`).

## What is modeled

- **Core loaded seat** (`coreSeat`): the `Seat` passed to `CoreEngine` / `exo serve --seat` (`src/exo/compose.py`, `CoreEngine.__init__` in `src/exo/core/engine.py`).
- **Sessions** (`liveSessions`, `sessionSeat`): rows in the shared home store; `foreignSessions` marks threads created before this core instance loaded its seat (another core or an unseated engine), matching shared `sessions.sqlite` in `docs/SEATS.md`.
- **Client launch vs display seat** (`clientLaunch`, `clientDisplay`): `ExoApp.launch_seat` and `self.seat` (`clients/tui/exo_tui/app.py`).
- **Outcomes** (`lastOutcome`): whether a create/resume/`/new` produced a visible result, a core refusal, a join notice, or (pre-fix) a silent failure.

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
| `ClientNew` | `/new` command | `ExoApp.new_session` (`clients/tui/exo_tui/app.py`); pre-fix uses `self.seat`, post-fix uses `launch_seat` and join fallback |
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

- `PreFix = TRUE` — post `5ba3813` TUI (`SeatBinding.cfg`).
- `PreFix = FALSE` — pre-fix TUI sends `self.seat` on `/new` (`SeatBinding_prefix.cfg`).

## Invariants

1. **SeatMatchesCore** — every non-foreign live session’s seat equals `coreSeat` when the core is seated.
2. **NoSilentRefusal** — `lastOutcome` is never `"SilentFail"` (pre-fix `/new` after a mismatched seat request).
3. **NewAfterResumeBindsThisCore** — after resuming another seat’s thread, a successful `/new` binds the new session to this core’s seat (regression in `tests/clients/tui/test_seat_new_session.py::test_new_after_resuming_another_seat_uses_this_core`).

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

Recorded outputs: `results/fixed.txt` (post-fix, all invariants pass), `results/prefix-counterexample.txt` (pre-fix, violates **NoSilentRefusal**).

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
3. After each step, evaluate **SeatMatchesCore**, **NoSilentRefusal**, and **NewAfterResumeBindsThisCore** (with `PreFix` set from build/version).

Property tests can later assert byte-identical approval text separately; this spec does not cover approvals.
