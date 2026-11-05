"""Focused tests for ``exo.core.continuation`` (the harness turn-continuation slice).

These cover only the module's own public API. They do not touch the engine: the point is to pin the
safety rules in the module docstring so the later integration has a contract to satisfy.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from exo import provenance
from exo.core.continuation import (
    DEFAULT_MAX_CHAIN,
    END_TURN,
    MAX_WAKE_AFTER,
    ORIGIN,
    Continuations,
    Request,
    register,
)
from exo.core.tools.registry import Registry, ToolError


class Harness:
    """Minimal stand-in for engine/Delegator wiring, with a fake sleep so timers fire at once."""

    def __init__(self, *, running: bool = False, max_chain: int = DEFAULT_MAX_CHAIN) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.notes: list[str] = []
        self.delays: list[float] = []
        self.running = running
        self.cont = Continuations(
            self._start,
            lambda session: self.running,
            sleep=self._sleep,
            notify=lambda session, text: self.notes.append(text),
            max_chain=max_chain,
        )

    async def _start(self, session: str, text: str, meta: dict) -> None:
        self.calls.append((session, text, meta))

    async def _sleep(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


async def settle() -> None:
    """Let a zero-delay timer task run to completion."""
    await asyncio.sleep(0.02)


# ------------------------------------------------------------------ request()


def test_request_wakes_property() -> None:
    assert Request(turn_id="t").wakes is False
    assert Request(turn_id="t", continue_now=True).wakes is True
    assert Request(turn_id="t", wake_after=5).wakes is True
    assert Request(turn_id="t", followup_pending=True).wakes is False


def test_request_rejects_bad_flags() -> None:
    h = Harness()
    for args in (
        {"continue_now": "yes"},
        {"followup_pending": 1},
        {"wake_after": 0},
        {"wake_after": MAX_WAKE_AFTER + 1},
        {"wake_after": 1.5},
        {"wake_after": True},  # bool is not a whole number of seconds
        {"continue_now": True, "wake_after": 30},  # exactly one wake mode
    ):
        with pytest.raises(ToolError):
            h.cont.request("s", "t1", args)


def test_request_messages_and_reason_cleanup() -> None:
    h = Harness()
    assert "right after this turn" in h.cont.request("s", "t1", {"continue_now": True})
    out = h.cont.request("s", "t1", {"wake_after": 120, "followup_pending": True})
    assert "120s after this turn" in out and "follow-up is recorded only" in out
    assert "no wake is scheduled" in h.cont.request("s", "t1", {})
    assert "no wake is scheduled" in h.cont.request("s", "t1", {"followup_pending": True})
    h.cont.request("s", "t1", {"continue_now": True, "reason": "  " + "x" * 400 + "  "})
    assert h.cont._s["s"].request is not None
    assert len(h.cont._s["s"].request.reason) == 300


def test_second_request_in_same_turn_replaces_first() -> None:
    h = Harness()
    h.cont.request("s", "t1", {"wake_after": 600, "reason": "first"})
    h.cont.request("s", "t1", {"continue_now": True, "reason": "second"})
    assert h.cont._s["s"].request is not None
    assert h.cont._s["s"].request.reason == "second"


# ------------------------------------------------------------- turn_ended()


def test_turn_ended_without_request_does_nothing() -> None:
    async def go() -> None:
        h = Harness()
        assert h.cont.turn_ended("s", "t1", "end") == "none"
        assert h.cont.pending("s") is False

    asyncio.run(go())


def test_continue_now_schedules_one_harness_wake() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True, "reason": "keep digging"})
        assert h.cont.turn_ended("s", "t1", "end") == "scheduled"
        assert h.cont.pending("s") is True
        await settle()
        assert h.cont.pending("s") is False
        assert len(h.calls) == 1
        session, text, meta = h.calls[0]
        assert session == "s"
        assert meta["origin"] == ORIGIN
        assert meta["continuation_of"] == "t1"
        assert meta["author"] == provenance.SYSTEM
        assert "harness continuation wake" in text and "keep digging" in text
        assert h.delays == [0]
        assert h.notes[-1] == "continuation scheduled now"

    asyncio.run(go())


def test_wake_after_uses_the_requested_delay_and_coalesces() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"wake_after": 30})
        assert h.cont.turn_ended("s", "t1", "end") == "scheduled"
        h.cont.request("s", "t1b", {"wake_after": 45})
        assert h.cont.turn_ended("s", "t1b", "end") == "scheduled"
        await settle()
        # the first timer was cancelled, so exactly one wake with the newest delay
        assert h.delays[-1] == 45.0
        assert len(h.calls) == 1

    asyncio.run(go())


def test_reply_request_from_a_different_turn_is_ignored() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True})
        assert h.cont.turn_ended("s", "other", "end") == "none"
        await settle()
        assert h.calls == []

    asyncio.run(go())


def test_followup_only_is_recorded_never_wakes() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"followup_pending": True, "reason": "tell them the build broke"})
        assert h.cont.turn_ended("s", "t1", "end") == "followup"
        await settle()
        assert h.calls == []
        assert h.cont._s["s"].followup == "tell them the build broke"
        assert h.notes == ["follow-up pending (recorded only): tell them the build broke"]

    asyncio.run(go())


@pytest.mark.parametrize("stop_reason", ["interrupt", "max_tokens", "error", "length"])
def test_non_end_stop_reasons_cancel_the_wake(stop_reason: str) -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True})
        assert h.cont.turn_ended("s", "t1", stop_reason) == f"cancelled:{stop_reason}"
        await settle()
        assert h.calls == []
        assert h.cont.pending("s") is False

    asyncio.run(go())


def test_error_argument_cancels_even_with_stop_reason_end() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True})
        assert h.cont.turn_ended("s", "t1", "end", error="provider exploded") == "cancelled:end"
        await settle()
        assert h.calls == []

    asyncio.run(go())


def test_denied_tool_cancels_the_wake() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True})
        h.cont.note_denied("s", "t1")
        assert h.cont.turn_ended("s", "t1", "end") == "cancelled:denied"
        await settle()
        assert h.calls == []
        assert h.cont._s["s"].denied == set()

    asyncio.run(go())


def test_denial_in_another_turn_does_not_leak() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.note_denied("s", "t1")
        h.cont.request("s", "t2", {"continue_now": True})
        assert h.cont.turn_ended("s", "t2", "end") == "scheduled"
        await settle()
        assert len(h.calls) == 1

    asyncio.run(go())


def test_chain_cap_stops_a_model_that_asks_forever() -> None:
    async def go() -> None:
        h = Harness(max_chain=2)
        out = []
        for i in range(3):
            h.cont.request("s", f"t{i}", {"continue_now": True})
            out.append(h.cont.turn_ended("s", f"t{i}", "end"))
            await settle()
        assert out == ["scheduled", "scheduled", "capped"]
        assert len(h.calls) == 2
        assert "continuation stopped after 2 harness wakes" in h.notes[-1]
        assert h.cont._s["s"].chain == 0  # reset, so a user turn resumes the budget

    asyncio.run(go())


def test_user_turn_cancels_pending_wake_but_harness_wake_does_not() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"wake_after": 60})
        assert h.cont.turn_ended("s", "t1", "end") == "scheduled"
        h.cont.turn_started("s", {"origin": ORIGIN, "continuation_of": "t1"})
        assert h.cont.pending("s") is True
        h.cont.turn_started("s", {"origin": "user"})
        assert h.cont.pending("s") is False
        await settle()
        assert h.calls == []

    asyncio.run(go())


def test_running_turn_drops_the_wake_instead_of_paralleling() -> None:
    async def go() -> None:
        h = Harness(running=True)
        h.cont.request("s", "t1", {"continue_now": True})
        assert h.cont.turn_ended("s", "t1", "end") == "scheduled"
        await settle()
        assert h.calls == []
        assert h.notes[-1] == "continuation dropped: a turn is already running"

    asyncio.run(go())


def test_closed_rejects_and_close_cancels_pending() -> None:
    async def go() -> None:
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True})
        h.cont.closed = True
        assert h.cont.turn_ended("s", "t1", "end") == "closed"
        h.cont.closed = False
        h.cont.request("s", "t2", {"wake_after": 600})
        assert h.cont.turn_ended("s", "t2", "end") == "scheduled"
        assert h.cont.pending("s") is True
        h.cont.close()
        assert h.cont.pending("s") is False
        await settle()
        assert h.calls == []

    asyncio.run(go())


def test_cancel_reports_whether_anything_was_dropped() -> None:
    async def go() -> None:
        h = Harness()
        assert h.cont.cancel("never-seen") is False
        h.cont.request("s", "t1", {"wake_after": 600})
        assert h.cont.turn_ended("s", "t1", "end") == "scheduled"
        assert h.cont.cancel("s") is True
        assert h.cont.cancel("s") is False
        assert h.cont.pending("s") is False
        await settle()
        assert h.calls == []

    asyncio.run(go())


# ----------------------------------------------------------------- register()


def _handler(reg: Registry, name: str):
    getter = getattr(reg, "get", None)
    if callable(getter):
        try:
            return getter(name).handler
        except Exception:  # pragma: no cover - defensive against registry shapes
            pass
    return reg._tools[name].handler


def test_end_turn_spec_shape() -> None:
    assert END_TURN.name == "exo_end_turn"
    assert END_TURN.toolset == "harness"
    props = END_TURN.parameters["properties"]
    assert set(props) == {"continue_now", "wake_after", "followup_pending", "reason"}
    assert props["wake_after"]["maximum"] == MAX_WAKE_AFTER
    assert "workers_pending" not in props
    assert DEFAULT_MAX_CHAIN >= 1


def test_register_wires_the_tool_and_reports_requests() -> None:
    h = Harness()
    reg = Registry()
    register(reg, h.cont)
    call = _handler(reg, "exo_end_turn")
    ctx = SimpleNamespace(meta={"turn_id": "t1"}, session="s")
    assert "right after this turn" in call({"continue_now": True}, ctx)
    assert h.cont._s["s"].request is not None
    assert h.cont._s["s"].request.turn_id == "t1"


def test_register_rejects_calls_outside_a_turn_and_for_subagents() -> None:
    h = Harness()
    reg = Registry()
    register(reg, h.cont, allowed=lambda session: session != "sub")
    call = _handler(reg, "exo_end_turn")
    with pytest.raises(ToolError):
        call({"continue_now": True}, SimpleNamespace(meta={}, session="s"))
    with pytest.raises(ToolError):
        call({"continue_now": True}, SimpleNamespace(meta={"turn_id": "t1"}, session="sub"))


# ------------------------------------------------------------------ Stop race fence (generation)


def _fired_wake(h: Harness, session: str = "s") -> dict:
    """Schedule continue_now and let the timer fire: the wake's start() ran but no turn holds the lock yet."""
    async def go() -> dict:
        h.cont.request(session, "t1", {"continue_now": True})
        assert h.cont.turn_ended(session, "t1", "end") == "scheduled"
        await settle()
        return h.calls[-1][2]
    return go


