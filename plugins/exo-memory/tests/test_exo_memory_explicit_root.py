"""A store made with `exo init <id> --root DIR` is recorded in the core config as `principals.<id>`. The plugin
finds it by id the way the store commands do (`exo.store.principal_root`), instead of a missing store under
`<exo_home>/principals/<id>` that left memory off."""
import json

from exo.store import PrincipalStore

from exo_memory import ExoMemoryProvider
from exo_memory import config as C


def _home(tmp_path, root):
    home = tmp_path / "exo-home"  # EXO_HOME, set by conftest
    (home / "config.json").write_text(json.dumps({"principal": "pat", "principals": {"pat": str(root)}}))
    return home


def test_store_root_reads_the_recorded_root(tmp_path, monkeypatch):
    root = PrincipalStore.create(tmp_path / "elsewhere" / "pat").root
    home = _home(tmp_path, root)
    assert C.store_root(C.load(str(home))) == root
    monkeypatch.setenv("EXO_PRINCIPAL", "other")  # an id with no recorded root keeps the default
    assert C.store_root(C.load(str(home))) == home / "principals" / "other"
    monkeypatch.setenv("EXO_STORE", str(tmp_path / "x"))  # an explicit store_root still wins
    assert C.store_root(C.load(str(home))) == tmp_path / "x"


def test_provider_runs_on_the_recorded_root(tmp_path):
    root = PrincipalStore.create(tmp_path / "elsewhere" / "pat").root
    home = _home(tmp_path, root)
    p = ExoMemoryProvider({"compact_enabled": False})
    assert p.is_available(), p.unavailable_reason()
    p.initialize("s1", exo_home=str(home), platform="cli")
    try:
        assert p._state().rt.root == root.resolve()
        assert (root / "ledger.sqlite").is_file() and not (home / "principals").exists()
        assert "## Principal beliefs" in p.prefetch("communication style and reporting")
    finally:
        p.shutdown()
