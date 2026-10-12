"""Key lookup, roles and providers. Hermetic: env={} and fakes, no keychain, no network."""
import logging
import os

import pytest

from exo.core.models import Router, keys
from exo.core.models.router import Route
from exo.models_cmd import remote_models, render

KEY = "sk-test-SECRET-0123456789"


def patch(monkeypatch, keychain=None, file=None):
    monkeypatch.setattr(keys, "keychain_get", lambda name: (keychain or {}).get(name))
    monkeypatch.setattr(keys, "file_get", lambda name, path=None: (file or {}).get(name))


def test_lookup_order(monkeypatch):
    patch(monkeypatch, {"K": "from-keychain"}, {"K": "from-file", "F": "only-file"})
    assert keys.lookup_key("K", {"K": "from-env"}) == "from-env"
    assert keys.lookup_key("K", {}) == "from-keychain"
    assert keys.lookup_key("F", {}) == "only-file"
    assert keys.lookup_key("NONE", {}) is None
    assert keys.lookup_key("K", {}, sources=False) is None


def test_keychain_skipped_off_darwin(monkeypatch):
    monkeypatch.setattr(keys.sys, "platform", "linux")
    called = []
    monkeypatch.setattr(keys.subprocess, "run", lambda *a, **k: called.append(a))
    assert keys.keychain_get("K") is None and not called


def test_keychain_command_and_miss(monkeypatch):
    monkeypatch.delenv("EXO_NO_KEYCHAIN", raising=False)  # subprocess.run is faked below
    monkeypatch.setattr(keys.sys, "platform", "darwin")
    seen = {}

    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw["timeout"]
        return R(0, KEY + "\n")
    monkeypatch.setattr(keys.subprocess, "run", run)
    assert keys.keychain_get("MY_KEY") == KEY
    assert seen["cmd"] == ["security", "find-generic-password", "-s", "exo", "-a", "MY_KEY", "-w"]
    assert seen["timeout"] <= 5
    monkeypatch.setattr(keys.subprocess, "run", lambda *a, **k: R(44, ""))
    assert keys.keychain_get("MY_KEY") is None


def test_keys_file_reads_at_0600(tmp_path):
    f = tmp_path / "keys.env"
    f.write_text(f"# c\nA={KEY}\nexport B='quoted'\n\nbad line\n")
    os.chmod(f, 0o600)
    assert keys.file_get("A", f) == KEY and keys.file_get("B", f) == "quoted" and keys.file_get("C", f) is None
    assert keys.file_get("A", tmp_path / "missing") is None


def test_keys_file_refused_when_loose(tmp_path, caplog):
    f = tmp_path / "keys.env"
    f.write_text(f"A={KEY}\n")
    os.chmod(f, 0o644)
    with caplog.at_level(logging.WARNING, logger="exo.keys"):
        assert keys.file_get("A", f) is None
    assert "refusing" in caplog.text and "644" in caplog.text and KEY not in caplog.text


def test_router_uses_sources_only_when_asked(monkeypatch):
    patch(monkeypatch, {"OPENCODE_GO_API_KEY": KEY})
    assert not Router(env={}).credentials("opencode-go")
    r = Router(env={}, key_sources=True)
    assert r.credentials("opencode-go")[0].key == KEY
    assert Router(env={"OPENCODE_GO_API_KEY": "e"}).credentials("opencode-go")[0].key == "e"


def test_key_never_in_status_repr_or_listing(monkeypatch):
    patch(monkeypatch, {"OPENCODE_GO_API_KEY": KEY})
    r = Router(env={"OPENAI_API_KEY": KEY + "2"}, key_sources=True,
               roles={"main": ["opencode-go:kimi-k3"]})
    creds = r.credentials("opencode-go") + r.credentials("openai")
    blob = " ".join([repr(r.status()), repr(r.providers_status()), repr(r.resolved_roles()), render(r),
                     repr(creds)])
    assert "SECRET" not in blob
    assert {p["provider"]: p["key"] for p in r.providers_status()}["opencode-go"] == "yes"
    assert {p["provider"]: p["key"] for p in r.providers_status()}["anthropic"] == "no"


def test_key_never_in_error_text(monkeypatch):
    import asyncio
    from exo.core.models.fake import FakeProvider
    from exo.core.types import Message
    patch(monkeypatch, {"OPENCODE_GO_API_KEY": KEY})
    r = Router(env={}, key_sources=True, roles={"main": ["opencode-go:kimi-k3"]})
    # the transport is real but the client fails; no network is touched
    class Boom:
        def stream(self, *a, **k):
            raise __import__("httpx").ConnectError("connection refused")
    r.client = Boom()
    r._creds.clear()

    async def go():
        return [e async for e in r.stream("main", [Message.user("hi")], [])]
    evs = asyncio.run(go())
    assert evs[-1].kind == "error" and KEY not in (evs[-1].error or "")


