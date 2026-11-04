"""Provider setup routes for the web client (D-034). The engine is ``exo.providers_setup``; this file is the
HTTP edge and its guard.

  GET  /api/providers                  every provider with status (never a key value), ``ready``, ``store``
  GET  /api/providers/models           models for the default picker (from the core); says why when it fails
  GET  /api/providers/custom           custom provider metadata from the core (never a key value)
  POST /api/providers/custom           add or explicitly edit a custom provider and optionally store its key
  POST /api/providers/<name>/key       {"key": "..."}  store it; the core is told to reload credentials
  POST /api/providers/<name>/remove    delete the stored key
  POST /api/providers/<name>/test      one tiny live call: latency and error class
  POST /api/providers/default          {"ref": "provider:model", "role": "main"}

Threat model, short: the server binds loopback, but a page on any site can still make the browser send
requests to 127.0.0.1, and DNS rebinding can make a hostile name resolve to 127.0.0.1. So every route here
needs all of: a loopback peer; a Host header that is 127.0.0.1, localhost or [::1] (a rebinding name fails);
an Origin, when the browser sends one, of that same host (and a Sec-Fetch-Site of same-origin or none); and the
per-launch token that `exo web` prints in the URL fragment, which the page keeps in sessionStorage and sends as
``X-Exo-Token``. A custom header also forces a CORS preflight, which this server never answers. The key only
travels in a POST body over loopback, is never logged, never echoed, and never put in browser storage.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from exo import providers_setup as ps
from exo.core.models.keys import redact_secrets
from exo.protocol import schema as s
from .source import core_failure

TOKEN_HEADER = "x-exo-token"
LOOPBACK_NAMES = ("127.0.0.1", "localhost", "::1")
NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")
Reply = Tuple[int, Dict[str, Any]]


def _host_name(netloc: str) -> str:
    """Hostname of a Host header or URL netloc, lower case, without port or brackets."""
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc[1:netloc.find("]")] if "]" in netloc else ""
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def _is_loopback_ip(peer: Optional[str]) -> bool:
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def guard(headers: Mapping[str, str], peer: Optional[str], token: str) -> Optional[Reply]:
    """None when the request may use a key route, else the refusal. Checks are listed in the module docstring."""
    refused = guard_peer(headers, peer)
    if refused is not None:
        return refused
    sent = headers.get(TOKEN_HEADER, "")
    if not token or not sent or not hmac.compare_digest(sent.encode(), token.encode()):
        return 403, {"error": "missing or wrong page token: open the link `exo web` printed"}
    return None


def guard_peer(headers: Mapping[str, str], peer: Optional[str]) -> Optional[Reply]:
    """Every check of ``guard`` but the token: loopback peer, loopback Host, same Origin, Sec-Fetch-Site. The
    launch-code route (#166) uses it alone, since the page has no token yet."""
    if not _is_loopback_ip(peer):
        return 403, {"error": "this route answers loopback clients only"}
    host = headers.get("host", "")
    if _host_name(host) not in LOOPBACK_NAMES:
        return 403, {"error": "bad Host header: open the page at 127.0.0.1 or localhost"}
    origin = headers.get("origin")
    if origin is not None:
        o = urlsplit(origin)
        if o.scheme != "http" or _host_name(o.netloc) not in LOOPBACK_NAMES or o.netloc.lower() != host.lower():
            return 403, {"error": "cross-origin request refused"}
    site = headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return 403, {"error": "cross-site request refused"}
    return None


def _name(raw: str) -> str:
    if not raw or len(raw) > 64 or set(raw) - NAME_OK:
        raise ps.SetupError("bad provider name")
    return raw


async def _reload(centre, name: str) -> bool:
    """Tell the core to read keys again for ``name``. Only the name crosses the protocol."""
    try:
        await centre.source.call(s.CommandRun(name="providers", args=f"reload {name}"))
        return True
    except Exception:
        return False


async def handle(centre, method: str, path: str, body: Dict[str, Any],
                 router_factory: Callable[[], Any] = ps.build_router) -> Reply:
    """One provider route, after ``guard`` passed. Never returns or raises with a key value in it."""
    parts = [p for p in path.split("/") if p][2:]       # after "api/providers"
    try:
        if not parts and method == "GET":
            router = await asyncio.to_thread(router_factory)
            table = await asyncio.to_thread(ps.rows, router)
            ready = any(r["ready"] and r["transport"] != "decision" and (not r["roles"] or "main" in r["roles"])
                        for r in table)
            return 200, {"providers": table, "ready": ready, "store": ps.default_store()}
        if parts == ["models"] and method == "GET":
            try:
                res = await centre.source.call(s.ModelsList())
            except Exception as e:
                # D-091: the picker keeps its shape (200, lists present) and adds a named failure, so the page can
                # say which kind it was and offer a retry only where retrying can help. The sentence is redacted
                # and length-capped: it reaches a browser, and a key value must never follow it there.
                kind, why = core_failure(e)
                return 200, {"models": [], "available": [], "error": redact_secrets(why)[:300],
                             "error_class": kind, "retryable": kind == "transport"}
            return 200, {"models": res.get("models") or [], "available": res.get("available") or []}
        if parts == ["custom"] and method == "GET":
            return 200, {"providers": await _custom_list(centre)}
        if parts == ["custom"] and method == "POST":
            return 200, await _custom_save(centre, body, router_factory)
        if parts == ["default"] and method == "POST":
            return 200, await _default(centre, body, router_factory)
        if len(parts) == 2 and method == "POST" and parts[1] in ("key", "remove", "test"):
            name, action = _name(parts[0]), parts[1]
            router = await asyncio.to_thread(router_factory)
            if action == "key":
                value = body.pop("key", None)
                store = body.get("store") or None
                if store is not None and store not in ps.STORES:
                    raise ps.SetupError("store must be one of {}".format(", ".join(ps.STORES)))
                try:
                    out = await asyncio.to_thread(ps.set_key, name, value if isinstance(value, str) else "",
                                                  router=router, store=store)
                finally:
                    value = None
                out["core_reloaded"] = await _reload(centre, name)
                return 200, {"ok": True, **out}
            if action == "remove":
                out = await asyncio.to_thread(ps.remove_key, name, router=router)
                out["core_reloaded"] = await _reload(centre, name)
                return 200, {"ok": True, **out}
            out = await asyncio.to_thread(ps.test_provider, name, router=router)
            if out.get("ok"):  # the core retries it now: its cooldown and route breaker clear
                out["core_reloaded"] = await _reload(centre, name)
            return 200, out
        if method not in ("GET", "POST"):
            return 405, {"error": "method not allowed"}
        return 404, {"error": "not found"}
    except ps.SetupError as e:
        return 400, {"error": redact_secrets(str(e))}
    except ValueError as e:  # router validation: bad refs, unknown providers
        return 400, {"error": redact_secrets(str(e))[:300]}
    except Exception as e:  # never echo an arbitrary failure message
        if getattr(e, "code", "") in ("forbidden", "unauthorized"):
            return 403, {"error": "the core refused this change: principal authority is required"}
        if getattr(e, "code", "") in ("http_401", "http_403"):
            return 403, {"error": "the core refused this web client's bearer token"}
        return 500, {"error": "provider setup failed ({})".format(type(e).__name__)}


async def _custom_config(centre) -> dict:
    try:
        res = await centre.source.call(s.ConfigGet(key="models.providers"))
    except Exception as e:
        if getattr(e, "code", "") == "not_found":
            return {}
        raise
    value = res.get("value")
    return value if isinstance(value, dict) else {}


async def _custom_list(centre) -> list[dict[str, str]]:
    out = []
    for name, spec in (await _custom_config(centre)).items():
        if not isinstance(name, str) or not name.startswith("custom-") or not isinstance(spec, dict) \
                or spec.get("setup_managed") is not True:
            continue
        try:
            _, safe = ps.custom_provider(name[7:], spec.get("base_url"), spec.get("transport"),
                                         spec.get("setup_auth"), spec.get("setup_model"))
        except ps.SetupError:
            continue
        out.append({"name": name[7:], "endpoint": safe["base_url"], "protocol": safe["transport"],
                    "auth": safe["setup_auth"], "model": safe["setup_model"]})
    return sorted(out, key=lambda item: item["name"])


async def _custom_save(centre, body: Dict[str, Any], router_factory: Callable[[], Any]) -> Dict[str, Any]:
    # A key never crosses the core protocol or enters config.json.
    value = body.pop("key", "")
    try:
        name, spec = ps.custom_provider(body.get("name"), body.get("endpoint"), body.get("protocol"),
                                        body.get("auth"), body.get("model"))
        if not isinstance(value, str):
            raise ps.SetupError("The key must be text.")
        if value:
            if spec["setup_auth"] != "key":
                raise ps.SetupError("Choose API key authentication before entering a key.")
            value = ps.check_key(value)
            ps.remember_secret(value)
        current = await _custom_config(centre)
        old = current.get(name)
        replace = body.get("replace", False)
        if not isinstance(replace, bool):
            raise ps.SetupError("Replace must be true or false.")
        if name in current and (not isinstance(old, dict) or old.get("setup_managed") is not True):
            raise ps.SetupError("That provider name already belongs to a manually configured provider.")
        if name in current and not replace:
            raise ps.SetupError("A custom provider with that name already exists. Use Edit to change it.")
        if name not in current and replace:
            raise ps.SetupError("This custom provider no longer exists. Add it instead.")
        # The core enforces principal authority; never fall back to editing local disk on refusal or outage.
        await centre.source.call(s.ConfigSet(key="models.providers." + name, value=spec))
        result: Dict[str, Any] = {"ok": True, "provider": name, "applies": "next start", "key_saved": False}
        if value:
            try:
                router = await asyncio.to_thread(router_factory)
                router.providers[name] = spec
                stored = await asyncio.to_thread(ps.set_key, name, value, router=router)
                result.update(key_saved=True, stored_in=stored["stored_in"], warning=stored.get("warning", ""))
            except Exception:
                result["warning"] = "Settings were saved, but the key could not be stored. Add it again before restarting."
        elif spec["setup_auth"] == "key":
            result["warning"] = "No new key was saved. If this endpoint has no saved key, add one before use."
        return result
    finally:
        value = ""


async def _default(centre, body: Dict[str, Any], router_factory: Callable[[], Any]) -> Dict[str, Any]:
    ref, role = body.get("ref"), body.get("role") or "main"
    if not isinstance(ref, str) or not ref or ref.startswith("-") or len(ref) > 200 or any(c.isspace() for c in ref):
        raise ps.SetupError("ref must look like provider:model")
    if not isinstance(role, str) or role not in ("main", "aux", "decision", "eval", "fast", "standard", "deep"):
        raise ps.SetupError("unknown role")
    try:  # same steps as the TUI's /default: apply now in the core, then save with fallbacks kept
        listed = await centre.source.call(s.ModelsList())
        old = [m["route"] for m in listed.get("models") or [] if m.get("role") == role]
        await centre.source.call(s.ModelsSet(model=ref, role=role))
        routes = [ref, *[r for r in old if r != ref]]
        await centre.source.call(s.ConfigSet(key="models.roles." + role, value=routes))
        return {"ok": True, "role": role, "routes": routes, "applied": "now"}
    except ps.SetupError:
        raise
    except Exception as e:
        if getattr(e, "code", "") == "forbidden":
            raise  # a refusal is never permission to edit the core config on disk
        if getattr(e, "code", "") in ("bad_model", "bad_request", "bad_config", "not_found"):
            raise ps.SetupError(getattr(e, "message", "") or "the core refused that model") from None
        router = await asyncio.to_thread(router_factory)  # core down: save it for the next start
        routes = await asyncio.to_thread(ps.set_default, ref, role=role, router=router)
        return {"ok": True, "role": role, "routes": routes, "applied": "next start"}
