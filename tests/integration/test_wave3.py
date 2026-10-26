"""Wave 3 through the engine: ui panels, schedule.edit, feedback.send, turn hooks, locked sessions."""

import asyncio

from exo.core.engine import CoreEngine
from exo.scheduler import Scheduler
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo.server.app import Dispatcher

PANEL = {"action": "open", "kind": "diff", "title": "change", "text": "one line changed",
         "data": {"files": [{"path": "a.py", "diff": "-x\n+y"}]}, "reason": "a diff reads better"}
MODELS = {"fake": {"rules": [{"match": "panel", "steps": [{"tool": "ui", "args": PANEL}, "done"]}]},
          "roles": {"main": ["fake:x"], "aux": ["fake:x"], "fast": ["fake:x"]}}


def _eng(tmp_path, register=None):
    pm = PluginManager()
    pm.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    if register:
        pm._run_register(Manifest(name="t"), "dir", register)
    return CoreEngine(tmp_path / "home", MODELS, plugins=pm, cwd=str(tmp_path), env={}, attach_scheduler=Scheduler)


async def _turn(d, eng, text, sid=None):
    if sid is None:
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    res = await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    while eng._tasks:
        await asyncio.sleep(0.01)
    return sid, res


def test_ui_tool_emits_panel_and_turn_hooks_fire(tmp_path):
    seen = []

    def reg(ctx):
        ctx.register_hook("on_turn_start", lambda **kw: seen.append(("start", kw["session_id"])))
        ctx.register_hook("on_turn_end", lambda **kw: seen.append(("end", kw["session_id"])))

    eng = _eng(tmp_path, reg)

    async def main():
        d = Dispatcher(eng, caller="principal:tui")
        sid, _ = await _turn(d, eng, "panel please")
        panels = [ev for ev in eng.hub.since(0) if isinstance(ev.body, s.UiPanel)]
        assert len(panels) == 1 and panels[0].session == sid
        p = panels[0].body
        assert p.kind == "diff" and p.text == "one line changed" and p.data["files"][0]["path"] == "a.py"
        assert seen == [("start", sid), ("end", sid)]
        r = await d.run(s.Command.of(s.UiRespond(panel_id=p.panel_id, action="dismissed")))
        assert r.ok  # learning off: recorded nothing, still answers

    asyncio.run(main())


def test_schedule_edit_round_trip(tmp_path):
    eng = _eng(tmp_path)

    async def main():
        d = Dispatcher(eng, caller="principal:tui")
        job = (await d.run(s.Command.of(s.ScheduleEdit(action="add", when="every 1d", prompt="hello",
                                                       title="daily")))).result["job"]
        jobs = (await d.run(s.Command.of(s.ScheduleEdit(action="list")))).result["jobs"]
        assert [j["title"] for j in jobs] == ["daily"]
        jid = job.get("id") or job.get("job_id")
        assert (await d.run(s.Command.of(s.ScheduleEdit(action="remove", job_id=jid)))).ok
        bad = await d.run(s.Command.of(s.ScheduleEdit(action="remove", job_id="nope")))
        assert not bad.ok
        await eng.close()

    asyncio.run(main())


def test_feedback_without_learning_store_is_harmless(tmp_path):
    eng = _eng(tmp_path)

    async def main():
        d = Dispatcher(eng, caller="principal:tui")
        sid, _ = await _turn(d, eng, "hi")
        r = await d.run(s.Command.of(s.FeedbackSend(session=sid, verdict="edit", text="better")))
        assert r.ok and r.result.get("recorded") is False

    asyncio.run(main())


def test_locked_session_refuses_turns(tmp_path):
    eng = _eng(tmp_path)

    async def main():
        d = Dispatcher(eng)
        sid, _ = await _turn(d, eng, "hi")
        eng.store.lock(sid, "done")
        _, res = await _turn(d, eng, "again", sid)
        assert not res.ok and res.error.code == "session_locked"

    asyncio.run(main())
