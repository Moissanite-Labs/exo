#!/usr/bin/env python3
"""Read-only, full-queue status of the original re-review findings in #51."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

FINDINGS = (31, 46, 49, 130, 131, 132, 133, 135, 136, 137, 143, 128)
FIELDS = '''number title url state timelineItems(first:100,itemTypes:[CROSS_REFERENCED_EVENT,CLOSED_EVENT]) {
 pageInfo {hasNextPage} nodes {__typename
 ... on ClosedEvent {closer {__typename ... on PullRequest {number url merged}}}
 ... on CrossReferencedEvent {source {__typename ... on PullRequest {
 number title url state headRefOid baseRefName reviewDecision isDraft }}} } }'''
QUERY = 'query { repository(owner:"aska-digital",name:"exo") {' + ''.join(
    f'i{number}:issue(number:{number}) {{ {FIELDS} }}' for number in FINDINGS) + '}}'


def summarize(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("incomplete tracker response")
    data = payload.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    if not isinstance(repository, dict):
        raise ValueError("incomplete tracker response")
    rows = []
    complete = True
    for number in FINDINGS:
        issue = repository.get(f"i{number}")
        if not isinstance(issue, dict) or issue.get("number") != number or issue.get("state") not in {"OPEN", "CLOSED"}:
            raise ValueError("incomplete tracker response")
        timeline = issue.get("timelineItems")
        if not isinstance(timeline, dict) or not isinstance(timeline.get("nodes"), list):
            raise ValueError("incomplete tracker response")
        page = timeline.get("pageInfo")
        if not isinstance(page, dict) or type(page.get("hasNextPage")) is not bool:
            raise ValueError("incomplete tracker response")
        truncated = page["hasNextPage"]
        complete &= not truncated
        references = {}
        closer = None
        for event in timeline["nodes"]:
            if not isinstance(event, dict):
                raise ValueError("malformed tracker event")
            if event.get("__typename") == "CrossReferencedEvent":
                source = event.get("source")
                if isinstance(source, dict) and source.get("__typename") == "PullRequest":
                    references[source["url"]] = {key: source[key] for key in (
                        "number", "title", "url", "state", "headRefOid", "baseRefName", "reviewDecision", "isDraft")}
            elif event.get("__typename") == "ClosedEvent":
                candidate = event.get("closer")
                closer = candidate if isinstance(candidate, dict) and candidate.get("__typename") == "PullRequest" else None
        open_refs = [pr for pr in references.values() if pr["state"] == "OPEN"]
        if issue["state"] == "CLOSED":
            status = "closed_by_merged_pr" if closer and closer.get("merged") is True else "closed_needs_closure_evidence"
        else:
            status = "open_with_pr_references" if open_refs else "open_without_pr_reference"
        rows.append({"number": number, "title": issue["title"], "url": issue["url"], "state": issue["state"],
                     "status": status, "timeline_truncated": truncated,
                     "closing_pr": closer, "pr_references": list(references.values())})
    return {"schema": 1, "tracker": 51, "read_only": True, "complete": complete,
            "finding_count": len(rows), "findings": rows,
            "limits": "PR references do not establish full coverage or current-main presence; verify exact heads before action."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--live", action="store_true", help="query GitHub read-only using current selected credentials")
    source.add_argument("--snapshot", type=Path, help="read a previously saved query response")
    args = parser.parse_args(argv)
    try:
        if args.live:
            result = subprocess.run(["gh", "api", "graphql", "-f", "query=" + QUERY],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=False)
            if result.returncode:
                raise ValueError("tracker query failed")
            payload = json.loads(result.stdout)
        else:
            payload = json.loads(args.snapshot.read_text())
        report = summarize(payload)
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        print(json.dumps({"error": "tracker incomplete; inspect source access and response shape locally", "tracker": 51}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
