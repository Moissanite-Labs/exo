"""API key lookup, one function for every provider (D-018).

Order, first hit wins:
  1. the environment variable named by the provider's ``key_env``;
  2. the macOS Keychain, service ``exo``, account = the variable name;
  3. ``~/.config/exo/keys.env`` (``NAME=value`` lines), refused with a warning
     if its mode is looser than 0600.

Key values are never logged, printed or put in error text.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

log = logging.getLogger("exo.keys")
KEYCHAIN_SERVICE = "exo"
KEYCHAIN_TIMEOUT = 3.0


def keys_file_path() -> Path:
    return Path(os.environ.get("EXO_KEYS_FILE") or "~/.config/exo/keys.env").expanduser()


def keychain_get(name: str) -> str | None:
    """Read one item from the macOS Keychain. Quiet on a miss, on other systems and on timeout."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
                             capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip() if out.returncode == 0 else ""
    return value or None


def file_get(name: str, path: Path | None = None) -> str | None:
    """Read ``name`` from the keys file. Refuses (and warns, once per call) when group or
    others can read it."""
    path = path or keys_file_path()
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return None
    if mode & 0o077:
        log.warning("refusing to read %s: mode is %o, expected 600 (run: chmod 600 %s)", path, mode, path)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().removeprefix("export ").strip()
        if k == name:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            return v or None
    return None


def lookup_key(name: str, env: Mapping[str, str] | None = None, *, sources: bool = True) -> str | None:
    """The key for variable ``name``, or None. ``sources=False`` reads ``env`` only
    (tests and sandboxes)."""
    env = os.environ if env is None else env
    if env.get(name):
        return env[name]
    if not sources:
        return None
    return keychain_get(name) or file_get(name)


def key_found(name: str, env: Mapping[str, str] | None = None, *, sources: bool = True) -> bool:
    return lookup_key(name, env, sources=sources) is not None
