import os
import sys
import threading
import time

import pytest

from exo.core.tools.builtin.shell import run_command

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); check its state via ps.
    state = os.popen(f"ps -o stat= -p {pid}").read().strip()
    return bool(state) and not state.startswith("Z")


def test_exit_code_cwd_and_stderr(run, tmp_path):
    (tmp_path / "sub").mkdir()
    res = run("bash", command="pwd; echo oops >&2; exit 3", cwd="sub")
    text = res.text()
    assert text.splitlines()[0].endswith("/sub")
    assert "oops" in text and text.endswith("[exit 3]")
    assert run("bash", command="true").text() == "(no output)\n[exit 0]"
    assert run("bash", command="true", cwd="nope").is_error


def test_timeout_kills_child_process_group(tmp_path):
    pidfile = tmp_path / "pid"
    # The grandchild ignores TERM, so the group must be escalated to KILL.
    cmd = f"bash -c 'trap \"\" TERM; echo $$ > {pidfile}; sleep 60' & wait"
    t0 = time.monotonic()
    code, out, status = run_command(cmd, str(tmp_path), timeout=0.5)
    assert status == "timeout" and code == 124
    assert time.monotonic() - t0 < 6
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_timeout_message_via_tool(run):
    res = run("bash", command="echo start; sleep 30", timeout=0.3)
    assert "start" in res.text() and "timed out" in res.text() and res.text().endswith("[exit 124]")


def test_cancel_interrupts(tmp_path):
    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()
    code, _out, status = run_command("sleep 30", str(tmp_path), timeout=20, cancel=ev)
    assert status == "interrupted" and code == 130


def test_output_cap_keeps_head_and_tail(tmp_path):
    seen = []
    code, out, _ = run_command("seq 1 100000", str(tmp_path), cap=2000, emit=seen.append)
    assert code == 0 and len(out) < 2200
    assert out.startswith("1\n2\n") and out.rstrip().endswith("100000") and "chars cut" in out
    assert "".join(seen).count("\n") == 100000


def test_background_straggler_does_not_hang(tmp_path):
    t0 = time.monotonic()
    code, out, status = run_command("sleep 30 & echo done", str(tmp_path), timeout=20)
    assert status == "ok" and "done" in out and time.monotonic() - t0 < 6


def test_bash_description_states_limits(reg):
    from exo.core.tools.builtin.shell import MAX_TIMEOUT
    spec = next(s for s in reg.schemas() if s["name"] == "bash")
    assert f"max {MAX_TIMEOUT}; larger values are rejected" in spec["description"]
    assert "macOS has no `timeout` command" in spec["description"]
    assert "larger values are rejected" in spec["parameters"]["properties"]["timeout"]["description"]


def test_bash_rejects_timeout_over_max(run):
    assert run("bash", command="true", timeout=601).is_error


def test_tool_env_drops_bus_identity_and_secret_vars(monkeypatch):
    from exo.core.tools.builtin.shell import run_command, tool_env

    for k, v in {"EXO_BUS_URL": "http://x", "EXO_BUS_SEAT": "s", "EXO_BUS_KEYS_DIR": "/k", "EXO_KEYS_FILE": "/f",
                 "EXO_API_TOKEN": "t", "EXO_HOME": "/h", "EXO_SEAT": "keep"}.items():
        monkeypatch.setenv(k, v)
    env = tool_env()
    assert not [k for k in env if k.startswith("EXO_BUS_")] and "EXO_KEYS_FILE" not in env and "EXO_API_TOKEN" not in env
    assert env["EXO_HOME"] == "/h" and env["EXO_SEAT"] == "keep" and env["TERM"] == "dumb"
    code, out, _ = run_command("env", ".")
    assert code == 0 and "EXO_BUS_URL" not in out and "EXO_BUS_SEAT" not in out and "EXO_KEYS_FILE" not in out


def test_raise_nofile_limit_raises_soft_toward_hard_and_is_safe(monkeypatch):
    import resource

    from exo.core.tools.builtin import shell

    calls = []
    monkeypatch.setattr(resource, "getrlimit", lambda r: (256, resource.RLIM_INFINITY))
    monkeypatch.setattr(resource, "setrlimit", lambda r, v: calls.append(v))
    assert shell.raise_nofile_limit() == 10240 and calls == [(10240, resource.RLIM_INFINITY)]
    monkeypatch.setattr(resource, "getrlimit", lambda r: (256, 4096))
    assert shell.raise_nofile_limit() == 4096
    monkeypatch.setattr(resource, "getrlimit", lambda r: (4096, 4096))
    assert shell.raise_nofile_limit() is None  # already at the hard limit

    def boom(r, v):
        raise OSError("no")
    monkeypatch.setattr(resource, "getrlimit", lambda r: (256, 4096))
    monkeypatch.setattr(resource, "setrlimit", boom)
    assert shell.raise_nofile_limit() is None


def test_bash_tool_env_has_no_provider_keys_but_seat_allowlist_passes(reg, tmp_path, monkeypatch):
    from exo.core.tools import ToolContext
    from exo.core.types import ToolCall

    fake = {"OPENROUTER_API_KEY": "fake-or", "ANTHROPIC_API_KEY": "fake-an", "OPENCODE_GO_API_KEY": "fake-oc",
            "MY_SERVICE_TOKEN": "fake-t", "DB_PASSWORD": "fake-p", "APP_SECRET": "fake-s",
            "AWS_SECRET_ACCESS_KEY": "fake-aws", "AWS_SESSION_TOKEN": "fake-aws2", "GH_TOKEN": "fake-gh"}
    for k, v in fake.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("KEEP_ME", "visible")

    def call(meta):
        res = reg.call_sync(ToolCall(id="c1", name="bash", arguments={"command": "env"}),
                            ToolContext(cwd=str(tmp_path), meta=meta))
        return res.text()

    out = call({})
    assert "KEEP_ME=visible" in out
    assert not [k for k in fake if k in out] and "fake-" not in out
    out = call({"env_allow": ["GH_TOKEN"]})
    assert "GH_TOKEN=fake-gh" in out and "OPENROUTER_API_KEY" not in out and "AWS_SESSION_TOKEN" not in out
    import os
    assert os.environ["OPENROUTER_API_KEY"] == "fake-or"  # the process itself keeps its keys


def test_seat_parses_tools_env_allow():
    from exo.core import seats

    assert seats.parse({"name": "a", "tools": {"env_allow": ["GH_TOKEN"]}}).env_allow == ["GH_TOKEN"]
    assert seats.parse({"name": "a"}).env_allow == []