def test_fired_wake_is_live_until_stop() -> None:
    h = Harness()
    meta = asyncio.run(_fired_wake(h)())
    assert meta["origin"] == ORIGIN and "continuation_gen" in meta
    assert h.cont.live("s", meta) is True
    assert h.cont.pending("s") is False  # timer already done: the old cancel found nothing to cancel
    h.cont.cancel("s")  # Stop in the window after fire, before the lock
    assert h.cont.live("s", meta) is False


def test_fired_wake_voided_by_human_turn_and_close() -> None:
    h = Harness()
    meta = asyncio.run(_fired_wake(h)())
    h.cont.turn_started("s", {"author": "user"})
    assert h.cont.live("s", meta) is False
    h2 = Harness()
    meta2 = asyncio.run(_fired_wake(h2)())
    h2.cont.close()
    assert h2.cont.live("s", meta2) is False


def test_non_continuation_turns_always_live() -> None:
    h = Harness()
    h.cont.cancel("s")
    h.cont.close()
    assert h.cont.live("s", None) is True
    assert h.cont.live("s", {"author": "user"}) is True
    assert h.cont.live("other", {"origin": "peer"}) is True


def test_wake_turn_started_does_not_bump_its_own_gen() -> None:
    h = Harness()
    meta = asyncio.run(_fired_wake(h)())
    h.cont.turn_started("s", meta)
    assert h.cont.live("s", meta) is True


