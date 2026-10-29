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
    reloads = [c for c in src.calls if isinstance(c, s.CommandRun)]
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


# ------------------------------------------------- D-091: the model list says which failure it was


class Failing(Src):
    """The core fails the model list itself; every other call behaves."""

    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    async def call(self, body):
        if type(body).TYPE == "models.list":
            raise self.exc
        return await super().call(body)


@pytest.mark.parametrize("case, kind, retryable, says", [
    ("connect", "transport", True, "not reachable"),
    ("no-socket", "transport", True, "not reachable"),
    ("forbidden", "authority", False, "principal"),
    ("unauthorized", "authority", False, "principal"),
    ("internal", "other", False, "router is busy"),
    ("source-internal", "other", False, "OSError: disk full"),
    ("http-401", "authority", False, "token"),
    ("http-403", "authority", False, "token"),
])
def test_model_list_names_the_failure_and_only_offers_a_retry_it_can_honour(case, kind, retryable, says):
    """A transport failure can be retried; a core that answered and refused cannot, so the page must not offer it
    one. The picker keeps its shape either way (200, both lists present), so the caller never sees a bare error."""
    from exo.server.engine import CommandFailed
    from exo_command.source import SourceError
    exc = {"connect": ConnectionError("core down"),
           "no-socket": FileNotFoundError(2, "no such socket"),
           "forbidden": SourceError("forbidden", "only the principal may list models"),
           "unauthorized": SourceError("unauthorized", "token expired"),
           "internal": CommandFailed("internal", "router is busy"),
           "source-internal": SourceError("internal", "OSError: disk full"),
           "http-401": SourceError("http_401", '{"error": "bearer token required"}'),
           "http-403": SourceError("http_403", "forbidden by a proxy")}[case]

    async def go(web, port):
        return await raw(port, "GET", "/api/providers/models", ok_headers(port))
    status, data, text = serve(go, Failing(exc))
    assert status == 200 and data["models"] == [] and data["available"] == []
    assert data["error_class"] == kind and data["retryable"] is retryable
    assert says in data["error"] and "Traceback" not in data["error"]
    assert "ConnectError" not in text and "Errno" not in text
    # #856: the sentence never carries the exception's class name, nor the core's raw JSON
    assert not any(n in data["error"] for n in ("CommandFailed", "SourceError", "ClientError", '{"error"'))


def test_a_core_on_tcp_that_refuses_the_bearer_token_is_an_authority_failure(tmp_path):
    """#856: `exo web --url URL --token-file FILE` with a stale token. A real core on TCP answers HTTP 401 before it
    reads the command; that is a refusal, not a core failure, so the page gets the token sentence and no retry."""
    from exo.server.app import Dispatcher, HttpServer
    from exo.server.echo import EchoEngine
    from exo_command.source import ClientSource

    async def go():
        core = HttpServer(Dispatcher(EchoEngine()), token="the-core-token-0123456789")
        core_port = await core.start_tcp("127.0.0.1", 0)
        serving = asyncio.create_task(core.serve_forever())
        src = ClientSource(url=f"http://127.0.0.1:{core_port}", token="a-stale-token-0123456789")
        web = make_server(src)
        port = await web.start("127.0.0.1", 0)
        try:
            return (await raw(port, "GET", "/api/providers/models", ok_headers(port)),
                    await raw(port, "POST", "/api/chat/sessions", ok_headers(port), {"title": "x"}))
        finally:
            await web.close()
            await src.aclose()
            serving.cancel()
            await core.close()

    (status, data, text), (chat_status, chat, chat_text) = asyncio.run(go())
    assert status == 200 and data["models"] == [] and data["available"] == []
    assert data["error_class"] == "authority" and data["retryable"] is False
    assert "token" in data["error"] and "exod.token" in data["error"]
    assert "bearer token required" not in text and "SourceError" not in text and "a-stale-token" not in text
    # the chat routes relay a core error through error_reply(): the same sentence, not the core's 401 body
    assert chat_status == 502 and chat["code"] == "http_401" and "exod.token" in chat["error"]
    assert "bearer token required" not in chat_text and "a-stale-token" not in chat_text


def test_a_key_value_in_a_failed_model_list_never_reaches_the_page(monkeypatch):
    """D-091: the failure sentence reaches a browser, so a key value the core echoed in its error is cut out."""
    from exo.server.engine import CommandFailed
    monkeypatch.setenv("OPENAI_API_KEY", FAKE)

    async def go(web, port):
        return await raw(port, "GET", "/api/providers/models", ok_headers(port))
    status, data, text = serve(go, Failing(CommandFailed("internal", f"router failed with key {FAKE} for openai")))
    assert status == 200 and data["error_class"] == "other"
    assert FAKE not in text and "[redacted]" in data["error"] and "router failed with key" in data["error"]
