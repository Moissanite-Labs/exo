"""Decision adapter: wire shapes, routing with generative fallback, tighten_only, error mapping.
Hermetic: env={}, httpx MockTransport, FakeProvider. No keychain, no network."""
import asyncio
import json

import httpx
import pytest

from exo.cli import main
from exo.core.models import FakeProvider, Router
from exo.core.models.base import error_event
from exo.core.models.decision import (DecisionError, DecisionProvider, ask_choice_result, choice_question,
                                      noul_question, tighten_only)
from exo.core.types import ProviderEvent

CHOICE = {"type": "choice", "choice": "high", "probabilities": {"low": 0.17, "medium": 0.29, "high": 0.54},
          "confidence": 0.31}
NOUL = {"type": "noul", "noul": 0.98}


def reply(answer, status=200, headers=None):
    return httpx.Response(status, json={"model": "jev-1.13.0", "usage": {"input_tokens": 3, "output_tokens": 4},
                                        "answers": {"q": answer}} if status == 200 else answer, headers=headers)


def client(handler, seen=None):
    def h(req):
        if seen is not None:
            seen.append(req)
        return handler(req)
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def router(handler, roles=None, seen=None, **instances):
    return Router(roles=roles or {"decision": ["jev:jev-latest"]}, env={}, client=client(handler, seen),
                  providers={"jev": {"transport": "decision", "base_url": "https://api.typesafe.ai/v1",
                                     "roles": ["decision"]}}, instances=instances)


def test_decide_request_and_choice_shape():
    seen = []
    p = DecisionProvider("https://api.typesafe.ai/v1/", "k-secret", client=client(lambda r: reply(CHOICE), seen))
    rep = asyncio.run(p.decide("state text", {"q": choice_question("risk?", ["low", "medium", "high"])},
                               model="jev-latest"))
    req = seen[0]
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == "Bearer k-secret"
    body = json.loads(req.content)
    assert body == {"state": "state text", "model": "jev-latest", "questions": {"q": {
        "type": "choice", "instructions": "risk?", "criteria": {"low": "low", "medium": "medium", "high": "high"}}}}
    a = rep.answers["q"]
    assert a.choice == "high" and a.probabilities["medium"] == 0.29 and a.confidence == 0.31
    assert (rep.model, rep.input_tokens, rep.output_tokens) == ("jev-1.13.0", 3, 4)


def test_noul_shape():
    p = DecisionProvider("https://x/v1", client=client(lambda r: reply(NOUL)))
    assert asyncio.run(p.decide("s", {"q": noul_question("delete?")})).answers["q"].noul == 0.98
    assert noul_question("x", true="yes")["criteria"] == {"true": "yes"}


def test_question_validation():
    with pytest.raises(ValueError):
        choice_question("x", ["only"])


@pytest.mark.parametrize("status,retryable", [(401, False), (422, False), (429, True), (529, True), (500, True)])
def test_http_errors_are_mapped(status, retryable):
    p = DecisionProvider("https://x/v1", client=client(
        lambda r: httpx.Response(status, json={"error": "nope"}, headers={"retry-after": "7"})))
    with pytest.raises(DecisionError) as e:
        asyncio.run(p.decide("s", {"q": choice_question("x", ["a", "b"])}))
    assert e.value.status == status and e.value.retryable is retryable
    assert e.value.retry_after == 7.0


