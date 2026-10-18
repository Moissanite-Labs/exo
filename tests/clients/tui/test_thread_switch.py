"""Switching threads in one window (D-045, D-032). The pump drops the old thread's events, so its turn state,
approval card, queued steers and numbers stay behind. The thread that opens shows its own running turn (session.get
and session.resume carry the status) and the approvals it still waits on (approvals.list)."""

from __future__ import annotations

import asyncio
import time

import pytest

import test_tui
from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.widgets import BUSY_HINT, ApprovalCard
from test_tui import FakeServerClient, make_engine, pending_approval, run, send, until


def interrupts(app):
    return [b for b in app.sent if isinstance(b, s.TurnInterrupt)]


def activity(app) -> str:
    return str(app.query_one("#activity").render()).strip()


def last_line(app) -> str:
    return app.transcript_text().splitlines()[-1]


async def ask_then_new(app, pilot):
    """Thread A's turn waits on an approval, then /new opens thread B. Returns A's id and the request id."""
    await send(app, pilot, "ask")
    await until(lambda: app.card is not None, pilot)
    a, rid = app.session, app.card.req.request_id
    await send(app, pilot, "/new")
    await until(lambda: app.session != a, pilot)
    return a, rid


async def waiting_thread(client, eng) -> tuple[str, str]:
    """A thread whose turn already waits on an approval before any window shows it."""
    a = (await client.call(s.SessionCreate(title="busy", seat="tester")))["session"]["id"]
    await client.call(s.TurnSend(session=a, text="ask"))
    end = time.monotonic() + 5
    while not eng.approvals.open:
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.02)
    return a, next(iter(eng.approvals.open))


def run_with(tmp_path, scenario, client_cls=FakeServerClient, eng=None):
    """test_tui.run with a chosen client class and engine."""
    eng = eng or make_engine(tmp_path)
    client = client_cls(eng)

    async def main():
        app = ExoApp(client, seat="tester")
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


# ---------------------------------------------------------------- approvals.list on open (D-045)
@pytest.mark.parametrize("how", ["session", "resume"])
def test_a_window_opened_on_a_waiting_thread_shows_its_card_and_can_stop_the_turn(tmp_path, how):
    """A second window, /restart or `exo-tui --session/--resume`: the request was published before this window."""
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        a, rid = await waiting_thread(client, eng)
        app = ExoApp(client, seat="tester", **({"session": a} if how == "session" else {"resume": True}))
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            assert app.session == a
            assert app.card is not None and app.card.req.request_id == rid
            assert app.card.req.call is not None and app.card.req.call.name == "bash"  # decoded, not a raw dict
            assert app.busy and app.brand.live  # session.resume says a turn runs there
            app.composer.focus()
            await pilot.press("ctrl+c")
            await until(lambda: interrupts(app), pilot)
            assert interrupts(app)[0].session == a
            await until(lambda: not eng.loop.state(a).running, pilot)
            await until(lambda: app.card is None and not app.busy, pilot)

    asyncio.run(main())


def test_the_restored_card_answers_the_turn_that_waited(tmp_path):
    eng = make_engine(tmp_path)
    client = FakeServerClient(eng)

    async def main():
        a, rid = await waiting_thread(client, eng)
        app = ExoApp(client, seat="tester", session=a)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await until(lambda: app.card is not None, pilot)
            await pilot.press("ctrl+f")
            await pilot.press("o")
            await until(lambda: any(isinstance(b, s.ApprovalRespond) for b in app.sent), pilot)
            (r,) = [b for b in app.sent if isinstance(b, s.ApprovalRespond)]
            assert (r.request_id, r.decision) == (rid, "allow")
            await until(lambda: "after ask" in app.transcript_text() and not app.busy, pilot)
            assert not eng.approvals.open and app.card is None

    asyncio.run(main())


def test_switching_away_and_back_brings_the_waiting_card_back(tmp_path):
    async def sc(app, pilot, client, eng):
        a, rid = await ask_then_new(app, pilot)
        assert app.card is None and app.approvals == []  # A's card is not this thread's
        app.reconcile_every = 0.05
        await pilot.pause(0.3)
        assert rid in eng.approvals.open  # still waiting in the core
        await send(app, pilot, f"/resume {a}")
        await until(lambda: app.session == a, pilot)
        await until(lambda: app.card is not None, pilot)
        assert app.card.req.request_id == rid and app.busy
        await pilot.press("ctrl+f")
        await pilot.press("o")
        await until(lambda: "after ask" in app.transcript_text() and not app.busy, pilot)
        assert app.card is None and not eng.approvals.open

    run(tmp_path, sc)


