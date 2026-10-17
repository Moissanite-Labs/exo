"""A chain that ends on a refused key, or on a local server that is down, says so and is not retried.

The audit: a bad key read as an outage (three retries, then "next route reopens in 300s" with the 401 gone),
and a stopped local server read as a raw ConnectError. Waiting fixes neither, so the error is not retryable and
names what to fix: the provider and its key, or the server address."""

import asyncio

import httpx
import pytest

from exo.core.context import DefaultCompressor, SummaryError
from exo.core.loop import Loop
from exo.core.models import FakeProvider, Router
from exo.core.models.fake import rate_limited
from exo.core.models.router import AUTH_TTL
from exo.core.sessions import EventBus, SessionStore
from exo.core.types import Message, ProviderEvent

from .conftest import Recorder, collect, fixture_bytes

BAD_KEY = b'{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}'
CATALOG_MAIN = ["anthropic:claude-opus-5-5", "openrouter:anthropic/claude-opus-5.5"]


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def run(router, role="main"):
    return collect(router.stream(role, [Message.user("hi")], []))


def refused(status, text="refused"):
    return ProviderEvent(kind="error", error=f"HTTP {status}: {text}", status=status)


def bad_key_router(clock, rec):
    """The audit's setup: the catalog main chain, an invalid Anthropic key, no OpenRouter key."""
    return Router(roles={"main": CATALOG_MAIN}, env={"ANTHROPIC_API_KEY": "invalid"}, clock=clock, client=rec.client())


def test_rejected_key_names_provider_and_key_and_is_not_retryable():
    clock, rec = Clock(), Recorder((401, BAD_KEY, {}))
    ev = run(bad_key_router(clock, rec))[-1]
    assert ev.kind == "error" and not ev.retryable and ev.status == 401
    assert ev.error == ("anthropic rejected the key (HTTP 401: invalid x-api-key): fix ANTHROPIC_API_KEY "
                        "or add a key in /providers")
    assert "cooling down" not in ev.error and "reopens" not in ev.error


def test_later_calls_during_the_auth_cooldown_keep_the_cause():
    clock, rec = Clock(), Recorder((401, BAD_KEY, {}))
    r = bad_key_router(clock, rec)
    run(r)
    clock.t += 20
    ev = run(r)[-1]
    assert len(rec.requests) == 1  # the credential is benched: nothing is sent
    assert ev.kind == "error" and not ev.retryable and ev.status == 401
    assert ev.error == "anthropic rejected the key (HTTP 401): fix ANTHROPIC_API_KEY or add a key in /providers"


def test_key_is_tried_again_after_the_auth_cooldown_or_a_reload():
    clock = Clock()
    rec = Recorder((401, BAD_KEY, {}), (401, BAD_KEY, {}), (200, fixture_bytes("anthropic_tool_stream.sse"), {}))
    r = bad_key_router(clock, rec)
    run(r)
    clock.t += AUTH_TTL + 1
    assert "rejected the key" in run(r)[-1].error and len(rec.requests) == 2
    r.reload_credentials("anthropic")  # what /providers sends after a new key or a passing test
    assert run(r)[-1].kind == "done" and len(rec.requests) == 3


@pytest.mark.parametrize("status", [401, 402, 403])
def test_every_refusal_status_stops_the_retry(status):
    r = Router(roles={"main": ["p:m"]}, providers={"p": {"key_env": "P_API_KEY"}},
               catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"p": FakeProvider([refused(status, "no")], name="p")}, env={}, clock=Clock())
    ev = run(r)[-1]
    assert not ev.retryable and ev.status == status
    assert ev.error == f"p rejected the key (HTTP {status}: no): fix P_API_KEY or add a key in /providers"


def test_sibling_keys_are_all_named():
    r = Router(roles={"main": ["p:m"]}, providers={"p": {"key_env": ["P_KEY_1", "P_KEY_2"]}},
               catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"p": FakeProvider([refused(401)], name="p")}, env={}, clock=Clock())
    assert "fix P_KEY_1 or P_KEY_2 or add a key" in run(r)[-1].error


