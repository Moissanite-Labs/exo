"""Providers setup (D-034): one engine behind `exo providers`, the TUI `/providers` panel and the web Providers view.

What it does:
  - ``rows``: every catalog provider with its status: key found and where (env, keychain, secret-service,
    file; never the value), local server reachable, last live test, model count, roles, default.
  - ``set_key`` / ``remove_key``: store or delete a key. macOS: the Keychain (service ``exo``). Linux: the
    Secret Service through ``secret-tool`` when present, else ``keys.env``. ``keys.env`` is written 0600,
    atomically (temp file in the same folder, fsync, rename).
  - ``test_provider``: one tiny live call (list models, or one decision question), with latency and an
    error class.
  - ``set_default``: the same thing `/default` does: the ref goes first in ``models.roles.<role>``, old
    routes stay behind it as fallbacks, saved to config.json.

Keys are write-only. No function here returns a stored value, and none takes one except ``set_key``.
A value passed to ``set_key`` is taught to redaction (``keys.remember_secret``) before anything else.
Error text is built from classes and status codes, never from exception text that could carry a header.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .config import Config, exo_home
from .core.models import keys as keys_mod
from .core.models import muse_cli
from .core.models.keys import KEYCHAIN_SERVICE, keys_file_path, redact_secrets, remember_secret
from .core.models.router import Router
from .core.models.urls import display_url
from .core.private import atomic_write, private_dir

STORES = ("keychain", "secret-service", "file")
MIN_KEY, MAX_KEY = keys_mod.MIN_SECRET_LEN, 4096
# Printable ASCII without spaces, quotes or backslashes: every real API key fits, and the value can be
# written to keys.env and to `security -i` without escaping.
KEY_RE = re.compile(r"[!#-&(-\[\]-~]+")
SUBPROCESS_TIMEOUT = 5.0
PROBE_TIMEOUT = 0.3
TEST_TIMEOUT = 15.0
CLI_TRANSPORTS = ("claude-cli", "muse-cli")  # keyless: the CLI's own login is the credential
MUSE_TEST_TIMEOUT = 90.0  # a `muse exec` process start plus one model turn
ERROR_CLASSES = ("no_key", "auth", "billing", "not_found", "rate_limited", "server", "timeout", "network",
                 "not_running", "not_installed", "unsigned", "bad_response", "http", "error")


class SetupError(Exception):
    """A setup step failed. The message never holds a key value."""


def custom_provider(name: object, endpoint: object, protocol: object, auth: object, model: object) -> tuple[str, dict]:
    """Validate the supported custom HTTP transports and bind a key name to the endpoint.

    Endpoint changes use a different secret reference, so an existing credential cannot silently follow it.
    Only non-secret metadata belongs in config.json.
    """
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
        raise SetupError("Use a provider name with lowercase letters, numbers, hyphens or underscores.")
    if protocol not in ("openai", "anthropic") or auth not in ("key", "none"):
        raise SetupError("Choose a supported protocol and authentication method.")
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in endpoint):
        raise SetupError("Enter a valid HTTP or HTTPS base URL without spaces.")
    try:
        u = urlsplit(endpoint)
        if "?" in endpoint or "#" in endpoint or u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None \
                or u.query or u.fragment or "\\" in u.netloc or u.netloc.endswith(":"):
            raise ValueError()
        _ = u.port
    except ValueError:
        raise SetupError("Use an HTTP or HTTPS base URL without credentials, query or fragment.") from None
    if not isinstance(model, str) or not model or len(model) > 200 - len("custom-") - len(name) - 1 \
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model):
        raise SetupError("Enter a model ID without spaces.")
    from .config import ConfigError, check_not_secret
    try:
        check_not_secret("models.providers", {"base_url": endpoint, "setup_model": model})
    except ConfigError:
        raise SetupError("The URL or model looks like a secret. Put API keys only in the key field.") from None
    name = "custom-" + name
    endpoint = endpoint.rstrip("/")
    spec: dict[str, Any] = {"transport": protocol, "base_url": endpoint, "setup_managed": True,
                            "setup_model": model, "setup_auth": auth}
    if auth == "key":
        digest = hashlib.sha256(f"{name}\0{protocol}\0{endpoint}".encode()).hexdigest()[:32].upper()
        spec["key_env"] = "EXO_CUSTOM_" + digest + "_API_KEY"
    return name, spec


def _custom_status_id(spec: Mapping[str, Any]) -> str:
    """Bind a probe to non-secret configuration so edits cannot inherit an old result."""
    fields = ("transport", "base_url", "setup_auth", "setup_model", "key_env")
    value = json.dumps([spec.get(field) for field in fields], ensure_ascii=True)
    return hashlib.sha256(value.encode()).hexdigest()


# --- helpers -----------------------------------------------------------------------------------

def _run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    """Run a key-store tool. Tests replace this. Output is captured and never shown (it may echo input)."""
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
                          stdin=None if stdin is not None else subprocess.DEVNULL)


def build_router(home: str | os.PathLike | None = None, env: Mapping[str, str] | None = None) -> Router:
    return Router.from_config(Config.load(home).models(), env=env)


def key_vars(spec: Mapping[str, Any]) -> list[str]:
    envs = spec.get("key_env") or []
    return [envs] if isinstance(envs, str) else list(envs)


def _spec(router: Router, provider: str) -> dict:
    spec = router.providers.get(provider)
    if spec is None:
        known = ", ".join(sorted(router.providers))  # the name is not repeated: it could be a pasted key
        raise SetupError(f"unknown provider; known: {known}")
    return spec


def default_store(platform: str | None = None, which: Callable[[str], str | None] = shutil.which) -> str:
    """Where a new key goes on this machine."""
    platform = platform or sys.platform
    if platform == "darwin" and which("security") and not os.environ.get("EXO_NO_KEYCHAIN"):
        return "keychain"
    if platform.startswith("linux") and which("secret-tool") and not os.environ.get("EXO_NO_SECRET_SERVICE"):
        return "secret-service"
    return "file"


def check_key(value: str) -> str:
    """The key, stripped, or SetupError. The message never repeats the value."""
    if not isinstance(value, str):
        raise SetupError("the key must be text")
    value = value.strip()
    if not value:
        raise SetupError("no key given")
    if len(value) < MIN_KEY:
        raise SetupError(f"that is too short for an API key (under {MIN_KEY} characters)")
    if len(value) > MAX_KEY:
        raise SetupError("that is too long for an API key")
    if not KEY_RE.fullmatch(value):
        raise SetupError("the key has spaces, quotes, backslashes or non-ASCII characters; paste only the key")
    return value


# --- state (last test per provider, no secrets) ------------------------------------------------

def _state_path(home: str | os.PathLike | None) -> Path:
    return Path(home or exo_home()) / "state" / "providers.json"


def load_state(home: str | os.PathLike | None = None) -> dict[str, Any]:
    try:
        data = json.loads(_state_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(home: str | os.PathLike | None, provider: str, record: dict[str, Any] | None) -> None:
    state = load_state(home)
    if record is None:
        state.pop(provider, None)
    else:
        state[provider] = record
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


# --- key stores --------------------------------------------------------------------------------

@contextmanager
def _keys_file_lock(path: Path):
    """Serialize cooperating file writers on a persistent, owner-only lock inode."""
    private_dir(path.parent)
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise OSError(errno.EINVAL, "keys lock must be a regular file")
        if stat.S_IMODE(mode) != 0o600:
            os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # never unlink: another writer may already be waiting on this inode


def _keyfile_lines(path: Path, drop: str) -> list[str]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, encoding="utf-8") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            raise OSError(errno.EINVAL, "keys file must be a regular file")
        text = fh.read()
    out = []
    for line in text.splitlines():
        k = line.strip().partition("=")[0].strip().removeprefix("export ").strip()
        if "=" in line and not line.strip().startswith("#") and k == drop:
            continue
        out.append(line)
    return out


def write_keys_file(name: str, value: str, path: Path | None = None) -> Path:
    """Set ``name`` in keys.env under a process-safe lock, atomically, mode 0600."""
    path = path or keys_file_path()
    with _keys_file_lock(path):
        lines = _keyfile_lines(path, name) + [f"{name}={value}"]
        atomic_write(path, "\n".join(lines) + "\n")
    return path


def remove_from_keys_file(name: str, path: Path | None = None) -> bool:
    path = path or keys_file_path()
    if not path.exists() and not path.is_symlink():
        return False
    with _keys_file_lock(path):
        before = _keyfile_lines(path, "\0")
        after = _keyfile_lines(path, name)
        if before == after:
            return False
        atomic_write(path, "\n".join(after) + ("\n" if after else ""))
    return True


def _keychain_set(name: str, value: str) -> None:
    # `security -i` reads the command from stdin, so the value never sits in argv (visible in `ps`).
    line = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {name} -w "{value}"\n'
    try:
        out = _run(["security", "-i"], stdin=line)
    except (OSError, subprocess.SubprocessError) as e:
        raise SetupError(f"could not reach the Keychain ({type(e).__name__})") from None
    if out.returncode != 0 or "returned" in (out.stdout or "") + (out.stderr or ""):
        raise SetupError("the Keychain refused the key (is it locked?)")


def _keychain_delete(name: str) -> bool:
    try:
        out = _run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name])
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def _secret_tool_set(name: str, value: str) -> None:
    try:  # secret-tool reads the secret from stdin
        out = _run(["secret-tool", "store", f"--label=exo {name}", "service", KEYCHAIN_SERVICE, "account", name],
                   stdin=value)
    except (OSError, subprocess.SubprocessError) as e:
        raise SetupError(f"could not reach the Secret Service ({type(e).__name__})") from None
    if out.returncode != 0:
        raise SetupError("the Secret Service refused the key (is a keyring unlocked?)")


def _secret_tool_delete(name: str) -> bool:
    try:
        out = _run(["secret-tool", "clear", "service", KEYCHAIN_SERVICE, "account", name])
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def set_key(provider: str, value: str, *, router: Router | None = None, home: str | os.PathLike | None = None,
            store: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Store the key for ``provider`` under its first ``key_env`` name. Returns where it went, never the value."""
    router = router or build_router(home)
    spec = _spec(router, provider)
    names = key_vars(spec)
    if not names:
        raise SetupError(f"{provider} takes no API key")
    value = check_key(value)
    remember_secret(value)  # first: from here on, redaction knows it in this process
    where = store or default_store()
    if where not in STORES:
        raise SetupError(f"store must be one of {', '.join(STORES)}")
    name = names[0]
    try:
        if where == "keychain":
            _keychain_set(name, value)
        elif where == "secret-service":
            _secret_tool_set(name, value)
        else:
            write_keys_file(name, value)
    except SetupError:
        raise
    except Exception as e:  # never str(e): an OS error could quote a path or a buffer
        raise SetupError(f"could not store the key ({type(e).__name__})") from None
    _save_state(home, provider, None)  # an older test result says nothing about this key
    env = router.env if env is None else env
    effective = keys_mod.key_source(name, env, sources=router.key_sources)
    priority = ("env", *STORES)
    shadow = effective if effective and priority.index(effective) < priority.index(where) else None
    warning = ""
    if shadow == "env":
        warning = (f"The environment variable {name} wins over the saved key. Update it, or unset it in the "
                   "shell that launches Exo and restart Exo.")
    elif shadow:
        remove = (f"security delete-generic-password -s {KEYCHAIN_SERVICE} -a {name}" if shadow == "keychain"
                  else f"secret-tool clear service {KEYCHAIN_SERVICE} account {name}")
        warning = (f"The {shadow} key still wins; the saved {where} key is shadowed. Update the effective key "
                   f"with `exo providers add {provider} --store {shadow}`, or remove only that item with "
                   f"`{remove}`, then reload or restart Exo.")
    elif effective is None:
        warning = ("This router reads environment credentials only; the saved key is not used here."
                   if not router.key_sources else
                   "The key was saved, but no readable effective key was found. Check the store and reload or restart Exo.")
    elif effective != where:
        warning = (f"Lookup currently uses {effective}; the saved {where} key was not confirmed readable. "
                   f"Check or unlock {where}, then reload or restart Exo.")
    return {"provider": provider, "var": name, "stored_in": where, "shadowed_by_env": bool(env.get(name)),
            "effective_source": effective, "shadowed_by": shadow, "warning": warning}


