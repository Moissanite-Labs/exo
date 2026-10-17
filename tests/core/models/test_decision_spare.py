"""A decision call that spares another role (the gate spares main, D-081): it never asks that role's routes and a
failure cools only the model that failed, so a shared login (the claude CLI) keeps serving the main role.
Hermetic: env={}, FakeProvider. No keychain, no network."""
import asyncio

import pytest

from exo.core.models import FakeProvider, Router
from exo.core.models.base import error_event
from exo.core.models.decision import DecisionError, ask_choice_result


def _one_login(*turns, **roles):
    """One chat credential behind every role, as with the claude CLI's own login."""
    chat = FakeProvider(list(turns), name="chat")
    return chat, Router(roles=roles, env={}, instances={"chat": chat}, catalog={"providers": {}})


async def _main_last(r):
    return [e async for e in r.stream("main", [], [])][-1]


def test_spare_main_never_calls_a_main_route():
    chat, r = _one_login('{"choice": "b"}', main=["chat:big"])
    with pytest.raises(DecisionError, match="no decision route"):
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"], spare="main"))
    assert chat.calls == [] and r.cooldowns == {}
    res = asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))  # other callers still fall back to main
    assert (res.choice, res.route) == ("b", "chat:big")


def test_spare_main_uses_aux_but_not_a_main_route_listed_under_aux():
    chat, r = _one_login('{"choice": "b"}', aux=["chat:small"], main=["chat:big"])
    res = asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"], spare="main"))
    assert (res.choice, res.route, res.source) == ("b", "chat:small", "generative")
    chat, r = _one_login('{"choice": "b"}', aux=["chat:big"], main=["chat:big"])
    with pytest.raises(DecisionError):
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"], spare="main"))
    assert chat.calls == []


@pytest.mark.parametrize("turn", [
    error_event("claude exited 1 with an incomplete response", None, retryable=True),  # the CLI process failed
    error_event("HTTP 429: slow down", 429, retry_after=30),
    error_event("HTTP 401: bad key", 401),
    "I would say b, to be safe.",  # not the requested JSON
])
def test_spare_main_failure_on_a_shared_credential_never_cools_main(turn):
    chat, r = _one_login(turn, "main reply", aux=["chat:small"], main=["chat:big"])
    with pytest.raises(DecisionError):
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"], spare="main"))
    assert set(r.cooldowns) <= {"chat|small"}  # only the model that failed, never the credential
    assert asyncio.run(_main_last(r)).kind == "done"


def test_only_a_spared_call_keeps_the_shared_credential_warm():
    err = error_event("claude exited 1", None, retryable=True)
    _, r = _one_login(err, err, aux=["chat:small"], main=["chat:big"])
    with pytest.raises(DecisionError):
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"], spare="main"))
    assert "chat" not in r.cooldowns
    with pytest.raises(DecisionError):  # exo decide and the baseline judge: main stays the last fallback
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))
    assert "chat" in r.cooldowns
