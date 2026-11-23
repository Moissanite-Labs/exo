#!/usr/bin/env python3
"""Scratch production mutations: emit traces, replay with stock replay.py, pin seq+invariant.

No production mutation switches — patches apply only under a temp copy of src/ and clients/tui/.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REPLAY = ROOT / "specs/seat-binding/conformance/replay.py"

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}

ENGINE_REFUSAL_BLOCK = """            if seat != self.seat.name:
                maybe_note_refusal(b.seat or seat)
                raise CommandFailed(
                    "bad_request", f"this core runs the {self.seat.name!r} seat; it cannot start a thread for seat {seat!r}"
                )"""
ENGINE_REFUSAL_BYPASS = "            if seat != self.seat.name:\n                pass"

APP_LAUNCH_SEAT = "title=self.title_arg, seat=self.launch_seat, kind=kind, cwd=self.new_cwd))"
APP_DISPLAY_SEAT = "title=self.title_arg, seat=self.seat, kind=kind, cwd=self.new_cwd))"

# trace filename -> (expected seq, expected invariant name)
EXPECTED: dict[str, tuple[int, str]] = {
    "new_after_resume.jsonl": (5, "TraceReplay"),
    "cross_seat_create.jsonl": (2, "TraceReplay"),
}


def _replay_module():
    spec = importlib.util.spec_from_file_location("seat_binding_replay", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def toolchain_exit(message: str) -> int:
    print(message, file=sys.stderr)
    return 3


def patch_engine_refusal(engine_path: Path) -> None:
    text = engine_path.read_text(encoding="utf-8")
    if ENGINE_REFUSAL_BLOCK not in text:
        raise RuntimeError(f"engine refusal block not found in {engine_path}")
    engine_path.write_text(text.replace(ENGINE_REFUSAL_BLOCK, ENGINE_REFUSAL_BYPASS), encoding="utf-8")


def patch_app_revert_5ba3813(app_path: Path) -> None:
    text = app_path.read_text(encoding="utf-8")
    if APP_LAUNCH_SEAT not in text:
        raise RuntimeError(f"launch_seat SessionCreate line not found in {app_path}")
    app_path.write_text(text.replace(APP_LAUNCH_SEAT, APP_DISPLAY_SEAT), encoding="utf-8")


def prepare_scratch() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="seat-binding-scratch-"))
    shutil.copytree(ROOT / "src", scratch / "src")
    shutil.copytree(ROOT / "clients" / "tui", scratch / "clients" / "tui")
    return scratch


def install_scratch_imports(scratch: Path) -> None:
    for name in list(sys.modules):
        if name == "exo" or name.startswith("exo.") or name == "exo_tui" or name.startswith("exo_tui."):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if "seat-binding-scratch-" not in p]
    sys.path.insert(0, str(scratch / "clients" / "tui"))
    sys.path.insert(0, str(scratch / "src"))


def replay_violation_line(trace_path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(trace_path.resolve())],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    combined = (proc.stderr or "") + (proc.stdout or "")
    for line in combined.splitlines():
        if line.startswith("VIOLATION "):
            return proc.returncode, line.strip()
    return proc.returncode, combined.strip() or "(no output)"


def assert_violation(trace_name: str, line: str, exit_code: int) -> None:
    expected_seq, expected_inv = EXPECTED[trace_name]
    if exit_code != 1:
        raise AssertionError(f"{trace_name}: expected replay exit 1, got {exit_code}; output={line!r}")
    pattern = re.compile(
        rf"^VIOLATION .*{re.escape(trace_name)} seq={expected_seq} invariant={expected_inv}$"
    )
    if not pattern.match(line):
        raise AssertionError(
            f"{trace_name}: expected "
            f"'VIOLATION ... {trace_name} seq={expected_seq} invariant={expected_inv}', "
            f"got {line!r}"
        )


async def emit_new_after_resume(trace_path: Path, trace_dir: Path) -> None:
    from exo.core.engine import CoreEngine
    from exo.core.plugins import PluginManager
    from exo.core.seats import Seat
    from exo.core.seat_binding_conformance import SeatBindingRecorder
    from exo_tui.app import ExoApp
    from exo_tui.conn import LocalClient

    home = trace_dir / "home"
    recorder = SeatBindingRecorder.start("new_after_resume", trace_path)
    try:
        bare = CoreEngine(
            home, MODELS, plugins=PluginManager(), cwd=str(trace_dir), env={}, seat=None
        )
        try:
            scout_id = bare.store.create_session(agent="worker", seat="scout", title="scout work").id
        finally:
            await bare.close()

        worker = CoreEngine(
            home, MODELS, plugins=PluginManager(), cwd=str(trace_dir), env={}, seat=Seat(name="worker")
        )
        app = ExoApp(LocalClient(worker), session=scout_id, resume=False, seat=None)
        try:
            async with app.run_test(size=(120, 40)):
                await asyncio.wait_for(app.ready.wait(), 5)
                await app.new_session()
        finally:
            await worker.close()
    finally:
        recorder.close()


async def emit_cross_seat_create(trace_path: Path, trace_dir: Path) -> None:
    from exo.core.engine import CoreEngine
    from exo.core.plugins import PluginManager
    from exo.core.seats import Seat
    from exo.core.seat_binding_conformance import SeatBindingRecorder
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher

    recorder = SeatBindingRecorder.start("cross_seat", trace_path)
    eng = CoreEngine(
        trace_dir / "home",
        MODELS,
        plugins=PluginManager(),
        cwd=str(trace_dir),
        env={},
        seat=Seat(name="worker"),
    )
    try:
        response = await Dispatcher(eng).run(s.Command.of(s.SessionCreate(seat="scout")))
        assert response.ok, response.error
    finally:
        await eng.close()
        recorder.close()


def run_mutation_a(trace_dir: Path) -> None:
    scratch = prepare_scratch()
    try:
        patch_app_revert_5ba3813(scratch / "clients" / "tui" / "exo_tui" / "app.py")
        patch_engine_refusal(scratch / "src" / "exo" / "core" / "engine.py")
        trace_path = trace_dir / "new_after_resume.jsonl"
        install_scratch_imports(scratch)
        asyncio.run(emit_new_after_resume(trace_path, trace_dir))
        if not trace_path.is_file() or trace_path.stat().st_size == 0:
            raise RuntimeError("new_after_resume.jsonl missing or empty")
        code, line = replay_violation_line(trace_path)
        assert_violation("new_after_resume.jsonl", line, code)
        print(f"scratch_mutation new_after_resume.jsonl OK {line}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_mutation_b(trace_dir: Path) -> None:
    scratch = prepare_scratch()
    try:
        patch_engine_refusal(scratch / "src" / "exo" / "core" / "engine.py")
        trace_path = trace_dir / "cross_seat_create.jsonl"
        install_scratch_imports(scratch)
        asyncio.run(emit_cross_seat_create(trace_path, trace_dir))
        if not trace_path.is_file() or trace_path.stat().st_size == 0:
            raise RuntimeError("cross_seat_create.jsonl missing or empty")
        code, line = replay_violation_line(trace_path)
        assert_violation("cross_seat_create.jsonl", line, code)
        print(f"scratch_mutation cross_seat_create.jsonl OK {line}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    reason = _replay_module().toolchain_skip_reason()
    if reason:
        return toolchain_exit(f"missing toolchain: {reason}")

    trace_dir = Path(tempfile.mkdtemp(prefix="seat-binding-mut-traces-"))
    failed = False
    try:
        for label, fn in (
            ("a_revert_5ba3813", run_mutation_a),
            ("b_seated_refusal_bypass", run_mutation_b),
        ):
            try:
                fn(trace_dir)
            except (AssertionError, RuntimeError) as exc:
                print(f"scratch_mutation {label} FAIL: {exc}", file=sys.stderr)
                failed = True
    finally:
        shutil.rmtree(trace_dir, ignore_errors=True)

    if failed:
        print("scratch mutation check failed", file=sys.stderr)
        return 1
    print("scratch mutation check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
