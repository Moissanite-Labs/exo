#!/usr/bin/env python3
"""Read one GitHub Actions attempt; never retry, dispatch or alter settings."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def gh_get(endpoint: str) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "api", "--method", "GET", endpoint],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        # gh stderr can contain account/transport details; do not copy it into reports.
        raise ValueError(f"GitHub GET failed (exit {result.returncode})")
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("GitHub response is not an object")
    return data


def inspect_run(repo: str, run_id: int, *, expected_sha: str | None = None,
                get: Callable[[str], dict[str, Any]] = gh_get) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo) or run_id <= 0:
        raise ValueError("expected owner/repository and a positive run ID")
    if expected_sha is not None and not re.fullmatch(r"[a-fA-F0-9]{40}", expected_sha):
        raise ValueError("expected SHA must be a full 40-character commit ID")
    root = f"repos/{repo}/actions/runs/{run_id}"
    run = get(root)
    if run.get("id") != run_id:
        raise ValueError("run response does not match requested ID")
    attempt = run.get("run_attempt")
    sha = run.get("head_sha")
    if type(attempt) is not int or attempt < 1 or not isinstance(sha, str):
        raise ValueError("missing run attempt or head SHA")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", sha):
        raise ValueError("malformed run head SHA")
    jobs: list[dict[str, Any]] = []
    total = None
    for page in range(1, 11):
        # Pin the attempt so a concurrent rerun cannot mix jobs from different attempts.
        data = get(f"{root}/attempts/{attempt}/jobs?per_page=100&page={page}")
        count, batch = data.get("total_count"), data.get("jobs")
        if type(count) is not int or count < 0 or not isinstance(batch, list):
            raise ValueError("malformed jobs response")
        if total is not None and count != total:
            raise ValueError("job inventory changed while paging; inspect again")
        total = count
        for job in batch:
            if not isinstance(job, dict) or type(job.get("id")) is not int:
                raise ValueError("malformed job record")
            jobs.append(job)
        if len(jobs) >= total:
            break
        if not batch:
            raise ValueError("incomplete job inventory")
    if len(jobs) != total or len({job["id"] for job in jobs}) != len(jobs):
        raise ValueError("incomplete, oversized or duplicate job inventory")
    # Check that the attempt and run state did not change during collection.
    latest = get(root)
    keys = ("id", "run_attempt", "head_sha", "status", "conclusion")
    if any(latest.get(key) != run.get(key) for key in keys):
        raise ValueError("run changed during inspection; inspect again")
    reported = bool(jobs) and run.get("status") == "completed" and all(
        job.get("status") == "completed" and job.get("conclusion") is not None for job in jobs
    )
    if not jobs and run.get("conclusion") == "startup_failure":
        state = "blocked_before_jobs"
    elif reported:
        state = "jobs_reported"
    else:
        state = "not_yet_proven"
    matches = expected_sha is None or sha.lower() == expected_sha.lower()
    return {
        "repository": repo, "run_id": run_id, "attempt": attempt, "head_sha": sha,
        "run_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        "status": run.get("status"), "conclusion": run.get("conclusion"),
        "job_count": len(jobs), "state": state, "expected_head_matches": matches,
        "hosted_execution_verified": reported and matches,
        "ci_passed": reported and matches and run.get("conclusion") == "success" and all(
            job.get("conclusion") in {"success", "skipped"} for job in jobs
        ),
        "jobs": [{key: job.get(key) for key in ("id", "status", "conclusion")} for job in jobs],
        "cause": "not established by run and job records",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    try:
        report = inspect_run(args.repository, args.run_id, expected_sha=args.expected_sha)
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        print(json.dumps({"state": "inspection_failed", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["hosted_execution_verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
