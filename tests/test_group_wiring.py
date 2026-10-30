"""Bounded trusted-host injection: compose.create(..., group_host=...) with patched config.

The host is an explicit trusted-Python-caller opt-in, never config/protocol/tool input. Validation
(owner = engine agent, GroupStore wraps the engine's session store) runs BEFORE the engine is
constructed, so a mismatch creates nothing. Close awaits the host before releasing owner resources.
"""
import asyncio
from types import SimpleNamespace

import pytest

from exo import compose
from exo.core.group_host import GroupHost, HostError
from exo.core.groups import GroupStore
from exo.core.sessions import SessionStore


class _Composed(Exception):  # raised by the patched PluginManager before any engine exists
    pass


def _config(tmp_path, monkeypatch):
    cfg = SimpleNamespace(home=tmp_path, data={}, principal=None,
                          get=lambda key, default=None: default)
    monkeypatch.setattr(compose.Config, "load", lambda: cfg)  # no live config
    monkeypatch.setattr(compose, "load_executor", lambda: (None, False))
    monkeypatch.setattr(compose, "configured", lambda *a, **kw: None)
    monkeypatch.setattr(compose, "load_seat", lambda *a: None)  # agent is "default"
    monkeypatch.delenv("EXO_PRINCIPAL", raising=False)

    class Manager:
        def __init__(self, **kw):
            pass

        def load_all(self, **kw):
            raise _Composed  # proof we got past host validation, before any engine is built

    monkeypatch.setattr(compose, "PluginManager", Manager)
    return cfg


def _host(owner, store, factory=None):
    return GroupHost(store, owner=owner, known_profiles={"a", "b"},
                     engine_factory=factory or (lambda saved: {}))


def _store(path):
    return GroupStore(SessionStore(str(path)))


def run(coroutine):
    return asyncio.run(asyncio.wait_for(coroutine, 10))


def test_host_reaches_engine_when_valid(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    host = _host("default", _store(tmp_path / "sessions.sqlite"))
    with pytest.raises(_Composed):  # validation passed; creation stopped before the engine exists
        compose.create(group_host=host)


def test_owner_mismatch_fails_before_engine(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    host = _host("other-agent", _store(tmp_path / "sessions.sqlite"))
    with pytest.raises(HostError):  # no _Composed: rejected before PluginManager ran
        compose.create(group_host=host)


def test_home_mismatch_fails_before_engine(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    host = _host("default", _store(tmp_path / "elsewhere" / "sessions.sqlite"))
    with pytest.raises(HostError):
        compose.create(group_host=host)


def test_wrong_type_fails_closed(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    with pytest.raises(HostError):
        compose.create(group_host=object())


def test_default_create_has_no_host(tmp_path, monkeypatch):
    _config(tmp_path, monkeypatch)
    with pytest.raises(_Composed):  # unchanged default path; no host validation involved
        compose.create()


class _RecordingHost:  # minimal CoreEngine stand-in harness pieces are real; only the host is fake
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def aclose(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("host cleanup failed")


def _engine(tmp_path):
    from exo.core.engine import CoreRuntime
    eng = CoreRuntime.__new__(CoreRuntime)  # close() ordering only needs the touched attributes
    eng._group_host = None
    eng.jobs = SimpleNamespace(target=SimpleNamespace(closed=False), close=lambda: _none())
    eng._writer = None
    eng.stop_channels = lambda: _none()
    eng._stop_peers = lambda: _none()
    eng._close_scheduler = lambda: _none()
    eng._close_mcp = lambda: _none()
    eng._on_harness_error = None
    eng.store = SimpleNamespace(close=lambda: None)
    return eng


async def _none():
    return None


def test_close_awaits_host_before_teardown(tmp_path, monkeypatch):
    eng = _engine(tmp_path)
    host = _RecordingHost()
    eng._group_host = host
    order = []
    host.aclose = lambda: (order.append("host"), _none())[1]
    store_close = eng.store.close
    eng.store.close = lambda: order.append("store") or store_close()
    run(eng.close())
    assert eng._group_host is host  # a completed close never drops a healthy bound host
    assert order == ["host", "store"]


def test_close_host_failure_keeps_owner_resources(tmp_path):
    eng = _engine(tmp_path)
    host = _RecordingHost(fail=True)
    eng._group_host = host
    with pytest.raises(RuntimeError):
        run(eng.close())
    assert host.calls == 1  # retained for an explicit retry
    assert eng._group_host is host
    assert not eng.jobs.target.closed  # teardown never started; store/peers untouched


def test_close_cancellation_keeps_owner_resources(tmp_path):
    eng = _engine(tmp_path)
    host = _RecordingHost()

    async def hanging():
        await asyncio.sleep(60)

    host.aclose = hanging
    eng._group_host = host

    async def cancel_close():
        task = asyncio.ensure_future(eng.close())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(cancel_close())
    assert not eng.jobs.target.closed  # owner resources stay available behind the active runtime
    assert eng._group_host is host
