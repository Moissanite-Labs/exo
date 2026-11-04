#!/usr/bin/env python3
"""Validate seat-binding JSONL traces and replay them through TLC (TraceSeatBinding.tla)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPECS = ROOT.parent
SEAT_BINDING = SPECS / "SeatBinding.tla"
TRACE_TLA = ROOT / "TraceSeatBinding.tla"
TRACES_DIR = ROOT / "traces"

TLA2TOOLS_URL = "https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar"
TLA2TOOLS_SHA256 = "e20447644bfabd29d6bd0fc2c0eb1473fb301c1a0e4dfe1961938ce5a191edbf"
DEFAULT_JAR = SPECS / "tla2tools.jar"

SESSION_POOL = ("s1", "s2", "s3")
SEATS = ("worker", "scout")
OUTCOMES = {
    "NoOutcome",
    "OkCreated",
    "OkResumed",
    "VisibleRefusal",
    "VisibleJoin",
    "SilentFail",
}
EVENTS = {
    "initial",
    "load_seat",
    "persist_session",
    "session_create",
    "resume",
    "client_boot",
    "client_new",
    "refusal",
    "reconnect",
}


def _exit(code: int, msg: str | None = None) -> None:
    if msg:
        print(msg, file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


def toolchain_exit(message: str) -> None:
    _exit(3, message)


def ensure_toolchain(jar_path: Path) -> Path:
    if shutil.which("java") is None:
        toolchain_exit("missing toolchain: java not on PATH")
    if os.environ.get("TLA2TOOLS_JAR"):
        jar_path = Path(os.environ["TLA2TOOLS_JAR"]).resolve()
    if not jar_path.is_file():
        toolchain_exit(f"missing toolchain: tla2tools.jar not found at {jar_path}")
    digest = hashlib.sha256(jar_path.read_bytes()).hexdigest()
    if digest != TLA2TOOLS_SHA256:
        toolchain_exit(
            f"missing toolchain: tla2tools.jar checksum mismatch (expected {TLA2TOOLS_SHA256}, got {digest})"
        )
    return jar_path


def tla_str(value: str | None) -> str:
    if value is None or value == "":
        return "NoSeat" if "seat" in str(value) else "NoSession"
    if value in ("NoSeat", "NoSession"):
        return value
    return f'"{value}"'


def encode_seat(v: str | None) -> str:
    if v is None:
        return '"NoSeat"'
    return f'"{v}"'


def encode_session(v: str | None) -> str:
    if v is None:
        return '"NoSession"'
    return f'"{v}"'


def encode_bool(v: bool) -> str:
    return "TRUE" if v else "FALSE"


def encode_outcome(v: str) -> str:
    if v not in OUTCOMES:
        raise ValueError(f"invalid last_outcome {v!r}")
    return f'"{v}"'


def validate_line(obj: object, line_no: int) -> dict:
    if not isinstance(obj, dict):
        raise ValueError(f"line {line_no}: expected JSON object")
    required = (
        "schema_version",
        "run_id",
        "seq",
        "timestamp",
        "actor",
        "event",
        "fixed",
        "state",
    )
    for key in required:
        if key not in obj:
            raise ValueError(f"line {line_no}: missing field {key!r}")
    if obj["schema_version"] != 1:
        raise ValueError(f"line {line_no}: unsupported schema_version")
    if obj["actor"] not in ("core", "client", "system"):
        raise ValueError(f"line {line_no}: invalid actor")
    if obj["event"] not in EVENTS:
        raise ValueError(f"line {line_no}: invalid event")
    st = obj["state"]
    if not isinstance(st, dict):
        raise ValueError(f"line {line_no}: state must be object")
    for key in (
        "core_seat",
        "client_launch_seat",
        "client_display_seat",
        "client_session",
        "sessions",
        "last_outcome",
        "user_visible_outcome",
        "resumed_other_seat",
        "from_client_new",
        "next_sid",
    ):
        if key not in st:
            raise ValueError(f"line {line_no}: state missing {key!r}")
    if not isinstance(st["sessions"], list):
        raise ValueError(f"line {line_no}: state.sessions must be array")
    for sid in SESSION_POOL:
        pass
    return obj


def load_trace(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("empty trace file")
    records: list[dict] = []
    for i, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            raise ValueError(f"line {i}: blank line")
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {i}: invalid JSON ({exc})") from exc
        records.append(validate_line(obj, i))
    seqs = [r["seq"] for r in records]
    if seqs[0] != 0 or seqs != list(range(len(records))):
        raise ValueError("seq must start at 0 and increase by 1")
    if records[0]["event"] != "initial":
        raise ValueError("first event must be initial")
    fixed = records[0]["fixed"]
    if any(r["fixed"] != fixed for r in records):
        raise ValueError("fixed must be constant for the run")
    for r in records[1:]:
        if r["event"] == "initial":
            raise ValueError("only seq 0 may be initial")
    return records


def session_maps(state: dict) -> tuple[dict[str, str], set[str], set[str]]:
    seat_of = {s: "NoSeat" for s in SESSION_POOL}
    live: set[str] = set()
    foreign: set[str] = set()
    for row in state["sessions"]:
        sid = row["id"]
        if sid not in SESSION_POOL:
            raise ValueError(f"session id {sid!r} not in {SESSION_POOL}")
        seat_of[sid] = row["seat"]
        live.add(sid)
        if row.get("foreign"):
            foreign.add(sid)
    for sid in SESSION_POOL:
        if sid not in live:
            seat_of[sid] = "NoSeat"
    return seat_of, live, foreign


def seq_bool(name: str, values: list[bool]) -> str:
    return f"{name} == << {', '.join(encode_bool(v) for v in values)} >>"


def seq_str(name: str, values: list[str]) -> str:
    return f"{name} == << {', '.join(values)} >>"


def generate_trace_instance(records: list[dict]) -> str:
    posts = [r["state"] for r in records]
    events = records[1:]
    trace_len = len(events)

    post_core = [encode_seat(s["core_seat"]) for s in posts]
    post_launch = [encode_seat(s["client_launch_seat"]) for s in posts]
    post_display = [encode_seat(s["client_display_seat"]) for s in posts]
    post_client = [encode_session(s["client_session"]) for s in posts]
    post_resumed = [encode_bool(s["resumed_other_seat"]) for s in posts]
    post_from_new = [encode_bool(s["from_client_new"]) for s in posts]
    post_outcome = [encode_outcome(s["last_outcome"]) for s in posts]
    post_next = [f'"{s["next_sid"]}"' for s in posts]

    live_s = {sid: [] for sid in SESSION_POOL}
    foreign_s = {sid: [] for sid in SESSION_POOL}
    seat_s = {sid: [] for sid in SESSION_POOL}
    for st in posts:
        seat_of, live, foreign = session_maps(st)
        for sid in SESSION_POOL:
            live_s[sid].append(sid in live)
            foreign_s[sid].append(sid in foreign)
            seat_s[sid].append(
                encode_seat(None if seat_of[sid] == "NoSeat" else seat_of[sid])
            )

    kinds: list[str] = []
    ev_seat: list[str] = []
    ev_launch: list[str] = []
    ev_req: list[str] = []
    ev_sess: list[str] = []

    for ev in events:
        kinds.append(f'"{ev["event"]}"')
        params = ev.get("params") or {}
        ev_seat.append(encode_seat(params.get("seat")))
        ev_launch.append(encode_seat(params.get("launch_seat")))
        req = params.get("request_seat")
        ev_req.append(encode_seat(req))
        ev_sess.append(encode_session(params.get("session_id")))

    lines = [
        "---- MODULE TraceInstance ----",
        "EXTENDS Naturals",
        "",
        "EmptySet == {}",
        "",
        f"TraceLen == {trace_len}",
        seq_str("EventKind", kinds),
        seq_str("EventSeat", ev_seat),
        seq_str("EventLaunch", ev_launch),
        seq_str("EventReqSeat", ev_req),
        seq_str("EventSession", ev_sess),
        seq_str("PostCoreSeat", post_core),
        seq_str("PostClientLaunch", post_launch),
        seq_str("PostClientDisplay", post_display),
        seq_str("PostClientSession", post_client),
        seq_str("PostResumedOther", post_resumed),
        seq_str("PostFromClientNew", post_from_new),
        seq_str("PostLastOutcome", post_outcome),
        seq_str("PostNextSid", post_next),
    ]
    for sid in SESSION_POOL:
        lines.append(seq_bool(f"PostLive{sid}", live_s[sid]))
        lines.append(seq_bool(f"PostForeign{sid}", foreign_s[sid]))
        lines.append(seq_str(f"PostSeat{sid}", seat_s[sid]))

    lines.extend(
        [
            "",
            "PostLiveSet(i) ==",
            "  IF i < 1 \\/ i > TraceLen + 1 THEN EmptySet",
            '  ELSE {k \\in {"s1", "s2", "s3"} :',
            '       (k = "s1" /\\ PostLives1[i])',
            '       \\/ (k = "s2" /\\ PostLives2[i])',
            '       \\/ (k = "s3" /\\ PostLives3[i])}',
            "",
            "PostForeignSet(i) ==",
            "  IF i < 1 \\/ i > TraceLen + 1 THEN EmptySet",
            '  ELSE {k \\in {"s1", "s2", "s3"} :',
            '       (k = "s1" /\\ PostForeigns1[i])',
            '       \\/ (k = "s2" /\\ PostForeigns2[i])',
            '       \\/ (k = "s3" /\\ PostForeigns3[i])}',
            "",
            "PostSessionSeatFn(i) ==",
            "  [s1 |-> PostSeats1[i], s2 |-> PostSeats2[i], s3 |-> PostSeats3[i]]",
            "",
            "====",
            "",
        ]
    )
    return "\n".join(lines)


def write_cfg(build: Path, fixed: bool) -> None:
    (build / "Trace.cfg").write_text(
        f"""SPECIFICATION TraceSpec