def test_keyless_provider_refusal_asks_for_a_login():
    r = Router(roles={"main": ["cli:m"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"cli": FakeProvider([refused(401, "not logged in")], name="cli")}, env={}, clock=Clock())
    ev = run(r)[-1]
    assert not ev.retryable
    assert ev.error == "cli refused the login (HTTP 401: not logged in): log in again, then test it in /providers"


def test_only_the_last_failure_decides():
    """A refusal that ends the chain stops the retry; a 503 after a 401 can still clear, so it stays retryable."""
    def chain(*last):
        p = FakeProvider([refused(401)], name="p")
        b = FakeProvider([ProviderEvent(kind="error", error=e, status=st, retryable=st == 503) for e, st in last],
                         name="b")
        return Router(roles={"main": ["p:m", "b:m"]}, catalog={"providers": {}, "models": {}, "roles": {}},
                      instances={"p": p, "b": b}, env={}, clock=Clock())

    ev = run(chain(("HTTP 403: forbidden", 403)))[-1]
    assert not ev.retryable and ev.error.startswith("b refused the login (HTTP 403: forbidden)")
    ev = run(chain(("HTTP 503: down", 503)))[-1]
    assert ev.retryable and "all routes for 'main' failed" in ev.error and "HTTP 503" in ev.error


def test_a_later_call_names_the_refusal_only_while_nothing_reopens_sooner():
    """Nothing ran on the later call: the chain's credential that reopens first gives the cause. A refused key
    that reopens last does not hide a sibling route whose rate limit ends sooner."""
    clock = Clock()
    p = FakeProvider([refused(401)], name="p")
    b = FakeProvider([rate_limited(retry_after=30)], name="b")
    r = Router(roles={"main": ["p:m", "b:m"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"p": p, "b": b}, env={}, clock=clock)
    run(r)
    clock.t += 5
    ev = run(r)[-1]
    assert ev.retryable and "reopens in 25s" in ev.error and "refused" not in ev.error
    clock.t += 30  # the rate limit is over; b answers 401 too and both are benched for the auth cooldown
    b.script.append(refused(401))
    run(r)
    clock.t += 5
    ev = run(r)[-1]
    assert not ev.retryable and ev.error.startswith("p refused the login (HTTP 401)")


@pytest.mark.parametrize("name,where", [("ollama", "127.0.0.1:11434"), ("lmstudio", "127.0.0.1:1234"),
                                        ("llamacpp", "127.0.0.1:8080")])
def test_local_server_down_says_where_to_start_it(name, where):
    rec = Recorder((0, httpx.ConnectError("All connection attempts failed"), {}))
    r = Router(roles={"main": [f"{name}:some-model"]}, env={}, clock=Clock(), client=rec.client())
    ev = run(r)[-1]
    assert ev.kind == "error" and not ev.retryable
    assert ev.error == f"{name} is not running at {where}: start it, then test it in /providers"
    assert "key" not in ev.error


def test_only_a_refused_local_connection_reads_as_stopped():
    """A remote connect error and a local timeout stay retryable outages; a refused local connect does not."""
    def last(name, exc, env=None):
        rec = Recorder((0, exc, {}))
        return run(Router(roles={"main": [f"{name}:m"]}, env=env or {}, clock=Clock(), client=rec.client()))[-1]

    down = httpx.ConnectError("All connection attempts failed")
    assert not last("ollama", down).retryable
    ev = last("openrouter", down, {"OPENROUTER_API_KEY": "k"})
    assert ev.retryable and "ConnectError" in ev.error and "not running" not in ev.error
    ev = last("ollama", httpx.ReadTimeout("timed out"))
    assert ev.retryable and "not running" not in ev.error


def test_a_background_summary_is_retried_later_not_dropped():
    """The turn is not retried, but the error says when the credential reopens (retry_after), so a summary that
    failed on it stays transient: the context tree tries it again later instead of skipping the span for good after
    three tries. Summaries use the aux role, which falls back to main."""
    def summary_error(router):
        with pytest.raises(SummaryError) as caught:
            asyncio.run(DefaultCompressor(router.provider("aux"), retries=0).summarize("source"))
        return caught.value

    exc = summary_error(bad_key_router(Clock(), Recorder((401, BAD_KEY, {}))))
    assert "rejected the key" in str(exc) and exc.retryable
    rec = Recorder((0, httpx.ConnectError("All connection attempts failed"), {}))
    exc = summary_error(Router(roles={"main": ["ollama:m"], "aux": []}, env={}, clock=Clock(), client=rec.client()))
    assert "ollama is not running" in str(exc) and exc.retryable


def test_loop_does_not_retry_a_rejected_key(tmp_path):
    """End to end through the turn loop: one request, no "retrying model call" notices, and the Error names the key."""
    rec = Recorder((401, BAD_KEY, {}), (401, BAD_KEY, {}), (401, BAD_KEY, {}), (401, BAD_KEY, {}))
    router = bad_key_router(Clock(), rec)
    store = SessionStore(tmp_path / "s.sqlite")
    bus, seen = EventBus(store), []
    bus.subscribe(seen.append)
    loop = Loop(store, router.provider("main"), bus=bus, retry_delay=0)

    async def go():
        info = await loop.create_session()
        return await loop.send(info.id, "say hi")

    res = asyncio.run(go())
    assert res.stop_reason == "error" and len(rec.requests) == 1
    assert not [e for e in seen if e.type == "notice" and "retrying" in e.body.text]
    err = next(e.body for e in seen if e.type == "error")
    assert not err.retryable and "fix ANTHROPIC_API_KEY" in err.message
