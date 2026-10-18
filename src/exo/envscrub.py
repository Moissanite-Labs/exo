"""One environment scrub for every subprocess Exo starts for the model (D-039).

Used by the bash tool and background jobs (``tool_env``) and by ``code_find``. The ``claude`` CLI provider does not
start from the parent environment at all: it gets ``minimal_env`` (a short allowlist) plus its own switches.

A variable is dropped when any of these holds:

- its name is a provider key the model catalog names (``key_env``);
- it ends in _API_KEY, _TOKEN, _SECRET, _PASSWORD, _CREDENTIALS, _KEY, _KEYS, _KEYS_DIR, _KEYS_FILE or _DSN;
- it contains TOKEN, SECRET, PASSWORD, PASSWD, CREDENTIAL, APIKEY, API_KEY, PRIVATE_KEY, ACCESS_KEY or _KEY_;
- it starts with AWS_, GH_, EXO_BUS_, EXOD_ or EXO_PRINCIPAL, or is GITHUB_TOKEN or ends in DATABASE_URL;
- it is an EXO_ variable that names a socket (contains SOCK): the core's socket and credential stay out of tools;
- it is a shell startup hook, matched by exact name only: bash itself acts on ``BASH_ENV``, an exported function
  (``BASH_FUNC_<name>%%``, or ``BASH_FUNC_<name>()`` in Red Hat-patched bash), ``SHELLOPTS`` and ``BASHOPTS`` when
  it starts; ``ENV`` is read by an interactive POSIX shell a command starts; ``PS4`` matters once xtrace is on;
- it is a known credential/configuration name (including database passwords, package/docker auth and kube config);
- its value has a recognized token/private-key format, or its URL/URI/proxy value carries userinfo or a credential query;
- it is git's environment config (GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n>, GIT_CONFIG_VALUE_<n>, GIT_CONFIG_PARAMETERS):
  the numbered family is kept or dropped as one unit, kept only when env_allow names GIT_CONFIG_COUNT (#159);
- it matches the seat's ``[tools] env_deny`` (exact names or shell patterns like ``COMPANY_*``).

A seat's ``[tools] env_allow`` (exact names) keeps a variable the rules above would drop. The denylist wins over
the allowlist. This is a heuristic: an unrecognized secret under an innocent name still passes, so keys belong in the key store,
not the environment. Loader and interpreter hooks (``LD_PRELOAD``, ``PYTHONPATH``, ``NODE_OPTIONS``,
``GIT_SSH_COMMAND`` and the like) pass: they have ordinary development uses, and docs/TOOLS.md says so.
"""
from __future__ import annotations

import fnmatch
import functools
import os
import re
from urllib.parse import parse_qsl, urlsplit
from typing import Iterable, Mapping

SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIALS", "_KEY", "_KEYS", "_KEYS_DIR",
            "_KEYS_FILE", "_DSN", "_PASS", "_PWD", "_AUTH")
WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "APIKEY", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY",
         "_KEY_")
PREFIXES = ("AWS_", "GH_", "EXO_BUS_", "EXOD_", "EXO_PRINCIPAL")
NAMES = frozenset({"GITHUB_TOKEN", "STRIPE_SK", "NPM_CONFIG__AUTH", "DOCKER_AUTH_CONFIG", "KUBECONFIG",
                   "PGPASSFILE", "PGSERVICEFILE", "PGSSLKEY", "SLACK_WEBHOOK_URL"})
