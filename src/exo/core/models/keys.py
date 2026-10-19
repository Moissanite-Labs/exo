"""API key lookup, one function for every provider (D-018).

Order, first hit wins:
  1. the environment variable named by the provider's ``key_env``;
  2. the macOS Keychain (skipped on other systems), service ``exo``, account = the variable name;
  3. Linux only: the Secret Service through ``secret-tool``, attributes ``service=exo account=<name>``
     (skipped when ``secret-tool`` is missing);
  4. ``~/.config/exo/keys.env`` (``NAME=value`` lines; path overridable with ``EXO_KEYS_FILE``),
     refused with a warning if its mode is looser than 0600.

``EXO_NO_KEYCHAIN=1`` and ``EXO_NO_SECRET_SERVICE=1`` skip steps 2 and 3 (tests and sandboxes).

Key values are never logged, printed or put in error text. Writing keys lives in
``exo.providers_setup`` (D-034).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator, Mapping, Sequence

log = logging.getLogger("exo.keys")
KEYCHAIN_SERVICE = "exo"
KEYCHAIN_TIMEOUT = 3.0
_warned_loose: set[str] = set()  # keys files already warned about in this process


def keys_file_path() -> Path:
    return Path(os.environ.get("EXO_KEYS_FILE") or "~/.config/exo/keys.env").expanduser()


def _run_lookup(argv: list[str]) -> str | None:
    """Run one secret-store lookup command. Quiet on a miss, a missing tool and a timeout."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip() if out.returncode == 0 else ""
    return value or None


