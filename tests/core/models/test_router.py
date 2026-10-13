import asyncio
import threading
import time

import pytest

from exo.core.models import FakeProvider, ModelError, Router, aux
from exo.core.models.fake import rate_limited, tool_turn
from exo.core.models.router import SOLE_TTL, Breaker, Route, cooldown_until
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


def test_slow_credential_lookup_keeps_event_loop_responsive(monkeypatch):
    rec = Recorder((200, fixture_bytes("llamacpp_text_stream.sse"), {}))
    r = Router(roles={"main": ["p:m"]}, catalog={}, env={}, client=rec.client(),
               providers={"p": {"base_url": "https://example.invalid/v1", "key_env": ["K"]}})
    loop_thread = threading.get_ident()

    def key(_):
        assert threading.get_ident() != loop_thread
        time.sleep(.15)  # stands in for a slow Secret Service subprocess; never reads a real store
        return "fake-key"

    monkeypatch.setattr(r, "_key", key)

    async def go():
        beats = 0
        running = True
        async def heartbeat():
            nonlocal beats
            while running:
                await asyncio.sleep(.01)
                beats += 1
        task = asyncio.create_task(heartbeat())
        events = [e async for e in r.stream("main", [Message.user("hi")], [])]
        running = False
        await task
        assert events[-1].kind == "done" and beats >= 5
    asyncio.run(go())


def test_success_does_not_resolve_unused_fallback_credentials(monkeypatch):
    rec = Recorder((200, fixture_bytes("llamacpp_text_stream.sse"), {}))
    r = Router(roles={"main": ["p:m", "unused:m"]}, catalog={}, env={}, client=rec.client(),
               providers={p: {"base_url": "https://example.invalid/v1", "key_env": [k]}
                          for p, k in [("p", "PRIMARY"), ("unused", "FALLBACK")]})
    lookups = []
    monkeypatch.setattr(r, "_key", lambda key: lookups.append(key) or "fake-key")
    assert run(r)[-1].kind == "done"
    assert lookups == ["PRIMARY"] and "unused" not in r._creds


def test_concurrent_credential_resolution_shares_one_lookup(monkeypatch):
    r = Router(catalog={}, env={}, providers={"p": {"base_url": "https://example.invalid", "key_env": ["K"]}})
    calls = []
    def key(_):
        calls.append(1)
        time.sleep(.1)
        return "fake-key"
    monkeypatch.setattr(r, "_key", key)

    async def go():
        creds = await asyncio.gather(*(asyncio.to_thread(r.credentials, "p") for _ in range(8)))
        assert len(calls) == 1 and all(c is creds[0] for c in creds)
    asyncio.run(go())


@pytest.mark.parametrize("all_providers", [False, True])
def test_reload_during_lookup_discards_the_old_generation(monkeypatch, all_providers):
    r = Router(catalog={}, env={}, providers={"p": {"base_url": "https://example.invalid", "key_env": ["K"]}})
    started, release = threading.Event(), threading.Event()
    calls = []
    def key(_):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(2)
            return "old-key"
        return "new-key"
    monkeypatch.setattr(r, "_key", key)

    async def go():
        first = asyncio.create_task(asyncio.to_thread(r.credentials, "p"))
        assert await asyncio.to_thread(started.wait, 1)
        assert r.reload_credentials(None if all_providers else "p") == ["p"]
        second = await asyncio.to_thread(r.credentials, "p")
        release.set()
        old_result = await first
        assert second is old_result and second[0].key == "new-key"
        assert r.credentials("p") is second and len(calls) == 2
    try:
        asyncio.run(go())
    finally:
        release.set()


def test_decision_role_falls_back_to_aux_then_main():
    r = make({"main": ["m:big"], "aux": ["a:small"], "decision": []}, m=FakeProvider(), a=FakeProvider())
    assert [x.ref for x in r.chain("decision")] == ["a:small", "m:big"]
    r.instances["d"] = FakeProvider()
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
    assert r.roles["decision"] == ["jev:jev-latest", "laya:laya"]
    assert [x.ref for x in r.chain("decision")] == ["jev:jev-latest", "laya:laya", "anthropic:claude-haiku-5-5", "anthropic:claude-opus-5-5",
                                                  "openrouter:anthropic/claude-opus-5.5"]
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


def test_set_role_rejects_flag_text_and_unknown_provider():
    r = make({"main": ["a:m"]}, a=object())
    for bad in ["--default", "-x:y", "opencode-go:glm 5", "nope:model", "a:", "a:m --default"]:
        with pytest.raises(ValueError):
            r.set_role("main", [bad])
        with pytest.raises(ValueError):
            r.prefer("main", bad)
    assert r.roles["main"] == ["a:m"]
    r.prefer("main", "a:other")
    assert r.roles["main"] == ["a:other", "a:m"]


def _flaky_pair(n_fail, **cfg):
    primary = FakeProvider([rate_limited()] * n_fail + ["primary back"] * 5, name="p")
    backup = FakeProvider(["from backup"] * 20, name="b")
    clock = Clock()
    r = Router(roles={"main": ["p:free", "b:small"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"p": primary, "b": backup}, env={}, clock=clock, breaker=cfg or None)
    return r, primary, backup, clock


def test_breaker_opens_after_two_failures_and_logs_once():
    r, primary, backup, clock = _flaky_pair(3)
    first = run(r)
    assert first[-1].attempts[0].get("cooldown_s") is None  # one failure: not yet
    clock.t += SOLE_TTL + 1  # the credential cooldown is over; only the breaker can keep the route out
    second = run(r)
    assert second[-1].attempts[0]["cooldown_s"] == 600
    assert any(e.kind == "notice" and "skipping it for 10 min" in e.text for e in second)
    clock.t += SOLE_TTL + 1
    third = run(r)
    assert len(primary.calls) == 2 and third[-1].message.text() == "from backup"
    assert not any("cooldown_s" in a for a in third[-1].attempts)  # logged once, on the failure that tripped it


def test_breaker_trial_after_cooldown_reopens_doubled_then_recovers():
    r, primary, backup, clock = _flaky_pair(3)
    for _ in range(2):
        run(r)
        clock.t += SOLE_TTL + 1
    clock.t += 600  # cool-down over: one trial call
    trial = run(r)
    assert len(primary.calls) == 3 and trial[-1].attempts[0]["cooldown_s"] == 1200  # failed trial: doubled
    clock.t += 1201 + SOLE_TTL
    assert run(r)[-1].message.text() == "primary back"
    assert "p:free" not in r.breakers  # recovered: counters cleared


def test_breaker_cooldown_capped_and_configurable():
    r, *_ = _flaky_pair(0, failures=1, cooldown_s=100, max_cooldown_s=250)
    assert [r.route_failed("x:y", 0.0), r.route_failed("x:y", 1.0), r.route_failed("x:y", 2.0)] == [100, 200, 250]


def test_breaker_ignored_when_every_route_is_open():
    r, primary, backup, clock = _flaky_pair(0)
    for ref in ("p:free", "b:small"):
        r.breakers[ref] = Breaker(level=1, until=clock.t + 500)
    assert run(r)[-1].message.text() == "primary back"  # tried anyway: failing without a call helps nobody


def test_nonretryable_errors_do_not_count():
    r, *_ = _flaky_pair(0)
    bad = FakeProvider([ProviderEvent(kind="error", error="HTTP 400: bad", status=400, retryable=False)] * 3, name="p")
    r.instances["p"] = bad
    r._creds.clear()
    for _ in range(3):
        run(r)
    assert "p:free" not in r.breakers
