"""D-032 items 3 and 7: the facts /handoff is built from, the seat default cwd, and the cwd guard."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import shlex
from datetime import date
from pathlib import Path

import pytest

from exo.core import workspace
from exo.core.engine import CoreEngine
from exo.core.seats import parse
from exo.core.types import ToolCall, ToolResult
from exo.protocol import schema as s


def run(coro):
    return asyncio.run(coro)


def engine(tmp_path, rules=None, **kw) -> CoreEngine:
    models = {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}, "fake": {"rules": rules or []}}
    kw.setdefault("cwd", str(tmp_path))
    return CoreEngine(tmp_path / "home", models, env={}, **kw)


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@x", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                        "HOME": str(cwd)})


def repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("one")
    git(path, "add", "a.txt")
    git(path, "commit", "-q", "-m", "first commit")
    (path / "b.txt").write_text("two")  # untracked: shows in status
    return path


def notices(eng, sid=None):
    return [e.body.text for e in eng.hub.since(0) if isinstance(e.body, s.Notice) and (sid is None or e.session == sid)]


# ---------------------------------------------------------------- workspace helpers


def test_git_root_and_notice(tmp_path):
    r = repo(tmp_path / "proj")
    (r / "sub" / "deep").mkdir(parents=True)
    assert workspace.git_root(r / "sub" / "deep") == r.resolve()
    assert workspace.cwd_notice(r / "sub") is None
    plain = tmp_path / "plain"
    plain.mkdir()
    line = workspace.cwd_notice(plain)
    assert line and "not inside a git repo" in line and "\n" not in line and str(plain) in line
    assert "—" not in line and "–" not in line


def test_git_file_marks_a_worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere")  # a linked worktree has a .git file
    assert workspace.git_root(wt) == wt.resolve()


def test_leading_cd(tmp_path):
    assert workspace.leading_cd("cd sub && ls", tmp_path) == tmp_path / "sub"
    assert workspace.leading_cd("cd /abs/x; ls", tmp_path) == Path("/abs/x")
    assert workspace.leading_cd('cd "a b" && ls', tmp_path) == tmp_path / "a b"
    assert workspace.leading_cd("ls && cd sub", tmp_path) is None


def test_git_facts(tmp_path):
    r = repo(tmp_path / "proj")
    f = workspace.git_facts(r)
    assert f["branch"] == "main" and f["changed"] == ["?? b.txt"] and f["recent"][0].endswith("first commit")
    assert workspace.git_facts(tmp_path / "nowhere") == {"repo": None}


def test_required_handoff_never_runs_workspace_fsmonitor(tmp_path, trusted_gate):
    r = repo(tmp_path / "workspace")
    outside = tmp_path / "outside-marker"
    hook = r / ".git" / "evil-fsmonitor"
    hook.write_text(f"#!/bin/sh\nprintf exploited > {shlex.quote(str(outside))}\n")
    hook.chmod(0o755)
    git(r, "config", "core.fsmonitor", str(hook))
    # Positive control: this Git build executes the writable workspace configuration.
    workspace.git_facts(r)
    assert outside.exists()
    outside.unlink()

    class Broker:
        async def execute(self, request, tool, run_trusted):
            return ToolResult.of_text(request.call_id, "denied", True)

    eng = engine(tmp_path, cwd=str(r), executor=Broker(), executor_required=True, plugins=trusted_gate)

    async def go():
        try:
            info = await eng.loop.create_session(title="work", cwd=str(r))
            facts = (await eng._command_run(s.CommandRun(name="handoff_facts", session=info.id)))["output"]
            assert facts["git"]["repo"] is None and "isolated" in facts["git"]["unavailable"]
            assert not outside.exists()
        finally:
            await eng.close()

    run(go())


def test_handoff_path(tmp_path, monkeypatch):
    d = date(2026, 10, 12)
    home = Path(os.environ["EXO_HOME"])
    assert workspace.handoff_path("razi", "", d) == home / "handoffs" / "razi-2026-10-12.md"   # the default
    (home).mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"handoff_dir": str(tmp_path / "cfg")}))
    assert workspace.handoff_path("razi", "", d) == tmp_path / "cfg" / "razi-2026-10-12.md"   # the config key
    monkeypatch.setenv("EXO_HANDOFF_DIR", str(tmp_path / "env"))
    assert workspace.handoff_path("razi", "", d) == tmp_path / "env" / "razi-2026-10-12.md"   # the env wins
    assert workspace.handoff_path("razi", str(tmp_path) + "/", d) == tmp_path / "razi-2026-10-12.md"
    assert workspace.handoff_path("razi", str(tmp_path), d) == tmp_path / "razi-2026-10-12.md"  # an existing folder
    assert workspace.handoff_path("razi", str(tmp_path / "x.md"), d) == tmp_path / "x.md"


# ---------------------------------------------------------------- handoff facts


def test_handoff_facts_collect_thread_git_findings_and_a_jobs_hook(tmp_path):
    r = repo(tmp_path / "proj")
    eng = engine(tmp_path, [{"match": "go", "steps": ["done"]}], cwd=str(r))

    async def go():
        info = await eng.loop.create_session(title="work", cwd=str(r))
        await eng.loop.send(info.id, "go")
        eng.findings.file("manual:x", "Something odd", source="principal", severity="bug")
        eng.hub.publish(s.TaskUpdated(task_id="k1", title="scan the repo", status="running"), info.id)
        eng.hub.publish(s.TaskUpdated(task_id="k2", title="old", status="done"), info.id)
        return info.id, (await eng._command_run(s.CommandRun(name="handoff_facts", args=str(tmp_path / "out") + "/",
                                                             session=info.id)))["output"]

    sid, f = run(go())
    assert f["seat"] == "default" and f["thread"]["id"] == sid and f["thread"]["user_turns"] == 1
    assert f["path"] == str(tmp_path / "out" / f"default-{f['date']}.md")
    assert f["git"]["branch"] == "main" and f["git"]["changed"] == ["?? b.txt"]
    assert [x["title"] for x in f["findings"]] == ["Something odd"]
    assert f["tasks"] == [{"id": "k1", "title": "scan the repo", "status": "running"}]
    assert f["jobs"] == [] and "trace" in f and "decisions" in f


def test_handoff_jobs_hook_takes_a_source_and_survives_a_broken_one(tmp_path):
    eng = engine(tmp_path)
    eng.handoff_sources["jobs"] = lambda session: [{"id": "j1", "command": "make", "state": "running"}]

    def boom(session):
        raise RuntimeError("nope")

    eng.handoff_sources["extra"] = boom

    async def go():
        info = await eng.loop.create_session(title="w")
        return await eng.handoff_facts(info.id)

    f = run(go())
    assert f["jobs"][0]["id"] == "j1" and f["extra"] == []


def test_handoff_facts_need_a_thread(tmp_path):
    from exo.server.engine import CommandFailed

    eng = engine(tmp_path)
    with pytest.raises(CommandFailed):
        run(eng.handoff_facts("nope"))


# ---------------------------------------------------------------- seat default cwd and the guard


def test_seat_cwd_parses_and_sets_the_default(tmp_path):
    home = tmp_path / "live"
    home.mkdir()
    seat = parse({"name": "canary", "session": {"cwd": str(home)}})
    assert seat.cwd == str(home) and parse({"name": "c2", "cwd": "~/x"}).cwd == "~/x" and parse({"name": "c3"}).cwd is None
    eng = engine(tmp_path, seat=seat, cwd=str(tmp_path))
    assert eng.cwd == str(home)  # the seat's folder beats the shared default

    async def go():
        return await eng._session_create(s.SessionCreate(title="t"))

    assert run(go())["session"]["cwd"].endswith("live")


def test_seat_cwd_that_is_not_a_folder_is_ignored_and_said(tmp_path):
    seat = parse({"name": "canary", "session": {"cwd": str(tmp_path / "missing")}})
    eng = engine(tmp_path, seat=seat)
    assert eng.cwd == str(tmp_path) and "not a folder" in eng.cwd_note
    run(eng._session_create(s.SessionCreate(title="t")))
    assert any("missing" in n and "not inside a git repo" in n for n in notices(eng))


def test_session_start_notice_only_outside_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    eng = engine(tmp_path, cwd=str(plain))
    out = run(eng._session_create(s.SessionCreate(title="t")))
    got = notices(eng, out["session"]["id"])
    assert len(got) == 1 and "not inside a git repo" in got[0]
    r = repo(tmp_path / "proj")
    eng2 = engine(tmp_path / "e2", cwd=str(r))
    out2 = run(eng2._session_create(s.SessionCreate(title="t")))
    assert notices(eng2, out2["session"]["id"]) == []
    out3 = run(eng2._session_create(s.SessionCreate(title="t", cwd=str(plain))))  # an explicit cwd is checked too
    assert len(notices(eng2, out3["session"]["id"])) == 1


def test_cd_guard_warns_once_per_folder_on_a_shell_call(tmp_path):
    r = repo(tmp_path / "proj")
    plain = tmp_path / "plain"
    plain.mkdir()
    eng = engine(tmp_path, cwd=str(r))

    async def go():
        info = await eng.loop.create_session(title="t", cwd=str(r))
        for cmd in (f"cd {plain} && ls", f"cd {plain} && ls -a", "ls", f"cd {r} && ls"):
            await eng.run_tool(ToolCall("c", "bash", {"command": cmd}), {"session": info.id})
        await eng.run_tool(ToolCall("c", "bash", {"command": "ls", "cwd": str(plain)}), {"session": info.id})
        return info.id

    sid = run(go())
    got = notices(eng, sid)
    assert len(got) == 1 and str(plain.resolve()) in got[0] or str(plain) in got[0]
    assert not any(n for n in notices(eng, sid) if str(r) in n)


def test_handoff_lists_open_background_jobs(tmp_path):
    import time

    eng = engine(tmp_path)

    async def go():
        info = await eng.loop.create_session(title="w")
        now = time.time()
        for jid, state in (("j_run", "running"), ("j_done", "exited")):
            eng.store._q("INSERT INTO jobs (id, agent, session, command, cwd, state, started, log) VALUES (?,?,?,?,?,?,?,?)",
                         (jid, eng.agent, info.id, "make test", str(tmp_path), state, now - 90, f"/x/{jid}.log"))
        return await eng.handoff_facts(info.id)

    f = run(go())
    assert [j["id"] for j in f["jobs"]] == ["j_run"] and f["jobs"][0]["command"] == "make test"
    assert f["jobs"][0]["state"] == "running" and f["jobs"][0]["elapsed"]
