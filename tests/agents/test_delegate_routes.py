"""Named delegation routes (D02): a child runs on a configured ``delegation.routes.<name>`` instead of
a tier. A route pins the model exactly: no tier chain, no fallback, and no escalation when the child
fails. Config validation and tier checks happen before any task row exists."""

from __future__ import annotations

import asyncio

import pytest
from test_tiers import TIER_ROLES, briefs, delegate, make, receipts, run

ROUTES = {"backend": "model_map", "routes": {"economy": "fake:fast"}}


def _eng(tmp_path, script, *, roles=None, routes=None, loop_model=None):
    """An engine whose main model sits at the deep tier and whose delegation config maps 'economy' -> fast."""
    roles = roles if roles is not None else {**TIER_ROLES, "main": ["fake:deep"]}
    routes = routes if routes is not None else ROUTES
    eng, fake = make(tmp_path, script, roles=roles, routes=routes)
    eng.loop.model = loop_model if loop_model is not None else roles["main"][0]
    return eng, fake


def _run_parent(eng, text="PARENT go"):
    async def go():
        info = await eng.loop.create_session(title="parent")
        await eng.loop.send(info.id, text)
        return info.id
    return asyncio.run(go())


def test_route_runs_the_child_on_the_named_model(tmp_path):
    eng, fake = _eng(tmp_path, delegate({"goal": "g", "role": "builder", "route": "economy"}))
    run(eng)
    r = receipts(eng)[0]
    assert r.route == "economy"
    assert r.tier == "fast"          # validated against the router's tiers
    assert r.model == "fake:fast"    # pinned: the named route's exact model
    assert r.status == "done"
    assert fake.served == ["fast"]
    b = next(iter(briefs(eng).values()))
    assert b.route == "economy" and b.tier == "fast"


def test_failed_route_child_fails_once_without_escalation(tmp_path):
    eng, fake = _eng(tmp_path, delegate({"goal": "g FAIL@fast", "role": "builder", "route": "economy"}))
    run(eng)
    rs = receipts(eng)
    assert len(rs) == 1  # one attempt only: a route pins the model, so there is no retry one tier up
    r = rs[0]
    assert r.route == "economy"
    assert r.tier == "fast"
    assert r.model == "fake:fast"
    assert r.status == "failed"
    assert r.escalated == 0
    assert fake.served, "expected at least one child call"
    # the child only ever ran on the named route's model: no retry one tier up
    assert set(fake.served) <= {"fast"}, f"child left the named route: {fake.served}"


@pytest.mark.parametrize("task,tweaks,expected", [
    # tier and route together: ambiguous, rejected before any child runs
    ({"goal": "g", "role": "builder", "route": "economy", "tier": "fast"}, {}, "not both"),
    # an unconfigured route name
    ({"goal": "g", "role": "builder", "route": "nope"}, {}, "unknown route 'nope'"),
    # a route whose model is not ranked on any tier cannot be verified safe
    ({"goal": "g", "role": "builder", "route": "odd"},
     {"routes": {"backend": "model_map", "routes": {"odd": "fake:none"}}},
     "is not on any model tier"),
    # a route above the parent's own tier would violate the no-upgrade cap
    ({"goal": "g", "role": "builder", "route": "deeproute"},
     {"roles": {**TIER_ROLES, "main": ["fake:fast"]},
      "routes": {"backend": "model_map", "routes": {"deeproute": "fake:deep"}},
      "loop_model": "fake:fast"},
     "above your own tier fast"),
    # the parent itself runs on an unknown tier: nothing can be verified
    ({"goal": "g", "role": "builder", "route": "economy"},
     {"loop_model": "fake:err"}, "the parent's own tier is unknown"),
    # remote (seat) delegation cannot carry a named route
    ({"goal": "g", "role": "builder", "route": "economy", "seat": "worker"}, {},
     "cannot carry a named route"),
])
def test_invalid_routes_reject_before_any_task(tmp_path, task, tweaks, expected):
    eng, _ = _eng(tmp_path, delegate(task), **tweaks)
    sid = _run_parent(eng)
    assert receipts(eng) == []
    assert not eng.agents.list()  # not even a brief/task row
    errs = [t.text() for m in eng.store.messages(sid) for t in m.tool_results if t.is_error]
    assert any(expected in e for e in errs), f"no tool error mentioning {expected!r}: {errs}"
