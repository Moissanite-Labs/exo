#!/usr/bin/env python3
"""Decide whether a diff touches paths that require the never-looser gate preflight."""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from collections.abc import Iterable

# Keep in sync with docs/DEVELOPER-GUIDE.md section 7, .github/CODEOWNERS and tests/test_gate_preflight_relevant.py.
# The entries before plugins/ cover the files that change the gate's code or data as Python loads it, not only
# gate.py. The comparator's child puts the tree's src/ first on sys.path and runs `from exo import gate`:
# - src/exo/__init__ runs first; gate.py imports .store and reads core/models/catalog.toml;
# - for each of those modules Python imports a package (gate/__init__.py) or an extension module (gate.abi3.so) of
#   the same name in place of the .py, so the patterns end in `*`, and it loads a committed __pycache__ .pyc instead
#   of the source when the .pyc says it is current;
# - a .gitattributes line (working-tree-encoding, eol) changes those files' bytes on checkout with no change to them;
# - EDITABLE_ROOTS below: since the child puts src/ first, a module in src/ outside src/exo/ (tomllib.py, copy.py) is
#   imported in place of a standard library module the gate imports. The editable install also lists src/ and
#   clients/tui/ in a .pth (after the standard library), so a sitecustomize.py in either runs at start-up in both
#   children.
# tests/test_gate_preflight_relevant.py walks gate.py's imports and its `Path(__file__).parent` reads and fails when
# one of those files, or one that can stand in for it, is not covered here.
# This list decides when the comparator runs. It is no guard against a PR written to defeat the comparator: the job
# runs the PR's own tests and dependencies first. Only review guards against that, and .github/CODEOWNERS does not
# cover plugins/exo-gate/, pyproject.toml or uv.lock, so ordinary review is all those get.
GATE_RELEVANT_PATTERNS: tuple[str, ...] = (
    "src/exo/gate*",
    "src/exo/__init__*",
    "src/exo/store*",
    "src/exo/*__pycache__/*",  # any depth: fnmatch's * matches /
    "src/exo/core/models/catalog.toml",
    ".gitattributes",
    "*/.gitattributes",  # any depth
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


# Each directory the editable install puts on sys.path, with the package it holds: the parents of
# `[tool.hatch.build.targets.wheel] packages` in pyproject.toml (a test checks they match). Every path in a root but
# outside its package is gate-relevant (see above). No pattern can say "outside", so is_gate_relevant checks it, and
# .github/CODEOWNERS says it as `/src/` with `/src/exo/` taken back out, and the same for clients/tui/.
EDITABLE_ROOTS: tuple[tuple[str, str], ...] = (("src/", "src/exo/"), ("clients/tui/", "clients/tui/exo_tui/"))


def is_gate_relevant(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        return False
    for root, package in EDITABLE_ROOTS:
        if normalized.startswith(root) and not normalized.startswith(package):
            return True
    return any(path_matches_pattern(normalized, pattern) for pattern in GATE_RELEVANT_PATTERNS)


def diff_touches_gate_paths(changed_paths: Iterable[str]) -> bool:
    return any(is_gate_relevant(path) for path in changed_paths)


def changed_paths_from_name_status(name_status: str) -> list[str]:
    """Collect every path from ``git diff -z --name-status -M`` (both paths of a rename or copy, and deletes).

    With ``-z`` git writes each field NUL-terminated and leaves paths unquoted. Without it, a name with a
    non-ASCII byte, a tab, a newline, a quote or a backslash comes back C-quoted (``"a\\303\\251.py"``) and
    matches no pattern, so the check would be skipped.
    """
    fields = name_status.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    paths: set[str] = set()
    i = 0
    while i < len(fields):
        status = fields[i]
        i += 1
        if not status:
            continue
        count = 2 if status[0] in ("R", "C") else 1
        for path in fields[i : i + count]:
            if path:
                paths.add(path)
        i += count
    return sorted(paths)


def git_changed_paths(base_sha: str) -> tuple[list[str] | None, str | None]:
    """Return changed paths, or (None, error_message) when git diff fails."""
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "-z", "--name-status", "-M", base_sha, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        return None, err
    paths = changed_paths_from_name_status(completed.stdout)
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
        matched = sorted(p for p in paths if is_gate_relevant(p))
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
