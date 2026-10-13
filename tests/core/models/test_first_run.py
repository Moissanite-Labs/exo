"""Default routing uses real providers; all HTTP and credential probes are fake."""
import asyncio
import json
import threading

import httpx
import pytest

from exo import providers_setup as ps
from exo.config import Config
from exo.core.engine import CoreEngine
from exo.core.models.fake import FakeProvider
from exo.core.models.router import load_catalog
from exo.core.types import Message
from exo.protocol import schema as s

KEY = "test-key-for-routing-only"


def test_configuration_does_not_read_credentials(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("configuration read a key source")
    monkeypatch.setattr("exo.core.models.keys.lookup_key", forbidden)
    cfg = Config(home=tmp_path)
    assert cfg.models() == cfg.models(env={}) == {}
    cfg.data["models"] = {"fake": {}}
    assert cfg.models()["roles"]["main"] == ["fake:scripted"]
    cfg.data["models"] = {"roles": {"main": ["fake:chosen"]}}
    assert cfg.models()["roles"]["main"] == ["fake:chosen"]
    cfg.data["models"] = {"local": {"base_url": "http://unit.invalid/v1", "model": "my-model"}}
    assert cfg.models()["roles"]["main"] == ["local:my-model"]


@pytest.mark.parametrize("provider", ["openrouter", "openai", "nous", "opencode-go"])
def test_key_only_real_http_route_and_model_selection(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(ps, "probe", lambda url: False)
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        payload = {"choices": [{"delta": {"content": "Real transport reply"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            key_env = load_catalog()["providers"][provider]["key_env"]
            eng = CoreEngine(tmp_path / "home", Config(home=tmp_path).models(),
                             env={key_env: KEY, "PATH": ""}, cwd=str(tmp_path))
            eng.router.client = client
            await eng.start()
            state = (await eng._command_run(s.CommandRun(name="providers")))["output"]["main"]
            if provider != "openrouter":
                assert not state["ready"] and state["needs_model"] == [provider]
                before = [ev async for ev in eng.router.stream("main", [Message.user("hello")], [])]
                assert before[-1].kind == "error" and "/providers" in before[-1].error
                assert not requests and not any(ev.kind == "text" for ev in before)
                await eng._models_set(s.ModelsSet(model=f"{provider}:user-chosen-chat"))
                assert (await eng._main_status())["ready"]
            else:
                assert state["ready"] and state["route"].startswith("openrouter:")
            events = [ev async for ev in eng.router.stream("main", [Message.user("hello")], [])]
            assert events[-1].kind == "done" and events[-1].route.startswith(provider + ":")
            assert events[-1].message.text() == "Real transport reply" and len(requests) == 1
            await eng.close()

    asyncio.run(run())


def test_cli_only_uses_existing_catalog_route_off_event_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "probe", lambda url: False)
    monkeypatch.setattr(ps.shutil, "which", lambda name, path=None: "/fake/claude" if name == "claude" else None)
    threads = []
    original = ps.rows

    def rows(*args, **kwargs):
        threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(ps, "rows", rows)

    async def run():
        eng = CoreEngine(tmp_path / "home", Config(home=tmp_path).models(env={}),
                         env={"PATH": ""}, cwd=str(tmp_path))
        eng.router.instances["claude-cli"] = FakeProvider(["CLI reply"])
        await eng.start()
        assert eng.router.roles["main"][0] == "claude-cli:sonnet"
        assert all(t != threading.get_ident() for t in threads)
        events = [ev async for ev in eng.router.stream("main", [Message.user("hello")], [])]
        assert events[-1].route == "claude-cli:sonnet" and events[-1].message.text() == "CLI reply"
        await eng.close()

    asyncio.run(run())


def test_no_provider_has_no_stub_and_aux_only_muse_is_not_main(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "probe", lambda url: False)
    monkeypatch.setattr(ps.shutil, "which", lambda *args, **kwargs: None)

    async def run():
        eng = CoreEngine(tmp_path / "home", Config(home=tmp_path).models(env={}), env={}, cwd=str(tmp_path))
        await eng.start()
        assert not (await eng._main_status())["ready"]
        events = [ev async for ev in eng.router.stream("main", [Message.user("hello")], [])]
        assert events[-1].kind == "error" and "no credentials" in events[-1].error
        assert not any(ev.kind == "text" for ev in events)
        state = ps.main_status(eng.router, eng.home, automatic=True, table=[{
            "provider": "muse-cli", "ready": True, "roles": ["aux"], "transport": "muse-cli"}])
        assert not state["ready"] and not state["needs_model"]
        with pytest.raises(ValueError, match="cannot serve role"):
            eng.router.prefer("main", "muse-cli:default")
        await eng.close()

    asyncio.run(run())
