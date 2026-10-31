"""#166: `exo web` opens the browser on a one-time launch code, never on the page token, because a launcher's argv is
readable by other users of the host in /proc. The code is traded once for the token over loopback (`/api/launch`); a
used code that comes back replaces the token. The printed `#token=` link is unchanged. Raw HTTP so every header is
the test's; the last test drives the real page in headless Chrome."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from browser import Browser, Served, find_chrome
from exo_command import providers as prov
from exo_command import server
from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre

TOKEN = "page-token-for-launch-tests-0123456789"
NODE = shutil.which("node")


class Src:
    centre = None

    async def call(self, body):
        t = type(body).TYPE
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": []}
        if t == "config.get":
            return {"key": body.key, "value": None}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


async def raw(port, method, path, headers=None, body=None):
    """Status, headers and decoded JSON body of one request."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else b""
    hs = dict({"Host": "127.0.0.1:{}".format(port)}, **(headers or {}))
    head = "".join("{}: {}\r\n".format(k, v) for k, v in hs.items() if v is not None)
    w.write("{} {} HTTP/1.1\r\n{}Content-Length: {}\r\n\r\n".format(method, path, head, len(data)).encode() + data)
    await w.drain()
    got = await asyncio.wait_for(r.read(), 5)
    w.close()
    top, _, payload = got.partition(b"\r\n\r\n")
    try:
        parsed = json.loads(payload)
    except ValueError:
        parsed = None
    return int(top.split()[1]), top.decode("latin-1").lower(), parsed


