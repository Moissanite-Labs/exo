"""Trace replay conformance tests (require Java for replay.py)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT / "specs/seat-binding/conformance/replay.py"
TRACES = ROOT / "specs/seat-binding/conformance/traces"


def _run(trace: Path) -> int:
    proc = subprocess.run(
        [sys.executable, str(REPLAY), str(trace)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode


def test_replay_good_exits_zero():
    if shutil.which("java") is None:
        import pytest

        pytest.skip("java not on PATH")
    assert _run(TRACES / "good_new_after_resume.jsonl") == 0


def test_replay_b1_exits_one():
    if shutil.which("java") is None:
        import pytest

        pytest.skip("java not on PATH")
    assert _run(TRACES / "b1_silent_fail.jsonl") == 1


def test_replay_malformed_exits_two():
    if shutil.which("java") is None:
        import pytest

        pytest.skip("java not on PATH")
    assert _run(TRACES / "malformed.jsonl") == 2
