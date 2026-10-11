"""Fixtures for exo-memory tests.

The Hermes checkout is used read-only for the real MemoryProvider ABC: it goes at the END of
sys.path, bytecode writing is off, and nothing is installed into it. Set HERMES_AGENT_DIR to
point elsewhere. Without it the provider falls back to its stand-in base class.
"""
import os
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True  # never write .pyc files into the Hermes checkout

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HERMES_DIR = Path(os.environ.get("HERMES_AGENT_DIR") or Path.home() / ".hermes" / "hermes-agent")

HERMES_IMPORT_ERROR = ""
if HERMES_DIR.is_dir():
    if str(HERMES_DIR) not in sys.path:
        sys.path.append(str(HERMES_DIR))
    try:
        import agent.memory_provider  # noqa: F401
    except Exception as exc:  # report why in the smoke test
        HERMES_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    HERMES_IMPORT_ERROR = f"no Hermes checkout at {HERMES_DIR}"

sys.path.insert(0, str(PLUGIN_ROOT))

from exo.store import PrincipalStore  # noqa: E402

from exo_memory import ExoMemoryProvider  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    (tmp_path / "hermes-home").mkdir()
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
        p.initialize(session, hermes_home=str(tmp_path / "hermes-home"), platform="cli",
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
