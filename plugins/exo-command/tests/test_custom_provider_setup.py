"""Custom setup uses only synthetic keys, a fake core, and mock transports."""
import asyncio
import json

import httpx
import pytest

from exo import providers_setup as ps
from exo.core.models.router import Router
from exo.core.types import Message
from exo.models_cmd import available_models
from exo.protocol import schema as s
from exo.server.engine import CommandFailed
from exo_command import providers

KEY = "synthetic-provider-key-0123456789"
BASE = {"name": "demo", "endpoint": "https://example.test/v1", "protocol": "openai",
        "auth": "key", "model": "model-one"}


class Source:
    def __init__(self):
        self.providers = {}
        self.calls = []
        self.refuse = False

    async def call(self, command):
        self.calls.append(command)
        if isinstance(command, s.ConfigGet):
            return {"value": self.providers.copy()}
        if isinstance(command, s.ConfigSet):
            if self.refuse:
                raise CommandFailed("forbidden", "principal required")
            self.providers[command.key.removeprefix("models.providers.")] = command.value
            return {"saved": True}
        return {}


class Centre:
    def __init__(self, source):
        self.source = source


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_KEYS_FILE", str(tmp_path / "keys.env"))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")
    monkeypatch.setattr(ps, "default_store", lambda: "file")


def call(centre, method, body=None):
    return asyncio.run(providers.handle(centre, method, "/api/providers/custom", body or {}))


def test_save_edit_and_endpoint_change_do_not_forward_old_key(tmp_path):
    src = Source()
    centre = Centre(src)
    code, result = call(centre, "POST", {**BASE, "key": KEY})
    assert code == 200 and result["key_saved"] and result["applies"] == "next start"
    old = src.providers["custom-demo"].copy()
    assert KEY not in json.dumps(old) and KEY not in json.dumps(result)
    assert KEY in (tmp_path / "keys.env").read_text()
    code, result = call(centre, "POST", {**BASE, "model": "model-two", "key": "", "replace": True})
    assert code == 200 and src.providers["custom-demo"]["key_env"] == old["key_env"]
    assert src.providers["custom-demo"]["setup_model"] == "model-two"
    code, _ = call(centre, "POST", {**BASE, "endpoint": "https://other.test/v1", "key": "", "replace": True})
    new = src.providers["custom-demo"]
    assert code == 200 and new["key_env"] != old["key_env"]
    assert ps.keys_mod.file_get(new["key_env"]) is None
    _, listing = call(centre, "GET")
    assert listing["providers"] == [{"name": "demo", "endpoint": "https://other.test/v1", "protocol": "openai",
                                     "auth": "key", "model": "model-one"}]
    assert KEY not in json.dumps(listing) and KEY not in repr(src.calls)


@pytest.mark.parametrize("change", [
    {"name": "bad.name"}, {"endpoint": "file:///tmp/a"}, {"endpoint": "https://user:secret@127.0.0.1/v1"},
    {"endpoint": "https://example.test/v1?key=secret"}, {"endpoint": "https://example.test/v1#secret"},
    {"endpoint": "https://example.test/v1?"}, {"endpoint": "https://example.test/v1#"},
    {"endpoint": "https://example.test/v1/?#"},
    {"endpoint": "https://example.test:99999/v1"}, {"protocol": "unknown"}, {"auth": "custom-header"},
    {"model": "bad model"}, {"model": "x" * 200}, {"model": "model\x7f"},
])
def test_invalid_values_do_not_write_config_or_key(change, tmp_path):
    src = Source()
    code, result = call(Centre(src), "POST", {**BASE, **change, "key": KEY})
    assert code == 400 and KEY not in json.dumps(result)
    assert not src.providers and not (tmp_path / "keys.env").exists()


def test_local_endpoints_and_protocol_auth_are_explicit():
    name, spec = ps.custom_provider("local", "http://127.0.0.1:11434/v1/", "openai", "none", "llama")
    assert name == "custom-local" and spec["base_url"] == "http://127.0.0.1:11434/v1"
    assert "key_env" not in spec
    _, spec = ps.custom_provider("a", "https://example.test", "anthropic", "key", "model")
    assert spec["transport"] == "anthropic" and spec["key_env"].endswith("_API_KEY")


