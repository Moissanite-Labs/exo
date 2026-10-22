"""D-030: /intro, first run, tutorial hints, the toggle, and the skill/help coverage test."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from test_tui import FAKE, FakeServerClient, install_intro_skill, make_engine, send, until

from exo.config import Config
from exo.core.engine import TUTORIAL_LINE, CoreEngine
from exo.core.types import Message
from exo.protocol import schema as s
from exo_tui import app as app_mod
from exo_tui import onboarding as onb_mod
from exo_tui.app import COMMANDS, ExoApp
from exo_tui.onboarding import INTRO_PROMPT, Onboarding, load_catalog

SKILL = Path(__file__).resolve().parents[3] / "skills" / "exo-intro" / "SKILL.md"
FAKE["rules"].insert(0, {"match": "exo-intro", "steps": ["Welcome to Exo."]})


def run_app(tmp_path, scenario, client_cls=FakeServerClient, intro_skill=True, **kw):
    if intro_skill:
        install_intro_skill(tmp_path)
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


def test_static_orientation_names_real_commands_from_the_skill(tmp_path):
    """The short version shown without the skill teaches the same facts: its commands exist and the skill has them."""
    text = SKILL.read_text(encoding="utf-8")
    eng = make_engine(tmp_path)
    names = set(COMMANDS) | {c["name"] for c in asyncio.run(eng._commands_list(s.CommandsList()))["commands"]}
    used = set(re.findall(r"(?<![\w/])/([a-z][a-z-]*)", onb_mod.ORIENTATION))
    assert {"help", "tutorial", "context"} <= used
    assert used <= names, used - names
    assert all(re.search(rf"/{re.escape(n)}\b", text) for n in used)
    assert onb_mod.INTRO_SKILL == SKILL.parent.name


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


def test_explicit_fake_intro_does_not_complete_real_onboarding(tmp_path):
    home = tmp_path / "exo-state"
    Probe.onboarded_at_send = []
    Probe.home = home

    async def sc(app, pilot, client, eng):
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
        assert Probe.onboarded_at_send == [False]  # not written before the turn began
        assert not Onboarding(home).onboarded
        assert "/intro" in app.transcript_text().splitlines()[0]  # first line: a plain startup prints no "new thread" line
        assert INTRO_PROMPT.split("\n")[2][:30] not in app.transcript_text()  # the user sees "/intro", not the brief
        assert not app.tutorial
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


def test_refused_intro_stays_due_for_the_next_launch(tmp_path):
    class Refuse(FakeServerClient):
        async def send(self, body):
            if isinstance(body, s.TurnSend):
                return s.Response(id="x", ok=False, error=s.ErrorBody(code="boom", message="no"))
            return await super().send(body)

    async def sc(app, pilot, client, eng):
        await until(lambda: any(isinstance(b, s.TurnSend) for b in app.sent), pilot)
        await pilot.pause(0.2)
        assert Onboarding(tmp_path / "exo-state").intro_due  # never started, so the next launch tries again

    run_app(tmp_path, sc, client_cls=Refuse, auto_intro=True)


def test_started_intro_without_a_real_reply_is_not_resent_next_launch(tmp_path):
    """A first intro that started but did not complete (here the fake model's reply) runs once, not at every launch;
    later launches offer /intro in the composer instead."""
    home = tmp_path / "exo-state"

    async def first(app, pilot, client, eng):
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
        assert not Onboarding(home).onboarded

    run_app(tmp_path, first, auto_intro=True)

    async def again(app, pilot, client, eng):
        await pilot.pause(0.3)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]
        assert app.tip is not None and app.tip.fill == "/intro"
        await send(app, pilot, "/intro")  # still there on request
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)

    run_app(tmp_path, again, auto_intro=True)


def test_the_intro_that_completes_after_an_interrupted_first_one_turns_tutorial_on(tmp_path):
    """Launch 1 starts the intro but gets no real reply (quit on an approval card, say). On launch 2 the user takes
    the composer's /intro; once it gets a real reply it is the first run, so tutorial mode turns on."""
    async def first(app, pilot, client, eng):
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
        assert not app.onb.onboarded and not app.tutorial

    run_app(tmp_path, first, auto_intro=True)

    async def again(app, pilot, client, eng):
        handle = app.handle

        def real_route(ev):  # the fake model stands in for a real provider's reply
            m = getattr(ev.body, "message", None)
            if isinstance(ev.body, s.MessageCompleted) and m.role == "assistant":
                m.meta["model"] = "openai:chosen"
            handle(ev)

        app.handle = real_route
        await pilot.pause(0.3)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]
        assert app.tip is not None and app.tip.fill == "/intro"
        await send(app, pilot, "/intro")
        await until(lambda: app.onb.onboarded, pilot)
        await until(lambda: app.tutorial, pilot)

    run_app(tmp_path, again, auto_intro=True)


