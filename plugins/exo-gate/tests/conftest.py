import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_fixtures import write_store  # noqa: E402  (also puts the plugin and src on sys.path)


@pytest.fixture
def example_store(tmp_path):
    return write_store(tmp_path / "store")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    for k in ("EXO_PRINCIPAL", "EXO_PRINCIPAL_STORE", "EXO_GATE_MODE", "EXO_GATE_PRINCIPAL_STORE"):
        monkeypatch.delenv(k, raising=False)