def remove_key(provider: str, *, router: Router | None = None, home: str | os.PathLike | None = None,
               env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Delete the provider's key from every store Exo writes. An environment variable cannot be removed
    from here; it is reported instead."""
    router = router or build_router(home)
    names = key_vars(_spec(router, provider))
    if not names:
        raise SetupError(f"{provider} takes no API key")
    removed: list[str] = []
    for name in names:
        if sys.platform == "darwin" and not os.environ.get("EXO_NO_KEYCHAIN") and _keychain_delete(name):
            removed.append("keychain")
        if (sys.platform.startswith("linux") and shutil.which("secret-tool") and not os.environ.get("EXO_NO_SECRET_SERVICE")
                and _secret_tool_delete(name)):
            removed.append("secret-service")
        if remove_from_keys_file(name):
            removed.append("file")
    _save_state(home, provider, None)
    env = os.environ if env is None else env
    return {"provider": provider, "removed_from": sorted(set(removed)),
            "still_in_env": [n for n in names if env.get(n)]}


# --- status ------------------------------------------------------------------------------------

def probe(base_url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """Is something listening at ``base_url``'s host and port? One TCP connect, nothing sent."""
    u = urlsplit(base_url)
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _model_count(home: Path, provider: str, spec: Mapping[str, Any]) -> int | None:
    if spec.get("models"):
        return len(spec["models"])
    try:
        return len(json.loads((home / "cache" / "models" / f"{provider}.json").read_text())["ids"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def rows(router: Router | None = None, home: str | os.PathLike | None = None, *,
         probe_local: bool = True, prober: Callable[[str], bool] = probe) -> list[dict[str, Any]]:
    """Every catalog provider with its status. No key value or URL userinfo."""
    router = router or build_router(home)
    h = Path(home or exo_home())
    state = load_state(home)
    main = router.roles.get("main") or []
    default = main[0] if main else ""
    out = []
    for name, spec in sorted(router.providers.items()):
        names = key_vars(spec)
        where = None
        for n in names:
            where = keys_mod.key_source(n, router.env, sources=router.key_sources)
            if where:
                break
        transport = spec.get("transport", "openai")
        local = bool(spec.get("local"))
        reachable = prober(spec.get("base_url", "")) if local and probe_local else None
        cli = transport in CLI_TRANSPORTS
        installed = bool(shutil.which(spec.get("command", transport.removesuffix("-cli")), path=router.env.get("PATH", ""))) \
            if cli else None
        logged_in = muse_cli.login_state(router.env)["logged_in"] if transport == "muse-cli" and installed else None
        last = state.get(name) if isinstance(state.get(name), dict) else None
        if spec.get("setup_managed") is True and last and last.get("configuration") != _custom_status_id(spec):
            last = None
        if names:
            needs, has = "key", bool(where)
        elif cli:
            needs, has = "cli", bool(installed) and logged_in is not False
        elif spec.get("setup_managed") is True and spec.get("setup_auth") == "none":
            # A keyless custom endpoint is configured, not verified or known to be running.
            needs, has = "server", True
        else:
            needs, has = "server", bool(reachable)
        if not cli and not spec.get("base_url") and name not in router.instances:  # the router gives it no route
            status, has = "no base_url", False
        elif last and not last.get("ok") and has:
            status = f"failed: {last.get('error_class') or 'error'}"
        elif last and last.get("ok") and has:
            status = "verified"
        elif needs == "key":
            status = "key found" if has else "no key"
        elif needs == "cli":
            status = "installed" if has else ("not logged in" if installed else "not installed")
        elif spec.get("setup_managed") is True and spec.get("setup_auth") == "none":
            status = "configured"
        else:
            status = "running" if has else ("not running" if reachable is False else "local")
        out.append({
            "provider": name, "transport": transport, "base_url": display_url(spec.get("base_url", "")), "local": local,
            "key_env": names, "needs": needs, "key": ("n/a" if not names else "found" if where else "missing"),
            "source": where, "reachable": reachable, "installed": installed, "ready": has,
            "verified": bool(last and last.get("ok") and has), "last_test": last, "status": status,
            "models": _model_count(h, name, spec), "roles": list(spec.get("roles") or []),
            "serves": sorted(r for r, refs in router.roles.items() if any(x.split(":", 1)[0] == name for x in refs or [])),
            "default": bool(default) and default.split(":", 1)[0] == name,
        })
    return out


def any_ready(router: Router | None = None, home: str | os.PathLike | None = None, *,
              prober: Callable[[str], bool] = probe) -> bool:
    """True when at least one provider that can serve the main role has a key, a running local server, or an
    installed CLI. No network beyond a loopback connect."""
    for r in rows(router, home, prober=prober):
        if r["ready"] and (not r["roles"] or "main" in r["roles"]) and r["transport"] != "decision":
            return True
    return False


def main_status(router: Router, home: str | os.PathLike, *, automatic: bool = False,
                table: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Configured main readiness; call off the event loop (keys/local probes).

    Credentials without a known main model require the existing model chooser.
    Only Claude's chat-only static catalog supplies an automatic missing route.
    """
    table = rows(router, home, prober=probe) if table is None else table
    ready = {r["provider"] for r in table if r["ready"] and
             (not r["roles"] or "main" in r["roles"]) and r["transport"] != "decision"}
    route = next((r.ref for r in router.chain("main") if r.provider in ready), "")
    suggested = ""
    if not route and automatic:
        for name, spec in router.providers.items():
            if name in ready and spec.get("transport") == "claude-cli" and spec.get("models"):
                suggested = f"{name}:{spec['models'][0]}"
                break
    return {"ready": bool(route or suggested), "route": route or suggested, "suggested": suggested,
            "needs_model": sorted(ready) if not route and not suggested else []}


def render(table: list[dict[str, Any]]) -> str:
    """The `exo providers` table: plain text, no secrets."""
    lines = [f"{'provider':<13} {'status':<18} {'key':<26} {'models':>6}  default"]
    for r in table:
        if r["needs"] == "key":
            key = f"{r['key_env'][0]} ({r['source']})" if r["source"] else f"{r['key_env'][0]} (none)"
        elif r["needs"] == "cli":
            key = "cli login"
        else:
            key = "none (local)"
        lat = f" {r['last_test']['latency_ms']}ms" if r["verified"] and r["last_test"].get("latency_ms") else ""
        lines.append(f"{r['provider']:<13} {(r['status'] + lat)[:18]:<18} {key[:26]:<26} "
                     f"{'' if r['models'] is None else r['models']:>6}  {'*' if r['default'] else ''}")
    if not any(r["ready"] and r["transport"] != "decision" for r in table):
        lines.append("")
        lines.append("No provider is set up yet. Add a key: exo providers add <provider>")
    return "\n".join(lines)


# --- live test ---------------------------------------------------------------------------------

def _http(method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> tuple[int, Any]:
    """Default transport. Raises only TimeoutError, ConnectionError or RuntimeError, with no text from the
    request (which carries the key in a header)."""
    import httpx
    try:
        resp = httpx.request(method, url, headers=headers, json=body, timeout=timeout)
    except httpx.TimeoutException:
        raise TimeoutError() from None
    except (httpx.ConnectError, httpx.NetworkError):
        raise ConnectionError() from None
    except Exception as e:
        raise RuntimeError(type(e).__name__) from None
    try:
        data = resp.json()
    except ValueError:
        data = None
    return resp.status_code, data


def classify_status(status: int) -> str:
    if 200 <= status < 300:
        return ""
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "billing"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server"
    return "http"


def _request_for(router: Router, provider: str, spec: Mapping[str, Any], key: str | None) -> tuple[str, str, dict, Any]:
    from .core.models.base import USER_AGENT
    base = spec.get("base_url", "").rstrip("/")
    transport = spec.get("transport", "openai")
    headers = {"user-agent": USER_AGENT, **(spec.get("headers") or {})}
    if transport == "anthropic":
        from .core.models.anthropic import API_VERSION
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = API_VERSION
        return "GET", base + "/v1/models?limit=1", headers, None
    if key:
        headers["authorization"] = f"Bearer {key}"
    if transport == "decision":
        from .core.models.decision import DEFAULT_MODEL, noul_question
        model = next((r.split(":", 1)[1] for r in router.roles.get("decision") or [] if r.startswith(provider + ":")),
                     DEFAULT_MODEL)
        return "POST", base + "/systemone", headers, {"state": "connection test", "model": model,
                                                       "questions": {"q": noul_question("Is this a test?")}}
    return "GET", base + "/models", headers, None


def test_provider(provider: str, *, router: Router | None = None, home: str | os.PathLike | None = None,
                  http: Callable[..., tuple[int, Any]] | None = None, timeout: float = TEST_TIMEOUT,
                  save: bool = True) -> dict[str, Any]:
    """One tiny live call. Returns ``{provider, ok, latency_ms, error_class, detail, models}``; never the key,
    and ``detail`` is built from the status code or error class only."""
    router = router or build_router(home)
    if router.spend_enforced:
        return {"provider": provider, "ok": False, "latency_ms": None, "error_class": "spend_boundary",
                "detail": "Live setup probes have no bounded billing contract; use the guarded model router.",
                "models": None, "at": int(time.time())}
    spec = _spec(router, provider)
    transport = spec.get("transport", "openai")
    res: dict[str, Any] = {"provider": provider, "ok": False, "latency_ms": None, "error_class": "",
                           "detail": "", "models": None, "at": int(time.time())}
    if transport == "claude-cli":
        exe = shutil.which(spec.get("command", "claude"), path=router.env.get("PATH", ""))
        res.update(ok=bool(exe), error_class="" if exe else "not_installed",
                   detail="the claude CLI is on PATH (its own login is the credential)" if exe
                   else "the claude CLI is not on PATH")
    elif transport == "muse-cli":
        exe = shutil.which(spec.get("command", "muse"), path=router.env.get("PATH", ""))
        if not exe:
            res.update(error_class="not_installed", detail="the muse CLI is not on PATH")
        elif not muse_cli.login_state(router.env)["logged_in"]:
            res.update(error_class="no_key", detail="not logged in; run: muse login")
        elif muse_cli.login_state(router.env)["subs_active"] is False:
            res.update(error_class="billing", detail="the Muse subscription is not active")
        else:
            t0 = time.monotonic()
            out = muse_cli.probe(exe, router.env, max(timeout, MUSE_TEST_TIMEOUT))
            res.update(latency_ms=int((time.monotonic() - t0) * 1000), ok=not out["error_class"], **out)
    else:
        names = key_vars(spec)
        key = next((k for n in names if (k := router._key(n))), None) if names else None
        if names and not key:
            res.update(error_class="no_key", detail=f"no key found for {names[0]}")
        else:
            method, url, headers, body = _request_for(router, provider, spec, key)
            t0 = time.monotonic()
            try:
                status, data = (http or _http)(method, url, headers, body, timeout)
            except TimeoutError:
                status, data, cls = None, None, "timeout"
            except ConnectionError:
                status, data, cls = None, None, "not_running" if spec.get("local") else "network"
            except Exception as e:  # an injected transport: keep only the class name
                status, data, cls = None, None, "error"
                res["detail"] = type(e).__name__
            res["latency_ms"] = int((time.monotonic() - t0) * 1000)
            if status is not None:
                cls = classify_status(status)
                res["detail"] = f"HTTP {status}"
                if not cls:
                    items = data.get("data") if isinstance(data, dict) else data if isinstance(data, list) else None
                    if transport == "decision":
                        if not (isinstance(data, dict) and "answers" in data):
                            cls = "bad_response"
                    elif not isinstance(items, list):
                        cls = "bad_response"
                    elif transport != "anthropic":  # anthropic was asked for one page of one
                        res["models"] = len(items)
            res["ok"] = not cls
            res["error_class"] = cls
            if not res["detail"]:
                res["detail"] = {"timeout": f"no answer in {timeout:.0f} s", "network": "could not connect",
                                 "not_running": "no server is listening"}.get(cls, cls)
            res["detail"] = redact_secrets(str(res["detail"]))[:200]
    if save:
        record = {k: res[k] for k in ("ok", "latency_ms", "error_class", "detail", "at")}
        if spec.get("setup_managed") is True:
            record["configuration"] = _custom_status_id(spec)
        _save_state(home, provider, record)
    return res


# --- default model -----------------------------------------------------------------------------

def set_default(ref: str, *, role: str = "main", home: str | os.PathLike | None = None,
                router: Router | None = None) -> list[str]:
    """Put ``ref`` first for ``role`` and keep the old routes as fallbacks; save to config.json. Same result as
    `/default <ref>` in the TUI. Applies to new sessions (a running core picks it up on its next start, or now
    when a client also sends models.set)."""
    router = router or build_router(home)
    router.prefer(role, ref)  # validates the ref and the provider's allowed roles
    cfg = Config.load(home)
    old = cfg.get(f"models.roles.{role}") or []
    if not isinstance(old, list) or not old:
        old = [r for r in router.roles.get(role, []) if r != ref]
    routes = [ref, *[r for r in old if r != ref]]
    cfg.set(f"models.roles.{role}", routes)
    cfg.save()
    return routes
