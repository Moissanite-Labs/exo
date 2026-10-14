#!/usr/bin/env python3
"""Check pinned mypy output against a counted baseline; updates only shrink it."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

VERSION = "2.4.0"
PYTHON = "3.11"
PLATFORM = "linux"
# Both directories ship in the root wheel. Making TUI an explicit target avoids
# mypy silencing it as a site package when it is installed from this checkout.
TARGETS = ["src/exo", "clients/tui/exo_tui"]
SEARCH_PATHS = ["src", "clients/tui", "plugins/exo-command"]
BASELINE = Path(".github/mypy-baseline.json")
SETTINGS = {"schema": 1, "mypy": VERSION, "python": PYTHON, "platform": PLATFORM,
            "targets": TARGETS, "search_paths": SEARCH_PATHS}
EXTRAS = ("mcp", "httpx2", "textual", "rich", "fastapi", "starlette", "uvicorn", "websockets")

Diagnostic = tuple[str, str, str]  # relative path, message, error code


class CheckError(ValueError):
    """Compiler, environment, or baseline could not be checked reliably."""


def diagnostic(path: str, message: str, code: str, root: Path) -> Diagnostic:
    if not path or not message or not re.fullmatch(r"[a-z][a-z0-9-]*", code):
        raise CheckError("malformed diagnostic")
    candidate = Path(path)
    absolute = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        relative = absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        raise CheckError("diagnostic refers to a file outside the checkout") from None
    # Mypy embeds the earlier definition's source row in no-redef messages.
    if code == "no-redef":
        message = re.sub(r"\bon line \d+\b", "on line <line>", message)
    return relative, message, code


def parse_output(stdout: str, stderr: str, returncode: int, root: Path) -> Counter[Diagnostic]:
    if returncode not in (0, 1) or stderr.strip():
        raise CheckError(f"mypy did not finish normally (exit {returncode}): {stderr.strip() or 'fatal compiler output'}")
    found: Counter[Diagnostic] = Counter()
    for line in stdout.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, TypeError):
            raise CheckError("mypy emitted non-JSON output") from None
        if not isinstance(item, dict) or item.get("severity") not in ("error", "note"):
            raise CheckError("mypy emitted an unknown diagnostic record")
        if (not isinstance(item.get("message"), str) or not item["message"]
                or not isinstance(item.get("file"), str) or not item["file"]
                or type(item.get("line")) is not int or item["line"] < 1
                or type(item.get("column")) is not int or item["column"] < 0):
            raise CheckError("mypy emitted a malformed diagnostic record")
        if item["severity"] == "note":
            continue
        if not isinstance(item.get("code"), str):
            raise CheckError("mypy error has no diagnostic code")
        found[diagnostic(item["file"], item["message"], item["code"], root)] += 1
    if (returncode == 0 and found) or (returncode == 1 and not found):
        raise CheckError("mypy exit status does not match its diagnostics")
    return found


def load_baseline(path: Path, root: Path) -> Counter[Diagnostic]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"cannot read baseline: {exc}") from None
    if (not isinstance(data, dict) or set(data) != {*SETTINGS, "diagnostics"}
            or type(data.get("schema")) is not int
            or any(data.get(k) != v for k, v in SETTINGS.items())):
        raise CheckError("baseline settings do not match the pinned check")
    entries = data.get("diagnostics")
    if not isinstance(entries, list):
        raise CheckError("baseline diagnostics must be a list")
    found: Counter[Diagnostic] = Counter()
    for item in entries:
        if (not isinstance(item, dict) or set(item) != {"path", "message", "code", "count"}
                or any(not isinstance(item.get(k), str) for k in ("path", "message", "code"))
                or type(item.get("count")) is not int or item["count"] < 1):
            raise CheckError("malformed baseline entry")
        key = diagnostic(item["path"], item["message"], item["code"], root)
        if (Path(item["path"]).is_absolute() or key[:2] != (item["path"], item["message"])
                or key in found):
            raise CheckError("baseline paths must be canonical and entries unique")
        found[key] = item["count"]
    return found


def write_baseline(path: Path, found: Counter[Diagnostic]) -> None:
    entries = [{"path": p, "message": m, "code": c, "count": count}
               for (p, m, c), count in sorted(found.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**SETTINGS, "diagnostics": entries}, indent=2) + "\n", encoding="utf-8")


def differences(actual: Counter[Diagnostic], expected: Counter[Diagnostic]) -> tuple[Counter[Diagnostic], Counter[Diagnostic]]:
    return actual - expected, expected - actual


def check_environment() -> None:
    if sys.version_info[:2] != (3, 11):
        raise CheckError("use Python 3.11: uv sync --frozen --all-extras --python 3.11")
    try:
        version = importlib.metadata.version("mypy")
        for name in EXTRAS:
            importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        raise CheckError("install the full dependency set: uv sync --frozen --all-extras --python 3.11") from None
    if version != VERSION:
        raise CheckError(f"expected mypy {VERSION}, found {version}; use the frozen lockfile")


def compile_errors(root: Path) -> Counter[Diagnostic]:
    env = {**os.environ, "MYPYPATH": os.pathsep.join(str(root / p) for p in SEARCH_PATHS)}
    # No suppressions or disk cache: followed local imports remain checked, even
    # when inspecting a separate checkout with --root.
    proc = subprocess.run([sys.executable, "-m", "mypy", "--config-file=", "--python-version", PYTHON,
                           "--platform", PLATFORM, "--no-incremental", "--cache-dir", os.devnull,
                           "--output", "json", *TARGETS], cwd=root, env=env,
                          capture_output=True, text=True)
    return parse_output(proc.stdout, proc.stderr, proc.returncode, root)


def show(label: str, found: Counter[Diagnostic]) -> None:
    for (path, message, code), count in sorted(found.items()):
        print(f"{label} {count}x {path}: {message} [{code}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true", help="remove resolved entries; reject any new diagnostics")
    mode.add_argument("--initialize", action="store_true", help="create a reviewed first baseline; refuses an existing file")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    path = args.baseline if args.baseline.is_absolute() else root / args.baseline
    try:
        check_environment()
        if args.initialize and path.exists():
            raise CheckError("initialization refuses an existing baseline")
        actual = compile_errors(root)
        if args.initialize:
            write_baseline(path, actual)
            print(f"initialized {sum(actual.values())} existing diagnostics; review the baseline diff")
            return 0
        expected = load_baseline(path, root)
        new, resolved = differences(actual, expected)
        show("NEW", new)
        if new:
            show("RESOLVED", resolved)
            return 1  # never expand the baseline, including in update mode
        if args.update:
            write_baseline(path, actual)
            print(f"removed {sum(resolved.values())} resolved diagnostics; {sum(actual.values())} remain")
            return 0
        show("RESOLVED", resolved)
        if resolved:
            print("prune resolved entries: uv run --frozen --all-extras python scripts/check_types.py --update")
            return 1
        print(f"type baseline matches: {sum(actual.values())} existing diagnostics")
        return 0
    except (CheckError, OSError) as exc:
        print(f"type check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
