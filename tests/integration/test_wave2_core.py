"""Wave 2 core through the engine: skills tool and prompt, MCP tools, seats, config, hook and channel wiring."""

import asyncio
import sys
from pathlib import Path

import pytest

from exo.core.engine import CoreEngine
from exo.core.plugins import Manifest, PluginManager
from exo.core.seats import parse
from exo.protocol import schema as s
from exo.server.app import Dispatcher

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_echo_server.py"
MODELS = {"fake": {"rules": [{"match": "mcp", "steps": [{"tool": "mcp__e__add", "args": {"a": 1, "b": 2}}, "done"]},
                             {"match": "skill", "steps": [{"tool": "skill", "args": {"name": "demo"}}, "done"]},
                             {"match": "danger", "steps": [{"tool": "bash", "args": {"command": "echo asked"}}, "done"]}]},
          "roles": {"main": ["fake:x"], "aux": ["fake:x"]}}


def _eng(tmp_path, seat=None, register=None, **kw):
    pm = PluginManager()
    pm.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    if register:
        pm._run_register(Manifest(name="t"), "dir", register)
    sk = tmp_path / "skills" / "demo"
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text("---\nname: demo\ndescription: a demo skill\n---\nDEMO BODY")
    return CoreEngine(tmp_path / "home", MODELS, plugins=pm, cwd=str(tmp_path), seat=seat,
                      skill_folders=[tmp_path / "skills"], env={}, **kw)


async def _run(d, eng, text):
    sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
    await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
    while eng._tasks:
        await asyncio.sleep(0.01)
    return [ev.body.result.text() for ev in eng.hub.since(0) if isinstance(ev.body, s.ToolCompleted)]


def test_skill_tool_and_prompt_index(tmp_path):
    eng = _eng(tmp_path)
    assert "demo: a demo skill" in eng.loop.system_prompt and "DEMO BODY" not in eng.loop.system_prompt

    async def main():
        d = Dispatcher(eng)
        assert "DEMO BODY" in (await _run(d, eng, "skill"))[0]
        assert (await d.run(s.Command.of(s.SkillsList()))).result["skills"][0]["name"] == "demo"
        assert "skill" in {t["name"] for t in (await d.run(s.Command.of(s.ToolsList()))).result["tools"]}

    asyncio.run(main())


def test_mcp_tools_through_engine_and_clean_close(tmp_path):
    pytest.importorskip("mcp")
    eng = _eng(tmp_path, mcp={"e": {"command": sys.executable, "args": [str(FIXTURE)]}})

    async def main():
        d = Dispatcher(eng)
        assert (await _run(d, eng, "mcp"))[0] == "3"
        assert (await d.run(s.Command.of(s.SeatList()))).result["mcp"]["e"]["ok"]
        await eng.close()
        assert not any(n.startswith("mcp__") for n in eng.registry.names())

    asyncio.run(main())


def test_seat_toolsets_soul_tier_and_seat_list(tmp_path):
    seat = parse({"seat": {"name": "scout"}, "tools": {"toolsets": ["fs"]}, "models": {"tier": "fast", "roles": {
        "fast": ["fake:y"], "main": ["fake:x"]}}}, soul="I am scout.")
    eng = _eng(tmp_path, seat=seat)
    names = {sp.name for sp in eng._specs()}
    assert "skill" in names and "read_file" in names and "bash" not in names
    assert "I am scout." in eng.loop.system_prompt and eng.loop.model == "fake:y"
    (tmp_path / "home" / "seats").mkdir(parents=True)
    (tmp_path / "home" / "seats" / "scout.toml").write_text('[seat]\nname = "scout"\n')
    res = asyncio.run(Dispatcher(eng).run(s.Command.of(s.SeatList()))).result
    assert res["active"] == "scout" and res["seats"][0]["name"] == "scout"