def test_keyless_custom_endpoint_is_configured_but_not_verified(tmp_path):
    name, spec = ps.custom_provider("local", "http://127.0.0.1:11434/v1", "openai", "none", "llama")
    router = Router(catalog={"providers": {name: spec}, "roles": {"main": [name + ":llama"]}},
                    env={}, key_sources=False)
    row = ps.rows(router, home=tmp_path, probe_local=False)[0]
    assert row["ready"] is True and row["status"] == "configured" and row["verified"] is False
    ps._save_state(tmp_path, name, {"ok": False, "error_class": "network",
                                     "configuration": ps._custom_status_id(spec)})
    failed = ps.rows(router, home=tmp_path, probe_local=False)[0]
    assert failed["ready"] is True and failed["status"] == "failed: network" and not failed["verified"]


def test_custom_probe_result_does_not_follow_configuration_edit(tmp_path):
    name, spec = ps.custom_provider("local", "http://127.0.0.1:11434/v1", "openai", "none", "llama")
    def router_for(config):
        return Router(catalog={"providers": {name: config}, "roles": {}}, env={}, key_sources=False)
    router = router_for(spec)
    result = ps.test_provider(name, router=router, home=tmp_path, http=lambda *args: (200, {"data": []}))
    assert result["ok"] and ps.rows(router, home=tmp_path, probe_local=False)[0]["verified"]
    for field, value in (("base_url", "http://127.0.0.1:9999/v1"), ("setup_model", "other")):
        changed = router_for({**spec, field: value})
        row = ps.rows(changed, home=tmp_path, probe_local=False)[0]
        assert row["status"] == "configured" and not row["verified"] and row["last_test"] is None


def test_refusal_or_manual_name_collision_does_not_store_key(tmp_path):
    src = Source()
    src.refuse = True
    code, _ = call(Centre(src), "POST", {**BASE, "key": KEY})
    assert code == 403 and not (tmp_path / "keys.env").exists()
    src.refuse = False
    src.providers["custom-demo"] = {"base_url": "https://manual.test"}
    code, _ = call(Centre(src), "POST", {**BASE, "key": KEY})
    assert code == 400 and src.providers["custom-demo"] == {"base_url": "https://manual.test"}


def test_add_collision_requires_explicit_edit_and_rejects_stale_edit():
    src = Source()
    centre = Centre(src)
    assert call(centre, "POST", {**BASE, "auth": "none"})[0] == 200
    before = src.providers["custom-demo"].copy()
    code, result = call(centre, "POST", {**BASE, "auth": "none", "endpoint": "https://other.test/v1"})
    assert code == 400 and "already" in result["error"] and src.providers["custom-demo"] == before
    assert call(centre, "POST", {**BASE, "auth": "none", "replace": "true"})[0] == 400
    assert call(centre, "POST", {**BASE, "name": "missing", "auth": "none", "replace": True})[0] == 400
    assert call(centre, "POST", {**BASE, "auth": "none", "replace": True})[0] == 200


def test_custom_key_save_respects_required_spend_policy(tmp_path, monkeypatch):
    from exo.config import Config
    from exo.model_spend import SpendJournal
    home = tmp_path / "home"
    journal = tmp_path / "spend.sqlite"
    spend = {"principal": "alice", "journal": str(journal), "journal_id": SpendJournal.provision(journal, "alice"),
             "daily_usd": "0", "monthly_usd": "0", "routes": {}}
    cfg = Config.load(home)
    cfg.set("models.spend", spend)
    cfg.save()
    monkeypatch.setenv("EXO_MODEL_SPEND_REQUIRED", "1")
    monkeypatch.delenv("EXO_MODEL_SPEND_POLICY", raising=False)
    src = Source()
    code, result = call(Centre(src), "POST", {**BASE, "key": KEY})
    assert code == 200 and result["key_saved"] and ps.keys_mod.file_get(src.providers["custom-demo"]["key_env"]) == KEY


