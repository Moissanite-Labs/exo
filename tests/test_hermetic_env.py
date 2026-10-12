"""The repo-wide conftest keeps a live machine's environment out of every test (f_688c099e)."""
import os
from pathlib import Path


def test_no_live_exo_env_or_home(monkeypatch):
    assert not any(k.startswith(("EXO_BUS_", "DIVAN_", "XDG_")) for k in os.environ)
    assert "EXO_SEAT" not in os.environ
    home = Path.home().resolve()
    assert "pytest" in str(home)
    assert "pytest" in str(Path(os.environ["EXO_HOME"]).resolve())
    assert os.environ["EXO_NO_KEYCHAIN"] == "1"