def test_config_get_set_refuse_secrets(tmp_path):
    eng = _eng(tmp_path)

    async def main():
        d = Dispatcher(eng, caller="principal:tui")
        ok = await d.run(s.Command.of(s.ConfigSet(key="cwd", value="/tmp/w")))
        assert ok.ok
        assert (await d.run(s.Command.of(s.ConfigGet(key="cwd")))).result["value"] == "/tmp/w"
        bad = await d.run(s.Command.of(s.ConfigSet(key="models.providers.p.api_key", value="sk-" + "x" * 30)))
        assert not bad.ok and bad.error.code == "bad_config"
        (tmp_path / "home" / "config.json").write_text('{"x": {"api_key": "hunter2"}, "y": 1}')
        got = (await d.run(s.Command.of(s.ConfigGet()))).result["config"]
        assert got["x"]["api_key"] == "***" and got["y"] == 1
        assert not (await d.run(s.Command.of(s.ConfigGet(key="nothing.here")))).ok

    asyncio.run(main())


def test_hook_gets_risk_and_approval_client_id(tmp_path):
    seen = []

    def register(ctx):
        def pre(tool_name="", args=None, risk=None, **kw):
            seen.append(("pre", tool_name, risk))
            return {"action": "approve", "message": "ok?", "rule_key": "r"} if tool_name == "bash" else None

        ctx.register_hook("pre_tool_call", pre)
        ctx.register_hook("post_approval_response", lambda **kw: seen.append(("decided", kw["decided_by"])))
        ctx.register_tool("hi", "demo", {}, handler=lambda a, **kw: "x", risk="high")

    eng = _eng(tmp_path, register=register)
    assert eng.registry.get("hi").spec.risk == "high"

    async def main():
        d = Dispatcher(eng)
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        await d.run(s.Command.of(s.TurnSend(session=sid, text="danger")))
        for _ in range(300):
            reqs = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalRequested)]
            if reqs:
                break
            await asyncio.sleep(0.01)
        r = await d.run(s.Command.of(s.ApprovalRespond(request_id=reqs[0].request_id, decision="allow", client="phone-7")),
                        "principal")
        assert r.ok
        while eng._tasks:
            await asyncio.sleep(0.01)
        resolved = [e.body for e in eng.hub.since(0) if isinstance(e.body, s.ApprovalResolved)]
        assert resolved[0].by == "principal:phone-7"  # D-039: the connection says who; the body only labels it

    asyncio.run(main())
    assert [x for x in seen if x[:2] == ("pre", "bash")][0][2] in ("low", "medium", "high")
    assert ("decided", "principal:phone-7") in seen


def test_channel_lifecycle(tmp_path):
    log = []

    class Chan:
        def __init__(self, link, **cfg):
            log.append(("make", link, cfg))

        async def start(self):
            log.append("start")

        def stop(self):
            log.append("stop")

    def boom(link, **cfg):
        raise RuntimeError("bad token")

    def register(ctx):
        ctx.register_channel("c", Chan)
        ctx.register_channel("b", boom)
        ctx.register_channel("unused", Chan)

    eng = _eng(tmp_path, register=register, channels={"c": {"x": 1}, "b": {}, "ghost": {}})

    async def main():
        res = await eng.start_channels("LINK")
        assert res["c"] is None and "bad token" in res["b"] and "no plugin" in res["ghost"]
        assert (await Dispatcher(eng).run(s.Command.of(s.SeatList()))).result["channels"]["c"] is True
        await eng.close()

    asyncio.run(main())
    assert log == [("make", "LINK", {"x": 1}), "start", "stop"]


