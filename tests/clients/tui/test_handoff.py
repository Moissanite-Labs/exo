"""D-032: /handoff. The client gets the facts from the core (command.run), sends one agent-led turn, and the agent
writes the file through its normal write tool."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from test_tui import FAKE, FakeServerClient, make_engine, send, until

from exo.protocol import schema as s
from exo_tui.app import COMMANDS, ExoApp
from exo_tui.handoff import FACTS_LIMIT, handoff_prompt

SKILLS = Path(__file__).resolve().parents[3] / "skills"


def run_app(tmp_path, scenario, client_cls=FakeServerClient, rules=()):
    old = list(FAKE["rules"])
    FAKE["rules"][:0] = list(rules)
    try:
        eng = make_engine(tmp_path)
    finally:
        FAKE["rules"][:] = old
    client = client_cls(eng)

    async def main():
        app = ExoApp(client, seat="tester", home=str(tmp_path / "exo-state"))
        async with app.run_test(size=(120, 40)) as pilot:
            await asyncio.wait_for(app.ready.wait(), 5)
            await scenario(app, pilot, client, eng)

    asyncio.run(main())


def test_handoff_is_a_listed_command_and_in_the_intro_skill():
    assert "handoff" in COMMANDS
    assert re.search(r"/handoff\b", (SKILLS / "exo-intro" / "SKILL.md").read_text())
    skill = (SKILLS / "exo-handoff" / "SKILL.md").read_text()
    assert skill.startswith("---\nname: exo-handoff\n") and "## Next" in skill and "## Blockers" in skill
    assert any(x["name"] == "exo-handoff" for x in json.loads((SKILLS / "MANIFEST.json").read_text())["skills"])


def test_prompt_carries_the_path_and_facts_and_stays_bounded():
    p = handoff_prompt({"path": "/tmp/x.md", "seat": "a", "jobs": [{"id": "j1"}], "decisions": ["d" * 9000, "e" * 9000]})
    assert p.startswith("/handoff") and "exo-handoff skill" in p and "/tmp/x.md" in p and '"j1"' in p
    assert len(p) < FACTS_LIMIT + 1500


def test_handoff_writes_the_file_through_the_write_tool(tmp_path):
    out = tmp_path / "handoffs"
    target = out / "tester-note.md"
    rules = [{"match": "exo-handoff", "steps": [
        {"tool": "write_file", "args": {"path": str(target), "content": "# Handoff\n\n## Goal\nship it\n"}},
        "Wrote the handoff."]}]
    sent = []

    class Probe(FakeServerClient):
        async def send(self, body):
            if isinstance(body, s.TurnSend):
                sent.append(body)
            return await super().send(body)

    async def sc(app, pilot, client, eng):
        await send(app, pilot, f"/handoff {target}")
        await until(lambda: "Wrote the handoff." in app.transcript_text() and not app.busy, pilot)
        assert target.read_text().startswith("# Handoff")
        assert len(sent) == 1 and "exo-handoff skill" in sent[0].text and str(target) in sent[0].text
        facts = json.loads(sent[0].text.split("```json\n")[1].split("\n```")[0])
        assert facts["path"] == str(target) and facts["seat"] == eng.agent and "jobs" in facts and "git" in facts
        assert f"/handoff {target}" in app.transcript_text()
        assert "exo-handoff skill" not in app.transcript_text()  # the user sees "/handoff", not the brief

    run_app(tmp_path, sc, Probe, rules)


def test_handoff_default_path_is_the_handoffs_folder(tmp_path):
    async def sc(app, pilot, client, eng):
        got = await client.call(s.CommandRun(name="handoff_facts", args="", session=app.session))
        path = got["output"]["path"]
        assert path.endswith(f"/handoffs/{eng.agent}-{got['output']['date']}.md")

    run_app(tmp_path, sc)


def test_long_turn_hint_ignores_tool_time(tmp_path):
    """D-032: the tip uses the harness's share (wall minus tool time, approvals and model time), like turn.slow."""
    async def sc(app, pilot, client, eng):
        app.tutorial = True

        def fire(timing):
            app.hint_budget = 1
            app.handle(s.Event.of(s.TurnCompleted(turn_id="t", timing=timing), 1, session=app.session))

        fire({"wall_ms": 400_000, "tool_ms": 380_000, "approval_ms": 0})  # a long build
        assert "long_turn" not in app.onb.seen()
        fire({"wall_ms": 170_000, "tool_ms": 10_000, "approval_ms": 0, "model_ms": 150_000})  # a slow provider
        assert "long_turn" not in app.onb.seen()
        fire({"wall_ms": 90_000, "tool_ms": 10_000, "approval_ms": 0, "model_ms": 20_000})
        assert "long_turn" in app.onb.seen()

    run_app(tmp_path, sc)