def test_network_error_is_retryable_and_malformed_reply_is_not_a_crash():
    def boom(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(DecisionError) as e:
        asyncio.run(DecisionProvider("https://x/v1", client=client(boom)).decide("s", {"q": choice_question("x", ["a", "b"])}))
    assert e.value.retryable and e.value.status is None
    with pytest.raises(DecisionError):
        asyncio.run(DecisionProvider("https://x/v1", client=client(lambda r: httpx.Response(200, json={"hi": 1}))).decide(
            "s", {"q": choice_question("x", ["a", "b"])}))


def test_ask_choice_routes_through_decision_role():
    r = router(lambda req: reply(CHOICE))
    res = asyncio.run(ask_choice_result(r, "s", "risk?", ["low", "medium", "high"]))
    assert res.source == "decision" and res.route == "jev:jev-latest" and res.choice == "high"
    assert res.probabilities == CHOICE["probabilities"]


def test_generative_fallback_when_decision_route_is_down():
    chat = FakeProvider(['Sure: {"choice": "medium"}'], name="chat")
    r = router(lambda req: httpx.Response(503, json={"error": "down"}),
               roles={"decision": ["jev:jev-latest"], "aux": ["chat:small"]}, chat=chat)
    res = asyncio.run(ask_choice_result(r, {"cmd": "ls"}, "risk?", {"low": "harmless", "medium": "ok", "high": "bad"}))
    assert res.source == "generative" and res.route == "chat:small" and res.confidence is None
    assert res.probabilities == {"low": 0.0, "medium": 1.0, "high": 0.0}
    prompt = chat.calls[0]["messages"][1].text()
    assert "- medium: ok" in prompt and '"cmd": "ls"' in prompt
    assert (r.cooldowns["jev"].status, r.cooldowns["jev"].strikes) == (503, 1)  # the failed route cools down once


def test_generative_only_route_choice_and_bad_answers():
    chat = FakeProvider(['{"choice": "HIGH"}', "no json here", '{"choice": "banana"}'], name="chat")
    r = Router(roles={"decision": ["chat:small"]}, env={}, instances={"chat": chat}, catalog={"providers": {}})
    res = asyncio.run(ask_choice_result(r, "s", "q", ["low", "high"]))
    assert res.probabilities == {"low": 0.0, "high": 1.0} and res.source == "generative"
    with pytest.raises(DecisionError):
        asyncio.run(ask_choice_result(r, "s", "q", ["low", "high"]))


def chat_router(chat, **roles):
    return Router(roles=roles, env={}, instances={"chat": chat}, catalog={"providers": {}})


async def _main_ends(r):
    return [e async for e in r.stream("main", [], [])][-1]


@pytest.mark.parametrize("bad", ["no json here", '{"choice": "banana"}'])
def test_malformed_generative_answer_does_not_cool_the_shared_credential(bad):
    chat = FakeProvider([bad, "main still works"], name="chat")
    r = chat_router(chat, main=["chat:big"])  # decision falls back to main: one credential for both
    with pytest.raises(DecisionError, match="JSON|not one of the options"):
        asyncio.run(ask_choice_result(r, "s", "q", ["allow", "deny"]))
    assert r.cooldowns == {} and len(chat.calls) == 1  # asked once, not again on the same credential
    assert asyncio.run(_main_ends(r)).kind == "done"  # the main role keeps working


@pytest.mark.parametrize("bad", ["no json here", '{"choice": "banana"}'])
def test_malformed_generative_answer_falls_through_to_the_next_model(bad):
    chat = FakeProvider([bad, '{"choice": "deny"}'], name="chat")
    r = chat_router(chat, aux=["chat:small"], main=["chat:big"])
    res = asyncio.run(ask_choice_result(r, "s", "q", ["allow", "deny"]))
    assert (res.choice, res.route, res.source) == ("deny", "chat:big", "generative")
    assert [c["opts"]["model"] for c in chat.calls] == ["small", "big"] and r.cooldowns == {}


@pytest.mark.parametrize("turn,key", [
    (error_event("HTTP 503: down", 503), "chat"),
    ([ProviderEvent(kind="text", text='{"cho'), error_event("HTTP 503: down", 503)], "chat"),  # after output started
    (error_event("HTTP 404: no such model", 404), "chat|small"),  # a missing model cools that model only
])
def test_one_generative_transport_failure_is_one_strike(turn, key):
    r = chat_router(FakeProvider([turn], name="chat"), decision=["chat:small"])
    with pytest.raises(DecisionError) as e:
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))
    assert list(r.cooldowns) == [key] and (r.cooldowns[key].strikes, r.cooldowns[key].status) == (1, e.value.status)


def test_bad_question_422_does_not_fall_back():
    chat = FakeProvider(['{"choice": "a"}'], name="chat")
    r = router(lambda req: httpx.Response(422, json={"detail": "bad"}),
               roles={"decision": ["jev:jev-latest"], "aux": ["chat:small"]}, chat=chat)
    with pytest.raises(DecisionError) as e:
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))
    assert e.value.status == 422 and not chat.calls


def test_no_route_error_is_clear():
    r = Router(roles={}, env={}, catalog={"providers": {}})
    with pytest.raises(DecisionError, match="no decision route"):
        asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))


def test_chat_stream_of_decision_provider_reports_nonfatal_error():
    async def go():
        return [e async for e in DecisionProvider("https://x/v1").stream([], [])]
    ev = asyncio.run(go())
    assert ev[0].kind == "error" and ev[0].retryable


def test_tighten_only():
    assert tighten_only("allow", "ask") == "ask"
    assert tighten_only("ask", "allow") == "ask"
    assert tighten_only("log", "deny") == "deny"
    assert tighten_only("deny", "log") == "deny"
    assert tighten_only("ask", "ask") == "ask"
    assert tighten_only("ask", "bogus") == "ask"
    with pytest.raises(ValueError):
        tighten_only("bogus", "ask")
    assert tighten_only("a", "c", order=("a", "b", "c")) == "c"


def test_default_catalog_routes_decision_to_jev_then_laya():
    r = Router(env={})
    assert r.providers["laya"]["base_url"] == "http://127.0.0.1:8000/v1" and r.providers["laya"]["local"]
    assert [x.ref for x in r.chain("decision")][:2] == ["jev:jev-latest", "laya:laya"]


def test_cli_decide(monkeypatch, capsys):
    r = router(lambda req: reply(CHOICE))
    monkeypatch.setattr("exo.models_cmd.build_router", lambda env=None: r)
    assert main(["decide", "risk?", "--options", "low,medium,high", "--state", "rm -rf build"]) == 0
    out = capsys.readouterr().out
    assert "high" in out and "0.540" in out and "route: jev:jev-latest source=decision" in out
    assert main(["decide", "x", "--options", "only"]) == 2
