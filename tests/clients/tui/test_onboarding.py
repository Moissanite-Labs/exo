"""D-030: /intro, first run, tutorial hints, the toggle, and the skill/help coverage test."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from test_tui import FAKE, FakeServerClient, make_engine, send, until

import exo_tui
from exo.config import Config
from exo.core.engine import TUTORIAL_LINE, CoreEngine
from exo.protocol import schema as s
from exo_tui import app as app_mod
from exo_tui.app import COMMANDS, ExoApp
from exo_tui.onboarding import INTRO_PROMPT, Onboarding, load_catalog

SKILL = Path(__file__).resolve().parents[3] / "skills" / "exo-intro" / "SKILL.md"
FAKE["rules"].insert(0, {"match": "exo-intro", "steps": ["Welcome to Exo."]})


def run_app(tmp_path, scenario, client_cls=FakeServerClient, **kw):
    eng = make_engine(tmp_path)
    client = client_cls(eng)

    async def main():
        app = ExoApp(client, seat="tester", home=str(tmp_path / "exo-state"), **kw)
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


def tips(app) -> list[str]:
    return [ln for ln in app.transcript_text().splitlines() if ln.startswith("tip: ")]


# ---------------------------------------------------------------- the skill cannot drift from /help
def test_skill_mentions_every_help_command(tmp_path):
    text = SKILL.read_text(encoding="utf-8")
    eng = make_engine(tmp_path)
    own = asyncio.run(eng._commands_list(s.CommandsList()))["commands"]
    names = set(COMMANDS) | {c["name"] for c in own}
    assert {"intro", "tutorial", "trace", "findings"} <= names
    missing = sorted(n for n in names if not re.search(rf"/{re.escape(n)}\b", text))
    assert not missing, f"skills/exo-intro/SKILL.md does not mention: {missing}"


def test_intro_and_tutorial_are_in_help(tmp_path):
    async def sc(app, pilot, client, eng):
        helptext = app.help_text()
        assert "/intro" in helptext and "/tutorial" in helptext

    run_app(tmp_path, sc)


def test_every_hint_has_text_and_a_real_command():
    cat = load_catalog()
    assert len(cat) == 12
    for key, h in cat.items():
        assert h["text"] and h["then"].lstrip("/") in set(COMMANDS) | {"trace", "findings", "context"}, key


# ---------------------------------------------------------------- first run
class Probe(FakeServerClient):
    """Records whether the home was already onboarded at the moment the intro turn was sent."""

    onboarded_at_send: list[bool] = []

    async def send(self, body):
        if isinstance(body, s.TurnSend) and body.text.startswith("/intro"):
            self.onboarded_at_send.append(Onboarding(self.home).onboarded)
        return await super().send(body)


def test_first_run_intro_marks_only_after_the_turn_starts(tmp_path):
    home = tmp_path / "exo-state"
    Probe.onboarded_at_send = []
    Probe.home = home

    async def sc(app, pilot, client, eng):
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
        assert Probe.onboarded_at_send == [False]  # not written before the turn began
        assert Onboarding(home).onboarded
        assert "/intro" in app.transcript_text().splitlines()[0]  # first line: a plain startup prints no "new thread" line
        assert INTRO_PROMPT.split("\n")[2][:30] not in app.transcript_text()  # the user sees "/intro", not the brief
        await until(lambda: app.tutorial, pilot)
        assert Config.load(eng.home).get("tutorial.enabled") is True
        sent = [b for b in app.sent if isinstance(b, s.TurnSend)]
        assert len(sent) == 1 and "exo-intro skill" in sent[0].text and "/tutorial" in sent[0].text

    run_app(tmp_path, sc, client_cls=Probe, auto_intro=True)


def test_first_run_not_marked_when_the_turn_never_starts(tmp_path):
    class Refuse(FakeServerClient):
        async def send(self, body):
            if isinstance(body, s.TurnSend):
                return s.Response(id="x", ok=False, error=s.ErrorBody(code="boom", message="no"))
            return await super().send(body)

    async def sc(app, pilot, client, eng):
        await until(lambda: any(isinstance(b, s.TurnSend) for b in app.sent), pilot)
        await pilot.pause(0.2)
        assert not Onboarding(tmp_path / "exo-state").onboarded
        assert not app.tutorial

    run_app(tmp_path, sc, client_cls=Refuse, auto_intro=True)


def test_no_auto_intro_by_default_or_once_onboarded_or_resuming(tmp_path):
    async def plain(app, pilot, client, eng):
        await pilot.pause(0.2)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]
        assert not Onboarding(tmp_path / "exo-state").onboarded

    run_app(tmp_path, plain)  # tests and headless use never pass auto_intro

    Onboarding(tmp_path / "exo-state").mark_onboarded()

    async def done(app, pilot, client, eng):
        await pilot.pause(0.2)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]

    run_app(tmp_path, done, auto_intro=True)  # already onboarded


def test_slash_intro_runs_the_orientation_without_changing_tutorial(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/intro")
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
        assert not app.tutorial  # only the first run turns tips on

    run_app(tmp_path, sc)


def test_no_intro_flag_skips_and_remembers(tmp_path, monkeypatch):
    home = tmp_path / "h"
    monkeypatch.setenv("EXO_HOME", str(home))
    seen = {}

    class Fake:
        def __init__(self, client, **kw):
            seen.update(kw)

        def run(self):
            pass

    import exo_tui.conn
    monkeypatch.setattr(exo_tui.conn, "connect", lambda *a, **k: object())
    monkeypatch.setattr(app_mod, "ExoApp", Fake)
    assert exo_tui.main(["--no-intro"]) == 0
    assert seen["auto_intro"] is False and Onboarding(home).onboarded
    other = tmp_path / "h2"
    monkeypatch.setenv("EXO_HOME", str(other))
    monkeypatch.setenv("EXO_NO_INTRO", "1")
    exo_tui.main([])
    assert seen["auto_intro"] is False and not Onboarding(other).onboarded  # env skips without writing
    monkeypatch.delenv("EXO_NO_INTRO")
    exo_tui.main([])
    assert seen["auto_intro"] is True


# ---------------------------------------------------------------- hints
def test_hints_show_once_one_per_turn_and_reset(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/tutorial on")
        await until(lambda: app.tutorial, pilot)
        await send(app, pilot, "hello")
        await until(lambda: "Hello **there**, friend" in app.transcript_text() and not app.busy, pilot)
        assert len(tips(app)) == 1 and "keep typing while I work" in tips(app)[0]
        assert "(/intro)" in tips(app)[0]
        await send(app, pilot, "hello")  # same moment again: no second tip
        await until(lambda: not app.busy, pilot)
        await pilot.pause(0.1)
        assert len(tips(app)) == 1
        # two moments in one turn: only the first shows, the other waits for its next chance
        app.hint_budget = 1
        app.hint("peer_message")
        app.hint("new_thread")
        assert len(tips(app)) == 2 and "another agent session" in tips(app)[1]
        assert "new_thread" not in app.onb.seen()
        app.hint_budget = 1
        app.hint("new_thread")
        assert len(tips(app)) == 3
        # reset shows them again
        await send(app, pilot, "/tutorial reset")
        await pilot.pause(0.1)
        assert app.onb.seen() == set()
        app.hint_budget = 1
        app.hint("peer_message")
        assert len(tips(app)) == 4

    run_app(tmp_path, sc)


def test_hints_silent_when_tutorial_off_and_never_reach_the_model(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "hello")
        await until(lambda: "Hello **there**, friend" in app.transcript_text() and not app.busy, pilot)
        assert tips(app) == [] and app.onb.seen() == set()
        app.tutorial = True
        app.hint("approval")
        assert len(tips(app)) == 1
        sent = " ".join(getattr(b, "text", "") for b in app.sent)
        assert "approval gate" not in sent
        msgs = eng.store.messages(app.session)
        assert not any("approval gate" in m.text() for m in msgs)

    run_app(tmp_path, sc)


def test_event_moments_fire_their_hints(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/tutorial on")
        await until(lambda: app.tutorial, pilot)

        def fire(body):
            app.hint_budget = 1
            app.handle(s.Event.of(body, 1, session=app.session))

        fire(s.PeerMessage(msg_id="m", sender="razi", text="hi"))
        fire(s.MemorySummarized(node_id="n", level=0, start=1, end=9, before_tokens=900, after_tokens=90))
        fire(s.ModelCall(requested="a:x", route="b:y", attempts=[{"route": "a:x", "outcome": "fallback"}]))
        fire(s.TurnCompleted(turn_id="t", timing={"wall_ms": 90_000, "approval_ms": 0}))
        fire(s.TurnCompleted(turn_id="t2", timing={"wall_ms": 90_000, "approval_ms": 80_000}))  # waiting on you is not slow
        assert app.onb.seen() == {"peer_message", "memory_summarized", "route_change", "long_turn"}
        app.onb.mark_seen("first_message")  # one tip per turn: leave the budget for the approval card
        app.hint_budget = 1
        await send(app, pilot, "ask")  # a tool approval card
        await until(lambda: app.card is not None, pilot)
        assert "approval" in app.onb.seen() and any("approval gate" in t for t in tips(app))

    run_app(tmp_path, sc)


def test_seen_hints_persist_across_launches(tmp_path):
    ob = Onboarding(tmp_path / "exo-state")
    ob.mark_seen("steer")
    ob.mark_seen("steer")
    assert Onboarding(tmp_path / "exo-state").seen() == {"steer"}
    Onboarding(tmp_path / "exo-state").reset_hints()
    assert ob.seen() == set()


# ---------------------------------------------------------------- the toggle
def test_tutorial_toggle_persists(tmp_path):
    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/tutorial")
        await until(lambda: "tutorial mode is off" in app.transcript_text(), pilot)
        await send(app, pilot, "/tutorial on")
        await until(lambda: app.tutorial, pilot)
        assert Config.load(eng.home).get("tutorial.enabled") is True

    run_app(tmp_path, sc)

    async def again(app, pilot, client, eng):  # a new launch on the same home reads it back
        assert app.tutorial
        await send(app, pilot, "/tutorial off")
        await until(lambda: not app.tutorial, pilot)
        assert Config.load(eng.home).get("tutorial.enabled") is False
        await send(app, pilot, "/tutorial maybe")
        await until(lambda: "usage: /tutorial" in app.transcript_text(), pilot)

    run_app(tmp_path, again)


# ---------------------------------------------------------------- the agent line and the cache prefix
def test_system_line_is_appended_and_off_is_byte_identical(tmp_path):
    eng = make_engine(tmp_path)

    def system():
        return asyncio.run(eng.loop._system("s1", "q"))[0]

    base = system()
    assert TUTORIAL_LINE not in base
    cfg = Config.load(eng.home)
    cfg.set("tutorial.enabled", False)
    cfg.save()
    assert system() == base  # off: nothing changed
    cfg.set("tutorial.enabled", True)
    cfg.save()
    on = system()
    assert on == base + "\n\n" + TUTORIAL_LINE  # appended after the rest, so nothing before it moves
    cfg.set("tutorial.enabled", False)
    cfg.save()
    assert system() == base
    assert isinstance(eng, CoreEngine)


def test_job_events_render_as_plain_lines(tmp_path):  # D-032
    async def sc(app, pilot, client, eng):
        app.handle(s.Event.of(s.JobUpdated(job_id="j_1", state="done", command="make", exit_code=0), 1, session=app.session))
        app.handle(s.Event.of(s.JobUpdated(job_id="j_2", state="failed", command="npm test", exit_code=2,
                                           detail="notice queued"), 2, session=app.session))
        text = app.transcript_text()
        assert "job j_1 done, exit 0: make" in text and "job j_2 failed, exit 2: npm test (notice queued)" in text

    run_app(tmp_path, sc)