def test_intro_started_marker_is_written_once_and_ends_the_automatic_intro(tmp_path):
    ob = Onboarding(tmp_path / "h")
    assert ob.intro_due
    ob.mark_intro_started()
    first = json.loads(ob.path.read_text())["intro_started"]
    ob.mark_intro_started()
    assert json.loads(ob.path.read_text())["intro_started"] == first and not ob.intro_due and not ob.onboarded
    other = Onboarding(tmp_path / "h2")
    other.mark_onboarded()
    assert not other.intro_due


def test_missing_intro_skill_shows_the_static_orientation_on_first_run(tmp_path):
    """No exo-intro skill (a home without the installer's skill pack): no model turn that is bound to fail."""
    def panels(app):
        return [w for w in app.chrono() if getattr(w, "kind", "") == "intro"]

    async def sc(app, pilot, client, eng):
        await until(lambda: panels(app) or any(isinstance(b, s.TurnSend) for b in app.sent), pilot)
        assert not [b for b in app.sent if isinstance(b, s.TurnSend)]
        panel = panels(app)[0]
        assert panel.text == onb_mod.ORIENTATION and panel.head == "Exo in brief"
        assert panel.meta == onb_mod.ORIENTATION_NOTE and "scripts/install.sh" in panel.meta
        assert app.onb.onboarded
        await until(lambda: app.tutorial, pilot)  # a first run turns tips on, as a completed intro does
        msgs = eng.store.messages(app.session)
        assert not any("exo-intro" in m.text() for m in msgs)  # nothing reached the model or the log

    run_app(tmp_path, sc, intro_skill=False, auto_intro=True)


@pytest.mark.parametrize("listing,static", [
    ([], True),
    ([{"name": "other"}], True),
    ([{"name": "exo-intro"}], False),
    ([{"name": "exo-orchestra:exo-intro"}], False),  # a plugin-registered copy loads too
    (None, False),  # the core could not answer: send the turn as before
])
def test_manual_intro_falls_back_only_when_the_core_lacks_the_skill(tmp_path, listing, static):
    """/intro by hand. The home is not onboarded yet, so the short version is its first run and turns tips on."""
    class Skills(FakeServerClient):
        async def call(self, body):
            if isinstance(body, s.SkillsList):
                if listing is None:
                    raise ConnectionError("core busy")
                return {"skills": listing}
            return await super().call(body)

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/intro")
        if static:
            await until(lambda: onb_mod.ORIENTATION in app.transcript_text(), pilot)
            assert not [b for b in app.sent if isinstance(b, s.TurnSend)]
            assert app.onb.onboarded
            await until(lambda: app.tutorial, pilot)
        else:
            await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
            assert onb_mod.ORIENTATION not in app.transcript_text()

    run_app(tmp_path, sc, client_cls=Skills)


