"""Repo-wide hermetic environment (canary finding f_688c099e). Applies to the root suite and every plugin suite.

A launcher on a live machine exports EXO_BUS_URL, EXO_BUS_SEAT, EXO_HOME and friends, and ~/.exo may hold
real seats and keys. No test may see any of it. Every EXO_* and DIVAN_* variable is removed, and HOME and the
XDG dirs point into a temp dir, so a path derived from the home directory cannot reach the real one. A test
that needs a variable sets it itself (after this fixture runs)."""
import os

import pytest

_PREFIXES = ("EXO_", "DIVAN_", "XDG_")


@pytest.fixture(autouse=True)
def _repo_hermetic_env(tmp_path_factory, monkeypatch):
    for k in list(os.environ):
        if k.startswith(_PREFIXES):
            monkeypatch.delenv(k)
    monkeypatch.delenv("NO_COLOR", raising=False)
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")
    monkeypatch.setenv("EXO_KEYS_FILE", str(home / "keys.env"))
