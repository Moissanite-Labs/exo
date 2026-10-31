"""Credential ids stay put when a key comes or goes, and a 501 refusal cools nothing (exo-desktop #52).

A provider with several ``key_env`` names numbered only the keys it found. When a key appeared or went away, the
other keys moved to new ids. A call in flight across a reload then kept the old ids: it skipped the new key as
the one that had failed, sent the key that had just failed once more, and cooled the new key in its place. Each
key is now numbered by its place in ``key_env``.

A 501 is a transport saying it cannot carry this request (muse-cli refuses any call with tools). The router cooled
the whole credential for it, so a later call without tools skipped a route that would have served it. It now
falls back without a cooldown.
"""
import asyncio

from exo.core.models import FakeProvider, Router
from exo.core.models.base import error_event
from exo.core.types import Message, ToolSpec

from .conftest import Recorder, collect, fixture_bytes


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _router(env, client=None):
    return Router(roles={"main": ["or:m"]}, catalog={}, env=env, clock=Clock(), client=client,
                  providers={"or": {"transport": "openai", "base_url": "http://h/v1", "key_env": ["K1", "K2", "K3"]}})


def test_each_key_keeps_its_id_when_another_key_comes_or_goes():
    env = {"K2": "b", "K3": "c"}
    r = _router(env)
    assert [(c.id, c.key) for c in r.credentials("or")] == [("or#1", "b"), ("or#2", "c")]
    env["K1"] = "a"
    r.reload_credentials("or")
    assert [(c.id, c.key) for c in r.credentials("or")] == [("or#0", "a"), ("or#1", "b"), ("or#2", "c")]
    del env["K2"]
    r.reload_credentials("or")
    assert [(c.id, c.key) for c in r.credentials("or")] == [("or#0", "a"), ("or#2", "c")]


def test_one_named_key_keeps_the_provider_id():
    r = Router(roles={"main": ["or:m"]}, catalog={}, env={"K1": "a"}, clock=Clock(),
               providers={"or": {"transport": "openai", "base_url": "http://h/v1", "key_env": "K1"}})
    assert [c.id for c in r.credentials("or")] == ["or"]


def test_a_key_added_during_a_call_is_tried_and_the_failed_key_is_not_resent():
    env = {"K2": "b", "K3": "c"}
    holder = {}

    class AddKeyOnFirstRequest(Recorder):
        def __call__(self, request):
            if not self.requests:  # while the first key's call is out, the principal adds K1 (D-034 reload)
                env["K1"] = "a"
                holder["router"].reload_credentials("or")
            return super().__call__(request)

    rec = AddKeyOnFirstRequest((429, b'{"error":{"message":"limit"}}', {"retry-after": "600"}),
                               (200, fixture_bytes("llamacpp_text_stream.sse"), {}))
    r = holder["router"] = _router(env, rec.client())
    events = collect(r.stream("main", [Message.user("hi")], []))
    assert events[-1].message.text() == "Hello there"
    assert [q.headers["authorization"] for q in rec.requests] == ["Bearer b", "Bearer a"]
    assert "or#1" in r.cooldowns and "or#0" not in r.cooldowns  # the key that answered 429 is the one cooling


class TextOnly:
    """A transport that, like muse-cli, refuses a request that carries tools with a 501."""

    name = "text-only"

    def __init__(self):
        self.calls = 0

    async def stream(self, messages, tools, **opts):
        self.calls += 1
        if tools:
            yield error_event("text only: it cannot carry tools", 501, retryable=False)
            return
        async for ev in FakeProvider(["from text-only"]).stream(messages, tools):
            yield ev


def test_a_capability_refusal_falls_back_without_cooling_the_credential():
    text, backup = TextOnly(), FakeProvider(["from backup", "again"], name="b")
    r = Router(roles={"main": ["muse:x", "b:y"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"muse": text, "b": backup}, env={}, clock=Clock())
    tools = [ToolSpec(name="read_file", description="read a file")]
    first = collect(r.stream("main", [Message.user("use a tool")], tools))
    assert first[-1].message.text() == "from backup"
    assert "muse" not in r.cooldowns and not any(k.startswith("muse|") for k in r.cooldowns)
    second = collect(r.stream("main", [Message.user("just talk")], []))
    assert second[-1].message.text() == "from text-only" and text.calls == 2


def test_a_capability_refusal_still_ends_the_call_when_no_route_is_left():
    r = Router(roles={"main": ["muse:x"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               instances={"muse": TextOnly()}, env={}, clock=Clock())
    ev = asyncio.run(_last(r.stream("main", [Message.user("use a tool")], [ToolSpec(name="t", description="t")])))
    assert ev.kind == "error" and "cannot carry tools" in (ev.error or "")
    assert not r.cooldowns


async def _last(agen):
    last = None
    async for ev in agen:
        last = ev
    return last
