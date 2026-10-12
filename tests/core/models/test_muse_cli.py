"""muse-cli provider against a fake ``muse`` script: no network, no real CLI, no real keychain."""
import asyncio
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from exo import providers_setup as ps
from exo.core.models import muse_cli
from exo.core.models.fake import FakeProvider
from exo.core.models.router import Router
from exo.core.types import Message, ToolSpec
from exo.models_cmd import available_models

CANARY = "canary-secret-value-123456"

# A fake CLI. fake.json beside it gives the mode and where to record what it saw (the child gets a minimal env).
FAKE = f"""#!{sys.executable}
import json, os, sys, time
cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "fake.json")))
mode = cfg["mode"]
a = sys.argv[1:]
if mode == "sleep":
    open(cfg["pid"], "w").write(str(os.getpid()))
    time.sleep(60)
prompt = open(a[a.index("--prompt-file") + 1]).read() if "--prompt-file" in a else None
json.dump({{"argv": a, "prompt": prompt, "cwd": os.getcwd(), "env": dict(os.environ)}}, open(cfg["out"], "w"))
if mode == "empty":
    sys.exit(0)
if mode == "login":
    sys.stderr.write("not logged in, run muse login\\n"); sys.exit(2)
if mode == "401":
    sys.stderr.write("HTTP 401 unauthorized\\n"); sys.exit(1)
if mode == "429":
    sys.stderr.write("HTTP 429\\n"); sys.exit(1)
if mode == "keychain":
    sys.stderr.write("keychain write failed: OSStatus -67068\\n"); sys.exit(1)
if mode == "fail":
    sys.stderr.write("boom " + {CANARY!r} + "\\n"); sys.exit(3)
sys.stderr.write("muse: workspace root: x\\n")
print("hi there")
"""
TOOL = ToolSpec(name="get_weather", description="weather", parameters={"type": "object", "properties": {}})


@pytest.fixture(autouse=True)
def signed(monkeypatch):
    monkeypatch.setattr(muse_cli, "signature_state", lambda exe: "signed")


def home_with_login(tmp_path, subs_active=True, mechanism="oauth"):
    home = tmp_path / "home"
    (home / ".config" / "muse").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "muse" / "auth.json").write_text(json.dumps({
        "schema_version": 1,
        "providers": {"meta": {"mechanism": mechanism, "subs_active": subs_active, "user_email": CANARY,
                               "access_token": CANARY}}}))
    return home


def router(tmp_path, mode="ok", login=True, **env):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    exe = d / "muse"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (d / "fake.json").write_text(json.dumps({"mode": mode, "out": str(tmp_path / "out.json"),
                                             "pid": str(tmp_path / "pid")}))
    home = home_with_login(tmp_path) if login else tmp_path / "nohome"
    return Router(roles={"main": ["muse-cli:default"]}, env={"PATH": str(d), "HOME": str(home), **env})


def run(r, messages, tools=()):
    async def go():
        return [e async for e in r.stream("main", messages, list(tools))]
    return asyncio.run(go())


def seen(tmp_path):
    return json.loads((tmp_path / "out.json").read_text())


def test_success_argv_env_and_isolation(tmp_path):
    r = router(tmp_path, META_API_KEY="mk-secret", SOME_PARENT_VAR="x", AWS_ACCESS_KEY_ID="a", LANG="C")
    evs = run(r, [Message.system("be brief"), Message.user("hello")])
    assert "".join(e.text for e in evs if e.kind == "text") == "hi there"
    done = evs[-1]
    assert done.kind == "done" and done.route == "muse-cli:default" and done.usage is None
    s = seen(tmp_path)
    a = s["argv"]
    assert a[0] == "exec" and "--model" not in a  # "default" leaves the CLI's own default
    for flag in ("--no-session-log", "--disable-shell", "--disable-write", "--disable-web-tools"):
        assert flag in a
    assert a[a.index("--approval-mode") + 1] == "never"
    assert "--yolo" not in a and "--disable-approval" not in a and "--disable-sandbox" not in a
    assert s["prompt"] == "be brief\n\nhello"
    assert "hello" not in " ".join(a)  # the prompt travels by file, not argv
    assert "META_API_KEY" not in s["env"] and "SOME_PARENT_VAR" not in s["env"] and "AWS_ACCESS_KEY_ID" not in s["env"]
    assert s["env"]["LANG"] == "C" and "HOME" in s["env"]
    assert os.path.realpath(s["cwd"]) == os.path.realpath(a[a.index("--workspace") + 1])  # an empty private dir


