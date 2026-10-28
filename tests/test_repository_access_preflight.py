"""Repo access checks do not change logins, exercise writes or expose credentials (#180)."""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_repository_access.py"
spec = importlib.util.spec_from_file_location("repository_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def payload(actor="fixture", permission="WRITE"):
    return {"data": {"viewer": {"login": actor}, "repository": {
        "nameWithOwner": "aska-digital/exo", "isPrivate": True, "viewerPermission": permission}}}


@pytest.mark.parametrize("permission", ["READ", "TRIAGE", "WRITE", "MAINTAIN", "ADMIN"])
def test_role_is_not_presented_as_proof_of_write_or_permanent_access(permission):
    report = preflight.summarize(payload(permission=permission), "FIXTURE")
    assert report["actor_matches_expected"]
    assert report["account_write_role"] is (permission in {"WRITE", "MAINTAIN", "ADMIN"})
    assert not report["write_operation_verified"] and not report["permanent_access_verified"]
    assert "fixture" not in json.dumps(report).lower()


def test_wrong_actor_fails_closed_without_printing_the_actor(monkeypatch, capsys):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload("other-fixture")).encode())
    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.main(["--expected-actor", "fixture"]) == 1
    output = capsys.readouterr().out
    assert not json.loads(output)["actor_matches_expected"]
    assert "other-fixture" not in output
    assert calls == [["gh", "api", "graphql", "-f", "query=" + preflight.QUERY]]
    assert "mutation" not in preflight.QUERY


@pytest.mark.parametrize("bad", [None, [], {}, {"errors": [{"message": "private-token-fixture"}]},
    {"data": {"viewer": {"login": "fixture"}, "repository": None}},
    {"data": {"viewer": {"login": "fixture"}, "repository": {
        "nameWithOwner": "other/private-fixture", "isPrivate": True, "viewerPermission": "ADMIN"}}}])
def test_bad_or_unavailable_repository_is_not_verified(bad):
    with pytest.raises(ValueError, match="could not be verified"):
        preflight.summarize(bad, "fixture")


@pytest.mark.parametrize("failure", [OSError("private-token-fixture"),
                                     subprocess.TimeoutExpired("private-command-fixture", 30)])
def test_tool_errors_are_sanitized(monkeypatch, capsys, failure):
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.main(["--expected-actor", "fixture"]) == 2
    output = capsys.readouterr().out
    assert "private-token-fixture" not in output and "private-command-fixture" not in output
    assert json.loads(output)["error"]
