"""D-037 in the terminal client: the dropdown, argument lists, @ files, ghost text, history, typo help, guidance."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from exo import complete as cm
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.widgets import IDLE_HINT
from test_tui import run, until


def test_completion_honors_explicit_local_engine_required_mode(tmp_path, monkeypatch):
    monkeypatch.delenv('EXO_TOOL_EXECUTOR_REQUIRED', raising=False)
    client = SimpleNamespace(engine=SimpleNamespace(executor_required=True))
    app = ExoApp(client, home=tmp_path)
    (tmp_path / 'safe.txt').write_text('safe')
    app.files.cwd = tmp_path
    monkeypatch.setattr(cm.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('host Git')))
    assert app.files.executor_required
    assert [i.value for i in app.files.query('')] == ['@safe.txt']


async def send(app, pilot, text):
    """Like a paste then Enter: the caret ends up after the text."""
    app.composer.text = text
    app.composer.move_cursor(app.composer.document.end)
    await pilot.press("enter")


def menu_text(app) -> str:
    return str(app.menu.render()) if app.menu.display else ""


async def type_(app, pilot, text):
    app.composer.focus()
    for ch in text:
        await pilot.press("space" if ch == " " else ch)
    await pilot.pause(0.05)


def test_slash_opens_the_menu_and_filters(tmp_path):
    async def sc(app, pilot, client, eng):
        assert not app.menu.display
        await type_(app, pilot, "/")
        assert app.menu.display and app.sug.mode == "command"
        shown = [i.value for i in app.sug.items]
        assert all(n in shown for n in ("new", "model", "compact", "context", "help"))  # client plus core commands
        await type_(app, pilot, "cmpa")
        assert [i.value for i in app.sug.items] == ["compact"]
        assert "/compact" in menu_text(app)
        await pilot.press("escape")
        assert not app.menu.display and app.composer.text == "/cmpa"  # esc closes, keeps the text

    run(tmp_path, sc)


def test_tab_completes_and_enter_completes_then_sends(tmp_path):
    async def sc(app, pilot, client, eng):
        await type_(app, pilot, "/hand")
        await pilot.press("tab")
        assert app.composer.text == "/handoff "
        app.composer.text = ""
        await type_(app, pilot, "/cl")
        await pilot.press("enter")  # completes first
        assert app.composer.text == "/clear "
        await pilot.press("enter")  # now complete: sends
        await until(lambda: app.composer.text == "", pilot)

    run(tmp_path, sc)


def test_arrows_move_the_selection(tmp_path):
    async def sc(app, pilot, client, eng):
        await type_(app, pilot, "/")
        first = app.sug.items[0].value
        await pilot.press("down")
        assert app.sel == 1
        await pilot.press("up", "up")
        assert app.sel == len(app.sug.items) - 1  # wraps
        await pilot.press("down")
        await pilot.press("tab")
        assert app.composer.text == f"/{first} "

    run(tmp_path, sc)


def test_model_arguments_come_from_the_core_list(tmp_path):
    async def sc(app, pilot, client, eng):
        await type_(app, pilot, "/model ")
        await until(lambda: "models" in app.arg_ctx, pilot)
        assert any(i.value == "fake:x" for i in app.sug.items)
        assert "default" in [i.value for i in app.sug.items]

    run(tmp_path, sc)


def test_resume_lists_threads_and_loads_one_by_title(tmp_path):
    async def sc(app, pilot, client, eng):
        other = (await client.call(s.SessionCreate(title="the quarterly report")))["session"]["id"]
        await type_(app, pilot, "/resume ")
        await until(lambda: "threads" in app.arg_ctx, pilot)
        assert any(i.label == "the quarterly report" for i in app.sug.items)
        await send(app, pilot, "/resume quarterly")  # Enter completes the thread first, a second Enter runs it
        assert app.composer.text == f"/resume {other}"
        await pilot.press("enter")
        await until(lambda: app.session == other, pilot)

    run(tmp_path, sc)


def test_grants_revoke_offers_ids_and_tutorial_words(tmp_path):
    async def sc(app, pilot, client, eng):
        app.arg_ctx["grants"] = [cm.Item("g_1", "g_1", "shell on git")]
        app.arg_at["grants"] = 1e12
        await type_(app, pilot, "/grants revoke ")
        assert [i.value for i in app.sug.items] == ["g_1"]
        app.composer.text = ""
        await type_(app, pilot, "/tutorial ")
        assert [i.value for i in app.sug.items] == ["on", "off", "reset"]

    run(tmp_path, sc)


def test_handoff_shows_the_default_path_and_a_ghost_hint(tmp_path):
    async def sc(app, pilot, client, eng):
        await type_(app, pilot, "/handoff ")
        await until(lambda: "handoff_path" in app.arg_ctx, pilot)
        assert app.sug.items and app.sug.items[0].meta == "default path"
        assert app.sug.hint == "[path]"

    run(tmp_path, sc)


def test_at_completes_files_from_the_cwd(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "notes.md").write_text("x")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x")

    async def sc(app, pilot, client, eng):
        app.files = cm.FileIndex(tmp_path)
        await type_(app, pilot, "look at @")
        values = [i.value for i in app.sug.items]
        assert values.index("@pkg/") < values.index("@notes.md")  # dirs first
        await type_(app, pilot, "mod")
        assert [i.value for i in app.sug.items] == ["@pkg/mod.py"]
        await pilot.press("tab")
        assert app.composer.text == "look at @pkg/mod.py "

    run(tmp_path, sc)


def test_bang_shows_a_short_hint_in_the_menu(tmp_path):
    async def sc(app, pilot, client, eng):
        await type_(app, pilot, "!ls")
        assert app.menu.display and "shell" in menu_text(app)
        assert not app.sug.items

    run(tmp_path, sc)


def test_history_up_arrow_and_ghost_text(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/usage")
        await send(app, pilot, "/cwd")
        await until(lambda: app.hist.entries[-2:] == ["/usage", "/cwd"], pilot)
        app.composer.focus()
        await pilot.press("up")
        assert app.composer.text == "/cwd"
        await pilot.press("up")
        assert app.composer.text == "/usage"
        await pilot.press("down", "down")
        assert app.composer.text == ""
        await type_(app, pilot, "/us")
        assert app.composer.ghost == "age"  # history (and the menu) both say so
        await pilot.press("right")
        assert app.composer.text == "/usage"

    run(tmp_path, sc)


def test_history_is_per_seat_on_disk(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/usage")
        await until(lambda: app.hist.entries, pilot)
        assert app.hist.path.name == "tester.jsonl" and app.hist.path.is_file()

    run(tmp_path, sc)


def test_unknown_command_suggests_instead_of_erroring(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/comand")
        await until(lambda: "did you mean /" in app.transcript_text(), pilot)
        assert app.composer.text.startswith("/co")  # the corrected line waits in the composer
        assert not any(isinstance(b, s.CommandRun) and b.name == "comand" for b in app.sent)  # no round trip

    run(tmp_path, sc)


def test_unknown_without_a_close_command_match_is_sent_as_literal_message(tmp_path):
    async def sc(app, pilot, client, eng):
        text = "/qqqqqqq"
        await send(app, pilot, text)
        await until(lambda: any(isinstance(b, s.TurnSend) and b.text == text for b in app.sent), pilot)
        sent = next(b for b in app.sent if isinstance(b, s.TurnSend) and b.text == text)
        assert sent.session == app.session and sent.mode == "send"
        assert not any(isinstance(b, s.CommandRun) and b.name == "qqqqqqq" for b in app.sent)

    run(tmp_path, sc)


def test_guidance_first_run_line_tab_fills_and_tutorial_off_silences(tmp_path):
    async def sc(app, pilot, client, eng):
        assert app.tip is not None and app.tip.fill == "/intro"
        assert app.composer.placeholder.startswith("first time here?") and len(app.composer.placeholder) < 90
        app.composer.focus()
        await pilot.press("tab")
        assert app.composer.text == "/intro"
        app.composer.text = ""
        await send(app, pilot, "/tutorial off")
        await until(lambda: not app.guide.enabled, pilot)
        assert app.tip is None and app.composer.placeholder == IDLE_HINT
        app.gstate.findings = 3
        app.guide.last_at = None
        app.update_guidance()
        assert app.tip is None  # off means off

    run(tmp_path, sc)


def test_guidance_comes_from_events_and_is_one_line(tmp_path):
    async def sc(app, pilot, client, eng):
        app.guide.last_at = None
        app.tip, app.gstate.first_run = None, False
        eng.hub.publish(s.ModelCall(requested="a:x", route="", error="401 unauthorized"), app.session)
        eng.hub.publish(s.TurnCompleted(turn_id="t"), app.session)
        await until(lambda: app.tip is not None, pilot)
        assert "/providers" in app.tip.text and "\n" not in app.composer.placeholder
        # it is a placeholder: typing hides it, nothing else on screen moves
        await type_(app, pilot, "h")
        assert app.composer.text == "h"

    run(tmp_path, sc)


def test_no_timer_under_reduced_motion(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")

    async def sc(app, pilot, client, eng):
        assert not app.motion and app.guide.enabled

    run(tmp_path, sc)


def test_tutorial_hint_the_first_time_the_menu_opens(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/tutorial on")
        await until(lambda: app.tutorial, pilot)
        app.hint_budget = 1
        await type_(app, pilot, "/")
        assert "slash_menu" in app.onb.seen()

    run(tmp_path, sc)