async def held_stream(port, path, token):
    """Open an event stream and keep it: the reader returns at EOF when the server ends it."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write("GET {}?token={} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\r\n".format(path, token, port).encode())
    await w.drain()
    head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    assert int(head.split()[1]) == 200
    return r, w


def code_of(url):
    frag = urlsplit(url).fragment
    assert frag.startswith("code=") and "&" not in frag
    return frag[len("code="):]


def _web():
    return WebServer(CommandCentre(load_settings({}), Src()), token=TOKEN)


def test_the_browser_gets_a_one_time_code_and_the_printed_link_is_unchanged(monkeypatch, capsys):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(prov.ps, "any_ready", lambda: True)

    async def go():
        web = _web()
        web.open_browser = True
        task = asyncio.create_task(web.run("127.0.0.1", 0))
        for _ in range(200):
            if opened:
                break
            await asyncio.sleep(0.01)
        try:
            port = urlsplit(opened[0]).port
            first = await raw(port, "POST", "/api/launch", body={"code": code_of(opened[0])})
            return web, port, first
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    web, port, first = asyncio.run(go())
    assert len(opened) == 1
    url = opened[0]
    assert TOKEN not in url and "token" not in url.lower()            # no long-lived secret in the launcher's argv
    assert url.startswith("http://127.0.0.1:{}/#code=".format(port))
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "exo-command: http://127.0.0.1:{}/#token={}".format(port, TOKEN)
    assert code_of(url) not in out                                    # the code is never printed
    assert first[0] == 200 and first[2] == {"token": TOKEN}
    assert web.page_url("127.0.0.1", port) == "http://127.0.0.1:{}/#token={}".format(port, TOKEN)


def test_a_non_loopback_bind_opens_no_browser_and_says_why(monkeypatch, capsys):
    """/api/launch answers loopback only, so a LAN bind would open a page that cannot start; the token link must
    not go to the launcher either. The printed link is the way in."""
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(prov.ps, "any_ready", lambda: True)

    async def go():
        web = _web()
        web.open_browser = True

        async def start(host, port):   # a test host has no LAN address to bind: listen on loopback, show the LAN one
            bound = await WebServer.start(web, "127.0.0.1", 0)
            web._shown = (host, bound)
            return bound
        web.start = start
        task = asyncio.create_task(web.run("192.0.2.2", 0))
        out = ""
        for _ in range(200):
            out += capsys.readouterr().out
            if "not opening a browser" in out:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return web, out + capsys.readouterr().out

    web, out = asyncio.run(go())
    assert opened == [] and web._codes == {}                    # no launcher, no code made
    lines = out.splitlines()
    assert lines[0] == "exo-command: http://192.0.2.2:{}/#token={}".format(web._shown[1], TOKEN)
    assert any("not opening a browser" in x and "127.0.0.1, localhost, ::1 or a wildcard bind" in x for x in lines)
    assert sum(TOKEN in x for x in lines) == 1                  # only the link line holds the token


def test_each_launch_makes_a_fresh_code():
    web = _web()
    a, b = code_of(web.launch_url("127.0.0.1", 7342)), code_of(web.launch_url("127.0.0.1", 7342))
    assert a != b and len(a) >= 40
    assert web.redeem(a) == (200, {"token": TOKEN}) and web.redeem(b) == (200, {"token": TOKEN})


def test_a_code_redeems_once_and_a_second_try_replaces_the_token(capsys):
    async def go():
        web = _web()
        port = await web.start("127.0.0.1", 0)
        try:
            code = code_of(web.launch_url("127.0.0.1", port))
            first = await raw(port, "POST", "/api/launch", body={"code": code})
            old_ok = (await raw(port, "GET", "/api/health", {"X-Exo-Token": TOKEN}))[0]
            events = await held_stream(port, "/api/events", TOKEN)
            seqs = await held_stream(port, "/api/stream", TOKEN)
            capsys.readouterr()
            second = await raw(port, "POST", "/api/launch", body={"code": code})
            new = web.token
            # both streams opened with the old token end at once
            ended = []
            for r, _ in (events, seqs):
                await asyncio.wait_for(r.read(), 5)  # read() with no size returns only at end of stream
                ended.append(r.at_eof())
            for _, w in (events, seqs):
                w.close()
            after = {
                "old api": (await raw(port, "GET", "/api/health", {"X-Exo-Token": TOKEN}))[0],
                "old providers": (await raw(port, "GET", "/api/providers", {"X-Exo-Token": TOKEN}))[0],
                "old files": (await raw(port, "GET", "/api/complete/files", {"X-Exo-Token": TOKEN}))[0],
                "new api": (await raw(port, "GET", "/api/health", {"X-Exo-Token": new}))[0],
                "new files": (await raw(port, "GET", "/api/complete/files?q=", {"X-Exo-Token": new}))[0],
            }
            old_stream = (await raw(port, "GET", "/api/events?token=" + TOKEN))[0]
            r, w = await held_stream(port, "/api/events", new)   # asserts 200
            w.close()
            third = await raw(port, "POST", "/api/launch", body={"code": code})
            return web, new, first, old_ok, second, ended, after, old_stream, third
        finally:
            await web.close()

    web, new, first, old_ok, second, ended, after, old_stream, third = asyncio.run(go())
    assert first[0] == 200 and first[2] == {"token": TOKEN}
    assert "cache-control: no-store" in first[1]
    assert old_ok == 200
    assert second[0] == 403 and "token" not in second[2] and "already used" in second[2]["error"]
    assert "cache-control: no-store" in second[1]
    assert new != TOKEN and len(new) >= 40
    assert ended == [True, True]
    assert after["old api"] == after["old providers"] == after["old files"] == 403, after
    assert after["new api"] == 200 and after["new files"] != 403, after
    assert old_stream == 403
    # the terminal is told, with the new link; nothing else carries a secret
    out = capsys.readouterr().out
    assert "warning" in out and "used twice" in out
    assert out.splitlines()[-1] == "exo-command: {}".format(web.page_url("127.0.0.1", web._shown[1]))
    assert TOKEN not in out
    # a third try is refused too, and does not replace the token again
    assert third[0] == 403 and "token" not in third[2] and web.token == new


class Recording(Src):
    def __init__(self):
        self.calls = []

    async def call(self, body):
        self.calls.append(type(body).TYPE)
        return await super().call(body)


# Requests that act: each one names the token in its headers, then waits for its body.
HELD = [("/api/approvals/r1", {"decision": "allow_always"}),
        ("/api/chat/sessions/s1/send", {"text": "the attacker's prompt"}),
        ("/api/providers/anthropic/key", {"key": "sk-attacker"})]


async def held_posts(web, port):
    """Open the HELD POSTs with the current token, headers only, and return once each has passed the token check."""
    passed, refuse = [], web._refuse
    web._refuse = lambda *a: passed.append(a[1]) or refuse(*a)
    conns = []
    for path, _ in HELD:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write("POST {} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nX-Exo-Token: {}\r\nContent-Length: 200\r\n\r\n"
                .format(path, port, web.token).encode())
        await w.drain()
        conns.append((r, w))
    for _ in range(500):
        if len(passed) == len(HELD):
            break
        await asyncio.sleep(0.01)
    assert sorted(passed) == sorted(p for p, _ in HELD)
    await asyncio.sleep(0.05)   # each is now waiting in readexactly() for its body
    return conns


async def finish(conns):
    """Send each held body and return the raw answer (b"" when the server ended the connection instead)."""
    got = []
    for (r, w), (_, body) in zip(conns, HELD):
        try:
            w.write(json.dumps(body).encode().ljust(200))
            await w.drain()
            got.append(await asyncio.wait_for(r.read(), 5))
        except ConnectionError:
            got.append(b"")
        w.close()
    return got


def test_replacing_the_token_ends_requests_that_passed_the_old_one(monkeypatch):
    """A request that sent its headers with the old token before the replacement must not act when its body arrives
    after it: no approval answered, no turn sent, no key stored."""
    handled = []

    async def handle(*a, **k):
        handled.append(a[2])
        return 200, {"ok": True}
    monkeypatch.setattr(prov, "handle", handle)

    async def go():
        src = Recording()
        web = WebServer(CommandCentre(load_settings({}), src), token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            code = code_of(web.launch_url("127.0.0.1", port))
            first = await raw(port, "POST", "/api/launch", body={"code": code})
            conns = await held_posts(web, port)
            second = await raw(port, "POST", "/api/launch", body={"code": code})
            got = await finish(conns)
            return web, src, first, second, got
        finally:
            await web.close()

    web, src, first, second, got = asyncio.run(go())
    assert first[2] == {"token": TOKEN} and second[0] == 403 and web.token != TOKEN
    assert got == [b""] * len(HELD), got      # each connection ended without an answer
    assert handled == [] and not {"approval.respond", "turn.send"} & set(src.calls), (handled, src.calls)


def test_a_body_is_checked_against_the_current_token(monkeypatch):
    """Defence in depth: the token is checked again once the body is read, so a request that slipped past the
    replacement is refused, not run."""
    handled = []

    async def handle(*a, **k):
        handled.append(a[2])
        return 200, {"ok": True}
    monkeypatch.setattr(prov, "handle", handle)

    async def go():
        src = Recording()
        web = WebServer(CommandCentre(load_settings({}), src), token=TOKEN)
        port = await web.start("127.0.0.1", 0)
        try:
            conns = await held_posts(web, port)
            web.token = "a-new-token-set-without-ending-connections"
            return src, await finish(conns)
        finally:
            await web.close()

    src, got = asyncio.run(go())
    for answer in got:
        top, _, payload = answer.partition(b"\r\n\r\n")
        assert top.startswith(b"HTTP/1.1 403 "), answer
        assert "page token" in json.loads(payload)["error"]
    assert handled == [] and not {"approval.respond", "turn.send"} & set(src.calls), (handled, src.calls)


def test_an_expired_or_unknown_code_is_refused_without_replacing_the_token(monkeypatch):
    async def go():
        web = _web()
        port = await web.start("127.0.0.1", 0)
        try:
            monkeypatch.setattr(server, "LAUNCH_TTL", 0.0)
            stale = code_of(web.launch_url("127.0.0.1", port))
            monkeypatch.setattr(server, "LAUNCH_TTL", 120.0)
            got = {
                "expired": await raw(port, "POST", "/api/launch", body={"code": stale}),
                "expired again": await raw(port, "POST", "/api/launch", body={"code": stale}),
                "unknown": await raw(port, "POST", "/api/launch", body={"code": "x" * 43}),
                "empty": await raw(port, "POST", "/api/launch", body={"code": ""}),
                "number": await raw(port, "POST", "/api/launch", body={"code": 7}),
                "no body": await raw(port, "POST", "/api/launch"),
                "bad json": await raw(port, "POST", "/api/launch", body=b"{nope"),
                "list": await raw(port, "POST", "/api/launch", body=[stale]),
                "too big": await raw(port, "POST", "/api/launch", body={"code": "y" * 2000}),
            }
            get = (await raw(port, "GET", "/api/launch"))[0]
            return web, got, get
        finally:
            await web.close()

    web, got, get = asyncio.run(go())
    assert {k: v[0] for k, v in got.items()} == {k: 403 for k in got}
    assert all(v[2] is not None and "token" not in v[2] and v[2].get("error") for v in got.values()), got
    assert got["expired"][2]["error"] == "launch code expired"
    assert get == 405
    assert web.token == TOKEN


def test_redemption_passes_every_other_guard_check():
    async def go():
        web = _web()
        port = await web.start("127.0.0.1", 0)
        try:
            code = code_of(web.launch_url("127.0.0.1", port))
            body = {"code": code}
            here = "127.0.0.1:{}".format(port)
            got = {
                "cross origin": await raw(port, "POST", "/api/launch", {"Origin": "http://evil.example"}, body),
                "rebind origin": await raw(port, "POST", "/api/launch",
                                           {"Origin": "http://rebind.example:{}".format(port)}, body),
                "cross site": await raw(port, "POST", "/api/launch", {"Sec-Fetch-Site": "cross-site"}, body),
                "same site": await raw(port, "POST", "/api/launch", {"Sec-Fetch-Site": "same-site"}, body),
                "rebind host": await raw(port, "POST", "/api/launch", {"Host": "rebind.example:{}".format(port)}, body),
                "other port": await raw(port, "POST", "/api/launch", {"Host": "127.0.0.1:{}".format(port + 1)}, body),
                "no host": await raw(port, "POST", "/api/launch", {"Host": None}, body),
            }
            # none of those burned the code: the page's own request still gets the token
            good = await raw(port, "POST", "/api/launch", {"Origin": "http://" + here, "Sec-Fetch-Site": "same-origin"},
                             body)
            return web, got, good
        finally:
            await web.close()

    web, got, good = asyncio.run(go())
    assert {k: v[0] for k, v in got.items()} == {k: 403 for k in got}
    assert all("token" not in (v[2] or {}) for v in got.values())
    assert good[0] == 200 and good[2] == {"token": TOKEN}
    assert web.token == TOKEN
    # the peer check: guard_peer is guard without the token, so a non-loopback client is refused
    h = {"host": "127.0.0.1:7342"}
    assert prov.guard_peer(h, "192.168.1.5")[0] == 403 and prov.guard_peer(h, None)[0] == 403
    assert prov.guard_peer(h, "127.0.0.1") is None
    assert prov.guard_peer({"host": "evil.example:7342"}, "127.0.0.1")[0] == 403


class PeerAs:
    """A server-side writer that reports another peer address; everything else is the real connection's."""

    def __init__(self, writer, addr):
        self._w, self._addr = writer, addr

    def get_extra_info(self, name, default=None):
        return (self._addr, 50000) if name == "peername" else self._w.get_extra_info(name, default)

    def __getattr__(self, name):
        return getattr(self._w, name)


