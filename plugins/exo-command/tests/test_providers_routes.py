"""Provider setup routes (D-034): the guard (loopback peer, Host, Origin, Sec-Fetch-Site, page token) and the rule
that a key goes in once and never comes back out. A real WebServer on 127.0.0.1, raw HTTP so every header is
under the test's control. Key stores are faked; the conftest points EXO_KEYS_FILE at a temp file."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess

import pytest

from exo import providers_setup as ps
from exo.core.models import keys
from exo.core.models.router import Router
from exo.protocol import schema as s
from exo_command import providers as prov
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre

FAKE = "sk-fake-WEBROUTE-0123456789abcdef"
TOKEN = "test-page-token-0123456789"


class Src:
    def __init__(self):
        self.calls = []

    async def call(self, body):
        self.calls.append(body)
        t = type(body).TYPE
        if t == "models.list":
            return {"models": [{"role": "main", "route": "anthropic:claude-opus-5-5"}],
                    "available": [{"route": "openai:gpt-5", "provider": "openai"}]}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    monkeypatch.setattr(ps, "_run", lambda argv, stdin=None: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(keys, "keychain_get", lambda name: None)
    monkeypatch.setattr(keys, "secret_service_get", lambda name: None)
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")  # the faked _run would report a removal on any Linux box with secret-tool
    monkeypatch.setattr(ps, "_http", lambda *a, **k: (401, None))
    for k in list(os.environ):
        if k.endswith(("_API_KEY", "_TOKEN")):
            monkeypatch.delenv(k)


def make_server(src=None):
    web = WebServer(CommandCentre(load_settings({}), src or Src()), token=TOKEN)
    web.router_factory = lambda: Router(env={}, key_sources=True)
    return web


async def raw(port, method, path, headers=None, body=None):
    """One HTTP/1.1 request with exactly these headers. Returns (status, json)."""
    data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if data:
        h.setdefault("Content-Type", "application/json")
        h["Content-Length"] = str(len(data))
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items() if v is not None) + "\r\n"
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(head.encode() + data)
    await w.drain()
    resp = await r.read()
    w.close()
    status = int(resp.split(b" ", 2)[1])
    payload = resp.split(b"\r\n\r\n", 1)[1]
    return status, json.loads(payload) if payload else None, resp.decode("latin-1")


def serve(fn, src=None):
    async def go():
        web = make_server(src)
        port = await web.start("127.0.0.1", 0)
        try:
            return await fn(web, port)
        finally:
            await web.close()
    return asyncio.run(go())


def ok_headers(port, **extra):
    return {"X-Exo-Token": TOKEN, "Origin": f"http://127.0.0.1:{port}", **extra}


# ---------------------------------------------------------------- the guard


@pytest.mark.parametrize("case", ["no-token", "wrong-token", "bad-host", "rebind-host", "cross-origin",
                                  "null-origin", "https-origin", "origin-other-port", "cross-site", "same-site",
                                  "no-host"])
@pytest.mark.parametrize("route", ["/api/providers/openai/key", "/api/providers/openai/remove",
                                   "/api/providers/openai/test", "/api/providers/default", "/api/providers"])
def test_key_routes_refuse_without_every_check(case, route):
    async def go(web, port):
        h = ok_headers(port)
        if case == "no-token":
            h.pop("X-Exo-Token")
        elif case == "wrong-token":
            h["X-Exo-Token"] = TOKEN[:-1] + "x"
        elif case == "bad-host":
            h["Host"] = "evil.example:7342"
        elif case == "rebind-host":
            h["Host"] = f"rebind.attacker.test:{port}"
            h["Origin"] = f"http://rebind.attacker.test:{port}"
        elif case == "cross-origin":
            h["Origin"] = "http://evil.example"
        elif case == "null-origin":
            h["Origin"] = "null"
        elif case == "https-origin":
            h["Origin"] = f"https://127.0.0.1:{port}"
        elif case == "origin-other-port":
            h["Origin"] = "http://127.0.0.1:1"
        elif case == "cross-site":
            h["Sec-Fetch-Site"] = "cross-site"
        elif case == "same-site":
            h["Sec-Fetch-Site"] = "same-site"
        elif case == "no-host":
            h["Host"] = None
        method = "GET" if route == "/api/providers" else "POST"
        body = {"key": FAKE, "ref": "openai:gpt-5"} if method == "POST" else None
        return await raw(port, method, route, h, body)
    status, data, text = serve(go)
    assert status == 403, (case, route, data)
    assert FAKE not in text
    assert not keys.keys_file_path().exists()


def test_non_loopback_peer_is_refused():
    assert prov.guard({"host": "127.0.0.1:7342", "x-exo-token": TOKEN}, "192.168.1.5", TOKEN)[0] == 403
    assert prov.guard({"host": "127.0.0.1:7342", "x-exo-token": TOKEN}, None, TOKEN)[0] == 403
    assert prov.guard({"host": "127.0.0.1:7342", "x-exo-token": TOKEN}, "127.0.0.1", TOKEN) is None
    assert prov.guard({"host": "[::1]:7342", "x-exo-token": TOKEN}, "::1", TOKEN) is None
    assert prov.guard({"host": "localhost:7342", "x-exo-token": TOKEN}, "::ffff:127.0.0.1", TOKEN) is None
    assert prov.guard({"host": "localhost:7342", "x-exo-token": ""}, "127.0.0.1", "")[0] == 403  # no token set


def test_other_routes_need_the_token_too():
    """D-039: every /api/ route but `POST /api/launch` (#166) needs the page token now, and none echoes it."""
    async def go(web, port):
        return [await raw(port, "GET", "/api/health"),
                await raw(port, "GET", "/api/health", {"X-Exo-Token": TOKEN})]
    [(no, _, _), (status, data, text)] = serve(go)
    assert no == 403 and status == 200 and TOKEN not in text


# ---------------------------------------------------------------- the happy path never echoes


def test_add_test_remove_and_default_with_every_check_passing(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    src = Src()
    texts = []

    async def go(web, port):
        h = ok_headers(port, **{"Sec-Fetch-Site": "same-origin"})
        st, data, text = await raw(port, "POST", "/api/providers/openai/key", h, {"key": FAKE, "store": "file"})
        texts.append(text)
        assert st == 200 and data["stored_in"] == "file" and data["core_reloaded"] is True
        assert data["effective_source"] == "file" and data["shadowed_by"] is None and not data["warning"]
        assert f"OPENAI_API_KEY={FAKE}" in keys.keys_file_path().read_text()
        assert stat.S_IMODE(keys.keys_file_path().stat().st_mode) == 0o600
        assert keys.redact_secrets(FAKE) == "[redacted]"  # learned at once in the web process
        st, data, text = await raw(port, "GET", "/api/providers", h)
        texts.append(text)
        row = next(r for r in data["providers"] if r["provider"] == "openai")
        assert st == 200 and row["source"] == "file" and row["key"] == "found"
        st, data, text = await raw(port, "POST", "/api/providers/openai/test", h, {})
        texts.append(text)
        assert st == 200 and data["error_class"] == "auth" and not data["ok"]
        st, data, text = await raw(port, "GET", "/api/providers/models", h)
        texts.append(text)
        assert [m["route"] for m in data["available"]] == ["openai:gpt-5"]
        st, data, text = await raw(port, "POST", "/api/providers/default", h, {"ref": "openai:gpt-5"})
        texts.append(text)
        assert st == 200 and data["routes"][:2] == ["openai:gpt-5", "anthropic:claude-opus-5-5"]
        st, data, text = await raw(port, "POST", "/api/providers/openai/remove", h, {})
        texts.append(text)
        assert st == 200 and data["removed_from"] == ["file"]
        st, data, text = await raw(port, "POST", "/api/providers/openai/key", h, {"key": "bad key with spaces"})
        texts.append(text)
        assert st == 400 and "bad key" not in text
        st, data, text = await raw(port, "POST", f"/api/providers/{FAKE}/key", h, {"key": FAKE})
        texts.append(text)
        assert st == 400
    serve(go, src)
    out = capsys.readouterr()
    blob = "\n".join(texts) + caplog.text + out.out + out.err + "".join(repr(c) for c in src.calls)
    assert FAKE not in blob
    reloads = [c for c in src.calls if isinstance(c, s.CommandRun) and c.args.startswith("reload ")]
    assert [c.args for c in reloads] == ["reload openai", "reload openai"]
    sets = [c for c in src.calls if isinstance(c, (s.ModelsSet, s.ConfigSet))]
    assert sets[0].model == "openai:gpt-5" and sets[1].key == "models.roles.main"


@pytest.mark.parametrize("denied", [s.ModelsSet, s.ConfigSet])
def test_default_never_falls_back_after_core_refuses_authority(denied):
    from exo.config import Config
    from exo.server.engine import CommandFailed
    cfg = Config.load()
    cfg.set("models.roles.main", ["anthropic:claude-opus-5-5"])
    cfg.save()
    path = cfg.home / "config.json"
    before = path.read_bytes()

    class Refused(Src):
        async def call(self, body):
            if isinstance(body, denied):
                raise CommandFailed("forbidden", "only the principal can change settings")
            return await super().call(body)

    async def go(web, port):
        return await raw(port, "POST", "/api/providers/default", ok_headers(port), {"ref": "openai:gpt-5"})
    status, data, _ = serve(go, Refused())
    assert status == 403 and "principal" in data["error"]
    assert path.read_bytes() == before


def test_file_save_reports_shadowing_through_web_response(monkeypatch):
    old = "older-web-test-key-123456"
    monkeypatch.setattr(keys, "keychain_get", lambda name: old if name == "OPENAI_API_KEY" else None)

    async def go(web, port):
        status, data, text = await raw(port, "POST", "/api/providers/openai/key", ok_headers(port),
                                       {"key": FAKE, "store": "file"})
        assert status == 200 and data["ok"] and data["core_reloaded"]
        assert data["stored_in"] == "file" and data["effective_source"] == data["shadowed_by"] == "keychain"
        assert "remove only that item" in data["warning"] and "--store keychain" in data["warning"]
        assert FAKE not in text and old not in text
        assert keys.lookup_key("OPENAI_API_KEY", {}) == old
        assert keys.file_get("OPENAI_API_KEY") == FAKE

    serve(go)


def test_default_falls_back_to_config_when_the_core_is_down():
    class Down(Src):
        async def call(self, body):
            raise ConnectionError("core down")

    async def go(web, port):
        return await raw(port, "POST", "/api/providers/default", ok_headers(port), {"ref": "openai:gpt-5"})
    status, data, _ = serve(go, Down())
    assert status == 200 and data["applied"] == "next start" and data["routes"][0] == "openai:gpt-5"


def test_page_url_carries_the_token_in_the_fragment_only():
    web = make_server()
    assert web.page_url("127.0.0.1", 7342) == f"http://127.0.0.1:7342/#token={TOKEN}"
    assert web.page_url("::1", 7342).startswith("http://[::1]:7342/#token=")
    assert WebServer(CommandCentre(load_settings({}), Src())).token != WebServer(
        CommandCentre(load_settings({}), Src())).token


def test_server_source_has_no_request_logging():
    """The server prints its URL once and nothing per request; a body is never logged."""
    from pathlib import Path
    import exo_command
    src = (Path(exo_command.__file__).parent / "server.py").read_text()
    prints = [ln.strip() for ln in src.splitlines() if "print(" in ln or "log." in ln or "logging" in ln]
    assert all("exo-command:" in p for p in prints), prints


def test_passing_test_reloads_the_core_so_the_chat_retries_at_once(monkeypatch):
    """A local server came back: a failed test leaves the core alone, a passing one tells it to reload that
    provider, which clears its cooldown and route breaker (the chat used to stay blocked until they ran out)."""
    answers = [ConnectionError(), (200, {"data": [{"id": "llama3.2"}]})]

    def http(*a, **k):
        out = answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(ps, "_http", http)
    src = Src()

    async def go(web, port):
        down = await raw(port, "POST", "/api/providers/ollama/test", ok_headers(port), {})
        reloads_after_failure = [c for c in src.calls if isinstance(c, s.CommandRun)]
        up = await raw(port, "POST", "/api/providers/ollama/test", ok_headers(port), {})
        return down, reloads_after_failure, up

    (st, down, _), reloads_after_failure, (st2, up, _) = serve(go, src)
    assert st == 200 and not down["ok"] and down["error_class"] == "not_running" and "core_reloaded" not in down
    assert reloads_after_failure == []
    assert st2 == 200 and up["ok"] and up["core_reloaded"] is True
    assert [c.args for c in src.calls if isinstance(c, s.CommandRun)] == ["reload ollama"]
