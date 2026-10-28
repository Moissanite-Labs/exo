#!/usr/bin/env python3
"""Read-only account/repository preflight for the authorization record in #180."""
from __future__ import annotations

import argparse
import json
import subprocess

REPOSITORY = "aska-digital/exo"
QUERY = '''query { viewer { login } repository(owner:"aska-digital", name:"exo") {
  nameWithOwner isPrivate viewerPermission } }'''
PERMISSIONS = {"READ", "TRIAGE", "WRITE", "MAINTAIN", "ADMIN"}


def summarize(payload: object, expected_actor: str) -> dict:
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError("repository access could not be verified")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("viewer"), dict):
        raise ValueError("repository access could not be verified")
    actor = data["viewer"].get("login")
    repo = data.get("repository")
    if not isinstance(actor, str) or not isinstance(repo, dict) or repo.get("nameWithOwner") != REPOSITORY:
        raise ValueError("repository access could not be verified")
    permission = repo.get("viewerPermission")
    if permission not in PERMISSIONS or type(repo.get("isPrivate")) is not bool:
        raise ValueError("repository access could not be verified")
    matches = actor.casefold() == expected_actor.casefold()
    return {"repository": REPOSITORY, "actor_matches_expected": matches,
            "repository_visible": True, "repository_private": repo["isPrivate"],
            "account_write_role": permission in {"WRITE", "MAINTAIN", "ADMIN"},
            "viewer_permission": permission, "write_operation_verified": False,
            "permanent_access_verified": False, "read_only": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-actor", required=True, help="expected public GitHub login; never a credential")
    args = parser.parse_args(argv)
    try:
        result = subprocess.run(["gh", "api", "graphql", "-f", "query=" + QUERY],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
        if result.returncode:
            raise ValueError("repository access could not be verified")
        report = summarize(json.loads(result.stdout), args.expected_actor)
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        print(json.dumps({"repository": REPOSITORY, "read_only": True,
                          "error": "access not verified; check the selected account and repository access locally"}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["actor_matches_expected"] and report["account_write_role"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