def test_the_launch_route_checks_its_own_peer_and_host(monkeypatch):
    """The requests here pass `_refuse` (their Host is in `_hosts`), so only `_launch`'s own `guard_peer` stands
    between them and the token: a peer off loopback, and a loopback peer naming the LAN address of a `--host` bind."""
    listen = asyncio.start_server
    # a test host has no LAN address to bind: the real start() names 192.0.2.2 in _hosts, the socket is on loopback
    monkeypatch.setattr(asyncio, "start_server", lambda cb, host, port: listen(cb, "127.0.0.1", port))

    async def go():
        web = _web()
        peer = {"addr": None}
        web._request = lambda r, w: WebServer._request(web, r, PeerAs(w, peer["addr"]) if peer["addr"] else w)
        port = await web.start("192.0.2.2", 0)
        try:
            body = {"code": code_of(web.launch_url("127.0.0.1", port))}
            loop_host, lan_host = {"Host": "127.0.0.1:{}".format(port)}, {"Host": "192.0.2.2:{}".format(port)}
            assert lan_host["Host"] in web._hosts
            peer["addr"] = "192.0.2.7"
            got = {"lan peer": await raw(port, "POST", "/api/launch", loop_host, body),
                   "lan peer, lan host": await raw(port, "POST", "/api/launch", lan_host, body)}
            peer["addr"] = None
            got["lan host"] = await raw(port, "POST", "/api/launch", lan_host, body)
            good = await raw(port, "POST", "/api/launch", loop_host, body)
            return web, got, good
        finally:
            await web.close()

    web, got, good = asyncio.run(go())
    assert {k: (v[0], v[2]) for k, v in got.items()} == {
        "lan peer": (403, {"error": "this route answers loopback clients only"}),
        "lan peer, lan host": (403, {"error": "this route answers loopback clients only"}),
        "lan host": (403, {"error": "bad Host header: open the page at 127.0.0.1 or localhost"}),
    }
    # none of those burned the code
    assert good[0] == 200 and good[2] == {"token": TOKEN}
    assert web.token == TOKEN


