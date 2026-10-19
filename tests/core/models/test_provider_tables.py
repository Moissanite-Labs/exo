"""A [models.providers.NAME] config table merges key by key over the catalog entry of the same name.
Hermetic: env={}, httpx MockTransport. No keychain, no network."""
import asyncio
import copy

import httpx
import pytest

from exo.config import Config
from exo.core.models import Router
from exo.core.models.anthropic import AnthropicProvider
from exo.core.models.claude_cli import ClaudeCliProvider
from exo.core.models.decision import DecisionProvider, ask_choice_result
from exo.core.models.openai_chat import OpenAIChatProvider
from exo.core.models.router import load_catalog
from exo.core.types import Message

from .conftest import Recorder, collect, fixture_bytes

LAYA_8001 = "http://127.0.0.1:8001/v1"  # the PROVIDERS.md case: Laya moved off port 8000 (vLLM's default)
LAN = "http://127.0.0.1:1234/v1"  # a LAN vLLM or LM Studio behind a catalog name
CATALOG_KEY = "k-catalog-provider-key"
EMPTY = {"providers": {}, "models": {}, "roles": {}}


def key_names(entry):
    envs = entry.get("key_env") or []
    return [envs] if isinstance(envs, str) else list(envs)


KEYED = sorted(n for n, p in load_catalog()["providers"].items() if key_names(p) and p.get("base_url"))


def run(router, role="main"):
    return collect(router.stream(role, [Message.user("hi")], []))


def test_partial_laya_table_keeps_catalog_transport_local_and_roles():
    r = Router.from_config({"providers": {"laya": {"base_url": LAYA_8001}}}, env={})
    spec = r.providers["laya"]
    assert (spec["transport"], spec["local"], spec["roles"], spec["base_url"]) == ("decision", True, ["decision"],
                                                                                   LAYA_8001)
    [cred] = r.credentials("laya")
    assert isinstance(cred.provider, DecisionProvider) and cred.provider.base_url == LAYA_8001 and cred.key is None
    assert not r._allows("laya", "main") and "laya:laya" not in [rt.ref for rt in r.chain("main")]
    assert "laya:laya" in [rt.ref for rt in r.chain("decision")]


def test_documented_laya_port_override_still_answers_decisions(tmp_path):
    cfg = Config(home=tmp_path)
    cfg.set("models.providers.laya.base_url", LAYA_8001)  # what `exo config set` writes: a one-key table
    seen = []

    def reply(req):
        seen.append(req)
        return httpx.Response(200, json={"model": "laya", "answers": {"q": {
            "type": "choice", "choice": "b", "probabilities": {"a": 0.2, "b": 0.8}}}})

    r = Router.from_config(cfg.models(), env={}, client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    res = asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))
    assert (res.source, res.route, res.choice) == ("decision", "laya:laya", "b")
    assert [str(q.url) for q in seen] == [LAYA_8001 + "/systemone"]


def test_one_key_table_keeps_catalog_base_url_and_streams():
    rec = Recorder((200, fixture_bytes("anthropic_tool_stream.sse"), {}))
    r = Router.from_config({"providers": {"anthropic": {"read_timeout": 300}},
                            "roles": {"main": ["anthropic:claude-opus-5-5"]}},
                           env={"ANTHROPIC_API_KEY": "k-test"}, client=rec.client())
    [cred] = r.credentials("anthropic")
    assert isinstance(cred.provider, AnthropicProvider) and cred.key == "k-test"
    assert cred.provider.base_url == "https://api.anthropic.com" and cred.provider._timeout.read == 300
    events = run(r)
    assert [e.kind for e in events].count("done") == 1 and events[-1].kind == "done"
    assert str(rec.requests[0].url).startswith("https://api.anthropic.com/")