CONSTANTS
  Seats = {{"worker", "scout"}}
  SessionPool = {{"s1", "s2", "s3"}}
  Fixed = {str(fixed).upper()}
  MaxSessions = 3
INVARIANTS
  TypeOK
  TraceStateOk
  SeatMatchesCore
  NoForeignBeforeCoreLoad
  NewAfterResumeBindsThisCore
  NoSilentRefusal
""",
        encoding="utf-8",
    )


def parse_violation(output: str) -> tuple[int | None, str | None]:
    inv = None
    m = re.search(r"Invariant (\w+) is violated", output)
    if m:
        inv = m.group(1)
    step = None
    for m in re.finditer(r"/\\ step = (\d+)", output):
        step = int(m.group(1))
    return step, inv


def run_tlc(build: Path, jar: Path) -> tuple[int, str, int | None, str | None]:
    cmd = [
        "java",
        "-XX:+UseParallelGC",
        "-cp",
        str(jar),
        "tlc2.TLC",
        "-config",
        "Trace.cfg",
        "TraceSeatBinding.tla",
    ]
    proc = subprocess.run(
        cmd,
        cwd=build,
        capture_output=True,
        text=True,
    )
    out = proc.stdout + proc.stderr
    if proc.returncode == 0 and "No error has been found" in out:
        return 0, out, None, None
    if "Deadlock reached" in out:
        step, _ = parse_violation(out)
        return 1, out, step, "TraceReplay"
    step, inv = parse_violation(out)
    if inv:
        return 1, out, step, inv
    if "Parsing or semantic analysis failed" in out or "Error:" in out:
        return 1, out, step, inv or "TraceReplay"
    return 1, out, step, inv


def check_trace(trace_path: Path, jar: Path, display_path: Path | None = None) -> tuple[int, str | None]:
    display = display_path or trace_path
    (ROOT / "build").mkdir(parents=True, exist_ok=True)
    try:
        records = load_trace(trace_path)
    except ValueError as exc:
        return 2, str(exc)

    with tempfile.TemporaryDirectory(prefix="seat-trace-", dir=ROOT / "build") as tmp:
        build = Path(tmp)
        build.mkdir(parents=True, exist_ok=True)
        shutil.copy(SEAT_BINDING, build / "SeatBinding.tla")
        shutil.copy(TRACE_TLA, build / "TraceSeatBinding.tla")
        (build / "TraceInstance.tla").write_text(generate_trace_instance(records), encoding="utf-8")
        write_cfg(build, records[0]["fixed"])
        code, out, step, inv = run_tlc(build, jar)
        if code == 0:
            return 0, None
        seq = step if step is not None else "?"
        inv_name = inv or "Unknown"
        rel = display
        try:
            rel = display.relative_to(Path.cwd())
        except ValueError:
            pass
        return 1, f"VIOLATION {rel} seq={seq} invariant={inv_name}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay seat-binding JSONL traces through TLC.")
    parser.add_argument("traces", nargs="*", type=Path, help="trace.jsonl files")
    parser.add_argument("--all", type=Path, help="directory of *.jsonl traces")
    parser.add_argument(
        "--jar",
        type=Path,
        default=DEFAULT_JAR,
        help="path to tla2tools.jar (default: ../tla2tools.jar or TLA2TOOLS_JAR)",
    )
    args = parser.parse_args(argv)

    jar_path = Path(os.environ["TLA2TOOLS_JAR"]) if os.environ.get("TLA2TOOLS_JAR") else args.jar
    try:
        jar = ensure_toolchain(jar_path)
    except SystemExit as exc:
        return int(exc.code)

    paths: list[Path] = list(args.traces)
    if args.all:
        paths.extend(sorted(args.all.glob("*.jsonl")))
    if not paths:
        print("usage: replay.py <trace.jsonl>... or --all <dir>", file=sys.stderr)
        return 2

    worst = 0
    messages: list[str] = []

    for path in paths:
        if not path.is_file():
            messages.append(f"malformed trace {path}: file not found")
            worst = max(worst, 2)
            continue
        code = 0
        msg: str | None = None
        try:
            code, msg = check_trace(path.resolve(), jar, path)
        except ValueError as exc:
            code, msg = 2, str(exc)
        if code == 2:
            messages.append(f"malformed trace {path}: {msg}")
        elif code == 1 and msg:
            messages.append(msg)
        worst = max(worst, code)

    for m in messages:
        print(m, file=sys.stderr if worst else sys.stdout)
    return worst


if __name__ == "__main__":
    sys.exit(main())
