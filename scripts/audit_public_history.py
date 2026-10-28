#!/usr/bin/env python3
"""Read-only, content-free audit of the historical paths named in issue #49."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

TARGETS = ("inventory/", "docs/BRIEF.md", "docs/STORE-V2.md", "scripts/deploy-site.sh")
PATTERNS = {
    "email": re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "home_path": re.compile(rb"/(?:Users|home)/[^/\s\"'`<>]+"),
}
MAX_BLOB_BYTES = 16 * 1024 * 1024


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "--no-pager", "--no-replace-objects", "-C", str(repo), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError("Git history audit failed; inspect repository access locally")
    return result.stdout


def audit(repo: Path, literals: tuple[bytes, ...] = ()) -> dict:
    refs = git(repo, "for-each-ref", "--format=%(objectname)").splitlines()
    report = {"schema": 1, "read_only": True, "ref_count": len(refs), "targets": [], "complete": True}
    for target in TARGETS:
        objects = git(repo, "rev-list", "--objects", "--all", "--full-history", "--", target).splitlines()
        candidates = {line.split(b" ", 1)[0].decode("ascii") for line in objects if line}
        blobs = set()
        for oid in candidates:
            if git(repo, "cat-file", "-t", oid).strip() == b"blob":
                blobs.add(oid)
        row = {"target": target, "historical_blobs": len(blobs), "scanned_blobs": 0,
               "oversized_blobs": 0, "pattern_matches": {name: 0 for name in PATTERNS},
               "literal_matches": 0, "removal_candidate": target in TARGETS[:2] and bool(blobs)}
        for oid in blobs:
            if int(git(repo, "cat-file", "-s", oid)) > MAX_BLOB_BYTES:
                row["oversized_blobs"] += 1
                report["complete"] = False
                continue
            content = git(repo, "cat-file", "blob", oid)
            row["scanned_blobs"] += 1
            for name, pattern in PATTERNS.items():
                row["pattern_matches"][name] += len(pattern.findall(content))
            row["literal_matches"] += sum(content.count(value) for value in literals)
        report["targets"].append(row)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--literal-file", type=Path,
                        help="private newline-delimited bytes to count; values are never printed")
    args = parser.parse_args(argv)
    try:
        literals = tuple(x for x in args.literal_file.read_bytes().splitlines() if x) if args.literal_file else ()
        report = audit(args.repo, literals)
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({"error": "history audit failed; inspect repository and private pattern file locally"}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
