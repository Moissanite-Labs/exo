import asyncio

import pytest

from exo.core.models import FakeProvider, ModelError, Router, aux
from exo.core.models.fake import rate_limited, tool_turn
from exo.core.models.router import SOLE_TTL, Route, cooldown_until
from exo.core.types import Message, Provider, ProviderEvent

from .conftest import Recorder, collect, fixture_bytes


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(roles, clock=None, **fakes):
    return Router(roles=roles, catalog={"providers": {}, "models": {}, "roles": {}},
                  instances=fakes, env={}, clock=clock or Clock())


def run(router, role="main"):
    return collect(router.stream(role, [Message.user("hi")], []))


def test_fallback_on_429_then_cooldown_skips_primary():
    primary = FakeProvider([rate_limited(), "should not be used"], name="p")
    backup = FakeProvider(["from backup", "again"], name="b")
    clock = Clock()
    r = make({"main": ["p:big", "b:small"]}, clock, p=primary, b=backup)
    events = run(r)
    assert [e.kind for e in events][-1] == "done"
    assert events[-1].message.text() == "from backup" and events[-1].message.meta["model"] == "b:small"
    assert backup.calls[0]["opts"]["model"] == "small"
    assert r.cooldowns["p"].until == clock.t + SOLE_TTL
    run(r)
    assert len(primary.calls) == 1 and len(backup.calls) == 2
    clock.t += SOLE_TTL + 1
    assert run(r)[-1].message.text() == "should not be used"
    assert "p" not in r.cooldowns


def test_no_fallback_once_output_started():
    partial = [ProviderEvent(kind="text", text="half"), ProviderEvent(kind="error", error="cut", status=500,
                                                                     retryable=True)]
    backup = FakeProvider(["unused"], name="b")
    r = make({"main": ["p:m", "b:m"]}, p=FakeProvider([partial]), b=backup)
    events = run(r)
    assert [e.kind for e in events] == ["text", "error"] and not backup.calls


def test_bad_request_does_not_fall_back():
    bad = ProviderEvent(kind="error", error="HTTP 400", status=400)
    backup = FakeProvider(["unused"])
    r = make({"main": ["p:m", "b:m"]}, p=FakeProvider([bad]), b=backup)
    assert run(r)[-1].status == 400 and not backup.calls and not r.cooldowns


def test_lone_credential_reprobe_with_long_reset():
    clock = Clock()
    day5 = 5 * 86400.0
    p = FakeProvider([rate_limited(retry_after=day5), rate_limited(), "recovered"], name="p")
    r = make({"main": ["p:m"]}, clock, p=p)
    ev = run(r)[-1]
    assert ev.kind == "error" and ev.retryable
    cd = r.cooldowns["p"]
    first_wait = cd.until - clock.t
    assert first_wait == pytest.approx(0.02 * day5)  # 2.4 h, not 5 days and not 60 s
    assert cd.reset_at == clock.t + day5
    assert run(r)[-1].kind == "error" and len(p.calls) == 1  # still benched: no probe
    clock.t = cd.until + 1
    run(r)  # probe fails without a new reset: shorter window toward the same reset
    cd2 = r.cooldowns["p"]
    assert len(p.calls) == 2 and cd2.reset_at == cd.reset_at
    assert cd2.until - clock.t < first_wait and cd2.until <= cd.reset_at
    clock.t = cd2.until + 1
    assert run(r)[-1].message.text() == "recovered" and not r.cooldowns


def test_cooldown_rules():
    now = 0.0
    assert cooldown_until(now, 429, 1, None, sole=True) == SOLE_TTL
    assert cooldown_until(now, 429, 1, 30.0, sole=True) == 30.0  # never past the reset
    assert cooldown_until(now, 429, 1, 100000.0, sole=True) == 2000.0
    assert cooldown_until(now, 402, 1, 100000.0, sole=True) == 100000.0  # billing keeps the reset
    assert cooldown_until(now, 429, 1, 100000.0, sole=False) == 100000.0  # siblings keep the reset
    assert cooldown_until(now, 429, 3, None, sole=False) == 240.0
    assert cooldown_until(now, 429, 99, None, sole=False) == 4 * 3600.0


