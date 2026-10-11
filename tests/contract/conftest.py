"""Contract tests against a Hermes Agent checkout.

Set EXO_HERMES_DIR to a Hermes checkout (for example the one made by
scripts/pin-hermes.sh). Without it every test here is skipped.

Run with the Python that has Hermes's dependencies installed:

    EXO_HERMES_DIR=~/.exo/hermes PYTHONPATH=~/.exo/hermes \\
        <hermes-venv>/bin/python -m pytest --confcutdir tests/contract tests/contract

`--confcutdir` stops pytest from loading the repo's top-level conftest, which
imports the exo package and is not needed here.
"""

from __future__ import annotations

import importlib
import os
import pwd
import sys
import tempfile
from pathlib import Path

import pytest

_SANDBOX = None


def _hermes_dir() -> Path | None:
    raw = os.environ.get("EXO_HERMES_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def pytest_collection_modifyitems(config, items):
    if _hermes_dir() is not None:
        return
    skip = pytest.mark.skip(reason="EXO_HERMES_DIR is not set; contract tests need a Hermes checkout")
    for item in items:
        if "tests/contract" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def hermes_dir() -> Path:
    path = _hermes_dir()
    if path is None:
        pytest.skip("EXO_HERMES_DIR is not set")
    if not (path / "hermes_cli" / "plugins.py").is_file():
        pytest.fail(f"EXO_HERMES_DIR={path} does not look like a Hermes Agent checkout")
    return path


@pytest.fixture(scope="session", autouse=False)
def hermes_sandbox(hermes_dir):
    """Put the checkout first on sys.path and keep Hermes away from any real home.

    HOME and HERMES_HOME point into a temporary directory unless the caller
    already set HERMES_HOME somewhere else on purpose.
    """
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = ""
        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        live = real_home / ".hermes"
        hh = os.environ.get("HERMES_HOME", "")
        need_hh = not hh or Path(hh).expanduser().resolve() == live
        need_home = Path(os.environ.get("HOME", str(real_home))).resolve() == real_home
        if need_hh or need_home:
            _SANDBOX = tempfile.mkdtemp(prefix="exo-contract-")
        if need_hh:
            os.environ["HERMES_HOME"] = os.path.join(_SANDBOX, "hermes-home")
            os.makedirs(os.environ["HERMES_HOME"], exist_ok=True)
        if need_home:
            os.environ["HOME"] = os.path.join(_SANDBOX, "home")
            os.makedirs(os.environ["HOME"], exist_ok=True)
    if str(hermes_dir) not in sys.path:
        sys.path.insert(0, str(hermes_dir))
    return hermes_dir


@pytest.fixture(scope="session")
def hermes_import(hermes_sandbox):
    """Import a Hermes module and assert it came from EXO_HERMES_DIR."""

    def _import(name: str):
        mod = importlib.import_module(name)
        origin = Path(getattr(mod, "__file__", "") or "").resolve()
        assert hermes_sandbox in origin.parents, (
            f"{name} imported from {origin}, not from EXO_HERMES_DIR={hermes_sandbox}. "
            "Put the checkout first on PYTHONPATH."
        )
        return mod

    return _import
