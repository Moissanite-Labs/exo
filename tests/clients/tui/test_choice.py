"""D-066: the choice selector. A choice panel or a question with options is a card in the chat column that takes
focus at once (unless you are writing), picks with arrows and Enter, number keys or a click, sends the pick exactly
once (to the agent as your reply), and closes. Esc dismisses it and never stops a turn."""

from __future__ import annotations

import io

from rich.console import Console

from exo.protocol import schema as s
from exo_tui.widgets import ApprovalCard, ChoiceCard, ChoiceRow
from test_tui import pending_approval, run, until

OPTIONS = {"prompt": "Which target?", "options": [
    {"id": "stg", "label": "Staging", "description": "preview build"}, {"id": "prod", "label": "Production"},
    "Skip"]}


def plain(renderable) -> str:
    con = Console(width=100, file=io.StringIO(), record=True)
    con.print(renderable)
    return con.export_text()


def open_choice(app, eng, pid="c1", data=None, title="Deploy"):
    eng.hub.publish(s.UiPanel(panel_id=pid, action="open", kind="choice", title=title, text="pick a target",
                              data=data or OPTIONS), app.session)


def acted(client):
    return [b for b in client.seen if isinstance(b, s.UiRespond) and b.action == "acted"]


def turns(app, text):
    return [b for b in app.sent if isinstance(b, s.TurnSend) and b.text == text]


async def card_up(app, pilot):
    await until(lambda: isinstance(app.choice, ChoiceCard) and app.choice.is_mounted, pilot)
    await pilot.pause(0.05)
    return app.choice


def test_choice_gets_focus_and_draws_in_the_chat_column(tmp_path):
    async def sc(app, pilot, client, eng):
        open_choice(app, eng)
        card = await card_up(app, pilot)
        await until(lambda: app.focused is card, pilot)
        assert card.parent is app.query_one("#left")  # the chat column, not the side panel
        assert not app.query_one("#panel").display
        head = plain(card.head())
        assert "CHOICE" in head and "DEPLOY" in head
        rows = [plain(card.row(i)) for i in range(3)]
        assert "1" in rows[0] and "Staging" in rows[0] and "preview build" in rows[0] and "▌" in rows[0]
        assert "▌" not in rows[1]
        assert "enter" in plain(card.foot())
        assert app.sphere_state() == "approval"  # the figure shows we are waiting on you

    run(tmp_path, sc)


def test_arrows_and_enter_pick_and_send_exactly_once(tmp_path):
    async def sc(app, pilot, client, eng):
        open_choice(app, eng)
        card = await card_up(app, pilot)
        await until(lambda: app.focused is card, pilot)
        await pilot.press("down", "down", "up")
        await pilot.press("enter")
        await until(lambda: acted(client) and turns(app, "Production"), pilot)
        await pilot.press("enter")  # a second Enter lands in the composer, not on a closed card
        await pilot.pause(0.3)
        assert [b.value for b in acted(client)] == [{"choice": "prod"}]
        assert len(turns(app, "Production")) == 1  # the agent hears the pick as your reply, once
        assert app.choice is None and not app.query("#choice")
        assert "c1" not in app.panels
        assert app.focused is app.composer

    run(tmp_path, sc)


def test_number_keys_pick_directly(tmp_path):
    async def sc(app, pilot, client, eng):
        open_choice(app, eng)
        card = await card_up(app, pilot)
        await until(lambda: app.focused is card, pilot)
        await pilot.press("7")  # out of range: nothing
        await pilot.pause(0.1)
        assert not acted(client)
        await pilot.press("3")
        await until(lambda: acted(client), pilot)
        assert acted(client)[0].value == {"choice": "Skip"}
        await until(lambda: turns(app, "Skip"), pilot)

    run(tmp_path, sc)


def test_click_picks_an_option(tmp_path):
    async def sc(app, pilot, client, eng):
        app.composer.text = "half a thought"  # even mid-draft, a click is deliberate
        open_choice(app, eng)
        card = await card_up(app, pilot)
        row = [r for r in card.query(ChoiceRow) if r.index == 1][0]
        await pilot.click(row)
        await until(lambda: acted(client), pilot)
        assert acted(client)[0].value == {"choice": "prod"}
        assert app.choice is None
        assert app.composer.text == "half a thought"

    run(tmp_path, sc)


def test_esc_dismisses_without_choosing_or_stopping_a_turn(tmp_path):
    async def sc(app, pilot, client, eng):
        app.busy = True
        open_choice(app, eng)
        card = await card_up(app, pilot)
        await until(lambda: app.focused is card, pilot)
        await pilot.press("escape")
        await until(lambda: any(b.action == "dismissed" for b in client.seen), pilot)
        await pilot.pause(0.1)
        assert not acted(client)
        assert not [b for b in app.sent if isinstance(b, (s.TurnInterrupt, s.TurnSend))]
        assert app.choice is None and not app.choices
        assert app.focused is app.composer

    run(tmp_path, sc)