def test_sibling_credentials_rotate_from_env():
    rec = Recorder((429, b'{"error":{"message":"limit"}}', {"retry-after": "600"}),
                   (200, fixture_bytes("llamacpp_text_stream.sse"), {}))
    r = Router(roles={"main": ["or:m"]}, providers={"or": {"transport": "openai", "base_url": "http://h/v1",
                                                          "key_env": ["K1", "K2", "K3"]}},
               catalog={}, env={"K1": "a", "K3": "c"}, clock=Clock(), client=rec.client())
    assert [c.id for c in r.credentials("or")] == ["or#0", "or#1"]
    events = run(r)
    assert events[-1].message.text() == "Hello there"
    assert [q.headers["authorization"] for q in rec.requests] == ["Bearer a", "Bearer c"]
    assert r.cooldowns["or#0"].until == r.clock() + 600


def test_missing_key_skips_route_and_reports():
    r = Router(roles={"main": ["anthropic:claude-opus-5-5"]}, env={}, clock=Clock())
    ev = run(r)[-1]
    assert ev.kind == "error" and not ev.retryable and "key_env" in ev.error


def test_decision_role_falls_back_to_aux_then_main():
    r = make({"main": ["m:big"], "aux": ["a:small"], "decision": []}, m=FakeProvider(), a=FakeProvider())
    assert [x.ref for x in r.chain("decision")] == ["a:small", "m:big"]
    r.set_role("decision", ["d:tiny"])
    assert [x.ref for x in r.chain("decision")] == ["d:tiny", "a:small", "m:big"]
    assert [x.ref for x in r.chain("aux")] == ["a:small", "m:big"]
    assert [x.ref for x in r.chain("main")] == ["m:big"]
    with pytest.raises(ValueError):
        r.set_role("decision", ["no-colon"])


def test_decision_call_uses_aux_when_decision_down():
    d = FakeProvider([ProviderEvent(kind="error", error="down", status=503, retryable=True)])
    a = FakeProvider(['{"show_panel": false}'])
    r = make({"main": ["m:x"], "aux": ["a:y"], "decision": ["d:z"]}, d=d, a=a, m=FakeProvider())
    out = asyncio.run(aux(r, "show the panel?", system="answer json", role="decision", max_tokens=50))
    assert out == '{"show_panel": false}'
    assert a.calls[0]["messages"][0].role == "system" and a.calls[0]["opts"]["max_tokens"] == 50


def test_aux_raises_model_error_when_all_fail():
    r = make({"main": ["m:x"]}, m=FakeProvider([rate_limited()]))
    with pytest.raises(ModelError):
        asyncio.run(aux(r, "title?"))


def test_role_provider_satisfies_protocol_and_drives_tool_turns():
    fake = FakeProvider([tool_turn("read", {"path": "a"}), "done"])
    r = make({"main": ["f:m"]}, f=fake)
    prov = r.provider()
    assert isinstance(prov, Provider) and prov.name == "router:main"
    first = collect(prov.stream([Message.user("x")], []))
    assert first[-1].stop_reason == "tool_use" and first[-1].message.tool_calls[0].arguments == {"path": "a"}
    assert collect(prov.stream([Message.user("x")], []))[-1].message.text() == "done"


def test_catalog_loads_and_prices_usage():
    r = Router(env={"ANTHROPIC_API_KEY": "k"}, clock=Clock())
    assert r.chain("main")[0] == Route("anthropic", "claude-opus-5-5")
    assert r.roles["decision"] == [] and r.chain("decision")[0].ref == "anthropic:claude-haiku-5-5"
    from exo.core.types import Usage
    u = r.price(Route("anthropic", "claude-opus-5-5"), Usage(input_tokens=1_000_000, output_tokens=100_000))
    assert u.cost_usd == pytest.approx(6.0)
    status = {s["route"]: s for s in r.status() if s["role"] == "main"}
    assert status["anthropic:claude-opus-5-5"]["available"]
    assert not status["openrouter:anthropic/claude-opus-5.5"]["configured"]


def test_from_config_overrides():
    r = Router.from_config({"roles": {"main": ["llamacpp:qwen3"]}}, env={})
    assert r.chain("main")[0].ref == "llamacpp:qwen3"
    assert r.credentials("llamacpp")[0].key is None
