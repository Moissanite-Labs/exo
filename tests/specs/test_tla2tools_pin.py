"""The tla2tools.jar SHA-256 pin must agree everywhere it is recorded.

The seat-binding conformance gate verifies the jar in three places: the
spec-conformance workflow, replay.py, and CI-INTERFACE.md. A pin that drifts
in one of them fails the gate (or, worse, verifies against a different hash
than the docs claim). This test pins the agreement, not the value itself:
when upstream re-publishes the artifact (#1195), all three move together.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/spec-conformance.yml"
REPLAY = ROOT / "specs/seat-binding/conformance/replay.py"
CI_INTERFACE = ROOT / "specs/seat-binding/conformance/CI-INTERFACE.md"

SHA256 = re.compile(r"\b[0-9a-f]{64}\b")


def _pins(path: Path) -> set[str]:
    return set(SHA256.findall(path.read_text(encoding="utf-8")))


def test_tla2tools_pin_agrees_across_workflow_replay_and_docs() -> None:
    pins = {path: _pins(path) for path in (WORKFLOW, REPLAY, CI_INTERFACE)}
    for path, found in pins.items():
        assert len(found) == 1, f"{path}: expected exactly one SHA-256 pin, found {sorted(found)}"
    values = {next(iter(found)) for found in pins.values()}
    assert len(values) == 1, (
        "tla2tools pins disagree: "
        + ", ".join(f"{path}: {sorted(found)}" for path, found in pins.items())
    )
