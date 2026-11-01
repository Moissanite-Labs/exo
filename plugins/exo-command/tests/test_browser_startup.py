"""Chrome startup failures report safe diagnostics and clean up their processes."""
import builtins
import os
import sys
import time
import urllib.error

import pytest

import browser

# Lines a healthy headless Chromium 141 start writes on Linux. The D-Bus errors come from a start in a container,
# the sandbox warning is the one the #1037 review saw. They name no cause, so a failed start must not report them.
HEALTHY_LINUX_START = (
    "[10:12:1101/072846.697327:ERROR:dbus/bus.cc:408] Failed to connect to the bus: Failed to connect to socket"
    " /run/dbus/system_bus_socket: No such file or directory\n"
    "[10:12:1101/072846.817876:ERROR:dbus/bus.cc:408] Failed to connect to the bus: Could not parse server address:"
    " Unknown address type (examples of valid types are \"tcp\" and on UNIX \"unix\")\n"
    "[10:10:1101/072846.959192:ERROR:dbus/object_proxy.cc:573] Failed to call method:"
    " org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type: \n"
    "[31:31:1101/072847.012345:WARNING:sandbox/policy/linux/sandbox_linux.cc] InitializeSandbox() called with"
    " multiple threads in process gpu-process.\n"
)


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
        "sys.stderr.write('x' * 200000 + ' No usable sandbox! https://user:secret@127.0.0.1/'\n"
        "                 ' token=synthetic-private\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(17)\n"
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    assert time.monotonic() - started < 5  # A dead process must not consume the 20 s startup timeout.
    msg = str(exc.value)
    assert "exited before DevTools was ready (exit 17)" in msg
    assert "sandbox: no usable sandbox" in msg and "synthetic-private" not in msg and "127.0.0.1" not in msg
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
        f"sys.stderr.write({HEALTHY_LINUX_START!r})\n"
        "sys.stderr.write('Failed to create /synthetic-private/SingletonLock: File exists (17)\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(browser, "START_TIMEOUT", 0.5)
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    msg = str(exc.value)
    assert msg.endswith("did not create DevToolsActivePort within 0.5s; stderr: profile lock")
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


def test_exit_after_port_creation_reports_exit_code_and_hints(fake_chrome, monkeypatch):
    profiles, processes = fake_chrome(
        "import pathlib, sys, time\n"
        "profile = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--user-data-dir='))\n"
        "pathlib.Path(profile, 'DevToolsActivePort').write_text('12345\\n')\n"
        "time.sleep(0.2)\n"  # Still running when the connection fails: only waiting for the exit can see it.
        "sys.stderr.write('Creating shared memory in /dev/shm/synthetic-private failed: No space left on device\\n')\n"
        "sys.exit(5)\n"
    )
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    def refuse(*args, **kwargs):
        raise refused
    monkeypatch.setattr(browser.urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError) as exc:
        browser.Browser()
    msg = str(exc.value)
    assert "Chrome exited after writing DevToolsActivePort (exit 5)" in msg
    assert "storage exhausted" in msg and "synthetic-private" not in msg
    assert exc.value.__cause__ is refused
    assert processes[0].poll() == 5 and not os.path.exists(profiles[0])


def _hints(text):
    read, write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    tail = browser._StderrTail(os.fdopen(read, "rb"))
    tail.finish()
    tail.stream.close()
    return tail.hints()


def test_healthy_start_lines_are_not_reported_as_a_cause():
    assert _hints(HEALTHY_LINUX_START) == "no recognized diagnostic (output withheld)"


# Sandbox failure messages as Chromium 141 writes them. The "No usable sandbox" text mentions the SUID sandbox
# too, and must not be read as the setuid helper failing.
@pytest.mark.parametrize("lines, hints", [
    ("[1:1:1101/000000.000000:ERROR:content/browser/zygote_host/zygote_host_impl_linux.cc:101] Running as root"
     " without --no-sandbox is not supported. See https://crbug.com/638180.",
     "sandbox: running as root without --no-sandbox, zygote"),
    ("No usable sandbox! If you are running on Ubuntu 23.10+ or another Linux distro that has disabled unprivileged"
     " user namespaces with AppArmor, see https://chromium.googlesource.com/chromium/src/+/main/docs/security/"
     "apparmor-userns-restrictions.md. Otherwise see https://chromium.googlesource.com/chromium/src/+/main/docs/"
     "linux/suid_sandbox_development.md for more information on developing with the (older) SUID sandbox. If you"
     " want to live dangerously and need an immediate workaround, you can try using --no-sandbox.",
     "sandbox: no usable sandbox"),
    ("The SUID sandbox helper binary was found, but is not configured correctly. Rather than run without sandboxing"
     " I'm aborting now.", "sandbox: setuid helper"),
    ("Failed to instantiate the setuid sandbox client.\nThe SUID sandbox helper binary is missing: /synthetic/"
     "chrome-sandbox Aborting now.", "sandbox: setuid helper"),
])
def test_sandbox_failure_is_named_beside_healthy_start_lines(lines, hints):
    assert _hints(HEALTHY_LINUX_START + lines + "\n") == hints
