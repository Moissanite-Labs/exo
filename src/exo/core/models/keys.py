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


_loaded: set[str] = set()   # values this process has looked up (Keychain ones included); never logged
_SECRET_NAME = ("_KEY", "_KEYS", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD", "_API_KEY")
MIN_SECRET_LEN = 8


def lookup_key(name: str, env: Mapping[str, str] | None = None, *, sources: bool = True) -> str | None:
    """The key for variable ``name``, or None. ``sources=False`` reads ``env`` only
    (tests and sandboxes)."""
    env = os.environ if env is None else env
    value = env.get(name) or (keychain_get(name) or file_get(name) if sources else None)
    if value and len(value) >= MIN_SECRET_LEN:
        _loaded.add(value)
    return value or None


def _keys_file_values(path: Path | None = None) -> list[str]:
    """Every value in the keys file, whatever its mode (this only feeds redaction)."""
    try:
        text = (path or keys_file_path()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        v = line.partition("=")[2].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out.append(v)
    return [v for v in out if len(v) >= MIN_SECRET_LEN]


def known_secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    """Key values this process holds: looked up before, in secret-named environment variables, or in the
    keys file. Longest first so a long value is cut out before a shorter one inside it."""
    env = os.environ if env is None else env
    vals = set(_loaded) | set(_keys_file_values())
    vals |= {v for k, v in env.items() if v and len(v) >= MIN_SECRET_LEN and k.upper().endswith(_SECRET_NAME)}
    return sorted(vals, key=len, reverse=True)


def redact_secrets(text: str, values: list[str] | None = None) -> str:
    """``text`` with every known key value replaced by ``[redacted]``. Values are never logged."""
    if not text:
        return text
    for v in (known_secret_values() if values is None else values):
        if v in text:
            text = text.replace(v, "[redacted]")
    return text


def key_found(name: str, env: Mapping[str, str] | None = None, *, sources: bool = True) -> bool:
    return lookup_key(name, env, sources=sources) is not None
