"""Skills loader, MCP client, seat files and config secrets (hermetic: env={}, local stdio fixture only)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from exo.config import Config, ConfigError, redact
from exo.core import seats
from exo.core.skills import SkillIndex, parse_frontmatter, register_skill_tool
from exo.core.tools.registry import Registry
from exo.core.types import ToolCall

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"


def _skill(root: Path, name: str, desc: str, body: str = "Do the thing.") -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n")


def test_frontmatter_forms():
    fm = parse_frontmatter('---\nname: "a b"\ndescription: >\n  one\n  two\ntags: [x, y]\nmetadata:\n  k: v\n---\nbody')
    assert fm == {"name": "a b", "description": "one two", "tags": ["x", "y"]}
    assert parse_frontmatter("no header") == {}


def test_index_priority_progressive_disclosure_and_plugin_skills(tmp_path):
    seat_dir, pack = tmp_path / "seat", tmp_path / "pack"
    _skill(seat_dir, "alpha", "seat version", "SEAT BODY")
    _skill(pack, "alpha", "pack version")
    _skill(pack, "beta", "second")
    plug = tmp_path / "p.md"
    plug.write_text("---\ndescription: from plugin\n---\nPLUGIN BODY")

    class E:
        name, path, description, frontmatter, plugin = "x:gamma", plug, "plug desc", {}, "x"

    idx = SkillIndex.discover([seat_dir, tmp_path / "missing", pack], [E()])
    assert set(idx.skills) == {"alpha", "beta", "x:gamma"}
    prompt = idx.prompt()
    assert "alpha: seat version" in prompt and "SEAT BODY" not in prompt  # index only, no bodies
    reg = Registry()
    register_skill_tool(reg, idx)
    out = reg.call_sync(ToolCall(id="1", name="skill", arguments={"name": "alpha"}))
    assert "SEAT BODY" in out.text() and not out.is_error
    assert "PLUGIN BODY" in reg.call_sync(ToolCall(id="2", name="skill", arguments={"name": "x:gamma"})).text()
    bad = reg.call_sync(ToolCall(id="3", name="skill", arguments={"name": "nope"}))
    assert bad.is_error and "alpha" in bad.text()
    assert set(SkillIndex.discover([pack], only=["beta"]).skills) == {"beta"}
    assert SkillIndex().prompt() == ""


def test_a_plugin_entry_for_a_folder_skill_is_listed_once(tmp_path):
    home = tmp_path / "home" / "skills"
    _skill(home, "exo-intro", "orientation")

    class E:
        name, path, description, frontmatter, plugin = ("exo-orchestra:exo-intro", home / "exo-intro" / "SKILL.md",
                                                        "orientation", {}, "exo-orchestra")

    idx = SkillIndex.discover([home], [E()])
    assert set(idx.skills) == {"exo-intro"}
    assert idx.prompt().count("orientation") == 1
    only_plugin = SkillIndex.discover([], [E()])  # a source checkout: the pack comes only through the plugin
    assert "Do the thing." in only_plugin.load("exo-intro")


def test_exo_pack_loads():
    from exo.core.skills import pack_dir
    idx = SkillIndex.discover([pack_dir({})])
    assert "exo-bus" in idx.skills and idx.skills["exo-bus"].description


def test_seat_formats_and_list(tmp_path):
    d = tmp_path / "seats"
    (d / "orch").mkdir(parents=True)
    (d / "orch" / "seat.toml").write_text(
        '[seat]\nname = "orch"\nrole = "architect"\nsoul = "SOUL.md"\n[principal]\nid = "ada"\n[models]\ntier = "deep"\n'
        '[tools]\ntoolsets = ["fs", "mcp"]\n[skills]\npack = "/x"\nload = ["a"]\n[mcp.files]\ncommand = "srv"\n'
        '[channels.bus]\nseat = "orch"\n[plugins]\nenabled = ["exo-bus"]\n')
    (d / "orch" / "SOUL.md").write_text("You are the orchestrator.")
    (d / "flat.json").write_text(json.dumps({"principal": "bo", "toolsets": ["fs"], "channels": ["bus"],
                                             "mcp": {"servers": {"s": {"url": "http://h/mcp"}}}}))
    (d / "broken.toml").write_text("= nope")
    st = seats.load(tmp_path, "orch")
    assert (st.principal, st.tier, st.toolsets, st.skill_load, st.plugins) == ("ada", "deep", ["fs", "mcp"], ["a"], ["exo-bus"])
    assert st.soul.startswith("You are") and st.mcp["files"]["command"] == "srv" and st.channels == {"bus": {"seat": "orch"}}
    flat = seats.load(tmp_path, "flat")
    assert flat.principal == "bo" and flat.channels == {"bus": {}} and "s" in flat.mcp
    rows = {r["name"]: r for r in seats.list_seats(tmp_path)}
    assert rows["orch"]["role"] == "architect" and "error" in rows["broken"] and "soul" not in rows["orch"]
    with pytest.raises(seats.SeatError):
        seats.load(tmp_path, "ghost")
    with pytest.raises(seats.SeatError):
        seats.load(tmp_path, "../etc")


def test_config_refuses_and_redacts_secrets(tmp_path):
    cfg = Config(home=tmp_path)
    for key, val in [("plugin_config.x.api_key", "abc"), ("mcp.s.headers", {"Authorization": "Bearer abc"}),
                     ("mcp.s.env", {"A": "plain"}), ("note", "sk-" + "a" * 30)]:
        with pytest.raises(ConfigError):
            cfg.set(key, val)
    cfg.set("mcp.s.headers", {"Authorization": "Bearer ${TOK}"})
    cfg.set("models.providers.p.key_env", "MY_KEY")
    cfg.set("authors", "me")
    assert redact({"api_key": "zzz", "key_env": "K", "n": {"token": "t"}}) == {"api_key": "***", "key_env": "K", "n": {"token": "***"}}


async def _mcp_roundtrip(extra: dict | None = None):
    from exo.core.mcp_client import McpManager
    reg = Registry()
    m = McpManager({"e": {"command": sys.executable, "args": [str(FIXTURE)], **(extra or {})},
                    "bad": {"command": "/nonexistent/xyz"}, "off": {"command": "x", "enabled": False}},
                   env={}, connect_timeout=20)
    status = await m.start(reg)
    return reg, m, status


def test_mcp_stdio_tools_risk_errors_and_shutdown():
    pytest.importorskip("mcp")

    async def main():
        reg, m, status = await _mcp_roundtrip({"tool_risks": {"boom": "high"}, "exclude": ["echo"]})
        assert status["e"]["ok"] and not status["bad"]["ok"] and "off" not in status
        assert reg.names() == ["mcp__e__add", "mcp__e__boom"]
        assert reg.get("mcp__e__add").spec.risk == "medium" and reg.get("mcp__e__boom").spec.risk == "high"
        assert reg.get("mcp__e__add").spec.toolset == "mcp:e"
        ok = await reg.call(ToolCall(id="1", name="mcp__e__add", arguments={"a": 2, "b": 3}))
        assert ok.text() == "5" and not ok.is_error
        assert (await reg.call(ToolCall(id="2", name="mcp__e__boom", arguments={}))).is_error
        assert (await reg.call(ToolCall(id="3", name="mcp__e__add", arguments={"a": "x"}))).is_error
        await m.close()
        assert reg.names() == []

    asyncio.run(main())


def test_mcp_missing_sdk_is_reported_not_raised(monkeypatch):
    from exo.core.mcp_client import McpManager
    monkeypatch.setitem(sys.modules, "mcp", None)
    status = asyncio.run(McpManager({"e": {"command": "x"}}, env={}).start(Registry()))
    assert not status["e"]["ok"] and "not installed" in status["e"]["error"]


def test_seat_key_is_generated_once_with_mode_0600(tmp_path):
    import os
    import stat

    from exo import crypto
    from exo.core import seats

    st = seats.parse({"name": "scout"})
    path = st.key_path(tmp_path)
    assert path == tmp_path / "keys" / "scout.key" and not path.exists()
    seats.ensure_key(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and path.with_suffix(".pub").is_file()
    seed = path.read_text()
    seats.ensure_key(path)  # never overwrites
    assert path.read_text() == seed and crypto.load_key(path)
    assert seats.parse({"seat": {"name": "a", "key": "~/k/a.key"}}).key == "~/k/a.key"
