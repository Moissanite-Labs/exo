"""D-045: the approval card is reachable while a draft is typed, never outlives its request, and a queued steer
can be pulled back."""

from __future__ import annotations

import time

from exo.protocol import schema as s
from test_tui import pending_approval, run, send, until


async def ask_over_a_draft(app, pilot, eng, draft="a draft"):
    """A card that arrives while the user has a draft in the composer (a steer being typed)."""
    app.composer.focus()
    app.composer.text = draft
    app.composer.last_typed = time.monotonic()
    pending_approval(eng, app.session, s.ApprovalRequested(request_id="r1", risk="medium", summary="run echo?",
                                                          options=["allow", "allow_always", "deny"]))
    await until(lambda: app.card is not None, pilot)
    assert not app.card.has_focus  # a draft is never interrupted


async def steer(app, pilot, text):
    app.composer.focus()  # the card may hold focus; the user clicks back into the composer to type
    await send(app, pilot, text)


def responds(app):
    return [b for b in app.sent if isinstance(b, s.ApprovalRespond)]


def test_card_is_reachable_by_tab_with_a_draft_in_the_composer(tmp_path):
    async def sc(app, pilot, client, eng):
        await ask_over_a_draft(app, pilot, eng)
        for _ in range(6):
            await pilot.press("tab")
            if app.card.has_focus:
                break
        assert app.card.has_focus, "tab must reach the approval card"
        await pilot.press("o")
        await until(lambda: responds(app), pilot)
        assert responds(app)[0].decision == "allow"
        assert app.composer.text == "a draft"
        await until(lambda: app.card is None, pilot)

    run(tmp_path, sc)


def test_card_has_a_key_that_works_from_the_composer(tmp_path):
    async def sc(app, pilot, client, eng):
        await ask_over_a_draft(app, pilot, eng)
        await pilot.press("ctrl+f")  # focus the card from anywhere
        await pilot.pause(0.05)
        assert app.card.has_focus
        await pilot.press("d")
        await pilot.press("enter")
        await until(lambda: responds(app), pilot)
        assert responds(app)[0].decision == "deny"

    run(tmp_path, sc)


def test_card_closes_when_the_core_no_longer_has_the_approval(tmp_path):
    """A missed approval.resolved (core restarted, event dropped) must not leave a card on screen."""
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        rid = app.card.req.request_id
        fut = eng.approvals.open[rid].future
        fut.set_result("allow")  # resolved behind the clients' backs: no ApprovalResolved goes out
        app.reconcile_every = 0.1
        await until(lambda: app.card is None, pilot)
        assert not app.approvals

    run(tmp_path, sc)


def test_stopping_a_turn_closes_its_approval_card(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        app.composer.focus()
        await pilot.press("ctrl+c")
        await until(lambda: app.card is None, pilot)
        assert not app.approvals

    run(tmp_path, sc)


def test_queued_steer_shows_pending_and_unsteer_pulls_it_back(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")  # the gated call keeps the turn running
        await until(lambda: app.card is not None, pilot)
        await steer(app, pilot, "change course")
        await until(lambda: app.pending_steers, pilot)
        assert "change course" in app.transcript_text()
        await steer(app, pilot, "/unsteer")
        await until(lambda: not app.pending_steers, pilot)
        assert app.composer.text == "change course"  # back in the composer, to edit
        assert "change course" not in app.transcript_text()  # the queued line is gone from the transcript too
        assert not eng.loop.state(app.session).steer

    run(tmp_path, sc)


def test_up_arrow_in_an_empty_composer_pulls_the_pending_steer_back(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        await steer(app, pilot, "first thought")
        await until(lambda: app.pending_steers, pilot)
        app.composer.focus()
        assert app.composer.text == ""
        await pilot.press("up")
        await until(lambda: not app.pending_steers, pilot)
        assert app.composer.text == "first thought"
        assert not eng.loop.state(app.session).steer

    run(tmp_path, sc)


def test_delivered_steer_is_no_longer_pending(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        await steer(app, pilot, "late word")
        await until(lambda: app.pending_steers, pilot)
        await pilot.press("ctrl+f")
        await pilot.press("o")  # let the tool run; the steer lands at the next tool boundary
        await until(lambda: not app.pending_steers and not app.busy, pilot)
        await steer(app, pilot, "/unsteer")
        await pilot.pause(0.1)
        assert app.composer.text == ""
        assert "nothing queued" in app.transcript_text()

    run(tmp_path, sc)
