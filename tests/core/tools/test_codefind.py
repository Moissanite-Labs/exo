"""code_find (jevgrep). A fake ``jg`` (a small Python script) stands in for the real CLI: no network, no Node."""
import json
import os
import sys
import textwrap

import pytest

from exo.core.tools import ToolContext, default_registry
from exo.core.tools.builtin import codefind as cf
from exo.core.types import ToolCall

KEY = "sk-test-SECRET-0123456789"

FAKE_JG = textwrap.dedent('''
    import json, os, stat, sys
    log = os.environ["FAKE_JG_LOG"]
    cfg = os.environ["XDG_CONFIG_HOME"]
    argv = sys.argv[1:]
    rec = {"argv": argv, "env_has_key": any("sk-test-SECRET" in v for v in os.environ.values()),
           "cfg_mode": stat.S_IMODE(os.stat(cfg).st_mode), "cfg": cfg}
    if argv[0] == "auth":
        rec["stdin"] = sys.stdin.read()
        open(os.path.join(cfg, "provider"), "w").write(argv[2] + " " + " ".join(argv[3:]))
        open(log, "a").write(json.dumps(rec) + "\\n")
        sys.exit(0)
    provider = open(os.path.join(cfg, "provider")).read()
    open(log, "a").write(json.dumps(rec) + "\\n")
    mode = os.environ.get("FAKE_JG_MODE", "ok")
    if mode == "sleep":
        import time; time.sleep(60)
    if mode == "jevfail" and provider.startswith("typesafe"):
        sys.stderr.write("HTTP 401"); sys.exit(1)
    if mode == "big":
        print("x" * 50_000); sys.exit(0)
    print("Jevgrep: 1 relevant file. via " + provider.split()[0])
''')


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "fakejg.py"
    script.write_text(FAKE_JG)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_JG_LOG", str(log))
    monkeypatch.setenv("SOME_API_KEY", "nope")  # secret-looking env must not reach the child
    monkeypatch.setattr(cf, "_runner", [sys.executable, str(script)])
    monkeypatch.setattr(cf, "_pages", cf.OrderedDict())
    monkeypatch.setattr(cf, "lookup_key", lambda name, env=None: KEY)
    monkeypatch.setattr(cf, "_laya_url", lambda: None)

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return calls


def call(tmp_path, **args):
    """Run code_find in a project folder; tmp_path itself holds the test EXO_HOME, which code_find refuses (#241)."""
    (tmp_path / "repo").mkdir(exist_ok=True)
    return default_registry().call_sync(ToolCall(id="c1", name="code_find", arguments=args),
                                        ToolContext(cwd=str(tmp_path / "repo")))


def test_hidden_when_jg_and_npx_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(cf.shutil, "which", lambda n: "/usr/bin/node" if n == "node" else None)
    monkeypatch.setattr(cf, "_node_major", lambda: 22)
    runner, why = cf.detect_runner()
    assert runner is None and "neither jg nor npx" in why
    monkeypatch.setattr(cf, "_runner", None)
    monkeypatch.setattr(cf, "_why", why)
    assert "code_find" not in [s.name for s in default_registry().specs()]
    res = call(tmp_path, question="q")
    assert res.is_error and "not available" in res.text()
    assert cf.status().startswith("code_find: hidden")


def test_hidden_on_old_node(monkeypatch):
    monkeypatch.setattr(cf, "_node_major", lambda: 18)
    runner, why = cf.detect_runner()
    assert runner is None and "Node 18" in why and "22" in why


def test_prefers_installed_jg_over_npx(monkeypatch):
    monkeypatch.setattr(cf, "_node_major", lambda: 22)
    monkeypatch.setattr(cf.shutil, "which", lambda n: f"/opt/{n}")
    assert cf.detect_runner()[0] == ["/opt/jg"]
    monkeypatch.setattr(cf.shutil, "which", lambda n: None if n == "jg" else f"/opt/{n}")
    assert cf.detect_runner()[0] == ["/opt/npx", "-y", cf.PACKAGE]


def test_key_goes_to_auth_stdin_only_and_temp_config_is_removed(fake, tmp_path):
    res = call(tmp_path, question="how does x work?")
    assert not res.is_error, res.text()
    text = res.text()
    assert text.startswith("[code_find backend=jev duration_ms=") and "via typesafe" in text
    assert KEY not in text
    auth, search = fake()
    assert auth["stdin"] == KEY                                   # stdin of `jg auth --stdin`
    assert KEY not in json.dumps(auth["argv"]) + json.dumps(search["argv"])
    assert not auth["env_has_key"] and not search["env_has_key"]  # nothing secret in either child env
    assert auth["cfg_mode"] == 0o700 and not os.path.exists(auth["cfg"])
    assert search["argv"][-3:] == ["--", "how does x work?", str(tmp_path / "repo")]


