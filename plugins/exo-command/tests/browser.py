"""A tiny headless-Chrome driver for the web client tests and the screenshot script (D-046).

Chrome is started with a debugging port and driven over the DevTools protocol with the `websockets` package, so no
browser-automation dependency is added. Tests skip when Chrome or `websockets` is missing. Nothing here touches a real
core: the page is served by `WebServer` over a stand-in source.
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def find_chrome() -> Optional[str]:
    env = os.environ.get("CHROME")
    if env and Path(env).exists():
        return env
    if Path(MAC).exists():
        return MAC
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


class Served:
    """A `WebServer` running on its own thread and loop, so a blocking browser driver can talk to it."""

    def __init__(self, source: Any, token: str = "test-token", static: Optional[Path] = None):
        from exo_command import server
        from exo_command.config import load_settings
        from exo_command.server import WebServer
        from exo_command.service import CommandCentre
        self.source, self.token, self._static0 = source, token, server.STATIC
        if static is not None:
            server.STATIC = static
        self._server_mod = server
        self.centre = CommandCentre(load_settings({}), source)
        self.centre.connected = True
        source.centre = self.centre
        self.web = WebServer(self.centre, token=token)
        self.loop = asyncio.new_event_loop()
        self.port = 0
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            self.port = self.loop.run_until_complete(self.web.start("127.0.0.1", 0))
            ready.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(10)

    def url(self, view: str = "chat") -> str:
        return f"http://127.0.0.1:{self.port}/?boot=0#token={self.token}&{view}"

    def call(self, fn, *args):
        """Run a function on the server's loop (for example to record an event) and wait for it."""
        done = threading.Event()
        box: list = []

        def go() -> None:
            box.append(fn(*args))
            done.set()

        self.loop.call_soon_threadsafe(go)
        done.wait(10)
        return box[0] if box else None

    def close(self) -> None:
        try:
            fut = asyncio.run_coroutine_threadsafe(self.web.close(), self.loop)
            fut.result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self._server_mod.STATIC = self._static0


class Browser:
    def __init__(self, width: int = 1440, height: int = 900):
        from websockets.sync.client import connect
        chrome = find_chrome()
        if not chrome:
            raise RuntimeError("no Chrome found")
        self.profile = tempfile.mkdtemp(prefix="exo-chrome-")
        self.proc = subprocess.Popen(
            # #907: keep a headless page's timers and frames running at full rate, whatever the runner's window state
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
             "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
             "--disable-renderer-backgrounding",
             f"--window-size={width},{height}", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--use-mock-keychain", "--password-store=basic", "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(self.close)  # never leave a browser behind, even when a test dies
        port_file = Path(self.profile) / "DevToolsActivePort"
        end = time.monotonic() + 20
        while not (port_file.exists() and port_file.read_text().strip()) and time.monotonic() < end:  # written, not only made
            time.sleep(0.05)
        port = port_file.read_text().split()[0]
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10).read())
        page = next(t for t in targets if t.get("type") == "page")
        self._cm = connect(page["webSocketDebuggerUrl"], max_size=None)
        self.ws = self._cm.__enter__()
        self.n = 0
        self.call("Page.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)

    def call(self, method: str, **params: Any) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)

    def eval(self, js: str) -> Any:
        r = self.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "js error"))
        return r["result"].get("value")

    def wait(self, js: str, timeout: float = 10.0) -> Any:
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            try:
                last = self.eval(js)
            except RuntimeError:
                last = None
            if last:
                return last
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for: {js} (last {last!r})")

    def type(self, selector: str, text: str) -> None:
        """Focus an element and type into it like a person (key events, so the page's handlers run)."""
        self.eval(f"document.querySelector({json.dumps(selector)}).focus()")
        self.call("Input.insertText", text=text)

    def press(self, key: str, code: str = "", vk: int = 0, modifiers: int = 0) -> None:
        for kind in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=kind, key=key, code=code or key, windowsVirtualKeyCode=vk,
                      modifiers=modifiers)

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        Path(path).write_bytes(base64.b64decode(data))

    def close(self) -> None:
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
