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


@pytest.fixture(autouse=True)
def hermetic_env(tmp_path, monkeypatch):
    """The caller's bus seat, keys and peers directory must never reach a test (a launcher exports them)."""
    import os
    for k in list(os.environ):
        if k.startswith(("EXO_BUS_", "DIVAN_", "EXO_PEERS")) or k in ("EXO_SEAT", "XDG_RUNTIME_DIR"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("EXO_PEERS_DIR", str(tmp_path / "peers"))
    # D-034: no test reads or writes the real Keychain, Secret Service or keys file.
    monkeypatch.setenv("EXO_NO_KEYCHAIN", "1")
    monkeypatch.setenv("EXO_NO_SECRET_SERVICE", "1")
    monkeypatch.setenv("EXO_KEYS_FILE", str(tmp_path / "keys" / "keys.env"))