def test_one_key_cli_table_keeps_the_catalog_transport_and_command(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    r = Router.from_config({"providers": {"claude-cli": {"read_timeout": 900}}}, env={"PATH": str(tmp_path)})
    [cred] = r.credentials("claude-cli")
    assert isinstance(cred.provider, ClaudeCliProvider) and cred.provider.command == str(exe)
    assert cred.provider._timeout.read == 900


def test_the_keyed_catalog_providers_are_all_covered():
    assert {"anthropic", "openai", "openrouter", "nous", "opencode-go", "jev"} <= set(KEYED)


@pytest.mark.parametrize("key_set", [False, True], ids=["no-key", "key-set"])
@pytest.mark.parametrize("moved", [True, False], ids=["lan-url", "catalog-url"])
@pytest.mark.parametrize("name", KEYED)
def test_base_url_only_override_of_a_keyed_provider_is_keyless(name, moved, key_set):
    entry = load_catalog()["providers"][name]
    url = LAN if moved else entry["base_url"]  # restating the catalog URL is keyless too, as before the merge
    env = {e: CATALOG_KEY for e in key_names(entry)} if key_set else {}
    r = Router.from_config({"providers": {name: {"base_url": url}}}, env=env)
    assert r.providers[name] == {**{k: v for k, v in entry.items() if k != "key_env"}, "base_url": url}
    [cred] = r.credentials(name)  # one keyless credential, as before the merge: the catalog key stays home
    assert cred.key is None and cred.provider.api_key is None and cred.provider.base_url == url
    assert [p["key"] for p in r.providers_status() if p["provider"] == name] == ["n/a"]


@pytest.mark.parametrize("name, fixture, ref", [
    ("openai", "openai_tool_stream.sse", "openai:local-model"),
    ("anthropic", "anthropic_tool_stream.sse", "anthropic:claude-opus-5-5")])
def test_a_moved_base_url_never_carries_the_catalog_key(name, fixture, ref):
    rec = Recorder((200, fixture_bytes(fixture), {}))
    env = {"OPENAI_API_KEY": CATALOG_KEY, "ANTHROPIC_API_KEY": CATALOG_KEY}
    r = Router.from_config({"providers": {name: {"base_url": LAN}}, "roles": {"main": [ref]}}, env=env,
                           client=rec.client())
    events = run(r)
    assert [e.kind for e in events].count("done") == 1 and events[-1].kind == "done"
    [req] = rec.requests
    assert str(req.url).startswith(LAN + "/") and "authorization" not in req.headers and "x-api-key" not in req.headers
    assert CATALOG_KEY not in str(req.headers.raw) + req.content.decode()


def test_self_hosted_jev_keeps_the_decision_route_without_the_typesafe_key():
    seen = []

    def reply(req):
        seen.append(req)
        return httpx.Response(200, json={"model": "jev-latest", "answers": {"q": {
            "type": "choice", "choice": "b", "probabilities": {"a": 0.1, "b": 0.9}}}})

    for env in ({}, {"TYPESAFE_API_KEY": CATALOG_KEY}):
        r = Router.from_config({"providers": {"jev": {"base_url": LAN}}}, env=env,
                               client=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
        res = asyncio.run(ask_choice_result(r, "s", "q", ["a", "b"]))
        assert (res.source, res.route, res.choice) == ("decision", "jev:jev-latest", "b")
    assert [str(q.url) for q in seen] == [LAN + "/systemone"] * 2
    assert not any("authorization" in q.headers for q in seen)


@pytest.mark.parametrize("key_env, sent", [("ANTHROPIC_API_KEY", CATALOG_KEY), ("LAN_KEY", "k-lan")])
def test_an_explicit_key_env_with_a_moved_base_url_sends_that_key(key_env, sent):
    rec = Recorder((200, fixture_bytes("anthropic_tool_stream.sse"), {}))
    proxy = "http://127.0.0.1:4000"  # an Anthropic-compatible proxy the user trusts with a key
    r = Router.from_config({"providers": {"anthropic": {"base_url": proxy, "key_env": key_env}},
                            "roles": {"main": ["anthropic:claude-opus-5-5"]}},
                           env={"ANTHROPIC_API_KEY": CATALOG_KEY, "LAN_KEY": "k-lan"}, client=rec.client())
    [cred] = r.credentials("anthropic")
    assert isinstance(cred.provider, AnthropicProvider) and cred.key == sent and cred.provider.base_url == proxy
    assert run(r)[-1].kind == "done"
    [req] = rec.requests
    assert str(req.url) == proxy + "/v1/messages" and req.headers["x-api-key"] == sent
    assert Router.from_config({"providers": {"anthropic": {"base_url": proxy, "key_env": key_env}}},
                              env={}).credentials("anthropic") == []  # the key it asks for is required


@pytest.mark.parametrize("table", [{"read_timeout": 60}, {"headers": {"x-team": "a"}},
                                   {"base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"}],
                         ids=["timeout", "headers", "url-and-key"])
def test_a_table_without_base_url_or_with_its_key_env_keeps_the_catalog_key(table):
    [cred] = Router.from_config({"providers": {"openai": table}}, env={"OPENAI_API_KEY": CATALOG_KEY}).credentials(
        "openai")
    assert cred.key == CATALOG_KEY and cred.provider.base_url == "https://api.openai.com/v1"
    assert Router.from_config({"providers": {"openai": table}}, env={}).credentials("openai") == []


@pytest.mark.parametrize("table", [{"key_env": []}, {"base_url": LAN, "key_env": []}])
def test_an_empty_key_env_is_keyless_at_any_url(table):
    [cred] = Router.from_config({"providers": {"openai": table}}, env={"OPENAI_API_KEY": CATALOG_KEY}).credentials(
        "openai")
    assert cred.key is None and isinstance(cred.provider, OpenAIChatProvider)
    assert cred.provider.base_url == table.get("base_url", "https://api.openai.com/v1")


def test_provider_without_base_url_ends_with_one_error_not_keyerror():
    r = Router(catalog=copy.deepcopy(EMPTY), providers={"x": {"transport": "openai", "read_timeout": 30}},
               roles={"main": ["x:m"]}, env={})
    assert r.credentials("x") == []
    events = run(r)
    assert [e.kind for e in events] == ["error"] and not events[0].retryable
    assert r.status() and not any(row["configured"] for row in r.status())
    assert [p["base_url"] for p in r.providers_status()] == [""]


def test_provider_tables_merge_without_changing_the_catalog():
    catalog = {"providers": {"a": {"transport": "openai", "base_url": "http://a/v1", "key_env": "A_KEY",
                                   "roles": ["aux"]},
                             "b": {"transport": "openai", "base_url": "http://b/v1"}}, "models": {}, "roles": {}}
    before = copy.deepcopy(catalog)
    r = Router(catalog=catalog, env={}, providers={"a": {"read_timeout": 9, "roles": ["aux", "main"]},
                                                   "c": {"base_url": "http://c/v1"}})
    assert r.providers["a"] == {"transport": "openai", "base_url": "http://a/v1", "key_env": "A_KEY",
                                "roles": ["aux", "main"], "read_timeout": 9}  # keys the user sets win
    assert r.providers["b"] == before["providers"]["b"] and r.providers["c"] == {"base_url": "http://c/v1"}
    for url in ("http://lan/v1", "http://a/v1"):  # a table that sets base_url uses only the key_env it sets
        moved = Router(catalog=catalog, env={}, providers={"a": {"base_url": url}})
        assert moved.providers["a"] == {"transport": "openai", "base_url": url, "roles": ["aux"]}
    assert catalog == before
    nulled = Router(catalog=catalog, env={}, providers={"b": None})  # a null table is kept as is, as before
    assert nulled.providers["b"] is None and nulled.credentials("b") == []
