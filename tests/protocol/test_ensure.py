"""D-036: `exo web` and `exo` share one core in any order."""
import asyncio
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from exo.core.agentlog import claim_writer, release_writer, socket_path
from exo import ensure_core as ensure


@pytest.fixture
def home():
    d = Path(tempfile.mkdtemp(prefix="exo-e-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class FakeProc:
    def poll(self):
        return None


def listener(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(8)
    return srv


def test_spawns_one_and_waits_until_it_answers(home):
    calls, keep = [], []

    def spawn(h):
        calls.append(h)
        threading.Timer(0.3, lambda: keep.append(listener(ensure.exod_socket(h)))).start()
        return FakeProc()

    got = ensure.ensure_core(home, "default", timeout=5, spawn=spawn)
    assert got == ensure.exod_socket(home) and len(calls) == 1
    # a second client attaches, no second spawn
    assert ensure.ensure_core(home, "default", timeout=5, spawn=spawn) == got and len(calls) == 1
    keep[0].close()


def test_concurrent_starts_spawn_once(home):
    calls, keep, out = [], [], []

    def spawn(h):
        calls.append(1)
        threading.Timer(0.4, lambda: keep.append(listener(ensure.exod_socket(h)))).start()
        return FakeProc()

    ts = [threading.Thread(target=lambda: out.append(ensure.ensure_core(home, "default", timeout=8, spawn=spawn)))
          for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and len(out) == 6 and set(out) == {ensure.exod_socket(home)}
    keep[0].close()


def test_stale_socket_file_is_cleaned_and_core_started(home):
    stale = ensure.exod_socket(home)
    srv = listener(stale)
    srv.close()  # the file stays, nobody listens
    assert ensure.probe(stale) == "stale"
    seen, keep = [], []

    def spawn(h):
        seen.append(ensure.exod_socket(h).exists())
        threading.Timer(0.2, lambda: keep.append(listener(ensure.exod_socket(h)))).start()
        return FakeProc()

    ensure.ensure_core(home, "default", timeout=5, spawn=spawn)
    assert seen == [False]


def test_tui_in_process_core_is_attached_not_replaced(home):
    """The terminal window holds the writer lock and serves agent-<agent>.sock: web joins it."""
    lock = claim_writer(home, "default")
    srv = listener(socket_path(home, "default"))
    try:
        got = ensure.ensure_core(home, "default", timeout=2, spawn=lambda h: pytest.fail("must not spawn"))
        assert got == socket_path(home, "default")
    finally:
        srv.close()
        release_writer(lock)


def test_lock_holder_without_socket_gives_plain_message(home):
    lock = claim_writer(home, "default")
    try:
        with pytest.raises(ensure.CoreUnavailable) as e:
            ensure.ensure_core(home, "default", timeout=0.5, spawn=lambda h: pytest.fail("must not spawn"))
        assert "held by process" in str(e.value) and "Close that Exo window" in str(e.value)
    finally:
        release_writer(lock)


def test_failed_start_reports_log(home):
    def spawn(h):
        ensure.log_path(h).parent.mkdir(parents=True, exist_ok=True)
        ensure.log_path(h).write_text("boom: no key\n")
        return subprocess.Popen([sys.executable, "-c", "pass"])

    with pytest.raises(ensure.CoreUnavailable) as e:
        ensure.ensure_core(home, "default", timeout=5, spawn=spawn)
    assert "did not start" in str(e.value) and "boom" in str(e.value)


def test_real_exod_starts_detached_and_stops(home, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(home))
    monkeypatch.setenv("EXO_ENGINE", "echo")
    sock = ensure.ensure_core(home, "default", timeout=30)
    try:
        assert sock == ensure.exod_socket(home) and ensure.probe(sock) == "alive"
        pid = int(ensure.pid_path(home).read_text().strip())
        from exo.principal import descends_from, _ppid
        assert not descends_from(pid, os.getpid())
        if sys.platform == "darwin":
            assert _ppid(pid) == 1  # macOS launchd adopts detached cores
        assert pid != os.getpid()
        assert ensure.ensure_core(home, "default", timeout=5) == sock  # second client attaches
    finally:
        msg = ensure.stop_core(home)
    assert "stopped" in msg
    time.sleep(0.2)
    assert ensure.probe(ensure.exod_socket(home)) in ("none", "stale")


def test_tui_waits_for_slow_exod_on_the_shared_start_lock(home, monkeypatch):
    """A daemon taking longer than the old TUI five-second wait still gets joined, never raced."""
    from exo.core.agentlog import WriterBusy
    from exo_tui import conn
    from exo.protocol.client import Client

    started = threading.Event()
    out, failures, sockets, locks, loads = [], [], [], [], []

    def spawn(h):
        locks.append(claim_writer(h, "default"))
        started.set()
        threading.Timer(5.2, lambda: sockets.append(listener(ensure.exod_socket(h)))).start()
        return FakeProc()

    def load(_):
        loads.append(1)
        raise WriterBusy("fake daemon is already starting")

    monkeypatch.setattr("exo.server.app.load_engine", load)

    def starter():
        try:
            out.append(ensure.ensure_core(home, "default", timeout=10, spawn=spawn))
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=starter)
    thread.start()
    assert started.wait(2)
    try:
        joined = conn._open(home, "default")
        thread.join(2)
        assert isinstance(joined, Client) and out == [ensure.exod_socket(home)]
        assert not failures and not loads and not thread.is_alive()
    finally:
        thread.join(10)
        for sock in sockets:
            sock.close()
        for lock in locks:
            release_writer(lock)


def test_start_lock_releases_on_failure_and_timeout_is_bounded(home):
    with pytest.raises(RuntimeError, match="boom"):
        with ensure.startup_lock(home):
            raise RuntimeError("boom")
    with ensure.startup_lock(home):
        with pytest.raises(ensure.CoreUnavailable, match="too long"):
            with ensure.startup_lock(home, timeout=0.01):
                pytest.fail("another owner cannot acquire the starter lock")
    with ensure.startup_lock(home, timeout=0.01):
        pass  # failure and timeout did not leave a held lock


def test_startup_helper_failure_is_prompt_and_reports_log(home, monkeypatch):
    monkeypatch.setattr(ensure, "_DAEMONIZE_SRC", "import sys; sys.stderr.write('helper boom\\n'); sys.exit(7)")
    started = time.monotonic()
    with pytest.raises(ensure.CoreUnavailable, match="helper boom"):
        ensure.ensure_core(home, timeout=0.5)
    assert time.monotonic() - started < 1.0
    assert not ensure.pid_path(home).exists()


def test_leaked_pid_pipe_cannot_deadlock_startup(home, monkeypatch):
    # Deliberately broken helper: a child retains stdout after its parent dies.
    child_file = home / "child.pid"
    script = ("import os,time,pathlib; pid=os.fork(); "
              f"pathlib.Path({str(child_file)!r}).write_text(str(os.getpid())) if pid == 0 else None; "
              "time.sleep(10)")
    monkeypatch.setattr(ensure, "_DAEMONIZE_SRC", script)
    started = time.monotonic()
    try:
        with pytest.raises(ensure.CoreUnavailable, match="helper timed out"):
            ensure.ensure_core(home, timeout=0.3)
        assert time.monotonic() - started < 1.0
        assert not ensure.pid_path(home).exists()
        with ensure.startup_lock(home, timeout=0.1):
            pass
    finally:
        if child_file.exists():
            os.kill(int(child_file.read_text()), signal.SIGKILL)


def test_auto_started_core_preserves_principal_and_tool_boundary(home, monkeypatch):
    from exo.protocol.client import Client
    from exo.protocol import schema as s

    result_file = home / "tool-result.json"
    child = r"""
import json,sys,time
from pathlib import Path
from exo.protocol import schema as s
import httpx
sock, result = sys.argv[1:]
key = Path(sock[:-len('.sock')] + '.principal.key')
for _ in range(100):
    if Path(sock).exists() and key.exists():
        break
    time.sleep(.05)
with httpx.Client(transport=httpx.HTTPTransport(uds=sock), base_url='http://exod') as c:
    response = c.post('/command', content=s.Command.of(s.ModelsSet(model='fake:x')).to_json(),
                      headers={'X-Exo-Principal': key.read_text().strip(), 'X-Exo-Client': 'web'}).json()
Path(result).write_text(json.dumps(response))
"""
    factory = home / "startup_engine.py"
    factory.write_text(
        "import os,sys,subprocess\nfrom pathlib import Path\nfrom exo.core.engine import CoreEngine\n"
        "def create():\n    home=Path(os.environ['EXO_HOME'])\n"
        "    eng=CoreEngine(home, {'roles': {'main': ['fake:x']}, 'fake': {'reply': 'ok'}}, env={})\n"
        f"    subprocess.Popen([sys.executable, '-c', {child!r}, str(home/'run'/'exod.sock'), {str(result_file)!r}])\n"
        "    return eng\n")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(home), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.setenv("EXO_ENGINE", "startup_engine:create")
    sock = ensure.ensure_core(home, timeout=10)
    try:
        async def set_model():
            async with Client(socket=str(sock), principal="web") as client:
                return await client.send(s.ModelsSet(model="fake:x"))
        response = asyncio.run(set_model())
        assert response.ok, response.error
        assert response.result["routes"][0] == "fake:x"
        deadline = time.monotonic() + 5
        while not result_file.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        tool_response = json.loads(result_file.read_text())
        assert not tool_response["ok"] and tool_response["error"]["code"] == "forbidden"
    finally:
        ensure.stop_core(home)