def test_a_request_in_the_new_thread_gets_its_card_at_once(tmp_path):
    """The old card is gone before the switch, so the new thread's card neither waits behind it nor clashes with it
    (both use the id "approval")."""
    async def sc(app, pilot, client, eng):
        await ask_then_new(app, pilot)
        pending_approval(eng, app.session, s.ApprovalRequested(request_id="r_b", summary="in B", options=["allow", "deny"]))
        await until(lambda: app.card is not None, pilot)
        assert app.card.req.request_id == "r_b" and len(app.query(ApprovalCard)) == 1
        assert "DuplicateIds" not in app.transcript_text()  # the event pump noted no failed mount

    run(tmp_path, sc)


class RefusingClient(FakeServerClient):
    """The first approval.respond comes back refused; the request stays open in the core."""

    def __init__(self, engine):
        super().__init__(engine)
        self.refused = 0

    async def send(self, body):
        if isinstance(body, s.ApprovalRespond) and not self.refused:
            self.refused += 1
            return s.Response(id="x", ok=False, error=s.ErrorInfo(code="forbidden", message="refused for the test"))
        return await super().send(body)


def test_a_refused_answer_brings_the_card_back(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        rid = app.card.req.request_id
        await pilot.press("ctrl+f")
        await pilot.press("o")
        await until(lambda: client.refused, pilot)
        await until(lambda: app.card is not None, pilot)  # the request still waits, so it can be answered again
        assert app.card.req.request_id == rid
        await pilot.press("ctrl+f")
        await pilot.press("o")
        await until(lambda: "after ask" in app.transcript_text() and not app.busy, pilot)

    run_with(tmp_path, sc, RefusingClient)


# ---------------------------------------------------------------- turn state stays with its thread
def test_a_switch_while_a_turn_runs_leaves_the_new_thread_idle(tmp_path):
    async def sc(app, pilot, client, eng):
        a, rid = await ask_then_new(app, pilot)
        assert not app.busy and not app.brand.live and app.running_tools == 0
        eng.approvals.open[rid].future.set_result("allow")  # A's turn ends; its turn.completed is not for this window
        await until(lambda: not eng.loop.state(a).running, pilot)
        await pilot.pause(0.2)
        assert not app.busy and not app.brand.live
        assert activity(app) == "" and app.composer.placeholder != BUSY_HINT
        app.stop_turn()
        assert not interrupts(app) and last_line(app) == "nothing is running"
        await send(app, pilot, "hello")
        await until(lambda: any(isinstance(b, s.TurnSend) and b.text == "hello" for b in app.sent), pilot)
        (t,) = [b for b in app.sent if isinstance(b, s.TurnSend) and b.text == "hello"]
        assert t.mode == "send" and t.session == app.session  # a new turn, not a steer into a turn that ended

    run(tmp_path, sc)


def test_back_into_a_thread_whose_tool_still_runs_shows_it_live(tmp_path, monkeypatch):
    """No approval involved: busy comes from the status session.resume reports."""
    slow = {"match": "slow", "steps": [{"tool": "bash", "args": {"command": "sleep 2"}}, "after slow"]}
    monkeypatch.setitem(test_tui.FAKE, "rules", [*test_tui.FAKE["rules"], slow])

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "slow")
        await until(lambda: app.running_tools, pilot)
        a = app.session
        await send(app, pilot, "/new")
        await until(lambda: app.session != a, pilot)
        assert not app.busy and app.running_tools == 0 and activity(app) == ""
        await send(app, pilot, f"/resume {a}")
        await until(lambda: app.session == a, pilot)
        assert eng.loop.state(a).running and app.busy and app.brand.live
        assert app.card is None  # nothing waits on an approval
        await until(lambda: "after slow" in app.transcript_text() and not app.busy, pilot, timeout=10)

    run(tmp_path, sc)


def test_a_queued_steer_stays_with_its_thread(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "ask")
        await until(lambda: app.card is not None, pilot)
        a = app.session
        app.composer.focus()
        await send(app, pilot, "change of plan")
        await until(lambda: app.pending_steers, pilot)
        await send(app, pilot, "/new")
        await until(lambda: app.session != a, pilot)
        assert app.pending_steers == []
        app.composer.focus()
        await pilot.press("up")  # with nothing queued here, up is history: no unsteer goes to B for A's message
        await pilot.pause(0.1)
        assert not [b for b in app.sent if isinstance(b, s.CommandRun) and b.name == "unsteer"]
        assert [m.text() for m in eng.loop.state(a).steer] == ["change of plan"]  # still queued in A

    run(tmp_path, sc)


