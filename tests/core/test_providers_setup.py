"""Providers setup (D-034): key stores, status, live test, default, and the rule that a key value never comes
back out. Hermetic: temp EXO_HOME and keys file (root conftest), fake key-store tools, fake HTTP."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess

import pytest

from exo import providers_cmd, providers_setup as ps
from exo.core.models import keys
from exo.core.models.router import Router
from exo.protocol import schema as s

FAKE = "sk-fake-PROVIDERS-0123456789abcdef"


@pytest.fixture(autouse=True)
def no_real_stores(monkeypatch):
    """Any key-store tool call goes to a recorder, never to `security` or `secret-tool`."""
    calls = []

    def run(argv, stdin=None):
        calls.append((list(argv), stdin))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(ps, "_run", run)
    monkeypatch.setattr(keys, "keychain_get", lambda name: None)
    monkeypatch.setattr(keys, "secret_service_get", lambda name: None)
    for k in list(os.environ):
        if k.endswith(("_API_KEY", "_TOKEN")):
            monkeypatch.delenv(k)
    return calls


def router(**kw):
    return Router(env={}, key_sources=True, **kw)


def keyfile():
    return keys.keys_file_path()


# ---------------------------------------------------------------- keys.env


def test_keys_file_written_0600_atomically_and_keeps_other_lines(tmp_path):
    f = keyfile()
    f.parent.mkdir(parents=True)
    f.write_text("# mine\nOTHER=keep-this-value-1\nOPENAI_API_KEY=old-value-123456\n")
    f.chmod(0o644)  # loose: the rewrite also fixes it
    ps.write_keys_file("OPENAI_API_KEY", FAKE)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    lines = f.read_text().splitlines()
    assert lines == ["# mine", "OTHER=keep-this-value-1", f"OPENAI_API_KEY={FAKE}"]
    assert {p.name for p in f.parent.iterdir()} == {"keys.env", "keys.env.lock"}  # persistent lock, no temp file


def test_keys_file_write_is_atomic_on_failure(monkeypatch):
    f = keyfile()
    ps.write_keys_file("A_API_KEY", "first-value-123456")
    before = f.read_text()

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(ps.os, "replace", boom)
    with pytest.raises(OSError):
        ps.write_keys_file("B_API_KEY", FAKE)
    assert f.read_text() == before
    assert {p.name for p in f.parent.iterdir()} == {"keys.env", "keys.env.lock"}


def test_new_keys_file_and_folder_are_private():
    ps.write_keys_file("A_API_KEY", FAKE)
    f = keyfile()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(f.parent.stat().st_mode) & 0o077 == 0


def test_set_key_file_store_redaction_learns_at_once_and_status_has_no_value(monkeypatch):
    keys._loaded.discard(FAKE)
    out = ps.set_key("openai", FAKE, router=router(), store="file")
    assert out == {"provider": "openai", "var": "OPENAI_API_KEY", "stored_in": "file", "shadowed_by_env": False,
                   "effective_source": "file", "shadowed_by": None, "warning": ""}
    assert keys.redact_secrets(f"oops {FAKE} here") == "oops [redacted] here"
    assert FAKE in keys.known_secret_values()
    table = ps.rows(router(), probe_local=False)
    row = next(r for r in table if r["provider"] == "openai")
    assert row["key"] == "found" and row["source"] == "file" and row["status"] == "key found"
    assert FAKE not in json.dumps(table) and FAKE not in ps.render(table)


def test_redaction_learns_even_when_the_store_fails(monkeypatch):
    keys._loaded.discard(FAKE)
    monkeypatch.setattr(ps, "_run", lambda argv, stdin=None: subprocess.CompletedProcess(argv, 1, "", ""))
    with pytest.raises(ps.SetupError) as e:
        ps.set_key("openai", FAKE, router=router(), store="keychain")
    assert FAKE not in str(e.value)
    assert keys.redact_secrets(FAKE) == "[redacted]"


# ---------------------------------------------------------------- Keychain and Secret Service (mocked)


def test_keychain_store_keeps_the_key_out_of_argv(no_real_stores):
    out = ps.set_key("openai", FAKE, router=router(), store="keychain")
    assert out["stored_in"] == "keychain"
    (argv, stdin), = no_real_stores
    assert argv == ["security", "-i"] and FAKE not in " ".join(argv)
    assert stdin == f'add-generic-password -U -s exo -a OPENAI_API_KEY -w "{FAKE}"\n'
    assert not keyfile().exists()


def test_keychain_refusal_is_reported_without_the_key(monkeypatch):
    monkeypatch.setattr(ps, "_run", lambda argv, stdin=None: subprocess.CompletedProcess(
        argv, 0, f"add-generic-password: returned -25308 {stdin}", ""))
    with pytest.raises(ps.SetupError) as e:
        ps.set_key("openai", FAKE, router=router(), store="keychain")
    assert "Keychain" in str(e.value) and FAKE not in str(e.value)


def test_secret_service_store_reads_the_key_from_stdin(no_real_stores):
    ps.set_key("openrouter", FAKE, router=router(), store="secret-service")
    (argv, stdin), = no_real_stores
    assert argv[:2] == ["secret-tool", "store"] and FAKE not in " ".join(argv)
    assert argv[-4:] == ["service", "exo", "account", "OPENROUTER_API_KEY"] and stdin == FAKE


def test_default_store_by_platform(monkeypatch):
    monkeypatch.delenv("EXO_NO_KEYCHAIN", raising=False)
    monkeypatch.delenv("EXO_NO_SECRET_SERVICE", raising=False)
    which = {"security": "/usr/bin/security", "secret-tool": "/usr/bin/secret-tool"}.get
    assert ps.default_store("darwin", which) == "keychain"
    assert ps.default_store("linux", which) == "secret-service"
    assert ps.default_store("linux", lambda n: None) == "file"
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    assert ps.default_store("darwin", which) == "file"


def test_remove_key_everywhere_exo_writes(monkeypatch, no_real_stores):
    monkeypatch.delenv("EXO_NO_KEYCHAIN", raising=False)
    monkeypatch.setattr(ps.sys, "platform", "darwin")
    ps.write_keys_file("OPENAI_API_KEY", FAKE)
    ps.write_keys_file("OTHER_API_KEY", "other-value-123456")
    out = ps.remove_key("openai", router=router(), env={"OPENAI_API_KEY": "x"})
    assert out == {"provider": "openai", "removed_from": ["file", "keychain"], "still_in_env": ["OPENAI_API_KEY"]}
    assert no_real_stores[0][0] == ["security", "delete-generic-password", "-s", "exo", "-a", "OPENAI_API_KEY"]
    assert keyfile().read_text() == "OTHER_API_KEY=other-value-123456\n"


@pytest.mark.parametrize("bad", ["", "sh0rt-x", "has space in-it-0123", 'quote"d-0123456789', "back\\slash-0123456",
                                 "x" * 5000, "unicodé-0123456789"])
def test_bad_keys_are_refused_without_echo(bad):
    with pytest.raises(ps.SetupError) as e:
        ps.check_key(bad)
    assert (bad.strip() not in str(e.value)) or not bad.strip()


def test_keyless_and_unknown_providers():
    with pytest.raises(ps.SetupError, match="takes no API key"):
        ps.set_key("ollama", FAKE, router=router())
    with pytest.raises(ps.SetupError, match="unknown provider"):
        ps.set_key("nope", FAKE, router=router())


# ---------------------------------------------------------------- status


def test_rows_status_shapes(monkeypatch, tmp_path):
    monkeypatch.setattr(ps.shutil, "which", lambda name, path=None: None)
    r = router(providers={"anthropic": {"transport": "anthropic", "base_url": "https://a", "key_env": "ANTHROPIC_API_KEY"}})
    r.env = {"ANTHROPIC_API_KEY": FAKE}
    table = {x["provider"]: x for x in ps.rows(r, prober=lambda url: "11434" in url)}
    assert table["anthropic"]["source"] == "env" and table["anthropic"]["default"] is True
    assert table["ollama"]["status"] == "running" and table["ollama"]["needs"] == "server"
    assert table["vllm"]["status"] == "not running"
    assert table["claude-cli"]["status"] == "not installed"
    assert table["openai"]["status"] == "no key"
    assert FAKE not in json.dumps(table)
    assert ps.any_ready(r, prober=lambda url: False) is True
    r.env = {}
    assert ps.any_ready(r, prober=lambda url: False) is False
    assert ps.any_ready(r, prober=lambda url: "11434" in url) is True


@pytest.mark.parametrize("url", ["http://127.0.0.1:notaport/v1", "http://127.0.0.1:99999/v1", "http://[::1/v1",
                                 "http://" + "a" * 70 + ".local:1/v1"])
def test_probe_reads_a_url_it_cannot_parse_as_not_running(url):
    """These raised ValueError, so the provider check and every engine start with such a local provider failed."""
    assert ps.probe(url, timeout=0.1) is False


@pytest.mark.parametrize("value", [8080, ["http://127.0.0.1:1/v1"]])
def test_probe_reads_a_base_url_that_is_not_text_as_not_running(value):
    """A config can hold a number or a list here. They raised AttributeError and TypeError, as a bad port did."""
    assert ps.probe(value, timeout=0.1) is False


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:notaport/v1", 8080])
def test_engine_start_with_a_bad_local_port_succeeds(tmp_path, base_url):
    from exo.core.engine import CoreEngine
    models = {"providers": {"loc": {"transport": "openai", "local": True, "base_url": base_url}}}

    async def main():
        eng = CoreEngine(tmp_path / "home", models, cwd=str(tmp_path), env={})
        assert eng._automatic_main  # so the start runs the provider check
        await eng.start()
        await eng.close()
    asyncio.run(main())


def test_rows_provider_without_base_url_cannot_answer_even_with_its_key(monkeypatch):
    monkeypatch.setattr(ps.shutil, "which", lambda name, path=None: None)
    r = Router(catalog={"providers": {}, "models": {}, "roles": {}}, env={"MINE_API_KEY": FAKE},
               providers={"mine": {"transport": "openai", "key_env": "MINE_API_KEY"}}, roles={"main": ["mine:m"]})
    [row] = ps.rows(r, probe_local=False)
    assert (row["key"], row["source"], row["status"], row["ready"]) == ("found", "env", "no base_url", False)
    assert r.credentials("mine") == [] and ps.any_ready(r, prober=lambda url: False) is False
    assert "no base_url" in ps.render([row]) and FAKE not in ps.render([row])


# ---------------------------------------------------------------- live test


def fake_http(status=200, data=None, exc=None, seen=None):
    def http(method, url, headers, body, timeout):
        if seen is not None:
            seen.append((method, url, dict(headers), body))
        if exc is not None:
            raise exc
        return status, data
    return http


def keyed():
    r = router()
    r.env = {"OPENAI_API_KEY": FAKE, "ANTHROPIC_API_KEY": FAKE, "TYPESAFE_API_KEY": FAKE}
    return r


@pytest.mark.parametrize("status,data,cls", [
    (200, {"data": [{"id": "a"}, {"id": "b"}]}, ""),
    (200, {"nothing": 1}, "bad_response"),
    (401, None, "auth"), (403, None, "auth"), (402, None, "billing"), (404, None, "not_found"),
    (429, None, "rate_limited"), (500, None, "server"), (503, None, "server"), (418, None, "http"),
])
def test_error_classes_by_status(status, data, cls):
    res = ps.test_provider("openai", router=keyed(), http=fake_http(status, data))
    assert res["error_class"] == cls and res["ok"] == (cls == "")
    if not cls:
        assert res["models"] == 2
    assert FAKE not in json.dumps(res)
    assert res["detail"] == f"HTTP {status}"


@pytest.mark.parametrize("exc,provider,cls", [
    (TimeoutError(), "openai", "timeout"), (ConnectionError(), "openai", "network"),
    (ConnectionError(), "ollama", "not_running"), (RuntimeError(f"boom {FAKE}"), "openai", "error"),
])
def test_error_classes_by_exception_never_carry_text(exc, provider, cls):
    res = ps.test_provider(provider, router=keyed(), http=fake_http(exc=exc))
    assert res["error_class"] == cls and not res["ok"]
    assert FAKE not in json.dumps(res) and "boom" not in res["detail"]


def test_test_call_shapes_and_saved_state():
    seen = []
    ps.test_provider("anthropic", router=keyed(), http=fake_http(200, {"data": [{"id": "m"}]}, seen=seen))
    method, url, headers, body = seen[-1]
    assert (method, url) == ("GET", "https://api.anthropic.com/v1/models?limit=1")
    assert headers["x-api-key"] == FAKE and "anthropic-version" in headers
    ps.test_provider("jev", router=keyed(), http=fake_http(200, {"answers": {"q": {"noul": 0.9}}}, seen=seen))
    method, url, headers, body = seen[-1]
    assert (method, url) == ("POST", "https://api.typesafe.ai/v1/systemone")
    assert body["model"] == "jev-latest" and body["questions"]["q"]["type"] == "noul"
    state = ps.load_state()
    assert state["anthropic"]["ok"] and state["jev"]["ok"]
    assert FAKE not in ps._state_path(None).read_text()
    assert stat.S_IMODE(ps._state_path(None).stat().st_mode) == 0o600
    row = next(x for x in ps.rows(keyed(), probe_local=False) if x["provider"] == "anthropic")
    assert row["status"] == "verified" and row["verified"]


def test_no_key_and_cli_not_installed(monkeypatch):
    res = ps.test_provider("openai", router=router(), http=fake_http(exc=AssertionError("must not call")))
    assert res["error_class"] == "no_key"
    monkeypatch.setattr(ps.shutil, "which", lambda name, path=None: None)
    assert ps.test_provider("claude-cli", router=router())["error_class"] == "not_installed"


def test_new_key_clears_the_old_test_result():
    ps.test_provider("openai", router=keyed(), http=fake_http(401))
    assert ps.load_state()["openai"]["error_class"] == "auth"
    ps.set_key("openai", FAKE, router=router(), store="file")
    assert "openai" not in ps.load_state()


# ---------------------------------------------------------------- default


def test_set_default_keeps_fallbacks_and_validates():
    from exo.config import Config
    routes = ps.set_default("openai:gpt-5", router=router())
    assert routes[0] == "openai:gpt-5" and "anthropic:claude-opus-5-5" in routes
    assert Config.load().get("models.roles.main") == routes
    again = ps.set_default("anthropic:claude-opus-5-5", router=router())
    assert again[:2] == ["anthropic:claude-opus-5-5", "openai:gpt-5"]
    with pytest.raises(ValueError):
        ps.set_default("nope:x", router=router())
    with pytest.raises(ValueError):
        ps.set_default("jev:jev-latest", router=router())  # decision-only provider


# ---------------------------------------------------------------- core command and router


def test_router_reload_forgets_cached_credentials():
    r = router()
    assert r.credentials("openai") == []
    r.env = {"OPENAI_API_KEY": FAKE}
    assert r.credentials("openai") == []  # cached
    r.cooldowns["openai"] = object()
    assert r.reload_credentials("openai") == ["openai"]
    assert len(r.credentials("openai")) == 1 and "openai" not in r.cooldowns


def test_core_providers_command_reload_and_status(tmp_path):
    from exo.core.engine import CoreEngine
    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"]}, "fake": {"rules": []}}, cwd=str(tmp_path), env={})

    async def go():
        out = (await eng._command_run(s.CommandRun(name="providers", args="reload openai")))["output"]
        status = (await eng._command_run(s.CommandRun(name="providers")))["output"]
        return out, status
    out, status = asyncio.run(go())
    assert out == {"reloaded": ["openai"]}
    assert {r["provider"] for r in status["providers"]} >= {"openai", "anthropic"}


# ---------------------------------------------------------------- CLI


def cli(argv, capsys):
    rc = providers_cmd.main(argv)
    o = capsys.readouterr()
    return rc, o.out + o.err


@pytest.mark.parametrize("argv", [["add", "openai", FAKE], ["add", "openai", f"--key={FAKE}"],
                                  ["add", "openai", "--store", FAKE], ["test", FAKE, "x"], [FAKE], ["test", FAKE],
                                  ["add", FAKE], ["remove", FAKE, "--yes"], ["default", FAKE]])
def test_cli_never_takes_or_echoes_a_key_from_argv(argv, capsys):
    try:
        rc, out = cli(argv, capsys)
    except SystemExit as e:
        rc, out = e.code, "".join(capsys.readouterr())
    assert rc != 0 and FAKE not in out
    assert not keyfile().exists()


def test_cli_add_needs_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(providers_cmd.getpass, "getpass", lambda prompt: pytest.fail("must not prompt"))
    rc, out = cli(["add", "openai"], capsys)
    assert rc == 2 and "terminal" in out


def test_cli_add_prompts_without_echo_and_stores(monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    monkeypatch.setattr(providers_cmd.getpass, "getpass", lambda prompt: prompts.append(prompt) or FAKE)
    monkeypatch.setattr(ps, "_http", fake_http(401))
    rc, out = cli(["add", "openai", "--store", "file"], capsys)
    assert rc == 0 and prompts and "hidden" in prompts[0]
    assert "stored OPENAI_API_KEY in file" in out and "failed (auth)" in out
    assert FAKE not in out and FAKE not in caplog.text
    assert f"OPENAI_API_KEY={FAKE}" in keyfile().read_text()
    rc, out = cli([], capsys)
    assert "OPENAI_API_KEY (file)" in out and "failed: auth" in out and FAKE not in out
    rc, out = cli(["remove", "openai", "--yes"], capsys)
    assert rc == 0 and "removed from: file" in out and FAKE not in keyfile().read_text()


def test_cli_default_and_test(monkeypatch, capsys):
    rc, out = cli(["default", "openai:gpt-5"], capsys)
    assert rc == 0 and out.startswith("main: openai:gpt-5 > ")
    monkeypatch.setattr(ps, "_http", fake_http(200, {"data": [{"id": "a"}]}))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE)
    rc, out = cli(["test", "openai"], capsys)
    assert rc == 0 and "openai: ok" in out and "1 models" in out and FAKE not in out
    rc, out = cli(["test", "nope"], capsys)
    assert rc == 1 and "unknown provider" in out


def test_every_output_is_free_of_the_key(monkeypatch, capsys, caplog, tmp_path):
    """Inject one fake key, drive every path, then grep everything the code produced."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(providers_cmd.getpass, "getpass", lambda prompt: FAKE)
    monkeypatch.setattr(ps, "_http", fake_http(exc=RuntimeError(FAKE)))
    outputs = []
    for argv in (["add", "openai", "--store", "file"], ["test", "openai"], [], ["remove", "openai", "--yes"],
                 ["add", "anthropic", "--store", "keychain", "--no-test"]):
        outputs.append(cli(argv, capsys)[1])
    outputs.append(json.dumps(ps.rows(probe_local=False)))
    outputs.append(caplog.text)
    home = os.environ["EXO_HOME"]
    for root, _, files in os.walk(home):
        outputs += [open(os.path.join(root, f), errors="replace").read() for f in files]
    blob = "\n".join(outputs)
    assert FAKE not in blob
    assert "stored ANTHROPIC_API_KEY in keychain" in blob
