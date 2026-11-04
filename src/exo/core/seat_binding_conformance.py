"""Seat-binding conformance trace recorder (no-op when unset; test-only emission)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACE_POOL = ("s1", "s2", "s3")
SCHEMA_VERSION = 1

_active: SeatBindingRecorder | None = None


def active_recorder() -> SeatBindingRecorder | None:
    return _active


def set_recorder(recorder: SeatBindingRecorder | None) -> None:
    global _active
    _active = recorder


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class SeatBindingRecorder:
    """Records JSONL lines matching specs/seat-binding/conformance/trace.schema.json."""

    run_id: str
    path: Path
    fixed: bool = True
    seq: int = 0
    core_seat: str | None = None
    core_loaded: bool = False
    foreign_ids: set[str] = field(default_factory=set)
    live_ids: set[str] = field(default_factory=set)
    session_seats: dict[str, str] = field(default_factory=dict)
    id_map: dict[str, str] = field(default_factory=dict)
    client_launch_seat: str | None = None
    client_display_seat: str | None = None
    client_session: str | None = None
    resumed_other_seat: bool = False
    from_client_new: bool = False
    last_outcome: str = "NoOutcome"
    user_visible_outcome: str = "none"
    _session_create_depth: int = 0
    _client_new_depth: int = 0

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_line(self._line("initial", "system", {}, self._snapshot()))

    @classmethod
    def start(
        cls, run_id: str, path: Path, *, fixed: bool = True
    ) -> SeatBindingRecorder:
        rec = cls(run_id=run_id, path=path, fixed=fixed)
        set_recorder(rec)
        return rec

    def close(self) -> None:
        set_recorder(None)

    def trace_id(self, real_id: str) -> str:
        if real_id in self.id_map:
            return self.id_map[real_id]
        for slot in TRACE_POOL:
            if slot not in self.live_ids and slot not in self.id_map.values():
                self.id_map[real_id] = slot
                return slot
        raise RuntimeError(
            f"seat-binding trace session pool exhausted mapping {real_id!r}"
        )

    def note_load_core_seat(self, seat: str) -> None:
        self.core_seat = seat
        self.core_loaded = True
        self.foreign_ids |= set(self.live_ids)
        self._emit("load_seat", "core", {"seat": seat})

    def session_create_enter(self) -> None:
        self._session_create_depth += 1

    def session_create_exit(self) -> None:
        self._session_create_depth -= 1

    def note_session_row(
        self, *, seat: str | None, session_id: str, foreign: bool = False
    ) -> None:
        tid = self.trace_id(session_id)
        seat_name = seat or ""
        self.live_ids.add(tid)
        self.session_seats[tid] = seat_name
        if foreign:
            self.foreign_ids.add(tid)
        if self._client_new_depth > 0:
            return
        if self._session_create_depth > 0 and self.core_loaded:
            params = {"request_seat": seat if seat is not None else None}
            self.last_outcome = "OkCreated"
            self.from_client_new = False
            self.user_visible_outcome = "created"
            self._emit("session_create", "core", params)
            return
        if not self.core_loaded:
            params = {"seat": seat_name, "session_id": tid}
            self._emit("persist_session", "core", params)
            return
        params = {"seat": seat_name, "session_id": tid}
        self._emit("persist_session", "core", params)

    def note_refusal(self, request_seat: str | None) -> None:
        self.last_outcome = "VisibleRefusal"
        self.from_client_new = False
        self.user_visible_outcome = "refused"
        self._emit("refusal", "core", {"request_seat": request_seat})

    def note_client_boot(self, launch_seat: str | None) -> None:
        self.client_launch_seat = launch_seat
        if launch_seat:
            self.client_display_seat = launch_seat
        self._emit("client_boot", "client", {"launch_seat": launch_seat})

    def note_resume(self, session_id: str, *, session_seat: str | None) -> None:
        tid = self.id_map.get(session_id) or self.trace_id(session_id)
        self.client_session = tid
        seat_name = session_seat or self.session_seats.get(tid, "")
        if self.client_display_seat is None:
            self.client_display_seat = seat_name or None
        if self.core_seat and seat_name and seat_name != self.core_seat:
            self.resumed_other_seat = True
        self.last_outcome = "OkResumed"
        self.from_client_new = False
        self.user_visible_outcome = "resumed thread"
        self._emit("resume", "client", {"session_id": tid})

    def note_client_new(
        self,
        session_id: str,
        *,
        session_seat: str | None,
        joined: str | None = None,
    ) -> None:
        tid = self.trace_id(session_id)
        self.live_ids.add(tid)
        if session_seat:
            self.session_seats[tid] = session_seat
        self.client_session = tid
        self.from_client_new = True
        if joined is not None:
            self.last_outcome = "VisibleJoin"
            self.user_visible_outcome = "new thread"
        else:
            self.last_outcome = "OkCreated"
            self.user_visible_outcome = "new thread"
        self._emit("client_new", "client", {})

    def _next_sid(self) -> str:
        for slot in TRACE_POOL:
            if slot not in self.live_ids:
                return slot
        return TRACE_POOL[-1]

    def _sessions_array(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for slot in TRACE_POOL:
            if slot not in self.live_ids:
                continue
            rows.append(
                {
                    "id": slot,
                    "seat": self.session_seats.get(slot, ""),
                    "foreign": slot in self.foreign_ids,
                }
            )
        return rows

    def _snapshot(self) -> dict[str, Any]:
        return {
            "core_seat": self.core_seat,
            "client_launch_seat": self.client_launch_seat,
            "client_display_seat": self.client_display_seat,
            "client_session": self.client_session,
            "sessions": self._sessions_array(),
            "last_outcome": self.last_outcome,
            "user_visible_outcome": self.user_visible_outcome,
            "resumed_other_seat": self.resumed_other_seat,
            "from_client_new": self.from_client_new,
            "next_sid": self._next_sid(),
        }

    def _line(
        self, event: str, actor: str, params: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        line: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "seq": self.seq,
            "timestamp": _now_iso(),
            "actor": actor,
            "event": event,
            "fixed": self.fixed,
            "state": state,
        }
        if params:
            line["params"] = params
        return line

    def _emit(self, event: str, actor: str, params: dict[str, Any]) -> None:
        self.seq += 1
        self._write_line(self._line(event, actor, params, self._snapshot()))

    def _write_line(self, line: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")


def maybe_note_load_core_seat(seat: str | None) -> None:
    if seat and _active is not None:
        _active.note_load_core_seat(seat)


def maybe_note_session_row(
    *, seat: str | None, session_id: str, foreign: bool = False
) -> None:
    if _active is not None:
        _active.note_session_row(seat=seat, session_id=session_id, foreign=foreign)


def maybe_note_refusal(request_seat: str | None) -> None:
    if _active is not None:
        _active.note_refusal(request_seat)


def maybe_note_client_boot(launch_seat: str | None) -> None:
    if _active is not None:
        _active.note_client_boot(launch_seat)


def maybe_note_resume(session_id: str, *, session_seat: str | None) -> None:
    if _active is not None:
        _active.note_resume(session_id, session_seat=session_seat)


def maybe_note_client_new(
    session_id: str, *, session_seat: str | None, joined: str | None = None
) -> None:
    if _active is not None:
        _active.note_client_new(session_id, session_seat=session_seat, joined=joined)


def client_new_begin() -> None:
    if _active is not None:
        _active._client_new_depth += 1


def client_new_end() -> None:
    if _active is not None:
        _active._client_new_depth -= 1


class client_new_trace:
    """Mark SessionCreate calls that implement TUI ``/new`` (trace event client_new)."""

    def __enter__(self) -> client_new_trace:
        if _active is not None:
            _active._client_new_depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        if _active is not None:
            _active._client_new_depth -= 1


class session_create_trace:
    """Context manager: mark store inserts as SessionCreate while a command runs."""

    def __enter__(self) -> session_create_trace:
        if _active is not None:
            _active.session_create_enter()
        return self

    def __exit__(self, *exc: object) -> None:
        if _active is not None:
            _active.session_create_exit()
