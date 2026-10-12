"""Textual pilot tests. In-process core engine with the scripted fake model; env={}, no network."""

from __future__ import annotations

import asyncio
import time

from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.conn import LocalClient
from exo_tui.panels import bar_chart, render_panel
from exo_tui.widgets import ApprovalCard, Block, Composer, Palette, PanelView, fuzzy

FAKE = {"rules": [
    {"match": "hello", "steps": ["Hello **there**, friend"]},
    {"match": "ask", "steps": [{"tool": "bash", "args": {"command": "echo asked"}}, "after ask"]},
]}


class FakeServerClient(LocalClient):
    """Real engine for everything it supports; feedback.send and ui.respond are recorded and acked
    (the lead wires those handlers into the engine)."""

    def __init__(self, engine):
        super().__init__(engine)
        self.seen: list = []

    async def send(self, body):
        if isinstance(body, (s.FeedbackSend, s.UiRespond)):
            self.seen.append(body)
            return s.Response(id="x", result={})
        return await super().send(body)


def make_engine(tmp_path) -> CoreEngine:
    pm = PluginManager()

    def register(ctx):
        def pre(tool_name="", args=None, **kw):
            if "asked" in (args or {}).get("command", ""):
                return {"action": "approve", "message": "run echo?", "rule_key": "echo"}
            return None

        ctx.register_hook("pre_tool_call", pre)

    pm._run_register(Manifest(name="t"), "dir", register)
    return CoreEngine(tmp_path / "home", {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                      plugins=pm, cwd=str(tmp_path), env={})


async def until(cond, pilot, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        await pilot.pause(0.02)


def run(tmp_path, scenario):
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        app = ExoApp(client, seat="tester")
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


async def send(app, pilot, text):
    app.query_one(Composer).text = text
    await pilot.press("enter")


def test_send_and_stream(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "hello")
        await until(lambda: "Hello **there**, friend" in app.transcript_text() and not app.busy, pilot)
        assert "hello" in app.transcript_text()
        assert app.last_answer[1] == "Hello **there**, friend"
        assert app.status_text().startswith("tester · ")
        assert "ctx" in app.status_text() and "$" in app.status_text()

    run(tmp_path, sc)


def test_shift_enter_newline(tmp_path):
    async def sc(app, pilot, client, eng):
        c = app.query_one(Composer)
        c.text = "a"
        c.move_cursor((0, 1))
        await pilot.press("shift+enter")
        assert c.text == "a\n"
        await pilot.press("ctrl+j")
        assert c.text == "a\n\n"

    run(tmp_path, sc)


def test_approval_card_allow_and_deny_note(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        assert isinstance(app.card, ApprovalCard)
        await pilot.press("o")
        await until(lambda: any(isinstance(b, s.ApprovalRespond) for b in app.sent), pilot)
        r = [b for b in app.sent if isinstance(b, s.ApprovalRespond)][0]
        assert r.decision == "allow"
        await until(lambda: "after ask" in app.transcript_text(), pilot)
        assert app.card is None

        await send(app, pilot, "ask again")  # runs the same rule; "always" was not chosen
        await until(lambda: app.card is not None, pilot)
        await pilot.press("d")
        await pilot.pause()
        for ch in "nope":
            await pilot.press(ch)
        await pilot.press("enter")
        await until(lambda: len([b for b in app.sent if isinstance(b, s.ApprovalRespond)]) == 2, pilot)
        r2 = [b for b in app.sent if isinstance(b, s.ApprovalRespond)][1]
        assert (r2.decision, r2.note) == ("deny", "nope")

    run(tmp_path, sc)


def diff_panel(pid="p1", **kw):
    data = {"diff": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"}
    return s.UiPanel(panel_id=pid, action="open", kind="diff", title="x.py", text="diff of x", data=data, **kw)


def test_panel_diff_renders_and_dismiss_responds(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(diff_panel(), app.session)
        view = app.query_one(PanelView)
        await until(lambda: view.display, pilot)
        await until(lambda: any(isinstance(b, s.UiRespond) and b.action == "opened" for b in client.seen), pilot)
        text = render_panel("diff", "t", diff_panel().data)
        styles = {str(sp.style) for sp in text.spans}
        assert "green" in styles and "red" in styles
        await pilot.press("escape")
        await until(lambda: any(isinstance(b, s.UiRespond) and b.action == "dismissed" for b in client.seen), pilot)
        assert not view.display
        assert [b.panel_id for b in client.seen] == ["p1", "p1"]

    run(tmp_path, sc)


def test_panel_choice_acts(tmp_path):
    async def sc(app, pilot, client, eng):
        data = {"options": [{"id": "a", "label": "Alpha"}, "beta"]}
        eng.hub.publish(s.UiPanel(panel_id="c1", action="open", kind="choice", text="pick", data=data), app.session)
        view = app.query_one(PanelView)
        await until(lambda: view.display and view.options, pilot)
        await pilot.press("down")
        await pilot.press("enter")
        await until(lambda: any(isinstance(b, s.UiRespond) and b.action == "acted" for b in client.seen), pilot)
        act = [b for b in client.seen if b.action == "acted"][0]
        assert act.value == {"choice": "beta"}

    run(tmp_path, sc)


def test_panel_close_unseen_is_ignored(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(diff_panel("p2"), app.session)
        await until(lambda: app.query_one(PanelView).display, pilot)
        eng.hub.publish(s.UiPanel(panel_id="p2", action="close"), app.session)
        await until(lambda: any(getattr(b, "action", "") == "ignored" for b in client.seen), pilot)
        assert not app.query_one(PanelView).display

    run(tmp_path, sc)


def test_every_panel_kind_renders_and_unknown_falls_back():
    samples = {
        "markdown": {"markdown": "# hi"},
        "table": {"columns": ["a", "b"], "rows": [[1, 2], [3, 4]]},
        "tasks": {"tasks": [{"title": "one", "status": "done"}, {"title": "two"}]},
        "taste": {"rules": [{"text": "short prose", "path": "t.md"}]},
        "file": {"path": "a.py", "content": "print(1)"},
        "chart": {"labels": ["a", "b"], "values": [1, 2]},
        "approval": {},
        "weird": {"x": 1},
    }
    for kind, data in samples.items():
        assert render_panel(kind, "fallback text", data) is not None
    assert str(render_panel("weird", "fallback text", {"x": 1})) == "fallback text"
    assert str(render_panel("table", "fb", {"bad": 1})) == "fb"
    chart = bar_chart({"labels": ["a", "b"], "values": [1, 2]}, width=10)
    assert "██████████" in chart.plain and chart.plain.count("█") == 15


def test_verdict_keys_send_feedback(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "hello")
        await until(lambda: app.last_answer[1] and not app.busy, pilot)
        mid = app.last_answer[0]
        await pilot.press("ctrl+y")
        await until(lambda: len(client.seen) == 1, pilot)
        assert (client.seen[0].verdict, client.seen[0].message_id) == ("accept", mid)
        await pilot.press("ctrl+r")
        await until(lambda: len(client.seen) == 2, pilot)
        assert client.seen[1].verdict == "reject"
        await pilot.press("ctrl+e")
        await until(lambda: len(app.screen_stack) > 1, pilot)
        area = app.screen.query_one("#edit-area")
        area.text = "Better answer"
        await pilot.press("ctrl+s")
        await until(lambda: len(client.seen) == 3, pilot)
        assert (client.seen[2].verdict, client.seen[2].text) == ("edit", "Better answer")

    run(tmp_path, sc)


def test_palette_opens_filters_and_runs(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+p")
        await until(lambda: isinstance(app.screen, Palette), pilot)
        for ch in "hlp":
            await pilot.press(ch)
        await pilot.pause()
        assert app.screen.shown and "/help" in app.screen.shown[0][0]
        await pilot.press("enter")
        await until(lambda: not isinstance(app.screen, Palette), pilot)
        await until(lambda: "ctrl+p palette" in app.transcript_text(), pilot)

    run(tmp_path, sc)


def test_slash_new_sessions_and_file_context(tmp_path, monkeypatch):
    async def sc(app, pilot, client, eng):
        first = app.session
        (tmp_path / "notes.txt").write_text("secret-content")
        monkeypatch.chdir(tmp_path)
        await send(app, pilot, "hello see @notes.txt")
        await until(lambda: not app.busy and app.last_answer[1], pilot)
        sent = [b for b in app.sent if isinstance(b, s.TurnSend)][0]
        assert "secret-content" in sent.parts[0].text
        await send(app, pilot, "/new")
        await until(lambda: app.session != first, pilot)
        await send(app, pilot, "/sessions")
        await until(lambda: isinstance(app.screen, Palette), pilot)
        assert len(app.screen.items) == 2
        await pilot.press("escape")
        await send(app, pilot, "!echo hi")
        await until(lambda: [b for b in app.sent if isinstance(b, s.TurnSend) and "echo hi" in b.text], pilot)

    run(tmp_path, sc)


def test_fuzzy_and_tool_collapse(tmp_path):
    assert fuzzy("hlp", "/help") is not None and fuzzy("zzz", "/help") is None
    assert fuzzy("nw", "/new") < fuzzy("nw", "/something new")

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        await pilot.press("o")
        await until(lambda: "after ask" in app.transcript_text(), pilot)
        tb = next(iter(app.tools.values()))
        assert not tb.expanded
        await pilot.press("ctrl+o")
        assert tb.expanded and app.expanded

    run(tmp_path, sc)


def test_waiting_strip_then_text_and_warn_notice(tmp_path):
    async def sc(app, pilot, client, eng):
        app.handle(s.Event.of(s.TurnStarted(turn_id="t1", model="kimi-k3"), 1, app.session))
        app.turn_t0 -= 12  # twelve seconds without a byte
        app.refresh_activity()
        assert "waiting for kimi-k3 12s" in str(app.query_one("#activity").render())
        app.handle(s.Event.of(s.Notice(text="a:m did not answer in 60 s; trying b:m", level="warn"), 2, app.session))
        await pilot.pause(0.05)
        assert "warn: a:m did not answer in 60 s; trying b:m" in app.transcript_text()
        assert any("warn" in w.classes for w in app.transcript.children)
        app.handle(s.Event.of(s.ReasoningDelta(message_id="m1", text="hm"), 3, app.session))
        assert "waiting" not in str(app.query_one("#activity").render())
        assert "working" in str(app.query_one("#activity").render())

    run(tmp_path, sc)


def test_status_shows_findings_and_event_errors_reach_the_core(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.findings.file("sig", "something", severity="bug")
        await send(app, pilot, "hello")
        await until(lambda: "1 findings" in app.status_text(), pilot)
        # an exception in the TUI's own event path becomes a harness.error event (D-025)
        app.handle = lambda ev: (_ for _ in ()).throw(NameError("boom"))
        await send(app, pilot, "hello")
        await until(lambda: any(isinstance(e.body, s.HarnessError) and "tui event" in e.body.where
                                for e in eng.hub.since(0)), pilot)

    run(tmp_path, sc)


def test_help_lists_every_registered_command(tmp_path):
    from exo_tui.app import COMMANDS, GROUPS

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/help")
        await until(lambda: "/help" in app.transcript_text(), pilot)
        lines = [ln.strip() for ln in app.help_text().splitlines()]
        for name in [*COMMANDS, *(c["name"] for c in app.commands)]:
            assert any(ln.startswith(f"/{name}  ") for ln in lines), name
        assert all(f"{g}:" in lines for g in GROUPS)
        assert {"restart", "default", "new", "resume", "sessions", "clear", "quit", "cwd", "usage"} <= set(COMMANDS)

    run(tmp_path, sc)


def test_model_flag_text_is_rejected(tmp_path):
    async def sc(app, pilot, client, eng):
        before = app.model
        await send(app, pilot, "/model --default fake:x")
        await until(lambda: "not a model ref" in app.transcript_text(), pilot)
        assert app.model == before and not any(isinstance(b, s.ModelsSet) for b in app.sent)
        await send(app, pilot, "/model nope:thing")  # unknown provider: the engine says bad_model
        await until(lambda: "bad_model" in app.transcript_text(), pilot)
        assert app.model == before

    run(tmp_path, sc)


def test_default_model_persists_with_fallbacks(tmp_path):
    import json

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/model default fake:y")
        await until(lambda: "default model: fake:y" in app.transcript_text(), pilot)
        cfg = json.loads((tmp_path / "home" / "config.json").read_text())
        assert cfg["models"]["roles"]["main"] == ["fake:y", "fake:x"]

    eng_providers = None  # noqa: F841
    run(tmp_path, sc)


def test_session_update_does_not_clobber_model_in_use(tmp_path):
    async def sc(app, pilot, client, eng):
        app.model = "p:in-use"
        info = s.SessionInfo(id=app.session, model="p:stored")
        app.handle(s.Event.of(s.SessionUpdated(session=info), 1, app.session))
        assert app.model == "p:in-use"

    run(tmp_path, sc)


def test_restart_schedules_new_tab_and_exits(tmp_path, monkeypatch):
    from exo_tui import restart as rs
    calls = []
    monkeypatch.setattr(rs, "schedule_restart", lambda cmd, pid=None: calls.append(cmd))
    monkeypatch.setattr(rs, "has_opener", lambda *a, **k: True)
    monkeypatch.setenv("EXO_RESTART_CMD", "exo-canary /work")

    async def sc(app, pilot, client, eng):
        sid = app.session
        await send(app, pilot, "/restart")
        await until(lambda: calls, pilot)
        assert calls[0] == ["exo-canary", "/work", "--session", sid]

    run(tmp_path, sc)


def test_peers_command_send_and_inline_peer_message(tmp_path):
    import shutil
    import tempfile

    from exo.core.seats import Seat
    from exo.server.app import Dispatcher, HttpServer

    d = tempfile.mkdtemp(prefix="exot", dir="/tmp")  # short: unix socket paths
    try:
        models = {"fake": FAKE, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}
        mine = CoreEngine(tmp_path / "h1", models, cwd=str(tmp_path), env={}, seat=Seat(name="tester"), peers_dir=d)
        other = CoreEngine(tmp_path / "h2", models, cwd=str(tmp_path), env={}, seat=Seat(name="razi"), peers_dir=d)
        client = FakeServerClient(mine)

        async def main():
            srv = HttpServer(Dispatcher(other, other.hub))
            await srv.start_unix(f"{d}/razi.sock")
            await other.start_peers(f"{d}/razi.sock")
            sb = (await other.loop.create_session(title="razi work")).id
            app = ExoApp(client, seat="tester")
            async with app.run_test(size=(120, 40)) as pilot:
                await asyncio.wait_for(app.ready.wait(), 5)
                await mine.start_peers(f"{d}/tester.sock")  # the TUI's own engine serves no socket here; give it one
                srv2 = HttpServer(Dispatcher(mine, mine.hub))
                await srv2.start_unix(f"{d}/tester.sock")
                await send(app, pilot, "/peers")
                await until(lambda: "razi" in app.transcript_text(), pilot)
                await send(app, pilot, "/send razi are you free?")
                await until(lambda: any("are you free?" in m.text() for m in other.store.messages(sb)), pilot)
                await other.peer_node.send("tester", "yes, go ahead", sb, "agent")
                await until(lambda: "peer message from razi" in app.transcript_text(), pilot)
                assert "yes, go ahead" in app.transcript_text()
                assert "/peers" in app.help_text() and "/send" in app.help_text()
                await srv2.close()
            await other.close()
            await srv.close()

        asyncio.run(main())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_new_is_a_thread_of_the_same_memory(tmp_path):
    from exo.core.context import NOTE_HEAD

    async def sc(app, pilot, client, eng):
        first = app.session
        await send(app, pilot, "hello from the first window")
        await until(lambda: not app.busy and app.last_answer[1], pilot)
        await send(app, pilot, "/new")
        await until(lambda: app.session != first, pilot)
        assert "same memory" in app.transcript_text()
        second = app.session
        await send(app, pilot, "hello again")
        await until(lambda: not app.busy and "Hello" in app.last_answer[1], pilot)
        stored = eng.store.messages(second)
        assert any(m.meta.get("note") and "first window" in m.text() for m in stored)  # it knows the other thread
        await app.load_session(second)
        assert NOTE_HEAD not in app.transcript_text() and "resumed thread" in app.transcript_text()
        await send(app, pilot, "/new private")
        await until(lambda: app.session not in (first, second), pilot)
        assert eng.store.get_session(app.session).kind == "private"

    run(tmp_path, sc)