def test_history_is_flattened_with_roles_and_model_passes_through(tmp_path):
    r = router(tmp_path)
    run(r, [Message.user("one"), Message.assistant("two"), Message.user("three")])
    assert seen(tmp_path)["prompt"] == "[user]\none\n\n[assistant]\ntwo\n\n[user]\nthree"
    r.roles["main"] = ["muse-cli:m1"]
    run(r, [Message.user("x")])
    a = seen(tmp_path)["argv"]
    assert a[a.index("--model") + 1] == "m1"


def test_tools_are_refused_and_the_router_falls_back(tmp_path):
    r = router(tmp_path)
    r.roles["main"] = ["muse-cli:default", "b:m"]
    r.instances["b"] = FakeProvider(["fallback ok"], name="b")
    evs = run(r, [Message.user("hello")], [TOOL])
    assert any(e.kind == "notice" and "muse-cli:default" in e.text for e in evs)
    assert evs[-1].kind == "done" and evs[-1].message.text() == "fallback ok"
    assert not (tmp_path / "out.json").exists()  # muse was never started
    end = run(router(tmp_path), [Message.user("hello")], [TOOL])[-1]
    assert end.kind == "error" and "text only" in end.error


@pytest.mark.parametrize("mode,status,hint", [("login", 401, "muse login"), ("401", 401, "muse login"),
                                              ("429", 429, ""), ("keychain", None, "codesign")])
def test_failures_are_classified_and_never_return_text(tmp_path, mode, status, hint):
    evs = run(router(tmp_path, mode), [Message.user("hi")])
    assert not [e for e in evs if e.kind == "text"]
    assert evs[-1].kind == "error" and evs[-1].status == status and hint in evs[-1].error


def test_generic_failure_hides_stderr(tmp_path):
    end = run(router(tmp_path, "fail"), [Message.user("hi")])[-1]
    assert end.kind == "error" and "exit 3" in end.error and CANARY not in end.error


def test_empty_output_is_an_error(tmp_path):
    end = run(router(tmp_path, "empty"), [Message.user("hi")])[-1]
    assert end.kind == "error" and "nothing" in end.error


def test_unsigned_binary_is_refused_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(muse_cli, "signature_state", lambda exe: "unsigned")
    end = run(router(tmp_path), [Message.user("hi")])[-1]
    assert end.kind == "error" and "codesign -s -" in end.error and end.retryable is False
    assert not (tmp_path / "out.json").exists()


def test_signature_state_reads_codesign(monkeypatch):
    monkeypatch.undo()  # drop the autouse "signed" stub
    out = {"rc": 1, "err": "x: code object is not signed at all"}
    monkeypatch.setattr(muse_cli.sys, "platform", "darwin")
    monkeypatch.setattr(muse_cli.shutil, "which", lambda n: "/usr/bin/codesign")
    monkeypatch.setattr(muse_cli.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], out["rc"], "", out["err"]))
    assert muse_cli.signature_state("/x/muse") == "unsigned"
    out.update(rc=0, err="Identifier=muse")
    assert muse_cli.signature_state("/x/muse") == "signed"
    out.update(rc=1, err="something else")
    assert muse_cli.signature_state("/x/muse") == "unknown"
    monkeypatch.setattr(muse_cli.sys, "platform", "linux")
    assert muse_cli.signature_state("/x/muse") == "unknown"


def test_login_state_reads_only_non_secret_fields(tmp_path):
    env = {"HOME": str(home_with_login(tmp_path))}
    st = muse_cli.login_state(env)
    assert st == {"logged_in": True, "subs_active": True} and CANARY not in json.dumps(st)
    home_with_login(tmp_path, subs_active=False)
    assert muse_cli.login_state(env)["subs_active"] is False
    assert muse_cli.login_state({"HOME": str(tmp_path / "none")}) == {"logged_in": False, "subs_active": None}
    bad = tmp_path / "bad" / ".config" / "muse"
    bad.mkdir(parents=True)
    (bad / "auth.json").write_text("not json")
    assert muse_cli.login_state({"HOME": str(tmp_path / "bad")})["logged_in"] is False


