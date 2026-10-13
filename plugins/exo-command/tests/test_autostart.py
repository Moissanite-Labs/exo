"""D-036: the web client finds or starts the core and never shows a raw ConnectError."""
import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from exo_command.config import load_settings
from exo_command.server import WebServer
from exo_command.service import CommandCentre
from exo_command.source import core_message


@pytest.fixture
def home(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="exo-w-", dir="/tmp"))
    monkeypatch.setenv("EXO_HOME", str(d))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_connect_error_becomes_a_sentence():
    import httpx
    for exc in (httpx.ConnectError("[Errno 2] No such file or directory"), FileNotFoundError(2, "x")):
        msg = core_message(exc)
        assert "ConnectError" not in msg and "Errno" not in msg and "exo serve" in msg


def test_default_socket_means_auto_core(home):
    c = CommandCentre(load_settings())
    c.connect()
    assert c.auto_core is True
    c2 = CommandCentre(load_settings({"socket": str(home / "x.sock")}))
    c2.connect()
    assert c2.auto_core is False


def test_web_start_with_no_core_spawns_one_and_attaches(home, monkeypatch):
    monkeypatch.setenv("EXO_ENGINE", "echo")
    from exo import ensure_core as ensure

    async def go():
        c = CommandCentre(load_settings())
        c.connect()
        seen = []
        c._notify = lambda: seen.append(c.last_error)
        try:
            await c.ensure_core()
            await c.bootstrap()
            assert c.connected and c.last_error is None
            assert any(e and "starting it" in e for e in seen)
            assert c.source.socket == str(ensure.exod_socket(home))
        finally:
            await c.aclose()
            ensure.stop_core(home)

    asyncio.run(go())


def test_unreachable_core_shows_actionable_text(home):
    async def go():
        c = CommandCentre(load_settings({"socket": str(home / "none.sock")}))
        _web = WebServer(c)
        c.connect()
        with pytest.raises(Exception) as e:
            await c.bootstrap()
        return core_message(e.value)

    msg = asyncio.run(go())
    assert "Errno" not in msg and "ConnectError" not in msg
