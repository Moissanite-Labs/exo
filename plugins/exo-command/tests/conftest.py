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
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("EXO_"):
            monkeypatch.delenv(k, raising=False)


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
