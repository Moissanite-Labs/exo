"""One secret scrub for every subprocess Exo starts for the model (D-039).

Used by the bash tool and background jobs (``tool_env``) and by ``code_find``. The ``claude`` CLI provider does not
start from the parent environment at all: it gets ``minimal_env`` (a short allowlist) plus its own switches.

A variable is dropped when any of these holds:

- its name is a provider key the model catalog names (``key_env``);
- it ends in _API_KEY, _TOKEN, _SECRET, _PASSWORD, _CREDENTIALS, _KEY, _KEYS, _KEYS_DIR, _KEYS_FILE or _DSN;
- it contains TOKEN, SECRET, PASSWORD, PASSWD, CREDENTIAL, APIKEY, API_KEY, PRIVATE_KEY, ACCESS_KEY or _KEY_;
- it starts with AWS_, GH_, EXO_BUS_, EXOD_ or EXO_PRINCIPAL, or is GITHUB_TOKEN or ends in DATABASE_URL;
- it is an EXO_ variable that names a socket (contains SOCK): the core's socket and credential stay out of tools;
- it matches the seat's ``[tools] env_deny`` (exact names or shell patterns like ``COMPANY_*``).

A seat's ``[tools] env_allow`` (exact names) keeps a variable the rules above would drop. The denylist wins over
the allowlist. This is a heuristic: a secret under an innocent name still passes, so keys belong in the key store,
not the environment.
"""
from __future__ import annotations

import fnmatch
import functools
import os
from typing import Iterable, Mapping

SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIALS", "_KEY", "_KEYS", "_KEYS_DIR",
            "_KEYS_FILE", "_DSN")
WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "APIKEY", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY",
         "_KEY_")
PREFIXES = ("AWS_", "GH_", "EXO_BUS_", "EXOD_", "EXO_PRINCIPAL")
NAMES = frozenset({"GITHUB_TOKEN"})

# What the claude CLI may see from the parent: locale, paths, terminal, its own config, proxies and CA bundles.
MINIMAL = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "TZ", "CLAUDE_CONFIG_DIR",
           "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "HTTPS_PROXY", "HTTP_PROXY",
           "NO_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "no_proxy", "all_proxy", "NODE_EXTRA_CA_CERTS",
           "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "PYTHONPATH", "SYSTEMROOT")
MINIMAL_PREFIXES = ("LC_",)


@functools.lru_cache(maxsize=1)
def _catalog() -> frozenset[str]:
    try:
        from exo.core.models.router import load_catalog

        names: set[str] = set()
        for spec in (load_catalog().get("providers") or {}).values():
            v = spec.get("key_env") if isinstance(spec, dict) else None
            names.update([v] if isinstance(v, str) else [str(x) for x in v or []])
        return frozenset(n for n in names if n)
    except Exception:
        return frozenset()


def catalog_key_envs() -> frozenset[str]:
    """Every provider key variable the model catalog names (``key_env`` may be a string or a list)."""
    return _catalog()


def is_secret_name(name: str) -> bool:
    u = name.upper()
    return (name in catalog_key_envs() or u in NAMES or u.endswith(SUFFIXES) or u.endswith("DATABASE_URL")
            or any(w in u for w in WORDS) or u.startswith(PREFIXES) or (u.startswith("EXO") and "SOCK" in u))


def scrub_env(src: Mapping[str, str] | None = None, allow: Iterable[str] = (),
              deny: Iterable[str] = ()) -> dict[str, str]:
    """A copy of ``src`` (default ``os.environ``) without secrets; see the module docstring for the rules."""
    src = os.environ if src is None else src
    allowed = {str(a) for a in allow or ()}
    denied = [str(d) for d in deny or () if str(d)]

    def drop(k: str) -> bool:
        if any(fnmatch.fnmatchcase(k, d) for d in denied):
            return True
        return k not in allowed and is_secret_name(k)

    return {k: v for k, v in src.items() if not drop(k)}


def minimal_env(src: Mapping[str, str] | None = None) -> dict[str, str]:
    """Only the allowlisted basics from ``src``, and never a secret among them."""
    src = os.environ if src is None else src
    return {k: v for k, v in src.items()
            if (k in MINIMAL or k.startswith(MINIMAL_PREFIXES)) and not is_secret_name(k)}
