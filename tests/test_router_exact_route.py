"""Exact-route provider prerequisite (D02): one configured route, no fallback, shared router state.

The route is pinned before anything is streamed; an invalid ref fails at construction of the
provider (or at the first ``stream``) with ``ValueError``. A pinned call keeps the ordinary
credential rotation, cooldowns, circuit breaker and spend guard of the Router it came from.
"""

import json
import sqlite3

import httpx
import pytest

from exo.core.models import FakeProvider, Router
from exo.core.models.fake import rate_limited
from exo.core.models.router import SOLE_TTL
from exo.core.types import Message, ProviderEvent

from tests.core.models.conftest import collect


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


CATALOG = {
    "providers": {"limited": {"base_url": "http://127.0.0.1:1", "roles": ["decision"]}},
    "models": {},
    "roles": {},
}


def make(roles, clock=None, providers=None, **fakes):
    return Router(roles=roles, catalog=CATALOG, providers=providers or {}, instances=fakes,
                  env={}, clock=clock or Clock())


def run(provider):
    return collect(provider.stream([Message.user("hi")], []))


def test_unknown_provider_fails_before_stream():
    r = make({"main": ["p:m"]}, p=FakeProvider(["ok"], name="p"))
    with pytest.raises(ValueError, match="unknown provider"):
        r.provider_for("nope:m")
    with pytest.raises(ValueError, match="unknown provider"):
        collect(r.stream("delegate", [Message.user("hi")], [], _exact="nope:m"))


def test_non_delegate_role_provider_fails_before_stream():
    # The catalog table limits this provider to the decision role; it may not serve a delegate.
    r = make({"main": ["p:m"]}, providers={"limited": {"base_url": "http://127.0.0.1:1"}})
    with pytest.raises(ValueError, match="cannot serve role"):
        r.provider_for("limited:m")
    with pytest.raises(ValueError, match="cannot serve role"):
        collect(r.stream("delegate", [Message.user("hi")], [], _exact="limited:m"))


@pytest.mark.parametrize("ref", ["", "p", "p:", ":m", "p:m extra"])
def test_bad_ref_shape_fails_before_stream(ref):
    r = make({"main": ["p:m"]}, p=FakeProvider(["ok"], name="p"))
    with pytest.raises(ValueError):
        r.provider_for(ref)


def test_exact_route_used_even_when_main_is_another():
    exact, main = FakeProvider(["from exact"], name="p"), FakeProvider(["from main"], name="b")
    r = make({"main": ["b:m"]}, p=exact, b=main)
    events = run(r.provider_for("p:m"))
    assert events[-1].kind == "done" and events[-1].message.text() == "from exact"
    assert events[-1].message.meta["model"] == "p:m"
    assert len(exact.calls) == 1 and not main.calls  # the main role's route was never asked


def test_retryable_error_does_not_reach_another_provider():
    exact = FakeProvider([ProviderEvent(kind="error", error="down", status=503, retryable=True)], name="p")
    other = FakeProvider(["must not be used"], name="b")
    clock = Clock()
    r = make({"main": ["b:m"], "delegate": ["p:m", "b:m"]}, clock, p=exact, b=other)
    events = run(r.provider_for("p:m"))
    assert events[-1].kind == "error" and events[-1].retryable
    assert len(exact.calls) == 1 and not other.calls  # no fallback, in this role or another
    # Only the pinned route was tried: 'b:m' is a delegate route but was not part of this call.
    assert [a["route"] for a in events[-1].attempts] == ["p:m"]


def test_exact_route_keeps_cooldown_and_breaker_state_on_the_shared_router():
    clock = Clock()
    p = FakeProvider([rate_limited(retry_after=30), "recovered"], name="p")
    r = make({"main": ["p:m"]}, clock, p=p)
    prov = r.provider_for("p:m")
    assert prov.router is r
    assert run(prov)[-1].kind == "error"  # sole credential rate limited: the call fails, no second route
    assert r.cooldowns["p"].until == clock.t + 30  # the shared Router recorded the provider cooldown
    assert run(prov)[-1].kind == "error" and len(p.calls) == 1  # benched: the same provider is not asked again
    clock.t = r.cooldowns["p"].until + 1
    assert run(prov)[-1].message.text() == "recovered"
    assert "p" not in r.cooldowns


