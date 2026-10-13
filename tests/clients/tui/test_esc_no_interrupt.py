"""D-043: Esc never stops a turn. Ctrl+C or /stop does. The approval card does not take a typed key, and
failover does not block the event loop."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from exo.protocol import schema as s
from exo_tui import conn
from exo_tui.widgets import ApprovalCard, Composer
from test_tui import pending_approval, run, send, until


def interrupts(app):
    return [b for b in app.sent if isinstance(b, s.TurnInterrupt)]


def test_esc_during_a_turn_does_not_stop_it(tmp_path):
    async def sc(app, pilot, client, eng):
        app.busy = True
        app.composer.text = "draft"
        await pilot.press("escape")
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not interrupts(app)
        assert app.composer.text == "draft"
        assert "stopped" not in app.transcript_text()

    run(tmp_path, sc)


def test_ctrl_c_during_a_turn_stops_it_and_keeps_the_draft(tmp_path):
    async def sc(app, pilot, client, eng):
        app.busy = True
        app.composer.text = "draft"
        app.composer.focus()
        await pilot.press("ctrl+c")
        await until(lambda: interrupts(app), pilot)
        assert app.composer.text == "draft"
        assert "stopped" in app.transcript_text()

    run(tmp_path, sc)


def test_ctrl_c_when_idle_does_not_stop_or_quit(tmp_path):
    async def sc(app, pilot, client, eng):
        await pilot.press("ctrl+c")
        await pilot.pause(0.2)
        assert not interrupts(app) and app.is_running

    run(tmp_path, sc)


def test_slash_stop_stops_the_turn(tmp_path):
    async def sc(app, pilot, client, eng):
        app.busy = True
        await send(app, pilot, "/stop")
        await until(lambda: interrupts(app), pilot)
        assert "stopped" in app.transcript_text()

    run(tmp_path, sc)


def test_esc_clears_the_guidance_line_and_does_nothing_else(tmp_path):
    async def sc(app, pilot, client, eng):
        from exo.complete import Tip

        app.tip = Tip("intro", "first time here?", "/intro")
        app.refresh_status()
        await pilot.press("escape")
        assert app.tip is None
        await pilot.press("escape")  # nothing left to close: nothing happens
        assert not interrupts(app) and app.is_running

    run(tmp_path, sc)


def test_approval_card_does_not_take_a_typed_a(tmp_path):
    async def sc(app, pilot, client, eng):
        app.composer.focus()
        for ch in "ab":
            await pilot.press(ch)  # the user is typing a steer message when the card appears
        await send_ask(app, pilot, eng)
        assert not app.card.has_focus
        await pilot.press("a")
        await pilot.pause(0.1)
        assert app.composer.text.endswith("a") and "ab" in app.composer.text
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]

    run(tmp_path, sc)


async def send_ask(app, pilot, eng):
    from exo.protocol import schema as sc_

    pending_approval(eng, app.session, sc_.ApprovalRequested(request_id="r1", risk="medium", summary="run echo?",
                                                            options=["allow", "allow_always", "deny"]))
    await until(lambda: app.card is not None, pilot)


def test_allow_always_needs_a_confirm(tmp_path):
    async def sc(app, pilot, client, eng):
        await send_ask(app, pilot, eng)
        assert app.composer.has_focus
        await pilot.press("ctrl+f")
        assert isinstance(app.card, ApprovalCard) and app.card.has_focus
        await pilot.press("a")
        await pilot.pause(0.05)
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
        await pilot.press("enter")
        await until(lambda: [b for b in app.sent if isinstance(b, s.ApprovalRespond)], pilot)
        assert [b for b in app.sent if isinstance(b, s.ApprovalRespond)][0].decision == "allow_always"

    run(tmp_path, sc)


def test_shift_a_also_requires_confirmation_and_esc_does_not_deny(tmp_path):
    async def sc(app, pilot, client, eng):
        await send_ask(app, pilot, eng)
        await pilot.press("ctrl+f")
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.card is not None and not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
        await pilot.press("ctrl+f")
        await pilot.press("A")
        assert app.card.confirming and not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
        await pilot.press("enter")
        await until(lambda: [b for b in app.sent if isinstance(b, s.ApprovalRespond)], pilot)
        assert [b for b in app.sent if isinstance(b, s.ApprovalRespond)][0].decision == "allow_always"

    run(tmp_path, sc)


@pytest.mark.parametrize("text", ["Act now", "ok", "do it"])
def test_ordinary_typing_after_send_never_answers_an_approval(tmp_path, text):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        assert app.composer.text == "" and app.composer.has_focus
        app.reconcile_every = 0
        await app.reconcile_approvals()  # the pending fake-core request survives normal synchronization
        for ch in text:
            await pilot.press("space" if ch == " " else ch)
        assert app.composer.text == text and app.card is not None
        await pilot.press("enter")
        await pilot.pause(.1)
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
    run(tmp_path, sc)


def test_confirmation_cancels_when_focus_returns_to_editable_composer(tmp_path):
    async def sc(app, pilot, client, eng):
        await send_ask(app, pilot, eng)
        await pilot.press("ctrl+f", "a")
        assert app.card.confirming
        app.composer.focus()
        await pilot.press("A")
        assert not app.card.confirming and app.composer.text == "A"
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
        await pilot.press("ctrl+f", "enter")
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
    run(tmp_path, sc)


def test_click_focus_and_deny_note_cancellation_restore_focus(tmp_path):
    async def sc(app, pilot, client, eng):
        await send_ask(app, pilot, eng)
        assert app.composer.has_focus
        await pilot.click("#approval-body")
        assert app.card.has_focus
        await pilot.press("escape")
        assert app.composer.has_focus and app.card is not None
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
        await pilot.click("#approval-body")
        await pilot.press("d", "A")  # uppercase A in the editable deny note never grants anything
        assert not [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
        await pilot.press("escape")
        assert app.card.has_focus and not app.card.noting
        await pilot.press("o")
        await until(lambda: [b for b in app.sent if isinstance(b, s.ApprovalRespond)], pilot)
        assert app.composer.has_focus
    run(tmp_path, sc)


def test_failover_does_not_block_the_event_loop(tmp_path, monkeypatch):
    def slow_open(home, agent, wait=5.0):
        time.sleep(0.6)  # what a socket connect, an engine start or the poll loop costs
        return object()

    class Dead:
        async def send(self, body):
            raise httpx.ConnectError("gone")

        async def aclose(self):
            pass

    async def go():
        monkeypatch.setattr(conn, "_open", slow_open)
        shared = conn.SharedClient(tmp_path, "default", Dead())
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        t = asyncio.create_task(ticker())
        try:
            await shared._failover()
        finally:
            t.cancel()
        assert ticks >= 5, f"the loop was blocked during failover (ticks={ticks})"

    asyncio.run(go())
