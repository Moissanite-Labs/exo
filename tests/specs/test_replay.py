"""Trace replay conformance tests (require Java + pinned tla2tools.jar for replay.py)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT / "specs/seat-binding/conformance/replay.py"
TRACES = ROOT / "specs/seat-binding/conformance/traces"


def _replay_module():
    spec = importlib.util.spec_from_file_location("seat_binding_replay", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(trace: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(trace)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    err = (proc.stderr or "") + (proc.stdout or "")
    return proc.returncode, err


def _require_toolchain():
    import pytest

    reason = _replay_module().toolchain_skip_reason()
    if reason:
        pytest.skip(reason)


def test_replay_good_exits_zero():
    _require_toolchain()
    code, _ = _run(TRACES / "good_new_after_resume.jsonl")
    assert code == 0


def test_replay_b1_exits_one():
    _require_toolchain()
    code, err = _run(TRACES / "b1_silent_fail.jsonl")
    assert code == 1
    assert "seq=5 invariant=NewAfterResumeBindsThisCore" in err


def test_replay_malformed_exits_two():
    _require_toolchain()
    assert _run(TRACES / "malformed.jsonl")[0] == 2


def test_replay_header_only_exits_two():
    _require_toolchain()
    code, err = _run(TRACES / "header_only.jsonl")
    assert code == 2
    assert "no events after initial" in err


def test_replay_no_client_boot_exits_two():
    _require_toolchain()
    code, err = _run(TRACES / "no_client_boot.jsonl")
    assert code == 2
    assert "client_boot" in err


def test_replay_dropped_persist_exits_one():
    _require_toolchain()
    code, err = _run(TRACES / "dropped_persist.jsonl")
    assert code == 1
    assert "seq=1 invariant=TraceReplay" in err


def test_replay_dropped_mid_event_mutation_exits_one():
    """Delete load_seat from the good trace; post-states no longer match one action."""
    _require_toolchain()
    lines = (TRACES / "good_new_after_resume.jsonl").read_text(encoding="utf-8").splitlines()
    kept = [lines[0]]
    seq = 1
    for line in lines[1:]:
        obj = json.loads(line)
        if obj["event"] == "load_seat":
            continue
        obj["seq"] = seq
        kept.append(json.dumps(obj, separators=(",", ":")))
        seq += 1
    tmp = TRACES.parent / "build" / "mutation_no_load_seat.jsonl"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    code, err = _run(tmp)
    assert code == 1
    assert "seq=2 invariant=TraceReplay" in err
