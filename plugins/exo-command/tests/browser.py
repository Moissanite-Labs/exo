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
START_TIMEOUT = 20.0
# #907: the first Chrome start in a test run can be far slower than the ones after it. On a fresh hosted runner it
# reads the browser and its libraries from a cold disk. The two hosted DevToolsActivePort timeouts on record (#1036's
# ubuntu job, reported in #907, and #1092's) were each the job's first real start, in test_csp_web.py, out of many
# starts in each job. Like the 90 s for the first replies in Browser._start, this is a wait for Chrome to start, not
# a bound any test asserts.
FIRST_START_TIMEOUT = 90.0
_started = False  # set once a Chrome in this process has written DevToolsActivePort
# The first DevTools answers can wait for the new tab's renderer to start. On a loaded macOS runner one of them
# (Emulation.setDeviceMetricsOverride) once took more than the 30 s an ordinary command gets.
FIRST_REPLY_TIMEOUT = 90.0
# #907: on hosted macOS runners Chrome has twice started and then not answered one of its first DevTools calls within
# FIRST_REPLY_TIMEOUT (Page.enable in #894's job, Page.addScriptToEvaluateOnNewDocument in #887's), in a different
# test each time. A start that fails like that, or exits early, or never writes DevToolsActivePort, is tried once more
# with a fresh profile. No test code has run at that point. A second failure fails the test and names both.
START_ATTEMPTS = 2
STDERR_LIMIT = 16384
# Do not emit raw Chrome output: it can contain URLs, paths, environment values, or credentials.
STDERR_HINTS = ((b"sandbox", "sandbox"), (b"dbus", "D-Bus"), (b"crashpad", "crashpad"),
                (b"zygote", "zygote"), (b"singletonlock", "profile lock"),
                (b"no space left on device", "storage exhausted"), (b"shared memory", "shared memory"))


class _StderrTail:
    """Drain Chrome's stderr without blocking it, retaining only a bounded in-memory tail."""
    def __init__(self, stream):
        self.stream = stream
        self.tail = b""
        self.stop = threading.Event()
        os.set_blocking(stream.fileno(), False)
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        after_stop = 0
        while True:
            try:
                chunk = os.read(self.stream.fileno(), 4096)
            except BlockingIOError:
                if self.stop.is_set():
                    return
                self.stop.wait(0.05)
                continue
            if not chunk:
                return
            self.tail = (self.tail + chunk)[-STDERR_LIMIT:]
            if self.stop.is_set():
                after_stop += 1
                if after_stop >= 256:  # Bound cleanup even if a descendant keeps writing.
                    return

    def finish(self):
        self.stop.set()
        self.thread.join()

    def hints(self):
        tail = self.tail.lower()
        found = [label for needle, label in STDERR_HINTS if needle in tail]
        return ", ".join(found) if found else "no recognized diagnostic (output withheld)"

# Runs before the page's own scripts in every document: counts the page's requests until their body is read, so
# Browser.settle() can tell when the start-up requests are answered and handled.
COUNT_REQUESTS = """(() => {
  let open = 0;
  const fetch0 = window.fetch;
  Object.defineProperty(window, "__exoRequests", { get: () => open, configurable: true });
  window.fetch = function (...args) {
    open++;
    let done = false;
    const finish = () => { if (!done) { done = true; open--; } };
    return fetch0.apply(this, args).then((response) => {
      for (const read of ["json", "text", "arrayBuffer", "blob"]) {
        const own = response[read].bind(response);
        response[read] = () => own().finally(finish);
      }
      return response;
    }, (error) => { finish(); throw error; });
  };
})();"""


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


class ChromeStartError(RuntimeError):
    """Chrome did not start: it exited early, never wrote DevToolsActivePort, or never answered a first DevTools call."""