# ---------------------------------------------------------------- app.js in Node, offline
@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_the_page_strips_the_code_before_it_sends_it(tmp_path):
    static = server.STATIC
    (tmp_path / "app.mjs").write_text((static / "app.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "desktop_prefs.mjs").write_text((static / "desktop_prefs.js").read_text())
    (tmp_path / "appearance.mjs").write_text((static / "appearance.js").read_text())
    (tmp_path / "dom.mjs").write_text('''
export const token=()=>sessionStorage.getItem("xc-token") || "";
export const api=async()=>({});
export const redeem=async(code)=>{globalThis.sent.push([code, location.hash]);return code==="good" ? {token:"the-token"}
  : code==="host" ? {error:"bad Host header: open the page at the address `exo web` printed"}
  : {error:"launch code already used: the page token was replaced"};};
export const h=(tag,attrs,...children)=>({tag,attrs,children,classList:{remove(){},add(){}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},replaceChildren(...c){this.children=c;}});
''')
    (tmp_path / "bus.mjs").write_text('export const onState=f=>f({connected:false});export const start=async()=>{};'
                                      'export const restart=async()=>{globalThis.restarted++;};')
    for name in ("chat", "tasks", "schedule", "command", "providers"):
        (tmp_path / f"{name}.mjs").write_text('export const mount=()=>{globalThis.mounted++;return ()=>{};};')
    (tmp_path / "permissions.mjs").write_text('export const banner=()=>({hidden:true});export const start=()=>{};')
    (tmp_path / "test.mjs").write_text(r'''
import assert from "node:assert/strict";
let stored={};globalThis.sessionStorage={getItem:k=>stored[k],setItem:(k,v)=>stored[k]=v};
globalThis.location={hash:"#code=good&tasks",pathname:"/",search:""};
globalThis.history={replaceState:(a,b,u)=>{location.hash=u.slice(u.indexOf("#"));}};
globalThis.sent=[];globalThis.restarted=0;globalThis.mounted=0;
const app={children:[],replaceChildren(...c){this.children=c;},appendChild(){}}, handlers={};
globalThis.document={getElementById:()=>app};globalThis.window={matchMedia:()=>({matches:true}),addEventListener:(t,f)=>handlers[t]=f};
await import("./app.mjs");
assert.deepEqual(sent,[["good","#tasks"]]);              // out of the address bar before the request
assert.equal(stored["xc-token"],"the-token");assert.equal(location.hash,"#tasks");assert.equal(mounted,1);
stored={};location.hash="#code=used&chat";await handlers.hashchange();
assert.deepEqual(sent.at(-1),["used","#chat"]);assert.equal(stored["xc-token"],undefined);assert.equal(restarted,0);
const main=app.children.at(-1);assert.match(main.children[0].children.join(""),/already used or has expired\. Paste the link/);
location.hash="#code=host&chat";await handlers.hashchange();   // a refusal that is not about the code names its reason
assert.match(main.children[0].children.join(""),/refused \(bad Host header/);assert.equal(stored["xc-token"],undefined);
location.hash="#token=fresh&chat";await handlers.hashchange();
assert.equal(stored["xc-token"],"fresh");assert.equal(restarted,1);assert.equal(sent.length,3);
stored={};location.hash="#code=good&schedule";await handlers.hashchange();   // a good code while the page is open
assert.deepEqual(sent.at(-1),["good","#schedule"]);assert.equal(stored["xc-token"],"the-token");
assert.equal(restarted,2);assert.equal(location.hash,"#schedule");
console.log("ok");
''')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# ---------------------------------------------------------------- the real page in headless Chrome
needs_chrome = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")
READY = "!!document.querySelector('.xc-nav [aria-current=page]') && !document.querySelector('.xc-main > .xc-loading')"


@needs_chrome
def test_the_page_trades_a_launch_code_and_survives_a_reload():
    srv = Served(Src())
    try:
        with Browser() as b:
            code = code_of(srv.call(srv.web.launch_url, "127.0.0.1", srv.port))
            b.goto("http://127.0.0.1:{}/?boot=0#code={}".format(srv.port, code))
            b.wait(READY)
            assert b.eval("sessionStorage.getItem('xc-token')") == srv.token
            assert "code" not in b.eval("location.href") and code not in b.eval("location.href")
            b.call("Page.reload")
            b.wait(READY)
            assert "code" not in b.eval("location.href")
            assert b.eval("sessionStorage.getItem('xc-token')") == srv.token
            assert srv.web.token == srv.token                         # the reload did not send the code again
            # a code that gives nothing: the tab says why instead of showing a view
            b.eval("sessionStorage.clear()")
            b.goto("http://127.0.0.1:{}/?boot=0#code={}".format(srv.port, "z" * 43))
            msg = b.wait("(()=>{const text=(document.querySelector('.xc-main [role=status]') || {}).textContent || '';return text.includes('already used or has expired') && text;})()")
            assert "already used or has expired" in msg and "Paste the link exo web printed" in msg
            assert "into this tab" in msg and "Open the link" not in msg   # a click hands it to a launcher (#166)
            assert "code" not in b.eval("location.href") and not b.eval("sessionStorage.getItem('xc-token')")
            assert srv.web.token == srv.token
    finally:
        srv.close()
