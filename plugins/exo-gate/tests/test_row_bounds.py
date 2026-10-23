"""#485: a long enough line must not cost the ledger its row, nor the decision model its input.

The ledger authority refuses a document over 1 MiB. On the head before this change,
`gh pr comment 5 -R pub/<400,000 characters> -b x` built a row of about 1.2 MB, the append
failed, and the decision was never recorded. A route can likewise refuse the model's input
at that size, which leaves the rule's ask untightened. Long fields are cut with a marker
that carries the full length and the value's sha256, in the row and in the model input.
"""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from exo import crypto, gate
from exo.ledger import Ledger
from exo_gate import hook


class MockCtx:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.hooks = {}
        self.router = None

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


@pytest.fixture
def keyed_store(example_store, tmp_path):
    key = tmp_path / "keys" / "exo-gate.key"
    crypto.save_key(crypto.keygen(), key)
    return example_store, key


def make_gate(store, key=None, **extra):
    ctx = MockCtx({"principal_store": str(store) if store else "", "key_path": str(key) if key else "", **extra})
    g = hook.register(ctx)
    return g, ctx


def events(store):
    with Ledger(store / "ledger.sqlite") as led:
        return list(led.iter(kind="action"))


def call(ctx, tool, args, **kw):
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1",
                          task_id="", **kw))


LONG = "a" * 400_000
LINE = f"gh pr comment 5 -R pub/{LONG} -b x"


def test_a_line_with_a_long_target_still_records_its_row(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    call(ctx, "terminal", {"command": LINE})
    [ev] = events(store)                     # before the fix the append failed and no row was written
    raw = json.dumps(ev["body"], sort_keys=True, default=str)
    assert len(raw) < 512 * 1024             # the authority's limit is 1 MiB; the row stays well under it
    full = gate.classify(("terminal", {"command": LINE})).target
    target = ev["body"]["classification"]["target"]
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
    assert target.startswith("pub/aaaa") and f"sha256 {digest}" in target
    assert f"{len(full)} chars in full" in target


def test_bounded_cuts_each_long_field_and_keeps_the_rest():
    long_reason = "r" * 5000
    value = {"reason": long_reason, "short": "keep me",
             "violations": ["v" * 3000, "fine"], "reserved": [("github_post", "t" * 3000)],
             "n": 7, "ok": True, "none": None}
    out = hook._bounded(value)
    digest = hashlib.sha256(long_reason.encode("utf-8")).hexdigest()
    assert out["reason"].startswith("r" * 100) and f"sha256 {digest}" in out["reason"]
    assert len(out["reason"]) < 2200
    assert out["short"] == "keep me" and out["violations"][1] == "fine"
    assert out["reserved"][0][0] == "github_post" and "sha256" in out["reserved"][0][1]
    assert out["n"] == 7 and out["ok"] is True and out["none"] is None
    assert value["reason"] == long_reason    # the input itself is not changed


def test_the_decision_models_input_is_bounded_the_same_way(keyed_store, monkeypatch):
    store, key = keyed_store
    g, ctx = make_gate(store, key)
    ctx.router = object()
    seen = {}

    async def fake_ask(router, state, question, outcomes, spare=None):
        seen["state"] = state
        return SimpleNamespace(probabilities={"ask": 1.0}, route="test-route", source="test")

    monkeypatch.setattr("exo.core.models.decision.ask_choice_result", fake_ask)
    c = gate.classify(("terminal", {"command": LINE}))
    d = gate.Decision(outcome="ask", reason="r" * 5000)
    out = asyncio.run(g._verdict(d, c, "", "terminal"))
    assert out is not None and out[0] == "ask"
    raw = json.dumps(seen["state"], sort_keys=True, default=str)
    assert len(raw) < 512 * 1024
    digest = hashlib.sha256(c.target.encode("utf-8")).hexdigest()
    assert f"sha256 {digest}" in seen["state"]["classification"]["target"]
    assert "sha256" in seen["state"]["reason"]


def test_a_short_row_passes_through_unchanged(keyed_store):
    store, key = keyed_store
    _, ctx = make_gate(store, key)
    assert call(ctx, "read_file", {"path": "x"}) is None
    [ev] = events(store)
    assert ev["body"]["classification"]["category"] == "read"
    assert "cut:" not in json.dumps(ev["body"], default=str)
