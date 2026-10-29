"""Seat store selection happens before plugin discovery, without touching live stores."""
import os
from types import SimpleNamespace

import pytest

from exo import compose
from exo.core.seats import Seat


class Composed(Exception):
    pass


def test_seat_memory_overrides_before_load(tmp_path, monkeypatch):
    original = {"exo-memory": {"store_root": "old", "compact_enabled": False}}
    cfg = SimpleNamespace(home=tmp_path, data={"plugin_config": original}, principal=None,
                          get=lambda key, default=None: default)
    monkeypatch.setattr(compose.Config, "load", lambda: cfg)
    monkeypatch.setattr(compose, "load_executor", lambda: (None, False))
    monkeypatch.setattr(compose, "configured", lambda *a, **kw: None)
    monkeypatch.setenv("EXO_STORE", "ambient-store")
    monkeypatch.delenv("EXO_PRINCIPAL", raising=False)
    seen = []

    class Manager:
        def __init__(self, **kw):
            seen.append(kw["config"])

        def load_all(self, **kw):
            raise Composed

    monkeypatch.setattr(compose, "PluginManager", Manager)
    for name in ("first", "second"):
        monkeypatch.setattr(compose, "load_seat", lambda *a: Seat(name, principal_store=str(tmp_path / name)))
        with pytest.raises(Composed):
            compose.create(name)
        assert seen[-1]["exo-memory"] == {
            "store_root": str(tmp_path / name), "compact_enabled": False,
            "agent_id": name, "exo_home": str(tmp_path),
        }
    assert original["exo-memory"]["store_root"] == "old"
    assert os.environ["EXO_STORE"] == "ambient-store"
    assert "EXO_PRINCIPAL" not in os.environ