def test_available_only_with_muse_on_path(tmp_path):
    empty = Router(env={"PATH": str(tmp_path / "empty")})
    assert not empty.credentials("muse-cli")
    r = router(tmp_path)
    assert r.credentials("muse-cli")
    assert [m["route"] for m in available_models(r, tmp_path / "h") if m["provider"] == "muse-cli"] == ["muse-cli:default"]
    assert not [m for m in available_models(empty, tmp_path / "h") if m["provider"] == "muse-cli"]


def _wait_dead(pid):
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def test_idle_timeout_kills_and_reports(tmp_path):
    r = router(tmp_path, "sleep")
    r.providers["muse-cli"] = {**r.providers["muse-cli"], "read_timeout": 0.5}
    end = run(r, [Message.user("hi")])[-1]
    assert end.kind == "error" and "ReadTimeout" in end.error
    assert _wait_dead(int((tmp_path / "pid").read_text()))


def test_cancel_kills_process_group(tmp_path):
    r = router(tmp_path, "sleep")
    pidf = tmp_path / "pid"

    async def go():
        async def drain():
            return [e async for e in r.stream("main", [Message.user("hi")], [])]
        t = asyncio.create_task(drain())
        for _ in range(100):
            if pidf.exists() and pidf.read_text():
                break
            await asyncio.sleep(0.05)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    asyncio.run(go())
    assert _wait_dead(int(pidf.read_text()))


# ---------------------------------------------------------------- status and test call (providers_setup)


def row(r):
    return next(x for x in ps.rows(r, probe_local=False) if x["provider"] == "muse-cli")


@pytest.fixture
def exo_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))


def test_status_rows(tmp_path):
    assert row(Router(env={"PATH": str(tmp_path / "empty")}))["status"] == "not installed"
    assert row(router(tmp_path, login=False))["status"] == "not logged in"
    ok = row(router(tmp_path))
    assert ok["status"] == "installed" and ok["ready"] and ok["needs"] == "cli"
    assert CANARY not in json.dumps(ok)


def test_test_call_ok(tmp_path, exo_home):
    res = ps.test_provider("muse-cli", router=router(tmp_path))
    assert res["ok"] and res["error_class"] == "" and res["latency_ms"] is not None
    a = seen(tmp_path)["argv"]
    assert a[0] == "exec" and "--disable-shell" in a and seen(tmp_path)["prompt"] == muse_cli.PROBE_PROMPT
    assert ps.load_state()["muse-cli"]["ok"]


@pytest.mark.parametrize("mode,cls", [("login", "no_key"), ("401", "auth"), ("429", "rate_limited"),
                                      ("keychain", "unsigned"), ("empty", "bad_response"), ("fail", "error")])
def test_test_call_error_classes_never_carry_cli_text(tmp_path, exo_home, mode, cls):
    res = ps.test_provider("muse-cli", router=router(tmp_path, mode))
    assert res["error_class"] == cls and not res["ok"]
    assert CANARY not in json.dumps(res) and "boom" not in res["detail"]


def test_test_call_precondition_classes(tmp_path, exo_home, monkeypatch):
    assert ps.test_provider("muse-cli", router=Router(env={"PATH": str(tmp_path / "e")}))["error_class"] == "not_installed"
    assert ps.test_provider("muse-cli", router=router(tmp_path, login=False))["error_class"] == "no_key"
    r = router(tmp_path)
    home_with_login(tmp_path, subs_active=False)
    assert ps.test_provider("muse-cli", router=r)["error_class"] == "billing"
    home_with_login(tmp_path)
    monkeypatch.setattr(ps, "MUSE_TEST_TIMEOUT", 0.5)
    assert ps.test_provider("muse-cli", router=router(tmp_path, "sleep"), timeout=0.5)["error_class"] == "timeout"
    monkeypatch.setattr(muse_cli, "signature_state", lambda exe: "unsigned")
    res = ps.test_provider("muse-cli", router=router(tmp_path))
    assert res["error_class"] == "unsigned" and "codesign -s -" in res["detail"]
