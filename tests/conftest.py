import pytest

from exo import crypto
from exo.store import PrincipalStore


@pytest.fixture
def signer():
    return crypto.Signer("tester", crypto.keygen())


@pytest.fixture
def template_store(tmp_path):
    return PrincipalStore.create(tmp_path / "store")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    monkeypatch.delenv("EXO_PRINCIPAL", raising=False)