def test_context_and_memory_hooks_get_session_transcript_and_compaction(tmp_path):
    seen = {"select": [], "turn": [], "compacted": [], "checkpoint": [], "pre": []}

    class Ctx:
        name = "c"

        def select_context(self, request_messages, *, session_id="", **kw):
            seen["select"].append(session_id)
            return None

        def on_turn_complete(self, messages, usage=None, session_id=""):
            seen["turn"].append((session_id, [m["role"] for m in messages][-2:]))

        def on_compacted(self, session, node):
            seen["compacted"].append((session, node.level, node.status))

        def update_from_response(self, usage): ...

        def should_compress(self, prompt_tokens=None):
            return False

        def compress(self, messages, **kw):
            return messages

    class Mem:
        name = "m"

        def is_available(self): return True
        def initialize(self, session_id, **kw): ...
        def system_prompt_block(self): return ""
        def prefetch(self, query, *, session_id=""): return ""
        def sync_turn(self, u, a, *, session_id="", **kw): ...
        def get_tool_schemas(self): return []
        def on_pre_compress(self, messages):
            seen["pre"].append(len(messages))
            return "KEEP THIS"

        def checkpoint(self, *, session_id="", messages=None, reason="turn"):
            seen["checkpoint"].append((reason, session_id))

    def register(ctx):
        ctx.register_context_engine(Ctx())
        ctx.register_memory_provider(Mem())

    eng = _eng(tmp_path, register=register)

    async def main():
        d = Dispatcher(eng)
        eng.loop.context.tree.min_compact_tokens = 0  # tiny test spans still compact
        sid = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        for _ in range(12):
            await d.run(s.Command.of(s.TurnSend(session=sid, text="hello")))
            while eng._tasks:
                await asyncio.sleep(0.005)
        tree = eng.loop.context.tree
        await asyncio.gather(*[t for ts in tree._tasks.values() for t in ts.values()])
        return sid

    sid = asyncio.run(main())
    assert set(seen["select"]) == {sid} and seen["turn"][-1] == (sid, ["user", "assistant"])
    assert ("turn", sid) in seen["checkpoint"] and ("pre_compress", sid) in seen["checkpoint"]
    assert seen["pre"] and seen["compacted"][0] == (sid, 0, "done")


def test_a_private_thread_is_never_shown_to_the_context_plugin(tmp_path):
    """D-028: the context plugin (handoffs, pins) never sees a private thread: no session start, selection or
    turn transcript for it, and the tree's hooks name the other thread even while the private one is talking."""
    seen = []

    class Ctx:
        name = "c"

        def on_session_start(self, session_id, **kw):
            seen.append(("start", session_id, ""))

        def select_context(self, request_messages, *, session_id="", **kw):
            seen.append(("select", session_id, " ".join(m["content"] for m in request_messages)))

        def on_turn_complete(self, messages, usage=None, session_id=""):
            seen.append(("turn", session_id, " ".join(str(m.get("content")) for m in messages)))

        def update_from_response(self, usage):
            seen.append(("usage", "", ""))

        def on_compacted(self, session, node):
            seen.append(("compacted", session, node.text))

        def should_compress(self, prompt_tokens=None):
            return False

        def compress(self, messages, **kw):
            return messages

    eng = _eng(tmp_path, register=lambda ctx: ctx.register_context_engine(Ctx()))

    async def main():
        d = Dispatcher(eng)
        tree = eng.loop.context.tree
        tree.min_compact_tokens, tree.leaf_size, tree.tail_messages = 0, 2, 2
        a = (await d.run(s.Command.of(s.SessionCreate()))).result["session"]["id"]
        p = (await d.run(s.Command.of(s.SessionCreate(kind="private")))).result["session"]["id"]
        for sid, text in ((a, "hello"), (a, "again"), (p, "zebracorn plan"), (p, "more zebracorn")):
            await d.run(s.Command.of(s.TurnSend(session=sid, text=text)))
            while eng._tasks:
                await asyncio.sleep(0.005)
        await asyncio.gather(*[t for ts in tree._tasks.values() for t in ts.values()])
        await eng.close()
        return a, p

    a, p = asyncio.run(main())
    assert {k for k, _, _ in seen} >= {"start", "select", "turn", "compacted"}
    assert sum(k == "usage" for k, _, _ in seen) == 2  # the two turns of the normal thread only
    assert {sid for k, sid, _ in seen if k != "usage"} == {a}
    assert not any("zebracorn" in text for _, _, text in seen)