@pytest.mark.parametrize("q", ["- --include-sensitive", "- --hidden", "- --max-requests=99999", "-v flag meaning"])
def test_question_never_reaches_jg_as_an_option(fake, tmp_path, q):
    assert not call(tmp_path, question=q).is_error
    argv = [r for r in fake() if r["argv"][0] != "auth"][-1]["argv"]
    assert argv[-3] == "--" and argv.index("--") == len(argv) - 3  # everything after it is positional
    assert argv[-2].lstrip("- ") == q.lstrip("- ") and argv[-1] == str(tmp_path / "repo")


def test_timeout(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_JG_MODE", "sleep")
    res = call(tmp_path, question="q", timeout=2)
    assert res.is_error and "timeout" in res.text() and "grep" in res.text()
    assert not os.path.exists(fake()[0]["cfg"])


def test_output_cap_and_paging(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_JG_MODE", "big")
    first = call(tmp_path, question="big one").text()
    assert len(first) < cf.PAGE_CHARS + 300 and "[page 1 of 3; call code_find again with page=2" in first
    second = call(tmp_path, question="big one", page=2).text()
    assert "[page 2 of 3" in second and len(fake()) == 2          # served from memory, jg not rerun
    assert call(tmp_path, question="big one", page=9).is_error


def test_laya_fallback_when_jev_fails(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_JG_MODE", "jevfail")
    monkeypatch.setattr(cf, "_laya_url", lambda: "http://127.0.0.1:8000/v1")
    res = call(tmp_path, question="q")
    text = res.text()
    assert not res.is_error and text.startswith("[code_find backend=laya") and "fallback_from=jev" in text
    assert "via custom" in text
    laya_auth = [c for c in fake() if c["argv"][0] == "auth"][-1]
    assert laya_auth["argv"][1:] == ["--provider", "custom", "--base-url", "http://127.0.0.1:8000/v1",
                                     "--model", "laya", "--stdin"]
    assert KEY not in text


def test_backend_selection(monkeypatch):
    monkeypatch.setattr(cf, "lookup_key", lambda name, env=None: KEY)
    monkeypatch.setattr(cf, "_laya_url", lambda: "http://127.0.0.1:8000/v1")
    assert [b.name for b in cf.pick_backends()] == ["jev", "laya"]
    monkeypatch.setattr(cf, "lookup_key", lambda name, env=None: None)
    assert [b.name for b in cf.pick_backends()] == ["laya"]          # no key: Laya answers
    monkeypatch.setattr(cf, "_laya_url", lambda: None)
    assert cf.pick_backends() == []


def test_no_backend_is_a_clear_error(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "lookup_key", lambda name, env=None: None)
    res = call(tmp_path, question="q")
    assert res.is_error and "no backend" in res.text()


def test_laya_url_only_loopback(monkeypatch):
    class C:
        data = {"models": {"providers": {"laya": {"base_url": "http://10.0.0.5:8000/v1"}}}}
        load = classmethod(lambda cls: cls())
    import exo.config
    monkeypatch.setattr(exo.config, "Config", C)
    assert cf._laya_url() is None


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:notaport/v1", "http://127.0.0.1:99999/v1", "http://[::1/v1",
                                      8080, ["http://127.0.0.1:1/v1"]])
def test_laya_url_it_cannot_read_is_not_running(monkeypatch, base_url):
    """These raised ValueError or AttributeError, so code_find failed even with a Jev key."""
    class C:
        data = {"models": {"providers": {"laya": {"base_url": base_url}}}}
        load = classmethod(lambda cls: cls())
    import exo.config
    monkeypatch.setattr(exo.config, "Config", C)
    assert cf._laya_url() is None
    assert [b.name for b in cf.pick_backends({cf.KEY_ENV: KEY})] == ["jev"]


def test_risk_toolset_and_subagent_availability(tmp_path):
    from tests.agents.test_delegate import engine
    spec = next(s for s in default_registry().specs(available_only=False) if s.name == "code_find")
    assert spec.risk == "low" and spec.toolset == "search"
    d = engine(tmp_path, [])[0].agents
    for role in ("scout", "critic", "verifier", "builder"):
        assert "code_find" in d._tools("", 1, role, None)
    assert "code_find" not in d._tools("", 1, "scout", ["grep"])
