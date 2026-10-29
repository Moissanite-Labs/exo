"""The terminal command displays verified status, not a Python dictionary repr."""
import asyncio
from types import SimpleNamespace

from exo.protocol import schema as s
from exo_tui.app import ExoApp
from exo_tui.pet import summary


def test_pet_command_formats_read_only_core_reply_without_starting_turn():
    out = {"schema_version": 1, "personal": {"xp": 320.5, "level": 6}, "learned_count": 2,
           "verification": {"state": "verified"}, "stale": False}
    calls, notes = [], []
    async def post(request):
        calls.append(request)
        return SimpleNamespace(ok=True, result={"output": out})
    app = SimpleNamespace(busy=False, commands=[{"name": "pet"}], post=post, note=lambda text: notes.append(text), session="s")
    asyncio.run(ExoApp.slash(app, "pet", "status"))
    assert len(calls) == 1 and isinstance(calls[0], s.CommandRun) and calls[0].name == "pet"
    assert "Personal level 6 · 320.5 XP banked" in notes[0]
    assert "community hatch pending" in notes[0] and "2 recorded promotions" in notes[0]
    assert "schema_version" not in notes[0] and "{'" not in notes[0]


def test_pet_summary_preserves_unknown_and_frozen_growth():
    unknown = {"schema_version": 1, "personal": {"xp": None, "level": None}, "verification": {"state": "unverified"}}
    assert "Personal growth unavailable" in summary(unknown)
    assert "0 XP" not in summary(unknown)
    unknown.update(personal={"xp": 10, "level": 1}, stale=True)
    assert "10 XP banked" in summary(unknown) and "frozen" in summary(unknown)
    assert "unavailable" in summary({})
