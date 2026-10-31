"""Custom provider discovery is bound to the current core's configured endpoint."""
import json

import httpx

from exo import providers_setup as ps
from exo.core.models.router import Router
from exo.models_cmd import CACHE_TTL, available_models

KEY = "synthetic-cache-key-0123456789"


def custom_router(endpoint, *, model="chosen"):
    name, spec = ps.custom_provider("demo", endpoint, "openai", "key", model)
    return Router(catalog={"providers": {name: spec}, "roles": {"main": [f"{name}:{model}"]}},
                  env={spec["key_env"]: KEY}, key_sources=False)


def reply(model, calls):
    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={"data": [{"id": model}]}, request=httpx.Request("GET", url))
    return get


def routes(router, home, get, now):
    return [item["route"] for item in available_models(router, home, get=get, now=now)]


def test_endpoint_edit_after_restart_does_not_reuse_old_models(tmp_path):
    calls = []
    first = custom_router("https://first.test/v1")
    assert routes(first, tmp_path, reply("old-model", calls), 100) == ["custom-demo:chosen", "custom-demo:old-model"]
    restarted = custom_router("https://first.test/v1")
    assert routes(restarted, tmp_path, reply("unexpected", calls), 101) == ["custom-demo:chosen", "custom-demo:old-model"]
    assert calls == ["https://first.test/v1/models"]
    edited_model = custom_router("https://first.test/v1", model="another")
    assert routes(edited_model, tmp_path, reply("unexpected", calls), 101) == ["custom-demo:another", "custom-demo:old-model"]
    assert calls == ["https://first.test/v1/models"]
    changed = custom_router("https://second.test/v1")
    assert routes(changed, tmp_path, reply("new-model", calls), 102) == ["custom-demo:chosen", "custom-demo:new-model"]
    assert calls == ["https://first.test/v1/models", "https://second.test/v1/models"]
    assert changed.roles["main"] == ["custom-demo:chosen"]
    saved = (tmp_path / "cache/models/custom-demo.json").read_text()
    assert KEY not in saved and "second.test" not in saved


def test_changed_endpoint_never_falls_back_to_old_or_legacy_cache(tmp_path):
    first = custom_router("https://first.test/v1")
    routes(first, tmp_path, reply("old-model", []), 100)
    second = custom_router("https://second.test/v1")
    def offline(*args, **kwargs):
        raise ConnectionError("synthetic")
    assert routes(second, tmp_path, offline, 101) == ["custom-demo:chosen"]
    f = tmp_path / "cache/models/custom-demo.json"
    f.write_text(json.dumps({"at": 100, "ids": ["legacy"]}))
    assert routes(second, tmp_path, offline, 101) == ["custom-demo:chosen"]


def test_unchanged_custom_configuration_can_use_its_stale_cache(tmp_path):
    router = custom_router("https://first.test/v1")
    routes(router, tmp_path, reply("old-model", []), 100)
    def offline(*args, **kwargs):
        raise ConnectionError("synthetic")
    assert routes(custom_router("https://first.test/v1"), tmp_path, offline, 101 + CACHE_TTL) == [
        "custom-demo:chosen", "custom-demo:old-model"]


def test_custom_key_reference_change_does_not_reuse_old_models(tmp_path):
    first = custom_router("https://first.test/v1")
    routes(first, tmp_path, reply("old-model", []), 100)
    spec = {**first.providers["custom-demo"], "key_env": "OTHER_TEST_KEY"}
    changed = Router(catalog={"providers": {"custom-demo": spec}}, env={"OTHER_TEST_KEY": KEY}, key_sources=False)
    calls = []
    assert routes(changed, tmp_path, reply("new-model", calls), 101) == ["custom-demo:chosen", "custom-demo:new-model"]
    assert calls == ["https://first.test/v1/models"]


def test_custom_protocol_change_does_not_show_openai_discovery(tmp_path):
    first = custom_router("https://first.test/v1")
    routes(first, tmp_path, reply("old-model", []), 100)
    spec = {**first.providers["custom-demo"], "transport": "anthropic"}
    changed = Router(catalog={"providers": {"custom-demo": spec}}, env={spec["key_env"]: KEY}, key_sources=False)
    calls = []
    assert routes(changed, tmp_path, reply("unexpected", calls), 101) == ["custom-demo:chosen"]
    assert not calls


def test_legacy_cache_for_non_custom_provider_is_still_used(tmp_path):
    f = tmp_path / "cache/models/existing.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"at": 100, "ids": ["cached"]}))
    router = Router(catalog={"providers": {"existing": {"transport": "openai", "base_url": "https://first.test/v1",
                                                           "key_env": "TEST_API_KEY"}}},
                    env={"TEST_API_KEY": KEY}, key_sources=False)
    calls = []
    assert routes(router, tmp_path, reply("unexpected", calls), 101) == ["existing:cached"]
    assert not calls