def test_eval_defaults_to_main():
    r = Router(env={}, roles={"main": ["a:big"]}, catalog={"providers": {}, "models": {}, "roles": {}},
               providers={"a": {"transport": "openai", "base_url": "http://h/v1"}})
    assert [x.ref for x in r.chain("eval")] == ["a:big"]
    r.set_role("main", ["a:other"])
    assert [x.ref for x in r.chain("eval")] == ["a:other"]
    assert "eval" in r.resolved_roles()
    r2 = Router(env={})  # shipped catalog
    assert r2.chain("eval") == r2.chain("main")


def test_eval_override():
    r = Router(env={}, roles={"eval": ["opencode-go:kimi-k3"]})
    assert [x.ref for x in r.chain("eval")][0] == "opencode-go:kimi-k3"
    assert r.chain("eval")[1:] == r.chain("main")


def test_decision_default_falls_back_and_override():
    r = Router(env={})
    assert r.roles["decision"] == ["jev:jev-latest", "laya:laya"]
    assert r.chain("decision") == [Route("jev", "jev-latest"), Route("laya", "laya")] + r.chain("aux") + [
        x for x in r.chain("main") if x not in r.chain("aux")]
    r = Router(env={}, roles={"decision": ["jev:jev-latest"]})
    assert r.chain("decision")[0] == Route("jev", "jev-latest")


def test_jev_is_decision_only():
    r = Router(env={})
    with pytest.raises(ValueError):
        r.set_role("main", ["jev:jev-latest"])
    r2 = Router(env={}, roles={"main": ["jev:jev-latest", "a:b"]})
    assert [x.ref for x in r2.chain("main")] == ["a:b"]


def test_jev_stub_falls_through(monkeypatch):
    import asyncio
    from exo.core.models import FakeProvider
    from exo.core.types import Message
    patch(monkeypatch, {"TYPESAFE_API_KEY": KEY})
    r = Router(env={}, key_sources=True, roles={"decision": ["jev:jev-latest"], "aux": ["a:m"]},
               instances={"a": FakeProvider(["ok"], name="a")})
    async def go():
        return [e async for e in r.stream("decision", [Message.user("pick")], [])]
    evs = asyncio.run(go())
    assert evs[-1].kind == "done" and evs[-1].message.text() == "ok"


def test_catalog_providers():
    r = Router(env={})
    for name, env, base in [("opencode-go", "OPENCODE_GO_API_KEY", "https://opencode.ai/zen/go/v1"),
                            ("jev", "TYPESAFE_API_KEY", "https://api.typesafe.ai/v1"),
                            ("nous", "NOUS_API_KEY", "https://inference-api.nousresearch.com/v1")]:
        assert r.providers[name]["key_env"] == env and r.providers[name]["base_url"] == base
    assert r.providers["opencode-go"]["session_header"] == "x-opencode-session"
    for local in ("llamacpp", "ollama", "lmstudio"):
        assert r.providers[local]["local"]


def test_remote_models_hermetic():
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "kimi-k3"}, {"id": "glm-5.3"}]}
    got = {}
    def get(url, headers, timeout):
        got["url"], got["h"] = url, headers
        return Resp()
    r = Router(env={"OPENCODE_GO_API_KEY": KEY})
    assert remote_models(r, "opencode-go", get=get) == ["glm-5.3", "kimi-k3"]
    assert got["url"] == "https://opencode.ai/zen/go/v1/models"
    with pytest.raises(ValueError, match="no OpenAI-style"):
        remote_models(r, "jev", get=get)
    def bad(url, headers, timeout):
        raise RuntimeError(f"failed with {headers}")
    with pytest.raises(ValueError) as e:
        remote_models(r, "opencode-go", get=bad)
    assert KEY not in str(e.value)


def test_cli_models_and_config_roles(monkeypatch, tmp_path, capsys):
    from exo.cli import main
    patch(monkeypatch, {"OPENCODE_GO_API_KEY": KEY})
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    for v in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert "opencode-go" in out and "key=yes" in out and "eval" in out and KEY not in out
    assert main(["config", "roles"]) == 0
    out = capsys.readouterr().out
    assert "eval" in out and "decision" in out and KEY not in out
