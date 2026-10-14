"""Cost-aware model tiers for subagents: defaults, override, fallback, the no-upgrade cap, escalation. Fake models only."""

from __future__ import annotations

import asyncio

from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider, tool_turn
from exo.core.models.router import Router
from exo.wire import Receipt, decode, render

TIER_ROLES = {"main": ["fake:main"], "aux": ["fake:main"], "fast": ["fake:fast"], "standard": ["fake:std"],
              "deep": ["fake:deep"]}


class ByModel:
    """Fake provider that records which model served each call. A child whose goal contains FAIL@<model>
    loops on tool calls (so it runs out of rounds) while served by that model, and finishes otherwise."""

    name = "fake"

    def __init__(self, parent_script):
        self.parent_script, self.served = parent_script, []

    async def stream(self, messages, tools, **opts):
        model = opts.get("model", "")
        users = [m for m in messages if m.role == "user"]
        text = users[-1].text() if users else ""
        step = sum(1 for m in messages[messages.index(users[-1]) + 1:] if m.role == "assistant") if users else 0
        if "PARENT" in text:
            spec = self.parent_script[min(step, len(self.parent_script) - 1)]
        else:
            self.served.append(model)
            spec = tool_turn("glob", {"pattern": "*.none"}) if f"FAIL@{model}" in text else "child ok"
        async for ev in FakeProvider([spec]).stream(messages, tools):
            yield ev


def make(tmp_path, parent_script, roles=None, **cfg):
    eng = CoreEngine(tmp_path / "home", {"roles": roles or TIER_ROLES}, cwd=str(tmp_path), env={},
                     delegation={"max_turns": 2, **cfg})
    eng.plugins.hooks.register("pre_tool_call", lambda **kw: None, "test-allow")
    fake = ByModel(parent_script)
    eng.router.instances["fake"] = fake
    eng.loop.provider = eng.router.provider("main")
    return eng, fake


def run(eng, text="PARENT go"):
    async def go():
        info = await eng.loop.create_session(title="parent")
        return await eng.loop.send(info.id, text)
    return asyncio.run(go())


def receipts(eng) -> list[Receipt]:
    return [decode(t["receipt"]) for t in eng.agents.list() if t["receipt"]]


def briefs(eng):
    return {t["task_id"]: decode(t["brief"]) for t in eng.agents.list()}


def delegate(*tasks):
    return [tool_turn("delegate", {"tasks": list(tasks)}), "parent done"]


def test_role_defaults_and_config_override(tmp_path):
    eng, fake = make(tmp_path, delegate(*[{"goal": f"g {r}", "role": r} for r in ("scout", "verifier", "builder",
                                                                                 "critic")]))
    run(eng)
    got = {r.role: (r.tier, r.model) for r in receipts(eng)}
    assert got == {"scout": ("fast", "fake:fast"), "verifier": ("standard", "fake:std"),
                   "builder": ("standard", "fake:std"), "critic": ("deep", "fake:deep")}
    eng2, _ = make(tmp_path / "b", delegate({"goal": "g", "role": "scout"}), role_tiers={"scout": "standard"})
    run(eng2)
    assert receipts(eng2)[0].tier == "standard"


def test_explicit_tier_overrides_the_role_default(tmp_path):
    eng, fake = make(tmp_path, delegate({"goal": "g", "role": "scout", "tier": "deep"}))
    run(eng)
    r = receipts(eng)[0]
    assert (r.tier, r.model) == ("deep", "fake:deep") and fake.served == ["deep"]
    eng2, _ = make(tmp_path / "b", delegate({"goal": "g", "tier": "huge"}))
    run(eng2)
    assert not receipts(eng2)  # a bad tier is a tool error, no child runs


def test_unconfigured_tier_falls_back_to_main_and_fast_to_standard(tmp_path):
    only_main = {"main": ["fake:main"], "aux": ["fake:main"]}
    eng, fake = make(tmp_path, delegate({"goal": "a", "role": "scout"}, {"goal": "b", "role": "critic"}),
                     roles=only_main)
    run(eng)
    assert sorted(r.model for r in receipts(eng)) == ["fake:main", "fake:main"]
    assert sorted(r.tier for r in receipts(eng)) == ["deep", "fast"]
    std_only = {**only_main, "standard": ["fake:std"]}
    eng2, _ = make(tmp_path / "b", delegate({"goal": "a", "role": "scout"}), roles=std_only)
    run(eng2)
    assert receipts(eng2)[0].model == "fake:std"  # fast -> standard
    assert Router(roles=std_only, catalog={}).chain("fast")[0].ref == "fake:std"


