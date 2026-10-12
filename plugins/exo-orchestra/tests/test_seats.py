"""Seat format and native skills, against the real core (fake model, in-process server, no network)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

from exo.core.engine import CoreEngine  # noqa: E402
from exo.core.plugins import PluginManager  # noqa: E402
from exo.protocol import schema as s  # noqa: E402
from exo.server.app import Dispatcher  # noqa: E402
from exo_orchestra import orchestra  # noqa: E402

SKILLS = PLUGIN.parents[1] / "skills"
GOOD = """
schema = 1
[seat]
name = "forge"
[models]
tier = "standard"
main = "fake:x"
[models.tiers]
fast = "fake:small"
[tools]
toolsets = ["fs"]
[skills]
load = ["clean-writing"]
"""


def test_good_seat_validates_and_maps_to_core_inputs():
    seat = orchestra.parse_seat(GOOD)
    assert orchestra.validate_seat(seat, SKILLS) == []
    assert orchestra.session_body(seat) == {"title": "forge", "seat": "forge", "model": "fake:x"}
    assert orchestra.models_overlay(seat) == {"main": ["fake:x"], "fast": ["fake:small"]}


@pytest.mark.parametrize("edit,needle", [
    ("schema = 1", "schema must be 1"),
    ('name = "forge"', "seat.name is required"),
    ('tier = "standard"', "models.tier must be"),
    ('main = "fake:x"', "models.main must be"),
    ('toolsets = ["fs"]', "tools.toolsets must be"),
    ('load = ["clean-writing"]', "no skill"),
])
def test_bad_seats_are_reported(edit, needle):
    bad = {"schema = 1": "schema = 2", 'name = "forge"': 'name = "a b"', 'tier = "standard"': 'tier = "huge"',
           'main = "fake:x"': 'main = "nocolon"', 'toolsets = ["fs"]': 'toolsets = "fs"',
           'load = ["clean-writing"]': 'load = ["ghost"]'}[edit]
    errs = orchestra.validate_seat(orchestra.parse_seat(GOOD.replace(edit, bad)), SKILLS)
    assert any(needle in e for e in errs), errs


def test_unknown_keys_and_bad_toml():
    errs = orchestra.validate_seat(orchestra.parse_seat(GOOD + "\n[extras]\nx = 1\n[tools2]\n"))
    assert any("unknown table or key: extras" in e for e in errs)
    errs = orchestra.validate_seat(orchestra.parse_seat(GOOD.replace("[tools]", "[tools]\nshell = true")))
    assert any("unknown key: tools.shell" in e for e in errs)
    with pytest.raises(ValueError):
        orchestra.parse_seat("schema = ")


def test_check_command_reports_problems(tmp_path):
    f = tmp_path / "seat.toml"
    f.write_text(GOOD.replace('tier = "standard"', 'tier = "huge"'))
    out = orchestra.command_handler()(f"check {f}")
    assert out.startswith("Seat has problems") and "models.tier" in out
    assert orchestra.command_handler()("check /no/such/file").startswith("Cannot read seat")


def test_plugin_loads_in_the_core_and_skills_register_natively():
    pm = PluginManager()
    recs = {r.manifest.name: r for r in pm.load_all(dirs=[PLUGIN.parent], entry_points=False)}
    assert recs["exo-orchestra"].ok, recs["exo-orchestra"].error
    assert "orchestra" in pm.commands
    names = {k.split(":", 1)[1] for k in pm.skills if k.startswith("exo-orchestra:")}
    assert {"control-plane", "clean-writing", "sym-protocol"} <= names


def test_seat_drives_a_session_and_command_runs_over_the_protocol(tmp_path):
    seat = orchestra.parse_seat(orchestra.render_seat_text("builder", {"seat_name": "forge"}))
    pm = PluginManager()
    pm.load_all(dirs=[PLUGIN.parent], entry_points=False)
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]},
                                         "fake": {"reply": "built"}}, cwd=str(tmp_path), env={}, plugins=pm)
    disp = Dispatcher(eng)

    async def call(body):
        r = await disp.run(s.Command.of(body))
        assert r.ok, r.error
        return r.result

    async def main():
        body = orchestra.session_body(seat)
        sid = (await call(s.SessionCreate(**body)))["session"]["id"]
        assert [x["id"] for x in (await call(s.SessionList(seat="forge")))["sessions"]] == [sid]
        out = (await call(s.CommandRun(name="orchestra", args="seat builder --seat forge")))["output"]
        assert orchestra.parse_seat(out)["seat"]["name"] == "forge"
        skills = (await call(s.SkillsList()))["skills"]
        assert any(k["name"] == "exo-orchestra:control-plane" for k in skills)

    asyncio.run(main())