def test_the_old_threads_question_and_subagents_stay_behind(tmp_path):
    async def sc(app, pilot, client, eng):
        a = app.session
        eng.hub.publish(s.QuestionAsked(question_id="q1", text="which one?"), a)
        eng.hub.publish(s.TaskUpdated(task_id="t1", title="look around", status="running"), a)
        await until(lambda: app.question == "q1" and app.agents, pilot)
        assert "1 subagent" in activity(app)
        await send(app, pilot, "/new")
        await until(lambda: app.session != a, pilot)
        assert app.question is None and not app.agents and "subagent" not in activity(app)
        await send(app, pilot, "hello")  # a message for this thread, not an answer to A's question
        await until(lambda: any(isinstance(b, s.TurnSend) and b.text == "hello" for b in app.sent), pilot)
        assert not [b for b in app.sent if isinstance(b, s.QuestionAnswer)]

    run(tmp_path, sc)


# ---------------------------------------------------------------- COST and MEM belong to the open thread (D-032)
def test_cost_and_meters_belong_to_the_open_thread(tmp_path):
    async def sc(app, pilot, client, eng):
        a = app.session
        priced = s.Usage(input_tokens=1000, output_tokens=200, cache_read_tokens=1000, cost_usd=0.05)
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=priced), a)
        eng.hub.publish(s.UsageUpdated(usage=priced, session_total=priced, model="fake:x"), a)
        await until(lambda: app.cost == 0.05, pilot)
        assert app.cost_label() == "$0.0500" and app.ctx_tokens == 2000 and app.cache_share == 0.5
        await send(app, pilot, "/new")
        await until(lambda: app.session != a, pilot)
        assert app.cost_label() == "–" and "$" not in app.status_text()
        assert (app.ctx_tokens, app.cache_share, app.calls_seen) == (0, None, 0)
        await send(app, pilot, "hello")  # the fake route has no price
        await until(lambda: "Hello **there**, friend" in app.transcript_text() and not app.busy, pilot)
        assert app.cost_label() == "unknown" and "$" not in app.status_text()
        await send(app, pilot, "/undo")  # the same thread reloads: its numbers stay
        await until(lambda: any(isinstance(b, s.SessionUndo) for b in app.sent) and "hello" not in app.transcript_text(),
                    pilot)
        assert app.cost_label() == "unknown" and app.calls_seen == 1
        await send(app, pilot, f"/resume {a}")
        await until(lambda: app.session == a, pilot)
        assert app.cost_label() == "$0.0500" and app.calls_seen == 1  # what this window saw of A; never B's numbers
        assert (app.ctx_tokens, app.cache_share) == (0, None)  # MEM and CACHE come with A's next call

    run(tmp_path, sc)


def test_a_threads_plus_survives_a_round_trip(tmp_path):
    """The core's session_total keeps counting the whole thread, so a return keeps what this window saw of it: the
    '+' of its own unpriced call or of a subagent's unpriced roll-up stays on its next priced total (D-032)."""
    async def sc(app, pilot, client, eng):
        a = app.session
        bare = s.Usage(input_tokens=500, output_tokens=10)
        priced = s.Usage(input_tokens=1000, output_tokens=10, cost_usd=0.05)
        eng.hub.publish(s.ModelCall(route="fake:y", requested="fake:y", usage=bare), a)
        eng.hub.publish(s.UsageUpdated(usage=bare, session_total=bare, model="fake:y"), a)
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=priced), a)
        eng.hub.publish(s.UsageUpdated(usage=priced, session_total=bare + priced, model="fake:x"), a)
        await until(lambda: app.calls_seen == 2 and app.cost == 0.05, pilot)
        assert app.cost_label() == "$0.0500+"

        await send(app, pilot, "/new")  # B: a priced call of its own and a subagent's unpriced roll-up
        await until(lambda: app.session != a, pilot)
        b = app.session
        assert app.cost_label() == "–"
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=priced), b)
        eng.hub.publish(s.UsageUpdated(usage=priced, session_total=priced, model="fake:x"), b)
        eng.hub.publish(s.UsageUpdated(usage=bare, session_total=priced + bare, model="fake:y"), b)
        await until(lambda: app.unpriced == 1 and app.cost == 0.05, pilot)
        assert app.cost_label() == "$0.0500+"

        await send(app, pilot, f"/resume {a}")
        await until(lambda: app.session == a, pilot)
        assert app.cost_label() == "$0.0500+" and (app.calls_seen, app.unpriced) == (2, 1)
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=priced), a)
        eng.hub.publish(s.UsageUpdated(usage=priced, session_total=bare + priced + priced, model="fake:x"), a)
        await until(lambda: app.calls_seen == 3, pilot)
        assert app.cost_label() == "$0.1000+"

        await send(app, pilot, f"/resume {b}")
        await until(lambda: app.session == b, pilot)
        assert app.cost_label() == "$0.0500+"
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=priced), b)
        eng.hub.publish(s.UsageUpdated(usage=priced, session_total=priced + bare + priced, model="fake:x"), b)
        await until(lambda: app.calls_seen == 3, pilot)
        assert app.cost_label() == "$0.1000+"

    run(tmp_path, sc)


