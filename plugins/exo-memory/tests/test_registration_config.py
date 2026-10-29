from types import SimpleNamespace

from exo_memory import register


def test_registration_binds_explicit_store_before_availability(tmp_path, monkeypatch):
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "nodes").mkdir()
    monkeypatch.setenv("EXO_STORE", str(ambient))
    providers = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        (root / "nodes").mkdir()
        config = {"store_root": str(root), "exo_home": str(tmp_path / "home"), "agent_id": name}
        ctx = SimpleNamespace(get_explicit_config=config.get, register_memory_provider=providers.append)
        register(ctx)
        assert providers[-1].is_available()
        assert providers[-1].identity_signature()["exo-memory.store"] == str(root)
        assert providers[-1].identity_signature()["exo-memory.agent"] == name
    assert providers[0].identity_signature()["exo-memory.store"] != providers[1].identity_signature()["exo-memory.store"]
    missing = {"store_root": str(tmp_path / "missing"), "exo_home": str(tmp_path / "home")}
    register(SimpleNamespace(get_explicit_config=missing.get, register_memory_provider=providers.append))
    assert not providers[-1].is_available()
