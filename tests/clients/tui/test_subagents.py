"""Issue #97: /tasks, /open <task>, /steer <task> <text> and /stop <task> in the TUI, over the core's `subagent` built-in."""

from __future__ import annotations

from exo.protocol import schema as s
from exo.wire import Brief, encode
from test_tui import run, send, until

def add_task(eng, parent, status, title="CHILD-A work"):
    """A finished (or never started) delegation row with a real child session, as the Delegator leaves it."""
    info = eng.store.create_session(title="builder: " + title, parent=parent, kind="subagent")
    eng.store._q("INSERT INTO tasks (id, parent, child, depth, title, status, brief, created, updated) VALUES "
                 "(?,?,?,?,?,?,?,?,?)", ("k_abc123", parent, info.id, 1, title, status, encode(Brief(id="k_abc123", thread=parent, goal=title)), s.now_ts(), s.now_ts()))
    return info.id


def commands(app):
    return [b.args for b in app.sent if isinstance(b, s.CommandRun) and b.name == "subagent"]


def test_tasks_open_steer_stop_use_the_subagent_builtin(tmp_path):
    async def sc(app, pilot, client, eng):
        child = add_task(eng, app.session, "done")
        await send(app, pilot, "/tasks")
        await until(lambda: "k_abc123" in app.transcript_text(), pilot)
        assert "[done]" in app.transcript_text() and commands(app) == ["list"]
        await send(app, pilot, "/steer k_abc123 try again")
        await until(lambda: "finished (done)" in app.transcript_text(), pilot)  # a finished child: a clear error
        await send(app, pilot, "/stop k_abc123")
        await until(lambda: len(commands(app)) == 3, pilot)
        assert commands(app)[1:] == ["send k_abc123 try again", "stop k_abc123"]
        await send(app, pilot, "/open k_abc")
        await until(lambda: app.session == child, pilot)
        assert "finished" in app.transcript_text()  # the hint says so; nothing resumed the child's loop
        await send(app, pilot, "/open")
        await until(lambda: "usage: /open <task>" in app.transcript_text(), pilot)

    run(tmp_path, sc)


def test_bare_stop_still_stops_the_turn(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/stop")
        await pilot.pause(0.1)
        assert commands(app) == []

    run(tmp_path, sc)