def test_a_subagent_roll_up_marks_unpriced_calls_and_leaves_mem_alone(tmp_path):
    async def sc(app, pilot, client, eng):
        p = app.session
        priced = s.Usage(input_tokens=1000, output_tokens=200, cost_usd=0.05)
        child = s.Usage(input_tokens=5000, output_tokens=900)
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=priced), p)
        eng.hub.publish(s.UsageUpdated(usage=priced, session_total=priced, model="fake:x"), p)
        eng.hub.publish(s.ModelCall(route="fake:y", requested="fake:y", usage=child), "s_child")  # the child's session
        eng.hub.publish(s.UsageUpdated(usage=child, session_total=priced + child, model="fake:y"), p)  # the roll-up
        await until(lambda: app.unpriced == 1, pilot)
        assert app.cost_label() == "$0.0500+"
        assert (app.ctx_tokens, app.model) == (1000, "fake:x")  # the child's total is not this context

        await send(app, pilot, "/new")  # the other way round: an unpriced parent, a priced child
        await until(lambda: app.session != p, pilot)
        b, own, kid = app.session, s.Usage(input_tokens=300, output_tokens=20), s.Usage(input_tokens=90, cost_usd=0.03)
        eng.hub.publish(s.ModelCall(route="fake:x", requested="fake:x", usage=own), b)
        eng.hub.publish(s.UsageUpdated(usage=own, session_total=own, model="fake:x"), b)
        await until(lambda: app.cost_label() == "unknown", pilot)
        eng.hub.publish(s.UsageUpdated(usage=kid, session_total=own + kid, model="fake:y"), b)
        await until(lambda: app.cost == 0.03, pilot)
        assert app.cost_label() == "$0.0300+" and app.ctx_tokens == 300 and app.calls_seen == 1

    run(tmp_path, sc)


DELEGATE = {"rules": [
    {"match": "spawn", "steps": [{"tool": "delegate", "args": {"goal": "hello child, say hi", "role": "scout"}},
                                 "parent done"]},
    {"match": "hello", "steps": ["Hello from the child, " * 20]},
]}


def test_a_real_subagent_on_an_unpriced_route_marks_the_cost(tmp_path):
    """End to end: the parent's route is priced, the child's is not. The child's model.call goes out on its own
    session; only the roll-up reaches this window."""
    pm = PluginManager()
    pm._run_register(Manifest(name="t"), "dir", lambda ctx: ctx.register_hook("pre_tool_call", lambda **kw: None))
    eng = CoreEngine(tmp_path / "home", {"fake": DELEGATE, "models": {"fake:x": {"input": 1000.0, "output": 1000.0}},
                                         "roles": {"main": ["fake:x"], "aux": ["fake:x"], "delegate": ["fake:y"]}},
                     plugins=pm, cwd=str(tmp_path), env={})

    async def sc(app, pilot, client, _eng):
        ctx = []
        handle = app.handle

        def spy(ev):
            handle(ev)
            if isinstance(ev.body, s.UsageUpdated):
                ctx.append(app.ctx_tokens)

        app.handle = spy
        await send(app, pilot, "spawn a child")
        await until(lambda: "parent done" in app.transcript_text() and not app.busy, pilot, timeout=20)
        assert app.cost is not None and app.cost_label().endswith("+") and app.unpriced == 1
        assert len(ctx) == 3 and ctx[1] == ctx[0]  # the roll-up between the parent's two calls leaves MEM alone

    run_with(tmp_path, sc, eng=eng)
