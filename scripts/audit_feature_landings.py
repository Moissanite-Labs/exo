#!/usr/bin/env python3
"""Read-only source-path audit for the five merged PRs tracked by #741."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

PULL_REQUESTS = (508, 510, 512, 630, 723)
FIELDS = '''number url state baseRefName headRefOid mergeCommit {oid}
 files(first:100) {pageInfo {hasNextPage} nodes {path}}'''
QUERY = 'query {repository(owner:"aska-digital",name:"exo") {' + ''.join(
    f'p{number}:pullRequest(number:{number}) {{{FIELDS}}}' for number in PULL_REQUESTS) + '}}'
OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def git(repo: Path, *args: str, missing_ok: bool = False) -> bytes | None:
    result = subprocess.run(["git", "--no-pager", "--no-replace-objects", "--literal-pathspecs", "-C", str(repo), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if result.returncode:
        if missing_ok and result.returncode in (1, 128):
            return None
        raise ValueError("Git source metadata could not be read")
    return result.stdout


def entry(repo: Path, commit: str, path: str) -> tuple[str, str, str] | None:
    raw = git(repo, "ls-tree", "-z", commit, "--", path)
    if not raw:
        return None
    metadata, _ = raw.split(b"\t", 1)
    mode, kind, oid = metadata.decode("ascii").split()
    return mode, kind, oid


def audit(repo: Path, base: str, pull_requests: list[dict]) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", base) or base.startswith("-") or ".." in base:
        raise ValueError("invalid base reference")
    head = git(repo, "rev-parse", "--verify", base + "^{commit}").decode().strip()
    shallow = git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true"
    rows, complete = [], not shallow
    seen = set()
    for pr in pull_requests:
        number = pr["number"]
        if number in seen or pr["state"] != "MERGED":
            raise ValueError("audit source must contain unique merged pull requests")
        seen.add(number)
        merged, source = pr["mergeCommit"]["oid"], pr["headRefOid"]
        if not OID.fullmatch(merged) or not OID.fullmatch(source):
            raise ValueError("invalid source commit")
        row = {"pr": number, "url": pr["url"], "merge_commit": merged, "source_head": source,
               "merged_into": pr["baseRefName"], "base_commit": head, "merge_in_base": None,
               "missing_source_paths": [], "exact_source_paths": [], "changed_source_paths": [],
               "deleted_in_source": [], "source_complete": not pr["files"]["pageInfo"]["hasNextPage"]}
        available = all(git(repo, "cat-file", "-e", oid + "^{commit}", missing_ok=True) is not None
                        for oid in (merged, source))
        if not available:
            row["status"] = "source_objects_unavailable"
            complete = False
            rows.append(row)
            continue
        row["merge_in_base"] = git(repo, "merge-base", "--is-ancestor", merged, head, missing_ok=True) is not None
        for file in pr["files"]["nodes"]:
            path = file["path"]
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts or "\x00" in path:
                raise ValueError("invalid source path")
            wanted, current = entry(repo, source, path), entry(repo, head, path)
            if wanted is None:
                row["deleted_in_source"].append(path)
            elif current is None:
                row["missing_source_paths"].append(path)
            elif current == wanted:
                row["exact_source_paths"].append(path)
            else:
                row["changed_source_paths"].append(path)
        row["status"] = ("listed_paths_match" if not (row["missing_source_paths"] or row["changed_source_paths"]
                          or row["deleted_in_source"]) else "content_review_required")
        if not row["source_complete"]:
            complete = False
        rows.append(row)
    return {"schema": 1, "read_only": True, "complete": complete, "ancestry_complete": not shallow,
            "base_commit": head, "pull_requests": rows,
            "limits": "Ancestry and listed-path blob comparisons do not prove feature behavior, approved removals or complete integration."}


def source(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("incomplete GitHub source")
    repository = payload["data"]["repository"]
    rows = [repository[f"p{number}"] for number in PULL_REQUESTS]
    if [row["number"] for row in rows] != list(PULL_REQUESTS):
        raise ValueError("incomplete GitHub source")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="origin/main")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--live", action="store_true")
    inputs.add_argument("--snapshot", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.live:
            response = subprocess.run(["gh", "api", "graphql", "-f", "query=" + QUERY],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False)
            if response.returncode:
                raise ValueError("GitHub source unavailable")
            payload = json.loads(response.stdout)
        else:
            payload = json.loads(args.snapshot.read_text())
        report = audit(args.repo, args.base, source(payload))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        print(json.dumps({"error": "audit incomplete; inspect repository objects and source response locally"}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
