"""D-065 in the web client: the permissions routes, the /permissions command and the bypass banner on every page.
The banner test drives headless Chrome and skips without it."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from browser import Browser, Served, find_chrome
from exo.protocol import schema as s
from exo_command import api
from exo_command.config import load_settings
from exo_command.service import CommandCentre
from exo_command.source import DispatcherSource, SourceError

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"
REPO = Path(__file__).resolve().parents[3]


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


class PermSource:
    """Answers command.run permissions like the gate does, and records what was asked."""

    centre = None

    def __init__(self, mode="ask", refuse=None, missing=False):
        self.state = {"mode": mode, "available": True, "auto_approved": 4 if mode == "bypass" else 0}
        self.refuse, self.missing, self.calls = refuse, missing, []

    async def call(self, body):
        t = type(body).TYPE
        self.calls.append(body)
        if t == "hello":
            return {"seq": 0}
        if t == "events.subscribe":
            return {"events": [], "seq": 0}
        if t == "session.list":
            return {"sessions": []}
        if t == "config.get":
            return {"key": body.key, "value": None}
        if t == "command.run" and body.name == "permissions":
            if self.missing:
                raise SourceError("not_found", "no command /permissions")
            if body.args == "json":
                return {"output": dict(self.state)}
            if self.refuse:
                raise SourceError(self.refuse, "only the principal can turn on permissions bypass")
            self.state["mode"] = body.args
            return {"output": "permissions: " + body.args}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


def call(src, method, body=None):
    c = CommandCentre(load_settings({}), src)
    return asyncio.run(api.dispatch(c, method, "/api/permissions", {}, body or {}))


def test_get_reports_the_mode():
    assert call(PermSource("bypass"), "GET") == (200, {"mode": "bypass", "available": True, "auto_approved": 4})


def test_a_core_without_the_gate_reads_as_ask():
    status, data = call(PermSource(missing=True), "GET")
    assert status == 200 and data["mode"] == "ask"


def test_post_switches_and_returns_the_new_state():
    src = PermSource()
    status, data = call(src, "POST", {"mode": "bypass"})
    assert status == 200 and data["permissions"]["mode"] == "bypass"
    assert [b.args for b in src.calls if b.TYPE == "command.run"] == ["bypass", "json"]


@pytest.mark.parametrize("mode", ["", "yolo", "bypass launch", None])
def test_post_takes_only_bypass_or_ask(mode):
    src = PermSource()
    assert call(src, "POST", {"mode": mode})[0] == 400
    assert not [b for b in src.calls if b.TYPE == "command.run"]


def test_a_core_refusal_is_a_403():
    status, data = call(PermSource(refuse="forbidden"), "POST", {"mode": "bypass"})
    assert status == 403 and data["code"] == "forbidden"


def test_the_web_page_is_the_principal_against_a_real_gate(tmp_path):
    """In process the web answers as principal:web, so its switch is accepted; a local dispatcher's is not."""
    from exo.core.engine import CoreEngine
    from exo.core.plugins import PluginManager
    from exo.server.app import Dispatcher

    pm = PluginManager(config={"exo-gate": {"decision_model": False}})
    assert pm.load_folder(REPO / "plugins" / "exo-gate").ok, pm.errors()
    eng = CoreEngine(tmp_path / "home", {"fake": {"reply": "ok"}, "roles": {"main": ["fake:x"], "aux": ["fake:x"]}},
                     plugins=pm, cwd=str(tmp_path), env={})
    try:
        class LocalSource(DispatcherSource):
            async def call(self, body):
                resp = await self.dispatcher.run(s.Command.of(body), "local")
                if not resp.ok:
                    raise SourceError(resp.error.code, resp.error.message)
                return resp.result

        assert call(LocalSource(Dispatcher(eng)), "POST", {"mode": "bypass"})[0] == 403
        assert call(DispatcherSource(Dispatcher(eng)), "GET")[1]["mode"] == "ask"
        status, data = call(DispatcherSource(Dispatcher(eng)), "POST", {"mode": "bypass"})
        assert status == 200 and data["permissions"]["mode"] == "bypass" and data["permissions"]["by"] == "principal:web"
    finally:
        asyncio.run(eng.close())


def test_static_wiring():
    app, perm, chat, css = text("app.js"), text("permissions.js"), text("chat.js"), text("style.css")
    assert 'import * as permissions from "./permissions.js"' in app and "app.replaceChildren(nav, permBanner, main)" in app
    assert '"permissions: bypass"' in perm and "except changes to Exo's own records and reading secrets" in perm
    assert 'command.name === "permissions"' in chat
    assert ".xc-perm {" in css and ".xc-perm[hidden] { display: none; }" in css
    assert "permissions" in api.WEB_COMMANDS


# ---------------------------------------------------------------- the real page in headless Chrome
needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"),
                                  reason="headless Chrome or the websockets package is missing")
BANNER = "(()=>{const b=document.querySelector('.xc-perm');return b && !b.hidden ? b.textContent : ''})()"


@needs_chrome
@pytest.mark.parametrize("view", ["chat", "tasks", "providers"])
def test_banner_shows_on_every_page_while_bypass_is_on(view):
    src = PermSource("bypass")
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url(view))
            got = b.wait(BANNER)
            assert got.startswith("permissions: bypass") and "4 auto-approved" in got
    finally:
        srv.close()


@needs_chrome
def test_no_banner_in_ask_and_the_command_turns_it_on():
    src = PermSource("ask")
    srv = Served(src)
    try:
        with Browser() as b:
            b.goto(srv.url())
            b.wait("document.querySelector('.xc-perm') !== null")
            b.eval("new Promise((r) => setTimeout(r, 300))")
            assert b.eval(BANNER) == ""
            b.eval("(()=>{const i=document.querySelector('.xc-input');i.value='/permissions bypass';"
                   "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()")
            assert b.wait(BANNER).startswith("permissions: bypass")
            assert src.state["mode"] == "bypass"
    finally:
        srv.close()
