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
BUILD = TRACES.parent / "build"


def _replay_module():
    spec = importlib.util.spec_from_file_location("seat_binding_replay", REPLAY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run(trace: Path, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), *extra, str(trace)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    err = (proc.stderr or "") + (proc.stdout or "")
    return proc.returncode, err


def _run_diagnose(trace: Path) -> tuple[int, str]:
    return _run(trace, "--diagnose")


def _require_toolchain():
    import pytest

    reason = _replay_module().toolchain_skip_reason()
    if reason:
        pytest.skip(reason)


def _write_trace(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _b1_without_persist() -> Path:
    lines = (TRACES / "b1_silent_fail.jsonl").read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    seq = 0
    for line in lines:
        obj = json.loads(line)
        if obj["event"] == "persist_session":
            continue
        obj["seq"] = seq
        kept.append(json.dumps(obj, separators=(",", ":")))
        seq += 1
    return _write_trace(BUILD / "b1_without_persist.jsonl", kept)


def _truncated_good_prefix() -> Path:
    lines = (TRACES / "good_new_after_resume.jsonl").read_text(encoding="utf-8").splitlines()
    return _write_trace(BUILD / "truncated_good_prefix.jsonl", lines[:4])


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
    tmp = _write_trace(BUILD / "mutation_no_load_seat.jsonl", kept)
    code, err = _run(tmp)
    assert code == 1
    assert "seq=2 invariant=TraceReplay" in err
    assert "diagnosed=" not in err


def test_scratch_production_mutations_pin_violation_line():
    """Temp-copy production patches; replay must match pinned seq+invariant (not exit 1 alone)."""
    _require_toolchain()
    script = ROOT / "specs/seat-binding/conformance/check_scratch_mutations.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "seq=5 invariant=TraceReplay" in proc.stdout
    assert "seq=2 invariant=TraceReplay" in proc.stdout


def test_replay_diagnose_b1_names_invariant_via_tlc():
    _require_toolchain()
    code, err = _run_diagnose(TRACES / "b1_silent_fail.jsonl")
    assert code == 1
    assert "invariant=NewAfterResumeBindsThisCore" in err
    assert "diagnosed=NewAfterResumeBindsThisCore" in err


def test_replay_diagnose_dropped_persist_grk_proof():
    _require_toolchain()
    code, err = _run_diagnose(TRACES / "dropped_persist.jsonl")
    assert code == 1
    assert "invariant=TraceReplay" in err
    assert "diagnosed=none" in err
    assert "diagnosed=NoForeignBeforeCoreLoad" not in err


def test_replay_diagnose_b1_persist_dropped_grk_proof():
    _require_toolchain()
    trace = _b1_without_persist()
    code, err = _run_diagnose(trace)
    assert code == 1
    assert "diagnosed=none" in err
    assert "diagnosed=NoForeignBeforeCoreLoad" not in err
    assert "diagnosed=NewAfterResumeBindsThisCore" not in err


def test_replay_diagnose_truncated_good_grk_proof():
    _require_toolchain()
    trace = _truncated_good_prefix()
    code, err = _run_diagnose(trace)
    assert code == 0
    assert "diagnosed=" not in err
    assert "VIOLATION" not in err
