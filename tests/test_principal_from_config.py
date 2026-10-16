"""The principal named in $EXO_HOME/config.json is used when no flag or env var names one."""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "exo-memory"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path))
    monkeypatch.delenv("EXO_PRINCIPAL", raising=False)
    monkeypatch.delenv("EXO_STORE", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"principal": "pat"}))
    root = tmp_path / "principals" / "pat"
    (root / "nodes").mkdir(parents=True)
    (root / "PRINCIPAL.md").write_text("# pat\n")
    return tmp_path


def test_cli_resolve_store_uses_config(home):
    from exo.cli import resolve_store
    store = resolve_store(argparse.Namespace(root=None, principal=None))
    assert Path(store.root) == home / "principals" / "pat"


def test_env_beats_config(home, monkeypatch):
    from exo.cli import resolve_store
    monkeypatch.setenv("EXO_PRINCIPAL", "nobody")
    with pytest.raises(SystemExit):
        resolve_store(argparse.Namespace(root=None, principal=None))


def test_memory_plugin_uses_config(home):
    from exo_memory import config as C
    cfg = C.load(str(home))
    assert cfg["principal"] == "pat"
    assert C.store_root(cfg) == home / "principals" / "pat"


def test_memory_plugin_env_beats_config(home, monkeypatch):
    from exo_memory import config as C
    monkeypatch.setenv("EXO_PRINCIPAL", "other")
    assert C.load(str(home))["principal"] == "other"
