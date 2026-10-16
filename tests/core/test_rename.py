"""Issue #98: /rename <title> is a built-in command.run (no new protocol command); only the principal may use it."""
from __future__ import annotations

import asyncio

from exo import complete as cm
from exo.core.engine import CoreEngine
from exo.protocol import schema as s
from exo.server.app import Dispatcher

FAKE = {"rules": [{"match": "hello", "steps": ["hi"]}]}


def engine(tmp_path) -> CoreEngine:
    return CoreEngine(tmp_path / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                      cwd=str(tmp_path), env={})


def run(d, body):
    return asyncio.run(d.run(s.Command.of(body)))


def titles(d):
    return {r["id"]: r["title"] for r in run(d, s.SessionList()).result["sessions"]}


def test_rename_persists_across_reopen_and_announces(tmp_path):
    d = Dispatcher(engine(tmp_path), caller="principal:tui")
    sid = run(d, s.SessionCreate(title="old")).result["session"]["id"]
    got = run(d, s.CommandRun(name="rename", args="  Billing refactor ", session=sid))
    assert got.ok, got.error
    assert titles(d)[sid] == "Billing refactor"  # what /sessions and /resume read
    evs = [e for e in d.hub.since(0) if e.type == "session.updated" and e.body.session.title == "Billing refactor"]
    assert evs
    d2 = Dispatcher(engine(tmp_path), caller="principal:tui")  # a reopened store
    assert titles(d2)[sid] == "Billing refactor"


def test_rename_needs_a_title_and_a_session(tmp_path):
    d = Dispatcher(engine(tmp_path), caller="principal:tui")
    sid = run(d, s.SessionCreate(title="old")).result["session"]["id"]
    assert run(d, s.CommandRun(name="rename", args="  ", session=sid)).error.code == "bad_request"
    assert run(d, s.CommandRun(name="rename", args="x")).error.code == "bad_request"
    assert run(d, s.CommandRun(name="rename", args="x", session="nope")).error.code == "not_found"
    assert titles(d)[sid] == "old"


def test_non_principal_cannot_rename(tmp_path):
    eng = engine(tmp_path)
    sid = run(Dispatcher(eng, caller="principal:tui"), s.SessionCreate(title="mine")).result["session"]["id"]
    for who in ("local", "unknown", "seat:helper"):
        d = Dispatcher(eng, caller=who)
        got = run(d, s.CommandRun(name="rename", args="hijack", session=sid))
        assert not got.ok and got.error.code == "forbidden"
        assert titles(d)[sid] == "mine"


def test_completion_lists_rename():
    cat = cm.build_catalog({"rename": "rename this session", "resume": "pick a thread"})
    assert "rename" in [i.value for i in cm.suggest("/ren", cat).items]
    assert cm.ARG_HINTS["rename"] == "<title>"
