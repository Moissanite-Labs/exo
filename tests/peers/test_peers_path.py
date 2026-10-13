"""A long HOME must not stop the core: the peer socket path has to fit a Unix socket (issue #48)."""
import os
import shutil
import tempfile

from exo.compose import fitting_peers_dir
from exo.core.peers import peers_dir


def _fits(d) -> bool:
    return len(os.fsencode(str(d / "exo-9999999.sock"))) < 104


def test_short_home_keeps_the_default():
    env = {"HOME": "/home/a"}
    assert fitting_peers_dir(env) is None  # None: the engine's own default stands
    assert _fits(peers_dir(env))


def test_long_home_falls_back_to_a_short_private_dir(monkeypatch):
    short = tempfile.mkdtemp(prefix="x", dir="/tmp")  # pytest's own tmp_path is itself too long on macOS
    monkeypatch.setattr("tempfile.gettempdir", lambda: short)
    env = {"HOME": "/home/" + "x" * 120}
    d = fitting_peers_dir(env)
    assert d is not None and _fits(d)
    try:
        assert d.is_dir() and (d.stat().st_mode & 0o077) == 0
    finally:
        shutil.rmtree(short)


def test_explicit_peers_dir_is_never_replaced():
    env = {"HOME": "/home/" + "x" * 120, "EXO_PEERS_DIR": "/srv/peers"}
    assert fitting_peers_dir(env) is None