def test_exact_route_and_default_role_share_one_cooldown():
    clock = Clock()
    p = FakeProvider([rate_limited(), "after cooldown"], name="p")
    r = make({"main": ["p:m"]}, clock, p=p)
    assert run(r.provider("main"))[-1].kind == "error"
    assert r.cooldowns["p"].until > clock.t
    assert run(r.provider_for("p:m"))[-1].kind == "error"  # the default role's cooldown applies here too
    assert len(p.calls) == 1


def test_default_provider_and_role_stream_unchanged():
    p, b = FakeProvider([rate_limited()], name="p"), FakeProvider(["from backup"], name="b")
    r = make({"main": ["p:m", "b:m"]}, p=p, b=b)
    events = collect(r.stream("main", [Message.user("hi")], []))
    assert events[-1].message.text() == "from backup"  # ordinary fallback still happens
    assert r.provider("main").exact is None and r.provider("main").name == "router:main"
    assert r.provider_for("p:m").name == "router:delegate:p:m"


def test_exact_route_records_model_call_attempts():
    p = FakeProvider(["ok"], name="p")
    r = make({"main": ["p:m"]}, p=p)
    events = run(r.provider_for("p:m"))
    assert events[-1].attempts == [{"route": "p:m", "outcome": "ok", "error": "", "ms": events[-1].attempts[0]["ms"]}]


# --- injected SpendJournal: a pinned call still reserves and settles --------------------------


@pytest.fixture(autouse=True)
def synthetic_native_contract(monkeypatch):
    # This fake-price model exists only in test code. Production config cannot add native bounds.
    from exo.model_spend import billing
    monkeypatch.setattr(billing, 'NATIVE_BOUNDS', {
        **billing.NATIVE_BOUNDS,
        ('openai', 'https://api.openai.com/v1', 'm'): (1000, 1000),
    })


def spend_router(tmp_path, handler, clock):
    from exo.model_spend import SpendJournal
    routes = {"test:m": dict(contract="text-tokens-v1", base_url="https://api.openai.com/v1", model="m",
                             transport="openai", input_bound=1000, max_output=100,
                             input=1, output=10, cache_read=1, cache_write=1)}
    cfg = dict(journal=str(tmp_path / "spend.sqlite"), journal_id=SpendJournal.provision(tmp_path / "spend.sqlite", "alice"),
               principal="alice", daily_usd="1", monthly_usd="1", routes=routes)
    return Router(roles={"main": ["test:m"], "delegate": ["other:m"]}, catalog={},
                  providers={"test": {"transport": "openai", "base_url": "https://api.openai.com/v1"}},
                  env={}, clock=clock, spend=SpendJournal(cfg),
                  client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ok_response(input=100, output=10, cost=99):
    data = {'choices': [{'delta': {'content': 'hi'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': input, 'completion_tokens': output, 'cost': cost}}
    return httpx.Response(200, text='data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n')


def test_exact_route_with_injected_journal_reserves_and_settles(tmp_path):
    r = spend_router(tmp_path, lambda req: ok_response(), Clock())
    r.roles["delegate"] = ["test:m"]  # route 'test:m' may serve the pinned call's role
    prov = r.provider_for("test:m")
    events = run(prov)
    assert events[-1].kind == "done" and events[-1].message.meta["model"] == "test:m"
    reserved, actual = sqlite3.connect(tmp_path / "spend.sqlite").execute(
        "SELECT reserved, actual FROM calls").fetchall()[0]
    assert actual == 200_000 and reserved == 2_000_000  # $0.0002 settled under the 100-token reservation


def test_exact_route_keeps_breaker_state_on_the_shared_router():
    clock = Clock()
    outages = [ProviderEvent(kind="error", error="HTTP 503: unavailable", status=503, retryable=True)] * 2
    p = FakeProvider(outages + ["recovered"], name="p")
    r = make({"main": ["p:m"]}, clock, p=p)
    prov = r.provider_for("p:m")
    run(prov)  # first 503: error, the sole credential cools for SOLE_TTL
    clock.t += SOLE_TTL + 1
    events = run(prov)  # second failure opens the route's breaker
    assert events[-1].kind == "error" and r.breakers["p:m"].level == 1
    assert run(prov)[-1].kind == "error" and len(p.calls) == 2  # benched, not asked again
    clock.t += 600
    assert run(prov)[-1].message.text() == "recovered"  # sole pinned ref: it is the one trial call
    assert not r.breakers and not r._forced_probes  # recovered; no fallback probe was ever spawned