# Shell startup hooks: exact case, never through ``is_secret_name`` (it case-folds and has other callers, and ``ENV=dev``
# or a ``?env=`` URL query is no secret). Exported functions arrive as ``BASH_FUNC_<name>%%`` (upstream bash since the
# 2014 Shellshock patches; ``BASH_FUNC_<name>()`` in Red Hat-patched bash) and shadow commands. ``PS4`` is expanded
# before every traced line once xtrace is on (``set -x`` in the command), so a ``$(...)`` in it runs a command.
EXEC_HOOKS = frozenset({"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4"})
VALUE_SECRET = re.compile(r"^(?:sk-|gh[pousr]_|xox[baprs]-|whsec_)[A-Za-z0-9_-]{16,}|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
URL_SECRETS = frozenset({"token", "access_token", "api_key", "apikey", "password", "passwd", "secret",
                        "auth", "authorization", "key", "signature", "sig"})
# Git's environment config: GIT_CONFIG_COUNT with GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n>, and GIT_CONFIG_PARAMETERS
# (git's own ``-c``). Values can carry credentials (an http.extraHeader bearer token) and update_cmd already refuses
# GIT_CONFIG*, so all of it is dropped by default. The numbered family is one unit: a count without its keys
# fails every git command (#159). env_allow naming GIT_CONFIG_COUNT keeps it whole; if env_deny or the token-format
# value check drops any member, all of it goes (a bearer header is not a recognized format). GIT_CONFIG_PARAMETERS is kept only when env_allow names it.
GIT_CONFIG_FAMILY = re.compile(r"GIT_CONFIG_(?:COUNT|KEY_[0-9]+|VALUE_[0-9]+)")

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


def is_exec_hook(name: str) -> bool:
    """A shell startup hook (exact name; see ``EXEC_HOOKS``): bash acts on ``BASH_ENV``, exported functions,
    ``SHELLOPTS`` and ``BASHOPTS`` at startup, interactive POSIX shells read ``ENV``, xtrace expands ``PS4``."""
    return name in EXEC_HOOKS or (name.startswith("BASH_FUNC_") and name.endswith(("%%", "()")))


def is_secret_value(name: str, value: str) -> bool:
    """Recognized token formats and credential-bearing URL/URI fields; not arbitrary secret detection."""
    if VALUE_SECRET.search(value.strip()):
        return True
    if not name.upper().endswith(("URL", "URI", "PROXY")):
        return False
    try:
        url = urlsplit(value)
        return bool(url.username is not None or url.password is not None
                    or any((k.lower() in URL_SECRETS or is_secret_name(k)) and v
                           for k, v in parse_qsl(url.query) + parse_qsl(url.fragment)))
    except ValueError:  # a malformed authority can still visibly carry userinfo
        return "://" in value and "@" in value.split("://", 1)[1].split("/", 1)[0]


def scrub_env(src: Mapping[str, str] | None = None, allow: Iterable[str] = (),
              deny: Iterable[str] = ()) -> dict[str, str]:
    """A copy of ``src`` (default ``os.environ``) without secrets or shell startup hooks; see the module docstring for the rules."""
    src = os.environ if src is None else src
    allowed = {str(a) for a in allow or ()}
    denied = [str(d) for d in deny or () if str(d)]
    keep_git = "GIT_CONFIG_COUNT" in allowed and not any(
        any(fnmatch.fnmatchcase(k, d) for d in denied) or (k not in allowed and is_secret_value(k, v))
        for k, v in src.items() if GIT_CONFIG_FAMILY.fullmatch(k))

    def drop(k: str, v: str) -> bool:
        if GIT_CONFIG_FAMILY.fullmatch(k):
            return not keep_git
        if k == "GIT_CONFIG_PARAMETERS" and k not in allowed:
            return True
        if any(fnmatch.fnmatchcase(k, d) for d in denied):
            return True
        return k not in allowed and (is_exec_hook(k) or is_secret_name(k) or is_secret_value(k, v))

    return {k: v for k, v in src.items() if not drop(k, v)}


def minimal_env(src: Mapping[str, str] | None = None) -> dict[str, str]:
    """Only the allowlisted basics from ``src``, and never a secret among them."""
    src = os.environ if src is None else src
    return {k: v for k, v in src.items()
            if (k in MINIMAL or k.startswith(MINIMAL_PREFIXES)) and not is_secret_name(k) and not is_secret_value(k, v)}
