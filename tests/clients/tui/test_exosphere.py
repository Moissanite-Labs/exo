"""D-062: the exosphere. The braille figure, the corner widget, its states, and that it never gets in the way."""

from __future__ import annotations

import asyncio
import json

import pytest
from exo.core.types import ToolCall
from exo.protocol import schema as s
from exo_tui import exosphere as xs
from exo_tui import theme as th
from exo_tui.app import ExoApp
from exo_tui.exosphere import Exosphere
from exo_tui.widgets import Composer
from test_tui import FakeServerClient, make_engine, pending_approval, send, until


def go(tmp_path, scenario, size=(120, 40), layout=None, monkeypatch=None):
    if layout and monkeypatch:
        monkeypatch.setenv("EXO_LAYOUT", layout)
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        app = ExoApp(client, seat="tester", home=str(tmp_path / "ui"))
        async with app.run_test(size=size) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


async def first_message(app, pilot):
    await send(app, pilot, "hello")
    await until(lambda: not app.query("#hero") and not app.busy and app.sphere.on, pilot)
    await pilot.pause(0.05)


def text_widgets(app):
    """Everything in the chat column that draws text, except the figure and its own gap row block."""
    left = app.query_one("#left")
    return [w for w in left.walk_children() if w.display and w.region.area
            and w.id not in ("exosphere", "sphere-gap") and not isinstance(w, Exosphere)
            and w.parent is left]


def assert_clear(app):
    """No text-bearing widget's content area overlaps the figure's box, and the screen shows the figure there."""
    box = app.sphere.region
    assert box.area and app.sphere.on
    for w in text_widgets(app):
        assert not w.content_region.overlaps(box), (w, w.content_region, box)
    for y in range(box.y, box.bottom):
        for x in range(box.x, box.right):
            assert app.screen.get_widget_at(x, y)[0] is app.sphere


# ---------------------------------------------------------------- the figure
def test_frames_are_braille_loop_and_differ_by_state():
    f0 = xs.render(xs.ART_COLS, xs.ART_ROWS, "idle", 0)
    lines = f0.plain.split("\n")
    assert len(lines) == xs.ART_ROWS and all(len(ln) == xs.ART_COLS for ln in lines)
    assert all(ch in " \n" or 0x2800 <= ord(ch) <= 0x28FF for ch in f0.plain)
    assert xs.render(xs.ART_COLS, xs.ART_ROWS, "idle", 1).plain != f0.plain  # it breathes and turns
    assert xs.render(xs.ART_COLS, xs.ART_ROWS, "idle", xs.CYCLE).plain == f0.plain  # and loops
    assert any(th.ACCENT in str(sp.style) for sp in f0.spans)  # the seed is orange
    tool = xs.render(xs.ART_COLS, xs.ART_ROWS, "tool", 3)
    assert any(th.ACCENT_2 in str(sp.style) for sp in tool.spans)  # packets
    styles = {st: {str(sp.style) for sp in xs.render(xs.ART_COLS, xs.ART_ROWS, st, 2).spans} for st in xs.STATES}
    assert len({frozenset(v) for v in styles.values()}) == len(xs.STATES)  # every state looks different
    assert xs.frames_in("approval") == 1  # the steady glow is one frame


def test_label_and_forms():
    assert xs.FORMS[:3] == ("x0", "x", "3xo") and xs.FORM == "x0"
    assert xs.label() == "x0"  # no level source exists yet
    assert xs.label("x0", 3) == f"x0 {th.DOT} lv 3"


