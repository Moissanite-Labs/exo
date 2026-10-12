"""Decision adapter: wire shapes, routing with generative fallback, tighten_only, error mapping.
Hermetic: env={}, httpx MockTransport, FakeProvider. No keychain, no network."""
import asyncio
import json

import httpx
import pytest

from exo.cli import main
from exo.core.models import FakeProvider, Router
from exo.core.models.decision import (DecisionError, DecisionProvider, ask_choice, ask_choice_result, ask_noul,
                                      ask_score, choice_question, noul_question, score_question, tighten_only)

CHOICE = {"type": "choice", "choice": "high", "probabilities": {"low": 0.17, "medium": 0.29, "high": 0.54},
          "confidence": 0.31}
SCORE = {"type": "score", "score": 1.43, "probabilities": {"0": 0.08, "1": 0.41, "2": 0.51},
         "legend": {"0": "harmless", "1": "recoverable", "2": "destructive"}, "confidence": 0.15}
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


def test_score_and_noul_shapes():
    p = DecisionProvider("https://x/v1", client=client(lambda r: reply(SCORE)))
    a = asyncio.run(p.decide("s", {"q": score_question("bad?", ["harmless", "recoverable", "destructive"])})).answers["q"]
    assert a.score == 1.43 and a.legend["2"] == "destructive" and a.probabilities["2"] == 0.51
    p = DecisionProvider("https://x/v1", client=client(lambda r: reply(NOUL)))
    assert asyncio.run(p.decide("s", {"q": noul_question("delete?")})).answers["q"].noul == 0.98
    assert noul_question("x", true="yes")["criteria"] == {"true": "yes"}


def test_question_validation():
    with pytest.raises(ValueError):
        choice_question("x", ["only"])
    with pytest.raises(ValueError):
        score_question("x", ["a"] * 11)


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
    assert asyncio.run(ask_choice(r, "s", "risk?", ["low", "medium", "high"])) == CHOICE["probabilities"]


def test_ask_score_and_noul():
    r = router(lambda req: reply(SCORE))
    res = asyncio.run(ask_score(r, "s", "bad?", ["a", "b", "c"]))
    assert res.score == 1.43 and res.source == "decision"
    r = router(lambda req: reply(NOUL))
    assert asyncio.run(ask_noul(r, "s", "delete?")).probability == 0.98


def test_generative_fallback_when_decision_route_is_down():
    chat = FakeProvider(['Sure: {"choice": "medium"}'], name="chat")
    r = router(lambda req: httpx.Response(503, json={"error": "down"}),
               roles={"decision": ["jev:jev-latest"], "aux": ["chat:small"]}, chat=chat)
    res = asyncio.run(ask_choice_result(r, {"cmd": "ls"}, "risk?", {"low": "harmless", "medium": "ok", "high": "bad"}))
    assert res.source == "generative" and res.route == "chat:small" and res.confidence is None
    assert res.probabilities == {"low": 0.0, "medium": 1.0, "high": 0.0}
    prompt = chat.calls[0]["messages"][1].text()
    assert "- medium: ok" in prompt and '"cmd": "ls"' in prompt
    assert r.cooldowns["jev"].status == 503  # the failed route cools down


def test_generative_only_route_choice_score_and_bad_answers():
    chat = FakeProvider(['{"choice": "HIGH"}', '{"score": 2}', "no json here", '{"choice": "banana"}'], name="chat")
    r = Router(roles={"decision": ["chat:small"]}, env={}, instances={"chat": chat}, catalog={"providers": {}})
    assert asyncio.run(ask_choice(r, "s", "q", ["low", "high"])) == {"low": 0.0, "high": 1.0}
    sc = asyncio.run(ask_score(r, "s", "q", ["a", "b", "c"]))
    assert sc.score == 2.0 and sc.source == "generative" and sc.probabilities["2"] == 1.0
    with pytest.raises(DecisionError):
        asyncio.run(ask_choice(r, "s", "q", ["low", "high"]))


def test_bad_question_422_does_not_fall_back():
    chat = FakeProvider(['{"choice": "a"}'], name="chat")
    r = router(lambda req: httpx.Response(422, json={"detail": "bad"}),
               roles={"decision": ["jev:jev-latest"], "aux": ["chat:small"]}, chat=chat)
    with pytest.raises(DecisionError) as e:
        asyncio.run(ask_choice(r, "s", "q", ["a", "b"]))
    assert e.value.status == 422 and not chat.calls


def test_no_route_error_is_clear():
    r = Router(roles={}, env={}, catalog={"providers": {}})
    with pytest.raises(DecisionError, match="no decision route"):
        asyncio.run(ask_choice(r, "s", "q", ["a", "b"]))


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
    import exo.decide_cmd as dc
    r = router(lambda req: reply(CHOICE))
    monkeypatch.setattr("exo.models_cmd.build_router", lambda env=None: r)
    assert main(["decide", "risk?", "--options", "low,medium,high", "--state", "rm -rf build"]) == 0
    out = capsys.readouterr().out
    assert "high" in out and "0.540" in out and "route: jev:jev-latest source=decision" in out
    assert main(["decide", "x", "--options", "only"]) == 2