@pytest.mark.parametrize("reason,route,text,interrupted,complete", [
    ("end", "openai:chosen", "Welcome", False, True),
    ("error", "openrouter:chosen", "partial", False, False),
    ("interrupted", "claude-cli:sonnet", "partial", True, False),
    ("end", "fake:scripted", "stub", False, False),
    ("end", "openai:chosen", "", False, False),
])
def test_intro_completion_requires_its_successful_real_reply(tmp_path, reason, route, text, interrupted, complete):
    async def sc(app, pilot, client, eng):
        app.intro_pending = "first"

        def fire(body):
            app.handle(s.Event.of(body, 1, session=app.session))

        user = Message.user(INTRO_PROMPT)
        user.meta.update(author="principal", turn_id="intro")
        fire(s.MessageCompleted(message=user))
        fire(s.TurnStarted(turn_id="intro", model="anthropic:requested"))
        assert not app.onb.onboarded
        reply = Message.assistant(text)
        reply.meta.update(turn_id="intro", model=route, interrupted=interrupted)
        fire(s.MessageCompleted(message=reply))
        assert not app.onb.onboarded  # a partial reply followed by an error cannot finish first run
        fire(s.TurnCompleted(turn_id="scheduled", stop_reason="end"))
        assert not app.onb.onboarded and app.intro_pending == "first"
        fire(s.TurnCompleted(turn_id="intro", stop_reason=reason))
        assert app.onb.onboarded is complete
        assert not app.intro_pending and not app.intro_turn
        if complete:
            await until(lambda: app.tutorial, pilot)
        else:
            assert not app.tutorial
            # Failure/cancellation leaves a retry eligible; an actual later reply can complete it.
            app.intro_pending = "manual"
            user = Message.user(INTRO_PROMPT)
            user.meta.update(author="principal", turn_id="retry")
            fire(s.MessageCompleted(message=user))
            reply = Message.assistant("Ready now")
            reply.meta.update(turn_id="retry", model="openai:chosen")
            fire(s.MessageCompleted(message=reply))
            fire(s.TurnCompleted(turn_id="intro", stop_reason="end"))
            assert not app.onb.onboarded  # an old completion cannot complete the retry
            fire(s.TurnCompleted(turn_id="retry", stop_reason="end"))
            assert app.onb.onboarded

    run_app(tmp_path, sc)


def test_cancelled_queued_intro_cannot_complete_on_another_turn(tmp_path):
    async def sc(app, pilot, client, eng):
        app.intro_pending = "first"
        app.handle(s.Event.of(s.SteerCancelled(message_id="intro", text=INTRO_PROMPT), 1, session=app.session))
        app.handle(s.Event.of(s.TurnCompleted(turn_id="other"), 2, session=app.session))
        assert not app.onb.onboarded and not app.intro_pending

    run_app(tmp_path, sc)


def test_scheduled_intro_text_does_not_claim_the_pending_principal_intro(tmp_path):
    async def sc(app, pilot, client, eng):
        app.intro_pending = "first"
        user = Message.user(INTRO_PROMPT)
        user.meta.update(author="schedule", turn_id="scheduled")
        app.handle(s.Event.of(s.MessageCompleted(message=user), 1, session=app.session))
        app.handle(s.Event.of(s.TurnStarted(turn_id="scheduled", model="openai:chosen"), 2, session=app.session))
        reply = Message.assistant("Scheduled response")
        reply.meta.update(turn_id="scheduled", model="openai:chosen")
        app.handle(s.Event.of(s.MessageCompleted(message=reply), 3, session=app.session))
        app.handle(s.Event.of(s.TurnCompleted(turn_id="scheduled"), 4, session=app.session))
        assert not app.onb.onboarded and app.intro_pending == "first" and not app.intro_turn

    run_app(tmp_path, sc)


def test_intro_send_failure_clears_pending_for_retry(tmp_path):
    class FailOnce(FakeServerClient):
        failed = False

        async def send(self, body):
            if isinstance(body, s.TurnSend) and not self.failed:
                self.failed = True
                raise ConnectionError("core disconnected")
            return await super().send(body)

    async def sc(app, pilot, client, eng):
        await app.intro("first")
        assert not app.intro_pending and not app.onb.onboarded
        assert "Could not start orientation" in app.transcript_text()
        await app.intro("first")
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.intro_pending, pilot)
        assert sum(isinstance(b, s.TurnSend) for b in app.sent) == 2
        assert not app.onb.onboarded  # the retry reached the deliberately configured fake

    run_app(tmp_path, sc, client_cls=FailOnce)


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
    Onboarding(tmp_path / "exo-state").mark_onboarded()

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/intro")
        await until(lambda: "Welcome to Exo." in app.transcript_text() and not app.busy, pilot)
        assert not app.tutorial  # only the first run turns tips on

    run_app(tmp_path, sc)


def test_static_intro_on_an_onboarded_home_leaves_tutorial_alone(tmp_path):
    Onboarding(tmp_path / "exo-state").mark_onboarded()

    async def sc(app, pilot, client, eng):
        await send(app, pilot, "/intro")
        await until(lambda: onb_mod.ORIENTATION in app.transcript_text(), pilot)
        await pilot.pause(0.2)
        assert not app.tutorial

    run_app(tmp_path, sc, intro_skill=False)


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
