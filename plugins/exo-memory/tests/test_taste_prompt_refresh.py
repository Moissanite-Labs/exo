"""Taste controls reach the next model call, while ordinary traffic keeps the cached prompt."""
import asyncio
from datetime import date, timedelta

import pytest

from exo import taste
from exo.baseline import command, layers
from exo.baseline.pack import HouseItem, HousePack
from exo.baseline.prompt import TastePrompt
from exo.core.loop import Loop
from exo.core.models.fake import FakeProvider
from exo.core.sessions import SessionStore


CLAIM = "Write answers in short paragraphs."


@pytest.fixture
def house(monkeypatch):
    item = HouseItem("h-style", "method", "preference", "mind", CLAIM, taste.signature(CLAIM, None))
    pack = HousePack(None, "1.0.0", {}, (item,), "", True)
    monkeypatch.setattr(layers, "load_pack", lambda: pack)
    return pack


def test_taste_off_reaches_next_model_call(make_provider, tmp_path, house):
    mem = make_provider()
    model = FakeProvider(["Ready.", "Noted."])
    sessions = SessionStore(tmp_path / "sessions.sqlite")
    loop = Loop(sessions, model, memory=mem)
    st = mem._state()

    async def run():
        session = await loop.create_session(cwd=str(tmp_path / "project"))
        assert (await loop.send(session.id, "Hello.")).stop_reason == "end"
        command.run(st.rt.store, "off h-style", principal=True, open_ledger=lambda: st.rt.ledger, pack=house)
        assert (await loop.send(session.id, "Continue.")).stop_reason == "end"

    try:
        asyncio.run(run())
        assert CLAIM in model.calls[0]["messages"][0].text()
        assert CLAIM not in model.calls[1]["messages"][0].text()
        inputs = [e for e in st.rt.ledger.iter(kind="utterance") if e["body"].get("role") == "user"]
        assert all(e["body"]["project"] == str(tmp_path / "project") for e in inputs)
        assert all(e["author"] == "pat" for e in inputs)
    finally:
        sessions.close()


def test_cache_ignores_ordinary_events_and_refreshes_on_day_and_policy(make_provider, monkeypatch, house):
    from exo.baseline import prompt

    built = []
    original = prompt.taste_block

    def render(*args, **kwargs):
        built.append(kwargs.get("scope"))
        return original(*args, **kwargs)

    monkeypatch.setattr(prompt, "taste_block", render)
    day = [date(2026, 10, 25)]
    monkeypatch.setattr(TastePrompt, "_day", lambda self: day[0])
    mem = make_provider()
    stable = mem.system_prompt_block()  # consumes initialization's session_start
    built.clear()
    mem.on_turn_start(1, "Hello.", author="principal")
    mem.sync_turn("Hello.", "Hello again.")
    assert mem.system_prompt_block() == stable
    assert mem.system_prompt_block() == stable
    assert not built
    day[0] += timedelta(days=1)
    assert mem.system_prompt_block() == stable
    assert built == [None]
    path = mem._state().rt.store.root / "policies" / "consolidate.yaml"
    path.write_text(path.read_text() + "\n# policy changed\n")
    assert mem.system_prompt_block() == stable
    assert built == [None, None]


def test_project_scope_does_not_leak_between_sessions(make_provider, tmp_path, house):
    from exo.consolidate import Proposal

    mem = make_provider()
    mem.system_prompt_block()  # cache the initial house item before another component changes taste
    led = mem._state().rt.ledger
    claim = "Use bullet lists in this project."
    source = led.append("utterance", {"role": "user", "text": claim}, author="pat")
    proposal = Proposal("p-project", "taste_shift", "create", "taste:h-style", claim, claim, None, "", "",
                        "mind", "preference", "Project style", "high", 0.8,
                        sorted(taste.signature(claim, None)),
                        [{"id": source["id"], "ts": source["ts"], "strength": 0.8}], {}, False,
                        "taste-shift", source["ts"])
    led.append("belief_proposed", {**proposal.body(), "overrides": "h-style", "scope": "project:" + str(tmp_path / "a")})
    led.append("belief_accepted", {"proposal_id": proposal.id, "node_id": None, "rule": "taste_shift"})
    sessions = SessionStore(tmp_path / "sessions.sqlite")
    loop = Loop(sessions, FakeProvider(), memory=mem)

    async def run():
        a = await loop.create_session(cwd=str(tmp_path / "a"))
        b = await loop.create_session(cwd=str(tmp_path / "b"))
        a_prompt, b_prompt = await asyncio.gather(loop.base_system(a.id), loop.base_system(b.id))
        assert claim in a_prompt and CLAIM not in a_prompt
        assert claim not in b_prompt and CLAIM in b_prompt
        assert await loop.base_system(a.id) == a_prompt

    try:
        asyncio.run(run())
    finally:
        sessions.close()


def test_zero_budget_never_builds_taste_cache(make_provider, house):
    mem = make_provider(taste_budget=0)
    assert "# Taste" not in mem.system_prompt_block()
    assert mem._state().taste_prompt is None
