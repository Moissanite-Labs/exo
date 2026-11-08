"""Hardening of the local web server: request reads are bounded in time and size, the connection count is capped,
static files revalidate by ETag and travel gzipped, /api/health shows no userinfo or path, and a core error reaches
the page redacted. Raw HTTP, as test_csp_web.py does."""
from __future__ import annotations

import asyncio
import gzip
import json
import re
from pathlib import Path

import pytest

from exo_command import api, server
from exo_command.config import Settings, load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import SourceError

from test_csp_web import AUTH, POLICY, TOKEN, Src

STATIC = Path(server.__file__).parent / "static"


def run_web(body, src=None, settings=None, **attrs):
    """Start a server on a free loopback port, run ``await body(web, port)``, close."""
    async def go():
        web = WebServer(CommandCentre(settings or load_settings({}), src or Src()), token=TOKEN)
        for k, v in attrs.items():
            setattr(web, k, v)
        port = await web.start("127.0.0.1", 0)
        try:
            return await body(web, port)
        finally:
            await web.close()
    return asyncio.run(go())


async def fetch(port, path, headers=None, method="GET"):
    """``(status, headers, body)`` of one request; the body is read to EOF (the server closes after each reply)."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    hs = dict({"Host": "127.0.0.1:{}".format(port)}, **(headers or {}))
    w.write("{} {} HTTP/1.1\r\n{}\r\n".format(method, path, "".join("{}: {}\r\n".format(k, v) for k, v in hs.items()))
            .encode())
    await w.drain()
    raw = await asyncio.wait_for(r.read(), 5)
    w.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    out = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        assert k.lower() not in out, "header sent twice: " + k
        out[k.lower()] = v.strip()
    return int(lines[0].split()[1]), out, body


# ---- read limits ----------------------------------------------------------------------------------------------

def test_a_slow_header_read_is_dropped_within_the_timeout(monkeypatch):
    monkeypatch.setattr(server, "READ_TIMEOUT_S", 0.3)

    async def body(web, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n")   # never finishes the block
        await w.drain()
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        data = await asyncio.wait_for(r.read(), 3)                   # EOF means the server closed
        w.close()
        return data, loop.time() - t0

    data, took = run_web(body)
    assert data == b"" and took < 2.0


def test_a_stalled_body_is_dropped(monkeypatch):
    monkeypatch.setattr(server, "READ_TIMEOUT_S", 0.3)

    async def body(web, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("POST /api/sessions HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nX-Exo-Token: {}\r\nContent-Length: 50\r\n\r\n{{"
                .format(port, TOKEN).encode())
        await w.drain()
        data = await asyncio.wait_for(r.read(), 3)
        w.close()
        return data

    assert run_web(body) == b""


def test_too_many_header_lines_are_refused():
    async def body(web, port):
        extra = {"X-H{}".format(i): "v" for i in range(server.MAX_HEADER_LINES + 5)}
        return await fetch(port, "/api/health", dict(AUTH, **extra))

    status, hs, _ = run_web(body)
    assert status == 431 and hs["content-security-policy"] == POLICY


def test_oversized_headers_are_refused():
    async def body(web, port):
        big = {"X-Big{}".format(i): "a" * 3000 for i in range(8)}      # 8 lines, ~24 KB
        return await fetch(port, "/api/health", dict(AUTH, **big))

    assert run_web(body)[0] == 431


def test_a_normal_request_still_passes_the_caps():
    async def body(web, port):
        return await fetch(port, "/api/health", AUTH)

    assert run_web(body)[0] == 200


def test_connections_over_the_cap_get_503():
    async def body(web, port):
        held = []
        for _ in range(2):   # two idle sockets fill the cap of 2 (they send nothing)
            held.append(await asyncio.open_connection("127.0.0.1", port))
        await asyncio.sleep(0.1)
        over = await fetch(port, "/api/health", AUTH)
        for _, w in held:
            w.close()
        return over

    status, hs, _ = run_web(body, max_connections=2)
    assert status == 503 and hs["content-security-policy"] == POLICY


def test_event_streams_outlive_the_read_timeout(monkeypatch):
    monkeypatch.setattr(server, "READ_TIMEOUT_S", 0.2)

    async def body(web, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("GET /api/events?token={} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\r\n".format(TOKEN, port).encode())
        await w.drain()
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 3)
        await asyncio.sleep(0.6)                                       # three read timeouts
        alive = not r.at_eof()
        w.close()
        return head, alive

    head, alive = run_web(body)
    assert head.startswith(b"HTTP/1.1 200") and alive


# ---- static caching -------------------------------------------------------------------------------------------

def test_static_etag_and_304():
    async def body(web, port):
        first = await fetch(port, "/static/markdown.js")
        again = await fetch(port, "/static/markdown.js", {"If-None-Match": first[1]["etag"]})
        other = await fetch(port, "/static/markdown.js", {"If-None-Match": '"nope"'})
        return first, again, other

    first, again, other = run_web(body)
    assert first[0] == 200 and first[1]["cache-control"] == "no-cache"
    assert re.fullmatch(r'"[0-9a-f]+"', first[1]["etag"])
    assert first[2] == (STATIC / "markdown.js").read_bytes()
    assert "content-encoding" not in first[1] and first[1]["vary"] == "Accept-Encoding"
    assert again[0] == 304 and again[2] == b"" and again[1]["etag"] == first[1]["etag"]
    assert again[1]["content-security-policy"] == POLICY and again[1]["x-content-type-options"] == "nosniff"
    assert other[0] == 200


def test_static_gzip_decompresses_to_the_file_bytes():
    async def body(web, port):
        plain = await fetch(port, "/static/style.css")
        gz = await fetch(port, "/static/style.css", {"Accept-Encoding": "br, gzip"})
        revalidated = await fetch(port, "/static/style.css", {"Accept-Encoding": "gzip", "If-None-Match": gz[1]["etag"]})
        refused = await fetch(port, "/static/style.css", {"Accept-Encoding": "gzip;q=0"})
        return plain, gz, revalidated, refused

    plain, gz, revalidated, refused = run_web(body)
    raw = (STATIC / "style.css").read_bytes()
    assert gz[0] == 200 and gz[1]["content-encoding"] == "gzip" and gz[1]["vary"] == "Accept-Encoding"
    assert gzip.decompress(gz[2]) == raw and len(gz[2]) < len(raw)
    assert int(gz[1]["content-length"]) == len(gz[2])
    assert gz[1]["etag"] != plain[1]["etag"] and gz[1]["content-security-policy"] == POLICY
    assert revalidated[0] == 304
    assert "content-encoding" not in plain[1] and plain[2] == raw
    assert "content-encoding" not in refused[1]


def test_static_cache_follows_the_file(monkeypatch, tmp_path):
    f = tmp_path / "a.js"
    f.write_text("var a = 1;\n" * 100)
    web = WebServer(CommandCentre(load_settings({}), Src()), token=TOKEN)
    first = web._cached(f, True)
    assert web._cached(f, True) is not None and web._static[str(f)][3] is first[1]   # served from memory
    f.write_text("var a = 2;\n" * 100)
    assert web._cached(f, True)[0] != first[0]


def test_page_and_api_stay_no_store():
    async def body(web, port):
        return [await fetch(port, "/", {"Accept-Encoding": "gzip"}), await fetch(port, "/api/health", AUTH)]

    for status, hs, _ in run_web(body):
        assert status == 200 and hs["cache-control"] == "no-store"
        assert "etag" not in hs and "content-encoding" not in hs
        assert hs["content-security-policy"] == POLICY


# ---- /api/health ----------------------------------------------------------------------------------------------

def test_health_has_no_userinfo_and_no_paths(tmp_path):
    settings = load_settings({
        "url": "http://user:hunter2-pass@example.invalid:7341/base",
        "socket": str(tmp_path / "run" / "exod.sock"),
        "principal_store": str(tmp_path / "store"),
        "decisions_queue": str(tmp_path / "q" / "decisions.jsonl"),
    })

    class Down(Src):
        async def call(self, body):
            raise ConnectionError("cannot reach http://user:hunter2-pass@example.invalid:7341 via {}/run/exod.sock"
                                  .format(tmp_path))

    async def body(web, port):
        await web.centre.refresh() if hasattr(web.centre, "refresh") else None
        web.centre.last_error = "dial http://u:hunter2-pass@example.invalid/x failed at {}/run/exod.sock".format(tmp_path)
        return await fetch(port, "/api/health", AUTH)

    status, _, raw = run_web(body, src=Down(), settings=settings)
    assert status == 200
    text = raw.decode()
    health = json.loads(text)
    assert "hunter2-pass" not in text and str(tmp_path) not in text and "exod.sock" not in text
    assert health["settings"]["url"] == "http://example.invalid:7341/base"
    assert not re.search(r'"/[^"\s]*/[^"]*"', text), text            # no absolute path anywhere
    assert "[path]" in health["error"] and "u:" not in health["error"]
    assert "token" not in health["settings"] and TOKEN not in text


def test_public_settings_drop_paths_and_userinfo():
    pub = Settings(url="https://a:b@example.org/x", socket="/var/run/x.sock", principal_store="/home/x/s").public()
    assert pub["url"] == "https://example.org/x"
    for k in ("socket", "principal_store", "decisions_queue"):
        assert k not in pub


# ---- core error redaction -------------------------------------------------------------------------------------

def test_core_error_message_is_redacted_and_capped(monkeypatch):
    secret = "zk-0123456789-not-a-real-key"
    monkeypatch.setenv("EXO_TEST_API_KEY", secret)
    status, out = api.error_reply(SourceError("bad_model", "provider said key {} is invalid".format(secret)))
    assert status == 400 and secret not in out["error"] and "[redacted]" in out["error"]
    status, out = api.error_reply(SourceError("boom", "x" * 5000))
    assert status == 502 and len(out["error"]) == 500


def test_core_error_over_http_is_redacted(monkeypatch):
    secret = "zk-9876543210-not-a-real-key"
    monkeypatch.setenv("EXO_TEST_API_KEY", secret)

    class Failing(Src):
        async def call(self, body):
            if type(body).TYPE == "session.list":
                raise SourceError("bad_request", "upstream echoed " + secret)
            return await super().call(body)

    async def body(web, port):
        return await fetch(port, "/api/chat/sessions", AUTH)

    status, _, raw = run_web(body, src=Failing())
    assert status == 400 and secret.encode() not in raw


# ---- non-loopback bind ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("host,warns", [("127.0.0.1", False), ("localhost", False), ("::1", False),
                                        ("0.0.0.0", True), ("192.0.2.1", True)])
def test_non_loopback_bind_logs_one_warning(host, warns, capsys, caplog):
    assert server._is_loopback_bind(host) is not warns
    if not warns:
        return

    async def go():
        web = WebServer(CommandCentre(load_settings({}), Src()), token=TOKEN)
        try:
            await web.start(host, 0)
        except OSError:
            pass   # a documentation address cannot be bound here; the warning is logged before the bind result
        finally:
            await web.close()
    asyncio.run(go())
    assert capsys.readouterr().err.count("plain HTTP") == 1


@pytest.mark.parametrize("action", ["close", "rotate"])
def test_idle_overflow_is_owned_and_drained(action, monkeypatch):
    monkeypatch.setattr(server, "OVERFLOW_S", 30)

    async def body(web, port):
        peers = [await asyncio.open_connection("127.0.0.1", port) for _ in range(2)]
        try:
            # Both sockets must have reached acceptance before exercising lifecycle.
            for _ in range(100):
                if len(web._conns) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(web._conns) == 2
            if action == "close":
                await asyncio.wait_for(web.close(), 1)
            else:
                web.rotate()
            assert await asyncio.wait_for(peers[0][0].read(), 1) == b""
            assert await asyncio.wait_for(peers[1][0].read(), 1) == b""
            await asyncio.sleep(0)
            assert not web._conns and not web._overflow_conns
        finally:
            for _, writer in peers:
                writer.close()

    run_web(body, max_connections=1)


def test_idle_overflow_admission_is_bounded(monkeypatch):
    monkeypatch.setattr(server, "OVERFLOW_S", 30)

    async def body(web, port):
        peers = []
        try:
            for _ in range(8):
                peers.append(await asyncio.open_connection("127.0.0.1", port))
            # Accept callbacks precede EOF for the final rejected client.
            assert await asyncio.wait_for(peers[-1][0].read(), 1) == b""
            assert len(web._conns) == 2 and len(web._overflow_conns) == 1
            # Releasing the ordinary slot admits real work even while overflow waits.
            peers[0][1].close()
            for _ in range(100):
                if len(web._conns) == 1:
                    break
                await asyncio.sleep(0.01)
            assert (await fetch(port, "/api/health", AUTH))[0] == 200
            await asyncio.wait_for(web.close(), 1)
            assert await asyncio.wait_for(peers[1][0].read(), 1) == b""
        finally:
            for _, writer in peers:
                writer.close()

    run_web(body, max_connections=1)
