"""D-045: the approval card is reachable while a draft is typed, never outlives its request, and a queued steer
can be pulled back."""

from __future__ import annotations

import time

from exo.protocol import schema as s
from exo_tui.widgets import ApprovalCard
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


def test_after_an_answer_the_next_request_of_the_thread_gets_its_card(tmp_path):
    """The removal hands the focus to the card's neighbour (the transcript here) at once; that is not the user moving
    it, so the composer gets it back and the next prompt is sent and asked about (regression from #284)."""
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        await pilot.press("ctrl+f")
        await pilot.press("o")
        await until(lambda: app.card is None and "after ask" in app.transcript_text() and not app.busy, pilot)
        await until(lambda: app.composer.has_focus, pilot)
        await send(app, pilot, "ask again")
        await until(lambda: app.card is not None, pilot)
        await pilot.press("ctrl+f")
        await pilot.press("d")
        await pilot.press("enter")  # from the deny note, inside the card
        await until(lambda: len(responds(app)) == 2 and app.card is None, pilot)
        await until(lambda: app.composer.has_focus, pilot)

    run(tmp_path, sc)


def test_answering_a_card_mounts_the_one_queued_behind_it(tmp_path):
    """#276: the next card reuses the widget id, so it mounts once the answered one is gone. A request of no thread (a
    held peer message) comes first here, as in the report."""
    async def sc(app, pilot, client, eng):
        for rid, session in (("r1", None), ("r2", app.session), ("r3", app.session)):
            pending_approval(eng, session, s.ApprovalRequested(request_id=rid, summary=rid, options=["allow", "deny"]))
        await until(lambda: len(app.approvals) == 3 and app.card is not None, pilot)
        for rid in ("r1", "r2", "r3"):
            await until(lambda: app.card is not None and app.card.req.request_id == rid, pilot)
            assert len(app.query(ApprovalCard)) == 1
            await pilot.press("ctrl+f")
            await pilot.press("o")
            await until(lambda: app.composer.has_focus, pilot)
        await until(lambda: len(responds(app)) == 3 and app.card is None, pilot)
        assert [r.request_id for r in responds(app)] == ["r1", "r2", "r3"]
        assert "DuplicateIds" not in app.transcript_text()

    run(tmp_path, sc)


def test_reconciling_a_closed_card_mounts_the_one_queued_behind_it(tmp_path):
    async def sc(app, pilot, client, eng):
        for rid, session in (("r1", None), ("r2", app.session)):
            pending_approval(eng, session, s.ApprovalRequested(request_id=rid, summary=rid, options=["allow", "deny"]))
        await until(lambda: len(app.approvals) == 2 and app.card is not None, pilot)
        del eng.approvals.open["r1"]  # closed behind the clients' backs: no approval.resolved goes out
        app.reconcile_every = 0.05
        await until(lambda: app.card is not None and app.card.req.request_id == "r2", pilot)
        assert len(app.query(ApprovalCard)) == 1 and "DuplicateIds" not in app.transcript_text()

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


def test_a_send_the_core_steers_shows_pending_and_up_pulls_it_back(tmp_path):
    """Typing into an opened running subagent: the window is not busy, so it sends, and the core steers the text into
    the child (D-070) with a message id. The line shows as a queued steer, and up pulls it back."""
    async def sc(app, pilot, client, eng):
        plain = client.send

        async def send_steered(body):  # the core's answer for a running child's session, whatever the mode
            if isinstance(body, s.TurnSend):
                return s.Response(id="r", result={"steered": True, "message_id": "m_held"})
            if isinstance(body, s.CommandRun) and body.name == "unsteer":
                return s.Response(id="r", result={"output": {"cancelled": [body.args]}})
            return await plain(body)
        client.send = send_steered
        assert not app.busy
        await send(app, pilot, "note for the child")
        await until(lambda: app.pending_steers, pilot)
        ((mid, block),) = app.pending_steers
        assert mid == "m_held" and block.steer and block.pending
        app.composer.focus()
        await pilot.press("up")
        await until(lambda: not app.pending_steers, pilot)
        assert app.composer.text == "note for the child"

    run(tmp_path, sc)
