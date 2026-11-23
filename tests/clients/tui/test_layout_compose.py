"""D-046: focus follows the user, the big composer pane, and the inverse layout (input on top, newest first)."""

from __future__ import annotations

import json
import os
import stat

from textual import events

from exo_tui.app import SCAFFOLD
from exo_tui.prefs import DEFAULT_LAYOUT, Prefs
from exo_tui.widgets import Block
from test_tui import long_history, run, send, until


def kinds(app, *wanted):
    return [(w.kind, w.text) for w in app.chrono() if isinstance(w, Block) and w.kind in wanted]


# ---------------------------------------------------------------- focus follows the user
def test_clicking_the_transcript_returns_focus_to_the_composer(tmp_path):
    async def sc(app, pilot, client, eng):
        app.transcript.focus()
        await pilot.pause(0.1)
        assert app.focused is app.transcript
        await pilot.click("#transcript")
        await pilot.pause(0.1)
        assert app.focused is app.composer

    run(tmp_path, sc)


def test_typing_with_focus_on_the_transcript_goes_into_the_composer(tmp_path):
    async def sc(app, pilot, client, eng):
        app.transcript.focus()
        await pilot.pause(0.1)
        assert app.focused is app.transcript
        await pilot.press("h", "i", "slash")
        assert app.focused is app.composer
        assert app.composer.text == "hi/"
        app.screen.set_focus(None)  # focus nowhere at all
        await pilot.pause(0.1)
        await pilot.press("x")
        assert app.composer.text == "hi/x"

    run(tmp_path, sc)


def test_a_panel_that_holds_focus_keeps_its_keys(tmp_path):
    from exo.protocol import schema as s

    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.UiPanel(panel_id="p1", action="open", kind="choice", title="pick", text="",
                                  data={"options": [{"id": "a", "label": "alpha"}, {"id": "b", "label": "beta"}]}), app.session)
        await until(lambda: app.focused is not None and app.focused.id == "panel-choices", pilot)
        await pilot.press("z")
        assert app.composer.text == ""

    run(tmp_path, sc)


# ---------------------------------------------------------------- the big composer pane
def test_ctrl_g_opens_the_pane_enter_adds_lines_and_ctrl_s_sends(tmp_path):
    async def sc(app, pilot, client, eng):
        small = app.composer.size.height
        await pilot.press("ctrl+g")
        await pilot.pause(0.1)
        assert app.composing and app.query_one("#left").has_class("composing")
        assert app.composer.size.height > 20 > small
        assert app.cheader.display and not app.transcript.display
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("enter")
        for ch in "world":
            await pilot.press(ch)
        assert app.composer.text == "hello\nworld"  # Enter is a newline here, nothing was sent
        assert not [b for b in app.sent if type(b).__name__ == "TurnSend"]
        await pilot.press("ctrl+s")
        await until(lambda: [b for b in app.sent if type(b).__name__ == "TurnSend"], pilot)
        assert [b for b in app.sent if type(b).__name__ == "TurnSend"][0].text == "hello\nworld"
        assert not app.composing and app.composer.text == ""
        assert app.transcript.display

    run(tmp_path, sc)


