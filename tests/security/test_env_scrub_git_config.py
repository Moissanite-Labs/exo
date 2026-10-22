"""#159: git's environment config family leaves the tool scrub whole or not at all. Dropping only
GIT_CONFIG_KEY_<n> (it matches ``_KEY_``) left a count without its keys, and every git command failed."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from exo import envscrub
from exo.core.tools.builtin.shell import tool_env

GIT_FAMILY = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "color.ui", "GIT_CONFIG_VALUE_0": "never"}


def test_git_config_family_is_dropped_whole_by_default():
    assert envscrub.scrub_env({**GIT_FAMILY, "KEEP": "1"}) == {"KEEP": "1"}
    assert envscrub.scrub_env({"GIT_CONFIG_PARAMETERS": "'http.extraheader'='x'", "KEEP": "1"}) == {"KEEP": "1"}
    # an exact allow of one member does not split the family
    assert envscrub.scrub_env(GIT_FAMILY, allow=["GIT_CONFIG_KEY_0"]) == {}
    files = {"GIT_CONFIG_GLOBAL": "/g", "GIT_CONFIG_SYSTEM": "/s", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG": "/c"}
    assert envscrub.scrub_env(files) == files  # file locations, not injected config


def test_git_config_family_is_kept_whole_when_the_seat_allows_the_count():
    assert envscrub.scrub_env(GIT_FAMILY, allow=["GIT_CONFIG_COUNT"]) == GIT_FAMILY
    assert envscrub.scrub_env({"GIT_CONFIG_PARAMETERS": "p"}, allow=["GIT_CONFIG_PARAMETERS"]) == {
        "GIT_CONFIG_PARAMETERS": "p"}
    assert envscrub.scrub_env({"GIT_CONFIG_PARAMETERS": "p"}, allow=["GIT_CONFIG_COUNT"]) == {}


@pytest.mark.parametrize("deny,extra", [
    (["GIT_CONFIG_VALUE_0"], {}), (["GIT_CONFIG_KEY_*"], {}), (["GIT_CONFIG_COUNT"], {}),
    ([], {"GIT_CONFIG_KEY_1": "http.extraHeader", "GIT_CONFIG_VALUE_1": "ghp_" + "a" * 30}),
])
def test_git_config_family_is_dropped_whole_when_any_member_is_dropped(deny, extra):
    env = {**GIT_FAMILY, **extra, "KEEP": "1"}
    assert envscrub.scrub_env(env, allow=["GIT_CONFIG_COUNT"], deny=deny) == {"KEEP": "1"}


@pytest.mark.parametrize("allow", [(), ("GIT_CONFIG_COUNT",)])
def test_git_runs_in_tool_env_with_the_git_config_family_set(allow, tmp_path, monkeypatch):
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not installed")
    for k in list(os.environ):
        if k.startswith("GIT_CONFIG_"):
            monkeypatch.delenv(k)
    subprocess.run([git, "init", "-q", str(tmp_path)], check=True, env=tool_env())
    for k, v in GIT_FAMILY.items():
        monkeypatch.setenv(k, v)
    env = tool_env(allow=allow)
    status = subprocess.run([git, "status", "--short"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert status.returncode == 0, status.stderr
    if allow:  # the kept family reaches git whole
        got = subprocess.run([git, "config", "--get", "color.ui"], cwd=tmp_path, env=env, capture_output=True,
                             text=True)
        assert got.stdout.strip() == "never"
    else:
        assert not any(k.startswith("GIT_CONFIG_") for k in env)
