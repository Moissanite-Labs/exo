"""D-036: `exo web` and `exo` share one core in any order."""
import os
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
        assert ensure.pid_path(home).read_text().strip().isdigit()
        assert ensure.ensure_core(home, "default", timeout=5) == sock  # second client attaches
    finally:
        msg = ensure.stop_core(home)
    assert "stopped" in msg
    time.sleep(0.2)
    assert ensure.probe(ensure.exod_socket(home)) in ("none", "stale")