def test_a_draft_is_not_hijacked_and_ctrl_f_reaches_the_choice(tmp_path):
    async def sc(app, pilot, client, eng):
        app.composer.focus()
        await pilot.press("h", "i")  # typing: the card must not take the next keys
        open_choice(app, eng)
        card = await card_up(app, pilot)
        await pilot.press(" ", "1", "enter")  # "1" and Enter go to the draft and send it, not to the card
        await until(lambda: turns(app, "hi 1"), pilot)
        assert not acted(client)
        assert app.choice is card and app.focused is not card
        assert "ctrl+f" in plain(card.foot())  # the card says how to reach it
        await pilot.press("ctrl+f")
        await until(lambda: app.focused is card, pilot)
        await pilot.press("2")
        await until(lambda: acted(client), pilot)
        assert acted(client)[0].value == {"choice": "prod"}

    run(tmp_path, sc)


def test_ctrl_f_moves_between_approval_and_choice(tmp_path):
    async def sc(app, pilot, client, eng):
        req = s.ApprovalRequested(request_id="r1", summary="run x", risk="medium", options=["allow", "deny"])
        pending_approval(eng, app.session, req)
        await until(lambda: app.card is not None, pilot)
        app.composer.text = "draft"
        open_choice(app, eng)
        card = await card_up(app, pilot)
        assert app.focused is not card
        await pilot.press("ctrl+f")
        await until(lambda: isinstance(app.focused, ApprovalCard), pilot)
        await pilot.press("ctrl+f")
        await until(lambda: app.focused is card, pilot)
        assert app.composer.text == "draft"

    run(tmp_path, sc)


def test_letters_on_the_card_type_into_the_composer(tmp_path):
    async def sc(app, pilot, client, eng):
        open_choice(app, eng)
        card = await card_up(app, pilot)
        await until(lambda: app.focused is card, pilot)
        await pilot.press("o", "k")
        await pilot.pause(0.1)
        assert app.composer.text == "ok" and app.focused is app.composer
        assert not acted(client) and app.choice is card

    run(tmp_path, sc)


def test_question_with_options_uses_the_selector_and_typing_still_answers(tmp_path):
    async def sc(app, pilot, client, eng):
        eng.hub.publish(s.QuestionAsked(question_id="q1", text="Tabs or spaces?", options=["tabs", "spaces"]),
                        app.session)
        card = await card_up(app, pilot)
        assert card.key == ("question", "q1")
        assert "Tabs or spaces?" in plain(card.head())
        await until(lambda: app.focused is card, pilot)
        await pilot.press("down", "enter")
        await until(lambda: any(isinstance(b, s.QuestionAnswer) for b in app.sent), pilot)
        await pilot.pause(0.1)
        answers = [b for b in app.sent if isinstance(b, s.QuestionAnswer)]
        assert [(a.question_id, a.answer) for a in answers] == [("q1", "spaces")]
        assert app.question is None and app.choice is None

        eng.hub.publish(s.QuestionAsked(question_id="q2", text="Name?", options=["a", "b"]), app.session)
        await card_up(app, pilot)
        app.composer.text = "something else"
        app.composer.focus()
        await pilot.press("enter")
        await until(lambda: len([b for b in app.sent if isinstance(b, s.QuestionAnswer)]) == 2, pilot)
        last = [b for b in app.sent if isinstance(b, s.QuestionAnswer)][-1]
        assert (last.question_id, last.answer) == ("q2", "something else")
        assert app.choice is None

    run(tmp_path, sc)


def test_close_and_update_from_the_agent(tmp_path):
    async def sc(app, pilot, client, eng):
        open_choice(app, eng)
        card = await card_up(app, pilot)
        eng.hub.publish(s.UiPanel(panel_id="c1", action="update", kind="choice", text="pick",
                                  data={"options": ["one", "two"]}), app.session)
        await until(lambda: len(card.items) == 2, pilot)
        assert app.choice is card
        eng.hub.publish(s.UiPanel(panel_id="c1", action="close"), app.session)
        await until(lambda: app.choice is None, pilot)
        assert any(b.action == "ignored" for b in client.seen)

    run(tmp_path, sc)


def test_palette_arrows_enter_and_click(tmp_path):
    from exo_tui.widgets import Palette

    async def sc(app, pilot, client, eng):
        picked = []
        app.push_screen(Palette([("alpha", 1), ("beta", 2), ("gamma", 3)]), picked.append)
        await pilot.pause(0.1)
        await pilot.press("down", "enter")
        await until(lambda: picked == [2], pilot)
        app.push_screen(Palette([("alpha", 1), ("beta", 2), ("gamma", 3)]), picked.append)
        await pilot.pause(0.1)
        lst = app.screen.query_one("#palette-list")
        await pilot.click(lst, offset=(2, 2))
        await until(lambda: len(picked) == 2, pilot)
        assert picked[1] == 3

    run(tmp_path, sc)


def test_no_color_and_reduced_motion_keep_the_card_readable(monkeypatch, tmp_path):
    """NO_COLOR: the highlight is still a marker and bold text, never colour alone. Reduced motion: no fade."""
    monkeypatch.setenv("NO_COLOR", "1")
    card = ChoiceCard(("panel", "p"), "t", "pick", [("a", "Alpha", "first"), ("b", "Beta", "")])
    row = card.row(0)
    assert row.plain.startswith("▌ 1  Alpha")
    assert not any(sp.style and "#" in str(sp.style) for sp in row.spans)
    assert card.row(1).plain.startswith("  2  Beta")
    monkeypatch.setenv("EXO_REDUCED_MOTION", "1")

    async def sc(app, pilot, client, eng):
        open_choice(app, eng)
        card = await card_up(app, pilot)
        assert card.styles.opacity == 1.0

    run(tmp_path, sc)