def test_catalog_defaults_are_the_anthropic_tiers():
    r = Router()
    assert [r.roles[t] for t in ("fast", "standard", "deep")] == [
        ["anthropic:claude-haiku-5-5"], ["anthropic:claude-sonnet-5-5"], ["anthropic:claude-opus-5-5"]]
    assert [x.ref for x in r.chain("fast")][:2] == ["anthropic:claude-haiku-5-5", "anthropic:claude-sonnet-5-5"]
    assert r.tier_of("anthropic:claude-sonnet-5-5") == "standard"
    # no keys at all: nothing resolves, so callers use the parent's model
    assert Router(env={}).tier_route("fast") is None


def test_a_child_never_runs_above_the_parent(tmp_path):
    roles = {**TIER_ROLES, "main": ["fake:std"]}  # the main model is the standard tier
    eng, _ = make(tmp_path, delegate({"goal": "g", "role": "critic"}, {"goal": "h", "role": "scout", "tier": "deep"}),
                  roles=roles)
    run(eng)
    assert sorted(r.tier for r in receipts(eng)) == ["standard"] * 2
    eng2, _ = make(tmp_path / "b", delegate({"goal": "g", "role": "critic"}), roles=roles, allow_upgrade=True)
    run(eng2)
    assert receipts(eng2)[0].tier == "deep"
    # an unknown main model counts as deep: nothing is capped
    eng3, _ = make(tmp_path / "c", delegate({"goal": "g", "role": "critic"}))
    run(eng3)
    assert receipts(eng3)[0].tier == "deep"


def test_failed_child_escalates_once(tmp_path):
    eng, fake = make(tmp_path, delegate({"goal": "g FAIL@fast", "role": "scout"}))
    run(eng)
    rs = receipts(eng)
    assert [(r.tier, r.status, r.escalated) for r in rs] == [("fast", "failed", 0), ("standard", "done", 1)]
    assert fake.served[0] == "fast" and "std" in fake.served
    assert "retried one tier up" in render(rs[1])
    # fails at every tier: exactly one retry, no chain of retries
    eng2, fake2 = make(tmp_path / "b", delegate({"goal": "g FAIL@fast FAIL@std", "role": "scout"}))
    run(eng2)
    assert [(r.tier, r.status) for r in receipts(eng2)] == [("fast", "failed"), ("standard", "failed")]


def test_escalation_respects_the_cap_the_switch_and_same_model(tmp_path):
    goal = {"goal": "g FAIL@fast", "role": "scout"}
    roles = {**TIER_ROLES, "main": ["fake:fast"], "standard": ["fake:std"]}  # main is the fast tier
    eng, _ = make(tmp_path, delegate(goal), roles=roles)
    run(eng)
    assert [r.tier for r in receipts(eng)] == ["fast"]  # standard would be above the parent
    eng2, _ = make(tmp_path / "b", delegate(goal), escalate_on_fail=False)
    run(eng2)
    assert [r.tier for r in receipts(eng2)] == ["fast"]
    only_main = {"main": ["fake:main"], "aux": ["fake:main"]}
    eng3, _ = make(tmp_path / "c", delegate({"goal": "g FAIL@main", "role": "scout"}), roles=only_main)
    run(eng3)
    assert len(receipts(eng3)) == 1  # same model, so a retry would only waste money


def test_tier_is_in_the_brief_the_receipt_and_the_task_event(tmp_path):
    eng, _ = make(tmp_path, delegate({"goal": "g", "role": "scout"}))
    run(eng)
    assert [b.tier for b in briefs(eng).values()] == ["fast"]
    events = [e.body for e in eng.hub.since(0) if type(e.body).__name__ == "TaskUpdated"]
    done = events[-1]
    assert done.status == "done" and "fast tier, fake:fast" in done.detail
    assert decode(done.packet).model == "fake:fast"
    assert "Model tier: fast." in events[0].detail


def test_old_packets_still_decode():
    from exo.wire import encode
    line = encode(Receipt(id="r1", thread="t", brief="k1"))
    assert decode(line).tier == "" and decode(line).model == "" and "Model:" not in render(line)
