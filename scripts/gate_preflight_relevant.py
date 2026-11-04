#!/usr/bin/env python3
"""Decide whether a diff touches paths that require the never-looser gate preflight."""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from collections.abc import Iterable

# Keep in sync with gate-preflight.yml documentation and tests/test_gate_preflight_relevant.py.
GATE_RELEVANT_PATTERNS: tuple[str, ...] = (
    "src/exo/gate.py",
    "plugins/exo-gate/**",
    "scripts/gate_diff.py",
    "scripts/gate_preflight_relevant.py",
    "tests/security/test_gate*.py",
    "tests/test_gate_diff*.py",
    "tests/test_gate_preflight_relevant.py",
    ".github/workflows/gate-preflight.yml",
    "pyproject.toml",
    "uv.lock",
)


def path_matches_pattern(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/")
    if pattern.endswith("/**"):
        prefix = pattern[: -len("/**")]
        return normalized == prefix or normalized.startswith(prefix + "/")
    return fnmatch.fnmatch(normalized, pattern)


def diff_touches_gate_paths(changed_paths: Iterable[str]) -> bool:
    for path in changed_paths:
        stripped = path.strip()
        if not stripped:
            continue
        for pattern in GATE_RELEVANT_PATTERNS:
            if path_matches_pattern(stripped, pattern):
                return True
    return False


def git_changed_paths(base_sha: str) -> tuple[list[str] | None, str | None]:
    """Return changed paths, or (None, error_message) when git diff fails."""
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", base_sha, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        return None, err
    paths = [line for line in completed.stdout.splitlines() if line.strip()]
    return paths, None


def write_github_output(run_full: bool, message: str) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"run_full={'true' if run_full else 'false'}\n")
    print(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        required=True,
        help="Base commit SHA (pull_request.base.sha or merge_group.base_sha)",
    )
    args = parser.parse_args(argv)

    paths, err = git_changed_paths(args.base)
    if err is not None:
        write_github_output(
            True,
            f"Path detection failed ({err}); running full never-looser check (fail closed).",
        )
        return 0

    if diff_touches_gate_paths(paths):
        matched = sorted(
            p
            for p in paths
            if any(path_matches_pattern(p, pat) for pat in GATE_RELEVANT_PATTERNS)
        )
        write_github_output(
            True,
            "Gate-relevant paths changed: "
            + ", ".join(matched[:8])
            + (" …" if len(matched) > 8 else "")
            + "; running full never-looser check.",
        )
        return 0

    write_github_output(
        False,
        "No gate-relevant paths in diff against base; skipping never-looser (check still succeeds).",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
