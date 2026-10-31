#!/usr/bin/env python3
"""Read-only, content-free audit of the historical paths named in issue #49."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

TARGETS = ("inventory/", "docs/BRIEF.md", "docs/STORE-V2.md", "scripts/deploy-site.sh")
PATTERNS = {
    "email": re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "home_path": re.compile(rb"/(?:Users|home)/[^/\s\"'`<>]+"),
}
MAX_BLOB_BYTES = 16 * 1024 * 1024


def git_command(repo: Path, *args: str) -> list[str]:
    return ["git", "--no-pager", "--no-replace-objects", "-C", str(repo), *args]


def git_environment() -> dict[str, str]:
    return {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_GRAFT_FILE": os.devnull}


def head_state(repo: Path) -> tuple[bytes, bytes]:
    return (git(repo, "rev-parse", "--verify", "HEAD").strip(),
            git(repo, "rev-parse", "--symbolic-full-name", "HEAD").strip())


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(git_command(repo, *args), env=git_environment(),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise ValueError("Git history audit failed; inspect repository access locally")
    return result.stdout


def audit(repo: Path, literals: tuple[bytes, ...] = ()) -> dict:
    # Refuse before object traversal: older Git versions may ignore NO_LAZY_FETCH.
    shallow = git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true"
    config = git(repo, "config", "--includes", "--list", "--null").lower().split(b"\0")
    partial = any(item.split(b"\n", 1)[0] == b"extensions.partialclone" or
                  (item.split(b"\n", 1)[0].startswith(b"remote.") and
                   item.split(b"\n", 1)[0].endswith(b".promisor")) for item in config)
    # --git-path info/grafts follows GIT_GRAFT_FILE, which we disable for reads.
    # Resolve the common directory to inspect the repository's default file too.
    graft_path = Path(os.fsdecode(git(repo, "rev-parse", "--git-common-dir").strip()))
    if not graft_path.is_absolute():
        graft_path = repo / graft_path
    grafts = "GIT_GRAFT_FILE" in os.environ or (graft_path / "info/grafts").exists()
    refs = git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    report = {"schema": 2, "read_only": True, "ref_count": len(refs), "targets": [],
              "complete": not (shallow or partial or grafts), "shallow": shallow,
              "partial": partial, "grafts": grafts,
              "coverage": "local reachable objects only; excludes reflogs, unreachable and unadvertised remote objects",
              "literal_scope": "all reachable blob, commit and tag objects" if literals else "not requested"}
    if shallow or partial or grafts:
        return report
    head = head_state(repo)
    roots = tuple(sorted({line.split()[-1].decode("ascii") for line in refs} |
                         {head[0].decode("ascii")}))
    inventory = object_inventory(repo, roots)
    for target in TARGETS:
        objects = git(repo, "rev-list", "--objects", *roots, "--full-history", "--",
                      ":(top,literal)" + target).splitlines()
        candidates = {line.split(b" ", 1)[0].decode("ascii") for line in objects
                      if b" " in line and line.split(b" ", 1)[1]}
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
    if literals:
        report["all_objects_literals"] = scan_literals(repo, literals, inventory)
        if report["all_objects_literals"]["oversized_objects"]:
            report["complete"] = False
    # A concurrent ref update makes this snapshot unsuitable for verification.
    report["refs_unchanged"] = refs == git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
    report["head_unchanged"] = head == head_state(repo)
    report["complete"] = report["complete"] and report["refs_unchanged"] and report["head_unchanged"]
    return report


def object_inventory(repo: Path, roots: tuple[str, ...] | None = None) -> list[tuple[bytes, bytes, int]]:
    if roots is None:
        refs = git(repo, "for-each-ref", "--format=%(objectname)").splitlines()
        roots = tuple(sorted({x.decode("ascii") for x in refs} |
                             {head_state(repo)[0].decode("ascii")}))
    objects = git(repo, "rev-list", "--objects", *roots).splitlines()
    ids = sorted({line.split(b" ", 1)[0] for line in objects})
    inventory = subprocess.run(git_command(repo, "cat-file", "--batch-check"),
                               input=b"\n".join(ids) + b"\n", env=git_environment(),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if inventory.returncode:
        raise ValueError("history object inventory failed")
    rows = []
    for line in inventory.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise ValueError("history object unavailable")
        rows.append((fields[0], fields[1], int(fields[2])))
    return rows


def scan_literals(repo: Path, literals: tuple[bytes, ...], inventory: list) -> dict:
    result = {"scanned_blobs": 0, "scanned_commits": 0, "scanned_tags": 0,
              "oversized_objects": 0, "literal_matches": 0}
    # Request one object at a time to bound memory and avoid pipe deadlock.
    with subprocess.Popen(git_command(repo, "cat-file", "--batch"), env=git_environment(),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        try:
            for oid, kind, size in inventory:
                if kind not in (b"blob", b"commit", b"tag"):
                    continue
                if int(size) > MAX_BLOB_BYTES:
                    result["oversized_objects"] += 1
                    continue
                process.stdin.write(oid + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3 or header[0] != oid or header[1] != kind:
                    raise ValueError("history object unavailable")
                content = process.stdout.read(int(header[2]))
                if len(content) != int(header[2]) or process.stdout.read(1) != b"\n":
                    raise ValueError("history object truncated")
                result["scanned_" + kind.decode() + "s"] += 1
                result["literal_matches"] += sum(content.count(value) for value in literals)
        finally:
            process.stdin.close()
            process.wait()
        if process.returncode:
            raise ValueError("history object scan failed")
    return result


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