class _LoopStub:
    """Just enough of ``Loop`` for ``Loop.send`` to run its lock / guard path deterministically."""

    @staticmethod
    def make():
        from exo.core.loop import Loop
        st = SimpleNamespace(lock=asyncio.Lock(), running=False, accepting_steers=False,
                             interrupt=asyncio.Event(), steer=[])
        ran: list[str] = []

        async def _turn(session, msg, state):
            ran.append(session)
            return "result"

        fake = SimpleNamespace(
            store=SimpleNamespace(get_session=lambda s: {"id": s}, assert_open=lambda s: None),
            state=lambda s: st, _turn=_turn)
        return Loop, fake, st, ran


def test_loop_send_guard_blocks_wake_cancelled_while_waiting_on_lock() -> None:
    async def go() -> None:
        Loop, fake, st, ran = _LoopStub.make()
        h = Harness()
        h.cont.request("s", "t1", {"continue_now": True})
        h.cont.turn_ended("s", "t1", "end")
        await settle()
        meta = h.calls[-1][2]
        await st.lock.acquire()  # a turn (or hook) holds the session: the fired wake queues on the lock
        task = asyncio.ensure_future(Loop.send(fake, "s", "wake", mode="send", meta=meta,
                                               guard=lambda: h.cont.live("s", meta)))
        await asyncio.sleep(0)
        assert h.cont.live("s", meta)  # still live before Stop
        h.cont.cancel("s")  # Stop arrives while the wake waits for the lock
        st.lock.release()
        assert await task is None
        assert ran == [] and st.running is False
    asyncio.run(go())


def test_loop_send_without_guard_runs_normal_turn_unchanged() -> None:
    async def go() -> None:
        Loop, fake, st, ran = _LoopStub.make()
        assert await Loop.send(fake, "s", "hi", meta={"author": "user"}) == "result"
        assert await Loop.send(fake, "s", "hi", meta={"author": "user"}, guard=lambda: True) == "result"
        assert ran == ["s", "s"]
    asyncio.run(go())
