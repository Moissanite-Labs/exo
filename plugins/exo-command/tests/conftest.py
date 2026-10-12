import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for p in (str(PLUGIN_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402

GENERIC_HOME = "/home/someone"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path_factory):
    for k in list(os.environ):
        if k.startswith("EXO_"):
            monkeypatch.delenv(k, raising=False)
    # D-034: never the real Keychain, Secret Service or keys file.
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")
    monkeypatch.setenv("EXO_KEYS_FILE", str(tmp_path_factory.mktemp("keys") / "keys.env"))


@pytest.fixture
def now():
    return time.time()


@pytest.fixture
def world(tmp_path, now):
    return fixtures.build(tmp_path, now, home_dir=GENERIC_HOME)


@pytest.fixture
def settings(world):
    from exo_command.config import load_settings
    return load_settings({"principal_store": str(world["store"]), "generic_paths": [GENERIC_HOME]})


def digest_tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out
