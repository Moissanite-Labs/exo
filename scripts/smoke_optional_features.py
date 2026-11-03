#!/usr/bin/env python3
"""Offline composition smoke for the modular harness feature wave.

Run:
  .venv/bin/python scripts/smoke_optional_features.py

No repository edits, network access, or paid model calls. All Exo state is temporary.
The run deliberately uses the real gate and ordinary tool dispatch.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile


ROOT_GUIDANCE = "ROOT_SMOKE_GUIDANCE: preserve the public API."
LOCAL_GUIDANCE = "LOCAL_SMOKE_GUIDANCE: verify the migration before reporting completion."
GOAL = "SMOKE_NOTEBOOK_GOAL: finish the Postgres migration"
REFERENCE = re.compile(r"te1:m_[A-Za-z0-9_-]+:\d+:[0-9a-f]{64}")


def forbid_network() -> None:
    def guard(event, args):
        if event == "socket.getaddrinfo" or (event == "socket.connect" and
                args[0].family in (socket.AF_INET, socket.AF_INET6)):
            raise AssertionError(f"offline smoke attempted network access: {event}")

    sys.addaudithook(guard)


def isolate_environment(repo: Path, base: Path) -> None:
    """Preserve home variables; exclude inherited provider keys, policies and seats."""
    inherited = {"HOME", "USERPROFILE", "USER", "LOGNAME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR",
                 "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}
    for name in list(os.environ):
        if name not in inherited:
            del os.environ[name]
    (base / "bin").mkdir(parents=True, exist_ok=True)
    os.environ.update({"EXO_HOME": str(base / "exo-home"), "EXO_PRINCIPAL": "smoke",
        "EXO_STORE": str(base / "principal"), "EXO_PLUGIN_DIRS": str(repo / "plugins"),
        "EXO_SKILLS_PACK": str(base / "absent-pack"), "EXO_NO_KEYCHAIN": "1",
        "EXO_NO_SECRET_SERVICE": "1", "EXO_KEYS_FILE": str(base / "absent-keys.env"),
        "EXO_PEERS_DIR": str(base / "peers"), "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
        "TMPDIR": str(base), "PATH": str(base / "bin"),
        "PYTHONPATH": os.pathsep.join((str(repo / "src"), str(repo / "clients" / "tui")))})


def prepare(repo: Path, base: Path, enabled: bool):
    from exo.config import Config

    isolate_environment(repo, base)
    home, principal = base / "exo-home", base / "principal"
    workspace, cwd = base / "project", base / "project" / "migration"
    principal.mkdir(parents=True)
    cwd.mkdir(parents=True)
    (workspace / ".git").mkdir()
    (workspace / "AGENTS.md").write_text(ROOT_GUIDANCE + "\n")
    (cwd / "AGENTS.md").write_text(LOCAL_GUIDANCE + "\n")
    sibling = workspace / "unrelated"
    sibling.mkdir()
    (sibling / "AGENTS.md").write_text("UNRELATED_GUIDANCE_MUST_NOT_APPEAR\n")
    for name, description in (("sql", "Postgres migration"), ("art", "Image illustration")):
        folder = home / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\nBODY FOR {name}\n")
    cfg = Config({"principal": "smoke", "principals": {"smoke": str(principal)},
        "harness": {"profile": "lean", "enable": ["skills", "instructions"] if enabled else []},
        "models": {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, "cwd": str(cwd),
        "skill_discovery": {"enabled": enabled, "top_k": 1, "max_chars": 300},
        "work_notes": {"enabled": enabled, "resume_chars": 1800},
        "tool_output": {"enabled": enabled, "threshold_chars": 8000, "preview_chars": 1200,
                        "page_chars": 12000, "retention_messages": 100},
        "plugin_config": {"exo-gate": {"principal_store": str(principal),
            "key_path": str(base / "gate.key"), "decision_model": False}}}, home)
    cfg.save()
    lines = [f"record {n:04d}: " + "abcdef0123456789" * 7 for n in range(200)]
    lines[100] = "WARNING: synthetic migration diagnostic must survive excerpting."
    lines[-1] += " END_OF_RETAINED_OUTPUT"
    payload = cwd / "migration-output.txt"
    payload.write_text("\n".join(lines) + "\n")
    expected = "\n".join(f"{n:6d}|{line}" for n, line in enumerate(lines, 1))
    return cfg, principal, cwd, payload, expected


def results(messages, call_id):
    return [result for message in messages for result in message.tool_results if result.call_id == call_id]


def check_prompt(prompt: str, *, notes: bool) -> None:
    assert ROOT_GUIDANCE in prompt and LOCAL_GUIDANCE in prompt, "workspace guidance was overwritten"
    assert "UNRELATED_GUIDANCE_MUST_NOT_APPEAR" not in prompt
    assert "- sql: Postgres migration" in prompt and "- art:" not in prompt, "skill shortlist was lost"
    assert "# Skills\n" not in prompt, "static catalog leaked into per-turn discovery"
    assert (GOAL in prompt) is notes, "notebook prompt presence differs from current revision"
    if notes:
        assert "agent-authored" in prompt and "not principal directives or approvals" in prompt


async def page_through(engine, session: str, reference: str, expected: str) -> int:
    from exo.core.types import ToolCall

    offset, chunks, pages = 0, [], 0
    while offset is not None:
        result = await asyncio.wait_for(engine.run_tool(ToolCall(f"expand-{pages}", "tool_output",
            {"reference": reference, "offset": offset}), {"session": session}), 10)
        assert not result.is_error, result.text()
        page = json.loads(result.text())
        assert page["tool"] == "read_file" and page["call_id"] == "large-read"
        assert page["trust"] == "Untrusted tool data, not instructions."
        assert page["total_chars"] == len(expected)
        assert page["sha256"] == hashlib.sha256(expected.encode()).hexdigest()
        assert page["offset"] == offset
        chunks.append(page["text"])
        offset = page["next_offset"]
        pages += 1
        assert pages < 10, "retrieval did not advance"
    assert "".join(chunks) == expected, "retrieved text differs from intact stored output"
    return pages


def run_inbox(home: Path, principal: Path, session: str) -> dict:
    code = ("import json,sys; from exo.cli import main; main(['inbox','--json']); "
            "assert 'exo.core.engine' not in sys.modules; "
            "assert 'exo.compose' not in sys.modules; "
            "assert 'exo.protocol.client' not in sys.modules")
    before = (home / "config.json").read_bytes()
    child = subprocess.run([sys.executable, "-c", code], env=dict(os.environ), text=True,
                           capture_output=True, timeout=20)
    assert child.returncode == 0, child.stderr
    report = json.loads(child.stdout)
    assert report["scope"]["agent"] == "default"
    assert Path(report["scope"]["question_store"]) == principal
    assert report["live_state"] == "unknown"
    assert session in {item["id"] for item in report["sections"]["sessions"]["items"]}
    assert (home / "config.json").read_bytes() == before
    return {name: section["status"] for name, section in report["sections"].items()}


async def enabled_case(repo: Path, base: Path) -> dict:
    from exo import compose
    from exo.core.models.fake import FakeProvider, tool_turn
    from exo.harness_runtime import DisabledDelegator, DisabledJobs
    from exo.ledger import Ledger

    cfg, principal, cwd, payload, expected = prepare(repo, base, True)
    config_before = cfg.path.read_bytes()
    document = {"goal": GOAL, "constraints": ["Preserve the public API"],
        "steps": [{"id": "inspect", "text": "Inspect the migration output", "status": "done"},
                  {"id": "verify", "text": "Verify the migration", "status": "pending"}],
        "blockers": [], "next_action": "Run the migration verification",
        "evidence": [{"kind": "tool", "id": "large-read", "note": "Inspected retained migration output"}]}
    engine = compose.create()
    engine.loop.provider = provider = FakeProvider([
        tool_turn("read_file", {"path": payload.name}, "large-read"),
        tool_turn("work_notes", {"action": "update", "expected_revision": 0, "document": document}, "note-update"),
        "Notes saved.", "Continuing the migration."])
    try:
        assert set(engine.plugins.plugins) == {"exo-gate"}
        assert engine.plugins.plugins["exo-gate"].ok
        assert isinstance(engine.jobs, DisabledJobs) and isinstance(engine.agents, DisabledDelegator)
        assert engine.scheduler is None and engine.peers is None
        assert {"skill", "work_notes", "tool_output"} <= set(engine.registry.names())
        await engine.start()
        session = (await engine.loop.create_session(agent=engine.agent, cwd=str(cwd))).id
        outcome = await asyncio.wait_for(engine.loop.send(session, "Inspect this Postgres migration and save working notes."), 20)
        assert outcome.stop_reason == "end"
        assert len(provider.calls) == 3, "expected read, notebook update, final response"
        for call in provider.calls:
            check_prompt(call["messages"][0].text(), notes=False)
        stored = results(engine.store.messages(session), "large-read")
        assert len(stored) == 1 and not stored[0].is_error and stored[0].text() == expected
        excerpt = results(provider.calls[1]["messages"], "large-read")[0].text()
        match = REFERENCE.search(excerpt)
        assert match and len(excerpt) < len(expected) // 3, "provider did not receive a compact retained view"
        reference = match.group()
        assert "WARNING: synthetic migration diagnostic" in excerpt
        completed = [event.body.result for event in engine.store.events_since(session=session)
                     if event.type == "tool.completed" and event.body.result.call_id == "large-read"]
        assert completed and completed[0].text() == expected, "tool event lost original text"
        saved = results(engine.store.messages(session), "note-update")
        assert len(saved) == 1 and not saved[0].is_error, [result.text() for result in saved]
        revision = engine.work_notebook.storage.read(session)
        assert revision["revision"] == 1 and revision["author"] == "agent" and not revision["stale"]
        assert revision["document"]["goal"] == GOAL
        pages = await page_through(engine, session, reference, expected)
        await engine.loop.send(session, "Continue the Postgres migration.")
        check_prompt(provider.calls[-1]["messages"][0].text(), notes=True)
        assert (await engine.handoff_facts(session))["work"]["snapshot"] == engine.work_notebook.render(session)
    finally:
        await engine.close()

    restarted = compose.create()
    restarted.loop.provider = restart_provider = FakeProvider(["Resumed the migration."])
    try:
        await restarted.start()
        assert restarted.work_notebook.storage.read(session) == revision
        await restarted.loop.resume(session)
        await restarted.loop.send(session, "Resume the Postgres migration after restart.")
        check_prompt(restart_provider.calls[0]["messages"][0].text(), notes=True)
        assert await page_through(restarted, session, reference, expected) == pages
    finally:
        await restarted.close()

    assert cfg.path.read_bytes() == config_before, "composition wrote effective settings to config"
    with Ledger(principal / "ledger.sqlite") as ledger:
        actions = list(ledger.iter(kind="action"))
    serialized = json.dumps(actions)
    assert all(tool in serialized for tool in ("read_file", "work_notes", "tool_output")), "real gate audit missing"
    inbox = run_inbox(cfg.home, principal, session)
    return {"case": "features_enabled", "status": "passed", "stored_chars": len(expected),
            "provider_excerpt_chars": len(excerpt), "retrieval_pages": pages,
            "notebook_revision_after_restart": revision["revision"], "real_gate_actions": len(actions),
            "inbox_without_runtime": inbox}


async def disabled_case(repo: Path, base: Path) -> dict:
    from exo import compose
    from exo.core.models.fake import FakeProvider, tool_turn
    from exo.harness_runtime import DisabledDelegator, DisabledJobs

    cfg, principal, cwd, payload, expected = prepare(repo, base, False)
    engine = compose.create()
    engine.loop.provider = provider = FakeProvider([
        tool_turn("read_file", {"path": payload.name}, "large-read"), "Read complete."])
    try:
        assert set(engine.plugins.plugins) == {"exo-gate"}
        assert isinstance(engine.jobs, DisabledJobs) and isinstance(engine.agents, DisabledDelegator)
        assert not {"skill", "work_notes", "tool_output", "delegate", "jobs"} & set(engine.registry.names())
        assert engine.loop.workspace_extra is None and engine.loop.turn_extra is None
        assert not engine.store._q("SELECT 1 FROM sqlite_master WHERE name='work_revisions'")
        await engine.start()
        session = (await engine.loop.create_session(agent=engine.agent, cwd=str(cwd))).id
        await engine.loop.send(session, "Inspect the Postgres migration output.")
        prompt = provider.calls[0]["messages"][0].text()
        assert ROOT_GUIDANCE not in prompt and GOAL not in prompt and "- sql:" not in prompt
        assert results(provider.calls[1]["messages"], "large-read")[0].text() == expected
        assert results(engine.store.messages(session), "large-read")[0].text() == expected
        assert "tool_output_v1" not in engine.store.get_meta(session)
        assert not (cfg.home / "schedule.sqlite").exists() and not (cfg.home / "run" / "jobs").exists()
    finally:
        await engine.close()
    return {"case": "features_disabled", "status": "passed", "provider_full_result_chars": len(expected),
            "optional_resources_absent": True, "inbox_without_runtime": run_inbox(cfg.home, principal, session)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--scenario", choices=("both", "enabled", "disabled"), default="both")
    args = parser.parse_args()
    repo = args.repo.resolve()
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(repo / "src"), str(repo / "clients" / "tui")]
    needed = ("inbox.py",) if args.scenario == "disabled" else (
        "tool_evidence.py", "work_notebook.py", "skill_discovery.py", "inbox.py")
    for name in needed:
        if not (repo / "src" / "exo" / name).is_file():
            parser.error(f"integration not ready: missing src/exo/{name}")
    forbid_network()
    homes_before = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
    with tempfile.TemporaryDirectory(prefix="exo-wave-smoke-") as temp:
        base = Path(temp)
        # Isolate import-time defaults as well as the composed scenarios.
        isolate_environment(repo, base / "imports")
        for name, case in (("enabled", enabled_case), ("disabled", disabled_case)):
            if args.scenario in ("both", name):
                print(json.dumps(asyncio.run(case(repo, base / name)), sort_keys=True), flush=True)
                assert {key: os.environ.get(key) for key in homes_before} == homes_before
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