def test_ctrl_enter_sends_too(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+g")
        app.composer.text = "long one"
        await pilot.press("ctrl+enter")
        await until(lambda: [b for b in app.sent if type(b).__name__ == "TurnSend"], pilot)

    run(tmp_path, sc)


def test_esc_closes_the_pane_and_the_draft_survives(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+g")
        app.composer.text = "# Plan\n\nstep one"
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not app.composing and app.composer.text == "# Plan\n\nstep one"
        assert not [b for b in app.sent if type(b).__name__ == "TurnInterrupt"]  # D-043: Esc never stops a turn
        await pilot.press("ctrl+g")
        assert app.composer.text == "# Plan\n\nstep one"
        await pilot.press("ctrl+g")  # the same key closes it again
        assert not app.composing

    run(tmp_path, sc)


def test_esc_during_a_turn_in_the_pane_only_closes_the_pane(tmp_path):
    async def sc(app, pilot, client, eng):
        app.busy = True
        await pilot.press("ctrl+g")
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not app.composing
        assert not [b for b in app.sent if type(b).__name__ == "TurnInterrupt"]

    run(tmp_path, sc)


def test_compose_task_inserts_the_scaffold_and_keeps_an_existing_draft(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/compose task")
        await until(lambda: app.composing, pilot)
        assert app.composer.text == SCAFFOLD
        assert app.composer.cursor_location == (1, 0)  # on the blank line under Goal
        await pilot.press("x")
        assert app.composer.text.startswith("## Goal\nx")
        app.close_compose()
        app.composer.text = "my notes"
        app.compose_command("task")
        assert app.composer.text == "my notes\n\n" + SCAFFOLD
        assert app.composer.cursor_location[0] == 3

    run(tmp_path, sc)


def test_header_counts_words_and_lines_and_lists_the_keys(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+g")
        app.composer.text = "one two three\nfour"
        await pilot.pause(0.1)
        from rich.console import Console
        con = Console(width=100, record=True, file=open(os.devnull, "w"))
        con.print(app.cheader.shown)
        out = con.export_text()
        assert "4 words" in out and "2 lines" in out
        assert "ctrl+s" in out and "esc" in out and "ctrl+l" in out

    run(tmp_path, sc)


def test_a_long_paste_stays_intact(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+g")
        block = "\n".join(f"line {i}: " + "word " * 12 for i in range(300))
        app.post_message(events.Paste(block))
        await pilot.pause(0.3)
        assert app.composer.text == block
        assert not [b for b in app.sent if type(b).__name__ == "TurnSend"]

    run(tmp_path, sc)


def test_slash_and_file_completion_still_work_in_the_pane(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+g")
        await pilot.press("slash", "c", "o", "m", "p", "o")
        await pilot.pause(0.1)
        assert app.menu.display and app.sug.mode == "command"
        assert "compose" in [i.value for i in app.sug.items]
        await pilot.press("tab")
        assert app.composer.text.startswith("/compose")
        assert app.composing  # accepting a row does not send

    run(tmp_path, sc)


def test_up_arrow_in_the_pane_does_not_replace_the_draft(tmp_path):
    async def sc(app, pilot, client, eng):
        app.hist.add("an old line")
        await pilot.press("ctrl+g")
        app.composer.text = "draft"
        app.composer.move_cursor((0, 0))
        await pilot.press("up")
        assert app.composer.text == "draft"

    run(tmp_path, sc)


def test_markdown_is_lightly_coloured_in_the_pane_only(tmp_path):
    async def sc(app, pilot, client, eng):
        app.composer.text = "# Title\n- item with `code` and **bold**\n```\nx = 1\n```"
        plain = app.composer.get_line(0)
        assert not plain.spans
        await pilot.press("ctrl+g")
        head = app.composer.get_line(0)
        assert head.spans and "bold" in str(head.spans[0].style)
        item = app.composer.get_line(1)
        assert len(item.spans) >= 3  # the list mark, the code and the bold
        assert app.composer.get_line(3).spans  # inside a fence

    run(tmp_path, sc)


def test_line_numbers_toggle(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+g")
        assert not app.composer.show_line_numbers
        await pilot.press("ctrl+l")
        assert app.composer.show_line_numbers
        await pilot.press("ctrl+l")
        assert not app.composer.show_line_numbers

    run(tmp_path, sc)


def test_open_in_editor_takes_the_saved_text_back(tmp_path, monkeypatch):
    script = tmp_path / "ed.sh"
    script.write_text('#!/bin/sh\nprintf "\\nadded by the editor\\n" >> "$1"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("EDITOR", str(script))
    monkeypatch.delenv("VISUAL", raising=False)

    async def sc(app, pilot, client, eng):
        app.composer.text = "first"
        await pilot.press("ctrl+g")
        await pilot.press("ctrl+t")
        await pilot.pause(0.3)
        assert app.composer.text == "first\nadded by the editor"

    run(tmp_path, sc)


def test_open_in_editor_without_an_editor_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)

    async def sc(app, pilot, client, eng):
        app.composer.text = "keep me"
        app.compose_command("edit")
        assert "no editor" in app.transcript_text() and app.composer.text == "keep me"

    run(tmp_path, sc)


# ---------------------------------------------------------------- layout
def test_the_experiment_default_is_inverse():
    assert DEFAULT_LAYOUT == "inverse"


def test_inverse_puts_the_input_on_top_and_stacks_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_LAYOUT", "inverse")

    async def sc(app, pilot, client, eng):
        assert app.inverse
        assert app.query_one("#compose").region.y < app.transcript.region.y
        assert app.strip.region.y < app.query_one("#compose").region.y  # the status strip moved up with the input
        await send(app, pilot, "hello")
        await until(lambda: "friend" in app.transcript_text(), pilot)
        await pilot.pause(0.2)
        mine = kinds(app, "user", "assistant")
        assert [k for k, _ in mine] == ["user", "assistant"]  # chronological helper
        shown = [w for w in app.transcript.children if isinstance(w, Block) and w.kind in ("user", "assistant")]
        assert [w.kind for w in shown] == ["assistant", "user"]  # on screen the newest is first
        assert app.transcript.scroll_y == 0
        assert shown[0].region.y < shown[1].region.y

    run(tmp_path, sc)


def test_inverse_new_tool_lines_land_above_older_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_LAYOUT", "inverse")

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "hello")
        await until(lambda: app.transcript_text().count("Hello") >= 1, pilot)
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        card = app.card
        assert card.region.y < app.query_one("#activity").region.y < app.transcript.region.y

    run(tmp_path, sc)


def test_layout_command_flips_in_place_and_is_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_LAYOUT", "inverse")

    async def sc(app, pilot, client, eng):
        app.add(Block("first", "user"))
        app.add(Block("second", "assistant"))
        await pilot.pause(0.2)
        assert [w.text for w in app.transcript.children if w.kind in ("user", "assistant")] == ["second", "first"]
        monkeypatch.delenv("EXO_LAYOUT")  # so the saved choice is what the next app reads
        await send(app, pilot, "/layout classic")
        await until(lambda: not app.inverse, pilot)
        await pilot.pause(0.2)
        assert [w.text for w in app.transcript.children if w.kind in ("user", "assistant")] == ["first", "second"]
        assert app.query_one("#compose").region.y > app.transcript.region.y
        assert app.strip.region.y > app.query_one("#compose").region.y  # and the status strip went back down
        assert json.loads(Prefs(os.environ["EXO_HOME"]).path.read_text())["layout"] == "classic"
        assert Prefs(os.environ["EXO_HOME"]).layout() == "classic"
        await send(app, pilot, "/layout inverse")
        await until(lambda: app.inverse, pilot)
        await pilot.pause(0.2)
        assert app.strip.region.y < app.query_one("#compose").region.y
        assert Prefs(os.environ["EXO_HOME"]).layout() == "inverse"
        await send(app, pilot, "/layout sideways")
        await pilot.pause(0.1)
        assert app.inverse and "usage: /layout" in app.transcript_text()

    run(tmp_path, sc)


def test_saved_layout_wins_over_the_default_and_env_wins_over_saved(tmp_path, monkeypatch):
    monkeypatch.delenv("EXO_LAYOUT", raising=False)
    p = Prefs(os.environ["EXO_HOME"])
    assert p.layout() == DEFAULT_LAYOUT
    p.set_layout("classic")
    assert p.layout() == "classic"
    monkeypatch.setenv("EXO_LAYOUT", "inverse")
    assert p.layout() == "inverse"


def long_session(app, n):
    app.clear_transcript()
    app.render_history(long_history(n))


def at_bottom(tr):
    return tr.max_scroll_y > 0 and tr.scroll_y == tr.max_scroll_y


def tool_history(n):
    """n rounds of a question, a tool call with eight lines of output, and an answer."""
    msgs = []
    for i in range(n):
        out = "\n".join(f"line {k}" for k in range(8))
        msgs += [{"id": f"u{i}", "role": "user", "content": [{"type": "text", "text": f"question {i}"}]},
                 {"id": f"c{i}", "role": "assistant", "content": [],
                  "tool_calls": [{"id": f"call{i}", "name": "read_file", "arguments": {"path": f"f{i}"}}]},
                 {"id": f"r{i}", "role": "tool",
                  "tool_results": [{"call_id": f"call{i}", "content": [{"type": "text", "text": out}]}]},
                 {"id": f"a{i}", "role": "assistant", "content": [{"type": "text", "text": f"answer {i}"}]}]
    return msgs


def late_paints(monkeypatch, delay=0.3):
    """Blocks paint in on_mount. Hold each paint back, as a slow runner does, until the empty blocks have been laid
    out and scrolled to. The first scroll then lands at row 49 of 129, the numbers of the hosted failure in #1023."""
    paint = Block.on_mount

    def late(self):
        self.set_timer(delay, lambda: paint(self))

    monkeypatch.setattr(Block, "on_mount", late)


def test_classic_opens_at_the_latest_message_at_the_bottom(tmp_path):
    async def sc(app, pilot, client, eng):
        long_session(app, 40)
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        await pilot.pause(0.2)  # and it stays there once the layout settles
        assert at_bottom(tr)

    run(tmp_path, sc)


def test_classic_keeps_following_the_bottom_while_blocks_paint_late(tmp_path, monkeypatch):
    """#1023: the first scroll can land before every block has its height. The view follows the bottom down."""
    async def sc(app, pilot, client, eng):
        late_paints(monkeypatch)
        long_session(app, 40)
        tr = app.transcript
        blocks = [w for w in tr.children if isinstance(w, Block)]
        await until(lambda: all(b.shown is not None for b in blocks), pilot)
        await pilot.pause(0.2)
        assert at_bottom(tr)

    run(tmp_path, sc)


def test_classic_leaves_a_reader_who_scrolled_up_in_place(tmp_path, monkeypatch):
    async def sc(app, pilot, client, eng):
        app.clear_transcript()
        app.render_history(tool_history(10))
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        tr.scroll_to(y=3, animate=False)
        await until(lambda: tr.scroll_y == 3, pilot)
        await pilot.press("ctrl+o")  # every tool output opens: the content grows below the reader
        await until(lambda: all(tb.expanded for tb in app.tools.values()), pilot)
        await pilot.pause(0.2)
        assert tr.scroll_y == 3

    run(tmp_path, sc)


def test_classic_a_reader_one_line_up_is_not_pulled_down(tmp_path):
    async def sc(app, pilot, client, eng):
        app.clear_transcript()
        app.render_history(tool_history(10))
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        up = tr.max_scroll_y - 1
        tr.scroll_to(y=up, animate=False)
        await until(lambda: tr.scroll_y == up, pilot)
        await pilot.press("ctrl+o")
        await until(lambda: all(tb.expanded for tb in app.tools.values()), pilot)
        await pilot.pause(0.2)
        assert tr.scroll_y == up

    run(tmp_path, sc)


def test_classic_scrolling_back_to_the_bottom_follows_again(tmp_path):
    async def sc(app, pilot, client, eng):
        app.clear_transcript()
        app.render_history(tool_history(10))
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        tr.scroll_to(y=3, animate=False)  # the reader scrolls up ...
        await until(lambda: tr.scroll_y == 3, pilot)
        tr.scroll_end(animate=False)  # ... and back down to the end
        await until(lambda: at_bottom(tr), pilot)
        before = tr.max_scroll_y
        await pilot.press("ctrl+o")  # the content grows below: the view follows it again
        await until(lambda: tr.max_scroll_y > before, pilot)
        await pilot.pause(0.2)
        assert at_bottom(tr)

    run(tmp_path, sc)


def test_classic_a_wheel_notch_up_during_live_tool_output_stands(tmp_path):
    """A follow queued by one output line must not undo the reader's scroll that lands before it runs."""
    from exo.core.types import ToolCall
    from exo.protocol import schema as s

    async def sc(app, pilot, client, eng):
        app.clear_transcript()
        app.render_history(tool_history(10))
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        await pilot.press("ctrl+o")  # tool output open: each new output line grows the live block
        await pilot.pause(0.2)
        app.handle(s.Event.of(s.ToolStarted(call=ToolCall("live", "bash", {"command": "x"})), seq=1,
                              session=app.session))
        undone = 0
        for k in range(8):
            tr.scroll_end(animate=False)
            await until(lambda: at_bottom(tr), pilot)
            await pilot.pause(0.1)  # every scroll already asked for has landed
            app.handle(s.Event.of(s.ToolOutput(call_id="live", text=f"out {k}\n"), seq=2 + k, session=app.session))
            await pilot.pause(0)  # the new line is laid out, and a follow is queued
            tr.scroll_relative(y=-2, animate=False)  # one wheel notch up, right then
            up = tr.scroll_y
            await pilot.pause(0.15)
            undone += tr.scroll_y > up
        assert undone == 0  # the reader's notch stands, as it does on a still transcript

    run(tmp_path, sc)


def test_classic_a_thread_opened_from_the_top_of_another_follows_its_late_paints(tmp_path, monkeypatch):
    """Reading the top of one thread, then opening another: the new thread follows its bottom while it paints."""
    async def sc(app, pilot, client, eng):
        long_session(app, 40)
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        tr.scroll_home(animate=False)  # the reader is at the top: not following
        await until(lambda: tr.scroll_y == 0, pilot)
        late_paints(monkeypatch)
        long_session(app, 12)  # unpainted, its blocks fit the screen, so the first scroll moves nothing; painted, they scroll
        blocks = [w for w in tr.children if isinstance(w, Block)]
        await until(lambda: all(b.shown is not None for b in blocks), pilot)
        await pilot.pause(0.2)
        assert at_bottom(tr)

    run(tmp_path, sc)


def test_classic_at_the_bottom_stays_there_when_tool_output_opens(tmp_path):
    async def sc(app, pilot, client, eng):
        app.clear_transcript()
        app.render_history(tool_history(10))
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        before = tr.max_scroll_y
        await pilot.press("ctrl+o")
        await until(lambda: tr.max_scroll_y > before, pilot)
        await pilot.pause(0.2)
        assert at_bottom(tr)

    run(tmp_path, sc)


def test_classic_f6_at_the_bottom_keeps_the_reader_at_the_page_it_loaded(tmp_path):
    from exo_tui import app as appmod

    async def sc(app, pilot, client, eng):
        long_session(app, appmod.WINDOW + 10)
        tr = app.transcript
        await until(lambda: at_bottom(tr), pilot)
        await pilot.press("f6")
        await until(lambda: not app.older and app.earlier_row is None, pilot)
        await pilot.pause(0.3)
        assert tr.scroll_y < tr.max_scroll_y  # what was first on screen is still in view, not the bottom

    run(tmp_path, sc)


def test_inverse_opens_at_the_latest_message_at_the_top(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_LAYOUT", "inverse")

    async def sc(app, pilot, client, eng):
        long_session(app, 40)
        tr = app.transcript
        await until(lambda: tr.max_scroll_y > 0, pilot)
        await pilot.pause(0.2)
        assert tr.max_scroll_y > 0 and tr.scroll_y == 0
        first = [w for w in tr.children if isinstance(w, Block) and w.kind in ("user", "assistant")][0]
        assert first.text.startswith("answer 39")

    run(tmp_path, sc)


def test_inverse_loads_earlier_history_at_the_bottom(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_LAYOUT", "inverse")
    from exo_tui import app as appmod

    async def sc(app, pilot, client, eng):
        total = appmod.WINDOW + appmod.PAGE + 10
        long_session(app, total)
        await pilot.pause(0.3)
        assert app.earlier_row is not None and list(app.transcript.children)[-1] is app.earlier_row
        app.action_load_earlier()
        await until(lambda: len(app.older) == 10, pilot)
        await pilot.pause(0.2)
        assert list(app.transcript.children)[-1] is app.earlier_row
        await pilot.press("f6")
        await until(lambda: not app.older and app.earlier_row is None, pilot)
        await pilot.pause(0.2)
        texts = [w.text for w in app.chrono() if isinstance(w, Block) and w.kind in ("user", "assistant")]
        assert len(texts) == total and texts[0] == "question 0"

    run(tmp_path, sc)
