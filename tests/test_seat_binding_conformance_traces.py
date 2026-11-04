"""Emit seat-binding conformance traces from real session/seat code paths."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from exo.core.engine import CoreEngine
from exo.core.plugins import PluginManager
from exo.core.seats import Seat
from exo.core.seat_binding_conformance import SeatBindingRecorder
from exo.protocol import schema as s
from exo.server.app import Dispatcher
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "specs/seat-binding/conformance/replay.py"
SCHEMA = ROOT / "specs/seat-binding/conformance/trace.schema.json"

MODELS = {"fake": {"rules": []}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _replay_module():
    spec = importlib.util.spec_from_file_location("seat_binding_replay", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _engine(home, cwd, name):
    return CoreEngine(
        home, MODELS, plugins=PluginManager(), cwd=str(cwd), env={},
        seat=Seat(name=name) if name else None,
    )


def _schema_allowed_keys() -> set[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return set(schema.get("properties", {}))


def validate_emitted_trace(path: Path) -> None:
    """Validate JSONL against trace.schema.json (required fields, enums, no extra keys)."""
    replay = _replay_module()
    allowed = _schema_allowed_keys()
    records = replay.load_trace(path)
    for i, obj in enumerate(records, start=1):
        extra = set(obj) - allowed
        if extra:
            raise AssertionError(f"line {i}: unexpected fields {sorted(extra)}")


def _replay(path: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stderr or "") + (proc.stdout or "")


@pytest.fixture
def trace_dir(tmp_path):
    env = os.environ.get("EXO_SEAT_BINDING_TRACE_DIR")
    if env:
        path = Path(env)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return tmp_path / "traces"


def test_emit_new_after_resuming_another_seat_replays(trace_dir):
    async def run() -> Path:
        home = trace_dir / "home"
        trace_path = trace_dir / "new_after_resume.jsonl"
        recorder = SeatBindingRecorder.start("new_after_resume", trace_path)
        try:
            bare = _engine(home, trace_dir, None)
            try:
                response = await Dispatcher(bare).run(
                    s.Command.of(s.SessionCreate(seat="scout", title="scout work"))
                )
                assert response.ok, response.error
                scout_id = response.result["session"]["id"]
            finally:
                await bare.close()

            worker = _engine(home, trace_dir, "worker")
            app = ExoApp(LocalClient(worker), session=scout_id, resume=False, seat=None)
            try:
                async with app.run_test(size=(120, 40)):
                    await asyncio.wait_for(app.ready.wait(), 5)
                    await app.new_session()
                    assert worker.store.get_session(app.session).seat == "worker"
            finally:
                await worker.close()
        finally:
            recorder.close()
        return trace_path

    path = asyncio.run(run())
    validate_emitted_trace(path)
    reason = _replay_module().toolchain_skip_reason()
    if reason:
        pytest.skip(reason)
    code, err = _replay(path)
    assert code == 0, err


def test_emit_seated_refusal_replays(trace_dir):
    async def run() -> Path:
        trace_path = trace_dir / "refusal.jsonl"
        recorder = SeatBindingRecorder.start("refusal", trace_path)
        try:
            eng = _engine(trace_dir / "home", trace_dir, "worker")
            try:
                response = await Dispatcher(eng).run(s.Command.of(s.SessionCreate(seat="scout")))
                assert not response.ok
            finally:
                await eng.close()
        finally:
            recorder.close()
        return trace_path

    path = asyncio.run(run())
    validate_emitted_trace(path)
    reason = _replay_module().toolchain_skip_reason()
    if reason:
        pytest.skip(reason)
    code, err = _replay(path)
    assert code == 0, err