def test_no_color_frames_use_bold_and_dim_only(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    f = xs.render(xs.ART_COLS, xs.ART_ROWS, "thinking", 5)
    assert {str(sp.style) for sp in f.spans} <= {"bold", "dim", ""}


# ---------------------------------------------------------------- the corner
def test_it_takes_the_corner_after_the_start_and_shows_its_label(tmp_path):
    async def sc(app, pilot, client, eng):
        assert app.query("#hero") and not app.sphere.on  # the start motif is the same figure, large
        await first_message(app, pilot)
        left, box = app.query_one("#left").region, app.sphere.region
        assert (box.right, box.bottom) == (left.right, left.bottom)  # the bottom-right corner of the chat column
        assert (box.width, box.height) == (xs.WIDTH, xs.HEIGHT)
        assert "x0" in str(app.sphere.render())
        assert_clear(app)

    go(tmp_path, sc)


@pytest.mark.parametrize("layout", ["classic", "inverse"])
def test_text_is_never_drawn_under_it(tmp_path, monkeypatch, layout):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        long = "a long answer that wraps " * 60
        eng.hub.publish(s.TurnStarted(turn_id="t9", model="fake:x"), app.session)
        eng.hub.publish(s.MessageDelta(message_id="m9", text=long), app.session)
        app.composer.text = "a long draft " * 30  # the composer wraps beside the box in classic
        await pilot.pause(0.3)
        assert_clear(app)
        pending_approval(eng, app.session, s.ApprovalRequested(
            request_id="r1", summary="run it?", call=ToolCall("c1", "bash", {"command": "echo asked"})))
        await until(lambda: app.card is not None, pilot)
        await pilot.pause(0.2)
        assert_clear(app)
        assert app.transcript.region.width >= app.query_one("#left").region.width - 4  # the box costs no columns above it
        if layout == "classic":  # the composer's text stops short of the box
            assert app.query_one("#compose").content_region.right <= app.sphere.region.x

    go(tmp_path, sc, layout=layout, monkeypatch=monkeypatch)


def test_completion_menu_works_beside_it(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        app.composer.focus()
        for k in ("slash", "e", "x", "o"):
            await pilot.press(k)
        await until(lambda: app.menu.display, pilot)
        await pilot.pause(0.1)
        assert "exosphere" in str(app.menu.render())
        assert_clear(app)
        await pilot.press("tab")
        assert app.composer.text.startswith("/exosphere")

    go(tmp_path, sc)


def test_toggle_is_saved_per_user(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        await send(app, pilot, "/exosphere off")
        await until(lambda: not app.sphere.on, pilot)
        assert not app.query_one("#left").has_class("sphere")
        assert json.loads((tmp_path / "ui" / "state" / "ui.json").read_text())["exosphere"] is False
        await send(app, pilot, "/exosphere")
        await until(lambda: "exosphere: off" in app.transcript_text(), pilot)
        await send(app, pilot, "/exosphere sideways")
        await until(lambda: "usage: /exosphere on|off" in app.transcript_text(), pilot)

    go(tmp_path, sc)

    async def again(app, pilot, client, eng):  # a new session of the same user starts with it off
        assert app.sphere_on is False
        await send(app, pilot, "/exosphere on")
        await until(lambda: app.sphere_on, pilot)
        await first_message(app, pilot)  # the start motif gives way to it

    go(tmp_path, again)


def test_hides_on_a_narrow_terminal_and_comes_back(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "hello")
        await until(lambda: not app.query("#hero") and not app.busy, pilot)
        await pilot.pause(0.1)
        assert not app.sphere.on and not app.query_one("#left").has_class("sphere")
        await pilot.resize_terminal(120, 40)
        await until(lambda: app.sphere.on, pilot)
        await pilot.pause(0.1)
        assert_clear(app)
        await pilot.resize_terminal(89, 40)
        await until(lambda: not app.sphere.on, pilot)

    go(tmp_path, sc, size=(84, 36))


def test_never_takes_focus_or_keys(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        assert not app.sphere.can_focus and not app.sphere.focusable
        app.transcript.focus()
        box = app.sphere.region
        await pilot.click(offset=(box.x + 3, box.y + 2))
        await pilot.pause(0.1)
        assert app.focused is app.composer  # a click on it is a click on a plain surface
        for _ in range(6):
            await pilot.press("tab")
            assert app.focused is not app.sphere
        app.composer.focus()
        await pilot.press("h", "i")
        assert app.composer.text.endswith("hi")

    go(tmp_path, sc)


def test_states_follow_the_events(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        await until(lambda: app.sphere.state == "idle", pilot, timeout=3)
        pub = lambda b: eng.hub.publish(b, app.session)  # noqa: E731
        pub(s.TurnStarted(turn_id="t9", model="fake:x"))
        await until(lambda: app.sphere.state == "thinking", pilot)
        pub(s.ToolStarted(call=ToolCall("c1", "bash", {"command": "ls"})))
        await until(lambda: app.sphere.state == "tool", pilot)
        pending_approval(eng, app.session, s.ApprovalRequested(
            request_id="r1", summary="run it?", call=ToolCall("c2", "bash", {"command": "echo asked"})))
        await until(lambda: app.sphere.state == "approval", pilot)
        assert "!" in str(app.sphere.render())  # the attention mark
        assert app.sphere.timer is not None and not app.sphere.timer._active.is_set()  # a steady glow: no timer
        await pilot.press("ctrl+f")  # D-045: ctrl+f still reaches the card
        assert app.focused is app.card
        app.card.post_message(app.card.Answered("r1", "once"))
        await until(lambda: app.sphere.state == "tool", pilot)
        pub(s.TurnCompleted(turn_id="t9"))
        await until(lambda: app.sphere.state == "done", pilot)
        await until(lambda: app.sphere.state == "idle", pilot, timeout=4)
        pub(s.TurnStarted(turn_id="t10", model="fake:x"))
        pub(s.Error(code="turn_failed", message="boom"))
        await pilot.pause(0.1)
        assert app.sphere.state == "thinking"  # the turn's own state is not cut short
        pub(s.TurnCompleted(turn_id="t10", stop_reason="error"))
        await until(lambda: app.sphere.state == "error", pilot)

    go(tmp_path, sc)


def test_esc_never_stops_a_turn_with_it_on_screen(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        eng.hub.publish(s.TurnStarted(turn_id="t9", model="fake:x"), app.session)
        await until(lambda: app.busy, pilot)
        await pilot.press("escape")
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.busy and not any(isinstance(b, s.TurnInterrupt) for b in app.sent)

    go(tmp_path, sc)


def test_reduced_motion_is_still(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")

    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        assert not app.sphere.animated
        eng.hub.publish(s.TurnStarted(turn_id="t9", model="fake:x"), app.session)
        await until(lambda: app.sphere.state == "thinking", pilot)
        before = str(app.sphere.render())
        await pilot.pause(0.6)
        assert str(app.sphere.render()) == before and not app.sphere.timer._active.is_set()

    go(tmp_path, sc)


def test_the_big_editor_hides_it_and_layouts_keep_it(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        await pilot.press("ctrl+g")
        await until(lambda: app.composing, pilot)
        assert not app.sphere.on
        await pilot.press("escape")
        await until(lambda: app.sphere.on, pilot)
        await send(app, pilot, "/layout inverse")
        await until(lambda: app.inverse, pilot)
        await pilot.pause(0.2)
        assert app.sphere.on and not app.query_one("#left").has_class("sphere-flow")
        assert_clear(app)
        await send(app, pilot, "/layout classic")
        await until(lambda: not app.inverse, pilot)
        await pilot.pause(0.2)
        assert app.query_one("#left").has_class("sphere-flow")
        assert_clear(app)

    go(tmp_path, sc)


def test_composer_still_gets_typing_from_the_transcript(tmp_path):
    async def sc(app, pilot, client, eng):
        await first_message(app, pilot)
        app.transcript.focus()
        await pilot.press("o", "k")
        assert app.focused is app.query_one(Composer) and app.composer.text.endswith("ok")

    go(tmp_path, sc)


# ---------------------------------------------------------------- the brand files (mark and wordmark)
def test_mark_and_wordmark_come_from_the_brand_files():
    from pathlib import Path

    from exo_tui import brand

    site = Path(__file__).resolve().parents[3] / "site" / "brand"
    for name in ("tui-mark.txt", "tui-mark.html", "tui-wordmark.txt", "tui-wordmark.html"):
        assert (brand.PACKAGED / name).read_text() == (site / name).read_text(), f"copy site/brand/{name} to exo_tui/brand/"
    assert brand.wordmark().plain == (site / "tui-wordmark.txt").read_text().strip()
    assert brand.mark().plain.split("\n") == (site / "tui-mark.txt").read_text().rstrip("\n").split("\n")
    assert any(th.ACCENT in str(sp.style) for sp in brand.mark().spans)  # the V in orange