class Browser:
    def __init__(self, width: int = 1440, height: int = 900):
        chrome = find_chrome()
        if not chrome:
            raise RuntimeError("no Chrome found")
        failures = []
        for _ in range(START_ATTEMPTS):
            self.profile = tempfile.mkdtemp(prefix="exo-chrome-")
            self.proc = None
            self._stderr = None
            self._cm = None
            try:
                self._start(chrome, width, height)
                return
            except ChromeStartError as exc:
                self.close()
                failures.append(exc)
            except BaseException as exc:
                self.close()
                if failures:
                    exc.add_note("after a failed Chrome start: " + "; then ".join(map(str, failures)))
                raise
        if len(failures) == 1:
            raise failures[0]
        raise ChromeStartError(f"Chrome did not start in {len(failures)} attempts: "
                               + "; then ".join(f"({i}) {f}" for i, f in enumerate(failures, 1))) from failures[-1]

    def _start(self, chrome, width, height):
        global _started
        self.proc = subprocess.Popen(
            # #907: keep a headless page's timers and frames running at full rate, whatever the runner's window state
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
             "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
             "--disable-renderer-backgrounding",
             f"--window-size={width},{height}", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
             "--use-mock-keychain", "--password-store=basic", "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._stderr = _StderrTail(self.proc.stderr)
        atexit.register(self.close)  # never leave a browser behind, even when a test dies
        port_file = Path(self.profile) / "DevToolsActivePort"
        limit = START_TIMEOUT if _started else FIRST_START_TIMEOUT
        end = time.monotonic() + limit
        while not (port_file.exists() and port_file.read_text().strip()) and time.monotonic() < end:
            if self.proc.poll() is not None:
                self._stderr.finish()
                raise ChromeStartError(f"Chrome exited before DevTools was ready (exit {self.proc.returncode}); "
                                       f"stderr: {self._stderr.hints()}")
            time.sleep(0.05)
        if not port_file.exists() or not port_file.read_text().strip():
            self._stderr.finish()
            if self.proc.poll() is not None:
                raise ChromeStartError(f"Chrome exited before DevTools was ready (exit {self.proc.returncode}); "
                                       f"stderr: {self._stderr.hints()}")
            raise ChromeStartError(f"Chrome did not create DevToolsActivePort within {limit:g}s; "
                                   f"stderr: {self._stderr.hints()}")
        _started = True  # Chrome is loaded; a failure after this point is not a slow cold start
        port = port_file.read_text().split()[0]
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10).read())
        page = next(t for t in targets if t.get("type") == "page")
        from websockets.sync.client import connect
        self._cm = connect(page["webSocketDebuggerUrl"], max_size=None)
        self.ws = self._cm.__enter__()
        self.n = 0
        # A wait for Chrome to start (FIRST_REPLY_TIMEOUT), not a bound any test asserts.
        first = (("Page.enable", {}), ("Page.addScriptToEvaluateOnNewDocument", {"source": COUNT_REQUESTS}),
                 ("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}))
        for method, params in first:
            try:
                self.call(method, reply_within=FIRST_REPLY_TIMEOUT, **params)
            except TimeoutError as exc:
                self._stderr.finish()
                raise ChromeStartError(f"Chrome did not answer {method} within {FIRST_REPLY_TIMEOUT:g}s; "
                                       f"stderr: {self._stderr.hints()}") from exc

    def call(self, method: str, *, reply_within: float = 30, **params: Any) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=reply_within))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)

    def reload(self) -> None:
        """Load the page again as a new document, for example after a test wrote localStorage, and return once the old
        document is gone.

        A second goto() to the page's own link is not a reload: it differs from the current address only after `#`,
        so the browser keeps the document and the app re-authenticates and mounts the view again later, after a
        round trip to the server. Until then the old view still answers every query, so a wait() can pass on it and
        the test goes on against a view that is about to be replaced."""
        self.eval("window.__exoOldDocument = true")
        self.call("Page.reload")
        self.wait("!window.__exoOldDocument && document.readyState !== 'loading'")

    def resize(self, width: int, height: int) -> None:
        """Change the viewport and return once the page has adapted to it.

        A media query's change event, and so any layout the page sets from it, comes with the next rendered frame,
        not with the resize: a geometry read in between sees the old layout under the new size (on a slow runner
        that frame can land between any two later calls)."""
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
        self.wait(f"innerWidth === {width} && innerHeight === {height}")
        self.frames()

    def frames(self, n: int = 2) -> None:
        """Return after the page has rendered `n` more frames (and run what it scheduled for them)."""
        self.eval("new Promise(done => { const next = (left) => left ? requestAnimationFrame(() => next(left - 1)) : done(true);"
                  f" next({n}); }})")

    def settle(self, syncs: int = 1, timeout: float = 10.0) -> None:
        """Wait until the page's start-up work is done: its event stream has caught up `syncs` times, and every
        request the page made has been answered and handled.

        The page is usable before that. But each catch-up makes the chat view fetch its snapshot again and redraw
        the transcript, so a test that reads a position, an element or a request count right after the first
        messages appear can read it from the drawing a moment later replaces.

        A request counts as answered once the page reads its body (json, text, arrayBuffer or blob). A request
        whose body the page never reads keeps the count above zero, and settle() then times out."""
        self.wait("import('/static/bus.js').then(bus => { if (!window.__exoSyncs) { window.__exoSyncs = { n: 0 };"
                  " bus.onState(state => { window.__exoSyncs.n = state.synced; }); } return true; })", timeout)
        quiet = f"window.__exoSyncs.n >= {syncs} && window.__exoRequests === 0"
        self.wait(quiet, timeout)
        self.frames()
        self.wait(quiet, timeout)

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
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
            if self.proc is not None:
                if self.proc.poll() is None:
                    try:
                        self.proc.terminate()
                        self.proc.wait(5)
                    except ProcessLookupError:
                        self.proc.wait()
                    except subprocess.TimeoutExpired:
                        try:
                            self.proc.kill()
                        except ProcessLookupError:
                            pass
                        self.proc.wait()
                if self._stderr is not None:
                    self._stderr.finish()
                    self.proc.stderr.close()
            shutil.rmtree(self.profile, ignore_errors=True)
        finally:
            # Only once Chrome is gone: an interrupt during the close still leaves the exit hook to stop it.
            if self.proc is None or self.proc.poll() is not None:
                atexit.unregister(self.close)

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
