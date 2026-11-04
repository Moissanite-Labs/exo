"""Startup rollback when PluginManager lacks close() (#942 on main)."""
from types import SimpleNamespace

import pytest

from exo import compose
from exo.core.seats import Seat


class _StartupFail(Exception):
    pass


def test_startup_rollback_does_not_replace_error_when_plugin_manager_has_no_close(tmp_path, monkeypatch):
    cfg = SimpleNamespace(home=tmp_path, data={"plugin_config": {}}, principal=None, get=lambda k, d=None: d)
    monkeypatch.setattr(compose.Config, "load", lambda: cfg)
    monkeypatch.setattr(compose, "load_executor", lambda: (None, False))
    monkeypatch.setattr(compose, "configured", lambda *a, **kw: None)
    monkeypatch.setattr(compose, "load_seat", lambda *a: Seat("first", principal_store=str(tmp_path / "s")))

    class Manager:
        def __init__(self, **kw):
            pass

        def load_all(self, **kw):
            raise _StartupFail

    monkeypatch.setattr(compose, "PluginManager", Manager)
    with pytest.raises(_StartupFail):
        compose.create()
