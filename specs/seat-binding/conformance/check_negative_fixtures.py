#!/usr/bin/env python3
"""CI gate: replay negative trace fixtures and assert exact exit codes (see tests/specs/test_replay.py)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REPLAY = ROOT / "specs/seat-binding/conformance/replay.py"
TRACES = ROOT / "specs/seat-binding/conformance/traces"

# filename -> expected replay.py exit code (canonical with tests/specs/test_replay.py)
NEGATIVE_FIXTURES: tuple[tuple[str, int], ...] = (
    ("b1_silent_fail.jsonl", 1),
    ("dropped_persist.jsonl", 1),
    ("late_policy_violation.jsonl", 1),
    ("header_only.jsonl", 2),
    ("malformed.jsonl", 2),
    ("no_client_boot.jsonl", 2),
)


def main() -> int:
    failed = False
    for name, expected in NEGATIVE_FIXTURES:
        path = TRACES / name
        if not path.is_file():
            print(f"negative_fixture {name} expected={expected} actual=MISSING", file=sys.stderr)
            failed = True
            continue
        proc = subprocess.run(
            [sys.executable, str(REPLAY), str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        actual = proc.returncode
        status = "OK" if actual == expected else "FAIL"
        print(f"negative_fixture {name} expected={expected} actual={actual} {status}")
        if actual != expected:
            failed = True
            if proc.stderr:
                print(proc.stderr.rstrip(), file=sys.stderr)
            if proc.stdout:
                print(proc.stdout.rstrip(), file=sys.stderr)
    if failed:
        print("negative fixture check failed", file=sys.stderr)
        return 1
    print("negative fixture check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
