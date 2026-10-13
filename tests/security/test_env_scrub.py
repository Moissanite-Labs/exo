"""D-039: one secret scrub for every tool subprocess (bash and jobs, code_find) and an explicit minimal environment
for the claude CLI provider."""
from __future__ import annotations

import pytest

from exo import envscrub

SECRETS = {
    "OPENROUTER_API_KEY": "s", "MY_SERVICE_TOKEN": "s", "APP_SECRET": "s", "DB_PASSWORD": "s",
    "GOOGLE_APPLICATION_CREDENTIALS": "/p", "SIGNING_KEY": "s", "AWS_ACCESS_KEY_ID": "s",
    "AWS_SECRET_ACCESS_KEY": "s", "AWS_PROFILE": "p", "DATABASE_URL": "postgres://u:p@h/d", "SENTRY_DSN": "s",
    "GH_TOKEN": "s", "GH_ENTERPRISE_TOKEN": "s", "GITHUB_TOKEN": "s", "EXO_BUS_SEAT": "x", "EXO_KEYS_FILE": "/f",
    "EXOD_TOKEN": "t", "EXO_PRINCIPAL_TOKEN": "t", "EXO_SOCKET": "/s", "SECRET_KEY_BASE": "s",
    "STRIPE_APIKEY": "s",
}
KEEP = {"PATH": "/bin", "HOME": "/h", "LANG": "C", "EXO_HOME": "/h/.exo", "EXO_SEAT": "a", "KEEP_ME": "1",
        "EDITOR": "vi", "SSH_AUTH_SOCK": "/tmp/agent"}


def test_scrub_drops_secret_names_and_keeps_the_rest():
    out = envscrub.scrub_env({**SECRETS, **KEEP})
    leaked = sorted(k for k in SECRETS if k in out)
    assert leaked == [], leaked
    assert {k: out[k] for k in KEEP} == KEEP


def test_catalog_key_env_names_are_dropped(monkeypatch):
    monkeypatch.setattr(envscrub, "catalog_key_envs", lambda: frozenset({"OPENCODE_GO_THING"}))
    assert "OPENCODE_GO_THING" not in envscrub.scrub_env({"OPENCODE_GO_THING": "x", "A": "1"})


def test_seat_allow_keeps_and_seat_deny_drops():
    env = {"GH_TOKEN": "t", "AWS_PROFILE": "p", "COMPANY_URL": "u", "INTERNAL_HOST": "h", "KEEP": "1"}
    out = envscrub.scrub_env(env, allow=["GH_TOKEN"], deny=["COMPANY_URL", "INTERNAL_*"])
    assert out == {"GH_TOKEN": "t", "KEEP": "1"}
    # deny wins over allow: a seat that lists a name in both does not leak it
    assert "GH_TOKEN" not in envscrub.scrub_env(env, allow=["GH_TOKEN"], deny=["GH_*"])


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_every_subprocess_path_uses_the_one_scrub(name, monkeypatch):
    from exo.core.models.claude_cli import child_env
    from exo.core.tools.builtin.codefind import _child_env
    from exo.core.tools.builtin.shell import tool_env

    monkeypatch.setenv(name, "leak")
    assert name not in tool_env()
    assert name not in _child_env("/tmp/cfg")
    assert name not in child_env()


def test_tool_env_takes_the_seat_denylist(monkeypatch):
    from exo.core.tools.builtin.shell import tool_env

    monkeypatch.setenv("COMPANY_URL", "u")
    assert "COMPANY_URL" in tool_env()
    assert "COMPANY_URL" not in tool_env(deny=["COMPANY_*"])


def test_seat_parses_tools_env_deny():
    from exo.core import seats

    assert seats.parse({"name": "a", "tools": {"env_deny": ["COMPANY_*"]}}).env_deny == ["COMPANY_*"]
    assert seats.parse({"name": "a"}).env_deny == []


def test_bash_tool_and_jobs_apply_the_seat_denylist(tmp_path, monkeypatch):
    from exo.core.tools import ToolContext, default_registry
    from exo.core.types import ToolCall

    monkeypatch.setenv("COMPANY_URL", "visible-company")
    reg = default_registry()
    res = reg.call_sync(ToolCall(id="c1", name="bash", arguments={"command": "env"}),
                        ToolContext(cwd=str(tmp_path), meta={"env_deny": ["COMPANY_*"]}))
    assert "visible-company" not in res.text()


def test_claude_cli_gets_an_explicit_minimal_env(monkeypatch):
    from exo.core.models.claude_cli import child_env

    monkeypatch.setenv("RANDOM_PARENT_VAR", "x")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/c")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    env = child_env()
    assert "RANDOM_PARENT_VAR" not in env and "EXO_HOME" not in env
    assert env["PATH"] and env["HOME"] and env["CLAUDE_CONFIG_DIR"] == "/c" and env["HTTPS_PROXY"] == "http://proxy"
    assert env["LC_ALL"] == "en_US.UTF-8" and env["ENABLE_TOOL_SEARCH"] == "false"
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK"):
        monkeypatch.setenv(k, "x")
        assert k not in child_env()
    # an explicit env given to the provider is still filtered the same way
    assert "RANDOM_PARENT_VAR" not in child_env({"RANDOM_PARENT_VAR": "x", "PATH": "/bin", "HOME": "/h"})


@pytest.mark.parametrize("name,value", [
    ("REDIS_URL", "redis://:password@localhost/0"), ("MONGO_URI", "mongodb+srv://u:p@db.example/test"),
    ("MYSQL_PWD", "password"), ("DB_PASS", "password"), ("STRIPE_SK", "value"), ("TWILIO_AUTH", "value"),
    ("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/secret"), ("NPM_CONFIG__AUTH", "value"),
    ("DOCKER_AUTH_CONFIG", '{"auths":{}}'), ("KUBECONFIG", "/private/kube"), ("PGPASSFILE", "/private/pgpass"),
    ("PGSERVICEFILE", "/private/pgservice"), ("PGSSLKEY", "/private/pgkey"),
    ("SERVICE_URL", "https://service.example/?access_token=secret"),
    ("SIGNED_URI", "https://service.example/?sig=secret"),
    ("FRAGMENT_URL", "https://service.example/#token=secret"),
    ("SIGNED_URL", "https://service.example/?X-Amz-Credential=secret"), ("HTTPS_PROXY", "http://u:p@proxy.example"),
    ("ODD_URL", "redis://u:p@[malformed"), ("INNOCENT", "ghp_" + "a" * 30),
])
def test_confirmed_credential_families_and_url_values(name, value):
    assert name not in envscrub.scrub_env({name: value})
    assert envscrub.scrub_env({name: value}, allow=[name]) == {name: value}
    assert envscrub.scrub_env({name: value}, allow=[name], deny=[name]) == {}


def test_noncredential_urls_and_unknown_opaque_values_are_preserved():
    src = {"REDIS_URL": "redis://localhost:6379/0", "MONGO_URI": "mongodb://localhost/test",
           "DOCS_URL": "https://example.com/?page=1", "INNOCENT": "opaque-not-recognized"}
    assert envscrub.scrub_env(src) == src  # deliberate heuristic limit, not an arbitrary-secret guarantee
    assert "HTTPS_PROXY" not in envscrub.minimal_env({"HTTPS_PROXY": "http://u:p@proxy"})