def test_compressor_forwards_session_id_and_summary_marker():
    from exo.core.context import DefaultCompressor, VIEW_HEADER, is_compaction_summary_message
    from exo.core.types import Message, ProviderEvent

    got = {}

    class P:
        async def stream(self, messages, tools, **opts):
            got.update(opts)
            yield ProviderEvent(kind="text", text="summary")
            yield ProviderEvent(kind="done")

    assert asyncio.run(DefaultCompressor(P()).summarize("x", session="s_1")) == "summary"
    assert got["session_id"] == "s_1"
    assert is_compaction_summary_message(Message.user(VIEW_HEADER + " x"))
    assert is_compaction_summary_message({"role": "user", "content": [{"text": "[CONTEXT COMPACTION] y"}]})
    assert not is_compaction_summary_message({"role": "user", "content": "hi"})


def test_aux_sync_and_ctx_router_and_root_per_manager(tmp_path):
    from exo.core.models.router import aux_sync
    from exo.dispatcher import register_root

    holder = {}
    eng = _eng(tmp_path, register=lambda ctx: holder.update(ctx=ctx, root=register_root(ctx)))
    assert holder["ctx"].router is eng.router
    assert isinstance(aux_sync(eng.router, "hello there"), str)
    assert holder["root"] is True and register_root(holder["ctx"]) is False
    pm2 = PluginManager()
    got = []
    pm2._run_register(Manifest(name="t2"), "dir", lambda ctx: got.append(register_root(ctx)))
    assert got == [True]  # a second manager gets its own /exo


def test_handoff_uses_real_capture_api():
    from exo import handoff
    msgs = [{"role": "user", "content": "From now on, always run the tests before you commit."},
            {"role": "assistant", "content": "ok"}]
    rulings, corrections = handoff.extract_rulings_and_corrections(msgs)
    assert rulings and rulings[0].index == 0


def test_once_verdict_is_never_a_standing_allow(tmp_path):
    from exo.core.tools.registry import ToolContext
    from exo.core.types import ToolCall, ToolSpec

    def register(ctx):
        ctx.register_hook("pre_tool_call", lambda tool_name="", **kw: {
            "action": "approve", "message": "ok?", "rule_key": "k", "once": tool_name == "once"})

    eng = _eng(tmp_path, register=register)
    asked = []

    async def fake_ask(session, summary, call=None, risk="medium", decided_by=None, options=None, opened=None,
                       approvers=None, binding=""):
        asked.append(options)
        return "allow_always"

    eng.approvals.ask = fake_ask

    async def main():
        spec = ToolSpec(name="x", description="", parameters={})
        for name in ("once", "once", "plain", "plain"):
            await eng._pre_hook(ToolCall(id="1", name=name, arguments={}), spec, ToolContext())

    asyncio.run(main())
    assert asked == [["allow", "deny"], ["allow", "deny"], None]


def test_soul_is_reread_at_turn_start_when_the_file_changes(tmp_path):
    """f_e3dd66fa: an edit to SOUL.md reaches the next turn without a restart; turn.started carries the soul hash."""
    from exo.core import seats

    d = tmp_path / "home" / "seats" / "scout"
    d.mkdir(parents=True)
    (d / "seat.toml").write_text('[seat]\nname = "scout"\nsoul = "SOUL.md"\n')
    (d / "SOUL.md").write_text("Soul one.")
    eng = _eng(tmp_path, seat=seats.load(tmp_path / "home", "scout"))
    assert "Soul one." in eng.loop.system_prompt and "demo: a demo skill" in eng.loop.system_prompt

    async def turn():
        dsp = Dispatcher(eng)
        await _run(dsp, eng, "hi")
        return [e.body for e in eng.hub.since(0) if isinstance(e.body, s.TurnStarted)][-1]

    first = asyncio.run(turn())
    assert first.soul and "Soul one." in eng.loop.system_prompt
    (d / "SOUL.md").write_text("Soul two, longer.")
    second = asyncio.run(turn())
    assert "Soul two, longer." in eng.loop.system_prompt and "Soul one." not in eng.loop.system_prompt
    assert "demo: a demo skill" in eng.loop.system_prompt  # the rest of the prompt is kept
    assert second.soul and second.soul != first.soul
    (d / "SOUL.md").unlink()  # a removed file empties the soul
    assert asyncio.run(turn()).soul == "" and "Soul two" not in eng.loop.system_prompt
