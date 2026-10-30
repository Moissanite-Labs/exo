"""Chrome startup failures report safe diagnostics and clean up their processes."""
import builtins
import os
import sys

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
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
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
    profiles, processes = fake_chrome(
        "import sys, time\n"
        "sys.stderr.write('dbus synthetic-private\\n'); sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(browser, "START_TIMEOUT", 0.5)
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    msg = str(exc.value)
    assert "did not create DevToolsActivePort within 0.5s" in msg and "D-Bus" in msg
    assert "synthetic-private" not in msg
    assert processes[0].poll() is not None and not os.path.exists(profiles[0])


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
    with pytest.raises(RuntimeError, match="synthetic connection error"):
        browser.Browser()
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
