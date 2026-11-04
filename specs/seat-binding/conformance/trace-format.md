# Seat-binding trace format (JSONL)

One JSON object per line (JSONL). Each line is one step; `state` is the **observed model state after** that step’s event (including `initial` at `seq` 0).

## Required fields

| Field | Spec variable / role | Emission point (future) |
|-------|----------------------|-------------------------|
| `schema_version` | — | trace writer |
| `run_id` | groups one replay run | trace writer |
| `seq` | monotonic step index (0 = initial snapshot) | trace writer |
| `timestamp` | ordering / audit | trace writer |
| `actor` | `core`, `client`, or `system` | who initiated the event |
| `event` | maps to `SeatBinding` action (see below) | see per-event |
| `fixed` | `Fixed` constant (`true` = post-5ba3813 TUI) | build / client version |
| `params` | action arguments | per event |
| `state.core_seat` | `coreSeat` | after `CoreEngine` start / `load_seat` |
| `state.client_launch_seat` | `clientLaunch` | `ExoApp.__init__` / `client_boot` |
| `state.client_display_seat` | `clientDisplay` | after resume or boot (`app.py`) |
| `state.client_session` | `clientSession` | active thread id |
| `state.sessions[]` | `liveSessions`, `foreignSessions`, `sessionSeat` | `SessionStore` / resume / create |
| `state.last_outcome` | `lastOutcome` | create / resume / `/new` handlers |
| `state.user_visible_outcome` | transcript-level outcome (not in TLA yet) | `ExoApp.note`, errors |
| `state.resumed_other_seat` | `resumedOtherSeat` | after `load_session` |
| `state.from_client_new` | `fromClientNew` | after `/new` |
| `state.next_sid` | `nextSid` | allocator in model / store |

## Events → TLA+ → code

| `event` | TLA+ action | Params | Emit from |
|---------|-------------|--------|-----------|
| `initial` | (snapshot only) | — | trace start |
| `load_seat` | `LoadCoreSeat` | `seat` | `src/exo/compose.py` `create` / `CoreEngine.__init__` |
| `persist_session` | `PersistSession` | `seat`, `session_id` | foreign row in `sessions.sqlite` |
| `session_create` | `SessionCreate` | `request_seat` | `CoreRuntime._session_create` |
| `resume` | `ResumeSession` | `session_id` | `ExoApp.load_session`, `_session_resume` |
| `client_boot` | `ClientBoot` | `launch_seat` | `ExoApp.__init__` |
| `client_new` | `ClientNew` | — | `ExoApp.new_session` |
| `refusal` | `SessionCreate` → refusal outcome | `request_seat` | `_session_create` `CommandFailed` |
| `reconnect` | `Reconnect` | — | `clients/tui/exo_tui/conn.py` `events(reconnect=True)` |

## Outcome strings in `state.last_outcome`

Map to `SeatBinding.Outcome`: `NoOutcome`, `OkCreated`, `OkResumed`, `VisibleRefusal`, `VisibleJoin`, `SilentFail` (same spelling as the TLA strings).

## Validation

`conformance/replay.py` validates against `trace.schema.json` (required fields and enums), generates `TraceInstance.tla`, and runs TLC on `TraceSeatBinding.tla`.
