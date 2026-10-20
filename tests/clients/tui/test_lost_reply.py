"""D-060: a joined window never resends a change whose reply was lost, so it says so instead: the typed text goes
back in the composer, and an approval the core still holds gets its card back."""

from __future__ import annotations

import asyncio

import httpx

from exo.protocol import schema as s
from exo_tui.app import ExoApp
from test_tui import FakeServerClient, make_engine, pending_approval, send, until


class LosingClient(FakeServerClient):
    """The connection drops once for each of these commands, before the core sees them (the core survives)."""

    lose = (s.TurnSend, s.ApprovalRespond)

    def __init__(self, engine):
        super().__init__(engine)
        self.lost: set[type] = set()

    async def send(self, body):
        if isinstance(body, self.lose) and type(body) not in self.lost:
            self.lost.add(type(body))
            raise httpx.ReadError("connection lost")
        return await super().send(body)


def run(tmp_path, scenario):
    eng = make_engine(tmp_path)
    client = LosingClient(eng)

    async def main():
        app = ExoApp(client, seat="tester")
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


def test_lost_turn_is_reported_and_the_text_kept(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "pay the invoice")
        await until(lambda: "may not have been delivered" in app.transcript_text(), pilot)
        assert app.composer.text == "pay the invoice"  # to send again, or drop
        assert sum(isinstance(b, s.TurnSend) for b in app.sent) == 1  # never resent by itself

    run(tmp_path, sc)


def test_lost_approval_answer_puts_back_the_card_the_core_still_holds(tmp_path):
    async def sc(app, pilot, client, eng):
        pending_approval(eng, app.session, s.ApprovalRequested(request_id="r1", risk="medium", summary="run echo?",
                                                              options=["allow", "allow_always", "deny"]))
        await until(lambda: app.card is not None, pilot)
        await pilot.press("ctrl+f")
        await pilot.pause(0.05)
        await pilot.press("o")
        await until(lambda: "may not have been delivered" in app.transcript_text(), pilot)
        await until(lambda: app.card is not None and app.card.is_mounted and app.card.req.request_id == "r1", pilot)
        assert not eng.approvals.open["r1"].future.done()  # the core is still waiting on it

    run(tmp_path, sc)


def test_lost_question_answer_keeps_the_question_open_and_the_text(tmp_path):
    async def sc(app, pilot, client, eng):
        client.lose = (s.QuestionAnswer,)
        eng.hub.publish(s.QuestionAsked(question_id="q1", text="which file?"), app.session)
        await until(lambda: app.question == "q1", pilot)
        await send(app, pilot, "the second one")
        await until(lambda: "may not have been delivered" in app.transcript_text(), pilot)
        assert app.question == "q1"  # the next line still answers it
        assert app.composer.text == "the second one"
        assert sum(isinstance(b, s.QuestionAnswer) for b in app.sent) == 1

    run(tmp_path, sc)


def test_lost_unsteer_reply_leaves_the_steer_pending(tmp_path):
    async def sc(app, pilot, client, eng):
        client.lose = ()  # the turn and the steer go through
        await send(app, pilot, "ask")  # the gated call keeps the turn running
        await until(lambda: app.card is not None, pilot)
        app.composer.focus()
        await send(app, pilot, "change course")
        await until(lambda: app.pending_steers, pilot)
        real = client.send

        async def lose_unsteer(body):
            if isinstance(body, s.CommandRun) and body.name == "unsteer":
                raise httpx.ReadError("connection lost")
            return await real(body)
        client.send = lose_unsteer
        app.composer.focus()
        await send(app, pilot, "/unsteer")
        await until(lambda: "may not have been delivered" in app.transcript_text(), pilot)
        assert app.pending_steers  # whether it was pulled back is unknown, so it is not shown as delivered
        assert "change course" in app.transcript_text()

    run(tmp_path, sc)


def test_refused_principal_check_says_not_sent_and_why(tmp_path):
    """A refused peer check (``PrincipalRefused``, a ConnectError) writes no byte, so the window says the command was
    not sent and why, instead of "may not have been delivered" (#226). The text still goes back to the composer."""
    from exo.peerauth import PrincipalRefused

    async def sc(app, pilot, client, eng):
        client.lose = ()
        real = client.send

        async def refuse(body):
            if isinstance(body, s.TurnSend):
                raise PrincipalRefused("principal connection refused: exod.sock is served by pid 4242, not the core "
                                       "in the launch record (pid 4141)")
            return await real(body)
        client.send = refuse
        await send(app, pilot, "pay the invoice")
        await until(lambda: "not sent:" in app.transcript_text(), pilot)
        text = app.transcript_text()
        assert "served by pid 4242, not the core in the launch record" in text
        assert "may not have been delivered" not in text
        assert app.composer.text == "pay the invoice"
        assert sum(isinstance(b, s.TurnSend) for b in app.sent) == 1

    run(tmp_path, sc)