def keychain_get(name: str) -> str | None:
    """Read one item from the macOS Keychain. Quiet on a miss, on other systems and on timeout."""
    if sys.platform != "darwin" or os.environ.get("EXO_NO_KEYCHAIN"):
        return None
    return _run_lookup(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"])


def secret_service_get(name: str) -> str | None:
    """Read one item from the Secret Service with ``secret-tool`` (Linux). Quiet when the tool is missing,
    on a miss and on timeout. Off unless ``secret-tool`` is on PATH."""
    if sys.platform == "darwin" or os.environ.get("EXO_NO_SECRET_SERVICE"):
        return None
    import shutil
    exe = shutil.which("secret-tool")
    if not exe:
        return None
    return _run_lookup([exe, "lookup", "service", KEYCHAIN_SERVICE, "account", name])


def _file_pairs(text: str) -> Iterator[tuple[str, str]]:
    """``(name, value)`` for each ``NAME=value`` line: comments and blanks skipped, ``export`` and
    matching quotes around the value removed."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        yield k.strip().removeprefix("export ").strip(), v


def file_get(name: str, path: Path | None = None) -> str | None:
    """Read ``name`` from the keys file. Refuses (and warns, once per file per process) when group or
    others can read it."""
    path = path or keys_file_path()
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return None
    if mode & 0o077:
        if str(path) not in _warned_loose:
            _warned_loose.add(str(path))
            log.warning("refusing to read %s: mode is %o, expected 600 (run: chmod 600 %s)", path, mode, path)
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for k, v in _file_pairs(text):
        if k == name:
            return v or None
    return None


_loaded: set[str] = set()   # values this process has looked up (Keychain ones included); never logged
_SECRET_NAME = ("_KEY", "_KEYS", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD", "_API_KEY")
MIN_SECRET_LEN = 8


def _find(name: str, env: Mapping[str, str] | None, sources: bool) -> tuple[str | None, str | None]:
    """``(where, value)`` for ``name``: ``env``, ``keychain``, ``secret-service`` or ``file``, first hit wins;
    ``(None, None)`` on a miss. ``sources=False`` reads ``env`` only. A value found is remembered for redaction."""
    env = os.environ if env is None else env
    where, value = "env", env.get(name)
    for src, get in (("keychain", keychain_get), ("secret-service", secret_service_get), ("file", file_get)):
        if value or not sources:
            break
        where, value = src, get(name)  # looked up at call time: tests patch these module globals
    if not value:
        return None, None
    if len(value) >= MIN_SECRET_LEN:
        _loaded.add(value)
    return where, value


def lookup_key(name: str, env: Mapping[str, str] | None = None, *, sources: bool = True) -> str | None:
    """The key for variable ``name``, or None. ``sources=False`` reads ``env`` only
    (tests and sandboxes)."""
    return _find(name, env, sources)[1]


def key_source(name: str, env: Mapping[str, str] | None = None, *, sources: bool = True) -> str | None:
    """Where the key for ``name`` comes from: ``env``, ``keychain``, ``secret-service``, ``file``, or None.
    Never the value. A value found here is remembered for redaction like any lookup."""
    return _find(name, env, sources)[0]


def remember_secret(value: str) -> None:
    """Teach redaction a key value now (a key just added in this process). Never logged."""
    if value and len(value) >= MIN_SECRET_LEN:
        _loaded.add(value)


def _keys_file_values(path: Path | None = None) -> list[str]:
    """Every value in the keys file, whatever its mode (this only feeds redaction)."""
    try:
        text = (path or keys_file_path()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = [v for _, v in _file_pairs(text)]
    return [v for v in out if len(v) >= MIN_SECRET_LEN]


def known_secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    """Key values this process holds: looked up before, in secret-named environment variables, or in the
    keys file. Longest first so a long value is cut out before a shorter one inside it."""
    env = os.environ if env is None else env
    vals = set(_loaded) | set(_keys_file_values())
    vals |= {v for k, v in env.items() if v and len(v) >= MIN_SECRET_LEN and k.upper().endswith(_SECRET_NAME)}
    return sorted(vals, key=len, reverse=True)


REDACTED = "[redacted]"


def secret_spans(text: str, values: Sequence[str], end: int | None = None) -> tuple[list[tuple[int, int]], int]:
    """The ``(start, stop)`` spans of ``text`` that redaction replaces, in text order, and the cut point ``end``.

    The values go in the order given. Each value's matches are found from the left without overlapping each other,
    as ``str.replace`` finds them, and a match that overlaps a span taken before it is skipped. All matches are
    found in ``text`` itself before any marker goes in, so no value can match across a ``[redacted]`` marker.

    With ``end``, a span must stop at or before it. A value's matches end at its first match that stops after
    ``end``, and ``end`` moves back to that match's start if it starts before ``end``. Each span that comes back
    stops at or before the ``end`` that comes back. ``shell._safe_cut`` cuts the stream there."""
    # masked is text with each span taken so far overwritten by pad, a character no value holds. So find never
    # returns a match that overlaps a span, and the skipped matches cost no Python steps.
    pad = next(c for c in map(chr, range(0x110000)) if not any(c in v for v in values))
    spans, masked = [], text
    for v in values:
        n = len(v)
        i, upto = (masked.find(v) if n else -1), len(text)
        if i < 0:
            continue
        while i >= 0:
            if end is not None and i + n > end:
                end, upto = min(end, i), i
                break
            spans.append((i, i + n))
            i = masked.find(v, i + n)
        masked = masked[:upto].replace(v, pad * n) + masked[upto:]  # the same matches the loop took
    return sorted(spans), len(text) if end is None else end


def redact_secrets(text: str, values: Sequence[str] | None = None, *, keep_markers: bool = False) -> str:
    """``text`` with every known key value replaced by ``[redacted]``. Values are never logged.

    The spans come from ``secret_spans``. ``keep_markers`` treats each ``[redacted]`` already in ``text`` as taken
    first, so no value matches across it, and text this function made with the same values comes back as it is."""
    if not text:
        return text
    values = known_secret_values() if values is None else values
    spans, _ = secret_spans(text, [REDACTED, *values] if keep_markers else values)
    parts: list[str] = []
    at = 0
    for a, b in spans:
        parts += (text[at:a], REDACTED)
        at = b
    return "".join(parts) + text[at:] if spans else text