def test_missing_required_spend_policy_does_not_store_custom_key(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_MODEL_SPEND_REQUIRED", "1")
    monkeypatch.delenv("EXO_MODEL_SPEND_POLICY", raising=False)
    code, result = call(Centre(Source()), "POST", {**BASE, "key": KEY})
    assert code == 200 and not result["key_saved"] and "key could not be stored" in result["warning"]
    assert not (tmp_path / "keys.env").exists()


def test_key_store_failure_reports_partial_save(monkeypatch):
    src = Source()
    monkeypatch.setattr(ps, "set_key", lambda *a, **k: (_ for _ in ()).throw(OSError("secret")))
    code, result = call(Centre(src), "POST", {**BASE, "key": KEY})
    assert code == 200 and result["ok"] and not result["key_saved"]
    assert "key could not be stored" in result["warning"] and KEY not in json.dumps(result)
    assert "custom-demo" in src.providers


def test_custom_config_survives_reload_without_putting_key_in_config(tmp_path):
    from exo.config import Config

    home = tmp_path / "home"
    class PersistentSource(Source):
        async def call(self, command):
            if isinstance(command, s.ConfigGet):
                return {"value": Config.load(home).get(command.key) or {}}
            if isinstance(command, s.ConfigSet):
                config = Config.load(home)
                config.set(command.key, command.value)
                config.save()
                return {"saved": True}
            return await super().call(command)

    centre = Centre(PersistentSource())
    code, result = call(centre, "POST", {**BASE, "key": KEY})
    assert code == 200 and result["key_saved"]
    assert KEY not in (home / "config.json").read_text()
    config = Config.load(home).models()
    saved = config["providers"]["custom-demo"]
    assert saved["base_url"] == BASE["endpoint"] and saved["setup_model"] == BASE["model"]
    router = Router.from_config(config, env={saved["key_env"]: KEY}, key_sources=False)
    provider = router.credentials("custom-demo")[0].provider
    assert provider.base_url == BASE["endpoint"] and provider.headers()["authorization"] == "Bearer " + KEY


def test_model_and_gemini_wire_use_mock_endpoints(tmp_path):
    name, spec = ps.custom_provider(**{"name": "demo", "endpoint": BASE["endpoint"], "protocol": "openai",
                                       "auth": "key", "model": "model-one"})
    router = Router(catalog={"providers": {name: spec}}, env={spec["key_env"]: KEY}, key_sources=False)
    response = httpx.Response(200, json={"data": [{"id": "model-one"}, {"id": "model-two"}]},
                              request=httpx.Request("GET", "https://example.test/v1/models"))
    models = available_models(router, tmp_path, get=lambda *a, **k: response)
    assert [m["route"] for m in models] == ["custom-demo:model-one", "custom-demo:model-two"]

    requests = []
    async def handler(request):
        requests.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = Router(env={"GEMINI_API_KEY": KEY}, key_sources=False, client=client)
            provider = r.credentials("gemini")[0].provider
            return [event async for event in provider.stream([Message(role="user", content=[])], [], model="gemini-test")]

    events = asyncio.run(go())
    assert str(requests[0].url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer " + KEY
    assert any(event.kind == "text" for event in events)


def test_anthropic_custom_protocol_sends_its_expected_header():
    name, spec = ps.custom_provider("other", "https://example.test", "anthropic", "key", "model")
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "fixture refusal"}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            router = Router(catalog={"providers": {name: spec}}, env={spec["key_env"]: KEY},
                            key_sources=False, client=client)
            return [event async for event in router.credentials(name)[0].provider.stream(
                [Message(role="user", content=[])], [], model="model")]

    asyncio.run(go())
    assert str(requests[0].url) == "https://example.test/v1/messages"
    assert requests[0].headers["x-api-key"] == KEY and "authorization" not in requests[0].headers
