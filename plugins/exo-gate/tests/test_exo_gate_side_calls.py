"""The gate's own calls stay out of the user's way: Exo's read-only tools classify as reads, and the decision
call never asks a main route or cools the main role's login (D-081). Hermetic: env={}, FakeProvider."""
import asyncio
import logging

import pytest

from exo import crypto, gate
from exo.core.models import FakeProvider, Router
from exo.core.models.base import error_event
from exo.ledger import Ledger
from exo_gate import hook

ASK = '{"choice": "ask"}'


class Ctx:
    def __init__(self, store, key, router):
        self.settings = {"principal_store": str(store), "key_path": str(key), "decision_model": True}
        self.router, self.hooks = router, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb):
        self.hooks.setdefault(name, []).append(cb)


@pytest.fixture
def store_key(example_store, tmp_path):
    key = tmp_path / "keys" / "exo-gate.key"
    crypto.save_key(crypto.keygen(), key)
    return example_store, key


def one_login(*turns, **roles):
    """Every role on one CLI login (no keys, the claude CLI on PATH): one credential, several models."""
    cli = FakeProvider(list(turns), name="cli")
    return cli, Router(roles=roles, env={}, instances={"cli": cli}, catalog={"providers": {}})


def gate_call(store_key, router, tool, args):
    ctx = Ctx(*store_key, router)
    hook.register(ctx)
    [cb] = ctx.hooks["pre_tool_call"]
    return asyncio.run(cb(tool_name=tool, args=args, session_id="s1", tool_call_id="c1", turn_id="t1", task_id=""))


def last_body(store):
    with Ledger(store / "ledger.sqlite") as led:
        return list(led.iter(kind="action"))[-1]["body"]


async def main_turn(router):
    return [e async for e in router.stream("main", [], [])][-1]


@pytest.mark.parametrize("tool,args", [
    ("skill", {"name": "exo-intro"}), ("memory_zoom", {"start": 3, "n": 2}), ("exo_trace", {"last": 1}),
    ("ui", {"action": "open", "kind": "table", "text": "t"}),
])
def test_exos_own_read_only_tools_classify_as_reads(tool, args):
    c = gate.classify({"tool": tool, "args": args})
    assert (c.category, c.reversibility, c.signals) == ("read", "reversible", [])
    secret = gate.classify({"tool": tool, "args": {**args, "path": "~/.ssh/id_rsa"}})
    assert secret.category == "credentials"  # a secret store in the arguments still asks


@pytest.mark.parametrize("main_says", [ASK, error_event("claude exited 1 with an incomplete response", None,
                                                          retryable=True)])
def test_with_only_the_main_login_the_gate_asks_no_model(store_key, main_says, caplog):
    """The first-run case: the claude CLI is the only route. Loading a skill is not held for a model's verdict, and
    the gate's call neither spends a main-model turn nor fails into a cooldown."""
    cli, router = one_login(main_says, "main reply", main=["cli:sonnet"])
    with caplog.at_level(logging.INFO, logger="exo_gate"):
        assert gate_call(store_key, router, "skill", {"name": "exo-intro"}) is None
        assert gate_call(store_key, router, "read_file", {"path": "notes.txt"}) is None
    assert cli.calls == [] and router.cooldowns == {}
    assert "decision_model" not in last_body(store_key[0])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]  # no route is normal, not an error


def test_the_aux_route_still_gates_and_its_failure_never_cools_the_main_login(store_key):
    cli, router = one_login(ASK, error_event("claude exited 1 with an incomplete response", None, retryable=True),
                            "main reply", aux=["cli:haiku"], main=["cli:sonnet"])
    r = gate_call(store_key, router, "read_file", {"path": "notes.txt"})
    assert r["action"] == "approve"  # an aux verdict still tightens (D-015)
    assert last_body(store_key[0])["decision_model"] == {"verdict": "ask", "route": "cli:haiku",
                                                         "source": "generative"}
    assert gate_call(store_key, router, "read_file", {"path": "notes.txt"}) is None  # aux failed: the rule stands
    assert [c["opts"]["model"] for c in cli.calls] == ["haiku", "haiku"]  # never sonnet, the main model
    assert set(router.cooldowns) == {"cli|haiku"}  # the failed model only, not the login
    assert asyncio.run(main_turn(router)).kind == "done"
