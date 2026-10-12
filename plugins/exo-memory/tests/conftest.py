"""Fixtures for exo-memory tests. The provider runs on the Exo core base class
(``exo.core.slots.MemoryProvider``); nothing here needs Hermes or the network."""
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT))

from exo.store import PrincipalStore  # noqa: E402

from exo_memory import ExoMemoryProvider  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    (tmp_path / "exo-home").mkdir()
    for var in ("EXO_PRINCIPAL", "EXO_STORE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def store(tmp_path):
    return PrincipalStore.create(tmp_path / "store")


@pytest.fixture
def make_provider(tmp_path, store):
    made = []

    def make(root=None, session="s1", agent_context="primary", **cfg):
        conf = {"store_root": str(root or store.root), "principal": "pat", "agent_id": "agent-one",
                "compact_enabled": False, **cfg}
        p = ExoMemoryProvider(conf, summarizer=cfg.pop("summarizer", None))
        p.initialize(session, exo_home=str(tmp_path / "exo-home"), platform="cli",
                     agent_context=agent_context)
        made.append(p)
        return p

    yield make
    for p in made:
        p.shutdown()


@pytest.fixture
def provider(make_provider):
    return make_provider()


def events(p, kind=None):
    st = p._state()
    return list(st.rt.ledger.iter(kind=kind))
