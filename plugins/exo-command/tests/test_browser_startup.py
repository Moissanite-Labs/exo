"""Chrome startup failures report safe diagnostics and clean up their processes."""
import builtins
import importlib
import json
import os
import sys
import time
import types

import pytest

import browser


@pytest.fixture
def fake_chrome(tmp_path, monkeypatch):
    script = tmp_path / "chrome"
    profiles = []
    processes = []
    mkdir = browser.tempfile.mkdtemp
    popen = browser.subprocess.Popen
    import_module = builtins.__import__

    def no_websockets(name, *args, **kwargs):
        if name == "websockets" or name.startswith("websockets."):
            raise ModuleNotFoundError("synthetic missing optional dependency")
        return import_module(name, *args, **kwargs)

    def profile(*args, **kwargs):
        path = mkdir(*args, dir=tmp_path, **kwargs)
        profiles.append(path)
        return path

    def spawn(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(browser.tempfile, "mkdtemp", profile)
    monkeypatch.setattr(browser.subprocess, "Popen", spawn)
    monkeypatch.setattr(browser, "find_chrome", lambda: str(script))
    monkeypatch.setattr(builtins, "__import__", no_websockets)
    # A fake start that writes DevToolsActivePort must not end the first real start's longer wait for the rest of the
    # run (this file runs before the first real browser test), so each test gets the run's state back.
    monkeypatch.setattr(browser, "_started", browser._started)

    def make(source):
        script.write_text(f"#!{sys.executable}\n" + source)
        script.chmod(0o700)
        return profiles, processes
    return make


def test_early_exit_is_distinct_and_stderr_is_sanitized(fake_chrome, monkeypatch):
    lengths = []
    hints = browser._StderrTail.hints
    def record(tail):
        lengths.append(len(tail.tail))
        return hints(tail)
    monkeypatch.setattr(browser._StderrTail, "hints", record)
    profiles, processes = fake_chrome(
        "import sys\n"
        "sys.stderr.write('x' * 200000 + ' sandbox https://user:secret@127.0.0.1/ token=synthetic-private\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(17)\n"
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    assert time.monotonic() - started < 5  # A dead process must not consume the 20 s startup timeout.
    msg = str(exc.value)
    assert "exited before DevTools was ready (exit 17)" in msg
    assert "sandbox" in msg and "synthetic-private" not in msg and "127.0.0.1" not in msg
    assert lengths and max(lengths) <= browser.STDERR_LIMIT
    assert processes[0].poll() == 17 and not os.path.exists(profiles[0])


def test_unknown_stderr_is_never_echoed(fake_chrome):
    fake_chrome("import sys\nsys.stderr.write('synthetic-private-unknown\\n'); sys.stderr.flush()\nsys.exit(1)\n")
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    assert "no recognized diagnostic (output withheld)" in str(exc.value)
    assert "synthetic-private-unknown" not in str(exc.value)


def test_timeout_terminates_chrome_and_removes_profile(fake_chrome, monkeypatch):
    hints = browser._StderrTail.hints
    def finished_hints(tail):
        assert not tail.thread.is_alive(), "drain must finish before reading diagnostics"
        return hints(tail)
    monkeypatch.setattr(browser._StderrTail, "hints", finished_hints)
    profiles, processes = fake_chrome(
        "import sys, time\n"
        "sys.stderr.write('dbus synthetic-private\\n'); sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(browser, "START_TIMEOUT", 0.5)
    monkeypatch.setattr(browser, "_started", True)  # a later start in the run, so the 0.5 s wait applies
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    msg = str(exc.value)
    assert "did not create DevToolsActivePort within 0.5s" in msg and "D-Bus" in msg
    assert "synthetic-private" not in msg
    assert processes[0].poll() is not None and not os.path.exists(profiles[0])


def test_the_first_start_in_a_run_gets_the_longer_wait(fake_chrome, monkeypatch):
    """#907: a cold first start may take longer than START_TIMEOUT; a failed start does not use that allowance up."""
    profiles, processes = fake_chrome("import time\ntime.sleep(30)\n")
    monkeypatch.setattr(browser, "START_TIMEOUT", 0.2)
    monkeypatch.setattr(browser, "FIRST_START_TIMEOUT", 1.0)
    monkeypatch.setattr(browser, "_started", False)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"DevToolsActivePort within 1s"):
        browser.Browser()
    assert time.monotonic() - started >= 1.0
    assert browser._started is False  # it never reached DevTools, so the next start still waits the longer time
    with pytest.raises(RuntimeError, match=r"DevToolsActivePort within 1s"):
        browser.Browser()
    monkeypatch.setattr(browser, "_started", True)
    with pytest.raises(RuntimeError, match=r"DevToolsActivePort within 0.2s"):
        browser.Browser()
    assert all(p.poll() is not None for p in processes) and not any(os.path.exists(p) for p in profiles)


def test_a_slow_first_start_is_not_a_timeout_and_ends_the_allowance(fake_chrome, monkeypatch):
    """A first start slower than START_TIMEOUT goes on to DevTools; once one has, later starts get START_TIMEOUT."""
    profiles, processes = fake_chrome(
        "import pathlib, sys, time\n"
        "time.sleep(0.8)\n"
        "profile = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir='))\n"
        "pathlib.Path(profile, 'DevToolsActivePort').write_text('12345\\n')\n"
        "time.sleep(30)\n"
    )
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic connection error")
    monkeypatch.setattr(browser.urllib.request, "urlopen", fail)
    monkeypatch.setattr(browser, "START_TIMEOUT", 0.2)
    monkeypatch.setattr(browser, "FIRST_START_TIMEOUT", 10.0)
    monkeypatch.setattr(browser, "_started", False)
    with pytest.raises(RuntimeError, match="synthetic connection error"):  # past the port file, not a timeout
        browser.Browser()
    assert browser._started is True
    with pytest.raises(RuntimeError, match=r"DevToolsActivePort within 0.2s"):
        browser.Browser()
    assert all(p.poll() is not None for p in processes) and not any(os.path.exists(p) for p in profiles)


def test_error_after_port_creation_still_cleans_up(fake_chrome, monkeypatch):
    profiles, processes = fake_chrome(
        "import pathlib, sys, time\n"
        "profile = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir='))\n"
        "pathlib.Path(profile, 'DevToolsActivePort').write_text('12345\\n')\n"
        "time.sleep(30)\n"
    )
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic connection error")
    monkeypatch.setattr(browser.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="synthetic connection error") as exc:
        browser.Browser()
    assert type(exc.value) is RuntimeError and len(processes) == 1  # not a failed start: raised at once, no retry
    assert processes[0].poll() is not None and not os.path.exists(profiles[0])


def test_missing_connection_dependency_also_cleans_up(fake_chrome, monkeypatch):
    profiles, processes = fake_chrome(
        "import pathlib, sys, time\n"
        "profile = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir='))\n"
        "pathlib.Path(profile, 'DevToolsActivePort').write_text('12345\\n')\n"
        "time.sleep(30)\n"
    )
    class Response:
        def read(self):
            return b'[{"type":"page","webSocketDebuggerUrl":"ws://127.0.0.1/"}]'
    monkeypatch.setattr(browser.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(ModuleNotFoundError, match="synthetic missing optional dependency"):
        browser.Browser()
    assert processes[0].poll() is not None and not os.path.exists(profiles[0])


PORT_THEN_WAIT = (
    "import pathlib, sys, time\n"
    "profile = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir='))\n"
    "pathlib.Path(profile, 'DevToolsActivePort').write_text('12345\\n')\n"
    "time.sleep(30)\n"
)


def _fake_devtools(monkeypatch, answers, waits=None):
    """Stand in for the page's DevTools socket: answers(start, method) is True for a reply, False for no reply (the
    read times out) and "error" for an error reply. Returns the (start, method) calls in the order sent; start counts
    the sockets opened, from 1. Each read's timeout is appended to waits when given."""
    class Response:
        def read(self):
            return b'[{"type":"page","webSocketDebuggerUrl":"ws://127.0.0.1/"}]'
    monkeypatch.setattr(browser.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    calls = []

    class Socket:
        def __init__(self, start):
            self.start, self.last = start, None

        def send(self, text):
            self.last = json.loads(text)
            calls.append((self.start, self.last["method"]))

        def recv(self, timeout=None):
            if waits is not None:
                waits.append(timeout)
            answer = answers(self.start, self.last["method"])
            if answer == "error":
                return json.dumps({"id": self.last["id"], "error": {"code": -32000, "message": "synthetic"}})
            if not answer:
                raise TimeoutError(f"timed out in {timeout:.1f}s")
            return json.dumps({"id": self.last["id"], "result": {}})

    class Connection:
        def __init__(self, start):
            self.start = start

        def __enter__(self):
            return Socket(self.start)

        def __exit__(self, *exc):
            return False

    opened = []

    def connect(url, **kwargs):
        opened.append(url)
        return Connection(len(opened))
    client = types.ModuleType("websockets.sync.client")
    client.connect = connect

    def import_with_client(name, *args, **kwargs):
        if name == "websockets.sync.client":
            return client
        return importlib.__import__(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", import_with_client)
    return calls


FIRST_CALLS = ["Page.enable", "Page.addScriptToEvaluateOnNewDocument", "Emulation.setDeviceMetricsOverride"]


@pytest.mark.parametrize("method", FIRST_CALLS)
def test_a_first_call_never_answered_is_a_failed_start_and_the_next_start_runs(fake_chrome, monkeypatch, method):
    """#907: on hosted macOS Chrome started and never answered Page.enable (#894's job), and once
    Page.addScriptToEvaluateOnNewDocument (#887's). Such a start is closed and Chrome starts once more."""
    profiles, processes = fake_chrome(PORT_THEN_WAIT)
    waits = []
    calls = _fake_devtools(monkeypatch, lambda start, m: not (start == 1 and m == method), waits)
    b = browser.Browser()
    try:
        assert waits and all(w == browser.FIRST_REPLY_TIMEOUT for w in waits)
        assert [m for s, m in calls if s == 1] == FIRST_CALLS[:FIRST_CALLS.index(method) + 1]  # stopped there
        assert [m for s, m in calls if s == 2] == FIRST_CALLS
        assert len(processes) == 2 and processes[0].poll() is not None and not os.path.exists(profiles[0])
        assert b.proc is processes[1] and b.profile == profiles[1] != profiles[0] and os.path.exists(profiles[1])
    finally:
        b.close()
    assert processes[1].poll() is not None and not os.path.exists(profiles[1])


def test_a_second_failed_start_fails_and_names_both(fake_chrome, monkeypatch):
    profiles, processes = fake_chrome(PORT_THEN_WAIT)
    monkeypatch.setattr(browser, "FIRST_REPLY_TIMEOUT", 0.5)
    calls = _fake_devtools(monkeypatch, lambda start, m: False)
    with pytest.raises(browser.ChromeStartError) as exc:
        browser.Browser()
    msg = str(exc.value)
    assert msg.startswith("Chrome did not start in 2 attempts: (1) Chrome did not answer Page.enable within 0.5s")
    assert "; then (2) Chrome did not answer Page.enable within 0.5s" in msg
    assert calls == [(1, "Page.enable"), (2, "Page.enable")]
    assert len(processes) == 2 and all(p.poll() is not None for p in processes)
    assert not any(os.path.exists(p) for p in profiles)


def test_an_early_exit_is_tried_once_more_with_a_fresh_profile(fake_chrome, monkeypatch, tmp_path):
    count = tmp_path / "starts"
    profiles, processes = fake_chrome(
        "import pathlib, sys\n"
        f"count = pathlib.Path({str(count)!r})\n"
        "n = int(count.read_text()) + 1 if count.exists() else 1\n"
        "count.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.exit(3)\n" + PORT_THEN_WAIT)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic connection error")
    monkeypatch.setattr(browser.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="synthetic connection error") as exc:  # the second start got past the port
        browser.Browser()
    assert len(processes) == 2 and processes[0].poll() == 3 and profiles[0] != profiles[1]
    assert processes[1].poll() is not None and not any(os.path.exists(p) for p in profiles)
    # The error from the second start keeps the first one, so the log still shows a start failed before it.
    assert any("after a failed Chrome start: Chrome exited before DevTools was ready (exit 3)" in n
               for n in getattr(exc.value, "__notes__", []))


def test_an_error_reply_to_a_first_call_is_raised_at_once(fake_chrome, monkeypatch):
    """Only a start that fails is tried again: an answer that is an error is Chrome working, and is raised as it is."""
    profiles, processes = fake_chrome(PORT_THEN_WAIT)
    calls = _fake_devtools(monkeypatch, lambda start, m: "error")
    with pytest.raises(RuntimeError, match="Page.enable: ") as exc:
        browser.Browser()
    assert type(exc.value) is RuntimeError and calls == [(1, "Page.enable")] and len(processes) == 1
    assert processes[0].poll() is not None and not os.path.exists(profiles[0])
